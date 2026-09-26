"""Step ``regenerate``: one piece again, with an optional note (spec 3, 9.2).

``params = {"target": "bible:<field>" | "concepts", "note": str | None}``.

- ``concepts`` is the concepts step itself ("10 more"), the note joining the
  seed as "Author's note: ...". It needs no chosen concept: concepts are how
  one gets chosen.
- ``bible:<field>`` (``prompts.REGENERATE_TARGETS``) re-runs the one prompt
  that owns the field, shown its current values and told to rewrite only
  that field; whatever else the model sends back, only the target's keys are
  applied (``tone`` also carries ``genre_tags``; ``world`` and ``themes`` are
  a whole prompt). The bible approval is cleared, as by the bible step.

Phase 1 knows only these targets; every other one of the 9.2 grammar is
refused, naming the valid ones.
"""

from __future__ import annotations

import time

from .. import prompts
from . import bible, concepts, llm_call
from .llm_call import StepFailed

BIBLE_PREFIX = "bible:"
CONCEPTS_TARGET = "concepts"

VALID_TARGETS = tuple(f"{BIBLE_PREFIX}{field}" for field in prompts.REGENERATE_TARGETS) + (CONCEPTS_TARGET,)


def _note(params):
    note = params.get("note")
    if note is None:
        return None
    if not isinstance(note, str):
        raise StepFailed(f"The note must be text, not {type(note).__name__}.")
    return note.strip() or None


def run(ctx, *, runner=None, time_fn=time.monotonic) -> dict:
    params = ctx.params or {}
    target = params.get("target")

    if target == CONCEPTS_TARGET:
        return concepts.run(ctx, note=_note(params), runner=runner, time_fn=time_fn)

    field = None
    if isinstance(target, str) and target.startswith(BIBLE_PREFIX):
        field = target[len(BIBLE_PREFIX):]
    if field not in prompts.REGENERATE_TARGETS:
        raise StepFailed(
            f"Cannot regenerate {target!r}: the valid targets are {', '.join(VALID_TARGETS)}."
        )
    return _regenerate_bible_field(ctx, field, _note(params), runner=runner, time_fn=time_fn)


def _regenerate_bible_field(ctx, field, note, *, runner, time_fn) -> dict:
    part, keys = prompts.REGENERATE_TARGETS[field]
    store, story = llm_call.open_story(ctx)
    llm_call.require_concept(story)
    ctx.cancel.check()

    # The pack budgets the note like every other section (and says if it cut it).
    pack = bible.pack_for(story, note=note)
    llm_call.announce_trimmed(ctx, pack, set())
    system, user, schema = bible.BUILDERS[part](
        pack,
        regenerate={"field": field, "current": bible.current_fields(story, part), "note": pack.note},
    )
    reply = llm_call.call_json(
        ctx, part, system, user, schema,
        validator=bible.validator_for(story, part, keys), runner=runner, time_fn=time_fn,
    )

    store.update(ctx.story_id, lambda doc: bible.apply(doc, part, reply, keys), now=llm_call.utc_now())
    ctx.on_log(f"🔁 Regenerated {field}" + (f" (note: {pack.note})" if pack.note else ""))
    return {"target": f"{BIBLE_PREFIX}{field}", "fields": list(keys)}
