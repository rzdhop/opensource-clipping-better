"""Transcript analysis: beats, deterministic snapping, and language detection.

The division of labour this package exists to enforce: a language model decides
*which* moments are interesting, and Python decides *where* every cut lands.
"""

from . import beats, langdetect, presets, snap

__all__ = ["beats", "langdetect", "presets", "snap"]
