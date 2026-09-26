"""What the phase-2 step runners share (AI Story phase 2, stage 6).

The cast, places and season steps and the phase-2 targets of ``regenerate``
all write entity documents (``characters/``, ``places/``, ``props/``) or the
season arc, drive the LLM chain through ``llm_call.call_json`` and the image
and voice chains through ``refimages``/``voices``. The rules they have in
common live here, once:

- :class:`Tools` -- the stand-ins a test hands in (the LLM runner, the image
  and TTS adapters, a transport, the clocks); the worker passes none;
- the precondition on the story's derived status (a style approval without a
  bible approval is not an approval -- ``store.derive_status``);
- the cast order every step walks (leads, support, recurring, guest, then
  ``created_at``, then the id -- ``voices.propose``'s own order);
- the fresh seed a regenerate asks for, and clearing an entity's
  ``approved_at`` once what it approved has changed (an approval never
  outlives what it approved; the group approvals re-fold in
  ``store.write_entity``);
- a character is always re-read and written under the uploads' own lock
  (``uploads._ENTRIES_LOCK``), so a design reference appended while a call
  was in flight is never lost.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .. import defaults, imaging, schemas
from .. import store as store_mod
from .. import uploads as uploads_mod
from .llm_call import StepFailed

CHARACTERS, PLACES, PROPS = "characters", "places", "props"

# The regenerate grammar's word for each entity kind (spec 9.2).
TARGET_KINDS = {CHARACTERS: "character", PLACES: "place", PROPS: "prop"}

# A regenerate's seed: a fresh one, in the range every image provider takes.
SEED_RANGE = 2**31

_ROLE_ORDER = {role: index for index, role in enumerate(schemas.CHARACTER_ROLES)}


@dataclass
class Tools:
    """How a runner reaches the chains. ``None`` is the real thing: the LLM
    chain (``llm.run_chain``) and the registered adapters; a test hands in
    stand-ins. *time_fn* serves the LLM deadline and the image chain alike."""

    runner: Optional[Callable] = None
    time_fn: Callable[[], float] = time.monotonic
    sleep_fn: Callable[[float], None] = time.sleep
    adapters: Optional[dict] = None
    transport: Optional[Callable] = None

    def image_kwargs(self, ctx) -> dict:
        """The keyword arguments every ``refimages`` call takes from the step."""
        return {
            "env": ctx.settings_env, "on_log": ctx.on_log, "cancel": ctx.cancel,
            "adapters": self.adapters, "transport": self.transport,
            "sleep_fn": self.sleep_fn, "time_fn": self.time_fn,
        }

    def voice_kwargs(self, ctx) -> dict:
        """The keyword arguments ``voices.synthesize_sample`` takes from the step."""
        return {"env": ctx.settings_env, "on_log": ctx.on_log, "cancel": ctx.cancel,
                "adapters": self.adapters, "transport": self.transport}


# ------------------------------------------------------------------ the story

def require_status(story, status, message) -> None:
    """``StepFailed(message)`` unless the story's derived status has reached
    *status* (``defaults.STATUSES`` order). The status is derived from the
    approvals as a contiguous prefix, so an approval given on top of one that
    was since cleared does not count."""
    order = defaults.STATUSES
    reached = store_mod.derive_status(story.get("approvals"))
    if order.index(reached) < order.index(status):
        raise StepFailed(message)


def read_lock(stories, story_id) -> dict:
    """The story's style lock; ``StepFailed`` saying what to do without one."""
    return imaging.read_lock(stories, story_id, error=StepFailed)


def cast_order(characters) -> list:
    """*characters* as every phase-2 step walks them: leads, support,
    recurring, guest; then ``created_at``; then the id."""
    return sorted(characters, key=lambda doc: (_ROLE_ORDER.get(doc.get("role"), len(_ROLE_ORDER)),
                                               doc.get("created_at") or "", doc.get("char_id") or ""))


def name_key(name) -> str:
    """How two names are compared: case and runs of spaces do not count."""
    return " ".join(str(name).split()).casefold()


def by_name(entities) -> dict:
    """``{name_key: document}``; the first of two same-named entities wins."""
    found = {}
    for doc in entities:
        found.setdefault(name_key(doc["name"]), doc)
    return found


def quoted_list(items) -> str:
    """``'a'``, ``'a' and 'b'``, ``'a', 'b' and 'c'``."""
    quoted = [f"'{item}'" for item in items]
    if len(quoted) <= 1:
        return "".join(quoted)
    return ", ".join(quoted[:-1]) + " and " + quoted[-1]


