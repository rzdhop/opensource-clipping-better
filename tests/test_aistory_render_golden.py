"""The golden render (spec 13; plan phase 4 stage 7, "Golden render and
parity"; DEC-156).

A real render, with the real runner and the ffmpeg on this machine, of the
tiny fixture in ``clipping/aistory/render/golden.py`` (3 stdlib-written
shots, 3 line blips, a looped bed, one shipped SFX, the shipped font, a cut,
a dissolve and a fade to black into the end card, ``word_pop`` and the AI
label, GOLDEN profile). It checks the output's duration, geometry and frame
rate, the manifest, and the video's framemd5 against the digest recorded for
``"<ffmpeg version>/<machine>"`` in ``tests/fixtures/aistory_golden/
framemd5.json``.

**This test never skips.** Without ffmpeg it fails, saying so: CI installs
ffmpeg for it (``.github/workflows/ci.yml``). An ffmpeg or a machine with no
recorded digest fails too, printing the key, the digest and the command that
records it (``python3 tools/render_golden.py --record``) -- after the frames
have been looked at.

Stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from clipping.aistory import schemas
from clipping.aistory.render import golden
from clipping.aistory.render import timeline as rt


def _require_ffmpeg():
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        pytest.fail(f"{' and '.join(missing)} not found on PATH. The golden render never skips (DEC-156): "
                    f"install ffmpeg (CI must install it: apt-get install ffmpeg).")


def _completed(result):
    if result["state"] != "completed":
        manifest = result.get("manifest") or {}
        tail = next((s["stderr_tail"] for s in reversed(manifest.get("stages", [])) if s.get("stderr_tail")), "")
        pytest.fail(f"the golden render did not complete: {result['error']}\n{tail}")
    return result


@pytest.fixture(scope="module")
def first_render(tmp_path_factory):
    _require_ffmpeg()
    workdir = tmp_path_factory.mktemp("golden")
    return workdir, _completed(golden.render_fixture(workdir))


def test_the_parity_check_fails_an_unknown_key_loudly():
    problem = golden.parity_problem("9.9.9/riscv64", "d" * 64, {"6.1.1-3ubuntu5/aarch64": "a" * 64})
    assert "'9.9.9/riscv64'" in problem and "d" * 64 in problem
    assert "python3 tools/render_golden.py --record" in problem
    changed = golden.parity_problem("6.1.1-3ubuntu5/aarch64", "d" * 64, {"6.1.1-3ubuntu5/aarch64": "a" * 64})
    assert "changed" in changed and "d" * 64 in changed and "a" * 64 in changed
    assert golden.parity_problem("k", "a" * 64, {"k": "a" * 64}) is None


def test_a_parity_problem_becomes_a_github_annotation_on_one_line():
    line = golden.github_annotation("key 'x/y': 100% changed\nsecond line")
    assert line.startswith("::error title=AI-Story golden render::")
    assert "\n" not in line and "100%25 changed%0Asecond line" in line


def test_the_recorded_keys_are_well_formed():
    keys = golden.load_keys()
    assert keys, "at least this repository's host key is recorded"
    for key, digest in keys.items():
        version, _, machine = key.rpartition("/")
        assert version and machine and "/" not in machine, key
        assert len(digest) == 64 and int(digest, 16) >= 0, key


def test_the_golden_render_matches_its_recorded_frames(first_render):
    workdir, result = first_render
    doc = json.loads((workdir / "render_manifest.json").read_text(encoding="utf-8"))
    assert schemas.render_manifest_errors(doc) == []
    assert doc["profile"] == "golden" and doc["params"] == {"subtitles": "word_pop", "encoder": "libx264"}

    docs = golden.build_documents()
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], golden.STORY["language"],
                                 style_lock=docs["style_lock"])
    output = doc["output"]
    assert abs(output["duration_s"] - timeline["total_s"]) <= 0.1, (output["duration_s"], timeline["total_s"])
    assert (output["width"], output["height"], output["fps"]) == (1080, 1920, "30/1")
    assert all(isinstance(v, float) for v in output["loudness"].values())

    ids = [stage["id"] for stage in doc["stages"]]
    assert ids == ["S:sh01", "S:sh02", "S:sh03", "E", "A", "L1", "F", "L2", "P", "P:loudness", "M"]
    for stage in doc["stages"]:
        assert stage["state"] in ("done", "cached"), stage
        assert stage["argv"] and stage["output"] and len(stage["output_sha256"]) == 64, stage

    framemd5 = workdir / output["framemd5"]["file"]
    frames = [line for line in framemd5.read_text().splitlines() if line and not line.startswith("#")]
    assert len(frames) == timeline["total_frames"]
    assert (workdir / output["path"]).is_file()

    digest = output["framemd5"]["sha256"]
    problem = golden.parity_problem(golden.parity_key(doc["ffmpeg"]), digest, golden.load_keys())
    if problem:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(golden.github_annotation(problem))
        pytest.fail(problem)


def test_one_changed_shot_image_reruns_only_that_shot(first_render, tmp_path):
    workdir, first = first_render
    copy = tmp_path / "rerender"
    shutil.copytree(workdir, copy, symlinks=True)
    result = _completed(golden.render_fixture(copy, variant_shot="sh02"))

    states = {stage["id"]: stage["state"] for stage in result["manifest"]["stages"]}
    assert [sid for sid, state in states.items() if sid.startswith("S:") and state == "done"] == ["S:sh02"]
    assert result["cached"] == ["S:sh01", "S:sh03", "E"]
    assert result["ran"] == ["S:sh02", "A", "L1", "F", "L2", "P", "P:loudness", "M"]
    assert result["output"]["framemd5"]["sha256"] != first["output"]["framemd5"]["sha256"]
