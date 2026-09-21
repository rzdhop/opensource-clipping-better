"""The settings endpoint must report the defaults the pipeline actually uses.

``GET /api/settings`` reports a default for every setting the dashboard shows.
Those defaults were written as literals in two places -- the route's
``env.get(NAME, fallback)`` and ``SettingsResponse``'s own field defaults -- and
they drifted: the route said the Whisper device defaults to ``cuda`` and the AI
provider to ``gemini``, while the pipeline had long since moved to ``auto``
(DEC-014) and ``nvidia`` (DEC-002). A fresh install therefore described itself
incorrectly on the Settings page.

Stdlib-only on purpose: CI installs pytest and nothing else (DEC-012), so the
declared model defaults are read with ``ast`` rather than by importing pydantic.
"""

import ast
import pathlib
import re

from clipping.config import AI_PROVIDER, WHISPER_DEVICE

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ROUTE = PROJECT_ROOT / "web/api/routes/settings.py"
MODELS = PROJECT_ROOT / "web/api/models.py"


def _response_model_defaults() -> dict:
    """``SettingsResponse`` field defaults, read without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SettingsResponse":
            out = {}
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.value is not None:
                        try:
                            out[stmt.target.id] = ast.literal_eval(stmt.value)
                        except (ValueError, SyntaxError):
                            pass
            return out
    raise AssertionError("SettingsResponse not found in models.py")


def test_the_reader_actually_sees_the_model():
    """A broken ast reader would make every assertion below vacuous."""
    defaults = _response_model_defaults()
    assert {"default_whisper_device", "default_ai_provider"} <= set(defaults)


def test_response_model_defaults_match_the_pipeline():
    defaults = _response_model_defaults()
    assert defaults["default_whisper_device"] == WHISPER_DEVICE == "auto"
    # "chain" since the three-pass analyzer landed; "nvidia" is now one of the
    # legacy single-request escape hatches, not the default. The == AI_PROVIDER
    # half is the invariant -- the API must not report a default the pipeline
    # does not use -- and it is what this line is really for.
    assert defaults["default_ai_provider"] == AI_PROVIDER == "chain"


def test_route_does_not_hardcode_a_stale_fallback():
    """The route now reads the constants; no literal may creep back in."""
    src = ROUTE.read_text(encoding="utf-8")

    stale = re.findall(
        r"""env\.get\(\s*"DEFAULT_(?:WHISPER_DEVICE|AI_PROVIDER)"\s*,\s*['"]([^'"]+)['"]""",
        src,
    )
    assert stale == [], (
        f"literal fallback(s) {stale} in settings.py -- import the constant from "
        "clipping.config instead so the two cannot drift again"
    )
    assert "from clipping.config import AI_PROVIDER, WHISPER_DEVICE" in src
