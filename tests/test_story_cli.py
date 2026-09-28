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
    {"concepts": [_c1_concept(f"Titre {k}", "anime" if k % 2 == 0 else "fruit_drama")]}
    for k in range(1, 11)
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
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "ALLOW_PAID"):
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


def test_a_chain_keyed_only_on_a_paid_link_is_refused_unless_allow_paid_is_set(cli):
    story_id = _new(cli)
    cli.monkeypatch.setenv("LLM_CHAIN", "gemini/gemini-test,openrouter/test-model")
    cli.monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    runner = _fake_llm(cli, *BIBLE_REPLIES)

    for step in ("bible", "concepts"):
        assert cli.run("step", story_id, step) == 1
        err = cli.capsys.readouterr().err
        assert err.strip() == (
            "The only keyed link of the LLM chain is paid (openrouter/test-model), and allow_paid is "
            "off: AI Story spends only on opt-in. Set the key of a free link, one of: "
            "GOOGLE_API_KEY (https://aistudio.google.com/apikey), in the environment or in .env; "
            "or turn allow_paid on to use it."
        )
    assert runner.calls == []
    assert cli.story(story_id)["logline"] is None

    cli.monkeypatch.setenv("ALLOW_PAID", "1")
    assert cli.run("step", story_id, "bible") == 0
    assert len(runner.calls) == 3
    assert runner.calls[0]["chain"] == [Link("gemini", "gemini-test"), Link("openrouter", "test-model")]


# ---------------------------------------------------------------- concepts

def test_concepts_run_here_and_a_note_reaches_the_prompt(cli):
    story_id = _new(cli)
    _keyed(cli)
    runner = _fake_llm(cli, *C1_REPLIES)

    assert cli.run("step", story_id, "concepts", "--note", "plus sombre") == 0

    doc = json.loads((cli.outputs / "stories" / story_id / "concepts.json").read_text(encoding="utf-8"))
    assert schemas.story_concepts_errors(doc) == []
    assert len(doc["concepts"]) == 10
    assert len(runner.calls) == 10
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


@pytest.mark.parametrize("step", ["script", "render", "regenerate"])
def test_a_step_the_cli_does_not_run_is_a_usage_error(cli, step):
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


# =================================================================== phase 2
#
# Steps 5-7 from a terminal (phase 2, stage 8): ``cast``, ``places_proposal``,
# ``places`` and ``season`` run in this process through the worker's registry
# with the runner defaults -- the LLM through ``llm.run_chain`` (a stand-in
# answering per prompt id), the image chains and Edge TTS through fake
# adapters registered in the one table the steps read. Hermetic like
# ``tests/test_story_cast_steps.py``: no image, TTS or vision variable, cap or
# limit of the machine reaches a test, no request leaves the process, and the
# repository's ``data/`` files and ``outputs/stories`` are fingerprinted.

import hashlib  # noqa: E402  (the phase-2 block keeps its imports with it)

from clipping.providers.generation import GenResult  # noqa: E402

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "POLLINATIONS_API_KEY", "OPENROUTER_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN",
    "TTS_CHAIN", "VISION_CHAIN", "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# The free route of this machine (test values): a free text-to-image link, no
