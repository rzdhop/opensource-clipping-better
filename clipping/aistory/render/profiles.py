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

The audio-mix constants (DEC-157: the amix weights, the
``sidechaincompress`` ducking values, the bed fade-outs per ending, the
48 kHz stereo mix format) live at the bottom of this module, in one block,
so ``filtergraph.audio_mix_argv`` and its tests read one source of truth.
DEC-157's two-pass ``loudnorm`` target (I -14, LRA 11, its true-peak ceiling
lowered to -2.5 by the Tier-2 measurement) sits in the same block
(:data:`LOUDNORM_TARGET`), with the final AAC encode's own options
(:data:`AAC_ENCODER_ARGS`): the render plan hands the target to
``loudness.py``'s ``target=``, whose own default stays the clips' TP -1.5
(RC-A7).

Stdlib only (DEC-012).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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
    # A hardware encoder's own argv (``-c:v h264_nvenc ...``) in place of
    # libx264's codec/preset/crf: set only by the render step's opt-in
    # ``encoder="auto"`` on the final pass (:func:`with_encoder`); None
    # everywhere else, so every shipped profile's argv is unchanged.
    encoder_args: Optional[tuple] = None

    def video_encode_args(self) -> list:
        """``-c:v ...`` argv fragment, common to every builder in this
        stage (shot/tier2/end-card): codec, preset, crf, pix_fmt, this
        profile's optional ``fps_mode``/``threads``, and -- when
        :attr:`bitexact` -- the OUTPUT-side half of the parity flags
        (spec 13): ``-flags:v``/``-flags:a`` (encoder flags, per-stream
        output options) and ``-map_metadata -1``, both of which ffmpeg
        associates with the *output* file they precede, never with an
        input. :func:`global_bitexact_args` is the other, input-side half.
        With :attr:`encoder_args` set, those replace the codec, preset and
        crf; the pixel format and the rest follow as before."""
        if self.encoder_args is not None:
            args = list(self.encoder_args) + ["-pix_fmt", self.pix_fmt]
        else:
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

# The hardware encoders ``encoder="auto"`` may put on the final pass
# (``clipping.studio.ffmpeg_utils.detect_video_encoder``'s names). VAAPI is
# not one of them: its argv carries its own ``-vf format=nv12,hwupload``,
# which ffmpeg refuses beside the final pass's ``-filter_complex``.
HARDWARE_ENCODERS = ("h264_nvenc", "h264_amf")


def with_encoder(profile: RenderProfile, encoder_args) -> RenderProfile:
    """*profile* encoding with a detected hardware encoder's own argv
    (a copy; *profile* itself is frozen and unchanged)."""
    return replace(profile, encoder_args=tuple(str(token) for token in encoder_args))


# ------------------------------------------------------------ audio mix (DEC-157)
# One place for every constant of the Tier-1 audio graph
# (``filtergraph.audio_mix_argv``, spec 6.5's "Audio graph"; DEC-157, DEC-158).

AUDIO_RATE = 48000                 # every input is resampled to this, and every WAV is written at it
AUDIO_CHANNEL_LAYOUT = "stereo"    # mono lines/SFX are upmixed by aformat (equal-power, swresample's default)
AUDIO_SAMPLE_FMT = "fltp"          # the mixing format, kept to the WAVs (MIX_CODEC)
# The mix and its stems are written as 32-bit float WAV, and the final pass
# muxes the mix unchanged: ``amix normalize=0`` sums dialogue, bed and SFX, so
# the sum can exceed full scale before the loudnorm pass levels it (stage 6
# measured a -3.2 dBFS peak with real SFX). A float file keeps every sample
# above 0 dBFS for loudnorm to bring down instead of clipping it on write.
MIX_CODEC = "pcm_f32le"

# amix weights, in the amix input order the graph uses: dialogue, BGM, SFX.
MIX_WEIGHTS = (("dialogue", 1.0), ("bgm", 0.30), ("sfx", 0.8))

# The BGM bed is ducked under the dialogue (the sidechain) with exactly these.
DUCK_THRESHOLD = 0.03
DUCK_RATIO = 8
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 300

# The bed's fade-out at the very end of the episode, per cliffhanger ending.
ENDINGS = ("cut_to_black", "hard_stop")
BED_FADE_OUT_S = {"cut_to_black": 0.5, "hard_stop": 0.05}

# Two-pass loudnorm of the episode (DEC-157): passed as ``target=`` to
# ``clipping.loudness.measure_cmd``/``apply_cmd`` by ``render/plan.py``.
# ``clipping.loudness.TARGET`` (the clips', TP -1.5) is left as it is.
# TP -2.5, not the spec's -1 (Tier-2, FR ep01): the mix's peaks put loudnorm in
# its dynamic mode, whose -1 ceiling measured +0.57 dBTP after the AAC encode.
LOUDNORM_TARGET = "I=-14:TP=-2.5:LRA=11"
# The final AAC encode's options, after its bitrate (``plan.loudness_apply_argv``):
# PNS off, as its synthesized noise turned a -2.4 dBTP burst into a +4 dBTP over.
AAC_ENCODER_ARGS = ("-aac_pns", "0")
# A finished episode outside these is a warning in the manifest, never a
# failure (plan phase 4: "-14 +/- 1 LU"; the spec's -1 dBTP delivery limit,
# which the -2.5 ceiling above sits under).
LOUDNESS_TARGET_I = -14.0
LOUDNESS_TOLERANCE_LU = 1.0
TRUE_PEAK_MAX_DBTP = -1.0
