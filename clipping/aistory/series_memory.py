"""Series memory as a fold over per-episode S3 entries (AI Story phase 5,
plan 11 stage 1; spec 2.6).

``season.json``'s ``series_memory`` keeps what the memory step (S3) wrote
for each episode::

    entries["ep01"] = {"recap", "hooks_opened", "hooks_closed",
                       "relationship_deltas", "script_rev", "at", "approved_at"}

and the three spec-2.6 fields the prompt builders read
(``context.memory_section``, E4) are **derived** from them by
:func:`fold_memory` and stored alongside, so those readers are unchanged:

- entries are applied in episode order (ep01, ep02, ...) whatever their dict
  order, so the fold is order-independent;
- ``recaps[epNN]`` is that entry's recap;
- ``open_hooks`` is owned by the fold: it starts empty; each entry first
  closes its ``hooks_closed`` -- each must be *exactly* the text of a hook
  open at that point, never matched loosely, or the fold fails -- then opens
  its ``hooks_opened``, in order (a hook already open stays open, once);
- ``relationship_state[pair]`` is the latest delta for that pair, in episode
  order; its keys sorted.

Every entry counts, approved or not, so a draft shows in the derived fields
while it awaits review; whether an entry is approved (``approved_at``) and
fresh (:func:`entry_is_stale`) is the gate's question, not the fold's.
``introduced`` is written by the cast path and never derived.

The hooks open when an episode starts (:func:`open_hooks_before`) and the
audience direction chosen on the episode before it (:func:`chosen_direction`)
are what the episode prompts read (plan 11 stage 3): the script step hands
them to E1/E3/E4, since ``prompts`` never imports this module.

Re-running memory for an episode replaces its one entry and re-folds
(:func:`merge_entry`), which is idempotent: the result depends on the
entries alone.

What the memory step and the gate read (plan 11 stage 4):
:func:`memory_state` -- ``none``, ``draft``, ``approved`` or ``stale`` (the
entry written from another revision of its script) -- and, for S3, the
relationships as they stand before an episode
(:func:`relationship_state_before`) and the pairs of the cast
(:func:`cast_pairs`). A :class:`FoldError` carries its failures as
``(episode, hook)`` too, so the step can name the later episode.

Pure: no I/O, no store. No function modifies its arguments; what they
return shares nothing with them.
"""

from __future__ import annotations

import copy
import re

from . import schemas

# The fields fold_memory derives, in series_memory's own order.
DERIVED_FIELDS = ("recaps", "open_hooks", "relationship_state")

_MEMORY_KEY = re.compile(schemas.MEMORY_KEY_PATTERN)
_PAIR_KEY = re.compile(schemas.RELATIONSHIP_PAIR_PATTERN)
_CHAR_ID = re.compile(schemas.CHAR_ID_PATTERN)


class FoldError(ValueError):
    """The entries do not fold: a hook is closed that is not open then.

    ``problems`` are the sentences (:func:`fold_errors`'); ``closings`` the
    same failures as ``(episode, hook)``: the episode whose entry closes
    *hook* while it is not open then (what the memory step names)."""

    def __init__(self, problems, closings=()):
        self.problems = list(problems)
        self.closings = list(closings)
        super().__init__("; ".join(self.problems))


# ------------------------------------------------------------------- keys

def memory_key(ep) -> str:
    """``"ep01"`` for episode 1: the key of ``entries`` and ``recaps`` (the
    same as ``episode_common.recap_key``). ValueError unless *ep* is an int
    in 1..99 (not a bool, not ``"1"``, not ``1.0``)."""
    if type(ep) is not int or not 1 <= ep <= 99:
        raise ValueError(f"an episode is an int from 1 to 99, not {ep!r}")
    return f"ep{ep:02d}"


def _episode_of(key) -> int:
    if not (isinstance(key, str) and _MEMORY_KEY.fullmatch(key)):
        raise ValueError(f"{key!r} is not an episode key (ep01..ep99)")
    return int(key[2:])


def pair_key(a, b) -> str:
    """The ``relationship_state`` key of characters *a* and *b*:
    ``"<char_a>|<char_b>"`` with the two ids sorted. ValueError unless both
    are character ids and they differ."""
    for cid in (a, b):
        if not (isinstance(cid, str) and _CHAR_ID.fullmatch(cid)):
            raise ValueError(f"{cid!r} is not a character id")
    if a == b:
        raise ValueError(f"a pair is two different characters, not {a!r} twice")
    first, second = sorted((a, b))
    return f"{first}|{second}"


