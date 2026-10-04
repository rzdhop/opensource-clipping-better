"""The golden render fixture (spec 13; plan phase 4 stage 7, "Golden render and
parity"; DEC-156).

A tiny, self-contained episode rendered with the real runner in the GOLDEN
profile, so one test proves that the renderer's whole topology runs on the
ffmpeg at hand and produces exactly the frames it produced before:

- 3 shots from 108x192 PNGs written here (stdlib ``zlib`` + ``struct``), with
  a push-in, a hold and a pan, the bundled paper texture on every shot;
- 3 lines of 0.8 s WAV (a tone blip each), one with provider word timings;
- a 2 s synthetic bed (looped by the mix), one shipped SFX from
  ``assets/sfx``;
- the shipped Montserrat Black (``custom_fonts/`` is never consulted);
- a cut, a dissolve, then ``fadeblack`` into the 1.0 s end card
  (``cut_to_black``); ``word_pop`` subtitles and the AI label.

The template and style below are literals, not the shipped files: editing a
shipped template must not move the golden digest. What does move it -- the
timing engine, the builders, the ASS layout, ffmpeg itself -- is exactly what
the parity rule is for.

**Variants.** ``variant_shot`` (a shot id: other colours for its image) and
``line_seconds`` (``{line_id: seconds}``: that line's blip and its timing
last longer or shorter, so its scene, and the shot that ends it, do too) make
the edited documents the partial re-render test renders twice -- over a warm
cache and clean (phase 5 stage 8, RC-M8). Without them the fixture is the one
the parity keys record.

**Parity keys.** ``tests/fixtures/aistory_golden/framemd5.json`` maps
``"<ffmpeg version>/<machine>"`` (the manifest's ``ffmpeg`` block) to the
sha256 of the ``.framemd5`` file of the video. An unknown key is a failure,
never a pass: ``tools/render_golden.py --record`` adds one after the frames
have been looked at.

**Aspects** (plan 23 stage B6). ``render_fixture(aspect="16:9")`` and
``aspect="1:1"`` render the same episode in another frame: the shot images
are 192x108 / 108x108 (:data:`IMAGE_SIZES`, drawn with the same picture
laid out for that size) and the plan draws every shot, the end card and the
text to that frame. Each aspect has its own keys file (:data:`KEYS_PATHS`:
``framemd5_16x9.json``, ``framemd5_1x1.json``), recorded with
``tools/render_golden.py --record --aspect <aspect>``. The 9:16 fixture,
its keys and its digests are the ones they always were.

Stdlib + this package (DEC-012): usable by the test, by
``tools/render_golden.py`` and inside the deployed container (no pytest).
"""

from __future__ import annotations

import copy
import json
import math
import os
import struct
import subprocess
import time
import wave
import zlib
from pathlib import Path

from .. import timing
from . import audio_assets, fonts, plan as plan_mod, runner

REPO_ROOT = fonts.REPO_ROOT
KEYS_PATH = REPO_ROOT / "tests" / "fixtures" / "aistory_golden" / "framemd5.json"
RECORD_COMMAND = "python3 tools/render_golden.py --record"
# One keys file per frame (plan 23 stage B6); 9:16 keeps the original file.
ASPECTS = ("9:16", "16:9", "1:1")
KEYS_PATHS = {
    "9:16": KEYS_PATH,
    "16:9": KEYS_PATH.with_name("framemd5_16x9.json"),
    "1:1": KEYS_PATH.with_name("framemd5_1x1.json"),
}

EP = 1
STORY = {"story_id": "0123456789ab", "title": "Golden Fixture", "language": "en"}
SFX_PACK = "soap"
SFX_CUE = "dramatic_sting"

IMAGE_SIZE = (108, 192)
IMAGE_SIZES = {"9:16": IMAGE_SIZE, "16:9": (192, 108), "1:1": (108, 108)}
LINE_RATE = 24000
LINE_S = 0.8
BED_RATE = 44100
BED_S = 2.0

