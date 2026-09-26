"""The on-disk story workspace and its index (spec 2, 2.1; phase-1 plan 2).

Layout, under the same ``outputs/`` directory the job store uses::

    outputs/stories.json            # the index (stories_index_v1)
    outputs/stories/<story_id>/
        story.json                  # StoryBible (story_bible_v1)
        style_lock.json             # StyleLock (style_lock_v1)
        concepts.json               # generated concept cards
        activity.log                # one line per thing a step printed

Rules this module keeps:

- A story id is 12 lowercase hex characters and is checked **before** any path
  is built from it, so ``../x`` or an absolute path never reaches the
  filesystem. The per-story folder must also be a real directory directly
  inside ``outputs/stories/`` -- never a symlink, never through one -- which
  is the same rule ``web/api/cleanup.py::contained`` applies to job folders
  (re-implemented here: ``clipping/`` does not import ``web/``).
- Every JSON write is atomic: a temp file in the same directory, then
  ``os.replace``; a failure leaves the previous file byte-identical and no
  temp file behind. (``outputs/jobs.json`` is written in place; this does not
  copy that.)
- ``status`` is derived from ``approvals`` on every save and never taken from
  the caller, so an edit that clears an approval cannot leave a stale status.
- The index is a cache of the folders. Missing, torn or foreign, it is rebuilt
  from them and the rebuild is printed; a folder that does not hold a valid
  story is skipped and printed, never deleted.
- One re-entrant lock per resolved ``stories/`` root, shared by every
  ``StoryStore`` in the process: the API and the worker thread each build
  their own instance, and the index is a read-modify-write. Processes (the CLI
  next to the server) do not coordinate; writes stay atomic and the index can
  be rebuilt.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
from datetime import datetime, timezone

from . import defaults, schemas, templates

STORY_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")

STORIES_DIRNAME = "stories"
INDEX_FILENAME = "stories.json"
INDEX_SCHEMA = "stories_index_v1"
STORY_SCHEMA = "story_bible_v1"
STORY_FILENAME = "story.json"
ACTIVITY_LOG = "activity.log"

# The JSON documents of a story that read_doc/write_doc may name. Nothing else:
# a name is never joined onto a path unless it is one of these.
DOC_NAMES = (STORY_FILENAME, "style_lock.json", "concepts.json")

INDEX_FIELDS = ("story_id", "title", "language", "style_template_id", "status", "created_at", "updated_at")

# story.json keys a caller may never change once the story exists.
_FROZEN_KEYS = ("$schema", "story_id", "created_at")

# Each approval moves the story one step, but only on top of the previous one.
_APPROVAL_STEPS = (
    ("concept", "concept_chosen"),
    ("bible", "bible_approved"),
    ("style", "style_approved"),
)

_PROFILE_CHOICES = {
    "tier": defaults.TIERS,
    "route": defaults.ROUTES,
    "consistency_mode": defaults.CONSISTENCY_MODES,
    "budget_profile": defaults.BUDGET_PROFILES,
}

_INDEX_ENTRY_SCHEMA = {
    "type": "object",
    "properties": {
        "story_id": {"type": "string", "pattern": schemas.STORY_ID_PATTERN},
        "title": {"type": "string"},
        "language": {"type": "string"},
        "style_template_id": {"type": ["string", "null"]},
        "status": {"type": "string"},
        "created_at": {"type": "string"},
        "updated_at": {"type": "string"},
    },
    "required": list(INDEX_FIELDS),
}

# mkstemp creates its file 0600. A story workspace sits in a bind mount the
# human reads and edits from the host, as they do outputs/jobs.json (written
# with a plain open(), so the usual 0644).
_FILE_MODE = 0o644

_CREATE_ATTEMPTS = 8

_INDEX_MISSING = "the index is missing"

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def default_outputs_dir() -> str:
    """``<repo>/outputs`` -- the directory ``web/api/store.py`` keeps jobs.json in."""
    return os.path.join(_PROJECT_ROOT, "outputs")


def new_story_id() -> str:
    return uuid.uuid4().hex[:12]


def is_story_id(value) -> bool:
    """Whether *value* is a well-formed story id. ``fullmatch``, not ``match``:
    ``$`` also matches before a trailing newline."""
    return isinstance(value, str) and STORY_ID_PATTERN.fullmatch(value) is not None


def derive_status(approvals) -> str:
    """The story status that *approvals* amount to.

    ``draft -> concept_chosen -> bible_approved -> style_approved``. Only a
    contiguous prefix counts: a style approval without a bible approval is not
    an approval (the bible it was given against no longer stands).
    """
    status = defaults.STATUSES[0]
    if not isinstance(approvals, dict):
        return status
    for key, reached in _APPROVAL_STEPS:
        value = approvals.get(key)
        if not (isinstance(value, str) and value):
            break
        status = reached
    return status


# ----------------------------------------------------------------- helpers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contained(root: str, name, *, want_dir: bool):
    """The real path of *name* directly inside *root*, or None if it is
    anything else. The rules of ``web/api/cleanup.py::contained``."""
    if not isinstance(name, str) or name in ("", ".", ".."):
        return None
    if "/" in name or "\\" in name or os.path.splitdrive(name)[0] or os.path.isabs(name):
        return None
    base = os.path.realpath(root)
    path = os.path.join(base, name)
    if os.path.islink(path):
        return None
    real = os.path.realpath(path)
    if real == base or os.path.dirname(real) != base:
        return None
    if want_dir and not os.path.isdir(real):
        return None
    if not want_dir and not os.path.isfile(real):
        return None
    return real


def _atomic_write_json(path: str, data) -> None:
    """Write *data* to *path* so a reader sees the old file or the new one.

    The ledger's pattern (``ledger.CostLedger._write``): a temp file in the
    same directory, then ``os.replace``; the temp file is removed on any
    failure. Keys keep their insertion order -- story.json is meant to be read
    by a human, top to bottom.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".story-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _merge_generation_profile(partial) -> dict:
    profile = defaults.default_generation_profile()
    if partial is None:
        return profile
    if not isinstance(partial, dict):
        raise ValueError(f"generation_profile must be an object, not {type(partial).__name__}")
    for key, value in partial.items():
        choices = _PROFILE_CHOICES.get(key)
        if choices is None:
            raise ValueError(
                f"generation_profile has no field {key!r} (known: {', '.join(_PROFILE_CHOICES)})")
        # type() as well as membership: True == 1 and 1.0 == 1 in Python.
        if type(value) is not type(choices[0]) or value not in choices:
            raise ValueError(
                f"generation_profile.{key} must be one of {list(choices)}, not {value!r}")
        profile[key] = value
    return profile


