"""The generation cache and submit journal: never lose a paid generation (DEC-151..154).

One JSON entry per request under a root directory (a story's ``cache/gen``),
named by the request's key, with the request's outputs beside it:

* the **key** is the sha256 of the request's canonical JSON -- kind, link,
  prompt, negative, the sha256 of each reference file's *bytes*, seed, size,
  text, voice, rate, pitch, template and take; a video request adds its
  clip's length (``clip_s``), frame rate and native audio, and its keyframe
  is its one reference. Where the answer is written (``out_dir``,
  ``extra["name"]``) is not part of it. An image or video request without a
  seed has **no key**: nothing is cached or journaled, and the runner takes
  exactly the path it takes without a cache (DEC-154). Neither has a video
  request without its clip length, nor a vision request: the key has no
  field for the frames to describe.
* the **entry** (``gen_journal_v1``) follows one request through
  ``sending`` (a paid call is about to go out) -> ``submitted`` (a queue
  acknowledged it with a request id) -> ``done`` (its outputs are kept here),
  or ends ``failed`` (the provider refused or settled it) or ``lost`` (its
  outcome can no longer be known).

**Booking.** The journal books through the caller's ``book(entry)``: a ledger
row, and ``budget.record`` when paid, live on the caller's side. It books a
queued request the moment the queue acknowledges it, a synchronous paid one on
its outcome, and a free answer at $0 when it arrives, and stamps ``booked``.
With a cache the caller books nothing itself: every answer the runner returns
carries ``meta["booked"]``. The rule is conservative (DEC-153): a paid request
is booked unless an HTTP 4xx answer, or a failure before the transport was
called, proves it unbilled. Every state is written *before* the booking it
calls for, and an entry found ``sending``, or ``submitted``/``done`` without a
stamp, is booked on sight -- so a crash can book one request twice, never zero
times.

**Resuming** a ``submitted`` request polls and fetches the same request id: it
is not a new submit, so it passes no gate again (DEC-152).

Stdlib only, and story-agnostic: the root and the booking are the caller's
(DEC-012). ``generation.run_generation_chain(cache=...)`` drives it; without a
cache nothing here runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone

from . import errors
from .registry import describe

SCHEMA = "gen_journal_v1"
KEY_VERSION = 1

SENDING = "sending"
SUBMITTED = "submitted"
DONE = "done"
FAILED = "failed"
LOST = "lost"
STATES = (SENDING, SUBMITTED, DONE, FAILED, LOST)

# generation.IMAGE / IMAGE_EDIT / TTS / VIDEO, spelled out: generation imports this module.
CACHED_KINDS = ("image", "image_edit", "tts", "video")
SEEDED_KINDS = ("image", "image_edit", "video")
VIDEO = "video"

# Answers that prove the provider refused the request before doing (and
# billing) any work (DEC-153). Any other failure after sending is booked.
UNBILLED_STATUSES = frozenset({400, 401, 403, 404, 409, 413, 422, 429})

NOTE_CRASH = "unknown outcome: the process stopped during the call"

MAX_EVENTS = 100
_FILE_MODE = 0o644
_KEY = re.compile(r"[0-9a-f]{64}")

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class JournalError(errors.ProviderError):
    """The journal or the booking could not be written or read. The chain stops
    here: no other link is tried while a request may be in flight unrecorded."""

    def __init__(self, message: str, *, key=None, request=None):
        super().__init__(message)
        self.key = key
        self.request = request


class RequestFailed(errors.ProviderError):
    """The provider settled a submitted request without an answer (fal
    FAILED/CANCELLED, or completed with no image). Resuming it cannot help."""


# ------------------------------------------------------------------ the key

def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value):
    """A whole number as an int (``5.0`` -> ``5``), so the same clip length
    written either way is the same request, never a second purchase."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def key_payload(kind, link, request):
    """The request as its key sees it, or ``None`` when it gets no key."""
    if kind not in CACHED_KINDS:
        return None
    if kind in SEEDED_KINDS and request.seed is None:
        return None
    if kind == VIDEO and request.duration_s is None:
        return None
    refs = []
    for path in request.references or ():
        try:
            refs.append(_sha256_file(path))
        except OSError:
            # No key: the adapter then fails on the same file before sending, as today.
            return None
    extra = request.extra or {}
    payload = {
        "v": KEY_VERSION,
        "kind": kind,
        "link": link if isinstance(link, str) else describe(link),
        "prompt": request.prompt or "",
        "negative": request.negative or "",
        "refs": refs,
        "seed": request.seed,
        "width": request.width,
        "height": request.height,
        "text": request.text or "",
        "voice": request.voice or "",
        "rate": extra.get("rate"),
        "pitch": extra.get("pitch"),
        "template": extra.get("template"),
        "take": extra.get("take"),
    }
    if kind == VIDEO:
        # Only a clip carries these, so every image and voice key is the one
        # it always was (KEY_VERSION stays).
        payload.update(
            clip_s=_number(request.duration_s),
            fps=_number(request.fps),
            native_audio=bool(request.native_audio),
        )
        # A clip's size (phase 7 stage 4, DEC-227) only when it is not the 720p
        # every clip was bought at before: those keys stay what they were.
        resolution = extra.get("resolution")
        if resolution and resolution != "720p":
            payload["resolution"] = resolution
    return payload