# serial_60s_v1's timing values, frozen here, with a window that fits a 5 s
# episode, no hold extension and the plain tail on the cliffhanger (so the
# fixture stays under 5 s: the golden test runs on every CI push).
TEMPLATE = {
    "$schema": "episode_template_v1",
    "template_id": "golden_5s",
    "version": 1,
    "window_s": [3, 10],
    "target_s": 5,
    "tighten_above_s": 9,
    "scenes": [2, 12],
    "shots": [3, 30],
    "min_shot_s": 0.8,
    "recap_from_episode": 2,
    "default_body_count": 8,
    "slots": {
        "recap": {"functions": ["recap"], "count": [1, 1], "duration_s": [2.0, 3.0]},
        "hook": {"functions": ["hook"], "count": [1, 1], "duration_s": [1.5, 3.5]},
        "body": {"functions": ["setup", "rising", "peak", "turn"], "count": [5, 9], "duration_s": [4.0, 8.0]},
        "cliffhanger": {"functions": ["cliffhanger"], "count": [1, 1], "duration_s": [2.0, 5.0]},
    },
    "pauses_s": {"before_first_line": 0.35, "between_lines": 0.25, "tail": 0.6, "tail_peak": 1.2, "tail_floor": 0.3},
    "tail_peak_functions": ["peak"],
    "hold_extension_max_s": 0.0,
    "end_card_s": 1.0,
    "transitions_s": {"cut": 0.0, "dissolve": 0.4, "fadeblack": 0.4, "fadewhite": 0.3, "wipeleft": 0.3,
                      "wiperight": 0.3, "slideup": 0.3},
}

