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
  ``clipping.aistory.defaults.EPISODE_TEMPLATE_IDS`` (the list moved to
  ``pages/story/episodeTemplates.js`` in plan 20 stage 1, shared with the
  new-story form; ScriptPane.jsx imports it).
- ``ScriptPane.jsx`` renders a ``story-step-error`` slot.
- ``Tabs.jsx`` carries the ARIA tabs roles.
- The new route is declared in ``App.jsx`` before its catch-alls.

Phase 4, stage 14 (assets + the header's Fast track) extends the same
guards onto the new controls, in the same two files plus ``EpisodeStudio.jsx``:

- ``StoryboardPane.jsx``'s ``assetsParams`` (``POST /steps/assets``'
  ``params``) against ``clipping.aistory.workflow.ASSETS_PARAMS``, and
  ``EpisodeStudio.jsx``'s ``fastTrackParams`` (``POST /steps/fast-track``'s)
  against ``clipping.aistory.workflow.FAST_TRACK_PARAMS``.
- Every ``patchEpisodeAssets(storyId, ep, [{ ... }])`` call site's item keys
  against ``AssetsShotPatch``.
- The ``shot:${ep}:${shot.shot_id}`` (the image, told apart from its
  ``:plan``) and ``line:${ep}:${line.line_id}`` regenerate targets against
  ``clipping.aistory.steps.regenerate.EPISODE_TARGETS``.
- ``StoryboardPane.jsx``'s ``AssetsHeader``/``ApproveAssets`` and
  ``EpisodeStudio.jsx``'s ``FastTrackHeader`` each render a
  ``story-step-error`` slot.
- ``ScriptPane.jsx``'s ``LineRow`` shows the word-timing source label and
  links the cast editor when a line has no pinned voice.

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
from clipping.aistory.steps import render as render_step

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
APP_JSX = PROJECT_ROOT / "web" / "dashboard" / "src" / "App.jsx"
TABS = PROJECT_ROOT / "web" / "dashboard" / "src" / "components" / "Tabs.jsx"
EPISODE_STUDIO = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "EpisodeStudio.jsx"
EPISODE_SRC = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
SCRIPT_PANE = EPISODE_SRC / "ScriptPane.jsx"
# Plan 20 stage 1: the episode formats, shared by ScriptPane.jsx and the new-story form.
EPISODE_TEMPLATES_JS = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episodeTemplates.js"
DURATION_BAR = EPISODE_SRC / "DurationBar.jsx"
# Dashboard overhaul stage 4 (DEC-256): StoryboardPane.jsx split into the
# storyboard/ folder; each check reads the file its code moved to.
STORYBOARD_PANE = EPISODE_SRC / "storyboard" / "StoryboardPane.jsx"
SHOT_CARD = EPISODE_SRC / "storyboard" / "ShotCard.jsx"
ASSETS_CARDS = EPISODE_SRC / "storyboard" / "AssetsCards.jsx"
PREVIEW_PANE = EPISODE_SRC / "PreviewPane.jsx"
INDEX_CSS = PROJECT_ROOT / "web" / "dashboard" / "src" / "index.css"


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
    # Phase 7 stage 4 (DEC-227): serial_60s_v2 joins the two v1 templates.
    # Plan 20 stage 1 (on purpose): serial_90s_v2 and narrated_drama_60s_v2 join them.
    # Plan 22 stage 3 (on purpose): confrontation_50s_v2 joins them.
    assert len(defaults.EPISODE_TEMPLATE_IDS) == 6


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