# editor (fal has no key, and would be paid anyway), Edge for the voices.
PHASE2_ENV = {"IMAGE_CHAIN": "pollinations/flux", "IMAGE_EDIT_CHAIN": "fal/seedream-4-edit",
              "TTS_CHAIN": "edge/fr-FR-HenriNeural"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
PROMPT_ONLY_HINT = "re-run with --prompt-only to continue with prompt-only consistency (labelled)"

KIWI = "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh at the mouth"
MANGO = "an anthropomorphic mango with smooth orange-yellow skin and a sly grin"
FIG = "an anthropomorphic fig with soft purple skin and a tiny green stem"
BROCCOLI = "an anthropomorphic broccoli with a tall green crown and a rhinestone headset"
PEPPER = "an anthropomorphic red pepper with a glossy skin and heart-shaped sunglasses"
AVOCADO = "an anthropomorphic avocado with a dark pebbled skin and a signet ring"


def k1(descriptor, items, *, gender="male", age="adult", tags=("warm",), sample="Je gagne toujours, mon cœur.",
       relationships=()):
    return {
        "descriptor": descriptor,
        "signature_items": list(items),
        "personality": {"traits": ["rusé", "charmeur"], "wants": "Gagner le vote de la semaine.",
                        "fears": "Être démasqué devant tout le monde.", "speech_style": "Des phrases courtes."},
        "voice": {"gender": gender, "age": age, "style_tags": list(tags), "direction": "smug, warm, a little nasal",
                  "sample_line": sample},
        "relationships": [{"with": name, "relation": relation} for name, relation in relationships],
    }


K1_KIWI = k1(KIWI, ["thin gold chain", "white linen shirt", "left-eyebrow scar"])
K1_MANGO = k1(MANGO, ["rhinestone crown hair clip", "red satin dress"], gender="female",
              sample="Je ne perds jamais, chéri.", relationships=[("Kiwilo", "son ex secret")])
K1_FIG = k1(FIG, ["round glasses", "yellow cardigan"], gender="female", age="young", tags=("bright",),
            sample="Oh non, pas encore un vote !")
CAST_ARGS = ("--characters", "Kiwilo", "--characters", "Mangella",
             "--custom", "Figuette|support|Une figue timide qui voit tout.")
IDS = ["char_kiwilo", "char_mangella", "char_figuette"]

P0_REPLY = {
    "places": [{"name": "Le camp de plage", "one_line": "Là où les couples dorment et complotent."},
               {"name": "Le feu d'élimination", "one_line": "Là où tombe le verdict du vote."}],
    "props": [{"name": "Le coco-téléphone", "one_line": "Il annonce le résultat du vote.", "owner": "Kiwilo"}],
}
P1_BEACH = {"descriptor": "a crescent of white sand with palm-leaf huts and a stone bonfire ring",
            "layout_notes": "huts on the left, the sea on the right, the bonfire ring at the back",
            "time_variants": ["day", "night"]}
P1_FIRE = {"descriptor": "a ring of tiki torches around a pit of glowing embers under palm trees",
           "layout_notes": "torches in a circle, the jury bench at the back",
           "time_variants": ["day"]}
R1_PHONE = {"descriptor": "a hollow coconut with a curly cord and a brass dial", "owner": "Kiwilo"}
BEACH, FIRE, PHONE = "place_le_camp_de_plage", "place_le_feu_d_elimination", "prop_le_coco_telephone"

S1_REPLY = {"arc": [
    {"ep": 1, "function": "setup", "summary": "Les couples arrivent sur l'île et le premier vote tombe."},
    {"ep": 2, "function": "midpoint_twist", "summary": "Le coco-téléphone révèle une trahison."},
    {"ep": 3, "function": "climax_and_reset", "summary": "Le dernier couple affronte la vérité."},
]}


def s2(summary, characters=("Kiwilo", "Mangella")):
    return {"summary": summary, "open_hooks_in": [], "open_hooks_out": ["Qui a volé le coco-téléphone ?"],
            "characters": list(characters)}


class PromptLLM:
    """Stands in for ``llm.run_chain``: answers each prompt id from its own
    queue, records every call; an entry is a reply or an exception to raise."""

    def __init__(self, **queues):
        from clipping.aistory import prompts

        self.queues = {prompt: list(replies) for prompt, replies in queues.items()}
        self.calls = []
        self._ids = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}

    def __call__(self, chain, **kwargs):
        prompt = self._ids[kwargs["schema_name"]]
        self.calls.append(dict(kwargs, chain=list(chain), prompt=prompt))
        queue = self.queues.get(prompt) or []
        if not queue:
            raise AssertionError(f"no {prompt} reply queued (call {len(self.calls)})")
        reply = queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), LINK

    def of(self, prompt):
        return [call for call in self.calls if call["prompt"] == prompt]


