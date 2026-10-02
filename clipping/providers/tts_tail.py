"""The TTS tail guard: the burst of static Gemini TTS appends to the very end
of a clip is cut, never a word (the "crshhh" at the end of every line).

Gemini's speech generation has a known fault (Google AI forum, "Static
noise/artifact at the end of TTS generation", reported on 2.5 Flash/Pro and
3.1 Flash): a burst of broadband noise right after the last word. No real
sample was available when this was written, so the rules below are built on
what that noise is -- louder than the floor, noise-like, at the end -- and are
conservative: a line keeps anything that may be speech, and every cut is
reported (:func:`clean`'s report, kept with the line).

**The analysis** (:func:`analyse`), of 16-bit little-endian mono PCM: 10 ms
frames, each with its RMS level (dBFS) and zero-crossing rate (ZCR). The
line's noise floor is the 10th percentile of the levels, its speech level
the 95th percentile of its low-ZCR frames'; a frame is *active* above
``floor + 10 dB`` (kept between 40 and 30 dB under the speech level), and an
active frame is *noise-like* at a ZCR of :data:`NOISE_ZCR` or more (white
noise sits near 0.5, a voiced vowel well under 0.1). *Voiced* frames are
active low-ZCR frames in a run of :data:`VOICED_MIN_FRAMES` or more: a vowel
lasts longer than that, and one quiet-ZCR frame inside a burst of noise is
not speech. The end of speech is the last voiced frame; what follows it, to
the end of the file,
is then read as active stretches split by near-silent gaps of at least
:data:`GAP_MIN_S`:

- ``noise_run`` -- the stretch glued to the speech (no such gap before it)
  is noise-like for :data:`NOISE_RUN_MIN_S` or more: longer than a
  word-final fricative can be. Cut :data:`FRICATIVE_KEEP_S` after it starts,
  so a final "s"/"ch" survives.
- ``noise_after_gap`` -- after the speech (and a fricative glued to it, kept
  whole) comes a near-silent gap, then noise to the end: cut in the gap, at
  most :data:`GAP_KEEP_S` after it starts. Only when the noise after it runs
  :data:`NOISE_RUN_MIN_S` or more, or the gap is :data:`LONG_GAP_S` or
  more: a stop's closure before a final /s/ ("fax", "texts") is a short
  silence then a short noise, and stays.
- ``aligned_end`` -- with ``speech_end_s`` (the last word's end, from a
  word alignment) and nothing found above: cut :data:`ALIGNED_PAD_S` after
  it (or where noise starts, if sooner), when what follows holds no voiced
  frame and something audible. An alignment never moves a cut the frames
  made: an STT's last word often stretches into the noise after it.
- ``suspect`` -- a cut that would remove more than :data:`MAX_CUT_S`, or
  leave less than :data:`MIN_KEEP_RATIO` of the line (an all-noise file):
  nothing is cut, and the report says so.
- ``none`` -- nothing to cut.

**The fades**: whatever the verdict, :data:`FADE_IN_S` in at the start and
:data:`FADE_OUT_S` out at the (new) end, so a line never starts or ends on a
click.

Stdlib only (DEC-012), pure but for the file helpers at the end.
"""

from __future__ import annotations

import array
import math
import os
import sys
import tempfile
import wave

TAIL_GUARD_VERSION = 1