def test_check_params_send_exactly_the_check_only_run():
    """Plan 19 stage 3 (F6): the script header's "Check again" sends the
    check-only run (``workflow.SCRIPT_CHECK_PARAMS``, a closed list of its
    own: ``SCRIPT_PARAMS`` and its pin above are unchanged)."""
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    assert _object_literal_keys(src, "checkParams") == set(workflow.SCRIPT_CHECK_PARAMS) == {"check_only"}
    assert "const checkParams = { check_only: true }" in src
    assert "params: checkOnly ? checkParams : scriptParams" in src


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
    # Re-pointed (plan 20 stage 1): the literal moved out of ScriptPane.jsx
    # into episodeTemplates.js, which ScriptPane.jsx imports.
    src = EPISODE_TEMPLATES_JS.read_text(encoding="utf-8")
    match = re.search(r"export const EPISODE_TEMPLATES = \[(.*?)\]\n", src, re.DOTALL)
    assert match, "EPISODE_TEMPLATES not found in episodeTemplates.js"
    found = set(re.findall(r"id:\s*'([a-z0-9_]+)'", match.group(1)))
    assert found == set(defaults.EPISODE_TEMPLATE_IDS), (found, defaults.EPISODE_TEMPLATE_IDS)
    pane = SCRIPT_PANE.read_text(encoding="utf-8")
    assert "import { EPISODE_TEMPLATES } from '../episodeTemplates'" in pane
    assert "const EPISODE_TEMPLATES" not in pane  # one list, never a second copy


# --------------------------------------------------------------- error slot

def test_script_pane_renders_its_own_error_slot():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    assert "story-step-error" in src, "ScriptPane.jsx has no story-step-error slot"


# ------------------------------------------------------ consistency link colour (F5)

def test_consistency_issue_links_use_the_app_link_token():
    # The default browser link blue (rgb(0, 0, 238)) is unreadable on the
    # dark card; ScriptPane must not invent a new colour literal, so the
    # link is only ever styled through index.css, keyed off the same
    # --accent-hover token every other story link uses
    # (.story-ready-open-episode).
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    assert '<a href={`#scene-${issue.scene_id}`}>' in src
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert ".story-script-consistency a" in css
    rule = css.split(".story-script-consistency a", 1)[1].split("}", 1)[0]
    assert "var(--accent-hover)" in rule
    assert re.search(r"#[0-9a-fA-F]{3,6}", rule) is None, "a new colour literal was added instead of a token"


# ------------------------------------------------------- completeness (F6)

def test_approve_completeness_is_keyed_on_script_state_not_missing():
    # state.missing also lists "consistency_check" once the script is fully
    # written but the report is stale or missing (workflow.script_missing),
    # so `state.missing.length === 0` read a stale report as "not complete
    # yet" instead of "Check the consistency first."
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    approve_body = src.split("function ApproveScript", 1)[1]
    assert "const complete = state.script === 'complete' || state.script === 'approved'" in approve_body
    assert "state.missing.length === 0" not in approve_body


def test_measure_voices_stays_disabled_while_only_the_check_is_missing():
    # script.measure_estimate (GET /estimate/script?measure=1) never counts
    # the consistency check (E4) the runner would make first for a stale
    # report (steps/script.py _Run.run always runs beat_sheet/body/framing/
    # consistency before measure()) -- its chip never shows that call, so
    # the UI must not enable Measure from a completeness check that ignores
    # the report. Measure is only enabled once the script is complete AND
    # the report is not stale/missing.
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    measure_body = src.split("function MeasureVoices", 1)[1].split("function ApproveScript", 1)[0]
    assert "const ready = scriptComplete && !checkNeeded" in measure_body
    # Plan 11 stage 11's browser-check fix round added a fourth disabling
    # condition (a blocked estimate, estimateError) alongside these three;
    # `ready`'s own gating is unchanged and still required.
    assert "disabled={!ready || busy || running || Boolean(estimateError)}" in measure_body
    assert "Check the consistency first." in measure_body
    assert "Finish the script first." in measure_body
    assert "complete={episode.state.missing.length === 0}" not in src
    assert "scriptComplete={episode.state.script === 'complete' || episode.state.script === 'approved'}" in src
    assert "checkNeeded={episode.state.report === 'stale' || episode.state.report === 'none'}" in src


