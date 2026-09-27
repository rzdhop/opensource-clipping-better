"""The EpisodeStudio dashboard page must only send fields the backend
declares, and its regenerate targets and closed lists must match the
backend's grammar exactly (phase 3, stage 10; spec 9.2, 10).

Same reasoning as ``tests/test_story_payload_contract.py`` (phase 1/2's
version of this guard), extended to the episode page:

- ``ScriptPane.jsx``'s ``scriptParams``/``measureParams`` (sent as
  ``POST /steps/script``'s ``params``) against
  ``clipping.aistory.workflow.SCRIPT_PARAMS``.
- Every ``patchEpisodeScript(storyId, ep, { ... })`` call site's top-level
  keys against ``ScriptPatchRequest``, and its ``lines``/``scenes`` items'
  keys against ``ScriptLinePatch``/``ScriptScenePatch``.
- The ``approveStoryDoc(storyId, \\`script:${ep}\\`, { ... })`` call site's
  keys against ``StoryApproveRequest``.
- The ``scene:${ep}:${sceneId}`` / ``hook:${ep}`` / ``cliffhanger:${ep}`` /
  ``teaser:${ep}`` regenerate targets against
  ``clipping.aistory.steps.regenerate.EPISODE_TARGETS``.
- The JS ``EMOTIONS`` constant against ``clipping.aistory.schemas.EMOTIONS``.
- The JS ``EPISODE_TEMPLATES`` ids against
  ``clipping.aistory.defaults.EPISODE_TEMPLATE_IDS``.
- ``ScriptPane.jsx`` renders a ``story-step-error`` slot.
- ``Tabs.jsx`` carries the ARIA tabs roles.
- The new route is declared in ``App.jsx`` before its catch-alls.

Stdlib + pytest only (DEC-012): ``clipping.aistory.workflow``,
``clipping.aistory.schemas``, ``clipping.aistory.defaults`` and
``clipping.aistory.steps.regenerate`` are stdlib-only modules, imported
directly as the sibling contract test does; the dashboard sources are read as
text and with ``ast`` over ``models.py``, never through npm or a JS runtime.

Every test here is shown failing against the parent commit, where none of
these dashboard files exist yet.
"""

from __future__ import annotations

import ast
import pathlib
import re

from clipping.aistory import defaults, schemas, workflow
from clipping.aistory.steps import regenerate as regenerate_step

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
APP_JSX = PROJECT_ROOT / "web" / "dashboard" / "src" / "App.jsx"
TABS = PROJECT_ROOT / "web" / "dashboard" / "src" / "components" / "Tabs.jsx"
EPISODE_STUDIO = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "EpisodeStudio.jsx"
EPISODE_SRC = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
SCRIPT_PANE = EPISODE_SRC / "ScriptPane.jsx"
DURATION_BAR = EPISODE_SRC / "DurationBar.jsx"
STORYBOARD_PANE = EPISODE_SRC / "StoryboardPane.jsx"