def fresh_seed() -> int:
    """A regenerate's seed (0 .. 2**31-1): a provider that honours seeds
    must not answer the picture it answered before."""
    return secrets.randbelow(SEED_RANGE)


def target(kind, eid, *parts) -> str:
    """The regenerate target naming one entity's part: ``character:<id>:text``."""
    return ":".join((TARGET_KINDS[kind], eid) + tuple(parts))


# ------------------------------------------------------------------- entities

def write_character(stories, story_id, char_id, mutate, *, now) -> dict:
    """Re-read the character, apply *mutate* (in place), write it -- all under
    the uploads' lock, so an upload appended meanwhile survives. Returns what
    was written."""
    with uploads_mod._ENTRIES_LOCK:
        current = stories.read_entity(story_id, CHARACTERS, char_id)
        mutate(current)
        return stories.write_entity(story_id, CHARACTERS, current, now=now)


def write_entity(stories, story_id, kind, eid, mutate, *, now) -> dict:
    """``write_character``'s rule for any kind: re-read, *mutate*, write."""
    if kind == CHARACTERS:
        return write_character(stories, story_id, eid, mutate, now=now)
    current = stories.read_entity(story_id, kind, eid)
    mutate(current)
    return stories.write_entity(story_id, kind, current, now=now)


def clear_approval(stories, story_id, kind, eid, *, now) -> bool:
    """Clear the entity's ``approved_at`` when it is set (what it approved has
    changed); True when there was one. Nothing is written otherwise."""
    current = stories.read_entity(story_id, kind, eid)
    if current.get("approved_at") is None:
        return False

    def clear(doc):
        doc["approved_at"] = None

    write_entity(stories, story_id, kind, eid, clear, now=now)
    return True


def describe_uploads(ctx, stories, char_id, *, tools) -> list:
    """Describe every design reference of the character that has no
    description yet (``uploads.describe_upload``: U1 through VISION_CHAIN,
    every gate, booked), so K1 reads them all (``uploads.upload_notes``).

    Run right before K1, wherever K1 runs (the cast fill, and
    ``character:<id>:text``). A reference that cannot be described is a
    local failure: printed with its reason -- the job's feed and the story's
    activity log are its record -- and K1 proceeds without that note; the
    next K1 tries it again. Returns ``[(name, reason)]`` of those.
    ``Cancelled`` between two descriptions.
    """
    character = stories.read_entity(ctx.story_id, CHARACTERS, char_id)
    failed = []
    for entry in character["refs"]["uploads"]:
        if entry["description"] is not None:
            continue
        ctx.cancel.check()
        try:
            uploads_mod.describe_upload(stories, ctx.story_id, char_id, entry["name"], env=ctx.settings_env,
                                        on_log=ctx.on_log, cancel=ctx.cancel, adapters=tools.adapters,
                                        transport=tools.transport)
        except uploads_mod.UploadError as exc:
            reason = str(exc)
            if exc.reasons:
                reason += f" ({'; '.join(exc.reasons[:3])})"
            ctx.on_log(f"⚠️ {character['name']}: design reference {entry['name']} was not described -- {reason} "
                       "K1 writes the character without it.")
            failed.append((entry["name"], reason))
    return failed


def exists(stories, story_id, kind, eid) -> bool:
    """Whether the entity is still there (the user may delete one while a
    step runs); a document that does not validate still counts as there."""
    try:
        stories.read_entity(story_id, kind, eid)
    except KeyError:
        return False
    except schemas.SchemaError:
        return True
    return True


def has_file(stories, story_id, kind, eid, ref) -> bool:
    """Whether *ref* names an image whose file is really there (a regular
    file, never through a symlink): a reference to a missing file is a
    missing image."""
    if ref is None:
        return False
    try:
        stories.media_path(story_id, kind, eid, ref["name"])
    except KeyError:
        return False
    return True


def sample_names() -> tuple:
    from .. import voices

    return tuple(f"{voices.VOICE_SAMPLE_STEM}.{ext}" for ext in voices.KEPT_EXTENSIONS)


def has_sample(stories, story_id, char_id) -> bool:
    """Whether the character has a ``voice_sample.mp3``/``.wav``."""
    for name in sample_names():
        try:
            stories.media_path(story_id, CHARACTERS, char_id, name)
            return True
        except KeyError:
            continue
    return False


def drop_sample(stories, story_id, char_id) -> None:
    """Remove the character's voice sample (a new voice makes it stale): a
    regular file only; a symlink in its place is left alone, never followed."""
    try:
        folder = stories.entity_dir(story_id, CHARACTERS, char_id)
    except KeyError:
        return
    for name in sample_names():
        path = os.path.join(folder, name)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            os.remove(path)
        except OSError:
            pass
