"""A still that is not 9:16 is centre-cropped, never stretched (AI Story
phase 6, stage 13b; spec 6.5; RC-M2, RC-M3, RC-V1).

``filtergraph.shot_argv`` scaled a still to 1080 wide and let zoompan's
``s=1080x1920`` take whatever height came out, so a square Cloudflare still
was stretched vertically. The render plan now reads each still's size from
its header (``render/imagesize.py``, stdlib) and crops a still that is not
9:16 to 9:16 in its own pixels before the scale. A 9:16 still, a near-9:16
one and one of unknown size keep the argv they always had.

1. real ffmpeg, through the plan: a square still holding a centred square
   comes out holding a square;
2. the guard: a 9:16 or near-9:16 still's argv is byte-identical to the one
   without a size (the unedited tier-1 golden proves the same through the
   runner, RC-M2);
3. ``partial.shot_facts``: the 9:16 crop and a clip's cover are not the
   ``handheld`` modifier; a handheld crop still is;
4. the size reader: PNG, JPEG and WebP from ffmpeg itself; anything else is
   None.

Like the golden tests, the ffmpeg ones never skip: without ffmpeg they fail,
saying so. Stdlib + pytest (DEC-012, A-096: no PIL).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from clipping.aistory.render import filtergraph, golden, partial, profiles, runner
from clipping.aistory.render import plan as plan_mod

FFMPEG = {"version": "6.1.1-test", "machine": "testarch"}


def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH; these tests never skip (DEC-156): install "
                    f"ffmpeg (CI must install it: apt-get install ffmpeg).")


def _ffmpeg(*args, cwd=None) -> bytes:
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args], cwd=cwd,
                          capture_output=True, check=True, stdin=subprocess.DEVNULL).stdout


def _hold(modifiers=()):
    return {"shot_id": "sh01", "scene_id": "s01", "start_s": 0.0, "duration_s": 2.0, "frames": 60,
            "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"},
            "modifiers": list(modifiers), "transition_after": None}


def _bright_box(frame: bytes, width: int, height: int) -> tuple:
    """``(width, height)`` of the box holding every pixel brighter than mid
    grey in a ``gray`` frame."""
    bright = bytes(1 if value > 128 else 0 for value in range(256))
    rows, cols = [], []
    for y in range(height):
        row = frame[y * width:(y + 1) * width].translate(bright)
        first = row.find(1)
        if first >= 0:
            rows.append(y)
            cols += [first, row.rfind(1)]
    return max(cols) - min(cols) + 1, rows[-1] - rows[0] + 1


# ============================================ 1. real ffmpeg, through the plan

def test_a_square_still_holding_a_square_comes_out_holding_a_square(tmp_path):
    _require_ffmpeg()
    side, inner = 250, 62  # 250 is no multiple of 16: the crop must still scale to square pixels
    lo, hi = (side - inner) // 2, (side + inner) // 2
    drawn = tmp_path / "square.png"
    drawn.write_bytes(golden.png_bytes(
        side, side, lambda x, y: (255, 255, 255) if lo <= x < hi and lo <= y < hi else (0, 0, 0)))
    square = tmp_path / "square.jpg"  # a JPEG saying 1:1, as the providers' stills are
    _ffmpeg("-i", str(drawn), "-vf", "setsar=1", "-q:v", "2", str(square))

    docs = golden.build_documents()
    docs["style_lock"]["motion_rules"]["tier1"]["overlays"] = []  # the bare picture, no texture over it
    inputs = golden.write_sources(tmp_path)
    inputs["shots"]["sh02"] = runner.file_record(square, "fixture/shots/square.jpg")  # sh02 is a hold
    plan = plan_mod.build_render_plan(**docs, story=dict(golden.STORY), ep=golden.EP, inputs=inputs,
                                      ffmpeg=FFMPEG, profile="golden")
    stage = next(stage for stage in plan["stages"] if stage["id"] == "S:sh02")
    staged = next(item["staged"] for item in plan["inputs"] if item["role"] == "shot" and item["id"] == "sh02")

    render = tmp_path / "render"
    (render / "in").mkdir(parents=True)
    (render / "cache").mkdir()
    shutil.copyfile(square, render / staged)
    subprocess.run(stage["argv"], cwd=render, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    frame = _ffmpeg("-i", stage["write"], "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-", cwd=render)

    assert len(frame) == profiles.WIDTH * profiles.HEIGHT
    box_w, box_h = _bright_box(frame, profiles.WIDTH, profiles.HEIGHT)
    # 62 of the 126x224 crop's 224 rows -> 531 px a side at 1080x1920 (the
    # stretched square was 268 wide, 476 tall). 2 px: the scaler's edge.
    expected = round(inner * profiles.HEIGHT / 224)
    assert abs(box_w - expected) <= 2 and abs(box_h - expected) <= 2, (box_w, box_h, expected)
    # square pixels: the final pass's concat refuses a cut between two
    # shots whose sample aspect ratios differ
    sar = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=sample_aspect_ratio", "-of", "csv=p=0",
                          stage["write"]], cwd=render, capture_output=True, text=True, check=True).stdout.strip()
    assert sar == "1:1"


# ===================================================================== 2. guard

@pytest.mark.parametrize("size", [(576, 1024), (720, 1280), (1080, 1920), (108, 192), (768, 1344), None])
def test_a_9_16_or_near_9_16_still_keeps_its_argv_byte_for_byte(size):
    for profile in (profiles.SHOT, profiles.GOLDEN):
        for overlays in ([], ["paper_texture", "film_grain", "vignette"]):
            for modifiers in ((), ("handheld", "jitter_stopmotion")):
                before = filtergraph.shot_argv("in/a.jpg", _hold(modifiers), profile, overlays, "cache/x.mp4")
                after = filtergraph.shot_argv("in/a.jpg", _hold(modifiers), profile, overlays, "cache/x.mp4",
                                              image_size=size)
                assert after == before


def test_a_still_that_is_not_9_16_is_cropped_to_9_16_in_its_own_pixels_before_the_scale():
    for size, crop in (((1024, 1024), "crop=576:1024"), ((1024, 1536), "crop=864:1536"),
                       ((1024, 576), "crop=324:576"), ((480, 1080), "crop=468:832"),
                       ((1023, 1023), "crop=558:992")):
        argv = filtergraph.shot_argv("in/a.jpg", _hold(), profiles.SHOT, [], "cache/x.mp4", image_size=size)
        assert argv[argv.index("-filter_complex") + 1].startswith(f"[0:v]{crop},scale=4320:-2,zoompan="), size


# =============================================================== 3. shot_facts

def test_the_9_16_crop_and_a_clips_cover_are_not_the_handheld_modifier():
    plain = filtergraph.shot_argv("in/a.jpg", _hold(), profiles.SHOT, [], "cache/x.mp4")
    at = plain.index("-filter_complex") + 1
    cropped = list(plain)
    cropped[at] = plain[at].replace("[0:v]scale=", "[0:v]crop=576:1024,scale=", 1)
    assert cropped != plain
    handheld = filtergraph.shot_argv("in/a.jpg", _hold(["handheld"]), profiles.SHOT, [], "cache/x.mp4")

    assert partial.shot_facts(cropped)["modifiers"] == partial.shot_facts(plain)["modifiers"]
    assert partial.shot_change(plain, cropped) == "settings"
    assert partial.shot_change(plain, handheld) == "modifiers"
    assert partial.shot_change(handheld, plain) == "modifiers"
    # a Tier >= 2 clip: its cover crop (no zoompan) is no modifier either
    clip = filtergraph.tier2_clip_argv("in/c.mp4", _hold(), profiles.SHOT, "cache/x.mp4")
    assert partial.shot_facts(clip)["modifiers"] == (None, None, False, False)


# ============================================================ 4. the size reader

def test_the_size_reader_reads_png_jpeg_and_webp_headers(tmp_path):
    _require_ffmpeg()
    from clipping.aistory.render import imagesize

    made = {
        "png": ("color=red:s=123x457,format=rgb24", [], "a.png", (123, 457)),
        "jpeg": ("color=red:s=123x457,format=yuvj444p", [], "a.jpg", (123, 457)),
        "webp VP8": ("color=red:s=200x356", ["-c:v", "libwebp"], "a.webp", (200, 356)),
        "webp VP8L": ("color=red@0.5:s=123x457,format=rgba", ["-c:v", "libwebp", "-lossless", "1"], "b.webp",
                      (123, 457)),
        "webp VP8X": ("color=red@0.5:s=123x457,format=rgba", ["-c:v", "libwebp"], "c.webp", (123, 457)),
    }
    for label, (source, codec, name, size) in made.items():
        _ffmpeg("-f", "lavfi", "-i", source, "-frames:v", "1", *codec, str(tmp_path / name))
        assert imagesize.image_size(tmp_path / name) == size, label
    assert {(tmp_path / n).read_bytes()[12:16] for n in ("a.webp", "b.webp", "c.webp")} == {b"VP8 ", b"VP8L", b"VP8X"}

    # an EXIF-sized APP1 segment before the frame header is walked past
    jpeg = (tmp_path / "a.jpg").read_bytes()
    exif = tmp_path / "exif.jpg"
    exif.write_bytes(jpeg[:2] + b"\xff\xe1" + (60002).to_bytes(2, "big") + b"\x00" * 60000 + jpeg[2:])
    assert imagesize.image_size(exif) == (123, 457)

    # anything else keeps today's argv: None, never an error
    (tmp_path / "fake.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)  # the fake adapters' PNG
    (tmp_path / "cut.jpg").write_bytes(jpeg[:40])
    (tmp_path / "text.png").write_text("not an image")
    (tmp_path / "empty.webp").write_bytes(b"")
    for name in ("fake.png", "cut.jpg", "text.png", "empty.webp", "absent.png"):
        assert imagesize.image_size(tmp_path / name) is None, name
