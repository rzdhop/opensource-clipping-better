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


def test_the_server_makes_and_checks_but_writes_no_prompt():
    """DEC-328: Claude writes every prompt; the server only makes pictures, clips and sound, checks and cuts."""
    tools = server_tools()
    assert len(tools) == 21 and "voice_ref_from_take" in tools
    assert not [t for t in tools if t.startswith("prompt")]
    with open(os.path.join(ROOT, "showrunner", "mcp_server.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "showrunner.prompts" not in src and "import prompts" not in src


def test_one_skill_per_step_in_the_plans_order():
    assert B.ORDER == ("story-concepts", "story-universe", "story-cast", "story-script", "story-shots",
                       "story-clips", "story-assemble", "story-next-episode")
    on_disk = sorted(d for d in os.listdir(B.SKILLS_DIR) if d.startswith("story-") or d == "fruit-drama-episode")
    assert on_disk == sorted(B.ALL)


@pytest.mark.parametrize("name", B.ENTRY)
def test_the_entry_points_route_to_every_step_skill_and_ask(name):
    body = text(name)
    front = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", body)
    assert front and front.group(1) == name and len(front.group(2)) <= 1024
    assert B.expected(name, body) == body and "<!-- prompts:start -->" not in body
    assert "Propose, Rida corrects" in body and "## Gate question" in body
    router = text("story-director")
    for step in B.ORDER:
        assert f"`{step}`" in router, step
    assert "load that skill before doing the step" in router
    if name != "story-director":
        assert "load `story-director`" in body
    for dead in DEAD:
        assert dead not in body.replace("`story_step_start`", "").replace("`story_get`", "").replace("`tts_line`", ""), dead
    listed = re.findall(r"^- `(\w+)` — free", section(body, "## Tools"), re.M)
    assert listed and set(listed) <= server_tools()


@pytest.mark.parametrize("name", B.ORDER)
def test_each_skill_has_its_frontmatter_sections_and_the_shared_rules(name):
    body = text(name)
    front = re.match(r"^---\nname: (.+)\ndescription: (.+)\n---\n", body)
    assert front and front.group(1) == name
    assert 40 < len(front.group(2)) <= 1024 and "<" not in front.group(2)
    positions = [body.index(h + "\n") for h in SECTIONS]
    assert positions == sorted(positions), "sections out of order"
    assert B.expected(name, body) == body, "shared block stale: run showrunner/tools/build_skills.py"
    assert ("<!-- prompts:start -->" in body) == (name in B.PROMPT_SKILLS)
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


# Rida, 2026-10-09: Claude proposes and Rida corrects in the writing steps; the production runs without questions
# and Rida reviews the finished cast and the whole episode.
PROPOSE_STEPS = ("story-concepts", "story-universe", "story-cast", "story-script", "story-next-episode")
RUN_STEPS = ("story-cast", "story-shots", "story-clips", "story-assemble")


@pytest.mark.parametrize("name", B.ORDER)
def test_each_step_proposes_or_runs_and_never_quizzes(name):
    body = text(name)
    assert ("## Propose\n" in body) == (name in PROPOSE_STEPS), name
    assert ("## Run (no questions)\n" in body) == (name in RUN_STEPS), name
    if name in PROPOSE_STEPS:
        assert "No questions first" in section(body, "## Propose")
    assert "## Ask first" not in body
    own = body.replace(B.rules(), "").replace(B.prompt_guide(), "")
    for internal in (r"stage[- ]0", r"\bbatch a\b", r"\bgolden\b", r"\bDEC-\d", r"\bA-\d{3}\b"):
        assert not re.search(internal, own), (name, internal)


@pytest.mark.parametrize("name", ("story-shots", "story-clips"))
def test_the_production_steps_do_not_stop_for_rida(name):
    gate = section(text(name), "## Gate question")
    assert gate.strip().startswith("None")


def test_the_episode_review_then_the_next_episode_question():
    gate = section(text("story-assemble"), "## Gate question")
    assert "Good to post" in gate and "episode N+1" in gate
    show = section(text("story-assemble"), "## What to show Rida")
    assert "final.mp4" in show and "locked clips" in show


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
    for must in ("Propose, Rida corrects", "Rida's gates", "twice that estimate", "Claude's pick", "never a still", "Ken Burns", "edge-tts", "LLM API", "You write every prompt",
                 "values.prompt", "70 words", "Keyframes face the camera", "voice_ref_from_take",
                 "whole episode", "next episode"):
        assert must in r, must


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_the_prompt_guide_keeps_the_wording_that_worked():
    """Every fixed sentence of the batch-a templates (prompts/*.md, the golden) is in the guide, word for word:
    the guide is what Claude writes from, so the wording Rida chose cannot drift out of it."""
    from showrunner import prompts as P
    guide = _flat(B.prompt_guide())
    for name in ("clip_dialogue", "clip_exchange", "clip_reaction", "keyframe", "full_body", "turnaround", "emotions",
                 "framing_dialogue"):
        for chunk in P.PLACEHOLDER.split(_flat(P.load(name)))[::2]:
            chunk = chunk.strip(" .:,")
            if len(chunk) > 12:
                assert chunk in guide, (name, chunk)
    assert P.CLEAN_FRAME in guide
    for wording in P.KEYFRAME_FRAMINGS.values():
        assert wording.split("}", 1)[1].strip() in guide, wording
    for unwanted in ("no subtitles", "no on-screen text", "no black frames"):
        assert unwanted not in guide.lower()


def test_the_zips_hold_one_skill_each(tmp_path):
    made = B.zip_all(str(tmp_path))
    assert len(made) == len(B.ALL)
    with zipfile.ZipFile(made[0]) as zf:
        assert zf.namelist() == [f"{B.ALL[0]}/SKILL.md"]