# --------------------------------------------------------- estimate refusal (F8)

def test_a_failed_estimate_shows_in_the_step_error_slot_and_disables_write():
    # Found live: /estimate/script?ep=2 409s (its recap has not arrived
    # yet), but the header's `.catch(() => setEstimate(null))` swallowed the
    # refusal -- the chip was stuck at "estimating..." forever and Write
    # stayed enabled, so the refusal only surfaced after an actual click.
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    header_body = src.split("function ScriptHeader", 1)[1].split("function ConsistencyPanel", 1)[0]
    assert ".catch(() => setEstimate(null))" not in header_body
    assert "setEstimateError(err.message)" in header_body
    assert "disabled={busy || running || Boolean(estimateError)}" in header_body
    assert "{!estimateError && <EstimateChip estimate={estimate} />}" in header_body
    assert "message={estimateError || error}" in header_body


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


# --------------------------------------------------- stage 11: StoryboardPane.jsx

def test_the_readers_see_storyboard_things():
    """A broken regex would make every assertion below pass for free."""
    assert len(_class_fields("StoryboardPatchRequest")) >= 3
    assert len(_class_fields("StoryboardShotPatch")) >= 5
    assert len(_class_fields("StoryboardTransitionPatch")) >= 1
    assert len(workflow.STORYBOARD_PARAMS) == 1
    assert len(schemas.FRAMINGS) >= 5
    assert len(schemas.CAMERA_MOTIONS) >= 5
    assert len(schemas.MODIFIERS) >= 1
    assert len(schemas.TRANSITIONS) >= 5


def test_fast_and_plan_params_together_equal_workflow_storyboard_params():
    src = STORYBOARD_PANE.read_text(encoding="utf-8")
    fast_params = _object_literal_keys(src, "fastParams")
    plan_params = _object_literal_keys(src, "planParams")
    declared = set(workflow.STORYBOARD_PARAMS)
    assert fast_params <= declared, (fast_params, declared)
    assert plan_params <= declared, (plan_params, declared)
    assert fast_params | plan_params == declared, (fast_params | plan_params, declared)


def test_fast_params_sends_fast_true():
    src = STORYBOARD_PANE.read_text(encoding="utf-8")
    assert "const fastParams = { fast: true }" in src


def test_plan_params_sends_nothing():
    src = STORYBOARD_PANE.read_text(encoding="utf-8")
    assert "const planParams = {}" in src


# ------------------------------------------------ patchEpisodeStoryboard call sites

def _patch_episode_storyboard_call_sites() -> list[str]:
    """The literal top-level key of every `patchEpisodeStoryboard(storyId, ep,
    { ... })` call site in the episode pages."""
    keys = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"patchEpisodeStoryboard\(\s*storyId,\s*ep,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
            keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_episode_storyboard_call_sites():
    assert len(_patch_episode_storyboard_call_sites()) >= 6


