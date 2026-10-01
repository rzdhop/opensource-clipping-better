"""The tier-2 golden render fixture (AI Story phase 6, stage 9; spec 13, 6.5;
DEC-156, DEC-158): ``render/golden.py``'s sibling, for a shot cut from its own
clip.

A tiny episode rendered with the real runner in the GOLDEN profile:

- ``sh01`` (the hook) is cut from a **1.0 s clip** that ffmpeg itself makes
  here -- ``lavfi`` ``testsrc`` at 24 fps with a two-decimal timestamp, a
  120x208 frame (not 9:16, so the clip is centre-cropped -- T2-P6-F1),
  encoded with the golden profile's own libx264 settings and parity flags --
  for a shot of 1.5 s: the clip is shorter than its shot, so
  ``filtergraph.tier2_clip_argv`` holds its last frame to the shot's exact
  frame count;
- ``sh02`` (the cliffhanger) is a still with a push-in and the paper texture,
  cut to ``sh01``;
- three line blips, the synthetic bed, ``word_pop`` subtitles and the AI
  label, ``fadeblack`` into the 1.0 s end card.

The template, the style lock, the line blips, the bed and the images are
golden.py's own helpers and literals; the tier-1 fixture itself is left as
it is (its parity keys never move for this one).

**Variant.** ``variant_clip`` makes ``sh01``'s clip from ``testsrc2`` instead
-- another clip of the same length -- which the partial re-render test
renders over a warm cache and clean (RC-M8).

**Parity keys.** ``tests/fixtures/aistory_golden_tier2/framemd5.json`` maps
``"<ffmpeg version>/<machine>"`` to the sha256 of the video's ``.framemd5``,
exactly as ``tests/fixtures/aistory_golden/framemd5.json`` does for the
tier-1 fixture. An unknown key is a failure, never a pass:
``tools/render_golden.py --tier2 --record`` adds one after the frames have
been looked at.

Stdlib + this package (DEC-012): usable by the test, by
``tools/render_golden.py`` and inside the deployed container (no pytest).
"""

from __future__ import annotations

import copy
import os
import subprocess
import time
from pathlib import Path

from .. import timing
from . import fonts, golden, profiles, runner
from . import plan as plan_mod

REPO_ROOT = golden.REPO_ROOT
KEYS_PATH = REPO_ROOT / "tests" / "fixtures" / "aistory_golden_tier2" / "framemd5.json"
RECORD_COMMAND = "python3 tools/render_golden.py --tier2 --record"

EP = golden.EP
STORY = dict(golden.STORY, title="Golden Tier Two")

# The clip: shorter than its shot (the hold), at another rate than the
# render's (24 -> 30 fps) and not 9:16 (centre-cropped, never letterboxed: T2-P6-F1).
CLIP_SHOT = "sh01"
CLIP_S = 1.0
CLIP_FPS = 24
CLIP_SIZE = (120, 208)
CLIP_SOURCE = f"assets/clips/shot_{CLIP_SHOT[2:]}.mp4"

