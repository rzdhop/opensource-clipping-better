"""Render profiles for the AI-Story renderer (spec 6.5; plan phase 4 stage 4,
"Renderer" -> profiles.py; DEC-156, DEC-157).

Three encode profiles, each an immutable, frozen dataclass. ``schemas.py``'s
``RENDER_PROFILES == ("final", "golden")`` is the *overall* render's own
choice of profile (spec 2.9): a normal episode render runs in "final" mode,
the parity fixture (spec 13) runs in "golden" mode. Inside either mode, the
per-shot/end-card intermediates still need their *own* encode settings --
that per-shot intermediate is :data:`SHOT` in "final" mode, but is
:data:`GOLDEN` itself in "golden" mode (so the whole golden render stays
fast, tiny and byte-reproducible, spec 13's framemd5 parity). Concretely:

- :data:`SHOT` -- the per-shot intermediate encode (also what
  ``RENDER_CACHED_KINDS`` caches, spec 2.9): fast (``veryfast``), a low crf
  (visually close to lossless, this file is re-encoded again by the final
  pass) and a 4x pre-scale (spec 6.5: "scale=4320:-2 (4x upscale kills
  zoompan sub-pixel jitter)").
- :data:`FINAL` -- the final pass's own mux/encode (the episode's actual
  delivered quality): ``medium`` preset, crf 20, CFR at 30 fps, AAC
  192k/48kHz, ``+faststart``. It reads already-1080x1920 shot/tier2 clips,
  so it carries no upscale of its own.
- :data:`GOLDEN` -- the deterministic test profile (spec 13): ``ultrafast``,
  a high (low-quality, fast) crf, single-threaded (A-073: makes x264
  thread-independent, a precondition for framemd5 parity across machines
  with a different core count) and the ``bitexact`` flags that strip
  encoder/container nondeterminism (timestamps aside, spec 13's own
  concern). Used for every stage of a "golden" render, not only shots.

``WIDTH``/``HEIGHT``/``FPS`` are the one frame geometry every profile
renders to (spec 6.2: 1080x1920, 30 fps, 9:16 only -- v1 non-goal 1.2).

Audio-mix constants (DEC-157's weights, sidechain values, bed fades) belong
to stage 6's ``filtergraph.py`` additions, not here -- this module only
carries the *encode* settings the plan's ``profiles.py`` bullet names
(FINAL's own AAC/48kHz/faststart fields, since those are literally part of
the FINAL profile itself, not the audio *mix*).

Stdlib only (DEC-012).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

WIDTH = 1080
HEIGHT = 1920
FPS = 30


@dataclass(frozen=True)
class RenderProfile:
    """One ffmpeg encode profile. Every field is an argv fragment's worth
    of settings, consumed by ``render/filtergraph.py``'s builders -- never
    a filtergraph fragment itself (that is ``motion.py``'s and
    ``filtergraph.py``'s own job)."""

    name: str
    # video
    preset: str
    crf: int
    pix_fmt: str = "yuv420p"
    fps: int = FPS
    fps_mode: Optional[str] = None      # "cfr", FINAL only (spec 6.5's mux pass)
    threads: Optional[int] = None       # 1, GOLDEN only (A-073)
    bitexact: bool = False              # GOLDEN only (spec 13's parity flags)
    upscale: int = 1                    # 4, SHOT only; every other profile is 1 (spec 6.5)
    # audio -- the final-pass mux only; per-shot/end-card/tier2 clips carry
    # no audio track at this stage (DEC-158: the audio mix is stage 6/9).
    audio_codec: Optional[str] = None   # "aac", FINAL only
    audio_bitrate: Optional[str] = None  # "192k", FINAL only
    audio_rate: Optional[int] = None    # 48000, FINAL only
    movflags: Optional[str] = None      # "+faststart", FINAL only

    def video_encode_args(self) -> list:
        """``-c:v ...`` argv fragment, common to every builder in this
        stage (shot/tier2/end-card): codec, preset, crf, pix_fmt, this
        profile's optional ``fps_mode``/``threads``, and -- when
        :attr:`bitexact` -- the OUTPUT-side half of the parity flags
        (spec 13): ``-flags:v``/``-flags:a`` (encoder flags, per-stream
        output options) and ``-map_metadata -1``, both of which ffmpeg
        associates with the *output* file they precede, never with an
        input. :func:`global_bitexact_args` is the other, input-side half."""
        args = ["-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf), "-pix_fmt", self.pix_fmt]
        if self.fps_mode is not None:
            args += ["-fps_mode", self.fps_mode]
        if self.threads is not None:
            args += ["-threads", str(self.threads)]
        if self.bitexact:
            args += ["-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1"]
        return args

    def global_bitexact_args(self) -> list:
        """The parity flags' input-side half (spec 13): ``-fflags
        +bitexact`` only, present when :attr:`bitexact` is set (GOLDEN).
        ``-fflags`` is a demuxer/decoder flag -- a *generic* ffmpeg option
        -- so every builder in this stage places it before any ``-i``,
        unlike :func:`video_encode_args`'s own bitexact flags, which are
        output options and belong near the encoder settings instead."""
        if not self.bitexact:
            return []
        return ["-fflags", "+bitexact"]


SHOT = RenderProfile(name="shot", preset="veryfast", crf=12, upscale=4)

FINAL = RenderProfile(
    name="final", preset="medium", crf=20, fps_mode="cfr",
    audio_codec="aac", audio_bitrate="192k", audio_rate=48000, movflags="+faststart",
)

GOLDEN = RenderProfile(name="golden", preset="ultrafast", crf=30, threads=1, bitexact=True, upscale=1)