def pair_parts(key):
    """``(char_a, char_b)`` of a pair key made by :func:`pair_key`, or None
    for anything else (unsorted, one id twice, not two character ids)."""
    match = _PAIR_KEY.fullmatch(key) if isinstance(key, str) else None
    if match is None or not match.group(1) < match.group(2):
        return None
    return match.group(1), match.group(2)


def _names(key, char_id) -> bool:
    """Whether the relationship key *key* names *char_id*: any key split on
    ``|``, so a hand-written key of another shape is covered too."""
    return isinstance(key, str) and char_id in key.split("|")


# ------------------------------------------------------------------- fold

def _ordered(entries):
    return sorted((entries or {}).items(), key=lambda item: _episode_of(item[0]))


def _fold(entries):
    """``(derived, closings)``: the folded fields, and each ``(key, hook)``
    closed while it is not open then."""
    recaps, open_hooks, state, closings = {}, [], {}, []
    for key, entry in _ordered(entries):
        for hook in entry["hooks_closed"]:
            if hook in open_hooks:
                open_hooks.remove(hook)
            else:
                closings.append((key, hook))
        for hook in entry["hooks_opened"]:
            if hook not in open_hooks:
                open_hooks.append(hook)
        recaps[key] = entry["recap"]
        state.update(entry["relationship_deltas"])
    derived = {"recaps": recaps, "open_hooks": open_hooks, "relationship_state": dict(sorted(state.items()))}
    return derived, closings


def _problem(key, hook) -> str:
    return f"{key} closes {hook!r}, which is not an open hook then (a hook is closed only by its exact text)"


def fold_errors(entries) -> list:
    """Why *entries* do not fold (a closed hook that is not open then), or []."""
    return [_problem(key, hook) for key, hook in _fold(entries)[1]]


def fold_memory(entries) -> dict:
    """``{"recaps", "open_hooks", "relationship_state"}`` folded from
    *entries* (``series_memory.entries``; None or {} fold to empty fields).
    The entries must each be valid (``schemas.memory_entry_errors``);
    :class:`FoldError` (a ValueError) when a closed hook is not open then."""
    derived, closings = _fold(entries)
    if closings:
        raise FoldError([_problem(key, hook) for key, hook in closings],
                        [(_episode_of(key), hook) for key, hook in closings])
    return derived


def _before(season, ep) -> dict:
    """The fold of the entries of the episodes before *ep*."""
    memory_key(ep)
    entries = entry_map(season)
    return fold_memory({key: entry for key, entry in entries.items() if _episode_of(key) < ep})


def open_hooks_before(season, ep) -> list:
    """The hooks open when episode *ep* starts: the fold of the entries of
    the episodes before it (what S3 may close for *ep*, and what E1 of *ep*
    must pay off). The stored ``open_hooks`` is the fold of every entry,
    later ones included."""
    return _before(season, ep)["open_hooks"]


def relationship_state_before(season, ep) -> dict:
    """The relationships as they stand when episode *ep* starts: the fold of
    the entries before it (what S3 of *ep* is shown as "current"). The
    stored ``relationship_state`` folds later episodes' deltas too."""
    return _before(season, ep)["relationship_state"]


def fold_ledger(season, *, before_ep, knowledge) -> dict:
    """``{char_id: state}``: where every character stands when episode
    *before_ep* starts (phase 7 stage 5d, DEC-229, A13/A14) -- each
    character's latest ``ledger`` state from the entries before it (the
    memory step's L1 call, stage 5d), starting from *knowledge*'s own
    ``ledger_seed`` (stage 5b; ``{}`` without one). An entry with no
    ``ledger`` at all (a legacy story, or one the memory step wrote before
    this stage) changes nothing for it.

    Unlike :func:`fold_memory`'s fields, the ledger is never stored on the
    season: it is recomputed by this fold wherever it is read
    (``context.ledger_before``, which reads the knowledge base and returns
    None for a legacy story instead of calling this at all). ValueError as
    :func:`memory_key` for *before_ep*."""
    memory_key(before_ep)
    ledger = {cid: dict(state) for cid, state in ((knowledge or {}).get("ledger_seed") or {}).items()}
    for key, entry in _ordered(entry_map(season)):
        if _episode_of(key) >= before_ep:
            break
        for cid, state in (entry.get("ledger") or {}).items():
            ledger[cid] = dict(state)
    return ledger


