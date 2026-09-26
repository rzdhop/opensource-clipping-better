"""``python main.py --ai-story`` (AI Story phase 1, stage 9; spec 9.3, DEC-114).

``clipping/aistory/cli.py`` is driven through its ``main(argv)`` against a
story store under ``tmp_path`` (the hidden ``--outputs-dir``; never the
repository's ``outputs/``), and ``main.py`` itself through a subprocess for
the dispatch and the clip parser's ``--help``. An LLM step runs in-process
through the worker's registry with ``llm.run_chain`` replaced by a recorder
that answers from a queue: no network, and the keys set here are test values
that never leave the process.

Stdlib + pytest only: this file runs in the CI environment (DEC-012). The new
modules are imported inside the tests, so against the parent commit every
test fails on its own rather than the whole file failing to collect.
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, schemas, steps, templates
from clipping.cancel import CancelToken
from clipping.providers.errors import ProviderError
from clipping.providers.registry import Link

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"

LINK = Link("gemini", "gemini-test")
TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")
UNKNOWN_ID = "0123456789ab"

B1_REPLY = {
    "logline": "Des fruits en couple survivent au vote hebdomadaire d'une île de téléréalité.",
    "premise": (
        "Chaque semaine, les couples de fruits affrontent le vote du public. Le "
        "téléphone-coco annonce les résultats. Tout le monde ment pour rester à l'écran."
    ),
    "tone": "mélodramatique, conscient de lui-même, rapide",
    "genre_tags": ["soap", "survie", "comédie"],
}
B2_REPLY = {
    "setting_summary": "Une île tropicale de téléréalité où des fruits vivent en couple sous les caméras.",
    "rules": [
        "Les fruits sont des personnes ; personne ne le commente.",
        "Un vote public élimine un couple chaque semaine.",
        "Le téléphone-coco annonce les résultats du vote.",
        "Rompre en public coûte son image à un candidat.",
    ],
    "time_period": "contemporain",
    "recurring_motifs": ["le téléphone-coco", "le feu d'élimination", "le miroir des coulisses"],
}
B3_REPLY = {
    "themes_and_values": ["la loyauté contre l'ambition", "ce qu'on fait pour être aimé"],
    "audience": {"age": "13+", "platforms": ["tiktok", "shorts"]},
    "why_come_back": [
        "Le vote dont tout le monde parle.",
        "Une alliance se brise à chaque épisode.",
        "Le téléphone-coco change toujours tout.",
    ],
}
BIBLE_REPLIES = (B1_REPLY, B2_REPLY, B3_REPLY)
B2_OUTAGE = ProviderError("x", failures=[("gemini/gemini-test", "APITimeoutError: timed out")])


def _c1_concept(title, style_fit="fruit_drama"):
    return {
        "title": title,
        "logline": "Une ligne courte qui reste bien sous la limite de trente mots.",
        "world": "Une île de téléréalité où des fruits vivent en couple sous les caméras.",
        "cast_sketch": [
            {"name": "Mangue", "role": "lead", "one_line": "Elle veut gagner et ment pour rester."},
            {"name": "Kiwi", "role": "support", "one_line": "Il est loyal tant que cela l'arrange."},
            {"name": "Coco", "role": "recurring", "one_line": "Il anime le jeu et en sait plus que tous."},
        ],
        "hook_formula": "Un vote s'ouvre dans la première seconde.",
        "value": "La loyauté contre l'ambition.",
        "retention_mechanics": "Un vote chaque semaine.",
        "style_fit": style_fit,
    }


C1_REPLIES = [
    {"concepts": [_c1_concept(f"Titre {2 * k - 1}"), _c1_concept(f"Titre {2 * k}", "anime")]}
    for k in range(1, 6)
]


class FakeRunner:
    """Stands in for ``llm.run_chain``: records each call, answers from a queue
    (a reply, or an exception to raise)."""

    def __init__(self, *replies):
        self.queue = list(replies)
        self.calls = []

    def __call__(self, chain, **kwargs):
        self.calls.append(dict(kwargs, chain=list(chain)))
        if not self.queue:
            raise AssertionError(f"no reply queued for call {len(self.calls)}")
        reply = self.queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), LINK


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def cli(monkeypatch, tmp_path, capsys):
    """``cli.run(*argv)`` -> exit code, the story store under ``tmp_path``;
    no key, chain or slow-chain switch from the machine running the tests."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN"):
        monkeypatch.delenv(name, raising=False)
    outputs = tmp_path / "outputs"

    def run(*argv):
        module = importlib.import_module("clipping.aistory.cli")
        return module.main([*argv, "--outputs-dir", str(outputs)])

    def story(story_id):
        return json.loads((outputs / "stories" / story_id / "story.json").read_text(encoding="utf-8"))

    def lock(story_id):
        return json.loads((outputs / "stories" / story_id / "style_lock.json").read_text(encoding="utf-8"))

    return SimpleNamespace(run=run, outputs=outputs, story=story, lock=lock,
                           monkeypatch=monkeypatch, capsys=capsys)