def _index_entry(doc: dict) -> dict:
    return {key: doc[key] for key in INDEX_FIELDS}


# ------------------------------------------------------------------- locks

_locks_guard = threading.Lock()
_locks: dict = {}


def _lock_for(root: str) -> threading.RLock:
    key = os.path.realpath(root)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


# ------------------------------------------------------------------- store

class StoryStore:
    def __init__(self, outputs_dir, *, id_factory=new_story_id, on_log=print):
        self.outputs_dir = os.path.abspath(str(outputs_dir))
        self.root = os.path.join(self.outputs_dir, STORIES_DIRNAME)
        self.index_path = os.path.join(self.outputs_dir, INDEX_FILENAME)
        self._id_factory = id_factory
        self._on_log = on_log
        self._lock = _lock_for(self.root)

    # -------------------------------------------------------------- paths

    @staticmethod
    def _check_id(story_id) -> None:
        if not is_story_id(story_id):
            raise KeyError(story_id)

    @staticmethod
    def _check_doc_name(name) -> None:
        if not isinstance(name, str) or name not in DOC_NAMES:
            raise ValueError(f"not a story document: {name!r} (allowed: {', '.join(DOC_NAMES)})")

    def story_dir(self, story_id) -> str:
        """The real path of the story's folder; KeyError if the id is malformed
        or the folder is not a real directory directly inside ``stories/``."""
        self._check_id(story_id)
        real = _contained(self.root, story_id, want_dir=True)
        if real is None:
            raise KeyError(story_id)
        return real

    def _label(self, story_id) -> str:
        return f"outputs/{STORIES_DIRNAME}/{story_id}/"

    # ------------------------------------------------------------ logging

    def _log(self, messages) -> None:
        # Called after the lock is released: on_log may be a tee into a job's
        # activity feed, which takes locks of its own.
        for message in messages:
            try:
                self._on_log(message)
            except Exception:
                pass

    # -------------------------------------------------------------- story

    def _read_story(self, story_id: str) -> dict:
        """Load and validate story.json; KeyError if there is no story."""
        directory = self.story_dir(story_id)
        path = os.path.join(directory, STORY_FILENAME)
        name = f"{STORIES_DIRNAME}/{story_id}/{STORY_FILENAME}"
        if os.path.islink(path):
            raise schemas.SchemaError(name, ["story.json is a symlink; it is never followed"])
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except FileNotFoundError:
            raise KeyError(story_id) from None
        except ValueError as exc:
            raise schemas.SchemaError(name, [f"not valid JSON: {exc}"]) from None
        errors = schemas.story_bible_errors(doc)
        if not errors and doc["story_id"] != story_id:
            errors = [f"$.story_id: {doc['story_id']!r} does not match its folder {story_id!r}"]
        if errors:
            raise schemas.SchemaError(name, errors)
        return doc

    def _save_story(self, doc: dict, *, now) -> list:
        """Validate, write story.json atomically, update the index entry.
        Returns the log lines to print once the lock is released."""
        errors = schemas.story_bible_errors(doc)
        if errors:
            raise schemas.SchemaError(STORY_SCHEMA, errors)
        directory = self.story_dir(doc["story_id"])
        _atomic_write_json(os.path.join(directory, STORY_FILENAME), doc)
        return self._upsert_index(doc, now=now)

    def create(self, *, language, seed_text=None, style_template_id=None, generation_profile=None, now) -> dict:
        """Create a draft story. There is no default language (``defaults``)."""
        if not isinstance(language, str) or language not in schemas.LANGUAGES:
            raise ValueError(f"language must be one of {list(schemas.LANGUAGES)}, not {language!r}")
        if seed_text is not None and not isinstance(seed_text, str):
            raise ValueError(f"seed_text must be a string or null, not {type(seed_text).__name__}")
        if style_template_id is not None and style_template_id not in templates.list_style_ids():
            raise ValueError(
                f"unknown style template {style_template_id!r} "
                f"(shipped: {', '.join(templates.list_style_ids())})")
        profile = _merge_generation_profile(generation_profile)

        approvals = {"concept": None, "bible": None, "style": None}
        doc = {
            "$schema": STORY_SCHEMA,
            "story_id": None,
            "title": "",
            "language": language,
            "seed_text": seed_text,
            "concept_id": None,
            "concept": None,
            "logline": None,
            "premise": None,
            "tone": None,
            "genre_tags": [],
            "world": None,
            "themes_and_values": [],
            "audience": None,
            "why_come_back": [],
            "cast_ids": [],
            "place_ids": [],
            "prop_ids": [],
            "style_template_id": style_template_id,
            "episode_template_id": defaults.EPISODE_TEMPLATE_ID,
            "generation_profile": profile,
            "narrator": {"enabled": False, "voice": None},
            "approvals": approvals,
            "status": derive_status(approvals),
            "created_at": now,
            "updated_at": now,
        }

        with self._lock:
            for _ in range(_CREATE_ATTEMPTS):
                story_id = self._id_factory()
                if not is_story_id(story_id):
                    raise ValueError(f"id_factory returned a malformed story id: {story_id!r}")
                doc["story_id"] = story_id
                # Before anything exists on disk, so a refused story leaves nothing.
                errors = schemas.story_bible_errors(doc)
                if errors:
                    raise schemas.SchemaError(STORY_SCHEMA, errors)
                os.makedirs(self.root, exist_ok=True)
                try:
                    os.mkdir(os.path.join(self.root, story_id))
                except FileExistsError:
                    continue
                break
            else:
                raise RuntimeError(f"no free story id after {_CREATE_ATTEMPTS} attempts")
            try:
                messages = self._save_story(doc, now=now)
            except BaseException:
                self._discard_new_folder(story_id)
                raise
        self._log(messages)
        return copy.deepcopy(doc)

    def _discard_new_folder(self, story_id: str) -> None:
        """Undo the mkdir of a create whose story.json never landed. rmdir, not
        rmtree: only a folder left empty goes. One whose story.json did land
        (the index write failed) is kept for the next rebuild."""
        try:
            os.rmdir(os.path.join(self.root, story_id))
        except OSError:
            pass

    def get(self, story_id) -> dict:
        """The validated story. KeyError for a malformed or unknown id;
        ``SchemaError`` for a story.json that does not validate (never repaired)."""
        self._check_id(story_id)
        with self._lock:
            return self._read_story(story_id)

    def update(self, story_id, mutate, *, now) -> dict:
        """Apply *mutate* (it edits the dict in place) and save.

        ``status`` is re-derived from ``approvals`` whatever *mutate* did to it,
        ``updated_at`` becomes *now*, and ``$schema``/``story_id``/``created_at``
        may not change. Nothing is written unless the result validates.
        """
        self._check_id(story_id)
        with self._lock:
            current = self._read_story(story_id)
            doc = copy.deepcopy(current)
            mutate(doc)
            for key in _FROZEN_KEYS:
                if key not in doc or doc[key] != current[key]:
                    raise ValueError(f"{key} cannot be changed ({current[key]!r})")
            doc["status"] = derive_status(doc.get("approvals"))
            doc["updated_at"] = now
            messages = self._save_story(doc, now=now)
        self._log(messages)
        return copy.deepcopy(doc)

    # ----------------------------------------------------- other documents

    def read_doc(self, story_id, name):
        """One of the story's JSON documents, or None if it does not exist yet."""
        self._check_doc_name(name)
        self._check_id(story_id)
        if name == STORY_FILENAME:
            return self.get(story_id)
        with self._lock:
            path = os.path.join(self.story_dir(story_id), name)
            label = f"{STORIES_DIRNAME}/{story_id}/{name}"
            if os.path.islink(path):
                raise schemas.SchemaError(label, [f"{name} is a symlink; it is never followed"])
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except FileNotFoundError:
                return None
            except ValueError as exc:
                raise schemas.SchemaError(label, [f"not valid JSON: {exc}"]) from None
        if not isinstance(doc, dict):
            raise schemas.SchemaError(label, ["$: expected a JSON object"])
        return doc

    def write_doc(self, story_id, name, doc, *, now, validator=None) -> dict:
        """Write one of the story's documents (not story.json: use ``update``).

        ``updated_at`` becomes *now*; *validator*, when given, returns a list of
        errors and a non-empty one refuses the write. The story's own
        ``updated_at`` (story.json and the index) moves to *now* as well.
        """
        self._check_doc_name(name)
        if name == STORY_FILENAME:
            raise ValueError("story.json is written through update(), which derives its status")
        self._check_id(story_id)
        if not isinstance(doc, dict) or not isinstance(doc.get("$schema"), str) or not doc["$schema"]:
            raise ValueError(f"{name} must be an object with a '$schema' string")
        new = copy.deepcopy(doc)
        new["updated_at"] = now
        if validator is not None:
            errors = validator(new)
            if errors:
                raise schemas.SchemaError(name, errors)
        with self._lock:
            directory = self.story_dir(story_id)
            story = self._read_story(story_id)  # a document belongs to a valid story
            _atomic_write_json(os.path.join(directory, name), new)
            story["status"] = derive_status(story["approvals"])
            story["updated_at"] = now
            messages = self._save_story(story, now=now)
        self._log(messages)
        return copy.deepcopy(new)

    def append_activity(self, story_id, line) -> None:
        """Append one line to the story's activity.log. Best effort: never
        raises (like the job store's persistence), and a malformed id or a
        missing story is ignored."""
        try:
            if not is_story_id(story_id):
                return
            text = " ".join(str(line).splitlines())
            with self._lock:
                path = os.path.join(self.story_dir(story_id), ACTIVITY_LOG)
                if os.path.islink(path):
                    return
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
        except Exception:
            pass

    # -------------------------------------------------------------- index

    def _read_index(self):
        """``(stories, None)``, or ``(None, reason)`` when the index is unusable."""
        try:
            with open(self.index_path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return None, _INDEX_MISSING
        except (OSError, ValueError) as exc:
            return None, f"the index is unreadable ({type(exc).__name__}: {exc})"
        if not isinstance(data, dict):
            return None, "the index is not a JSON object"
        if data.get("$schema") != INDEX_SCHEMA:
            return None, f"the index $schema is {data.get('$schema')!r}, not {INDEX_SCHEMA!r}"
        stories = data.get("stories")
        if not isinstance(stories, dict):
            return None, "the index has no 'stories' object"
        for key, entry in stories.items():
            if not is_story_id(key) or schemas.validate(entry, _INDEX_ENTRY_SCHEMA) \
                    or entry["story_id"] != key:
                return None, f"the index entry {key!r} is malformed"
        return stories, None

    def _write_index(self, stories: dict, *, now=None) -> None:
        _atomic_write_json(self.index_path, {
            "$schema": INDEX_SCHEMA,
            "stories": stories,
            "updated_at": now or _utc_now(),
        })

    def _other_story_folders(self, story_id) -> bool:
        try:
            return any(is_story_id(n) and n != story_id for n in os.listdir(self.root))
        except OSError:
            return False

    def _upsert_index(self, doc: dict, *, now) -> list:
        stories, reason = self._read_index()
        messages = []
        if stories is None:
            if reason == _INDEX_MISSING and not self._other_story_folders(doc["story_id"]):
                stories = {}  # the first story: there is nothing to rebuild from
            else:
                # story.json is already written, so the rebuild includes this story.
                _, messages = self._rebuild_locked(reason, now=now)
                stories = self._read_index()[0] or {}
        stories[doc["story_id"]] = _index_entry(doc)
        self._write_index(stories, now=now)
        return messages

    def _rebuild_locked(self, reason, *, now=None):
        entries = {}
        messages = []
        if os.path.isdir(self.root):
            for name in sorted(os.listdir(self.root)):
                if not is_story_id(name):
                    continue
                label = self._label(name)
                if _contained(self.root, name, want_dir=True) is None:
                    messages.append(f"Skipped {label}: not a real directory inside outputs/{STORIES_DIRNAME}/")
                    continue
                try:
                    doc = self._read_story(name)
                except KeyError:
                    messages.append(f"Skipped {label}: no {STORY_FILENAME}")
                    continue
                except schemas.SchemaError as exc:
                    detail = "; ".join(exc.errors)
                    if len(detail) > 300:
                        detail = detail[:297] + "..."
                    messages.append(f"Skipped {label}: {STORY_FILENAME} is invalid ({detail})")
                    continue
                entries[name] = _index_entry(doc)
        self._write_index(entries, now=now)
        messages.append(
            f"Rebuilt outputs/{INDEX_FILENAME} from {len(entries)} story folder(s): {reason}")
        return len(entries), messages

    def rebuild_index(self, reason) -> int:
        """Rewrite the index from the story folders; returns how many it holds."""
        with self._lock:
            count, messages = self._rebuild_locked(reason)
        self._log(messages)
        return count

    def list(self) -> list:
        """Index entries, most recently updated first."""
        messages = []
        with self._lock:
            stories, reason = self._read_index()
            if stories is None:
                if reason == _INDEX_MISSING and not os.path.lexists(self.root):
                    # A fresh install: nothing to rebuild, nothing was lost.
                    return []
                _, messages = self._rebuild_locked(reason)
                stories = self._read_index()[0] or {}
            entries = [dict(entry) for entry in stories.values()]
        self._log(messages)
        entries.sort(key=lambda e: (e["updated_at"], e["created_at"], e["story_id"]), reverse=True)
        return entries

    # ------------------------------------------------------------- delete

    def delete(self, story_id) -> dict:
        """Remove the story's folder and its index entry.

        Returns ``{"removed": [...], "kept": [...]}`` like
        ``cleanup.remove_job_files``. The folder is removed only as a real
        directory directly inside ``stories/``; a symlink in its place is kept
        and reported, never followed. The index entry goes whenever the id is
        well-formed. KeyError for a malformed id, or for an id with neither a
        folder nor an index entry. Knows nothing about jobs: the caller checks
        for a running step first.
        """
        self._check_id(story_id)
        report = {"removed": [], "kept": []}
        messages = []
        label = self._label(story_id)
        try:
            with self._lock:
                stories, reason = self._read_index()
                if stories is None:
                    _, rebuilt = self._rebuild_locked(reason)
                    messages.extend(rebuilt)
                    stories = self._read_index()[0] or {}
                path = os.path.join(self.root, story_id)
                has_folder = os.path.lexists(path)
                if not has_folder and story_id not in stories:
                    raise KeyError(story_id)

                real = _contained(self.root, story_id, want_dir=True)
                if real is not None:
                    try:
                        shutil.rmtree(real)
                        report["removed"].append(label)
                    except OSError as exc:
                        report["kept"].append(f"{label} ({exc})")
                elif has_folder:
                    why = "a symlink, never followed" if os.path.islink(path) else "not a directory"
                    report["kept"].append(f"{label} ({why})")

                if story_id in stories:
                    del stories[story_id]
                    self._write_index(stories)
        finally:
            self._log(messages)
        return report
