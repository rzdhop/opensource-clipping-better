"""The rzdhop-story skill (plan 36): one folder — SKILL.md (rules, gates, routing), steps/*.md, PROMPTS.md.
Every step has its sections, names only tools the showrunner server has, hands over to the next step, and the prompt
guide keeps the wording Rida chose word for word. Stdlib only."""

from __future__ import annotations

import json
import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "showrunner", "tools"))

import build_skills as B  # noqa: E402

SECTIONS = ("## When to use", "## Read first", "## Template", "## Checklist", "## Gate question",
            "## What to show Rida", "## Tools", "## Next")
PAID = {"comfy_submit", "vc_clip"}
# Rida, 2026-10-09: Claude proposes and Rida corrects in the writing steps; the production runs without questions.
PROPOSE_STEPS = ("1-concepts", "2-universe", "3-cast", "4-script", "7-next-episode")
RUN_STEPS = ("3-cast", "5a-shots", "5b-clips", "6-assemble")
# Tools of the deleted rzdhop-story server and the old pipeline: a skill naming one is a dead skill.
DEAD = ("story_step_start", "story_make_episode", "story_estimate", "story_options", "tts_line", "tts_batch",
        "voice_ref_make", "comfy_download", "comfy_status", "file_upload", "s2v_wan22", "i2v_wan22", "prompt_from",
        "prompt_clip", "prompt_keyframe", "prompt_cast")
GOLDEN = os.path.join(ROOT, "docs", "plans", "36-stage0-batch-a-prompts.json")
CLEAN_FRAME = "One continuous, clean cinematic shot from the first frame to the last."
# The fixed sentences of the prompts Rida chose (takes s33 and s22): the guide must keep them word for word.
FIXED = (
    "Use the provided start image as the first frame.",
    "talks to someone just off-screen beside the camera, in three-quarter view, the eyeline passing just past the "
    "lens and never looking into it, as in a conversation scene of a drama, and says in",
    "The mouth moves naturally with every word, a small head tilt, a breath before and a beat of silence after the line.",
    "Medium close-up, the camera holds still on the speaker, soft natural motion only.",
    "Audio: the clear voice close to the microphone, quiet room tone, no music.",
    "They speak in turn, each one's mouth moving only on their own line, the other listening and reacting:",
    "Medium two-shot, the camera holds still.",
    "Audio: two distinct voices close to the microphone, quiet room tone, no music.",
)


def server_tools() -> set:
    """The tools of showrunner/mcp_server.py, read from its source (no fastmcp needed)."""
    with open(os.path.join(ROOT, "showrunner", "mcp_server.py"), encoding="utf-8") as fh:
        return set(re.findall(r"@mcp\.tool\s+def (\w+)\(", fh.read()))


def step(name: str) -> str:
    return B.read(B.step_file(name))


def section(body: str, heading: str) -> str:
    return body.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_one_skill_one_folder_and_nothing_missing():
    assert os.listdir(os.path.dirname(B.SKILL_DIR)) == ["rzdhop-story"]
    assert B.problems() == []
    body = B.read("SKILL.md")
    front = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", body)
    assert front and front.group(1) == "rzdhop-story"
    assert 40 < len(front.group(2)) <= 1024 and "<" not in front.group(2)
    for s in B.STEPS:
        assert f"`{B.step_file(s)}`" in body, s
    assert "read the step's file before doing the step" in body and "`PROMPTS.md`" in body


