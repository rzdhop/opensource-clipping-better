"""The story skills (plan 36 stage 3): every step has its skill, every skill has its sections and the shared
rules, names only tools the showrunner server has, and points at files that exist. Stdlib only."""

from __future__ import annotations

import os
import re
import sys
import zipfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "showrunner", "tools"))

import build_skills as B  # noqa: E402

SECTIONS = ("## When to use", "## Read first", "## Template", "## Checklist", "## Gate question",
            "## What to show Rida", "## Tools", "## Next")
# Tools of the deleted rzdhop-story server and paths plan 36 forbids: a skill naming one is a dead skill.
DEAD = ("story_step_start", "story_make_episode", "story_estimate", "story_options", "tts_line", "tts_batch",
        "voice_ref_make", "comfy_download", "comfy_status", "file_upload", "rzdhop-story", "s2v_wan22",
        "i2v_wan22", "Ken Burns effect")
PAID = {"comfy_submit", "vc_clip"}


def server_tools() -> set:
    """The tools of showrunner/mcp_server.py, read from its source (no fastmcp needed)."""
    with open(os.path.join(ROOT, "showrunner", "mcp_server.py"), encoding="utf-8") as fh:
        src = fh.read()
    return set(re.findall(r"@mcp\.tool\s+def (\w+)\(", src))


def text(name: str) -> str:
    with open(B.skill_path(name), encoding="utf-8") as fh:
        return fh.read()


def section(body: str, heading: str) -> str:
    after = body.split(heading + "\n", 1)[1]
    return after.split("\n## ", 1)[0]


def test_the_server_has_the_stage_three_tools():
    tools = server_tools()
    assert len(tools) == 24
    assert {"prompt_keyframe", "prompt_clip", "prompt_cast", "voice_ref_from_take"} <= tools


def test_one_skill_per_step_in_the_plans_order():
    assert B.ORDER == ("story-concepts", "story-universe", "story-cast", "story-script", "story-shots",
                       "story-clips", "story-assemble", "story-next-episode")
    on_disk = sorted(d for d in os.listdir(B.SKILLS_DIR) if d.startswith("story-"))
    assert on_disk == sorted(B.ORDER)


@pytest.mark.parametrize("name", B.ORDER)
def test_each_skill_has_its_frontmatter_sections_and_the_shared_rules(name):
    body = text(name)
    front = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", body)
    assert front and front.group(1) == name
    assert 40 < len(front.group(2)) <= 1024 and "<" not in front.group(2)
    positions = [body.index(h + "\n") for h in SECTIONS]
    assert positions == sorted(positions), "sections out of order"
    assert B.with_rules(body, B.rules()) == body, "rules block stale: run showrunner/tools/build_skills.py"
    for dead in DEAD:
        assert dead not in body, dead


@pytest.mark.parametrize("name", B.ORDER)
def test_each_skill_names_only_real_tools_and_flags_the_paid_ones(name):
    body = text(name)
    tools = server_tools()
    listed = re.findall(r"^- ((?:`\w+`(?:, )?)+) — (free|COSTS MONEY)", section(body, "## Tools"), re.M)
    assert listed, "the Tools section lists the tools as '- `name` — free|COSTS MONEY: …'"
    for names, price in listed:
        for tool in re.findall(r"`(\w+)`", names):
            assert tool in tools, f"{tool} is not a showrunner tool"
            assert (price == "COSTS MONEY") == (tool in PAID), f"{tool} is {price}"
    called = set(re.findall(r"`(\w+)\(", body))
    assert called <= tools, called - tools
    pays = any(price == "COSTS MONEY" for _, price in listed)
    description = body.split("---", 2)[1]
    assert ("Costs money" in description) == pays, "a skill's description says whether it spends money"
    assert pays or "Free." in description


@pytest.mark.parametrize("k", range(len(B.ORDER) - 1))
def test_each_skill_hands_over_to_the_next_step(k):
    assert f"`{B.ORDER[k + 1]}`" in section(text(B.ORDER[k]), "## Next")


@pytest.mark.parametrize("name", B.ORDER)
def test_the_files_a_skill_points_at_exist(name):
    body = text(name)
    for path in re.findall(r"`((?:showrunner|assets|stories)/[\w./-]+?\.(?:md|mp3|wav|json))`", body):
        if "<" in path:
            continue
        assert os.path.exists(os.path.join(ROOT, path)), path
    for pack in re.findall(r"`(\w+)/` \(", body):          # the sfx and bgm folder lists
        assert os.path.isdir(os.path.join(ROOT, "assets", "sfx", pack)) or \
            os.path.isdir(os.path.join(ROOT, "assets", "bgm", pack)), pack


def test_the_rules_say_the_non_negotiables():
    r = B.rules()
    for must in ("explicit go", "never a still", "Ken Burns", "edge-tts", "LLM API", "prompt_from", "70 words",
                 "Keyframes face the camera", "voice_ref_from_take"):
        assert must in r, must


def test_the_zips_hold_one_skill_each(tmp_path):
    made = B.zip_all(str(tmp_path))
    assert len(made) == len(B.ORDER)
    with zipfile.ZipFile(made[0]) as zf:
        assert zf.namelist() == [f"{B.ORDER[0]}/SKILL.md"]