def test_every_patch_episode_storyboard_call_site_sends_a_declared_field():
    declared = _class_fields("StoryboardPatchRequest")
    sent = set(_patch_episode_storyboard_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchEpisodeStoryboard(...) call sites send a key StoryboardPatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


def test_every_shots_item_sent_is_a_declared_storyboard_shot_patch_field():
    declared = _class_fields("StoryboardShotPatch")
    found = set()
    for path in EPISODE_SRC.rglob("*.jsx"):
        found |= _nested_item_keys(path.read_text(encoding="utf-8"), "shots")
    assert found, "no `shots: [{ ... }]` item found in the episode pages"
    undeclared = found - declared
    assert undeclared == set(), (found, declared)
    assert "shot_id" in found


def test_every_transitions_item_sent_is_a_declared_storyboard_transition_patch_field():
    declared = _class_fields("StoryboardTransitionPatch")
    found = set()
    for path in EPISODE_SRC.rglob("*.jsx"):
        found |= _nested_item_keys(path.read_text(encoding="utf-8"), "transitions")
    assert found, "no `transitions: [{ ... }]` item found in the episode pages"
    undeclared = found - declared
    assert undeclared == set(), (found, declared)
    assert "after" in found


# ------------------------------------------------------------------- closed lists

def _js_list_literal(src: str, const_name: str) -> set[str]:
    match = re.search(rf"const {const_name} = \[(.*?)\]\n", src, re.DOTALL)
    assert match, f"{const_name} not found"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_framings_constant_equals_the_schema_exactly():
    src = SHOT_CARD.read_text(encoding="utf-8")
    found = _js_list_literal(src, "FRAMINGS")
    assert found == set(schemas.FRAMINGS), (found, schemas.FRAMINGS)


def test_camera_motions_constant_equals_the_schema_exactly():
    src = SHOT_CARD.read_text(encoding="utf-8")
    found = _js_list_literal(src, "CAMERA_MOTIONS")
    assert found == set(schemas.CAMERA_MOTIONS), (found, schemas.CAMERA_MOTIONS)


def test_modifiers_constant_equals_the_schema_exactly():
    src = SHOT_CARD.read_text(encoding="utf-8")
    found = _js_list_literal(src, "MODIFIERS")
    assert found == set(schemas.MODIFIERS), (found, schemas.MODIFIERS)


def test_transitions_constant_equals_the_schema_exactly():
    src = SHOT_CARD.read_text(encoding="utf-8")
    found = _js_list_literal(src, "TRANSITIONS")
    assert found == set(schemas.TRANSITIONS), (found, schemas.TRANSITIONS)


# ------------------------------------------------------- shot:<ep>:<shot_id>:plan

def _shot_plan_regenerate_targets() -> set[str]:
    seg = r"\$\{[^`}]*\}"
    pattern = rf"`(shot:{seg}:{seg}:plan)`"
    literals: list[str] = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        literals += re.findall(pattern, path.read_text(encoding="utf-8"))
    assert literals, "no shot:<ep>:<shot_id>:plan regenerate target found in the episode pages"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_shot_plan_regenerate_target_matches_the_grammar_shape():
    templates = _shot_plan_regenerate_targets()
    assert templates == {"shot:<x>:<x>:plan"}
    normalized_shapes = {re.sub(r"<[a-z_]+>", "<x>", shape) for shape in regenerate_step.EPISODE_TARGETS}
    assert templates <= normalized_shapes


# --------------------------------------------------------------- error slot

def test_storyboard_pane_renders_its_own_error_slot():
    src = STORYBOARD_PANE.read_text(encoding="utf-8")
    assert "story-step-error" in src, "StoryboardPane.jsx has no story-step-error slot"


# ============================================================
# Phase 4, stage 14: assets (StoryboardPane, ScriptPane) + the header's Fast
# track (EpisodeStudio). Every test here is shown failing against the parent
# commit (this stage's own start), where none of these controls exist yet.
# ============================================================

# ------------------------------------------------------------- non-vacuity

def test_the_readers_see_phase4_things():
    """A broken regex would make every assertion below pass for free."""
    assert len(_class_fields("AssetsShotPatch")) >= 2
    assert len(_class_fields("AssetsPatchRequest")) >= 1
    assert len(workflow.ASSETS_PARAMS) == 2
    # Phase 7 follow-up stage C: storyboard and stop_at_keyframes; plan 19 stage 3, re-pinned on purpose:
    # stop_on_script_issues.
    assert len(workflow.FAST_TRACK_PARAMS) == 3


# --------------------------------------------------- StoryboardPane.jsx: assetsParams

def test_assets_params_equal_workflow_assets_params():
    src = ASSETS_CARDS.read_text(encoding="utf-8")
    assets_params = _object_literal_keys(src, "assetsParams")
    declared = set(workflow.ASSETS_PARAMS)
    assert assets_params == declared, (assets_params, declared)


# --------------------------------------------------- EpisodeStudio.jsx: fastTrackParams

def test_fast_track_params_equal_workflow_fast_track_params():
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    fast_track_params = _object_literal_keys(src, "fastTrackParams")
    declared = set(workflow.FAST_TRACK_PARAMS)
    assert fast_track_params == declared, (fast_track_params, declared)


# ------------------------------------------------ patchEpisodeAssets call sites

def _patch_episode_assets_call_sites() -> set[str]:
    """Every key used inside a ``patchEpisodeAssets(storyId, ep, [{ ... }])``
    call site's shot item, across the episode pages. Unlike
    ``patchEpisodeScript``/``patchEpisodeStoryboard`` (a payload object with
    several optional top-level keys), ``AssetsPatchRequest`` has exactly one
    field (``shots``), so the JS helper takes the list directly (api.js's own
    docstring) rather than a ``{shots: [...]}`` wrapper -- there is nothing
    else for a caller to send by mistake."""
    keys: set[str] = set()
    for path in EPISODE_SRC.rglob("*.jsx"):
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"patchEpisodeAssets\(\s*storyId,\s*ep,\s*\[\{([^}]*)\}\]\)", src):
            keys |= set(re.findall(r"([a-z_]+):", match.group(1)))
    return keys


