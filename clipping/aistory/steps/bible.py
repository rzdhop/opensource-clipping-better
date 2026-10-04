"""Step ``bible``: B1 -> B2 -> B3 into ``story.json`` (spec 3 step 3, 4.2).

Three small calls, each writing only its own fields the moment its reply is
accepted, each built from the story as it then stands -- B2 sees what B1
wrote, B3 sees B1 and B2. A failure is local (DEC-027): a part that fails is
printed and the next part still runs with whatever exists, the fields
already written stay, and the step ends failed naming each missing part and
the ``regenerate bible:<field>`` target that finishes it.

Writing any bible field clears ``approvals.bible`` -- a changed bible is an
unapproved bible -- and leaves ``approvals.style`` alone (the derived status
already stops counting it). The table here is shared with ``regenerate``.

Plan 22 stage 2 (DEC-274): with a non-empty ``seed_text`` and
``generation_profile.writing == "v3"``, B1 is written by B1v3 instead
(:func:`prompts.build_b1_v3`) -- the brief under the concept, told to keep
its names, setting and conflict; same fields, same cap. A bible-field
regenerate (``steps/regenerate.py``, one field at a time) keeps using B1
either way: it is not this step's full run.
"""

from __future__ import annotations

import copy
import time

from .. import context, media_policy, prompts, schemas
from . import llm_call
from .llm_call import StepFailed

PARTS = ("B1", "B2", "B3")

BUILDERS = {"B1": prompts.build_b1, "B2": prompts.build_b2, "B3": prompts.build_b3}
VALIDATORS = {"B1": schemas.b1_errors, "B2": schemas.b2_errors, "B3": schemas.b3_errors}

# The keys each part's reply carries, i.e. what it may write.
FIELDS = {
    "B1": ("logline", "premise", "tone", "genre_tags"),
    # Nested under story["world"].
    "B2": ("setting_summary", "rules", "time_period", "recurring_motifs"),
    "B3": ("themes_and_values", "audience", "why_come_back"),
}

WORLD_PART = "B2"


def regenerate_targets(part) -> list:
    """The ``bible:<field>`` targets that re-run *part*, in grammar order."""
    return [field for field, (owner, _keys) in prompts.REGENERATE_TARGETS.items() if owner == part]


def _quoted_list(names) -> str:
    quoted = [f"'{name}'" for name in names]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _writing_gate(story) -> bool:
    """Whether this story writes B1 from the brief (plan 22 stage 2,
    DEC-274): a non-empty ``seed_text`` and ``generation_profile.writing ==
    "v3"``. Without either, B1 is built exactly as it always was (RC-W2)."""
    if not story.get("seed_text"):
        return False
    return media_policy.writing_v3(story)


def pack_for(story, *, note=None, brief=False):
    """The context pack for a bible prompt: the chosen concept plus the bible
    and world written so far -- and, with *brief* (only ever true for this
    step's own full B1v3 run, :func:`_writing_gate`), the story's
    ``seed_text`` too (B1v3 reads it; B2/B3 and a bible-field regenerate
    never do, so they never carry it). ``StepFailed`` when the concept
    snapshot lacks what the prompt renders (a hand-edited or custom concept)."""
    try:
        return context.build_pack(
            language=story["language"],
            story=story,
            concept=story["concept"],
            note=note,
            brief_text=story.get("seed_text") if brief else None,
        )
    except (KeyError, TypeError) as exc:
        raise StepFailed(f"The chosen concept cannot be read ({type(exc).__name__}: {exc}); choose it again.") from None


def current_fields(story, part) -> dict:
    """What *part* last wrote, for a regenerate's "current values" block.
    Empty values are left out: "None" would read as a value to keep."""
    source = (story.get("world") or {}) if part == WORLD_PART else story
    current = {}
    for key in FIELDS[part]:
        value = source.get(key)
        if value not in (None, "", [], {}):
            current[key] = copy.deepcopy(value)
    return current