FRAME_S = 0.010
# A frame is noise-like at this zero-crossing rate or more (crossings per
# sample pair). White noise ~0.5 and a fricative 0.3-0.7 at 16-48 kHz; a
# voiced vowel (f0 120-250 Hz plus harmonics) stays under ~0.1.
NOISE_ZCR = 0.25
# Levels: the floor's percentile, the speech's, and the active threshold.
FLOOR_PERCENTILE = 10
SPEECH_PERCENTILE = 95
ACTIVE_OVER_FLOOR_DB = 10.0
ACTIVE_MAX_UNDER_SPEECH_DB = 40.0
# 30, not less: in a line with no real silence (the floor's percentile then
# lands in speech dips or in the noise itself), a burst 25 dB under the
# speech still counts.
ACTIVE_MIN_UNDER_SPEECH_DB = 30.0
SILENT_DB = -100.0
# Voiced speech is a run of at least this many low-ZCR active frames (30 ms).
VOICED_MIN_FRAMES = 3
# A near-silent stretch this long splits the tail into a gap; shorter ones
# (inside a fricative, a stop's closure) do not.
GAP_MIN_S = 0.060
# A gap this long cannot be a stop's closure (typically 50-120 ms).
LONG_GAP_S = 0.150
# Noise this long is longer than a word-final fricative can be (~80-200 ms).
NOISE_RUN_MIN_S = 0.250
# How much of a glued noise run is kept: room for a final "s"/"ch".
FRICATIVE_KEEP_S = 0.120
# How far into a gap after the speech the cut is made (its natural decay).
GAP_KEEP_S = 0.050
# The least noise after a long gap that counts as a burst.
MIN_BURST_S = 0.030
# After an aligned last word's end, the cut.
ALIGNED_PAD_S = 0.150
# The limits: never more than this cut, never less than this share kept.
MAX_CUT_S = 1.5
MIN_KEEP_RATIO = 0.5
FADE_IN_S = 0.005
FADE_OUT_S = 0.025

REASONS = ("none", "noise_after_gap", "noise_run", "aligned_end", "suspect")

_FULL_SCALE = 32768.0


# ---------------------------------------------------------------- the frames

def _samples(pcm: bytes) -> array.array:
    """16-bit little-endian samples of *pcm*; a trailing odd byte (half a
    sample) is dropped."""
    samples = array.array("h")
    samples.frombytes(bytes(pcm[: len(pcm) - len(pcm) % 2]))
    if samples.itemsize != 2:  # pragma: no cover - every platform CPython runs on
        raise ValueError("array('h') is not 16-bit here")
    if sys.byteorder != "little":  # pragma: no cover - the PCM is little-endian
        samples.byteswap()
    return samples


def _frame_size(rate) -> int:
    return max(1, int(round(rate * FRAME_S)))


def _frames(samples, size) -> list:
    """``[(db, zcr)]`` per whole frame of *size* samples (a last partial frame,
    under 10 ms, is not read: it goes with whatever is cut after it)."""
    out = []
    for start in range(0, len(samples) - size + 1, size):
        frame = samples[start:start + size]
        energy = sum(x * x for x in frame)
        rms = math.sqrt(energy / size)
        db = 20.0 * math.log10(rms / _FULL_SCALE) if rms > 0 else SILENT_DB
        crossings = sum(1 for a, b in zip(frame, frame[1:]) if (a < 0) != (b < 0))
        out.append((max(db, SILENT_DB), crossings / max(1, size - 1)))
    return out


def _percentile(values, pct) -> float:
    ordered = sorted(values)
    return ordered[int(round(pct / 100.0 * (len(ordered) - 1)))]


def _stretches(flags, start, stop):
    """``[(first, end)]`` of the runs of True in ``flags[start:stop]``."""
    runs, first = [], None
    for i in range(start, stop):
        if flags[i] and first is None:
            first = i
        elif not flags[i] and first is not None:
            runs.append((first, i))
            first = None
    if first is not None:
        runs.append((first, stop))
    return runs


def _segments(active, start, stop, gap_frames):
    """The active stretches of ``active[start:stop]``, merged across silences
    shorter than *gap_frames*: ``[(first, end, gap_before)]``, where
    *gap_before* is the near-silent frames between the previous segment (or
    *start*) and this one."""
    merged = []
    for first, end in _stretches(active, start, stop):
        if merged and first - merged[-1][1] < gap_frames:
            merged[-1] = (merged[-1][0], end, merged[-1][2])
        else:
            merged.append((first, end, first - (merged[-1][1] if merged else start)))
    return merged


# -------------------------------------------------------------- the analysis