STYLE_LOCK = {
    "palette": {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFFFFF", "#1E1E24"]},
    "motion_rules": {"tier1": {"overlays": ["paper_texture"], "modifiers": []}},
    "typography": {"font_family": "Montserrat ExtraBold", "subtitle_mode": "word_pop",
                   "highlight_colour": "#FFD400", "ai_label": True},
    "episode_defaults": {"hook_style": "insert_prop", "cliffhanger_style": "cut_to_black"},
    "audio": {"sfx_pack": SFX_PACK},
}

# (shot_id, scene_id, duration_s, motion)
SHOTS = (
    ("sh01", "s01", 0.9, {"type": "push_in", "zoom_from": 1.0, "zoom_to": 1.1, "pan": "none"}),
    ("sh02", "s01", None, {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}),
    ("sh03", "s02", None, {"type": "pan_lr", "zoom_from": 1.04, "zoom_to": 1.04, "pan": "lr"}),
)
# (line_id, scene_id, speaker, text, tone Hz)
LINES = (
    ("l01", "s01", "char_a", "Look out", 330.0),
    ("l02", "s02", "char_b", "Not again", 440.0),
    ("l03", "s02", "char_a", "Run now", 550.0),
)
WORD_TIMINGS = {"l01": [{"word": "Look", "start": 0.12, "end": 0.38}, {"word": "out", "start": 0.4, "end": 0.7}]}


# ------------------------------------------------------------- stdlib media

def png_bytes(width: int, height: int, pixel) -> bytes:
    """An 8-bit RGB PNG; *pixel(x, y)* returns ``(r, g, b)``."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        for x in range(width):
            rows.extend(pixel(x, y))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) \
        + chunk(b"IEND", b"")


def _shot_pixel(index: int, variant: int, size=IMAGE_SIZE):
    """A gradient backdrop, a sun disc and a floor band, coloured per shot;
    *variant* shifts the colours (the cache test's "changed image"). The
    disc's centre and the floor's top are laid out for *size* (``(w, h)``):
    at the 108x192 of the 9:16 fixture they are exactly the pixels they
    always were."""
    base = ((200, 90, 40), (40, 110, 190), (60, 150, 80))[index]
    shift = 70 * variant
    width, height = size
    cx = (30 + 24 * index) * width // IMAGE_SIZE[0]
    cy = (60 + 20 * index) * height // IMAGE_SIZE[1]
    floor = 150 * height // IMAGE_SIZE[1]
    radius = 18

    def pixel(x, y):
        if y > floor:
            return (30, 30, 36)
        if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
            return (250, 225, 120)
        r = (base[0] + shift + x) % 256
        g = (base[1] + y // 2) % 256
        b = (base[2] + (x + y) // 3 + shift) % 256
        return (r, g, b)

    return pixel


def wav_bytes_to(path, rate: int, samples) -> None:
    with wave.open(os.fspath(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def _blip(rate: int, seconds: float, hz: float):
    """Silence, a 0.5 s tone with 20 ms fades, silence."""
    n = int(round(rate * seconds))
    start, length, fade = int(rate * 0.15), int(rate * 0.5), int(rate * 0.02)
    out = []
    for i in range(n):
        k = i - start
        if 0 <= k < length:
            gain = min(1.0, k / fade, (length - 1 - k) / fade)
            out.append(int(round(0.5 * 32767 * gain * math.sin(2 * math.pi * hz * k / rate))))
        else:
            out.append(0)
    return out


def _bed(rate: int, seconds: float):
    n = int(round(rate * seconds))
    return [int(round(32767 * 0.2 * (math.sin(2 * math.pi * 110 * i / rate)
                                    + 0.5 * math.sin(2 * math.pi * 165 * i / rate)) / 1.5)) for i in range(n)]


# ------------------------------------------------------------------ fixture

def _line_seconds(line_id, line_seconds) -> float:
    return float((line_seconds or {}).get(line_id, LINE_S))


def _line(line_id, speaker, text, seconds=LINE_S):
    return {"line_id": line_id, "speaker": speaker, "text": text, "emotion": "neutral", "delivery": "calm",
            "timing": {"source": "tts_word_timestamps", "duration_s": seconds, "text_hash": timing.text_hash(text),
                       "voice": "edge/en-US-GuyNeural", "audio": f"assets/voice/{line_id}.wav"}}


def _scene(scene_id, function, lines, sfx_cues=()):
    return {"scene_id": scene_id, "function": function, "place_id": "place_a", "time_variant": "day",
            "characters": ["char_a", "char_b"], "props": [], "summary": "Golden fixture.", "emotion": "tension",
            "target_duration_s": 2.0, "lines": lines, "sfx_cues": list(sfx_cues), "on_screen_text": None,
            "state": "written", "source": "E2", "rev": 1}


def build_documents(*, line_seconds=None) -> dict:
    """The script, storyboard, assets doc, style lock and template (no files).
    *line_seconds* (``{line_id: seconds}``) times those lines otherwise."""
    by_scene = {}
    for line_id, scene_id, speaker, text, _hz in LINES:
        by_scene.setdefault(scene_id, []).append(_line(line_id, speaker, text, _line_seconds(line_id, line_seconds)))
    scenes = [
        _scene("s01", "hook", by_scene["s01"], [{"at": "start", "cue": SFX_CUE}]),
        _scene("s02", "cliffhanger", by_scene["s02"]),
    ]
    script = {"scenes": scenes, "hook": None,
              "cliffhanger": {"scene_id": "s02", "reveal": "The door opens.", "cut_to_black": True}}

    shots = []
    for order, (shot_id, scene_id, duration, motion) in enumerate(SHOTS, 1):
        shots.append({"shot_id": shot_id, "scene_id": scene_id, "order": order, "duration_s": duration or 0.0,
                      "motion": dict(motion), "modifiers": [], "keep_still": False,
                      "assets": {"image": f"assets/shots/shot_{order:02d}.png", "video": None, "seed": None,
                                 "provider": None, "approved": True}})
    board = {"shots": shots, "transitions": [{"after": "sh02", "type": "dissolve", "duration_s": 0.4}]}
    # A board timed before whole frames (no ``whole_frames`` flag): its shots
    # are cut to the timing the render reads for it, the recorded frames'.
    real, _scene_t = timing.episode_pass(script, TEMPLATE, STORY["language"], style_lock=STYLE_LOCK,
                                         storyboard=board, whole_frames=timing.board_whole_frames(board))
    for scene_id in ("s01", "s02"):
        own = [shot for shot in shots if shot["scene_id"] == scene_id]
        total = real["scenes"][scene_id]["duration_s"]
        fixed = sum(shot["duration_s"] for shot in own[:-1])
        own[-1]["duration_s"] = round(total - fixed, 3)

    assets = {
        "lines": {"l01": {"words_source": "provider"}, "l02": {"words_source": "even_split"},
                  "l03": {"words_source": "even_split"}},
        "sfx": [{"scene_id": "s01", "at": "start", "cue": SFX_CUE, "pack": SFX_PACK,
                 "file": f"{SFX_PACK}/{SFX_CUE}.wav", "offset_s": 0.0, "state": "resolved"}],
        "bgm": {"mood": "golden_bed", "dominant_emotion": "tension", "weights_s": {"tension": 1.0},
                "file": "fixture/bed.wav", "sha256": None, "licence": "self-made"},
    }
    return {"script": script, "storyboard": board, "assets": assets, "style_lock": copy.deepcopy(STYLE_LOCK),
            "template": copy.deepcopy(TEMPLATE)}


def write_sources(workdir, *, variant_shot=None, line_seconds=None, aspect="9:16") -> dict:
    """Write the fixture's media under ``<workdir>/sources/`` and return the
    plan's ``inputs`` (hashed). *variant_shot* (a shot id) gets different
    colours; *line_seconds* (``{line_id: seconds}``) makes those lines'
    blips that long; *aspect* sizes the shot images (:data:`IMAGE_SIZES`)."""
    size = IMAGE_SIZES[aspect]
    src = Path(workdir) / "sources"
    for sub in ("shots", "voice", "bgm", "custom_fonts"):
        (src / sub).mkdir(parents=True, exist_ok=True)

    shots = {}
    for index, (shot_id, _scene, _d, _m) in enumerate(SHOTS):
        path = src / "shots" / f"shot_{index + 1:02d}.png"
        path.write_bytes(png_bytes(*size, _shot_pixel(index, 1 if shot_id == variant_shot else 0, size)))
        shots[shot_id] = runner.file_record(path, f"fixture/shots/{path.name}")

    lines = {}
    for line_id, _scene, _speaker, _text, hz in LINES:
        path = src / "voice" / f"{line_id}.wav"
        wav_bytes_to(path, LINE_RATE, _blip(LINE_RATE, _line_seconds(line_id, line_seconds), hz))
        lines[line_id] = runner.file_record(path, f"fixture/voice/{path.name}")

    bed = src / "bgm" / "bed.wav"
    wav_bytes_to(bed, BED_RATE, _bed(BED_RATE, BED_S))

    sfx = audio_assets.resolve_sfx(SFX_PACK, SFX_CUE)
    if sfx is None:
        raise FileNotFoundError(f"the shipped SFX {SFX_PACK}/{SFX_CUE} is missing from assets/sfx")
    font = fonts.resolve_font(STYLE_LOCK["typography"]["font_family"], custom_fonts_dir=src / "custom_fonts")
    return {
        "shots": shots,
        "lines": lines,
        "sfx": {SFX_CUE: runner.file_record(sfx["abs_path"], f"assets/sfx/{sfx['file']}")},
        "bgm": runner.file_record(bed, "fixture/bgm/bed.wav"),
        "overlay": runner.paper_texture_record(),
        "font": font,
        "word_timings": copy.deepcopy(WORD_TIMINGS),
    }


def render_fixture(workdir, *, aspect="9:16", variant_shot=None, line_seconds=None, run=subprocess.run,
                   popen=subprocess.Popen, on_log=None) -> dict:
    """Write the fixture into *workdir* and render it with the real runner
    (GOLDEN profile): ``render/`` is the working folder,
    ``render_manifest.json`` (and, once a render completed,
    ``render_manifest.last_good.json``) and ``episode_final.mp4`` sit
    beside it. *variant_shot* and *line_seconds*: module docstring,
    "Variants". *aspect*: module docstring, "Aspects". Returns the runner's
    result plus ``seconds`` and ``key``."""
    if aspect not in IMAGE_SIZES:
        raise ValueError(f"unknown aspect {aspect!r}, expected one of {list(IMAGE_SIZES)}")
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    docs = build_documents(line_seconds=line_seconds)
    inputs = write_sources(workdir, variant_shot=variant_shot, line_seconds=line_seconds, aspect=aspect)
    started = time.monotonic()
    result = runner.render(
        plan_args={**docs, "story": dict(STORY), "ep": EP, "inputs": inputs, "aspect": aspect},
        profile="golden", render_dir=workdir / "render", manifest_path=workdir / "render_manifest.json",
        final_path=workdir / plan_mod.FINAL_REL, run=run, popen=popen, on_log=on_log)
    result["seconds"] = round(time.monotonic() - started, 2)
    manifest = result.get("manifest")
    result["key"] = parity_key(manifest["ffmpeg"]) if manifest else None
    return result


# --------------------------------------------------------------- parity keys

def keys_path(aspect: str = "9:16"):
    """The keys file of *aspect*'s fixture (:data:`KEYS_PATHS`)."""
    return KEYS_PATHS[aspect]


def record_command(aspect: str = "9:16") -> str:
    """The command that records a key of *aspect*'s fixture."""
    return RECORD_COMMAND if aspect == "9:16" else f"{RECORD_COMMAND} --aspect {aspect}"


def parity_key(ffmpeg: dict) -> str:
    """``"<ffmpeg version>/<machine>"`` (DEC-156)."""
    return f"{ffmpeg['version']}/{ffmpeg['machine']}"


def load_keys(path=KEYS_PATH) -> dict:
    """The recorded parity keys (``{key: framemd5 sha256}``); {} when none."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        raise ValueError(f"{path} must map '<ffmpeg version>/<machine>' to a sha256")
    return data


def record_key(key: str, digest: str, path=KEYS_PATH) -> dict:
    """Add or replace *key* in the keys file, keeping every other key."""
    keys = load_keys(path)
    keys[key] = digest
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(dict(sorted(keys.items())), handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)
    return keys


def github_annotation(problem: str, *, title: str = "AI-Story golden render") -> str:
    """*problem* as a GitHub Actions ``::error`` workflow command. CI's logs
    need a sign-in to read, but an annotation is public through the
    check-runs API, so an unrecorded CI key and its digest can be read and
    recorded from the machine that pushed (plan phase 4, Q5). *title* names
    the fixture (the 16:9 and 1:1 fixtures name their aspect; a property
    value escapes ``:`` and ``,`` as well)."""
    message = problem.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    title = title.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A")
    title = title.replace(",", "%2C")
    return f"::error title={title}::{message}"


def parity_problem(key: str, digest: str, keys: dict, *, command: str = RECORD_COMMAND):
    """None when *digest* is the one recorded for *key*; otherwise the
    failure message. An unknown key is a failure too, never a pass: a new
    ffmpeg (or machine) is exactly what the parity rule exists to catch.
    *command* is the one that records a key (the tier-2 sibling,
    ``render/golden_tier2.py``, names its own)."""
    if key not in keys:
        return (f"No golden framemd5 is recorded for {key!r} (this ffmpeg on this machine). "
                f"This render's framemd5 sha256 is {digest}. Look at the frames, then record it with "
                f"`{command}` (known keys: {sorted(keys) or 'none'}).")
    if keys[key] != digest:
        return (f"The golden render's frames changed on {key!r}: framemd5 sha256 {digest}, recorded "
                f"{keys[key]}. If the change is intended, look at the frames and re-record with "
                f"`{command}`.")
    return None