def _class_fields(name: str) -> set[str]:
    """A pydantic model's field names, read without importing pydantic --
    same helper as test_story_payload_contract.py's, duplicated so this file
    stays independently readable and importable."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"{name} not found in models.py")


# ------------------------------------------------------------- non-vacuity

def test_the_readers_see_something():
    """A broken regex would make every assertion below pass for free."""
    assert len(_class_fields("ScriptPatchRequest")) >= 5
    assert len(_class_fields("ScriptLinePatch")) >= 4
    assert len(_class_fields("ScriptScenePatch")) >= 2
    assert len(_class_fields("StoryApproveRequest")) >= 1
    assert len(workflow.SCRIPT_PARAMS) == 1
    assert len(schemas.EMOTIONS) >= 5
    assert len(defaults.EPISODE_TEMPLATE_IDS) == 2


# -------------------------------------------------- ScriptPane.jsx: scriptParams

def _object_literal_keys(src: str, const_name: str) -> set[str]:
    match = re.search(rf"const {const_name} = \{{([^}}]*)\}}", src)
    assert match, f"{const_name} object literal not found"
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


def test_script_and_measure_params_together_equal_workflow_script_params():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    script_params = _object_literal_keys(src, "scriptParams")
    measure_params = _object_literal_keys(src, "measureParams")
    declared = set(workflow.SCRIPT_PARAMS)
    assert script_params <= declared, (script_params, declared)
    assert measure_params <= declared, (measure_params, declared)
    assert script_params | measure_params == declared, (script_params | measure_params, declared)


def test_measure_params_sends_measure_voices_true():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    assert "const measureParams = { measure_voices: true }" in src


# -------------------------------------------------- ScriptPane.jsx: patchEpisodeScript

def _patch_episode_script_call_sites() -> list[str]:
    """The literal top-level key of every `patchEpisodeScript(storyId, ep,
    { ... })` call site in the episode pages."""
    keys = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"patchEpisodeScript\(\s*storyId,\s*ep,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
            keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_episode_script_call_sites():
    assert len(_patch_episode_script_call_sites()) >= 6


def test_every_patch_episode_script_call_site_sends_a_declared_field():
    declared = _class_fields("ScriptPatchRequest")
    sent = set(_patch_episode_script_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchEpisodeScript(...) call sites send a key ScriptPatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


def _nested_item_keys(src: str, list_key: str) -> set[str]:
    """Every key used inside a `<list_key>: [{ ... }]` item literal of every
    `patchEpisodeScript(...)` call site in *src*."""
    keys = set()
    for match in re.finditer(rf"{list_key}:\s*\[\{{([^}}]*)\}}\]", src):
        keys |= set(re.findall(r"([a-z_]+):", match.group(1)))
    return keys


def test_every_lines_item_sent_is_a_declared_script_line_patch_field():
    declared = _class_fields("ScriptLinePatch")
    found = set()
    for path in EPISODE_SRC.rglob("*.jsx"):
        found |= _nested_item_keys(path.read_text(encoding="utf-8"), "lines")
    assert found, "no `lines: [{ ... }]` item found in the episode pages"
    undeclared = found - declared
    assert undeclared == set(), (found, declared)
    assert "line_id" in found


def test_every_scenes_item_sent_is_a_declared_script_scene_patch_field():
    declared = _class_fields("ScriptScenePatch")
    found = set()
    for path in EPISODE_SRC.rglob("*.jsx"):
        found |= _nested_item_keys(path.read_text(encoding="utf-8"), "scenes")
    assert found, "no `scenes: [{ ... }]` item found in the episode pages"
    undeclared = found - declared
    assert undeclared == set(), (found, declared)
    assert "scene_id" in found


# ------------------------------------------------------- approveStoryDoc(script:<ep>)

def test_approve_script_call_site_sends_only_declared_fields():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    match = re.search(
        r"approveStoryDoc\(\s*storyId,\s*`script:\$\{ep\}`,\s*\{([^}]*)\}\s*\)", src)
    assert match, "no approveStoryDoc(storyId, `script:${ep}`, { ... }) call site found in ScriptPane.jsx"
    sent = set(re.findall(r"([a-z_]+):", match.group(1)))
    declared = _class_fields("StoryApproveRequest")
    undeclared = sent - declared
    assert undeclared == set(), (sent, declared)


# ------------------------------------------------------- episode regenerate targets

def _episode_regenerate_target_templates() -> set[str]:
    """Every `scene:${...}:${...}` / `hook:${...}` / `cliffhanger:${...}` /
    `teaser:${...}` template literal used as a regenerate target across the
    episode pages, interpolations normalized to `<x>`."""
    seg = r"\$\{[^`}]*\}"
    pattern = rf"`((?:scene|hook|cliffhanger|teaser):{seg}(?::{seg})?)`"
    literals: list[str] = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        literals += re.findall(pattern, path.read_text(encoding="utf-8"))
    assert literals, "no scene:/hook:/cliffhanger:/teaser: regenerate target found in the episode pages"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def _normalized_episode_target_shapes() -> set[str]:
    return {re.sub(r"<[a-z_]+>", "<x>", shape) for shape in regenerate_step.EPISODE_TARGETS}


def test_episode_regenerate_targets_match_the_grammar_shapes():
    templates = _episode_regenerate_target_templates()
    assert templates == {"scene:<x>:<x>", "hook:<x>", "cliffhanger:<x>", "teaser:<x>"}
    assert templates <= _normalized_episode_target_shapes()


# ------------------------------------------------------------------- closed lists

def test_emotions_constant_equals_the_schema_exactly():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    match = re.search(r"const EMOTIONS = \[(.*?)\]", src, re.DOTALL)
    assert match, "EMOTIONS not found in ScriptPane.jsx"
    found = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert found == set(schemas.EMOTIONS), (found, schemas.EMOTIONS)


def test_episode_templates_ids_equal_the_defaults_exactly():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    match = re.search(r"const EPISODE_TEMPLATES = \[(.*?)\]\n", src, re.DOTALL)
    assert match, "EPISODE_TEMPLATES not found in ScriptPane.jsx"
    found = set(re.findall(r"id:\s*'([a-z0-9_]+)'", match.group(1)))
    assert found == set(defaults.EPISODE_TEMPLATE_IDS), (found, defaults.EPISODE_TEMPLATE_IDS)


# --------------------------------------------------------------- error slot

def test_script_pane_renders_its_own_error_slot():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    assert "story-step-error" in src, "ScriptPane.jsx has no story-step-error slot"


# ------------------------------------------------------------------------ Tabs

def test_tabs_component_carries_the_aria_roles():
    src = TABS.read_text(encoding="utf-8")
    for needle in ('role="tablist"', 'role="tab"', 'role="tabpanel"', "aria-selected", "aria-controls", "tabIndex"):
        assert needle in src, f"Tabs.jsx is missing {needle!r}"


def test_tabs_component_handles_arrow_home_end_keys():
    src = TABS.read_text(encoding="utf-8")
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert f"'{key}'" in src, f"Tabs.jsx does not handle the {key} key"


# ------------------------------------------------------------------------ App.jsx

def test_episode_route_is_declared_before_the_catch_alls():
    src = APP_JSX.read_text(encoding="utf-8")
    episode_idx = src.index('path="/story/:storyId/episodes/:ep"')
    story_catch_idx = src.index('path="/story/*"')
    root_catch_idx = src.index('path="*"')
    assert episode_idx < story_catch_idx < root_catch_idx, (
        "the episode route must be declared before /story/* and the root catch-all"
    )


# ------------------------------------------------------------------ StoryboardPane

def test_storyboard_pane_placeholder_exists_for_stage_11():
    assert STORYBOARD_PANE.exists(), "StoryboardPane.jsx (a placeholder for stage 11) is missing"


def test_duration_bar_file_exists():
    assert DURATION_BAR.exists(), "DurationBar.jsx is missing"


def test_episode_studio_page_exists():
    assert EPISODE_STUDIO.exists(), "EpisodeStudio.jsx is missing"