def request_key(kind, link, request):
    """sha256 of the canonical JSON of :func:`key_payload`, or ``None``."""
    payload = key_payload(kind, link, request)
    if payload is None:
        return None
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------- the booking rule

def billing_verdict(exc, *, sent) -> tuple:
    """``(billed, note)`` for a paid call that failed before its request was
    acknowledged (DEC-153). *sent* is ``False`` when the transport was never
    called, ``True`` when it was, ``None`` when the adapter cannot tell (an SDK
    sends on its own): only ``False`` or a refusing 4xx proves it unbilled."""
    status = errors.status_code(exc)
    if status in UNBILLED_STATUSES:
        return False, f"HTTP {status}: refused, unbilled"
    if sent is False:
        return False, f"failed before sending ({type(exc).__name__}): unbilled"
    if isinstance(status, int):
        return True, f"HTTP {status} after sending: may be billed"
    name = type(exc).__name__
    if name in ("APITimeoutError", "APIConnectionError") or isinstance(exc, (TimeoutError, ConnectionError)):
        return True, "timeout or connection reset after sending: may be billed"
    return True, f"no usable answer after sending ({name}): may be billed"


# ------------------------------------------------------------------- storage

def _atomic_write_json(path: str, data) -> None:
    """``store._atomic_write_json``'s pattern, duplicated (providers never import
    the story package): a temp file in the same directory, fsync, 0644,
    ``os.replace``; the temp file never outlives a failure."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".gen-", suffix=".tmp")
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


def _atomic_copy(src: str, dest: str) -> str:
    """Copy *src* to *dest* atomically (the same pattern, for bytes); returns the sha256 written."""
    directory = os.path.dirname(os.path.abspath(dest)) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".gen-", suffix=".tmp")
    digest = hashlib.sha256()
    try:
        with os.fdopen(handle, "wb") as out, open(src, "rb") as source:
            for block in iter(lambda: source.read(1 << 20), b""):
                digest.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return digest.hexdigest()


def _lock_for(root: str):
    real = os.path.realpath(root)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(real, threading.RLock())


def _json_safe(meta) -> dict:
    """The scalar part of an answer's ``meta``, without the runner's own flags."""
    kept = {}
    for name, value in (meta or {}).items():
        if name in ("booked", "cached", "resumed", "cache_key") or not isinstance(name, str):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            kept[name] = value
    return kept


# --------------------------------------------------------------------- cache

class GenCache:
    """The entries under *root*; *book(entry)* books one request (the caller's
    ledger row, and ``budget.record`` when ``entry["paid"]``). A cache is cheap:
    a caller whose row needs more than the entry holds (a step, an episode, a
    line's length) closes over it and makes one per call. Every cache on the
    same root shares one lock."""

    def __init__(self, root, *, book, time_fn=None):
        if not callable(book):
            raise TypeError("book must be callable: the journal books every request through it")
        self.root = str(root)
        self.book = book
        self._time = time_fn or time.time
        self.lock = _lock_for(self.root)

    def now(self) -> str:
        return datetime.fromtimestamp(self._time(), tz=timezone.utc).isoformat()

    def key(self, kind, link, request):
        return request_key(kind, link, request)

    def entry_path(self, key) -> str:
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ValueError(f"not a request key: {key!r}")
        return os.path.join(self.root, f"{key}.json")

    def lookup(self, key):
        """The entry of *key*, or ``None``. :class:`JournalError` for an entry that
        exists but cannot be used: it may hold a request in flight, so it is never
        silently started afresh."""
        path = self.entry_path(key)
        with self.lock:
            if os.path.islink(path):
                raise JournalError(f"journal entry {key} is a symlink, which is never followed; "
                                   "replace it with the file itself.", key=key)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except FileNotFoundError:
                return None
            except (OSError, ValueError) as exc:
                raise JournalError(f"journal entry {key} cannot be read ({type(exc).__name__}: {exc}); "
                                   "it may hold a paid request, so nothing is sent until it is fixed.",
                                   key=key) from None
        if (not isinstance(data, dict) or data.get("$schema") != SCHEMA or data.get("key") != key
                or data.get("state") not in STATES or not isinstance(data.get("attempts"), list)):
            raise JournalError(f"journal entry {key} is not a {SCHEMA} document; it may hold a paid "
                               "request, so nothing is sent until it is fixed.", key=key)
        return data

    def write(self, entry) -> None:
        with self.lock:
            _atomic_write_json(self.entry_path(entry["key"]), entry)

    def file_path(self, name) -> str:
        return os.path.join(self.root, os.path.basename(name))

    def journal(self, kind, link, request, *, paid, on_log=print):
        """The journal of one request on one link, or ``None`` when it has no key."""
        key = self.key(kind, link, request)
        if key is None:
            return None
        return Journal(self, key, kind=kind, link=link, paid=paid, seed=request.seed, on_log=on_log)


# ------------------------------------------------------------------- journal

class Journal:
    """One request's entry, as the runner moves it. ``state`` is the entry's
    (``None`` before one exists)."""

    def __init__(self, cache, key, *, kind, link, paid, seed, on_log):
        self.cache = cache
        self.key = key
        self.kind = kind
        self.label = link if isinstance(link, str) else describe(link)
        self.paid = bool(paid)
        self.seed = seed
        self.on_log = on_log
        self.entry = None

    # ----------------------------------------------------------- reading

    @property
    def state(self):
        return (self.entry or {}).get("state")

    @property
    def booked(self):
        return (self.entry or {}).get("booked")

    @property
    def request_id(self):
        return ((self.entry or {}).get("request") or {}).get("request_id")

    def lookup(self):
        """What an earlier run left: :data:`DONE` (serve the kept answer),
        :data:`SUBMITTED` (resume it), or ``None`` (a fresh call, through the
        gates). Books on sight what an earlier run could not: a call it died in
        (``sending``: booked, then ``lost``) or a state it wrote without its
        booking stamp."""
        self.entry = self.cache.lookup(self.key)
        state = self.state
        if state is None:
            return None
        if state == SENDING:
            if not self.booked:
                self._book(NOTE_CRASH)
                self.on_log(f"   🧾 {self.label}: an earlier run stopped during this call; booked est "
                            f"${self._est():.3f} ({NOTE_CRASH}), marked lost")
            self._move(LOST, NOTE_CRASH)
            return None
        if state in (SUBMITTED, DONE) and not self.booked:
            note = f"booked late: the request was {state}, its booking was not stamped"
            self._book(note)
            self._write()
            self.on_log(f"   🧾 {self.label}: booked est ${self._est():.3f} ({note})")
        if state in (SUBMITTED, DONE):
            return state
        return None

    def restore(self, request):
        """Copy the kept answer into ``request.out_dir``; its paths, or ``None``
        (logged) when a file is missing or no longer matches its sha256."""
        if not request.out_dir:
            raise ValueError("GenRequest.out_dir is required: where the kept answer is copied")
        files = ((self.entry or {}).get("output") or {}).get("files") or []
        for item in files:
            path = self.cache.file_path(item.get("file") or "")
            try:
                intact = not os.path.islink(path) and _sha256_file(path) == item.get("sha256")
            except OSError:
                intact = False
            if not intact:
                files = []
                break
        if not files:
            self.on_log(f"   ⚠️ {self.label}: the kept answer {self.key[:12]} is missing or changed; "
                        "generating again")
            return None
        name = (request.extra or {}).get("name")
        originals = [os.path.basename(item.get("name") or item["file"]) for item in files]
        exts = [os.path.splitext(original)[1] for original in originals]
        rename = bool(name) and len(set(exts)) == len(exts)
        paths = []
        try:
            for item, original, ext in zip(files, originals, exts):
                base = f"{name}{ext}" if rename else original
                dest = os.path.join(request.out_dir, base if base not in ("", ".", "..") else item["file"])
                _atomic_copy(self.cache.file_path(item["file"]), dest)
                paths.append(dest)
        except OSError as exc:
            raise JournalError(f"{self.label}: the kept answer {self.key[:12]} could not be copied to "
                               f"{request.out_dir} ({type(exc).__name__}: {exc})", key=self.key) from None
        return tuple(paths)

    # ----------------------------------------------------------- writing

    def begin(self, est_usd):
        """A fresh call is about to go out, its gates passed. A paid one is
        written ``sending`` first (``JournalError``: nothing was sent)."""
        self.entry = {
            "$schema": SCHEMA,
            "key": self.key,
            "kind": self.kind,
            "link": self.label,
            "paid": self.paid,
            "est_usd": float(est_usd or 0.0),
            "state": None,
            "request": None,
            "output": None,
            "seed": self.seed,
            "meta": {},
            "note": None,
            "booked": None,
            "attempts": list((self.entry or {}).get("attempts") or []),
        }
        if self.paid:
            self._move(SENDING)

    def on_submit(self, info):
        """The adapter's word that the provider holds the request (fal: between
        the queue's answer and the first poll). The first word is written
        ``submitted``, booked and stamped; if that cannot be done, the request id
        and its URLs are printed and :class:`JournalError` stops the chain. A
        later word on the same request (its output URL) is kept if it can be."""
        info = dict(info or {})
        if self.state == SUBMITTED:
            self.entry["request"] = {**(self.entry.get("request") or {}), **info}
            try:
                self._write()
            except JournalError as exc:
                self.on_log(f"   ⚠️ {self.label}: {exc}; a resume polls request {self.request_id} again")
            return
        if self.entry is None:
            self.begin(0.0)
        self.entry["request"] = info
        failures = []
        try:
            self._move(SUBMITTED)
        except JournalError as exc:
            failures.append(str(exc))
        booking = True
        if not self.booked:
            try:
                self._book(None)
            except JournalError as exc:
                failures.append(str(exc))
                booking = False
        if booking:
            try:
                self._write()
            except JournalError as exc:
                failures.append(str(exc))
            else:
                failures = []  # the entry on disk is whole: submitted and stamped
        where = (f"request {info.get('request_id')}, status {info.get('status_url')}, "
                 f"response {info.get('response_url')}")
        if failures:
            message = (f"{self.label}: the provider accepted the request, but {'; '.join(failures)}. "
                       f"Recover it by hand: {where}. The chain stops: no other link is tried.")
            self.on_log(f"   🛑 {message}")
            raise JournalError(message, key=self.key, request=info)
        self.on_log(f"   🧾 {self.label}: request {info.get('request_id')} journaled, booked est ${self._est():.3f}")

    def unanswered(self, exc, *, sent):
        """A paid call failed before any acknowledgement: book it unless it is
        proven unbilled (:func:`billing_verdict`), then write it ``failed``.
        The booking comes first: the entry on disk still says ``sending``, so a
        crash in between is booked on sight. Returns whether it was booked."""
        billed, note = billing_verdict(exc, sent=sent)
        if billed and not self.booked:
            self._book(note)
            self.on_log(f"   🧾 {self.label}: booked est ${self._est():.3f} ({note})")
        self.entry["note"] = note
        self._move(FAILED, note)
        return billed

    def settle(self, state, note):
        """A submitted, booked request ends ``failed`` or ``lost``; it stays booked."""
        self.entry["note"] = note
        self._move(state, note)

    def answered(self, paths, *, seed, meta):
        """Keep the answer's files beside the entry, write it ``done``, then book
        it if nothing has yet (a synchronous or free call). Returns the stamp.
        :class:`JournalError` when the answer cannot be kept or booked; a booking
        that can be made is made first."""
        if self.entry is None:
            self.begin(0.0)
        failure = None
        try:
            files = self._keep(paths)
        except OSError as exc:
            failure = f"the answer could not be kept ({type(exc).__name__}: {exc})"
        else:
            self.entry.update(output={"files": files}, meta=_json_safe(meta))
            if seed is not None:
                self.entry["seed"] = seed
            try:
                self._move(DONE)
            except JournalError as exc:
                failure = str(exc)
        if not self.booked:
            self._book(None)
            if self.state is not None:  # a free call's entry exists on disk only once it is done
                self._write()
        if failure is not None:
            raise JournalError(f"{self.label}: {failure}; it is booked.", key=self.key,
                               request=self.entry.get("request"))
        return self.booked

    # ---------------------------------------------------------- internals

    def _est(self) -> float:
        return float((self.entry or {}).get("est_usd") or 0.0)

    def _event(self, name, note=None):
        event = {"at": self.cache.now(), "event": name}
        if note:
            event["note"] = note
        self.entry["attempts"] = (self.entry.get("attempts") or [])[-(MAX_EVENTS - 1):] + [event]

    def _move(self, state, note=None):
        self.entry["state"] = state
        self._event(state, note)
        self._write()

    def _write(self):
        try:
            self.cache.write(self.entry)
        except (OSError, ValueError, TypeError) as exc:
            raise JournalError(f"journal entry {self.key} could not be written ({type(exc).__name__}: {exc})",
                               key=self.key, request=self.entry.get("request")) from None

    def _book(self, note):
        """``book(entry)``, then the stamp in memory (the caller writes it)."""
        self.entry["note"] = note
        try:
            self.cache.book(copy.deepcopy(self.entry))
        except Exception as exc:  # noqa: BLE001 - any failure to book stops the chain
            raise JournalError(f"its booking failed ({type(exc).__name__}: {exc})", key=self.key,
                               request=self.entry.get("request")) from None
        stamp = {"at": self.cache.now(), "est_usd": round(self._est(), 4)}
        if note:
            stamp["note"] = note
        self.entry["booked"] = stamp
        self._event("booked", note)

    def _keep(self, paths) -> list:
        files = []
        for index, path in enumerate(paths or ()):
            path = str(path)
            ext = os.path.splitext(path)[1].lstrip(".").lower() or "bin"
            stored = f"{self.key}.{index}.{ext}"
            digest = _atomic_copy(path, self.cache.file_path(stored))
            files.append({"name": os.path.basename(path), "file": stored, "sha256": digest})
        return files
