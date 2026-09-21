"""Persist the Settings page's values to disk.

Until now they lived only in `worker._settings_env`, a module-level dict. Keys
typed into the dashboard survived exactly as long as the process: a
`docker compose restart` silently emptied them, and the next job failed for a
missing key the user could see listed as set.

The file is written atomically and chmod 0600, because it holds API keys. It
lives under `data/`, which is gitignored and bind-mounted so a container restart
does not lose it either.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import tempfile

DATA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data")
)
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")


def settings_path():
    """Where the settings file lives. ``WEB_SETTINGS_FILE`` overrides it."""
    return os.environ.get("WEB_SETTINGS_FILE") or SETTINGS_PATH


# Exactly what may be written to disk; anything else in the settings env is
# runtime-only. An allow-list rather than "whatever the route was handed", so a
# settings field added later cannot quietly start persisting arbitrary
# environment variables (DEC-043).
PERSISTED_KEYS = frozenset({
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "LLM_CUSTOM_API_KEY",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "OPENAI_COMPAT_MODEL",
    "PEXELS_API_KEY",
    "HF_TOKEN",
    "DEFAULT_CLIPS",
    "DEFAULT_RATIO",
    "DEFAULT_FONT_STYLE",
    "DEFAULT_WHISPER_MODEL",
    "DEFAULT_WHISPER_DEVICE",
    "DEFAULT_AI_PROVIDER",
})

# Values that are secrets. Listed explicitly rather than pattern-matched on
# "KEY", so adding a setting cannot accidentally make it world-readable.
SECRET_KEYS = frozenset({
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "LLM_CUSTOM_API_KEY",
    "OPENAI_COMPAT_API_KEY",
    "PEXELS_API_KEY",
    "HF_TOKEN",
})


def load(path=None):
    """The stored settings, or ``{}`` if there are none.

    Never raises. A corrupt or unreadable settings file must not stop the server
    from starting -- the user can always re-enter the values, but they cannot
    re-enter them into a server that will not boot.
    """
    path = path or settings_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if str(k) in PERSISTED_KEYS and v not in (None, "")
    }


def save(values, path=None):
    """Write *values* atomically with 0600 permissions. Returns True on success.

    Atomic because a half-written file is indistinguishable from a real one to
    :func:`load`, which would silently drop every key after it. 0600 because the
    file holds API keys and the containing directory is bind-mounted.
    """
    path = path or settings_path()
    directory = os.path.dirname(path) or "."
    payload = {
        str(k): str(v)
        for k, v in dict(values).items()
        if str(k) in PERSISTED_KEYS and v not in (None, "")
    }

    try:
        os.makedirs(directory, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=directory, prefix=".settings-", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
            # Set the mode on the temp file, so the real path is never briefly
            # world-readable between the rename and the chmod.
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError as exc:
        print(f"⚠️ Could not save settings to {path}: {exc}")
        return False
    return True


def redact(values):
    """A copy safe to print or log: secrets become ``"***"``."""
    return {
        key: ("***" if key in SECRET_KEYS and value else value)
        for key, value in (values or {}).items()
    }