def _keyed(cli):
    """A one-link chain with a (test) key: the key gate passes."""
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test")
    cli.monkeypatch.setenv("GOOGLE_API_KEY", "test-gemini-key")


def _fake_llm(cli, *replies):
    from clipping.providers import llm

    runner = FakeRunner(*replies)
    cli.monkeypatch.setattr(llm, "run_chain", runner)
    return runner


def _new(cli, *extra):
    """A French story with tentafruit_island chosen; its id."""
    assert cli.run("new", "--lang", "fr", "--concept", "tentafruit_island", *extra) == 0
    out = cli.capsys.readouterr().out
    return out.split()[0]


def _with_bible(cli):
    """A story whose bible is written and approved through the CLI."""
    story_id = _new(cli)
    _keyed(cli)
    _fake_llm(cli, *BIBLE_REPLIES)
    assert cli.run("step", story_id, "bible", "--auto-approve") == 0
    cli.capsys.readouterr()
    return story_id


def _with_style_draft(cli):
    story_id = _with_bible(cli)
    assert cli.run("step", story_id, "style") == 0
    cli.capsys.readouterr()
    return story_id


# --------------------------------------------------------------------- new

def test_new_without_a_language_is_a_usage_error_naming_lang(cli):
    assert cli.run("new", "--concept", "tentafruit_island") == 2
    err = cli.capsys.readouterr().err
    assert "--lang" in err
    assert not cli.outputs.exists()


def test_new_creates_a_valid_story_with_the_concept_chosen_in_its_language(cli):
    code = cli.run("new", "--lang", "fr", "--concept", "tentafruit_island", "--style", "fruit_drama")

    assert code == 0
    out = cli.capsys.readouterr().out
    story_id = out.split()[0]
    story = cli.story(story_id)
    assert schemas.story_bible_errors(story) == []
    assert (story["status"], story["language"], story["concept_id"]) == ("concept_chosen", "fr", "tentafruit_island")
    assert story["title"] == TENTAFRUIT["title"]["fr"]
    assert story["concept"]["world"] == TENTAFRUIT["world"]["fr"]
    assert story["style_template_id"] == "fruit_drama"
    assert story["approvals"]["concept"] and story["approvals"]["bible"] is None
    assert out.strip() == f"{story_id}  concept_chosen   fr  {TENTAFRUIT['title']['fr']}"


def test_new_takes_the_story_defaults_unless_told_otherwise(cli):
    story_id = _new(cli)
    assert cli.story(story_id)["generation_profile"] == defaults.default_generation_profile()

    assert cli.run("new", "--lang", "en", "--tier", "2", "--route", "local",
                   "--consistency-mode", "prompt_only", "--budget-profile", "quality") == 0
    other = cli.capsys.readouterr().out.split()[0]
    story = cli.story(other)
    assert story["generation_profile"] == {
        "tier": 2, "route": "local", "consistency_mode": "prompt_only", "budget_profile": "quality"}
    assert (story["status"], story["title"], story["concept"]) == ("draft", "", None)