class FakeImage:
    """An image adapter: records every request and writes a small PNG."""

    def __init__(self):
        self.probes = []
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_kwargs):
        self.probes.append(f"{link.provider}/{link.model}")
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        path = os.path.join(request.out_dir, f"{request.extra['name']}.png")
        with open(path, "wb") as fh:
            fh.write(PNG + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed, meta={})


class FakeTTS:
    """A free Edge stand-in: writes a small mp3, records every request."""

    def __init__(self):
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append((f"{link.provider}/{link.model}", request.text))
        path = os.path.join(request.out_dir, "sample.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake" + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), meta={"duration_s": 2.5})


def _fingerprint(path):
    if path.is_symlink() or path.exists():
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "present"
    return None


@pytest.fixture
def studio(cli, tmp_path):
    """``cli`` for the phase-2 steps: hermetic, the fakes registered, the
    free route of this machine and a keyed LLM chain in the environment."""
    from clipping.providers import (adapters, budget, generation, images, limits, llm, local_comfyui, pacing,
                                    transport)

    mp = cli.monkeypatch
    for name in GEN_VARS:
        mp.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            mp.delenv(name, raising=False)
    mp.setenv("LIMIT_POLLINATIONS_RPM", "0")
    mp.setenv("LIMIT_EDGE_RPM", "0")
    mp.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    mp.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    def no_sdk(**_kwargs):
        raise AssertionError("the OpenAI SDK was reached")

    mp.setattr(images, "urllib_transport", no_network)
    mp.setattr(local_comfyui, "urllib_transport", no_network)
    mp.setattr(transport, "urllib_transport", no_network)
    mp.setattr(images, "_openai_client", no_sdk)

    fakes = SimpleNamespace(t2i=FakeImage(), editor=FakeImage(), tts=FakeTTS())
    for key, adapter in ((("image", "pollinations"), fakes.t2i), (("image_edit", "fal"), fakes.editor),
                         (("image_edit", "local"), fakes.editor), (("tts", "edge"), fakes.tts)):
        mp.setitem(generation._ADAPTERS, key, adapter)
    for name, value in PHASE2_ENV.items():
        mp.setenv(name, value)
    _keyed(cli)

    def llm_answers(**queues):
        runner = PromptLLM(**queues)
        mp.setattr(llm, "run_chain", runner)
        return runner

    def character(story_id, char_id):
        path = cli.outputs / "stories" / story_id / "characters" / char_id / "character.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def entity_ids(story_id, kind):
        folder = cli.outputs / "stories" / story_id / kind
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield SimpleNamespace(cli=cli, run=cli.run, fakes=fakes, llm=llm_answers, character=character,
                          entity_ids=entity_ids, story=cli.story, capsys=cli.capsys)
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


def _styled(s):
    """A story whose bible and style are approved through the CLI."""
    story_id = _with_bible(s.cli)
    assert s.run("step", story_id, "style", "--auto-approve") == 0
    s.capsys.readouterr()
    return story_id


def _cast(s, *, extra=("--prompt-only", "--auto-approve")):
    """_styled, then the cast of three written, drawn, voiced and approved."""
    story_id = _styled(s)
    s.llm(K1=[K1_KIWI, K1_MANGO, K1_FIG])
    assert s.run("step", story_id, "cast", *CAST_ARGS, *extra) == 0
    s.capsys.readouterr()
    return story_id


def _placed(s):
    """_cast, then the proposal and its places and prop, made and approved."""
    story_id = _cast(s)
    s.llm(P0=[P0_REPLY])
    assert s.run("step", story_id, "places_proposal") == 0
    s.llm(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE])
    assert s.run("step", story_id, "places", "--auto-approve") == 0
    s.capsys.readouterr()
    return story_id


# ------------------------------------------------------------------ help

