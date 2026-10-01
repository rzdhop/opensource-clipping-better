"""The tier-2 golden render (AI Story phase 6, stage 9; spec 13, 6.5; DEC-156,
DEC-158; RC-M2, RC-M8).

A real render, with the real runner and the ffmpeg on this machine, of the
tiny fixture in ``clipping/aistory/render/golden_tier2.py``: ``sh01`` cut
from a 1.0 s clip ffmpeg makes from ``lavfi`` for a 1.5 s shot, ``sh02`` a
still, a cut, ``fadeblack`` into the end card, GOLDEN profile. It checks:

- the video's framemd5 against the digest recorded for ``"<ffmpeg
  version>/<machine>"`` in ``tests/fixtures/aistory_golden_tier2/
  framemd5.json``, exactly as the tier-1 golden does (its own keys file and
  fixture stay as they are);
- the hold: the clip shorter than its shot is held on its last frame to the
  shot's exact frame count;
- partial == full (RC-M8): the clip changed, a re-render over the warm cache
  makes that shot alone again, and its frames are those of a clean render of
  the same documents.

**It never skips**, like the tier-1 golden: without ffmpeg it fails, saying
so (CI installs ffmpeg); an ffmpeg or a machine with no recorded digest fails
too, printing the key, the digest and the command that records it (``python3
tools/render_golden.py --tier2 --record``) -- after the frames have been
looked at. On GitHub Actions the problem is also printed as an annotation, so
CI's key can be read and recorded from the machine that pushed.

Stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from clipping.aistory import schemas
from clipping.aistory.render import golden
from clipping.aistory.render import timeline as rt


def _module():
    from clipping.aistory.render import golden_tier2

    return golden_tier2


def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH. The tier-2 golden render never skips (DEC-156): "
                    f"install ffmpeg (CI must install it: apt-get install ffmpeg).")


def _completed(result):
    if result["state"] != "completed":
        manifest = result.get("manifest") or {}
        tail = next((s["stderr_tail"] for s in reversed(manifest.get("stages", [])) if s.get("stderr_tail")), "")
        pytest.fail(f"the tier-2 golden render did not complete: {result['error']}\n{tail}")
    return result


def _frames(path) -> list:
    """The video frames' md5 lines of *path* (ffmpeg's framemd5)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", os.fspath(path), "-map", "0:v", "-f",
                          "framemd5", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True)
    return [line for line in out.stdout.splitlines() if line and not line.startswith("#")]


def _timeline():
    docs = _module().build_documents()
    return rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], _module().STORY["language"],
                             style_lock=docs["style_lock"])


@pytest.fixture(scope="module")
def first_render(tmp_path_factory):
    _require_ffmpeg()
    workdir = tmp_path_factory.mktemp("golden_tier2")
    return workdir, _completed(_module().render_fixture(workdir))


def test_the_tier_2_golden_render_matches_its_recorded_frames(first_render):
    workdir, _result = first_render
    doc = json.loads((workdir / "render_manifest.json").read_text(encoding="utf-8"))
    assert schemas.render_manifest_errors(doc) == []
    assert doc["profile"] == "golden" and doc["params"] == {"subtitles": "word_pop", "encoder": "libx264"}
    assert doc["shot_modes"] == {"sh01": "video", "sh02": "motion"}
    clip = next(item for item in doc["inputs"] if item["id"] == "sh01")
    assert (clip["role"], clip["source"], clip["staged"][-4:]) == ("shot", "fixture/clips/shot_01.mp4", ".mp4")

    timeline = _timeline()
    output = doc["output"]
    assert abs(output["duration_s"] - timeline["total_s"]) <= 0.1, (output["duration_s"], timeline["total_s"])
    assert (output["width"], output["height"], output["fps"]) == (1080, 1920, "30/1")
    assert [stage["id"] for stage in doc["stages"]] == ["S:sh01", "S:sh02", "E", "A", "L1", "F", "L2", "P",
                                                        "P:loudness", "M"]
    for stage in doc["stages"]:
        assert stage["state"] in ("done", "cached"), stage
    frames = (workdir / output["framemd5"]["file"]).read_text().splitlines()
    assert len([line for line in frames if line and not line.startswith("#")]) == timeline["total_frames"]

    digest = output["framemd5"]["sha256"]
    problem = _module().parity_problem(golden.parity_key(doc["ffmpeg"]), digest, _module().load_keys())
    if problem:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(golden.github_annotation(problem))
        pytest.fail(problem)


def test_a_clip_shorter_than_its_shot_is_held_on_its_last_frame_to_the_shots_frame_count(first_render):
    """1.0 s of clip (24 frames at 24 fps, 30 once at 30 fps) for a 1.5 s
    shot: its S stage makes exactly the shot's 45 frames."""
    workdir, result = first_render
    shot = next(item for item in _timeline()["shots"] if item["shot_id"] == "sh01")
    assert (shot["duration_s"], shot["frames"]) == (1.5, 45)
    stage = next(item for item in result["manifest"]["stages"] if item["id"] == "S:sh01")
    assert "-loop" not in stage["argv"] and "tpad=stop_mode=clone:stop_duration=1.5" in " ".join(stage["argv"])
    assert len(_frames(workdir / "render" / stage["output"])) == shot["frames"]


