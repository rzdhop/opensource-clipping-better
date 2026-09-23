"""/api/health reports the version the app actually runs.

It used to hardcode "1.12.0" while the package, the OpenAPI title and GET /api
all said 3.0.0, so anything polling health for monitoring read a version that
had not shipped for several releases.

The guard reads the route's source so it runs in the pytest-only CI
environment; the live check needs FastAPI and skips without it.
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SETTINGS_ROUTE = PROJECT_ROOT / "web" / "api" / "routes" / "settings.py"


def _health_response_call() -> ast.Call:
    tree = ast.parse(SETTINGS_ROUTE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "health_check":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call)
                        and getattr(sub.func, "id", None) == "SystemHealthResponse"):
                    return sub
    raise AssertionError("health_check no longer builds a SystemHealthResponse")


def test_the_health_version_is_not_a_literal():
    version = next(kw.value for kw in _health_response_call().keywords
                   if kw.arg == "version")
    assert not isinstance(version, ast.Constant), (
        f"/api/health hardcodes version={version.value!r}; "
        "use clipping.__version__ so it cannot go stale")


def test_the_health_version_comes_from_the_package():
    version = next(kw.value for kw in _health_response_call().keywords
                   if kw.arg == "version")
    assert isinstance(version, ast.Name) and version.id == "__version__"
    source = SETTINGS_ROUTE.read_text(encoding="utf-8")
    assert "from clipping import __version__" in source


def test_health_answers_with_the_package_version(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    import clipping
    from web.api import auth
    from web.api.app import app

    monkeypatch.setenv("API_TOKEN", "test-token-12345")
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)

    with TestClient(app) as client:
        body = client.get("/api/health").json()
    monkeypatch.setattr(auth, "_TOKEN", None)

    assert body["version"] == clipping.__version__
