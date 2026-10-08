"""The story store (plan 36 §2.1): one folder per story under ``stories/<slug>/``, in git.

Layout::

    story.json                       slug, title, language (fr|en, D4), created_at
    00-brief.md  01-universe.md  04-season.md  memory.md
    02-cast/<char>/sheet.md          + full_body.png, turnaround.png, emotions.png, voice_ref.wav
    03-places/<place>/plate.md       + plate.png
    epNN/script.md  epNN/shots.json  epNN/takes.json  epNN/metadata.md
    epNN/keyframes/sNN.png  epNN/clips/sNN_vK.mp4  epNN/final.mp4
    locks.json                       what is locked (approved) and the unlock history
    costs.jsonl                      the story's GPU ledger (execution time billed, DEC-316)

Lock states: a file is a *draft* until it is locked; a locked file is never overwritten through the
store until it is explicitly unlocked (with a reason, kept in ``locks.json``). Approving a take locks
its clip: nothing is assembled from a clip that is not approved (DEC-317).

Text and images are tracked in git; clips, finals and voice files are not (``.gitignore``, decision of
2026-10-08) and live on the host plus a backup. Stdlib only; no ffmpeg, no network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import unicodedata

STORY_FILE = "story.json"
LOCKS_FILE = "locks.json"
COSTS_FILE = "costs.jsonl"
LANGUAGES = ("fr", "en")

SKELETON = {
    "00-brief.md": "# Brief\n\n## Pitch\n\n## Chosen concept\n\n## Language\n\n{language}\n",
    "01-universe.md": "# Universe — {universe_name}\n\n## Medium\n\n## Negative\n\n## Heads allowed\n\n"
                      "## Proportions\n\n## Palette\n\n## Lighting\n\n## Camera\n\n## Forbidden\n",
    "04-season.md": "# Season 1\n\n## Arc\n\n## Episodes\n",
    "memory.md": "# Series memory\n\n## What happened\n\n## Relationships\n\n## Open threads\n",
}


class StoreError(RuntimeError):
    pass


class Locked(StoreError):
    """A write to a locked file."""

    def __init__(self, relpath: str):
        super().__init__(f"{relpath} is locked (approved); unlock it with a reason before changing it")
        self.relpath = relpath


# ------------------------------------------------------------------ ids

def slugify(title: str) -> str:
    """``"Faille d'amour !"`` -> ``"faille-d-amour"``: ascii, lowercase, words joined by ``-``."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if not slug:
        raise StoreError(f"no usable slug in {title!r}")
    return slug


def shot_id(n: int) -> str:
    return f"s{int(n):02d}"


def take_id(k: int) -> str:
    return f"v{int(k)}"


def episode_dir(n: int) -> str:
    return f"ep{int(n):02d}"


# ------------------------------------------------------------------ markdown sections

def sections(text: str) -> dict:
    """``{heading: body}`` for every ``## heading`` of a markdown text (bodies stripped). Text before the
    first ``##`` heading is under ``""``."""
    out: dict = {}
    current = ""
    lines: list = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            out[current] = "\n".join(lines).strip()
            current, lines = m.group(1), []
        else:
            lines.append(line)
    out[current] = "\n".join(lines).strip()
    return out


