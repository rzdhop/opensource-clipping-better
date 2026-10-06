"""``aistory brief`` and ``aistory upload-clip`` (plan 22 stage 5, the manual
link): the shot brief printed or zipped for the human's terminal, and their
own clip taken through the upload route's own checks.

Driven through ``clipping.aistory.cli.main(argv)`` with
``tests/test_story_cli_phase4.py``'s ``cli`` fixture and stage 8's
``hermetic`` one; real ffmpeg on a tiny clip. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import os
import zipfile

import test_story_assets_step as tas
import test_story_manual_link as tml
import test_story_native_speech_plan as nsp
import test_story_native_take as tnt
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures
from test_story_cli_phase4 import cli  # noqa: F401 -- the CLI against the store's outputs


def test_brief_prints_the_markdown_and_writes_the_zip(cli, tmp_path):
    story_id = nsp.planned_story(cli.store, profile="native_speech_manual")
    cli.capsys.readouterr()
    assert cli.run("brief", story_id, "1") == 0
    out = cli.capsys.readouterr().out
    assert out.startswith("# Shot brief — episode 1") and "Google Flow (Veo 3.1)" in out and "```text" in out
    target = tmp_path / "brief.zip"
    assert cli.run("brief", story_id, "1", "--platform", "higgsfield", "--zip", str(target)) == 0
    assert "📦 Shot brief written to" in cli.capsys.readouterr().out
    assert {"shot_brief.md", "shot_brief.json"} <= set(zipfile.ZipFile(target).namelist())
    assert cli.run("brief", story_id, "1", "--platform", "sora") == 2


def test_upload_clip_takes_the_file_through_the_routes_checks(cli, tmp_path):
    tnt._require_ffmpeg()
    story_id = nsp.planned_story(cli.store, profile="native_speech_manual")
    shot = next(item for item in tas._board(cli.store, story_id)["shots"] if not item.get("speaks"))
    tml.own_keyframes(cli.store, story_id, [shot["shot_id"]])  # plan 28 F7, re-pinned on purpose
    clip = tnt.make_clip(tmp_path / "mine.mp4", 6)
    cli.capsys.readouterr()

    assert cli.run("upload-clip", story_id, "1", shot["shot_id"], clip) == 0

    out = cli.capsys.readouterr().out
    assert f"✅ Shot {shot['shot_id']}: uploaded" in out and "Waiting for" in out
    stored = next(item for item in tas._board(cli.store, story_id)["shots"] if item["shot_id"] == shot["shot_id"])
    assert stored["assets"]["clip"]["link"] == "manual/upload" and os.path.isfile(clip)  # copied, never moved
    wide = tnt.make_clip(tmp_path / "wide.mp4", 4)  # 64x112 is 9:16; make one that is not
    import subprocess

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=24:duration=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", wide], check=True)
    assert cli.run("upload-clip", story_id, "1", shot["shot_id"], wide) == 1
    assert "Refused: The clip is 160x90 (16:9), not 9:16" in cli.capsys.readouterr().err