@pytest.mark.parametrize("argv", [
    ["--lang", "de"],
    ["--lang", "fr", "--route", "cloud"],
    ["--lang", "fr", "--tier", "4"],
    ["--lang", "fr", "--style", "vaporwave"],
    ["--lang", "fr", "--concept", "gen_01"],
])
def test_new_refuses_a_value_outside_its_closed_list_and_creates_nothing(cli, argv):
    assert cli.run("new", *argv) == 2
    assert not cli.outputs.exists()


# -------------------------------------------------------------------- list

def test_list_prints_one_line_per_story(cli):
    story_id = _new(cli)

    assert cli.run("list") == 0
    out = cli.capsys.readouterr().out
    assert out.splitlines() == [f"{story_id}  concept_chosen   fr  {TENTAFRUIT['title']['fr']}"]


def test_list_with_no_story_says_so_on_stderr_only(cli):
    assert cli.run("list") == 0
    captured = cli.capsys.readouterr()
    assert captured.out == "" and "No stories" in captured.err


# ------------------------------------------------------------------- bible

def test_the_bible_runs_here_with_the_process_keys_and_is_approved(cli):
    story_id = _new(cli)
    _keyed(cli)
    runner = _fake_llm(cli, *BIBLE_REPLIES)

    code = cli.run("step", story_id, "bible", "--auto-approve")

    assert code == 0
    story = cli.story(story_id)
    assert schemas.story_bible_errors(story) == []
    assert story["logline"] == B1_REPLY["logline"] and story["tone"] == B1_REPLY["tone"]
    assert story["world"] == B2_REPLY
    assert story["why_come_back"] == B3_REPLY["why_come_back"]
    assert story["approvals"]["bible"] and story["status"] == "bible_approved"

    assert len(runner.calls) == 3
    call = runner.calls[0]
    assert call["chain"] == [LINK]
    assert call["keys"] == {"gemini": "test-gemini-key"}  # the process env: settings_env is {}
    assert isinstance(call["cancel"], CancelToken) and not call["cancel"].cancelled

    out = cli.capsys.readouterr().out
    for part in ("B1", "B2", "B3"):
        assert f"✍️ {part} via gemini/gemini-test" in out
    assert "✅ Bible approved." in out
    assert out.splitlines()[-1].startswith(f"{story_id}  bible_approved")


def test_without_auto_approve_the_bible_is_written_and_left_for_approval(cli):
    story_id = _new(cli)
    _keyed(cli)
    _fake_llm(cli, *BIBLE_REPLIES)

    assert cli.run("step", story_id, "bible") == 0
    story = cli.story(story_id)
    assert story["world"] == B2_REPLY
    assert story["approvals"]["bible"] is None and story["status"] == "concept_chosen"


def test_a_failed_step_is_exit_1_with_its_reason_and_nothing_is_approved(cli):
    story_id = _new(cli)
    _keyed(cli)
    _fake_llm(cli, B1_REPLY, B2_OUTAGE, B3_REPLY)

    code = cli.run("step", story_id, "bible", "--auto-approve")

    assert code == 1
    err = cli.capsys.readouterr().err
    assert "Bible incomplete: B2 failed" in err and "Regenerate 'world'" in err
    story = cli.story(story_id)
    assert story["logline"] == B1_REPLY["logline"] and story["world"] is None
    assert story["approvals"]["bible"] is None


def test_the_bible_needs_a_chosen_concept_before_anything_else(cli):
    assert cli.run("new", "--lang", "fr") == 0
    story_id = cli.capsys.readouterr().out.split()[0]
    runner = _fake_llm(cli)  # and no key at all: the concept is checked first

    assert cli.run("step", story_id, "bible") == 1
    assert cli.capsys.readouterr().err.strip() == "Choose a concept first."
    assert runner.calls == []


# ---------------------------------------------------------------- key gate

