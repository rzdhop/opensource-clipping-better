"""Register every generation adapter (image, image edit, video, TTS, vision, local, lipsync, manual).

Importing an adapter module registers it with ``generation``; this is the one
place that imports them all, so the settings route and the chain runner can
ask for a complete table without knowing the module list. The hosted video
adapters arrived in phase 6 (DEC-102); local ComfyUI video follows.
"""

from __future__ import annotations

_LOADED = False


def load_all() -> None:
    global _LOADED
    if _LOADED:
        return
    from . import images, lipsync, local_comfyui, manual, runpod_comfyui, runpod_images, tts, video, vision  # noqa: F401 - registration on import

    _LOADED = True