def test_the_help_lists_every_step_of_phases_1_and_2(cli):
    from clipping.aistory import cli as cli_module
    from clipping.aistory import workflow

    steps_ = workflow.PHASE1_STEPS + workflow.PHASE2_STEPS
    assert set(workflow.PHASE2_STEPS) == {"cast", "places_proposal", "places", "season"}
    for argv in (["--help"], ["step", "--help"]):
        assert cli_module.main(argv) == 0
        out = cli.capsys.readouterr().out
        for step in steps_:
            assert step in out, (argv, step)
    assert cli_module.main(["step", "--help"]) == 0
    out = cli.capsys.readouterr().out
    for option in ("--characters", "--custom", "--episodes", "--place", "--prop", "--prompt-only",
                   "--auto-approve"):
        assert option in out


def test_main_py_ai_story_step_help_lists_every_step(tmp_path):
    # DEC-009: choices= mirrors the closed list of steps, so this braced set
    # grows with each phase (phase 3, stage 9, adds script and storyboard;
    # phase 4, stage 13, adds assets, render and metadata).
    result = _main_py("--ai-story", "step", "--help", tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert ("{concepts,bible,style,style_preview,cast,places_proposal,places,season,script,storyboard,"
           "assets,render,metadata}" in result.stdout)


# ------------------------------------------------------------------ cast

def test_the_cast_runs_here_and_says_what_each_character_still_lacks(studio):
    s = studio
    story_id = _styled(s)
    runner = s.llm(K1=[K1_KIWI, K1_MANGO, K1_FIG])

    code = s.run("step", story_id, "cast", *CAST_ARGS)

    assert code == 0
    assert s.entity_ids(story_id, "characters") == sorted(IDS)
    assert [call["prompt"] for call in runner.calls] == ["K1", "K1", "K1"]
    assert runner.calls[0]["keys"] == {"gemini": "test-gemini-key"}  # the process env: settings_env is {}
    # Portraits and samples; the sheets wait: no editor can run, and none was asked.
    assert (len(s.fakes.t2i.requests), len(s.fakes.editor.requests), s.fakes.editor.probes,
            len(s.fakes.tts.requests)) == (3, 0, [], 3)
    out = s.capsys.readouterr().out
    for name in ("Kiwilo", "Mangella", "Figuette"):
        assert f"👤 {name}: still missing turnaround, expressions sheet (waiting for an editor)" in out
    assert "IMAGE_EDIT_CHAIN" in out
    assert PROMPT_ONLY_HINT in out
    story = s.story(story_id)
    # Prompt-only is the user's choice, never implied.
    assert story["generation_profile"]["consistency_mode"] == "references"
    assert story["approvals"]["cast"] is None and story["status"] == "style_approved"
    assert out.splitlines()[-1].startswith(f"{story_id}  style_approved")


def test_prompt_only_is_set_first_printed_and_the_sheets_come_out_labelled(studio):
    s = studio
    story_id = _styled(s)
    s.llm(K1=[K1_KIWI, K1_MANGO, K1_FIG])
    assert s.run("step", story_id, "cast", *CAST_ARGS) == 0
    s.capsys.readouterr()
    runner = s.llm()  # nothing to write: any LLM call fails the test

    code = s.run("step", story_id, "cast", "--prompt-only")

    assert code == 0
    assert runner.calls == []
    assert s.story(story_id)["generation_profile"]["consistency_mode"] == "prompt_only"
    # Only the six sheets were drawn -- text to image, never an edit; no new character.
    assert (len(s.fakes.t2i.requests), len(s.fakes.editor.requests), len(s.fakes.tts.requests)) == (9, 0, 3)
    assert s.entity_ids(story_id, "characters") == sorted(IDS)
    for char_id in IDS:
        refs = s.character(story_id, char_id)["refs"]
        assert (refs["portrait"]["consistency"], refs["turnaround"]["consistency"],
                refs["expressions"]["consistency"]) == ("base", "prompt_only", "prompt_only")
    out = s.capsys.readouterr().out
    assert "Consistency mode: prompt_only (--prompt-only)" in out
    assert out.index("Consistency mode: prompt_only") < out.index("👤 Kiwilo: turnaround")
    for name in ("Kiwilo", "Mangella", "Figuette"):
        assert f"👤 {name}: nothing missing, ready to approve" in out
    assert PROMPT_ONLY_HINT not in out


def test_auto_approve_approves_the_complete_characters_and_names_the_others(studio):
    s = studio
    story_id = _styled(s)
    s.llm(K1=[K1_KIWI, K1_MANGO, K1_FIG])

    assert s.run("step", story_id, "cast", *CAST_ARGS, "--auto-approve") == 0

    out = s.capsys.readouterr().out
    for name in ("Kiwilo", "Mangella", "Figuette"):
        assert f"⏸ {name} cannot be approved yet; missing: turnaround, expressions sheet." in out
    assert all(s.character(story_id, cid)["approved_at"] is None for cid in IDS)
    assert s.story(story_id)["approvals"]["cast"] is None

    s.llm()
    assert s.run("step", story_id, "cast", "--prompt-only", "--auto-approve") == 0

    out = s.capsys.readouterr().out
    for name in ("Kiwilo", "Mangella", "Figuette"):
        assert f"✅ {name} approved." in out
    assert all(s.character(story_id, cid)["approved_at"] for cid in IDS)
    story = s.story(story_id)
    assert story["approvals"]["cast"] and story["status"] == "cast_approved"
    assert out.splitlines()[-1].startswith(f"{story_id}  cast_approved")

    # Again: nothing is missing, nothing is called, nothing is re-approved.
    s.llm()
    approved = {cid: s.character(story_id, cid)["approved_at"] for cid in IDS}
    assert s.run("step", story_id, "cast", "--auto-approve") == 0
    out = s.capsys.readouterr().out
    assert {cid: s.character(story_id, cid)["approved_at"] for cid in IDS} == approved
    assert "✅ Kiwilo: already approved." in out
    assert (len(s.fakes.t2i.requests), len(s.fakes.tts.requests)) == (9, 3)


def test_without_characters_a_new_cast_is_the_whole_sketch(studio):
    s = studio
    story_id = _styled(s)
    # K1 runs in cast order: the leads, the supports, then the recurring host.
    s.llm(K1=[K1_KIWI, K1_MANGO,
              k1(PEPPER, ["heart-shaped sunglasses", "open red shirt"], sample="Toi, tu me plais."),
              k1(AVOCADO, ["signet ring", "velvet blazer"], age="young", sample="Mon père achètera le vote."),
              k1(BROCCOLI, ["rhinestone headset", "green sequin gown"], gender="female", age="elder",
                 sample="Le vote est ouvert, mes chéris.")])

    assert s.run("step", story_id, "cast", "--prompt-only") == 0

    assert s.entity_ids(story_id, "characters") == sorted(
        ["char_kiwilo", "char_mangella", "char_broccolia", "char_pepperino", "char_avocardo"])
    out = s.capsys.readouterr().out
    assert "No --characters: the concept's cast sketch, Kiwilo, Mangella, Broccolia, Pepperino and Avocardo." in out


def test_a_cast_that_exists_is_filled_not_extended_by_default(studio):
    s = studio
    story_id = _styled(s)
    s.llm(K1=[K1_KIWI])
    assert s.run("step", story_id, "cast", "--characters", "Kiwilo") == 0
    s.capsys.readouterr()

    runner = s.llm()
    assert s.run("step", story_id, "cast") == 0

    assert s.entity_ids(story_id, "characters") == ["char_kiwilo"]
    assert runner.calls == []
    assert "No --characters" not in s.capsys.readouterr().out


def test_a_failed_cast_still_says_what_each_character_lacks_and_approves_nothing(studio):
    s = studio
    story_id = _styled(s)
    outage = ProviderError("x", failures=[("gemini/gemini-test", "APITimeoutError: timed out")])
    s.llm(K1=[K1_KIWI, K1_MANGO, outage, outage])

    code = s.run("step", story_id, "cast", *CAST_ARGS, "--prompt-only", "--auto-approve")

    assert code == 1
    captured = s.capsys.readouterr()
    assert "Cast incomplete: Figuette text failed" in captured.err
    assert "👤 Figuette: still missing text, portrait, turnaround, expressions sheet, a pinned voice, " \
           "a voice sample" in captured.out
    assert "👤 Kiwilo: nothing missing, ready to approve" in captured.out
    assert all(s.character(story_id, cid)["approved_at"] is None for cid in IDS)


def test_an_unknown_sketch_name_is_exit_1_naming_the_valid_ones_and_creates_nothing(studio):
    s = studio
    story_id = _styled(s)
    runner = s.llm()

    assert s.run("step", story_id, "cast", "--characters", "Zorro") == 1

    err = s.capsys.readouterr().err
    assert "'Zorro' is not in the concept's cast sketch; pick from 'Kiwilo', 'Mangella'" in err
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []


def test_a_custom_character_is_checked_by_the_api_rules(studio):
    s = studio
    story_id = _styled(s)
    runner = s.llm()

    assert s.run("step", story_id, "cast", "--custom", "Figuette|villain|Une figue.") == 1

    err = s.capsys.readouterr().err
    assert "These characters cannot be created." in err and "custom[0].role: 'villain'" in err
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []


# ----------------------------------------------------------------- gates

def test_every_phase_2_step_waits_for_its_precondition_before_anything_else(studio):
    s = studio
    story_id = _with_bible(s.cli)  # the style is not approved
    for name in PHASE2_ENV:
        s.cli.monkeypatch.delenv(name)  # the preconditions come before every gate
    runner = s.llm()

    for step, reason in (("cast", "Approve the style first."), ("places_proposal", "Approve the style first."),
                         ("places", "Approve the style first."), ("season", "Approve the cast first.")):
        assert s.run("step", story_id, step) == 1, step
        assert s.capsys.readouterr().err.strip() == reason
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []


def test_the_key_gate_comes_before_any_character_and_prompt_only_stays_unset(studio):
    s = studio
    story_id = _styled(s)
    s.cli.monkeypatch.delenv("GOOGLE_API_KEY")
    runner = s.llm()

    assert s.run("step", story_id, "cast", *CAST_ARGS, "--prompt-only") == 1

    err = s.capsys.readouterr().err
    assert "No link in the LLM chain has an API key" in err and "in the environment or in .env" in err
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []
    assert s.story(story_id)["generation_profile"]["consistency_mode"] == "references"


def test_the_cast_is_refused_with_the_image_chains_verdict_when_no_link_can_run(studio):
    from clipping.aistory import store as story_store
    from clipping.aistory import workflow

    s = studio
    story_id = _styled(s)
    s.cli.monkeypatch.setenv("IMAGE_CHAIN", "fal/flux-schnell")  # no key, and paid
    runner = s.llm()

    assert s.run("step", story_id, "cast", *CAST_ARGS) == 1

    err = s.capsys.readouterr().err.strip()
    stories = story_store.StoryStore(s.cli.outputs, on_log=lambda line: None)
    verdict = workflow.image_verdict(stories, stories.get(story_id), 3, env={})
    assert verdict["ready"] is False and err == verdict["message"]
    assert "IMAGE_CHAIN" in err
    assert runner.calls == [] and s.entity_ids(story_id, "characters") == []
    assert s.fakes.t2i.requests == []


def test_the_slow_chain_switch_applies_to_the_phase_2_steps(studio):
    s = studio
    story_id = _styled(s)
    s.cli.monkeypatch.delenv("LLM_CHAIN")
    s.cli.monkeypatch.delenv("GOOGLE_API_KEY")
    s.cli.monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    runner = s.llm(K1=[K1_KIWI])

    assert s.run("step", story_id, "cast", "--characters", "Kiwilo", "--prompt-only") == 1
    assert "--allow-slow-chain" in s.capsys.readouterr().err
    assert runner.calls == []

    assert s.run("step", story_id, "cast", "--characters", "Kiwilo", "--prompt-only", "--allow-slow-chain") == 0
    assert [call["prompt"] for call in runner.calls] == ["K1"]


# ---------------------------------------------------------------- places

def test_places_proposal_then_places_from_it_approved_to_places_approved(studio):
    s = studio
    story_id = _cast(s)
    runner = s.llm(P0=[P0_REPLY])

    assert s.run("step", story_id, "places_proposal") == 0

    assert [call["prompt"] for call in runner.calls] == ["P0"]
    proposal = json.loads((s.cli.outputs / "stories" / story_id / "places_proposal.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in proposal["places"]] == ["Le camp de plage", "Le feu d'élimination"]
    assert s.entity_ids(story_id, "places") == []  # proposed, not made
    s.capsys.readouterr()

    runner = s.llm(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE])
    assert s.run("step", story_id, "places", "--auto-approve") == 0

    assert sorted(call["prompt"] for call in runner.calls) == ["P1", "P1", "R1"]
    assert s.entity_ids(story_id, "places") == sorted([BEACH, FIRE])
    assert s.entity_ids(story_id, "props") == [PHONE]
    out = s.capsys.readouterr().out
    for name in ("Le camp de plage", "Le feu d'élimination", "Le coco-téléphone"):
        assert f"✅ {name} approved." in out
    story = s.story(story_id)
    assert story["approvals"]["places"] and story["status"] == "places_approved"


def test_the_proposal_is_not_approved_it_is_chosen_with_the_places_step(studio):
    s = studio
    story_id = _cast(s)
    runner = s.llm(P0=[P0_REPLY])

    assert s.run("step", story_id, "places_proposal", "--auto-approve") == 2

    err = s.capsys.readouterr().err
    assert "--auto-approve does not apply to 'places_proposal'" in err
    assert "choose the places with" in err and "step STORY_ID places" in err
    assert runner.calls == []


def test_places_from_the_command_line_list_and_an_owner_by_name(studio):
    s = studio
    story_id = _cast(s)
    runner = s.llm(P1=[P1_BEACH], R1=[{"descriptor": "a hollow coconut with a curly cord", "owner": None}])

    code = s.run("step", story_id, "places", "--place", "Le camp de plage|Là où les couples dorment.",
                 "--prop", "Le coco-téléphone|Il sonne au pire moment.|Kiwilo")

    assert code == 0
    assert sorted(call["prompt"] for call in runner.calls) == ["P1", "R1"]
    assert s.entity_ids(story_id, "places") == [BEACH]
    prop = json.loads((s.cli.outputs / "stories" / story_id / "props" / PHONE / "prop.json").read_text(
        encoding="utf-8"))
    assert prop["owner_char_id"] == "char_kiwilo" and prop["approved_at"] is None
    assert s.story(story_id)["status"] == "cast_approved"


def test_places_without_a_list_or_a_proposal_is_refused(studio):
    s = studio
    story_id = _cast(s)
    runner = s.llm()

    assert s.run("step", story_id, "places") == 1
    assert s.capsys.readouterr().err.strip() == "Propose or list the places first."
    assert runner.calls == []


def test_a_prop_owner_that_is_no_character_is_exit_1(studio):
    s = studio
    story_id = _cast(s)
    runner = s.llm()

    assert s.run("step", story_id, "places", "--place", "Plage|Le sable.", "--prop", "Coco|Il sonne.|Zorro") == 1

    err = s.capsys.readouterr().err
    assert "These places and props cannot be made." in err and "props[0].owner: 'Zorro'" in err
    assert runner.calls == [] and s.entity_ids(story_id, "places") == []


# ---------------------------------------------------------------- season

def test_the_season_is_written_and_approved_and_the_story_is_ready(studio):
    s = studio
    story_id = _placed(s)
    runner = s.llm(S1=[S1_REPLY], S2=[s2("Un."), s2("Deux."), s2("Trois.")])

    assert s.run("step", story_id, "season", "--episodes", "3", "--auto-approve") == 0

    assert [call["prompt"] for call in runner.calls] == ["S1", "S2", "S2", "S2"]
    season = json.loads((s.cli.outputs / "stories" / story_id / "season.json").read_text(encoding="utf-8"))
    assert [entry["summary"] for entry in season["arc"]] == ["Un.", "Deux.", "Trois."]
    assert season["episodes_planned"] == 3 and season["approved_at"]
    story = s.story(story_id)
    assert story["approvals"]["season"] and story["status"] == "ready"
    out = s.capsys.readouterr().out
    assert "✅ Season approved." in out
    assert out.splitlines()[-1].startswith(f"{story_id}  ready")


def test_a_season_approval_the_rules_refuse_is_exit_1_after_the_arc_is_written(studio):
    s = studio
    story_id = _cast(s)  # the places are not approved
    s.llm(S1=[S1_REPLY], S2=[s2("Un."), s2("Deux."), s2("Trois.")])

    assert s.run("step", story_id, "season", "--episodes", "3", "--auto-approve") == 1

    assert s.capsys.readouterr().err.strip() == "Approve the cast, the places and the props first."
    season = json.loads((s.cli.outputs / "stories" / story_id / "season.json").read_text(encoding="utf-8"))
    assert len(season["arc"]) == 3 and season["approved_at"] is None
    assert s.story(story_id)["approvals"]["season"] is None


@pytest.mark.parametrize("episodes", ["2", "13"])
def test_an_episode_count_outside_3_to_12_is_refused_before_any_call(studio, episodes):
    s = studio
    story_id = _cast(s)
    runner = s.llm()

    assert s.run("step", story_id, "season", "--episodes", episodes) == 1

    assert s.capsys.readouterr().err.strip() == f"A season has 3 to 12 episodes, not {episodes}."
    assert runner.calls == []
    assert not (s.cli.outputs / "stories" / story_id / "season.json").exists()


# ----------------------------------------------------------- option rules

@pytest.mark.parametrize("step,option", [
    ("bible", ["--characters", "Kiwilo"]),
    ("places", ["--custom", "Figuette|support|Une figue."]),
    ("season", ["--place", "Plage|Le sable."]),
    ("cast", ["--prop", "Coco|Il sonne.|Kiwilo"]),
    ("cast", ["--episodes", "3"]),
    ("places", ["--episodes", "0"]),
    ("season", ["--prompt-only"]),
    ("places_proposal", ["--prompt-only"]),
    ("style", ["--prompt-only"]),
    ("style_preview", ["--allow-slow-chain"]),
    ("cast", ["--note", "plus sombre"]),
])
def test_a_phase_2_option_on_a_step_it_does_not_apply_to_is_a_usage_error(studio, step, option):
    s = studio
    story_id = _new(s.cli)
    runner = s.llm()

    assert s.run("step", story_id, step, *option) == 2

    err = s.capsys.readouterr().err
    assert "applies to" in err and f"not to '{step}'" in err
    assert runner.calls == []
    assert s.story(story_id)["generation_profile"]["consistency_mode"] == "references"


@pytest.mark.parametrize("step,option,shape", [
    ("cast", ["--custom", "Figuette|support"], "Name|role|one line"),
    ("cast", ["--custom", "Figuette"], "Name|role|one line"),
    ("places", ["--place", "Plage"], "Name|one line"),
    ("places", ["--place", "Plage|Le sable.|Kiwilo"], "Name|one line"),
    ("places", ["--prop", "Coco"], "Name|one line|Owner"),
    ("places", ["--prop", "Coco|Il sonne.|Kiwilo|x"], "Name|one line|Owner"),
])
def test_a_malformed_list_item_is_a_usage_error_naming_its_shape(studio, step, option, shape):
    s = studio
    story_id = _new(s.cli)
    runner = s.llm()

    assert s.run("step", story_id, step, *option) == 2

    err = s.capsys.readouterr().err
    assert shape in err and option[1] in err
    assert runner.calls == []