def test_a_chain_with_no_keyed_link_is_refused_naming_the_keys(cli):
    story_id = _new(cli)
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test")
    runner = _fake_llm(cli)

    for step in ("bible", "concepts"):
        assert cli.run("step", story_id, step) == 1
        err = cli.capsys.readouterr().err
        assert "No link in the LLM chain has an API key" in err
        assert "GOOGLE_API_KEY" in err and "in the environment or in .env" in err
    assert runner.calls == []
    assert cli.story(story_id)["logline"] is None


def test_the_default_chain_with_no_key_names_its_primaries(cli):
    from clipping.providers import registry

    story_id = _new(cli)
    _fake_llm(cli)

    assert cli.run("step", story_id, "concepts") == 1
    err = cli.capsys.readouterr().err
    for name in ("groq", "gemini", "openrouter", "mistral"):
        assert registry.PROVIDERS[name].env_key in err


def test_the_slow_floor_alone_is_refused_unless_allowed(cli):
    story_id = _new(cli)
    cli.monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    runner = _fake_llm(cli, *BIBLE_REPLIES)

    assert cli.run("step", story_id, "bible") == 1
    err = cli.capsys.readouterr().err
    assert "GROQ_API_KEY" in err and "--allow-slow-chain" in err
    assert runner.calls == []

    assert cli.run("step", story_id, "bible", "--allow-slow-chain") == 0
    assert len(runner.calls) == 3


def test_the_slow_chain_switch_in_the_environment_counts_too(cli):
    story_id = _new(cli)
    cli.monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    cli.monkeypatch.setenv("ALLOW_SLOW_CHAIN", "1")
    runner = _fake_llm(cli, *BIBLE_REPLIES)

    assert cli.run("step", story_id, "bible") == 0
    assert len(runner.calls) == 3


# ---------------------------------------------------------------- concepts

def test_concepts_run_here_and_a_note_reaches_the_prompt(cli):
    story_id = _new(cli)
    _keyed(cli)
    runner = _fake_llm(cli, *C1_REPLIES)

    assert cli.run("step", story_id, "concepts", "--note", "plus sombre") == 0

    doc = json.loads((cli.outputs / "stories" / story_id / "concepts.json").read_text(encoding="utf-8"))
    assert schemas.story_concepts_errors(doc) == []
    assert len(doc["concepts"]) == 10
    assert len(runner.calls) == 5
    assert all("plus sombre" in call["user"] for call in runner.calls)
    assert "Generated 10 of 10 concepts." in cli.capsys.readouterr().out
    # Generating concepts approves nothing: a concept is approved by choosing it.
    assert cli.story(story_id)["status"] == "concept_chosen"


# ------------------------------------------------------------------- style

def test_the_style_is_built_with_an_override_and_locked(cli):
    story_id = _with_bible(cli)

    code = cli.run("step", story_id, "style", "--template", "fruit_drama",
                   "--override", 'palette.accents=["#FFD400"]', "--auto-approve")

    assert code == 0
    lock = cli.lock(story_id)
    assert schemas.style_lock_errors(lock) == []
    assert lock["template_id"] == "fruit_drama"
    assert lock["palette"]["accents"] == ["#FFD400"]
    assert lock["overrides"] == {"palette.accents": ["#FFD400"]}
    assert lock["locked_at"]
    story = cli.story(story_id)
    assert story["status"] == "style_approved" and story["style_template_id"] == "fruit_drama"
    out = cli.capsys.readouterr().out
    assert '🎨 Style draft: fruit_drama v' in out and 'palette.accents=["#FFD400"]' in out
    assert f"🔒 Style locked at {lock['locked_at']}." in out

    # Locked: the style step no longer changes it.
    assert cli.run("step", story_id, "style", "--override", 'palette.accents=["#000000"]') == 1
    assert "locked" in cli.capsys.readouterr().err
    assert cli.lock(story_id)["palette"]["accents"] == ["#FFD400"]


def test_an_override_value_is_json_when_it_parses_and_text_otherwise(cli):
    story_id = _with_bible(cli)

    assert cli.run("step", story_id, "style", "--override",
                   "typography.font_family=Comic Neue", "typography.ai_label=true",
                   "--consistency-mode", "prompt_only") == 0
    lock = cli.lock(story_id)
    assert lock["typography"]["font_family"] == "Comic Neue"
    assert lock["typography"]["ai_label"] is True
    assert lock["locked_at"] is None
    story = cli.story(story_id)
    assert story["generation_profile"]["consistency_mode"] == "prompt_only"
    assert story["approvals"]["style"] is None


