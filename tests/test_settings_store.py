"""Settings entered in the dashboard must survive a restart.

They used to live only in a module-level dict, so `docker compose restart`
silently emptied them and the next job failed for a key the user could still
see listed as set. The file holds API keys, so it is written 0600 and
atomically.
"""

import json
import os
import stat

import pytest

from web.api import settings_store


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "settings.json")


# ------------------------------------------------------------------ round trip

def test_saved_values_come_back(path):
    values = {"GROQ_API_KEY": "abc", "DEFAULT_CLIPS": "5"}
    assert settings_store.save(values, path) is True
    assert settings_store.load(path) == values


def test_loading_a_missing_file_is_empty_not_an_error(path):
    assert settings_store.load(path) == {}


def test_a_corrupt_file_does_not_stop_the_server_starting(path):
    """The user can re-enter the values; they cannot re-enter them into a
    server that refuses to boot."""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json at all")
    assert settings_store.load(path) == {}


def test_a_json_file_that_is_not_an_object_is_ignored(path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump([1, 2, 3], handle)
    assert settings_store.load(path) == {}


def test_values_are_coerced_to_strings(path):
    settings_store.save({"DEFAULT_CLIPS": 5, "EMPTY": None}, path)
    loaded = settings_store.load(path)
    assert loaded["DEFAULT_CLIPS"] == "5"
    assert loaded["EMPTY"] == ""


# ------------------------------------------------------------------ security

def test_the_file_is_not_readable_by_anyone_else(path):
    settings_store.save({"GROQ_API_KEY": "secret"}, path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"settings file is {oct(mode)}, expected 0o600"


def test_the_mode_survives_a_rewrite(path):
    settings_store.save({"A": "1"}, path)
    settings_store.save({"A": "2"}, path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_redact_hides_secrets_but_keeps_plain_settings():
    out = settings_store.redact({
        "GROQ_API_KEY": "gsk_realkey", "DEFAULT_CLIPS": "7", "HF_TOKEN": "",
    })
    assert out["GROQ_API_KEY"] == "***"
    assert out["DEFAULT_CLIPS"] == "7"
    assert out["HF_TOKEN"] == ""        # empty is not a secret worth hiding


def test_every_provider_key_is_treated_as_a_secret():
    """A key added to the settings surface but not to SECRET_KEYS would be
    printed in the clear by anything that redacts before logging."""
    from clipping.config import PROVIDER_KEYS

    for _attr, env_name in PROVIDER_KEYS.values():
        assert env_name in settings_store.SECRET_KEYS, env_name


# -------------------------------------------------------------- atomicity

def test_no_temp_file_is_left_behind(tmp_path):
    target = str(tmp_path / "settings.json")
    settings_store.save({"A": "1"}, target)
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_an_unwritable_directory_is_reported_not_raised(tmp_path):
    """Failing to save settings must not take the request down with it."""
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        assert settings_store.save({"A": "1"}, str(blocked / "settings.json")) is False
    finally:
        blocked.chmod(0o700)


# ------------------------------------------------------- wired into the worker

def test_the_worker_loads_and_writes_through():
    """Loaded at import and saved on every update, or the whole point is lost."""
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "api" / "worker.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "settings_store.load" in calls
    assert "settings_store.save" in calls
