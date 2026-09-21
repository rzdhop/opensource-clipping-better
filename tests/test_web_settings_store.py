"""Settings entered on the dashboard must survive a backend restart.

They used to live only in ``worker._settings_env``, a module-level dict, so a
restart silently discarded every API key and fell back to ``.env``. These tests
pin the storage contract, including the two rules that are easy to get wrong:
an empty value CLEARS an override (rather than storing an empty string that
would shadow ``.env`` forever), and loading must not rewrite the file.

The storage half is stdlib-only and runs in CI, which installs pytest and
nothing else (DEC-012). ``web.api.worker`` reaches pydantic through its own
imports, so the handful of tests that need it fetch it lazily and skip where it
is absent -- rather than importing it at module scope, which would abort
collection and take the whole suite down with it.
"""

import json
import os
import pathlib

import pytest

from web.api import settings_store


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Point the store at a throwaway file."""
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    return tmp_path / "settings.json"


@pytest.fixture
def worker(monkeypatch):
    """The worker module with an empty settings env, or skip.

    Importing it pulls in pydantic via config_adapter/models.
    """
    pytest.importorskip("pydantic")
    from web.api import worker as worker_module

    monkeypatch.setattr(worker_module, "_settings_env", {})
    return worker_module


# ------------------------------------------------------------- where it lives

def test_default_path_is_not_under_outputs(monkeypatch):
    """outputs/ is served by routes/files.py; secrets do not belong there."""
    monkeypatch.delenv("WEB_SETTINGS_FILE", raising=False)
    path = settings_store.settings_path().replace("\\", "/")

    assert "/outputs/" not in path
    assert path.endswith("/.local/settings.json")


def test_env_var_overrides_the_path(store_path):
    assert settings_store.settings_path() == str(store_path)


# ------------------------------------------------------------- save and load

def test_round_trip(store_path):
    settings_store.save({"NVIDIA_API_KEY": "nv-1", "HF_TOKEN": "hf-1"})

    assert settings_store.load() == {"NVIDIA_API_KEY": "nv-1", "HF_TOKEN": "hf-1"}


def test_only_allow_listed_keys_are_written(store_path):
    settings_store.save({"NVIDIA_API_KEY": "nv-1", "AWS_SECRET_ACCESS_KEY": "nope"})

    written = json.loads(store_path.read_text(encoding="utf-8"))
    assert written == {"NVIDIA_API_KEY": "nv-1"}


def test_empty_values_are_not_written(store_path):
    settings_store.save({"NVIDIA_API_KEY": "nv-1", "HF_TOKEN": ""})

    assert json.loads(store_path.read_text(encoding="utf-8")) == {"NVIDIA_API_KEY": "nv-1"}


def test_missing_file_loads_as_empty(store_path):
    assert not store_path.exists()
    assert settings_store.load() == {}


def test_corrupt_file_loads_as_empty_without_raising(store_path):
    store_path.write_text("{ this is not json", encoding="utf-8")

    assert settings_store.load() == {}


def test_non_object_json_loads_as_empty(store_path):
    store_path.write_text('["a", "list"]', encoding="utf-8")

    assert settings_store.load() == {}


def test_save_leaves_no_temp_file_behind(store_path):
    settings_store.save({"NVIDIA_API_KEY": "nv-1"})

    leftovers = [p.name for p in pathlib.Path(store_path.parent).glob("*.tmp")]
    assert leftovers == []


def test_save_reports_failure_instead_of_raising(tmp_path, monkeypatch):
    """A read-only disk degrades the Settings page; it must not 500 the API."""
    # A path whose parent is a FILE, so makedirs cannot succeed.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(blocker / "nested" / "settings.json"))

    assert settings_store.save({"NVIDIA_API_KEY": "nv-1"}) is False


# ------------------------------------------------------- the worker's view

def test_set_settings_env_persists(store_path, worker):
    worker.set_settings_env({"NVIDIA_API_KEY": "nv-1"})

    assert settings_store.load() == {"NVIDIA_API_KEY": "nv-1"}
    assert worker.get_settings_env() == {"NVIDIA_API_KEY": "nv-1"}


def test_empty_value_clears_the_override(store_path, worker):
    """Otherwise a stored "" would shadow a working .env key permanently."""
    worker.set_settings_env({"NVIDIA_API_KEY": "nv-1"})
    worker.set_settings_env({"NVIDIA_API_KEY": ""})

    assert worker.get_settings_env() == {}
    assert settings_store.load() == {}


def test_load_settings_env_restores_into_the_worker(store_path, worker):
    settings_store.save({"NVIDIA_API_KEY": "nv-1", "PEXELS_API_KEY": "px-1"})

    assert worker.load_settings_env() == 2
    assert worker.get_settings_env() == {"NVIDIA_API_KEY": "nv-1", "PEXELS_API_KEY": "px-1"}


def test_load_does_not_rewrite_the_file(store_path, worker):
    settings_store.save({"NVIDIA_API_KEY": "nv-1"})
    before = store_path.read_bytes()
    mtime = os.stat(store_path).st_mtime_ns

    worker.load_settings_env()

    assert store_path.read_bytes() == before
    assert os.stat(store_path).st_mtime_ns == mtime