def test_refused_overrides_are_exit_1_listing_every_error_and_write_nothing(cli):
    story_id = _with_bible(cli)

    assert cli.run("step", story_id, "style", "--override", 'palette.accents=["red"]', "nope.path=1") == 1
    err = cli.capsys.readouterr().err
    assert "The style was refused" in err and "palette.accents" in err and "nope.path" in err
    assert not (cli.outputs / "stories" / story_id / "style_lock.json").exists()


def test_an_override_without_a_value_is_a_usage_error(cli):
    story_id = _with_bible(cli)
    assert cli.run("step", story_id, "style", "--override", "palette.accents") == 2
    assert "PATH=VALUE" in cli.capsys.readouterr().err


def test_the_style_needs_an_approved_bible(cli):
    story_id = _new(cli)
    assert cli.run("step", story_id, "style") == 1
    assert cli.capsys.readouterr().err.strip() == "Approve the bible first."


# ----------------------------------------------------------- style preview

def test_the_preview_needs_a_style_first(cli):
    story_id = _with_bible(cli)
    assert cli.run("step", story_id, "style_preview") == 1
    assert cli.capsys.readouterr().err.strip() == "Build the style first."


def test_the_preview_is_refused_when_no_image_link_can_run(cli):
    from clipping.aistory.steps import style_preview

    story_id = _with_style_draft(cli)
    ran = []
    cli.monkeypatch.setattr(style_preview, "estimate",
                            lambda env, **kw: {"ready": False, "message": "No link of IMAGE_CHAIN can run."})
    cli.monkeypatch.setitem(steps.RUNNERS, "style_preview", ran.append)

    assert cli.run("step", story_id, "style_preview") == 1
    assert cli.capsys.readouterr().err.strip() == "No link of IMAGE_CHAIN can run."
    assert ran == []


def test_the_preview_runs_through_the_registry_with_the_process_env_and_a_real_token(cli):
    from clipping.aistory.steps import style_preview

    story_id = _with_style_draft(cli)
    seen = {}

    def fake_estimate(env, **kwargs):
        seen["estimate"] = (env, kwargs)
        return {"ready": True, "message": "3 images on a free link."}

    ran = []
    cli.monkeypatch.setattr(style_preview, "estimate", fake_estimate)
    cli.monkeypatch.setitem(steps.RUNNERS, "style_preview", ran.append)

    assert cli.run("step", story_id, "style_preview") == 0
    assert seen["estimate"] == ({}, {"route": "auto", "story_spent": 0.0})
    [ctx] = ran
    assert (ctx.story_id, ctx.step, ctx.params, ctx.settings_env) == (story_id, "style_preview", {}, {})
    assert ctx.outputs_dir == str(cli.outputs)
    assert type(ctx.cancel) is CancelToken and not ctx.cancel.cancelled
    assert ctx.on_log is print


# ------------------------------------------------------------ option rules

@pytest.mark.parametrize("step", ["concepts", "style_preview"])
def test_auto_approve_is_refused_where_there_is_nothing_to_approve(cli, step):
    story_id = _new(cli)
    _keyed(cli)
    runner = _fake_llm(cli)

    assert cli.run("step", story_id, step, "--auto-approve") == 2
    err = cli.capsys.readouterr().err
    assert f"--auto-approve does not apply to '{step}'" in err
    assert ("choosing" if step == "concepts" else "style --auto-approve") in err
    assert runner.calls == []