def test_one_changed_clip_re_renders_only_its_shot_and_equals_a_full_render(first_render, tmp_path):
    """RC-M8: ``sh01``'s clip made again from another source; over the warm
    cache only ``S:sh01`` and the assembly run, and the frames are exactly
    those of a clean full render of the same documents."""
    workdir, first = first_render
    warm = tmp_path / "warm"
    shutil.copytree(workdir, warm, symlinks=True)

    partial = _completed(_module().render_fixture(warm, variant_clip=True))
    full = _completed(_module().render_fixture(tmp_path / "clean", variant_clip=True))

    assert partial["ran"] == ["S:sh01", "A", "L1", "F", "L2", "P", "P:loudness", "M"]
    assert partial["cached"] == ["S:sh02", "E"] and full["cached"] == []
    assert partial["manifest"]["reuse"] == {
        "baseline_output_sha256": first["output"]["sha256"], "shots_total": 2, "shots_rebuilt": ["sh01"],
        "shots_reused": ["sh02"], "reasons": {"sh01": "image"}, "timing_converted": False}
    assert partial["output"]["framemd5"]["sha256"] == full["output"]["framemd5"]["sha256"]
    assert partial["output"]["framemd5"]["sha256"] != first["output"]["framemd5"]["sha256"]


# ------------------------------------------------- framing (T2-P6-F1)

def _cut_frame(tmp_path, size) -> tuple:
    """A clip of *size* -- red top quarter, blue bottom quarter, grey between,
    a white square at its centre a third of its width -- cut by
    ``tier2_clip_argv`` (GOLDEN profile), and the cut's first frame decoded:
    ``(width, height, rgb24 bytes)``."""
    from clipping.aistory.render import filtergraph, profiles

    _require_ffmpeg()
    width, height = size
    box = width // 3
    source = (f"color=c=gray:s={width}x{height}:r=24:d=0.5,"
              f"drawbox=x=0:y=0:w={width}:h={height // 4}:color=red:t=fill,"
              f"drawbox=x=0:y={height - height // 4}:w={width}:h={height // 4}:color=blue:t=fill,"
              f"drawbox=x={(width - box) // 2}:y={(height - box) // 2}:w={box}:h={box}:color=white:t=fill")
    subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "lavfi", "-i", source]
                   + profiles.GOLDEN.video_encode_args() + ["-an", "clip.mp4"],
                   cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL, check=True)
    argv = filtergraph.tier2_clip_argv("clip.mp4", {"duration_s": 0.2, "frames": 6}, profiles.GOLDEN, "cut.mp4")
    subprocess.run(argv, cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL, check=True)
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", "cut.mp4", "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], cwd=tmp_path, capture_output=True, stdin=subprocess.DEVNULL,
                         check=True).stdout
    return 1080, 1920, raw


def _pixel(frame, x, y) -> tuple:
    width, _height, raw = frame
    at = (y * width + x) * 3
    return tuple(raw[at:at + 3])


def _black(rgb) -> bool:
    return max(rgb) < 40


def test_a_square_clip_fills_the_frame_cropped_around_its_centre_never_letterboxed(tmp_path):
    """T2-P6-F1: a 960x960 Kling clip came out as a square between black
    bars. A clip that is not 9:16 covers the frame and is centre-cropped,
    as the cover frames a shot's image (``filtergraph.cover_argv``): its
    top and bottom rows are the clip's own (red, blue -- not black), and
    its centre square stays a square at the frame's centre (cropped, not
    stretched)."""
    frame = _cut_frame(tmp_path, (96, 96))
    width, height, _raw = frame

    top = [_pixel(frame, x, 0) for x in range(0, width, 60)]
    bottom = [_pixel(frame, x, height - 1) for x in range(0, width, 60)]
    assert not any(_black(rgb) for rgb in top + bottom), (top, bottom)
    assert all(r > 150 and b < 90 for r, _g, b in top) and all(b > 150 and r < 90 for r, _g, b in bottom)

    def white(x, y):
        return min(_pixel(frame, x, y)) > 200

    across = [x for x in range(width) if white(x, height // 2)]
    down = [y for y in range(height) if white(width // 2, y)]
    assert white(width // 2, height // 2)
    assert abs((across[0] + across[-1]) / 2 - width / 2) <= 4 and abs((down[0] + down[-1]) / 2 - height / 2) <= 4
    assert abs(len(across) - len(down)) <= 8, (len(across), len(down))


def test_guard_a_9_16_clip_fills_the_frame_edge_to_edge(tmp_path):
    """A clip already 9:16 fills the frame as it did: no edge row or column
    is black."""
    frame = _cut_frame(tmp_path, (72, 128))
    width, height, _raw = frame
    edges = ([_pixel(frame, x, y) for x in range(0, width, 60) for y in (0, height - 1)]
             + [_pixel(frame, x, y) for y in range(0, height, 60) for x in (0, width - 1)])
    assert not any(_black(rgb) for rgb in edges), edges
