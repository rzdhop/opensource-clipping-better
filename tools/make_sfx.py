#!/usr/bin/env python3
"""tools/make_sfx.py -- deterministic stdlib synthesiser for AI-Story SFX.

Reads ``clipping/aistory/templates/styles/*.json`` to discover every SFX
pack and cue name the seven shipped style templates reference (the
templates are the source of truth, not this file: a new template
automatically gets its cues synthesised the next time this runs) and
synthesises one short WAV per distinct cue name with a crude but
name-driven, hash-seeded recipe -- a door slam reads as a low thump plus a
short noise burst, a phone ring as two alternating tone bursts, a heartbeat
as two low thumps, waves as a filtered-noise swell, and so on.

Layout for a cue name shared by more than one pack (e.g. ``boing`` is used
by both the ``cartoon`` and ``cartoon_soft`` packs): the alphabetically
first pack that names the cue *owns* it and gets the actual
``assets/sfx/<owner>/<cue>.wav`` file; every other pack's ``sfx_index.json``
points its entry's ``file`` at that same owner path (``<owner>/<cue>.wav``,
relative to ``assets/sfx/``). No file is ever duplicated and no separate
``_shared/`` folder is used -- the existing per-pack folders are the whole
layout.

Also writes ``assets/overlays/paper_texture.png``, a small tileable
grayscale paper-grain PNG encoded by hand with ``zlib`` + ``struct``.

Format: 22.05 kHz mono 16-bit WAV, each clip <= 3.0 s and <= 150 KB, peak
level at or below -1 dBFS (never clipping).

Stdlib only (DEC-012): ``wave``, ``math``, ``struct``, ``zlib``, seeded
``random``. No third-party audio or image library is imported.

Running this script twice must produce byte-identical output -- every
random draw is seeded from a sha256 of the cue name (or, for the overlay,
a fixed string), never from the wall clock or OS entropy.

Usage:
    python3 tools/make_sfx.py
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import random
import struct
import sys
import wave
import zlib
from pathlib import Path

SAMPLE_RATE = 22050
MAX_DURATION_S = 3.0
MAX_BYTES = 150 * 1024
GENERATOR_SOURCE = "tools/make_sfx.py v1"

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLES_DIR = REPO_ROOT / "clipping" / "aistory" / "templates" / "styles"
SFX_DIR = REPO_ROOT / "assets" / "sfx"
OVERLAYS_DIR = REPO_ROOT / "assets" / "overlays"


# ======================================================================
# Small deterministic DSP toolkit -- plain Python lists of floats in
# [-1, 1], stdlib only. Nothing here reads the clock or OS entropy; the
# only randomness is an explicitly seeded ``random.Random`` instance
# threaded through by the caller.
# ======================================================================

def _rng(seed_text: str) -> random.Random:
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest(), 16) % (2 ** 32)
    return random.Random(seed)


def _silence(n: int) -> list:
    return [0.0] * n


def _mix(*tracks) -> list:
    n = max((len(t) for t in tracks), default=0)
    out = [0.0] * n
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    return out


def _concat(*tracks) -> list:
    out: list = []
    for t in tracks:
        out.extend(t)
    return out


def _gain(track, factor: float) -> list:
    return [v * factor for v in track]


def _fade(track, fade_in_s: float = 0.0, fade_out_s: float = 0.0, sr: int = SAMPLE_RATE) -> list:
    n = len(track)
    fi = int(fade_in_s * sr)
    fo = int(fade_out_s * sr)
    out = list(track)
    for i in range(min(fi, n)):
        out[i] *= i / max(fi, 1)
    for i in range(min(fo, n)):
        out[n - 1 - i] *= i / max(fo, 1)
    return out


def _sine(freq: float, dur_s: float, sr: int = SAMPLE_RATE, phase: float = 0.0, amp: float = 1.0) -> list:
    n = int(dur_s * sr)
    return [amp * math.sin(2 * math.pi * freq * i / sr + phase) for i in range(n)]


def _sine_sweep(f0: float, f1: float, dur_s: float, sr: int = SAMPLE_RATE, amp: float = 1.0) -> list:
    n = int(dur_s * sr)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / sr
        freq = f0 + (f1 - f0) * (t / dur_s if dur_s > 0 else 0.0)
        phase += 2 * math.pi * freq / sr
        out.append(amp * math.sin(phase))
    return out


def _noise(dur_s: float, rng: random.Random, sr: int = SAMPLE_RATE, amp: float = 1.0) -> list:
    n = int(dur_s * sr)
    return [amp * (rng.random() * 2 - 1) for _ in range(n)]


def _lowpass(track, cutoff_hz: float, sr: int = SAMPLE_RATE) -> list:
    if not track:
        return list(track)
    rc = 1.0 / (2 * math.pi * cutoff_hz)
    dt = 1.0 / sr
    alpha = dt / (rc + dt)
    out = [0.0] * len(track)
    out[0] = track[0] * alpha
    for i in range(1, len(track)):
        out[i] = out[i - 1] + alpha * (track[i] - out[i - 1])
    return out


def _highpass(track, cutoff_hz: float, sr: int = SAMPLE_RATE) -> list:
    if not track:
        return list(track)
    rc = 1.0 / (2 * math.pi * cutoff_hz)
    dt = 1.0 / sr
    alpha = rc / (rc + dt)
    out = [0.0] * len(track)
    out[0] = track[0]
    for i in range(1, len(track)):
        out[i] = alpha * (out[i - 1] + track[i] - track[i - 1])
    return out


def _envelope_exp_decay(n: int, tau_s: float, sr: int = SAMPLE_RATE) -> list:
    return [math.exp(-i / (tau_s * sr)) for i in range(n)]


def _apply_envelope(track, env) -> list:
    n = min(len(track), len(env))
    return [track[i] * env[i] for i in range(n)]


def _amp_modulate(track, rate_hz: float, depth: float, sr: int = SAMPLE_RATE) -> list:
    out = []
    for i, v in enumerate(track):
        m = 1 - depth + depth * (0.5 + 0.5 * math.sin(2 * math.pi * rate_hz * i / sr))
        out.append(v * m)
    return out


def _env_ar(n: int, attack_s: float, sr: int = SAMPLE_RATE) -> list:
    """A generic percussive envelope: a quick cosine attack, then an
    exponential-ish decay sized to whatever length remains."""
    a = max(1, int(attack_s * sr))
    env = []
    tail = max(1, (n - a) / 3.0)
    for i in range(n):
        if i < a:
            env.append(0.5 - 0.5 * math.cos(math.pi * i / a))
        else:
            env.append(math.exp(-(i - a) / tail))
    return env


def _normalize(samples, peak_dbfs: float) -> list:
    peak = max((abs(v) for v in samples), default=0.0)
    if peak <= 1e-9:
        return list(samples)
    target = 10 ** (peak_dbfs / 20.0)
    factor = target / peak
    return [v * factor for v in samples]


def _to_pcm16(samples) -> bytes:
    out = bytearray()
    for v in samples:
        v = max(-1.0, min(1.0, v))
        out += struct.pack("<h", int(round(v * 32767)))
    return bytes(out)


def _wav_bytes(pcm: bytes, sr: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


# ======================================================================
# Per-cue recipes. Each takes a seeded ``random.Random`` and returns
# ``(samples, peak_dbfs)``: the raw (unnormalized) float samples and the
# peak level (in dBFS, always <= -1.0) the cue should be normalized to.
# Percussive/loud cues sit close to the -1 dBFS ceiling; ambient/soft cues
# are left with more headroom so the *kind* of sound is also distinct in
# level, not just in timbre.
# ======================================================================

def rec_whoosh_sharp(rng):
    dur = 0.45
    n = int(dur * SAMPLE_RATE)
    noise = _noise(dur, rng)
    band = _highpass(_lowpass(noise, 4000), 600)
    env = _env_ar(n, 0.05)
    return _apply_envelope(band, env), -2.0


def rec_impact_hit(rng):
    dur = 0.4
    n = int(dur * SAMPLE_RATE)
    thump = _apply_envelope(_sine(90, dur), _envelope_exp_decay(n, 0.09))
    burst = _noise(0.06, rng)
    burst = _highpass(_lowpass(burst, 6000), 1500)
    burst = _apply_envelope(burst, _envelope_exp_decay(len(burst), 0.015))
    return _mix(thump, _gain(burst, 0.6)), -1.2


def rec_heartbeat(rng):
    sr = SAMPLE_RATE

    def thump(freq, tau, dur=0.15):
        n = int(dur * sr)
        return _apply_envelope(_sine(freq, dur), _envelope_exp_decay(n, tau))

    lub = thump(55, 0.05)
    gap1 = _silence(int(0.18 * sr))
    dub = _gain(thump(48, 0.045), 0.75)
    tail = _silence(int(0.45 * sr))
    return _concat(lub, gap1, dub, tail), -3.0


def rec_rain_loop(rng):
    dur = 2.5
    noise = _noise(dur, rng)
    band = _highpass(_lowpass(noise, 3200), 250)
    band = _amp_modulate(band, 0.25, 0.2)
    band = _fade(band, 0.08, 0.12)
    return band, -8.0


def rec_chime(rng):
    dur = 1.2
    n = int(dur * SAMPLE_RATE)
    f0 = 1046.5
    mix = _mix(_sine(f0, dur, amp=1.0), _sine(f0 * 2, dur, amp=0.4), _sine(f0 * 3, dur, amp=0.2))
    return _apply_envelope(mix, _envelope_exp_decay(n, 0.35)), -2.0


def rec_boing(rng):
    dur = 0.5
    n = int(dur * SAMPLE_RATE)
    s = _sine_sweep(260, 80, dur)
    return _apply_envelope(s, _envelope_exp_decay(n, 0.18)), -2.0


def rec_honk(rng):
    dur = 0.3
    n = int(dur * SAMPLE_RATE)
    raw = _sine(230, dur)
    sq = [1.0 if v >= 0 else -1.0 for v in raw]
    blend = [0.7 * sq[i] + 0.3 * raw[i] for i in range(n)]
    return _apply_envelope(blend, _env_ar(n, 0.02)), -2.5


def rec_slide_whistle(rng):
    dur = 0.8
    n = int(dur * SAMPLE_RATE)
    half = n // 2
    up = _sine_sweep(400, 1400, half / SAMPLE_RATE)
    down = _sine_sweep(1400, 500, (n - half) / SAMPLE_RATE)
    s = _concat(up, down)
    return _fade(s, 0.03, 0.08), -2.0


def rec_pop(rng):
    dur = 0.12
    n = int(dur * SAMPLE_RATE)
    noise = _noise(dur, rng)
    band = _lowpass(noise, 5000)
    return _apply_envelope(band, _envelope_exp_decay(n, 0.012)), -1.2


def rec_record_scratch(rng):
    dur = 0.5
    noise = _noise(dur, rng)
    band = _highpass(_lowpass(noise, 5000), 500)
    mod = _amp_modulate(band, 9.0, 0.85)
    return _fade(mod, 0.01, 0.05), -3.0


def rec_room_tone(rng):
    dur = 2.5
    noise = _noise(dur, rng)
    band = _highpass(_lowpass(noise, 4000), 80)
    return _fade(band, 0.1, 0.1), -14.0


def rec_footsteps_concrete(rng):
    sr = SAMPLE_RATE

    def step():
        d = 0.08
        n = int(d * sr)
        band = _highpass(_lowpass(_noise(d, rng), 3000), 400)
        return _apply_envelope(band, _envelope_exp_decay(n, 0.02))

    s1, gap, s2, tail = step(), _silence(int(0.28 * sr)), step(), _silence(int(0.15 * sr))
    return _concat(s1, gap, s2, tail), -2.0


def rec_car_pass(rng):
    dur = 2.0
    n = int(dur * SAMPLE_RATE)
    band = _lowpass(_noise(dur, rng), 900)
    env = [math.sin(math.pi * i / n) ** 1.5 for i in range(n)]
    return _apply_envelope(band, env), -4.0


def rec_phone_buzz(rng):
    dur = 1.0
    n = int(dur * SAMPLE_RATE)
    raw = _sine(180, dur)
    sq = [1.0 if v >= 0 else -1.0 for v in raw]
    buzzy = [0.6 * sq[i] + 0.4 * raw[i] for i in range(n)]
    trem = _amp_modulate(buzzy, 14.0, 0.6)
    return _fade(trem, 0.02, 0.05), -3.0


def rec_breath(rng):
    dur = 1.5
    n = int(dur * SAMPLE_RATE)
    band = _highpass(_lowpass(_noise(dur, rng), 1800), 150)
    env = [math.sin(math.pi * i / n) for i in range(n)]
    return _apply_envelope(band, env), -9.0


def rec_clay_squish(rng):
    dur = 0.4
    n = int(dur * SAMPLE_RATE)
    band = _lowpass(_noise(dur, rng), 1200)
    band = _apply_envelope(band, _env_ar(n, 0.03))
    thump = _apply_envelope(_sine_sweep(180, 70, dur), _envelope_exp_decay(n, 0.12))
    return _mix(_gain(band, 0.7), _gain(thump, 0.6)), -2.5


def rec_wood_knock(rng):
    sr = SAMPLE_RATE

    def knock():
        d = 0.12
        n = int(d * sr)
        return _apply_envelope(_sine(320, d), _envelope_exp_decay(n, 0.02))

    k1, gap, k2, tail = knock(), _silence(int(0.12 * sr)), _gain(knock(), 0.8), _silence(int(0.1 * sr))
    return _concat(k1, gap, k2, tail), -2.0


def rec_paper_rustle(rng):
    dur = 0.8
    band = _highpass(_noise(dur, rng), 3000)
    mod = _amp_modulate(band, 11.0, 0.7)
    return _fade(mod, 0.02, 0.08), -6.0


def rec_tiny_bell(rng):
    dur = 0.6
    n = int(dur * SAMPLE_RATE)
    f0 = 2093.0
    mix = _mix(_sine(f0, dur), _sine(f0 * 2, dur, amp=0.3))
    return _apply_envelope(mix, _envelope_exp_decay(n, 0.15)), -2.0


def rec_footsteps_felt(rng):
    sr = SAMPLE_RATE

    def step():
        d = 0.1
        n = int(d * sr)
        band = _lowpass(_noise(d, rng), 700)
        return _apply_envelope(band, _envelope_exp_decay(n, 0.03))

    s1, gap, s2, tail = step(), _silence(int(0.3 * sr)), step(), _silence(int(0.15 * sr))
    return _concat(s1, gap, s2, tail), -6.0


def rec_whoosh_soft(rng):
    dur = 0.6
    n = int(dur * SAMPLE_RATE)
    band = _highpass(_lowpass(_noise(dur, rng), 2200), 400)
    env = [math.sin(math.pi * i / n) for i in range(n)]
    return _apply_envelope(band, env), -6.0


def rec_twinkle(rng):
    sr = SAMPLE_RATE
    freqs = [1568, 1976, 2637]
    parts = []
    for i, f in enumerate(freqs):
        d = 0.18
        n = int(d * sr)
        parts.append(_apply_envelope(_sine(f, d), _envelope_exp_decay(n, 0.06)))
        if i < len(freqs) - 1:
            parts.append(_silence(int(0.05 * sr)))
    return _concat(*parts), -2.5


def rec_footsteps_tiny(rng):
    sr = SAMPLE_RATE

    def step():
        d = 0.05
        n = int(d * sr)
        band = _highpass(_lowpass(_noise(d, rng), 5000), 1500)
        return _apply_envelope(band, _envelope_exp_decay(n, 0.012))

    s1, gap, s2, gap2 = step(), _silence(int(0.14 * sr)), step(), _silence(int(0.14 * sr))
    s3, tail = _gain(step(), 0.85), _silence(int(0.1 * sr))
    return _concat(s1, gap, s2, gap2, s3, tail), -4.0


def rec_gasp_crowd(rng):
    dur = 0.5
    n = int(dur * SAMPLE_RATE)
    band = _highpass(_lowpass(_noise(dur, rng), 2500), 300)
    rise = 0.1 * SAMPLE_RATE
    env = [(i / rise) if i < rise else math.exp(-(i - rise) / (0.12 * SAMPLE_RATE)) for i in range(n)]
    return _apply_envelope(band, env), -2.5


def rec_dramatic_sting(rng):
    dur = 1.5
    n = int(dur * SAMPLE_RATE)
    attack = 0.1 * SAMPLE_RATE
    tone_env = [min(1.0, i / attack) * math.exp(-max(0, i - attack) / (0.6 * SAMPLE_RATE)) for i in range(n)]
    tone = _apply_envelope(_sine(65, dur), tone_env)
    hit = _lowpass(_noise(0.12, rng), 3000)
    hit = _apply_envelope(hit, _envelope_exp_decay(len(hit), 0.03))
    return _mix(tone, _gain(hit, 0.7)), -1.5


def rec_slap(rng):
    dur = 0.15
    n = int(dur * SAMPLE_RATE)
    band = _highpass(_noise(dur, rng), 1200)
    return _apply_envelope(band, _envelope_exp_decay(n, 0.015)), -1.2


def rec_phone_ring(rng):
    sr = SAMPLE_RATE

    def burst():
        d = 0.35
        mix = _mix(_sine(480, d), _sine(620, d))
        return _fade(mix, 0.01, 0.05)

    b1, gap, b2, tail = burst(), _silence(int(0.15 * sr)), burst(), _silence(int(0.1 * sr))
    return _concat(b1, gap, b2, tail), -2.0


def rec_waves_soft(rng):
    dur = 2.5
    n = int(dur * SAMPLE_RATE)
    band = _lowpass(_noise(dur, rng), 700)
    swell = [0.5 + 0.5 * math.sin(2 * math.pi * 0.3 * i / SAMPLE_RATE) for i in range(n)]
    out = _apply_envelope(band, swell)
    return _fade(out, 0.15, 0.2), -6.0


def rec_door_slam(rng):
    dur = 0.45
    n = int(dur * SAMPLE_RATE)
    thump = _apply_envelope(_sine(75, dur), _envelope_exp_decay(n, 0.07))
    burst = _highpass(_lowpass(_noise(0.1, rng), 4000), 300)
    burst = _apply_envelope(burst, _envelope_exp_decay(len(burst), 0.025))
    return _mix(thump, _gain(burst, 0.8)), -1.2


def rec_page_turn(rng):
    dur = 0.4
    n = int(dur * SAMPLE_RATE)
    band = _highpass(_noise(dur, rng), 2500)
    return _apply_envelope(band, _env_ar(n, 0.04)), -6.0


def rec_wind_soft(rng):
    dur = 2.5
    band = _lowpass(_noise(dur, rng), 500)
    mod = _amp_modulate(band, 0.15, 0.3)
    return _fade(mod, 0.15, 0.2), -8.0


def rec_birds(rng):
    sr = SAMPLE_RATE
    chirp_freqs = [(3200, 3800), (2800, 2400), (3600, 4200)]
    parts = []
    for f0, f1 in chirp_freqs:
        d = 0.12
        n = int(d * sr)
        parts.append(_apply_envelope(_sine_sweep(f0, f1, d), _envelope_exp_decay(n, 0.04)))
        parts.append(_silence(int((0.08 + rng.random() * 0.05) * sr)))
    return _concat(*parts), -3.0


def rec_footsteps_grass(rng):
    sr = SAMPLE_RATE

    def step():
        d = 0.09
        n = int(d * sr)
        band = _highpass(_lowpass(_noise(d, rng), 2800), 900)
        return _apply_envelope(band, _envelope_exp_decay(n, 0.025))

    s1, gap, s2, tail = step(), _silence(int(0.3 * sr)), step(), _silence(int(0.15 * sr))
    return _concat(s1, gap, s2, tail), -6.0


CUE_RECIPES = {
    "whoosh_sharp": rec_whoosh_sharp,
    "impact_hit": rec_impact_hit,
    "heartbeat": rec_heartbeat,
    "rain_loop": rec_rain_loop,
    "chime": rec_chime,
    "boing": rec_boing,
    "honk": rec_honk,
    "slide_whistle": rec_slide_whistle,
    "pop": rec_pop,
    "record_scratch": rec_record_scratch,
    "room_tone": rec_room_tone,
    "footsteps_concrete": rec_footsteps_concrete,
    "car_pass": rec_car_pass,
    "phone_buzz": rec_phone_buzz,
    "breath": rec_breath,
    "clay_squish": rec_clay_squish,
    "wood_knock": rec_wood_knock,
    "paper_rustle": rec_paper_rustle,
    "tiny_bell": rec_tiny_bell,
    "footsteps_felt": rec_footsteps_felt,
    "whoosh_soft": rec_whoosh_soft,
    "twinkle": rec_twinkle,
    "footsteps_tiny": rec_footsteps_tiny,
    "gasp_crowd": rec_gasp_crowd,
    "dramatic_sting": rec_dramatic_sting,
    "slap": rec_slap,
    "phone_ring": rec_phone_ring,
    "waves_soft": rec_waves_soft,
    "door_slam": rec_door_slam,
    "page_turn": rec_page_turn,
    "wind_soft": rec_wind_soft,
    "birds": rec_birds,
    "footsteps_grass": rec_footsteps_grass,
}


def synthesize_wav_bytes(cue_name: str):
    """Return ``(wav_bytes, duration_s)`` for one cue, deterministically."""
    if cue_name not in CUE_RECIPES:
        raise KeyError(f"no synthesis recipe for cue {cue_name!r}")
    rng = _rng(cue_name)
    samples, peak_dbfs = CUE_RECIPES[cue_name](rng)
    samples = _normalize(samples, peak_dbfs)
    pcm = _to_pcm16(samples)
    wav = _wav_bytes(pcm)
    duration_s = len(samples) / SAMPLE_RATE
    return wav, duration_s


# ======================================================================
# Pack / cue discovery from the shipped style templates.
# ======================================================================

def load_pack_cues() -> dict:
    """``{pack_name: set(cue_name, ...)}`` read from every style template's
    ``audio`` block. The templates are the source of truth; this function
    trusts them over any hardcoded list."""
    pack_cues: dict = {}
    for path in sorted(STYLES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        audio = data.get("audio")
        if not audio:
            continue
        pack = audio["sfx_pack"]
        cues = audio.get("sfx_cues", [])
        pack_cues.setdefault(pack, set()).update(cues)
    return pack_cues


def cue_owners(pack_cues: dict) -> dict:
    """``{cue_name: owner_pack}``: the alphabetically first pack (by pack
    name) that names a cue owns the one physical file for it."""
    owners: dict = {}
    for pack in sorted(pack_cues):
        for cue in pack_cues[pack]:
            owners.setdefault(cue, pack)
    return owners


def _write_json(path: Path, obj) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def write_sfx(pack_cues: dict) -> None:
    owners = cue_owners(pack_cues)
    cue_info: dict = {}

    for cue in sorted(owners):
        owner = owners[cue]
        wav_bytes, duration_s = synthesize_wav_bytes(cue)
        if duration_s > MAX_DURATION_S + 1e-9:
            raise ValueError(f"cue {cue!r} is {duration_s:.3f}s, over the {MAX_DURATION_S}s cap")
        if len(wav_bytes) > MAX_BYTES:
            raise ValueError(f"cue {cue!r} is {len(wav_bytes)} bytes, over the {MAX_BYTES} byte cap")
        out_path = SFX_DIR / owner / f"{cue}.wav"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(wav_bytes)
        cue_info[cue] = {
            "owner": owner,
            "duration_s": round(duration_s, 3),
            "sha256": hashlib.sha256(wav_bytes).hexdigest(),
        }

    for pack in sorted(pack_cues):
        cues_section = {}
        for cue in sorted(pack_cues[pack]):
            info = cue_info[cue]
            cues_section[cue] = {
                "file": f"{info['owner']}/{cue}.wav",
                "duration_s": info["duration_s"],
                "sha256": info["sha256"],
                "licence": "self-made",
                "source": GENERATOR_SOURCE,
            }
        index = {"$schema": "sfx_index_v1", "pack": pack, "cues": cues_section}
        index_path = SFX_DIR / pack / "sfx_index.json"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(index_path, index)


# ======================================================================
# Paper-texture overlay PNG: a small hand-rolled encoder (stdlib zlib +
# struct only), deterministic, no PIL.
# ======================================================================

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    chunk = tag + data
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)


def _box_blur_wrap(img, size):
    out = [[0.0] * size for _ in range(size)]
    for y in range(size):
        ym1, yp1 = (y - 1) % size, (y + 1) % size
        for x in range(size):
            xm1, xp1 = (x - 1) % size, (x + 1) % size
            s = (
                img[ym1][xm1] + img[ym1][x] + img[ym1][xp1]
                + img[y][xm1] + img[y][x] + img[y][xp1]
                + img[yp1][xm1] + img[yp1][x] + img[yp1][xp1]
            )
            out[y][x] = s / 9.0
    return out


def write_paper_texture() -> None:
    size = 256
    rng = _rng("paper_texture")
    raw = [[rng.random() for _ in range(size)] for _ in range(size)]
    blurred = _box_blur_wrap(raw, size)
    blurred = _box_blur_wrap(blurred, size)

    rows = []
    for y in range(size):
        row = bytearray([0])  # PNG filter type 0 (None) for this scanline
        for x in range(size):
            base = 205 + (blurred[y][x] - 0.5) * 40.0
            grain = (raw[y][x] - 0.5) * 18.0
            v = max(0, min(255, int(round(base + grain))))
            row.append(v)
        rows.append(bytes(row))
    raw_data = b"".join(rows)
    compressed = zlib.compress(raw_data, 9)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8-bit grayscale
    png = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b"")

    OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
    (OVERLAYS_DIR / "paper_texture.png").write_bytes(png)


def main() -> int:
    pack_cues = load_pack_cues()
    if not pack_cues:
        print("no style templates with an audio block found; nothing to do", file=sys.stderr)
        return 1
    write_sfx(pack_cues)
    write_paper_texture()
    n_cues = len({c for cues in pack_cues.values() for c in cues})
    print(f"wrote {n_cues} SFX cues across {len(pack_cues)} packs, plus assets/overlays/paper_texture.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