def test_the_readers_see_patch_episode_assets_call_sites():
    assert len(_patch_episode_assets_call_sites()) >= 1


def test_every_patch_episode_assets_call_site_sends_a_declared_field():
    declared = _class_fields("AssetsShotPatch")
    sent = _patch_episode_assets_call_sites()
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchEpisodeAssets(...) call sites send a key AssetsShotPatch does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )
    assert "shot_id" in sent
    assert "locked" in sent


# ------------------------------------------- shot:<ep>:<shot_id> / line:<ep>:<line_id>

def _shot_image_and_line_regenerate_targets() -> set[str]:
    """Every ``shot:${...}:${...}`` (the image -- told apart from its
    ``:plan``, which this pattern does not match: a ``:plan`` literal has a
    third interpolation-free segment before the closing backtick) and
    ``line:${...}:${...}`` template literal used as a regenerate target."""
    seg = r"\$\{[^`}]*\}"
    pattern = rf"`((?:shot|line):{seg}:{seg})`"
    literals: list[str] = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        literals += re.findall(pattern, path.read_text(encoding="utf-8"))
    assert literals, "no shot:<ep>:<shot_id> / line:<ep>:<line_id> regenerate target found in the episode pages"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_shot_image_and_line_regenerate_targets_match_the_grammar_shapes():
    templates = _shot_image_and_line_regenerate_targets()
    assert templates == {"shot:<x>:<x>", "line:<x>:<x>"}
    normalized_shapes = {re.sub(r"<[a-z_]+>", "<x>", shape) for shape in regenerate_step.EPISODE_TARGETS}
    assert templates <= normalized_shapes


# --------------------------------------------------------------- error slots

def test_assets_header_renders_its_own_error_slot():
    src = ASSETS_CARDS.read_text(encoding="utf-8")
    body = src.split("function AssetsHeader", 1)[1].split("function ApproveAssets", 1)[0]
    assert "story-step-error" in body


def test_approve_assets_renders_its_own_error_slot():
    src = ASSETS_CARDS.read_text(encoding="utf-8")
    body = src.split("function ApproveAssets", 1)[1].split("function StoryboardPane", 1)[0]
    assert "story-step-error" in body


def test_fast_track_header_renders_its_own_error_slot():
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    body = src.split("function FastTrackHeader", 1)[1].split("export default function EpisodeStudio", 1)[0]
    assert "story-step-error" in body


# --------------------------------------------------------- ScriptPane.jsx: LineRow

def test_line_row_shows_the_word_timing_source_label():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    body = src.split("function LineRow", 1)[1].split("function SceneCard", 1)[0]
    assert "approximate timing" in body
    assert "wordsSource" in body