def cast_pairs(char_ids) -> list:
    """Every :func:`pair_key` of two of *char_ids*, sorted: the pairs S3 may
    report a delta for (``schemas.s3_schema``'s enum)."""
    ids = sorted(set(char_ids))
    return [pair_key(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]


# --------------------------------------------------------- audience feedback

def chosen_direction(season, ep):
    """The text of the audience direction chosen on episode *ep*'s feedback
    (``audience_feedback``), which steers the next episode's E1 (and N1),
    or None.

    The latest of *ep*'s feedback items whose choice was made decides
    (``chosen_direction`` present): its ``directions[chosen_direction]``,
    or None when it was approved with no direction (null). An item still
    waiting for its choice (the key absent: pasted, digested, not approved
    yet) decides nothing, so it never hides an earlier choice.
    ValueError unless *ep* is an episode (:func:`memory_key`)."""
    memory_key(ep)
    for item in reversed((season or {}).get("audience_feedback") or []):
        if item.get("ep") != ep or "chosen_direction" not in item:
            continue
        index = item["chosen_direction"]
        return None if index is None else item["directions"][index]
    return None


# ------------------------------------------------------------------ entries

def entry_map(season) -> dict:
    """The season's ``series_memory.entries`` ({} when it has none), as is."""
    return ((season or {}).get("series_memory") or {}).get("entries") or {}


def entry_for(season, ep):
    """A copy of episode *ep*'s memory entry, or None when it has none."""
    entry = entry_map(season).get(memory_key(ep))
    return copy.deepcopy(entry) if entry is not None else None


def entry_errors(entry, *, open_hooks, char_ids, path="$") -> list:
    """Every check of one S3 entry: what the entry decides on its own
    (``schemas.memory_entry_errors``: the recap's 40 words, hooks of at most
    120 characters, at most 3 opened, sorted pair keys ...), then against
    the story: each ``hooks_closed`` exactly one of *open_hooks* (the hooks
    open before the entry's episode, :func:`open_hooks_before`), and each
    pair of characters in *char_ids* (the story's cast). Opening a hook that
    is already open is not an error: it stays open, once."""
    errors = schemas.memory_entry_errors(entry, path)
    if errors:
        return errors

    errors = []
    still_open = list(open_hooks)
    for i, hook in enumerate(entry["hooks_closed"]):
        if hook not in still_open:
            errors.append(f"{path}.hooks_closed[{i}]: {hook!r} is not an open hook "
                          "(a hook is closed only by its exact text)")
    known = set(char_ids)
    for key in entry["relationship_deltas"]:
        unknown = [cid for cid in pair_parts(key) if cid not in known]
        if unknown:
            errors.append(f"{path}.relationship_deltas: {key!r} names {', '.join(unknown)}, "
                          "not a character of the story")
    return errors


def merge_entry(season, ep, entry) -> dict:
    """A new season: *season* with episode *ep*'s memory entry replaced by
    *entry* (or added) and the derived fields re-folded from all entries.

    Pure: *season* and *entry* are deep-copied, never modified. The entries
    are kept in episode order. ValueError for an episode outside 1..99, an
    entry that is not valid on its own (``schemas.memory_entry_errors``), or
    entries that no longer fold (:class:`FoldError`: say a replaced entry no
    longer opens a hook a later episode closes)."""
    key = memory_key(ep)
    errors = schemas.memory_entry_errors(entry)
    if errors:
        raise ValueError(f"the memory entry of {key} is not valid: {'; '.join(errors)}")
    new = copy.deepcopy(season)
    memory = new["series_memory"]
    entries = dict(memory.get("entries") or {})
    entries[key] = copy.deepcopy(entry)
    entries = dict(sorted(entries.items(), key=lambda item: _episode_of(item[0])))
    memory["entries"] = entries
    memory.update(fold_memory(entries))
    return new


def drop_character(memory, char_id):
    """``(new_memory, removed)``: *memory* (a ``series_memory``) without any
    relationship key naming *char_id* -- in ``relationship_state`` and in
    every entry's ``relationship_deltas`` -- without *char_id*'s own key in
    any entry's ``ledger`` (phase 7 stage 5d's follow-up, DEC-229: a
    dropped character keeps no continuity state either) -- and, when it has
    entries, its derived fields re-folded (the ledger is never one of
    them: :func:`fold_ledger` is not stored, nothing here re-runs it).

    *removed* is ``{"relationship_state": [keys], "entries": {"epNN":
    [keys]}, "ledger": ["epNN", ...]}``, empty lists/dict when nothing named
    it. The first two keys are exactly what this function returned before
    stage 5d (``store.py``'s caller, written then, reads only those two: a
    third key it does not know to look for is simply not in the message it
    builds from them). Pure: *memory* is never modified. ``introduced`` is
    left to the caller."""
    new = copy.deepcopy(memory)
    removed = {"relationship_state": [], "entries": {}, "ledger": []}
    state = new.get("relationship_state")
    if isinstance(state, dict):
        removed["relationship_state"] = [key for key in state if _names(key, char_id)]
        for key in removed["relationship_state"]:
            del state[key]
    entries = new.get("entries") or {}
    for ep_key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        deltas = entry.get("relationship_deltas")
        if isinstance(deltas, dict):
            keys = [key for key in deltas if _names(key, char_id)]
            for key in keys:
                del deltas[key]
            if keys:
                removed["entries"][ep_key] = keys
        ledger = entry.get("ledger")
        if isinstance(ledger, dict) and char_id in ledger:
            del ledger[char_id]
            removed["ledger"].append(ep_key)
    # A stored season's entries fold (the store validates it on every read);
    # ones that do not are left for the season's validator to report.
    if entries and _foldable(entries):
        derived, closings = _fold(entries)
        if not closings:
            new.update(derived)
    return new, removed


def drop_episode(memory, ep):
    """``(new_memory, removed)``: *memory* (a ``series_memory``) without
    episode *ep*'s entry and its recap -- the episode was archived
    (``StoryStore.discard_episode``) and the one written in its place starts
    a new script at rev 1, so an entry left behind (``script_rev`` 1) would
    read as current against it (:func:`entry_is_stale`).

    While the memory had entries, its derived fields become the fold of the
    entries left (empty ones when none is left): :class:`FoldError` (a
    ValueError, nothing returned) when they no longer fold -- a later
    episode closes a hook *ep* opened, so that episode goes first. A memory
    with no entries (written before phase 5) loses only the recap.
    ``introduced`` is the cast's and stays: the characters do.

    *removed* is ``{"entry": bool, "recap": bool}``. Pure: *memory* is never
    modified. ValueError as :func:`memory_key` for *ep*."""
    key = memory_key(ep)
    new = copy.deepcopy(memory)
    entries = dict(new.get("entries") or {})
    removed = {"entry": key in entries, "recap": key in (new.get("recaps") or {})}
    if removed["entry"]:
        del entries[key]
        new["entries"] = entries
        new.update(fold_memory(entries))
    elif removed["recap"]:
        del new["recaps"][key]
    return new, removed


def _foldable(entries) -> bool:
    return all(isinstance(key, str) and _MEMORY_KEY.fullmatch(key) and not schemas.memory_entry_errors(entry)
               for key, entry in entries.items())


# ---------------------------------------------------------------- staleness

def entry_is_stale(entry, script_doc) -> bool:
    """Whether *entry* was written from another revision of its episode's
    script than *script_doc* (``script_rev`` against the script's ``rev``),
    or from a script that is gone (*script_doc* None)."""
    return script_doc is None or entry["script_rev"] != script_doc["rev"]


# What an episode's memory is, for the gate and the episode views (plan 11
# stage 4): no entry; written, waiting for its approval; approved; or
# written from another revision of the script (stale, whatever its approval).
MEMORY_STATES = ("none", "draft", "approved", "stale")


def memory_state(season, ep, script_doc) -> str:
    """``none`` | ``draft`` | ``approved`` | ``stale`` (:data:`MEMORY_STATES`)
    of episode *ep*'s memory entry against *script_doc*, its script as it is
    now (None when it has none). Stale wins over approved: an approval of
    another revision of the script approves nothing now."""
    entry = entry_map(season).get(memory_key(ep))
    if entry is None:
        return "none"
    if entry_is_stale(entry, script_doc):
        return "stale"
    return "approved" if entry["approved_at"] else "draft"


def is_stale(season, ep, script_doc) -> bool:
    """:func:`entry_is_stale` for episode *ep*'s entry in *season*. KeyError
    when the episode has no entry: a missing entry is not "fresh", so the
    caller asks :func:`entry_for` first."""
    entry = entry_map(season).get(memory_key(ep))
    if entry is None:
        raise KeyError(f"no series memory entry for {memory_key(ep)}")
    return entry_is_stale(entry, script_doc)
