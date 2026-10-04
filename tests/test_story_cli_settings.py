"""The CLI's ``--settings`` (the log sweep of 2026-10-02: "the CLI reads keys
only from the process env; the VPS keeps them in Settings; every walk needs
a scratch driver"). With it, every step, estimate and gate of the run reads
the dashboard's stored Settings (``web.api.settings_store``) over the process
environment, as the API does; without it, nothing changes. No key value is
ever printed. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import importlib
import json

import pytest

from clipping.aistory import defaults

FAL = "fal-test-key-must-not-print"


@pytest.fixture
def cli(tmp_path, monkeypatch):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("ALLOW_PAID", "LLM_CHAIN", "STORY_LLM_CHAIN"):
        monkeypatch.delenv(name, raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"FAL_KEY": FAL, "ALLOW_PAID": "1"}), encoding="utf-8")
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(settings))
    module = importlib.import_module("clipping.aistory.cli")
    monkeypatch.setattr(module, "_SETTINGS_ENV", {})
    return module, tmp_path / "outputs"


def _new(module, outputs, *extra):
    from clipping.aistory.store import StoryStore

    assert module.main(["new", "--lang", "fr", "--outputs-dir", str(outputs), *extra]) == 0
    stories = StoryStore(str(outputs), on_log=lambda line: None)
    return stories.get(stories.list()[0]["story_id"])


def test_with_settings_a_new_story_sees_the_stored_fal_key(cli, capsys):
    module, outputs = cli

    story = _new(module, outputs, "--settings")

    # Re-pinned on purpose (plan 22 stage 5): the default with the keys is the manual native-speech profile.
    # Re-pinned again (plan 22 stage 2, DEC-274): every new story is stamped "writing": "v3".
    assert story["generation_profile"] == dict(
        defaults.manual_speech_generation_profile(), writing=defaults.WRITING_V3
    )
    out = capsys.readouterr()
    assert "stored Settings" in out.out and FAL not in out.out + out.err


def test_without_settings_the_cli_reads_the_environment_only(cli, capsys):
    module, outputs = cli

    story = _new(module, outputs)

    # Re-pinned on purpose (plan 22 stage 2, DEC-274): every new story is stamped "writing": "v3".
    assert story["generation_profile"] == dict(
        defaults.default_generation_profile(), writing=defaults.WRITING_V3
    )
    assert FAL not in "".join(capsys.readouterr())


def test_every_empty_env_of_the_cli_reads_the_settings_switch():
    """A text guard: no CLI call site hands a step, an estimate or a gate a
    literal empty env any more -- each goes through _settings_env()."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "clipping" / "aistory" / "cli.py").read_text(
        encoding="utf-8")
    for pattern in ("env={}", "settings_env={}", "llm_gate({}", "new_story_profile({})"):
        assert pattern not in src, pattern


def test_the_cli_reads_the_file_the_dashboard_writes(tmp_path, monkeypatch):
    """The CLI never imports web (tests/test_story_workflow.py's guard), so it
    reads the Settings file itself: the same default path and, for what the
    dashboard saves, the same values as web.api.settings_store.load."""
    from clipping.aistory import cli as cli_module
    from web.api import settings_store

    monkeypatch.delenv("WEB_SETTINGS_FILE", raising=False)
    assert cli_module.SETTINGS_FILE == settings_store.SETTINGS_PATH
    assert cli_module.settings_file() == settings_store.settings_path()

    path = tmp_path / "settings.json"
    assert settings_store.save({"FAL_KEY": "k", "ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "2.0",
                                "DAILY_CAP_USD": ""}, path=str(path))
    assert cli_module.stored_settings(str(path)) == settings_store.load(str(path))
    assert cli_module.stored_settings(str(tmp_path / "missing.json")) == {}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert cli_module.stored_settings(str(tmp_path / "bad.json")) == {}
