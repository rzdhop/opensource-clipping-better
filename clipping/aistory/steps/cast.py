"""Step ``cast``: the story's characters, written, drawn and voiced (spec 3
step 5, 2.3, 8.1, 11; phase-2 plan, DEC-119).

``params = {"selected": [sketch names], "custom": [{name, role, one_line,
archetype?}]}``, both optional. Each selected name is an entry of the chosen
concept's ``cast_sketch`` (an unknown name is refused, naming the valid ones,
before anything is written or called); each custom entry is a character of
the user's own. A new ``character_v1`` is created for each -- unless a
character of that name already exists, which is then simply included.

Then the step **fills what is missing**, for every character of the story
(leads, support, recurring, guest), in this order:

1. the text (``descriptor`` is null): K1, shown the characters already
   written and the descriptions of the character's design references
   (``uploads.upload_notes``); it writes the descriptor, signature items,
   personality, relationships (K1's names mapped to ids -- an unknown name is
   dropped and printed), the voice brief (``voice_hints``) and the
   ``prompt_block``;
2. the portrait, then 3. the turnaround and the expressions sheet
   (``refimages``). A sheet with no editor to make it (``NeedsEditor``, spec
   8.1's "stop and ask", raised before any call) is **not** a failure: the
   character is recorded as waiting for an editor or prompt-only
   consistency, and its other sheet is not tried;
4. once every character's text is there, one voice per unpinned character
   (``voices.propose`` over the cast; the voices already pinned are taken),
   pinned; a character left with none is recorded "pick a voice";
5. the voice sample of every pinned voice that has none
   (``voices.synthesize_sample``: that voice alone, never another).

Each part is a local failure (DEC-027): printed, recorded, and the next part
and the next character still run; a part that depends on a failed one (the
images of a character whose text failed, the sheets of a missing portrait)
is left for the next run. Anything the step writes clears the entity's
``approved_at``. Run again, it makes only what is still missing: with
nothing missing it calls nothing and writes nothing.

Returns ``{created, written, images, needs_editor, voices, pick_voice,
samples}``; any failed part ends the step with a ``StepFailed`` naming the
character, the part, the reason and the target that finishes it.
"""

from __future__ import annotations

import copy
import time

from .. import context, prompting, prompts, refimages, schemas, voices
from .. import uploads as uploads_mod
from . import entities, llm_call
from .entities import CHARACTERS
from .llm_call import StepFailed

SHEETS = ("turnaround", "expressions")

_EMPTY_PERSONALITY = {"traits": [], "wants": None, "fears": None, "speech_style": None}


# -------------------------------------------------------------- new characters

def _sketch(story) -> list:
    concept = story.get("concept") or {}
    sketch = concept.get("cast_sketch") if isinstance(concept, dict) else None
    return [entry for entry in (sketch or []) if isinstance(entry, dict) and isinstance(entry.get("name"), str)]