def test_line_row_links_the_cast_editor_when_a_line_has_no_pinned_voice():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    body = src.split("function LineRow", 1)[1].split("function SceneCard", 1)[0]
    assert "unvoicedReason" in body
    assert "to={`/story/${storyId}`}" in body


def test_line_row_offers_its_own_voice_regenerate():
    src = SCRIPT_PANE.read_text(encoding="utf-8")
    body = src.split("function LineRow", 1)[1].split("function SceneCard", 1)[0]
    assert "target: `line:${ep}:${line.line_id}`" in body


# ============================================================
# Phase 4, stage 15: PreviewPane (render, metadata, the ledger, the cover and
# the download link). Every test here is shown failing against the parent
# commit (this stage's own start), where PreviewPane.jsx does not exist yet
# and EpisodeStudio.jsx still renders the phase-3 placeholder inline.
# ============================================================

# ------------------------------------------------------------- non-vacuity

def test_the_readers_see_phase4_stage15_things():
    """A broken regex would make every assertion below pass for free."""
    assert len(workflow.RENDER_PARAMS) == 3  # phase 6 stage 9: fill_failed_with_motion
    assert len(workflow.METADATA_PARAMS) == 0
    assert len(render_step.SUBTITLE_CHOICES) == 4
    assert len(schemas.PLATFORMS) == 3


def test_preview_pane_file_exists():
    assert PREVIEW_PANE.exists(), "PreviewPane.jsx (stage 15) is missing"


# --------------------------------------------------------- PreviewPane.jsx: renderParams

def test_render_params_is_a_subset_of_the_workflow_render_params():
    # Only `subtitles` is exposed as a control (no encoder picker, per the
    # plan's Preview pane bullet); `encoder` is left unsent so the render
    # step defaults it -- a subset of the closed list, not the full set (the
    # scriptParams/measureParams pattern's union check does not apply here:
    # there is only the one render call site, and it never sends `encoder`).
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    render_params = _object_literal_keys(src, "renderParams")
    declared = set(workflow.RENDER_PARAMS)
    assert render_params <= declared, (render_params, declared)
    assert "subtitles" in render_params


def test_metadata_params_sends_nothing():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    assert "const metadataParams = {}" in src
    assert set(workflow.METADATA_PARAMS) == set()


# ------------------------------------------------------- closed lists (subtitles, platforms)

def test_subtitle_modes_constant_equals_the_render_step_choices():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    match = re.search(r"const SUBTITLE_MODES = \[(.*?)\]\n", src, re.DOTALL)
    assert match, "SUBTITLE_MODES not found in PreviewPane.jsx"
    found = set(re.findall(r"id:\s*'([a-z_]+)'", match.group(1)))
    assert found == set(render_step.SUBTITLE_CHOICES), (found, render_step.SUBTITLE_CHOICES)


def test_platforms_constant_equals_the_schema_exactly():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    found = _js_list_literal(src, "PLATFORMS")
    assert found == set(schemas.PLATFORMS), (found, schemas.PLATFORMS)


# ------------------------------------------------------- metadata:<ep>:<platform>

