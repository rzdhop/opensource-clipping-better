"""
web.api.settings_store — Durable storage for the Settings page values.

Everything typed into the dashboard's Settings page used to live in a single
module-level dict in ``worker.py``, so every backend restart silently threw the
API keys away and fell back to whatever ``.env`` happened to hold. People then
re-entered the same key after every restart, or -- worse -- ran a job that
failed on a missing key they were certain they had set.

Design notes
------------
* **Allow-list, not a free-for-all.** Only ``PERSISTED_KEYS`` is written, so a
  future settings field cannot accidentally start persisting arbitrary
  environment variables to disk.
* **Not under ``outputs/``.** ``routes/files.py`` serves files from that
  directory with only a ``".."`` check, and a secrets file does not belong
  inside a served tree. It lives in ``.local/``, which is git-ignored.
* **Atomic writes.** A half-written file would read as corrupt on next boot and
  drop every stored key, so the write goes to a temporary file and is renamed.
* **Values are never logged.** These are API keys.
"""

from __future__ import annotations

import json
import os
import stat

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_PATH = os.path.join(PROJECT_ROOT, ".local", "settings.json")

# Exactly what may be written to disk. Anything else in the settings env is
# runtime-only.
PERSISTED_KEYS = (
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "PEXELS_API_KEY",
    "HF_TOKEN",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_MODEL",
    "DEFAULT_CLIPS",
    "DEFAULT_RATIO",
    "DEFAULT_FONT_STYLE",
    "DEFAULT_WHISPER_MODEL",
    "DEFAULT_WHISPER_DEVICE",
    "DEFAULT_AI_PROVIDER",
)


def settings_path() -> str:
    """Where the settings file lives. ``WEB_SETTINGS_FILE`` overrides it."""
    return os.environ.get("WEB_SETTINGS_FILE") or DEFAULT_PATH


def load() -> dict[str, str]:
    """Read stored settings. A missing or unreadable file is simply empty.

    Never raises: a corrupt file must not stop the backend from booting.
    """
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        # Name the file and the failure, never its contents.
        print(f"⚠️ Ignoring unreadable settings file {path}: {type(exc).__name__}")
        return {}

    if not isinstance(data, dict):
        print(f"⚠️ Ignoring settings file {path}: expected a JSON object")
        return {}

    return {
        name: str(value)
        for name, value in data.items()
        if name in PERSISTED_KEYS and value not in (None, "")
    }


def save(env: dict[str, str]) -> bool:
    """Write the persistable subset of *env*. Returns whether it landed.

    Best-effort by design, mirroring ``store._persist``: a read-only or full
    disk degrades the Settings page to session-only, it does not take the API
    down.
    """
    path = settings_path()
    payload = {
        name: str(value)
        for name, value in env.items()
        if name in PERSISTED_KEYS and value not in (None, "")
    }

    tmp = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
        try:
            # Owner-only. A no-op on Windows, which has no POSIX mode bits.
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return True
    except Exception as exc:
        print(f"⚠️ Could not save settings to {path}: {type(exc).__name__}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