@pytest.mark.parametrize("step,option", [
    ("bible", ["--note", "plus sombre"]),
    ("style", ["--note", "plus sombre"]),
    ("bible", ["--template", "anime"]),
    ("concepts", ["--override", "palette.accents=[]"]),
    ("style_preview", ["--consistency-mode", "prompt_only"]),
    ("style", ["--allow-slow-chain"]),
])
def test_an_option_given_to_a_step_it_does_not_apply_to_is_a_usage_error(cli, step, option):
    story_id = _new(cli)
    runner = _fake_llm(cli)

    assert cli.run("step", story_id, step, *option) == 2
    assert "applies to" in cli.capsys.readouterr().err
    assert runner.calls == []


@pytest.mark.parametrize("step", ["cast", "render", "regenerate"])
def test_a_step_phase_1_does_not_run_is_a_usage_error(cli, step):
    story_id = _new(cli)
    assert cli.run("step", story_id, step) == 2


# -------------------------------------------------------------- unknown ids

@pytest.mark.parametrize("story_id", [UNKNOWN_ID, "../x", "ABC"])
def test_an_unknown_story_is_exit_1_and_creates_nothing(cli, story_id):
    _keyed(cli)
    runner = _fake_llm(cli)

    assert cli.run("step", story_id, "bible") == 1
    assert cli.capsys.readouterr().err.strip() == "Story not found"
    assert runner.calls == []
    assert not cli.outputs.exists()


def test_a_corrupt_story_is_exit_1_with_one_sentence(cli):
    story_id = _new(cli)
    (cli.outputs / "stories" / story_id / "story.json").write_text('{"not": "a story"}', encoding="utf-8")

    assert cli.run("step", story_id, "style") == 1
    err = cli.capsys.readouterr().err
    assert err.startswith(f"Story {story_id} cannot be read") and "Traceback" not in err


# ------------------------------------------------------------------ Ctrl-C

def test_ctrl_c_cancels_the_step_and_exits_130(cli):
    story_id = _new(cli)
    _keyed(cli)
    runner = _fake_llm(cli, KeyboardInterrupt())

    assert cli.run("step", story_id, "bible") == 130
    assert "Cancelled" in cli.capsys.readouterr().err
    [call] = runner.calls
    assert call["cancel"].cancelled
    assert cli.story(story_id)["logline"] is None


# ------------------------------------------------------------ the boundary

def test_the_cli_and_the_workflow_import_nothing_from_web():
    """A CLI user need not have fastapi: nothing under web/ is imported."""
    code = (
        "import sys; import clipping.aistory.cli, clipping.aistory.workflow; "
        "print(sorted(m for m in sys.modules if m == 'web' or m.startswith('web.')))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


# ----------------------------------------------------------------- main.py

def _main_py(*argv, tmp_path):
    return subprocess.run(
        [sys.executable, str(MAIN), *argv], cwd=tmp_path, capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))},
    )


def test_main_py_dispatches_ai_story_before_the_clip_parser(cli, tmp_path):
    story_id = _new(cli)

    result = _main_py("--ai-story", "list", "--outputs-dir", str(cli.outputs), tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"{story_id}  concept_chosen   fr  {TENTAFRUIT['title']['fr']}"]


def test_main_py_ai_story_new_without_a_language_is_its_own_usage_error(tmp_path):
    outputs = tmp_path / "outputs"

    result = _main_py("--ai-story", "new", "--outputs-dir", str(outputs), tmp_path=tmp_path)

    assert result.returncode == 2
    assert "main.py --ai-story new" in result.stderr and "--lang" in result.stderr
    assert "--video" not in result.stderr  # the clip parser never saw it
    assert not outputs.exists()


def test_main_py_checks_for_ai_story_before_it_builds_a_config():
    text = MAIN.read_text(encoding="utf-8")
    assert text.index('if sys.argv[1:2] == ["--ai-story"]:') < text.index("cfg = build_config(sys.argv[1:])")


def test_the_clip_help_still_works_and_points_at_ai_story(tmp_path):
    from clipping import config

    result = _main_py("--help", tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().splitlines()[-1] == config.AI_STORY_POINTER
    assert "python main.py --ai-story --help" in config.AI_STORY_POINTER
    # A line of the epilog, not an option of the clip parser.
    assert not any("--ai-story" in action.option_strings for action in config._build_parser()._actions)