def _metadata_regenerate_targets() -> set[str]:
    """Every ``metadata:${...}:${...}`` template literal used as a
    regenerate target in the episode pages, interpolations normalized to
    ``<x>`` -- same normalization as ``_shot_image_and_line_regenerate_targets``."""
    seg = r"\$\{[^`}]*\}"
    pattern = rf"`(metadata:{seg}:{seg})`"
    literals: list[str] = []
    for path in EPISODE_SRC.rglob("*.jsx"):
        literals += re.findall(pattern, path.read_text(encoding="utf-8"))
    assert literals, "no metadata:<ep>:<platform> regenerate target found in the episode pages"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_metadata_regenerate_target_matches_the_grammar_shape():
    # regenerate_step.EPISODE_TARGETS spells the platform part as a pipe
    # union ("metadata:<ep>:tiktok|shorts|reels"), not a placeholder, so it
    # cannot be compared to the JS template literal by the same
    # placeholder-normalization the shot/line test above uses. Checked
    # instead: the one template literal is `metadata:${ep}:${platform}` --
    # both segments interpolated -- and `platform` only ever iterates over
    # PLATFORMS above, which is asserted equal to schemas.PLATFORMS.
    templates = _metadata_regenerate_targets()
    assert templates == {"metadata:<x>:<x>"}
    grammar = next(shape for shape in regenerate_step.EPISODE_TARGETS if shape.startswith("metadata:"))
    assert grammar == f"metadata:<ep>:{'|'.join(schemas.PLATFORMS)}"
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    assert "target: `metadata:${ep}:${platform}`" in src
    assert "PLATFORMS.filter((platform) =>" in src or "PLATFORMS.map((platform) =>" in src


# --------------------------------------------------------------- error slots

def test_render_header_renders_its_own_error_slot():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    body = src.split("function RenderHeader", 1)[1].split("function RenderMedia", 1)[0]
    assert "story-step-error" in body


def test_metadata_header_renders_its_own_error_slot():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    body = src.split("function MetadataHeader", 1)[1].split("function PlatformCard", 1)[0]
    assert "story-step-error" in body


# ------------------------------------------------------------------ media

def test_video_uses_the_signed_media_url_directly_and_is_keyed_on_the_output_sha():
    # render.media.video_url/cover_url are already-signed URLs (DEC-163),
    # unlike the shot/voice blob routes (fetchShotImageUrl/fetchEpisodeVoiceUrl),
    # which need the bearer header and so are fetched into a blob URL first.
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    body = src.split("function RenderMedia", 1)[1].split("function MetadataHeader", 1)[0]
    assert "src={render.media.video_url}" in body
    assert 'key={render.output.sha256}' in body
    assert "playsInline" in body
    assert 'preload="metadata"' in body
    assert "fetchStoryMediaUrl(" not in body
    assert "fetchShotImageUrl(" not in body


def test_download_link_appends_download_flag_to_the_signed_url():
    # Signed URLs carry ?exp=&sig=; with no token the page hands out the plain
    # path (DEC-173), so the flag's separator follows the URL, as in JobDetail.
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    assert "render.media.video_url.includes('?') ? '&' : '?'}download=1" in src
    assert "href={downloadUrl}" in src
    assert "&download=1`}" not in src
    assert " download>" in src or " download\n" in src


def test_media_on_error_recovers_by_refetching_the_episode_page():
    # Same reasoning as JobDetail.jsx's recoverExpiredMedia: an expiring
    # signed URL can go stale in a tab left open a while, so onError re-fetches
    # the episode page rather than leaving the player stalled.
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    assert "onError={recoverMedia}" in src
    assert "if (recovered) return" in src


# ------------------------------------------------------------------- copy button

def test_copy_button_uses_the_clipboard_api_with_a_manual_select_fallback():
    src = PREVIEW_PANE.read_text(encoding="utf-8")
    body = src.split("function CopyButton", 1)[1].split("function RenderHeader", 1)[0]
    assert "navigator.clipboard.writeText(text)" in body
    assert "window.isSecureContext" in body
    assert "area.select()" in body


# --------------------------------------------------------------- EpisodeStudio wiring

def test_episode_studio_renders_preview_pane_with_its_props():
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    assert "import PreviewPane from './episode/PreviewPane'" in src
    assert "function PreviewPane(" not in src, "the phase-3 inline placeholder must be removed"
    preview_block = src.split("preview: (", 1)[1].split("\n  }", 1)[0]
    assert "<PreviewPane" in preview_block
    for prop in ("episode={episode}", "story={story}", "storyId={storyId}", "ep={epNumber}"):
        assert prop in preview_block, preview_block