def test_the_server_makes_and_checks_but_writes_no_prompt():
    """Claude writes every prompt; the server only makes pictures, clips and sound, checks and cuts."""
    tools = server_tools()
    assert len(tools) == 21 and "voice_ref_from_take" in tools
    assert not [t for t in tools if t.startswith("prompt")]
    with open(os.path.join(ROOT, "showrunner", "mcp_server.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert '"rzdhop-story"' in src and "story-director" not in src


def test_the_rules_say_the_non_negotiables():
    rules = B.read("SKILL.md")
    for must in ("Propose, Rida corrects", "Rida's gates", "twice that estimate", "Claude's pick", "never a still",
                 "Ken Burns", "edge-tts", "LLM API", "You write every prompt", "values.prompt", "70 words",
                 "Keyframes face the camera", "voice_ref_from_take", "whole episode", "next episode"):
        assert must in rules, must


@pytest.mark.parametrize("name", B.STEPS)
def test_each_step_has_its_sections_in_order(name):
    body = step(name)
    positions = [body.index(h + "\n") for h in SECTIONS]
    assert positions == sorted(positions), "sections out of order"
    assert ("## Propose\n" in body) == (name in PROPOSE_STEPS)
    assert ("## Run (no questions)\n" in body) == (name in RUN_STEPS)
    if name in PROPOSE_STEPS:
        assert "No questions first" in section(body, "## Propose")
    assert "## Ask first" not in body
    for dead in DEAD:
        assert dead not in body, dead
    for internal in (r"stage[- ]0", r"\bbatch a\b", r"\bgolden\b", r"\bDEC-\d", r"\bA-\d{3}\b"):
        assert not re.search(internal, body), internal


@pytest.mark.parametrize("name", B.STEPS)
def test_each_step_names_only_real_tools_and_flags_the_paid_ones(name):
    body = step(name)
    tools = server_tools()
    listed = re.findall(r"^- ((?:`\w+`(?:, )?)+) — (free|COSTS MONEY)", section(body, "## Tools"), re.M)
    assert listed, "the Tools section lists the tools as '- `name` — free|COSTS MONEY: …'"
    for names, price in listed:
        for tool in re.findall(r"`(\w+)`", names):
            assert tool in tools, f"{tool} is not a showrunner tool"
            assert (price == "COSTS MONEY") == (tool in PAID), f"{tool} is {price}"
    assert set(re.findall(r"`(\w+)\(", body)) <= tools


@pytest.mark.parametrize("k", range(len(B.STEPS) - 1))
def test_each_step_hands_over_to_the_next(k):
    assert f"`{B.step_file(B.STEPS[k + 1])}`" in section(step(B.STEPS[k]), "## Next")


@pytest.mark.parametrize("name", B.STEPS)
def test_the_files_a_step_points_at_exist(name):
    body = step(name)
    for rel in re.findall(r"`((?:showrunner|assets|stories|docs)/[\w./-]+?\.(?:md|mp3|wav|json))`", body):
        if "<" not in rel:
            assert os.path.exists(os.path.join(ROOT, rel)), rel
    for pack in re.findall(r"`(\w+)/` \(", body):
        assert os.path.isdir(os.path.join(ROOT, "assets", "sfx", pack)) or \
            os.path.isdir(os.path.join(ROOT, "assets", "bgm", pack)), pack


@pytest.mark.parametrize("name", ("5a-shots", "5b-clips"))
def test_the_production_steps_do_not_stop_for_rida(name):
    assert section(step(name), "## Gate question").strip().startswith("None")


def test_the_episode_review_then_the_next_episode_question():
    body = step("6-assemble")
    assert "Good to post" in section(body, "## Gate question") and "episode N+1" in section(body, "## Gate question")
    show = section(body, "## What to show Rida")
    assert "final.mp4" in show and "locked clips" in show


@pytest.mark.parametrize("name", ("3-cast", "5a-shots", "5b-clips"))
def test_the_steps_that_send_prompts_point_at_the_guide(name):
    assert "read `PROMPTS.md`" in step(name)


def test_the_prompt_guide_keeps_the_wording_rida_chose():
    """Each fixed sentence of the prompts Rida preferred (recorded as sent) is in the guide, word for word, with the
    clean closing sentence instead of the one that named what is unwanted."""
    with open(GOLDEN, encoding="utf-8") as fh:
        golden = _flat(" ".join(p["prompt"] for p in json.load(fh)["prompts"].values()))
    guide = _flat(B.read("PROMPTS.md"))
    for sentence in FIXED:
        assert sentence in golden, sentence
        assert sentence in guide, sentence
    assert CLEAN_FRAME in guide
    for unwanted in ("no subtitles", "no on-screen text", "no black frames"):
        assert unwanted not in guide.lower()
    assert "head turned three-quarters to the right, eyes looking past the right edge of the frame" in guide


def test_the_zip_holds_the_whole_folder(tmp_path):
    dest = B.zip_skill(str(tmp_path))
    with zipfile.ZipFile(dest) as zf:
        names = sorted(zf.namelist())
    assert names == sorted(f"rzdhop-story/{rel}" for rel in B.files())
    assert "rzdhop-story/SKILL.md" in names and "rzdhop-story/steps/3-cast.md" in names