def read_sections(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return sections(fh.read())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ the story

class Story:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    # -- creation
    @classmethod
    def create(cls, stories_dir: str, slug: str, *, title: str, language: str, universe_name: str = "") -> "Story":
        if language not in LANGUAGES:
            raise StoreError(f"language must be one of {LANGUAGES}, not {language!r}")
        if slug != slugify(slug):
            raise StoreError(f"{slug!r} is not a slug (try {slugify(slug)!r})")
        root = os.path.join(stories_dir, slug)
        if os.path.exists(os.path.join(root, STORY_FILE)):
            raise StoreError(f"story {slug!r} already exists at {root}")
        os.makedirs(os.path.join(root, "02-cast"), exist_ok=True)
        os.makedirs(os.path.join(root, "03-places"), exist_ok=True)
        story = cls(root)
        for name, body in SKELETON.items():
            path = os.path.join(root, name)
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(body.format(language=language, universe_name=universe_name or title))
        _write_json(os.path.join(root, STORY_FILE),
                    {"slug": slug, "title": title, "language": language, "created_at": _now()})
        return story

    @classmethod
    def open(cls, path: str) -> "Story":
        if not os.path.exists(os.path.join(path, STORY_FILE)):
            raise StoreError(f"{path} is not a story (no {STORY_FILE})")
        return cls(path)

    @property
    def meta(self) -> dict:
        return _read_json(os.path.join(self.root, STORY_FILE), {})

    @property
    def slug(self) -> str:
        return self.meta["slug"]

    @property
    def language(self) -> str:
        return self.meta["language"]

    # -- paths (relative to the story root)
    def path(self, relpath: str) -> str:
        full = os.path.normpath(os.path.join(self.root, relpath))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise StoreError(f"{relpath!r} leaves the story folder")
        return full

    def rel(self, path: str) -> str:
        return os.path.relpath(os.path.abspath(path), self.root)

    brief = staticmethod(lambda: "00-brief.md")
    universe = staticmethod(lambda: "01-universe.md")
    season = staticmethod(lambda: "04-season.md")
    memory = staticmethod(lambda: "memory.md")

    @staticmethod
    def cast_dir(char: str) -> str:
        return f"02-cast/{char}"

    @staticmethod
    def sheet(char: str) -> str:
        return f"02-cast/{char}/sheet.md"

    @staticmethod
    def plate(place: str) -> str:
        return f"03-places/{place}/plate.md"

    @staticmethod
    def episode(n: int) -> str:
        return episode_dir(n)

    @staticmethod
    def script(n: int) -> str:
        return f"{episode_dir(n)}/script.md"

    @staticmethod
    def shots(n: int) -> str:
        return f"{episode_dir(n)}/shots.json"

    @staticmethod
    def takes(n: int) -> str:
        return f"{episode_dir(n)}/takes.json"

    @staticmethod
    def keyframe(n: int, shot: str) -> str:
        return f"{episode_dir(n)}/keyframes/{shot}.png"

    @staticmethod
    def clip(n: int, shot: str, take: str) -> str:
        return f"{episode_dir(n)}/clips/{shot}_{take}.mp4"

    @staticmethod
    def final(n: int) -> str:
        return f"{episode_dir(n)}/final.mp4"

    @staticmethod
    def metadata(n: int) -> str:
        return f"{episode_dir(n)}/metadata.md"

    def cast(self) -> list:
        d = self.path("02-cast")
        return sorted(c for c in os.listdir(d) if os.path.exists(os.path.join(d, c, "sheet.md"))) if os.path.isdir(d) else []

    # -- reading
    def exists(self, relpath: str) -> bool:
        return os.path.exists(self.path(relpath))

    def read_text(self, relpath: str) -> str:
        with open(self.path(relpath), encoding="utf-8") as fh:
            return fh.read()

    def read_json(self, relpath: str, default=None):
        return _read_json(self.path(relpath), default)

    def sections(self, relpath: str) -> dict:
        return sections(self.read_text(relpath))

    # -- locks
    def _locks(self) -> dict:
        return _read_json(self.path(LOCKS_FILE), {"locked": {}, "history": []})

    def is_locked(self, relpath: str) -> bool:
        return os.path.normpath(relpath) in self._locks()["locked"]

    def locked(self) -> dict:
        return dict(self._locks()["locked"])

    def lock(self, relpath: str, note: str = "") -> None:
        relpath = os.path.normpath(relpath)
        if not self.exists(relpath):
            raise StoreError(f"cannot lock {relpath}: no such file")
        locks = self._locks()
        locks["locked"][relpath] = {"state": "locked", "at": _now(), "note": note}
        locks["history"].append({"action": "lock", "path": relpath, "at": _now(), "note": note})
        _write_json(self.path(LOCKS_FILE), locks)

    def unlock(self, relpath: str, reason: str) -> None:
        if not reason.strip():
            raise StoreError("an unlock needs a reason")
        relpath = os.path.normpath(relpath)
        locks = self._locks()
        if relpath not in locks["locked"]:
            raise StoreError(f"{relpath} is not locked")
        del locks["locked"][relpath]
        locks["history"].append({"action": "unlock", "path": relpath, "at": _now(), "note": reason})
        _write_json(self.path(LOCKS_FILE), locks)

    # -- writing (refused on a locked file)
    def _guard(self, relpath: str) -> str:
        if self.is_locked(relpath):
            raise Locked(os.path.normpath(relpath))
        full = self.path(relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        return full

    def writable(self, relpath: str) -> str:
        """The absolute path to write *relpath* to (folders made); ``Locked`` if it is locked. For files a
        tool writes itself (ffmpeg outputs)."""
        return self._guard(relpath)

    def write_text(self, relpath: str, text: str) -> str:
        full = self._guard(relpath)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
        return full

    def write_json(self, relpath: str, data) -> str:
        full = self._guard(relpath)
        _write_json(full, data)
        return full

    def write_bytes(self, relpath: str, data: bytes) -> str:
        full = self._guard(relpath)
        with open(full, "wb") as fh:
            fh.write(data)
        return full

    def copy_in(self, src: str, relpath: str) -> str:
        full = self._guard(relpath)
        shutil.copyfile(src, full)
        return full

    # -- takes
    def _takes(self, n: int) -> dict:
        return self.read_json(self.takes(n), {}) or {}

    def next_take(self, n: int, shot: str) -> str:
        return take_id(len(self._takes(n).get(shot, {}).get("takes", [])) + 1)

    def add_take(self, n: int, shot: str, relpath: str, *, seed=None, verdict: dict | None = None,
                 job: str | None = None, note: str = "") -> str:
        """Record a take of *shot* (its clip already in the store at *relpath*); returns its take id."""
        if not self.exists(relpath):
            raise StoreError(f"no clip at {relpath}")
        data = self._takes(n)
        entry = data.setdefault(shot, {"takes": []})
        take = take_id(len(entry["takes"]) + 1)
        entry["takes"].append({"take": take, "path": os.path.normpath(relpath), "seed": seed, "job": job,
                               "verdict": verdict or {}, "approved": False, "note": note, "at": _now()})
        self.write_json(self.takes(n), data)
        return take

    def set_verdict(self, n: int, shot: str, take: str, verdict: dict) -> None:
        data = self._takes(n)
        self._find_take(data, shot, take)["verdict"] = verdict
        self.write_json(self.takes(n), data)

    @staticmethod
    def _find_take(data: dict, shot: str, take: str) -> dict:
        for t in data.get(shot, {}).get("takes", []):
            if t["take"] == take:
                return t
        raise StoreError(f"no take {shot}/{take}")

    def approve_take(self, n: int, shot: str, take: str, note: str) -> str:
        """Rida approved this take: it becomes the shot's clip, the other takes are un-approved, and the
        clip file is locked. Returns the clip's path."""
        data = self._takes(n)
        chosen = self._find_take(data, shot, take)
        for t in data[shot]["takes"]:
            t["approved"] = t is chosen
        chosen["approved_note"] = note
        chosen["approved_at"] = _now()
        self.write_json(self.takes(n), data)
        if not self.is_locked(chosen["path"]):
            self.lock(chosen["path"], f"approved {shot}/{take}: {note}")
        return chosen["path"]

    def approved_take(self, n: int, shot: str) -> dict | None:
        for t in self._takes(n).get(shot, {}).get("takes", []):
            if t.get("approved"):
                return t
        return None

    def approved_clip(self, n: int, shot: str) -> str | None:
        t = self.approved_take(n, shot)
        return t["path"] if t else None

    # -- the cost ledger
    def add_cost(self, kind: str, template: str, job: str, billed_s: float, delay_s: float, usd: float, *,
                 episode: int | None = None, note: str = "") -> dict:
        row = {"at": _now(), "kind": kind, "template": template, "job": job, "billed_s": round(float(billed_s), 3),
               "delay_s": round(float(delay_s), 3), "usd": round(float(usd), 4), "episode": episode, "note": note}
        with open(self.path(COSTS_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def costs(self, episode: int | None = None) -> list:
        path = self.path(COSTS_FILE)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return [r for r in rows if episode is None or r.get("episode") == episode]

    def total_usd(self, episode: int | None = None) -> float:
        return round(sum(r["usd"] for r in self.costs(episode)), 4)
