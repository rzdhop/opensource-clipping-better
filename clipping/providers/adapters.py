"""Register every generation adapter of phase 0 (image, image edit, TTS, vision, local).

Importing an adapter module registers it with ``generation``; this is the one
place that imports them all, so the settings route and the chain runner can
ask for a complete table without knowing the module list. Video adapters
arrive in phase 6 (DEC-102).
"""

from __future__ import annotations

_LOADED = False


def load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    from . import images, local_comfyui, tts, vision  # noqa: F401 - registration on import

    _LOADED = True
