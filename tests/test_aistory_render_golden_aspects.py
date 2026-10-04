"""The golden render in the other frames (plan 23 stage B6; spec 13; DEC-156).

``clipping/aistory/render/golden.py``'s fixture rendered at 16:9 (192x108
shot images, a 1920x1080 episode) and 1:1 (108x108, 1080x1080) with the real
runner and the ffmpeg on this machine. Each aspect has its own keys file,
``tests/fixtures/aistory_golden/framemd5_16x9.json`` and
``framemd5_1x1.json``, keyed ``"<ffmpeg version>/<machine>"`` like the 9:16
``framemd5.json`` (which this test never reads or moves).

**An unknown build skips, it does not fail** -- unlike the 9:16 golden test.
These frames are not shipped to anyone yet (B7 brings the aspect to a
story), and CI's x86_64 key can only be read from CI itself: on GitHub
Actions the test prints the same ``::error`` annotation the 9:16 test prints
(the key and the digest, public through the check-runs API; written past
pytest's output capture, which hides a skipped test's output), then skips, so
the push that brings a new build is not blocked; the key is recorded after
it (``python3 tools/render_golden.py --record --aspect <aspect>``). A known
key whose digest differs fails. A missing ffmpeg fails too (DEC-156).

Stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from clipping.aistory import schemas
from clipping.aistory.render import golden
from clipping.aistory.render import timeline as rt

ASPECTS = {"16:9": (1920, 1080), "1:1": (1080, 1080)}


def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH. The golden render never skips for a missing "
                    f"ffmpeg (DEC-156): install ffmpeg (CI must install it: apt-get install ffmpeg).")


@pytest.fixture(scope="module", params=sorted(ASPECTS), ids=lambda a: a.replace(":", "x"))
def aspect_render(request, tmp_path_factory):
    _require_ffmpeg()
    aspect = request.param
    workdir = tmp_path_factory.mktemp(f"golden_{aspect.replace(':', 'x')}")
    result = golden.render_fixture(workdir, aspect=aspect)
    if result["state"] != "completed":
        manifest = result.get("manifest") or {}
        tail = next((s["stderr_tail"] for s in reversed(manifest.get("stages", [])) if s.get("stderr_tail")), "")
        pytest.fail(f"the {aspect} golden render did not complete: {result['error']}\n{tail}")
    return aspect, workdir, result


# ------------------------------------------------------------------ pure

def test_each_aspect_has_its_own_keys_file_and_record_command():
    assert golden.keys_path() == golden.keys_path("9:16") == golden.KEYS_PATH
    assert golden.keys_path("16:9").name == "framemd5_16x9.json"
    assert golden.keys_path("1:1").name == "framemd5_1x1.json"
    assert golden.keys_path("16:9").parent == golden.keys_path("1:1").parent == golden.KEYS_PATH.parent
    assert golden.record_command() == golden.RECORD_COMMAND == "python3 tools/render_golden.py --record"
    assert golden.record_command("16:9") == "python3 tools/render_golden.py --record --aspect 16:9"
    assert golden.IMAGE_SIZE == golden.IMAGE_SIZES["9:16"] == (108, 192)
    assert (golden.IMAGE_SIZES["16:9"], golden.IMAGE_SIZES["1:1"]) == ((192, 108), (108, 108))


@pytest.mark.parametrize("aspect", sorted(ASPECTS))
def test_the_recorded_aspect_keys_are_well_formed(aspect):
    keys = golden.load_keys(golden.keys_path(aspect))
    assert keys, f"at least this repository's host key is recorded for {aspect}"
    for key, digest in keys.items():
        version, _, machine = key.rpartition("/")
        assert version and machine and "/" not in machine, key
        assert len(digest) == 64 and int(digest, 16) >= 0, key


def test_the_annotation_names_the_aspect_with_its_property_escaped():
    line = golden.github_annotation("new key", title="AI-Story golden render 16:9")
    assert line == "::error title=AI-Story golden render 16%3A9::new key"
    assert golden.github_annotation("x") == "::error title=AI-Story golden render::x"


def test_the_portrait_picture_is_the_one_it_always_was():
    for index in range(3):
        for variant in (0, 1):
            default = golden._shot_pixel(index, variant)
            sized = golden._shot_pixel(index, variant, (108, 192))
            assert all(default(x, y) == sized(x, y) for x in range(0, 108, 3) for y in range(0, 192, 3))


def test_an_unknown_aspect_is_refused(tmp_path):
    with pytest.raises(ValueError, match="aspect"):
        golden.render_fixture(tmp_path, aspect="4:3")


def test_the_tool_refuses_an_aspect_for_the_tier2_fixture():
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "render_golden.py")
    done = subprocess.run([sys.executable, tool, "--tier2", "--aspect", "16:9"], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert done.returncode == 2 and "9:16 only" in done.stderr


# ------------------------------------------------------------------ renders

def test_the_aspect_render_matches_its_recorded_frames(aspect_render, capsys):
    aspect, workdir, _result = aspect_render
    doc = json.loads((workdir / "render_manifest.json").read_text(encoding="utf-8"))
    assert schemas.render_manifest_errors(doc) == []
    assert doc["profile"] == "golden"
    assert doc["params"] == {"subtitles": "word_pop", "encoder": "libx264", "aspect": aspect}

    docs = golden.build_documents()
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], golden.STORY["language"],
                                 style_lock=docs["style_lock"])
    output = doc["output"]
    assert abs(output["duration_s"] - timeline["total_s"]) <= 0.1, (output["duration_s"], timeline["total_s"])
    assert (output["width"], output["height"], output["fps"]) == (*ASPECTS[aspect], "30/1")

    ids = [stage["id"] for stage in doc["stages"]]
    assert ids == ["S:sh01", "S:sh02", "S:sh03", "E", "A", "L1", "F", "L2", "P", "P:loudness", "M"]
    for stage in doc["stages"]:
        assert stage["state"] in ("done", "cached"), stage

    framemd5 = workdir / output["framemd5"]["file"]
    frames = [line for line in framemd5.read_text().splitlines() if line and not line.startswith("#")]
    assert len(frames) == timeline["total_frames"]

    digest = output["framemd5"]["sha256"]
    key = golden.parity_key(doc["ffmpeg"])
    keys = golden.load_keys(golden.keys_path(aspect))
    problem = golden.parity_problem(key, digest, keys, command=golden.record_command(aspect))
    if problem is None:
        return
    if key not in keys:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # A skipped test's captured output is never shown: write past the capture, on a
            # line of its own (pytest's progress characters share the terminal line).
            with capsys.disabled():
                print("\n" + golden.github_annotation(problem, title=f"AI-Story golden render {aspect}"))
        pytest.skip(f"[{aspect}] {problem}")
    pytest.fail(problem)