def _report(reason, n, kept, rate) -> dict:
    return {"version": TAIL_GUARD_VERSION, "trimmed_s": round((n - kept) / rate, 3), "reason": reason,
            "original_s": round(n / rate, 3), "kept_s": round(kept / rate, 3)}


def analyse(pcm: bytes, rate: int, *, speech_end_s=None) -> dict:
    """What :func:`clean` would do to *pcm* (16-bit mono at *rate*), changing
    nothing::

        {"report": {version, trimmed_s, reason, original_s, kept_s},
         "cut_sample": n, "floor_db", "speech_db", "speech_end_s",
         "would_cut_s"}

    ``cut_sample`` is where the kept audio ends (every sample when nothing is
    cut); ``speech_end_s`` the end of the last voiced frame (None: none
    found); ``would_cut_s`` what the rules asked for before the limits (a
    ``suspect`` line keeps everything). The module docstring has the rules.
    """
    rate = int(rate)
    if rate <= 0:
        raise ValueError(f"a sample rate must be positive, not {rate}")
    samples = _samples(pcm)
    n = len(samples)
    size = _frame_size(rate)
    frames = _frames(samples, size)
    result = {"report": _report("none", n, n, rate), "cut_sample": n, "floor_db": None, "speech_db": None,
              "speech_end_s": None, "would_cut_s": 0.0}
    if not frames:
        return result

    dbs = [db for db, _zcr in frames]
    floor_db = _percentile(dbs, FLOOR_PERCENTILE)
    voiced_dbs = [db for db, zcr in frames if zcr < NOISE_ZCR]
    speech_db = _percentile(voiced_dbs or dbs, SPEECH_PERCENTILE)
    threshold = min(max(floor_db + ACTIVE_OVER_FLOOR_DB, speech_db - ACTIVE_MAX_UNDER_SPEECH_DB),
                    speech_db - ACTIVE_MIN_UNDER_SPEECH_DB)
    active = [db >= threshold and db > SILENT_DB for db in dbs]
    low = [act and zcr < NOISE_ZCR for act, (_db, zcr) in zip(active, frames)]
    count = len(frames)
    voiced = [False] * count
    for first, end in _stretches(low, 0, count):
        if end - first >= VOICED_MIN_FRAMES:
            voiced[first:end] = [True] * (end - first)
    result.update(floor_db=round(floor_db, 1), speech_db=round(speech_db, 1))

    gap_frames = max(1, int(round(GAP_MIN_S / FRAME_S)))
    run_frames = int(round(NOISE_RUN_MIN_S / FRAME_S))
    last_voiced = max((i for i in range(count) if voiced[i]), default=None)
    cut, reason = None, "none"
    if last_voiced is None:
        # No voiced frame: nothing here can be told from speech. Noise from
        # the first frame on would be "cut" to its first 120 ms -- under the
        # share a line keeps, so it ends suspect, whole.
        if any(active):
            cut, reason = int(round(FRICATIVE_KEEP_S / FRAME_S)), "noise_run"
        protected = 0
    else:
        speech_end = last_voiced + 1
        result["speech_end_s"] = round(speech_end * size / rate, 3)
        segments = _segments(active, speech_end, count, gap_frames)
        glued = segments[0] if segments and segments[0][2] < gap_frames else None
        later = segments[1:] if glued else segments
        protected = speech_end
        if glued and glued[1] - glued[0] >= run_frames:
            cut, reason = glued[0] + int(round(FRICATIVE_KEEP_S / FRAME_S)), "noise_run"
        else:
            if glued:
                protected = glued[1]  # a fricative glued to the speech: kept whole
            if later:
                gap = later[0][2]
                burst = later[-1][1] - later[0][0]
                if burst >= run_frames or (gap * FRAME_S >= LONG_GAP_S - 1e-9
                                          and burst * FRAME_S >= MIN_BURST_S - 1e-9):
                    keep = min(int(round(GAP_KEEP_S / FRAME_S)), gap // 2)
                    cut, reason = protected + keep, "noise_after_gap"
        if cut is None and speech_end_s is not None:
            cut = _aligned_cut(speech_end_s, protected, active, voiced, count, size, rate)
            if cut is not None:
                reason = "aligned_end"

    if cut is None or cut >= count:
        return result
    cut_sample = cut * size
    result["would_cut_s"] = round((n - cut_sample) / rate, 3)
    if (n - cut_sample) / rate > MAX_CUT_S + 1e-9 or cut_sample < MIN_KEEP_RATIO * n:
        result["report"] = _report("suspect", n, n, rate)
        return result
    result.update(report=_report(reason, n, cut_sample, rate), cut_sample=cut_sample)
    return result


def _aligned_cut(speech_end_s, protected, active, voiced, count, size, rate):
    """The ``aligned_end`` cut (a frame index), or None: :data:`ALIGNED_PAD_S`
    after the aligned last word's end -- never before the frames' own end of
    speech (*protected*), sooner where noise starts -- when what follows holds
    something audible and no voiced frame."""
    try:
        end_s = float(speech_end_s)
    except (TypeError, ValueError):
        return None
    if not end_s > 0 or math.isnan(end_s):
        return None
    start = max(protected, int(math.ceil(end_s * rate / size - 1e-9)))
    if start >= count:
        return None
    first_active = next((i for i in range(start, count) if active[i]), None)
    if first_active is None:
        return None  # trailing silence: nothing to cut
    cut = min(start + int(round(ALIGNED_PAD_S / FRAME_S)), first_active)
    if any(voiced[cut:]) or not any(active[cut:]):
        return None
    return cut


# ---------------------------------------------------------------- the cut

def _faded(samples, rate) -> array.array:
    """*samples* with a linear fade-in over :data:`FADE_IN_S` and a fade-out
    over :data:`FADE_OUT_S` reaching 0 on the last sample."""
    out = array.array("h", samples)
    n = len(out)
    fade_in = min(int(round(FADE_IN_S * rate)), n // 2)
    fade_out = min(int(round(FADE_OUT_S * rate)), n - fade_in)
    for i in range(fade_in):
        out[i] = int(out[i] * i / fade_in)
    for k in range(fade_out):
        i = n - 1 - k
        out[i] = int(out[i] * k / fade_out)
    return out


def clean(pcm: bytes, rate: int, *, speech_end_s=None) -> tuple:
    """``(pcm, report)``: *pcm* (16-bit little-endian mono at *rate*) cut where
    :func:`analyse` says, then faded in and out; *report* is ``{"version",
    "trimmed_s", "reason", "original_s", "kept_s"}`` (reason one of
    :data:`REASONS`). A trailing odd byte is dropped; nothing else changes
    the length but the cut."""
    plan = analyse(pcm, rate, speech_end_s=speech_end_s)
    samples = _samples(pcm)[: plan["cut_sample"]]
    out = _faded(samples, int(rate))
    if sys.byteorder != "little":  # pragma: no cover
        out.byteswap()
    return out.tobytes(), plan["report"]


# ---------------------------------------------------------- the file helpers

def read_wav(path) -> tuple:
    """``(pcm, rate)`` of a 16-bit mono PCM WAV, or None when *path* is any
    other format (left to the caller to say)."""
    try:
        with wave.open(path, "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                return None
            return wav.readframes(wav.getnframes()), wav.getframerate()
    except (OSError, EOFError, wave.Error):
        return None


def clean_wav(path, *, speech_end_s=None):
    """Clean the 16-bit mono WAV at *path* in place -- written whole to a
    temp file beside it, then ``os.replace``d, so a reader sees the old file
    or the new one -- and return the report; None (the file untouched) when
    it is not such a WAV."""
    read = read_wav(path)
    if read is None:
        return None
    pcm, rate = read
    cleaned, report = clean(pcm, rate, speech_end_s=speech_end_s)
    folder = os.path.dirname(os.path.abspath(path))
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".tail-", suffix=".wav")
    try:
        with os.fdopen(handle, "wb") as fh:
            with wave.open(fh, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(cleaned)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return report