def _text_or_none(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def sketch_entry(story, character):
    """The concept's cast-sketch entry a character was picked from, or None."""
    if character.get("source") != "sketch":
        return None
    return entities.by_name(_sketch(story)).get(entities.name_key(character["name"]))


def new_character(char_id, name, role, one_line, *, archetype, source, now) -> dict:
    """A ``character_v1`` with nothing written yet."""
    return {
        "$schema": schemas.CHARACTER_SCHEMA_NAME,
        "char_id": char_id,
        "name": name,
        "role": role,
        "archetype": archetype or "",
        "one_line": one_line,
        "descriptor": None,
        "signature_items": [],
        "personality": copy.deepcopy(_EMPTY_PERSONALITY),
        "relationships": {},
        "voice": None,
        "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None,
        "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": source,
        "approved_at": None,
        "created_at": now,
        "updated_at": now,
    }


def _wanted(story, params) -> list:
    """``[(name, role, one_line, archetype, source)]`` the params ask for,
    checked before anything is written. ``StepFailed`` for a malformed
    request or a name that is not in the concept's cast sketch."""
    selected = params.get("selected")
    custom = params.get("custom")
    if selected is None:
        selected = []
    if custom is None:
        custom = []
    if not isinstance(selected, list) or not all(isinstance(name, str) for name in selected):
        raise StepFailed("'selected' must be a list of names from the concept's cast sketch.")
    if not isinstance(custom, list) or not all(isinstance(entry, dict) for entry in custom):
        raise StepFailed("'custom' must be a list of characters, each {name, role, one_line, archetype?}.")

    sketch = _sketch(story)
    known = entities.by_name(sketch)
    unknown = [name for name in selected if entities.name_key(name) not in known]
    if unknown:
        valid = [entry["name"] for entry in sketch]
        raise StepFailed(
            f"{entities.quoted_list(unknown)} {'is' if len(unknown) == 1 else 'are'} not in the concept's "
            f"cast sketch; pick from {entities.quoted_list(valid) or 'none (the concept has no cast sketch)'}."
        )

    wanted = []
    for name in selected:
        entry = known[entities.name_key(name)]
        wanted.append((entry["name"], entry.get("role"), entry.get("one_line"), _text_or_none(entry.get("archetype")),
                       "sketch"))
    for entry in custom:
        name, one_line = entry.get("name"), entry.get("one_line")
        label = name if isinstance(name, str) and name.strip() else "a custom character"
        archetype = entry.get("archetype")
        if archetype is not None and not isinstance(archetype, str):
            raise StepFailed(f"{label}: the archetype must be text.")
        wanted.append((name.strip() if isinstance(name, str) else name, entry.get("role"),
                       one_line.strip() if isinstance(one_line, str) else one_line,
                       _text_or_none(archetype), "custom"))
    return wanted


def _create(ctx, store, story, params) -> list:
    """Create the characters the params ask for that do not exist yet;
    returns their ids. Every document is checked before the first is written."""
    wanted = _wanted(story, params)
    existing = store.list_entities(ctx.story_id, CHARACTERS)
    names = {entities.name_key(doc["name"]) for doc in existing}
    taken = {doc["char_id"] for doc in existing}
    docs = []
    for name, role, one_line, archetype, source in wanted:
        if not isinstance(name, str) or not name:
            raise StepFailed("Every custom character needs a name.")
        if entities.name_key(name) in names:
            continue  # already in the cast: simply included
        char_id = schemas.entity_id(CHARACTERS, name, taken)
        doc = new_character(char_id, name, role, one_line, archetype=archetype, source=source,
                            now=llm_call.utc_now())
        errors = schemas.character_errors(doc)
        if errors:
            raise StepFailed(f"{name} cannot be created: {'; '.join(errors[:3])}.")
        docs.append(doc)
        names.add(entities.name_key(name))
        taken.add(char_id)
    if not docs and not existing:
        raise StepFailed("Pick characters from the concept's cast sketch or add your own first.")
    for doc in docs:
        store.write_entity(ctx.story_id, CHARACTERS, doc, now=doc["created_at"])
        ctx.on_log(f"👤 {doc['name']}: created ({doc['role']}, from the {doc['source']})")
    return [doc["char_id"] for doc in docs]


# ------------------------------------------------------------------------ K1

def _k1_character(story, character) -> dict:
    """What K1 is told about the character it writes: its sketch entry's
    archetype and signature hint when it came from one."""
    entry = sketch_entry(story, character) or {}
    return {
        "name": character["name"],
        "role": character["role"],
        "archetype": character["archetype"] or _text_or_none(entry.get("archetype")),
        "one_line": character["one_line"],
        "signature_hint": _text_or_none(entry.get("signature_hint")),
    }


def _cast_line(doc) -> dict:
    return {key: doc[key] for key in ("name", "role", "one_line", "descriptor") if doc.get(key)}


def _relationships(reply, others) -> tuple:
    """``({char_id: relation}, [dropped names])``: K1's names mapped to the
    ids of the story's other characters."""
    known = entities.by_name(others)
    mapped, dropped = {}, []
    for item in reply.get("relationships") or ():
        doc = known.get(entities.name_key(item["with"]))
        if doc is None:
            dropped.append(item["with"])
            continue
        mapped[doc["char_id"]] = item["relation"]
    return mapped, dropped


def apply_k1(doc, reply, *, others, lock) -> list:
    """Write K1's reply into the character *doc* (in place) and clear its
    approval; returns the relationship names that matched no character."""
    relationships, dropped = _relationships(reply, others)
    doc["descriptor"] = reply["descriptor"]
    doc["signature_items"] = list(reply["signature_items"])
    personality = reply["personality"]
    doc["personality"] = {
        "traits": list(personality["traits"]),
        "wants": personality["wants"],
        "fears": personality["fears"],
        "speech_style": personality["speech_style"],
    }
    doc["relationships"] = relationships
    voice = reply["voice"]
    doc["voice_hints"] = {
        "gender": voice["gender"], "age": voice["age"], "style_tags": list(voice["style_tags"]),
        "direction": voice["direction"], "sample_line": voice["sample_line"],
    }
    doc["prompt_block"] = prompting.character_prompt_block(
        lock, descriptor=doc["descriptor"], signature_items=doc["signature_items"])
    doc["approved_at"] = None
    return dropped


def current_text(character, others) -> dict:
    """What K1 last wrote, for a regenerate's "current values" block (names,
    not ids; empty values left out)."""
    names = {doc["char_id"]: doc["name"] for doc in others}
    current = {}
    if character["descriptor"]:
        current["descriptor"] = character["descriptor"]
    if character["signature_items"]:
        current["signature_items"] = list(character["signature_items"])
    personality = {key: value for key, value in character["personality"].items() if value not in (None, "", [])}
    if personality:
        current["personality"] = personality
    hints = character.get("voice_hints") or {}
    voice = {key: hints[key] for key in ("direction", "sample_line") if hints.get(key)}
    if voice:
        current["voice"] = voice
    relationships = {names.get(cid, cid): relation for cid, relation in character["relationships"].items()}
    if relationships:
        current["relationships"] = relationships
    return current


def write_text(ctx, store, char_id, *, tools, note=None, regenerate=False, announced=None) -> dict:
    """K1 for one character, written into its document; returns the document.

    ``StepFailed`` (the chain failed, the reply was refused twice, or there is
    no style lock). With *regenerate*, K1 is shown the current values and the
    *note*; the images and the pinned voice stay, the voice brief is rewritten.
    """
    story = store.get(ctx.story_id)
    lock = entities.read_lock(store, ctx.story_id)
    character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
    others = [doc for doc in entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
              if doc["char_id"] != char_id]
    written = [_cast_line(doc) for doc in others if doc["descriptor"]]

    pack = context.build_pack(language=story["language"], story=story, template=lock, note=note)
    llm_call.announce_trimmed(ctx, pack, set() if announced is None else announced)
    regen = None
    if regenerate:
        regen = {"field": "text", "current": current_text(character, others), "note": pack.note}
    system, user, schema = prompts.build_k1(
        pack, character=_k1_character(story, character), cast_so_far=written,
        upload_notes=uploads_mod.upload_notes(character), regenerate=regen,
    )

    def validate(reply):
        errors = schemas.k1_errors(reply, character["name"])
        if errors:
            return errors
        trial = copy.deepcopy(character)
        apply_k1(trial, reply, others=others, lock=lock)
        return schemas.character_errors(trial)

    reply = llm_call.call_json(ctx, "K1", system, user, schema, validator=validate,
                               runner=tools.runner, time_fn=tools.time_fn)

    dropped = []

    def write(doc):
        dropped.extend(apply_k1(doc, reply, others=others, lock=lock))

    saved = entities.write_character(store, ctx.story_id, char_id, write, now=llm_call.utc_now())
    for name in dropped:
        ctx.on_log(f"ℹ️ {character['name']}: relationship with {name!r} dropped -- no character of that name.")
    return saved


# -------------------------------------------------------------------- the run

class _Run:
    """What one cast run did, and what failed."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.written = []
        self.images = {}
        self.needs_editor = []
        self.voices = {}
        self.pick_voice = []
        self.samples = {}
        self.failures = []  # (name, part, reason, target)

    def fail(self, character, part, reason, target):
        self.failures.append((character["name"], part, reason, target))
        self.ctx.on_log(f"✖ {character['name']} {part} failed: {reason}")

    def made(self, character, which, ref):
        self.images.setdefault(character["char_id"], {})[which] = ref["consistency"]


def _text(run, ctx, store, character, tools, announced) -> bool:
    """Part 1; True when the character has its text afterwards."""
    if character["descriptor"]:
        return True
    ctx.cancel.check()
    ctx.on_log(f"👤 {character['name']}: text")
    try:
        write_text(ctx, store, character["char_id"], tools=tools, announced=announced)
    except StepFailed as exc:
        run.fail(character, "text", exc.reason, entities.target(CHARACTERS, character["char_id"], "text"))
        return False
    run.written.append(character["char_id"])
    return True


def _image(run, ctx, store, char_id, which, tools):
    """One missing image: its ref, or None when it failed (recorded). A sheet
    with no editor to make it raises ``NeedsEditor`` for the caller to record
    and stop at; nothing was called or written then."""
    character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
    ctx.on_log(f"👤 {character['name']}: {which}")
    try:
        ref = refimages.character_image(store, ctx.story_id, char_id, which, **tools.image_kwargs(ctx))
    except refimages.RefImageError as exc:
        if isinstance(exc, refimages.NeedsEditor) and which in SHEETS:
            raise
        run.fail(character, which, str(exc), entities.target(CHARACTERS, char_id, "image", which))
        return None
    run.made(character, which, ref)
    entities.clear_approval(store, ctx.story_id, CHARACTERS, char_id, now=ref["created_at"])
    return ref


def _images(run, ctx, store, char_id, tools) -> None:
    """Parts 2 and 3: the portrait, then the sheets, each only when missing."""
    character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
    refs = character["refs"]
    if not entities.has_file(store, ctx.story_id, CHARACTERS, char_id, refs["portrait"]):
        ctx.cancel.check()
        if _image(run, ctx, store, char_id, "portrait", tools) is None:
            return  # the sheets are made from the portrait
    for which in SHEETS:
        character = store.read_entity(ctx.story_id, CHARACTERS, char_id)
        if entities.has_file(store, ctx.story_id, CHARACTERS, char_id, character["refs"][which]):
            continue
        ctx.cancel.check()
        try:
            _image(run, ctx, store, char_id, which, tools)
        except refimages.NeedsEditor as exc:
            run.needs_editor.append({"char_id": char_id, "name": character["name"],
                                     "message": exc.readiness["message"]})
            return  # the other sheet needs the same editor


def _pin_voices(run, ctx, store, story) -> None:
    """Part 4: one voice for every written character that has none."""
    cast = entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS))
    unpinned = [doc for doc in cast if not doc["voice"] and doc["voice_hints"]]
    if not unpinned:
        return
    ctx.cancel.check()
    taken = {(doc["voice"]["provider"], doc["voice"]["voice_id"]) for doc in cast if doc["voice"]}
    proposal = voices.propose(unpinned, story["language"], env=ctx.settings_env, taken=taken,
                              on_log=ctx.on_log)
    for doc in unpinned:
        choice = proposal.get(doc["char_id"])
        if choice is None:
            run.pick_voice.append(doc["char_id"])
            continue
        pinned = {}

        def pin(current, choice=choice):
            if current["voice"]:
                return  # pinned meanwhile: kept
            current["voice"] = voices.pin(current, choice)
            current["approved_at"] = None
            pinned.update(current["voice"])

        try:
            entities.write_character(store, ctx.story_id, doc["char_id"], pin, now=llm_call.utc_now())
        except KeyError:
            if entities.exists(store, ctx.story_id, CHARACTERS, doc["char_id"]):
                raise
            ctx.on_log(f"ℹ️ {doc['name']} was removed while the step ran; skipped.")
            continue
        if pinned:
            label = f"{pinned['provider']}/{pinned['voice_id']}"
            run.voices[doc["char_id"]] = label
            ctx.on_log(f"👤 {doc['name']}: voice {label}")


def _samples(run, ctx, store, tools) -> None:
    """Part 5: a sample of every pinned voice that has none."""
    for doc in entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS)):
        if not doc["voice"] or entities.has_sample(store, ctx.story_id, doc["char_id"]):
            continue
        ctx.cancel.check()
        ctx.on_log(f"👤 {doc['name']}: voice sample")
        try:
            sample = voices.synthesize_sample(store, ctx.story_id, doc["char_id"], **tools.voice_kwargs(ctx))
        except voices.VoiceError as exc:
            reason = str(exc)
            if exc.alternates:
                reason += " Other voices: " + ", ".join(f"{v.provider}/{v.voice_id}" for v in exc.alternates) + "."
            run.fail(doc, "voice sample", reason, entities.target(CHARACTERS, doc["char_id"], "voice"))
            continue
        except KeyError:
            if entities.exists(store, ctx.story_id, CHARACTERS, doc["char_id"]):
                raise
            ctx.on_log(f"ℹ️ {doc['name']} was removed while the step ran; skipped.")
            continue
        run.samples[doc["char_id"]] = sample["name"]
        entities.clear_approval(store, ctx.story_id, CHARACTERS, doc["char_id"], now=llm_call.utc_now())


def needs_editor_line(waiting) -> str:
    """The 🟡 line naming every character waiting for an editor."""
    names = ", ".join(item["name"] for item in waiting)
    messages = list(dict.fromkeys(item["message"] for item in waiting))
    return (f"🟡 Needs a reference-capable editor or prompt-only consistency for: {names} — "
            f"{' '.join(messages)}")


def run(ctx, *, runner=None, time_fn=time.monotonic, sleep_fn=time.sleep, adapters=None, transport=None) -> dict:
    tools = entities.Tools(runner=runner, time_fn=time_fn, sleep_fn=sleep_fn, adapters=adapters,
                           transport=transport)
    store, story = llm_call.open_story(ctx)
    entities.require_status(story, "style_approved", "Approve the style first.")
    entities.read_lock(store, ctx.story_id)
    created = _create(ctx, store, story, ctx.params or {})

    run_ = _Run(ctx)
    announced = set()
    for character in entities.cast_order(store.list_entities(ctx.story_id, CHARACTERS)):
        ctx.cancel.check()
        try:
            if _text(run_, ctx, store, character, tools, announced):
                _images(run_, ctx, store, character["char_id"], tools)
        except KeyError:
            if entities.exists(store, ctx.story_id, CHARACTERS, character["char_id"]):
                raise
            ctx.on_log(f"ℹ️ {character['name']} was removed while the step ran; skipped.")

    _pin_voices(run_, ctx, store, story)
    _samples(run_, ctx, store, tools)

    if run_.needs_editor:
        ctx.on_log(needs_editor_line(run_.needs_editor))

    if run_.failures:
        parts = "; ".join(f"{name} {part} failed ({reason})" for name, part, reason, _ in run_.failures)
        targets = list(dict.fromkeys(target for *_, target in run_.failures))
        message = (f"Cast incomplete: {parts}. Run the cast step again to fill what is missing, or regenerate "
                   f"{entities.quoted_list(targets)}.")
        if run_.needs_editor:
            names = ", ".join(item["name"] for item in run_.needs_editor)
            message += f" Waiting for an editor or prompt-only consistency: {names}."
        raise StepFailed(message)

    return {
        "created": created,
        "written": run_.written,
        "images": run_.images,
        "needs_editor": [{"char_id": item["char_id"], "message": item["message"]} for item in run_.needs_editor],
        "voices": run_.voices,
        "pick_voice": run_.pick_voice,
        "samples": run_.samples,
    }