def apply(story, part, reply, keys=None) -> None:
    """Write *keys* (default: all of *part*'s fields) of *reply* into *story*,
    and clear the bible approval. Mutates *story*."""
    keys = FIELDS[part] if keys is None else tuple(keys)
    if part == WORLD_PART:
        world = dict(story.get("world") or {})
        for key in keys:
            world[key] = copy.deepcopy(reply[key])
        story["world"] = world
    else:
        for key in keys:
            story[key] = copy.deepcopy(reply[key])
    story["approvals"]["bible"] = None


def validator_for(story, part, keys=None):
    """*part*'s post-validator, then: would the story still be a valid
    ``story_bible_v1`` with the reply applied? A reply that passes the first
    and would be refused by the store (a 45-character tag, say) is a
    rejected reply -- asked for again -- not a crash after it was paid for."""
    def validate(reply):
        errors = VALIDATORS[part](reply)
        if errors:
            return errors
        trial = copy.deepcopy(story)
        apply(trial, part, reply, keys)
        return schemas.story_bible_errors(trial)

    return validate


# ``why_come_back`` is complete with its three lines (``workflow.WHY_COME_BACK_LINES``).
WHY_COME_BACK_LINES = 3


def missing_parts(story) -> list:
    """The parts whose fields are still missing or empty, in :data:`PARTS`
    order: what a run must still write for the bible to be complete
    (``workflow.missing_bible_fields``' rule, by part: B2's is the world)."""
    missing = []
    for part in PARTS:
        if part == WORLD_PART:
            empty = _empty(story.get("world"))
        else:
            empty = any(_empty(story.get(key)) for key in FIELDS[part] if key != "why_come_back")
            if "why_come_back" in FIELDS[part]:
                lines = [line for line in story.get("why_come_back") or [] if isinstance(line, str) and line.strip()]
                empty = empty or len(lines) != WHY_COME_BACK_LINES
        if empty:
            missing.append(part)
    return missing


def _empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, dict):
        return all(_empty(v) for v in value.values())
    if isinstance(value, list):
        return all(_empty(v) for v in value)
    return False


def run(ctx, *, runner=None, time_fn=time.monotonic, parts=None) -> dict:
    """B1, B2, B3 -- or, with *parts* (plan 21 stage 1: the agent run's
    Continue, :func:`missing_parts`), only those, in order."""
    store, story = llm_call.open_story(ctx)
    llm_call.require_concept(story)

    written = []
    failed = []  # (part, reason)
    announced = set()

    for part in (PARTS if parts is None else [part for part in PARTS if part in parts]):
        ctx.cancel.check()
        # The story as it now stands: what the previous part wrote, and any
        # edit the user made meanwhile.
        story = store.get(ctx.story_id)
        use_brief = part == "B1" and _writing_gate(story)
        pack = pack_for(story, brief=use_brief)
        llm_call.announce_trimmed(ctx, pack, announced)
        if use_brief:
            system, user, schema = prompts.build_b1_v3(pack)
            prompt_id = "B1v3"
        else:
            system, user, schema = BUILDERS[part](pack)
            prompt_id = part

        try:
            reply = llm_call.call_json(
                ctx, prompt_id, system, user, schema,
                validator=validator_for(story, part), runner=runner, time_fn=time_fn,
            )
        except StepFailed as exc:
            failed.append((part, exc.reason))
            ctx.on_log(f"✖ {part} failed: {exc.reason}")
            continue

        store.update(ctx.story_id, lambda doc, p=part, r=reply: apply(doc, p, r), now=llm_call.utc_now())
        written.append(part)

    if failed:
        parts = "; ".join(f"{part} failed ({reason})" for part, reason in failed)
        targets = [target for part, _ in failed for target in regenerate_targets(part)]
        raise StepFailed(
            f"Bible incomplete: {parts}. Regenerate {_quoted_list(targets)} to finish it."
        )

    return {"written": written}