# (shot_id, scene_id, motion) -- one shot a scene, each as long as its scene.
SHOTS = (
    ("sh01", "s01", {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"}),
    ("sh02", "s02", {"type": "push_in", "zoom_from": 1.0, "zoom_to": 1.1, "pan": "none"}),
)
# (line_id, scene_id, speaker, text, tone Hz, seconds): l01 makes the hook
# scene -- 0.35 s before it, 0.55 s of line, the 0.6 s tail -- 1.5 s long.
LINES = (
    ("l01", "s01", "char_a", "Look out", 330.0, 0.55),
    ("l02", "s02", "char_b", "Not again", 440.0, 0.8),
    ("l03", "s02", "char_a", "Run now", 550.0, 0.8),
)
WORD_TIMINGS = {"l01": [{"word": "Look", "start": 0.04, "end": 0.26}, {"word": "out", "start": 0.28, "end": 0.5}]}


# ------------------------------------------------------------------ the clip

def clip_argv(out_path, *, variant=False) -> list:
    """The ffmpeg command that makes the fixture's clip at *out_path*:
    ``testsrc`` (``testsrc2`` for the *variant*) for :data:`CLIP_S` s at
    :data:`CLIP_FPS` fps, :data:`CLIP_SIZE`, with the golden profile's own
    encode settings and parity flags (``profiles.GOLDEN``), no audio."""
    width, height = CLIP_SIZE
    source = (f"testsrc2=size={width}x{height}:rate={CLIP_FPS}:duration={CLIP_S:g}" if variant
              else f"testsrc=size={width}x{height}:rate={CLIP_FPS}:duration={CLIP_S:g}:decimals=2")
    profile = profiles.GOLDEN
    return (["ffmpeg", "-hide_banner", "-nostdin", "-y"] + profile.global_bitexact_args()
            + ["-f", "lavfi", "-i", source] + profile.video_encode_args() + ["-an", os.fspath(out_path)])


def make_clip(out_path, *, variant=False, run=subprocess.run) -> None:
    """Make the fixture's clip (:func:`clip_argv`); RuntimeError with the end
    of ffmpeg's stderr when it cannot."""
    result = run(clip_argv(out_path, variant=variant), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0 or not os.path.isfile(out_path):
        raise RuntimeError(f"the fixture's clip could not be made (exit {result.returncode}): "
                           f"{(result.stderr or '')[-800:]}")


# ------------------------------------------------------------------ fixture

def build_documents() -> dict:
    """The script, storyboard, assets doc, style lock and template (no
    files): ``sh01`` names its clip (``assets.video``), ``sh02`` none."""
    by_scene = {}
    for line_id, scene_id, speaker, text, _hz, seconds in LINES:
        by_scene.setdefault(scene_id, []).append(golden._line(line_id, speaker, text, seconds))
    script = {"scenes": [golden._scene("s01", "hook", by_scene["s01"]),
                         golden._scene("s02", "cliffhanger", by_scene["s02"])],
              "hook": None, "cliffhanger": {"scene_id": "s02", "reveal": "The door opens.", "cut_to_black": True}}

    shots = []
    for order, (shot_id, scene_id, motion) in enumerate(SHOTS, 1):
        shots.append({"shot_id": shot_id, "scene_id": scene_id, "order": order, "duration_s": 0.0,
                      "motion": dict(motion), "modifiers": [], "keep_still": False,
                      "assets": {"image": f"assets/shots/shot_{order:02d}.png",
                                 "video": CLIP_SOURCE if shot_id == CLIP_SHOT else None,
                                 "seed": None, "provider": None, "approved": True}})
    board = {"shots": shots, "transitions": []}
    # Timed as golden.py's board is (no ``whole_frames`` flag): each shot is
    # its scene, as long as the timing the render reads for it.
    real, _scene_t = timing.episode_pass(script, golden.TEMPLATE, STORY["language"], style_lock=golden.STYLE_LOCK,
                                         storyboard=board, whole_frames=timing.board_whole_frames(board))
    for shot in shots:
        shot["duration_s"] = round(real["scenes"][shot["scene_id"]]["duration_s"], 3)

    assets = {
        "lines": {"l01": {"words_source": "provider"}, "l02": {"words_source": "even_split"},
                  "l03": {"words_source": "even_split"}},
        "sfx": [],
        "bgm": {"mood": "golden_bed", "dominant_emotion": "tension", "weights_s": {"tension": 1.0},
                "file": "fixture/bed.wav", "sha256": None, "licence": "self-made"},
    }
    return {"script": script, "storyboard": board, "assets": assets, "style_lock": copy.deepcopy(golden.STYLE_LOCK),
            "template": copy.deepcopy(golden.TEMPLATE)}


def write_sources(workdir, *, variant_clip=False, run=subprocess.run) -> dict:
    """Write the fixture's media under ``<workdir>/sources/`` -- the clip made
    by ffmpeg (:func:`make_clip`) -- and return the plan's ``inputs``
    (hashed), as the render step hands them at tier 2: every shot's image,
    the current clip of ``sh01`` (``videos``) and every shot's effective
    ``keep_still``."""
    src = Path(workdir) / "sources"
    for sub in ("shots", "clips", "voice", "bgm", "custom_fonts"):
        (src / sub).mkdir(parents=True, exist_ok=True)

    shots = {}
    for index, (shot_id, _scene, _motion) in enumerate(SHOTS):
        path = src / "shots" / f"shot_{index + 1:02d}.png"
        path.write_bytes(golden.png_bytes(*golden.IMAGE_SIZE, golden._shot_pixel(index, 0)))
        shots[shot_id] = runner.file_record(path, f"fixture/shots/{path.name}")
    clip = src / "clips" / os.path.basename(CLIP_SOURCE)
    make_clip(clip, variant=variant_clip, run=run)

    lines = {}
    for line_id, _scene, _speaker, _text, hz, seconds in LINES:
        path = src / "voice" / f"{line_id}.wav"
        golden.wav_bytes_to(path, golden.LINE_RATE, golden._blip(golden.LINE_RATE, seconds, hz))
        lines[line_id] = runner.file_record(path, f"fixture/voice/{path.name}")

    bed = src / "bgm" / "bed.wav"
    golden.wav_bytes_to(bed, golden.BED_RATE, golden._bed(golden.BED_RATE, golden.BED_S))
    font = fonts.resolve_font(golden.STYLE_LOCK["typography"]["font_family"], custom_fonts_dir=src / "custom_fonts")
    return {
        "shots": shots,
        "videos": {CLIP_SHOT: runner.file_record(clip, f"fixture/clips/{clip.name}")},
        "keep_still": {shot_id: False for shot_id, _scene, _motion in SHOTS},
        "lines": lines,
        "sfx": {},
        "bgm": runner.file_record(bed, "fixture/bgm/bed.wav"),
        "overlay": runner.paper_texture_record(),
        "font": font,
        "word_timings": copy.deepcopy(WORD_TIMINGS),
    }


def render_fixture(workdir, *, variant_clip=False, run=subprocess.run, popen=subprocess.Popen, on_log=None) -> dict:
    """Write the fixture into *workdir* and render it with the real runner
    (GOLDEN profile), as ``golden.render_fixture`` does: ``render/`` is the
    working folder, the manifests and ``episode_final.mp4`` sit beside it.
    *variant_clip*: module docstring, "Variant". Returns the runner's result
    plus ``seconds`` and ``key``."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    docs = build_documents()
    inputs = write_sources(workdir, variant_clip=variant_clip, run=run)
    started = time.monotonic()
    result = runner.render(
        plan_args={**docs, "story": dict(STORY), "ep": EP, "inputs": inputs},
        profile="golden", render_dir=workdir / "render", manifest_path=workdir / "render_manifest.json",
        final_path=workdir / plan_mod.FINAL_REL, run=run, popen=popen, on_log=on_log)
    result["seconds"] = round(time.monotonic() - started, 2)
    manifest = result.get("manifest")
    result["key"] = golden.parity_key(manifest["ffmpeg"]) if manifest else None
    return result


# --------------------------------------------------------------- parity keys

def load_keys(path=KEYS_PATH) -> dict:
    """The recorded tier-2 parity keys (``golden.load_keys``); {} when none."""
    return golden.load_keys(path)


def parity_problem(key: str, digest: str, keys: dict):
    """``golden.parity_problem`` for this fixture: its own record command."""
    return golden.parity_problem(key, digest, keys, command=RECORD_COMMAND)
