"""The AI Story dashboard pages must only send fields the backend declares
(phase 1, stage 11; spec 10).

Same reasoning as ``tests/test_dashboard_payload_contract.py``: a request
model is default-strict, so a key it does not declare is silently dropped and
the control that sent it does nothing. This file is the story pages' half of
that guard:

- ``NewStoryWizard.jsx``'s ``createFields`` (sent to ``POST /api/stories``)
  against ``StoryCreateRequest``.
- ``StyleStep.jsx``'s ``styleParams`` (sent as ``POST /steps/style``'s
  ``params``) against ``clipping.aistory.workflow.STYLE_PARAMS``.
- Every ``patchStory(storyId, { ... })`` call site across the story pages
  against ``StoryPatchRequest``.
- The Bible step's regenerate targets against
  ``clipping.aistory.prompts.REGENERATE_TARGETS``.
- ``chooseConcept`` sends ``concept_id``.

Stdlib + pytest only (DEC-012): ``clipping.aistory.workflow`` and
``clipping.aistory.prompts`` are stdlib-only modules (no fastapi/pydantic), so
they are imported directly, same as ``tests/test_stories_api.py`` already
does for ``clipping.aistory.{defaults,schemas,templates}``. The dashboard
sources are read as text and with ``ast`` over ``models.py``, never through
npm or a JS runtime, so this runs in CI (which installs pytest and nothing
else).

Every test here is shown failing against the parent commit (``f6b96b0``),
where none of these dashboard files exist yet.
"""

from __future__ import annotations

import ast
import pathlib
import re

from clipping.aistory import prompts, workflow

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
STORY_SRC = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story"
NEW_STORY_WIZARD = STORY_SRC / "NewStoryWizard.jsx"
STYLE_STEP = STORY_SRC / "steps" / "StyleStep.jsx"
BIBLE_STEP = STORY_SRC / "steps" / "BibleStep.jsx"
CONCEPTS_STEP = STORY_SRC / "steps" / "ConceptsStep.jsx"


def _class_fields(name: str) -> set[str]:
    """A pydantic model's field names, read without importing pydantic."""
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
    assert len(_class_fields("StoryCreateRequest")) >= 4
    assert len(_class_fields("StoryPatchRequest")) >= 8
    assert len(workflow.STYLE_PARAMS) == 3
    assert len(prompts.REGENERATE_TARGETS) == 5


# --------------------------------------------------- NewStoryWizard.jsx: createFields

def _create_fields() -> set[str]:
    src = NEW_STORY_WIZARD.read_text(encoding="utf-8")
    match = re.search(r"const createFields = \{(.*?)\n      \}", src, re.DOTALL)
    assert match, "createFields object literal not found in NewStoryWizard.jsx"
    # 8-space indent is exactly the object's own top level; the nested
    # generation_profile literal is indented two spaces further in.
    return set(re.findall(r"^ {8}([a-z0-9_]+)[,:]", match.group(1), re.MULTILINE))


def test_create_fields_matches_story_create_request_exactly():
    sent = _create_fields()
    declared = _class_fields("StoryCreateRequest")
    assert sent == declared, (sent, declared)


# ------------------------------------------------------- StyleStep.jsx: styleParams

def _style_params() -> set[str]:
    src = STYLE_STEP.read_text(encoding="utf-8")
    match = re.search(r"const styleParams = \{([^}]*)\}", src)
    assert match, "styleParams object literal not found in StyleStep.jsx"
    # Each entry is `key: value` or a bare shorthand `key` (no colon).
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


def test_style_params_matches_workflow_style_params_exactly():
    sent = _style_params()
    assert sent == set(workflow.STYLE_PARAMS), (sent, workflow.STYLE_PARAMS)


# ------------------------------------------------------------- patchStory call sites

def _patch_story_call_sites() -> list[str]:
    """The literal top-level key of every `patchStory(storyId, { ... })` call
    site across the story pages -- source paths chosen the same way
    test_dashboard_payload_contract.py reads NewJob.jsx: as plain text."""
    keys = []
    for path in STORY_SRC.rglob("*.jsx"):
        src = path.read_text(encoding="utf-8")
        for match in re.finditer(r"patchStory\(\s*storyId,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
            keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_call_sites():
    assert len(_patch_story_call_sites()) >= 6


def test_every_patch_story_call_site_sends_a_declared_field():
    declared = _class_fields("StoryPatchRequest")
    sent = set(_patch_story_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchStory(...) call sites send a key StoryPatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


# --------------------------------------------------------- BibleStep.jsx: regenerate

def _bible_regenerate_fields() -> set[str]:
    src = BIBLE_STEP.read_text(encoding="utf-8")
    match = re.search(r"const BIBLE_REGENERATE_FIELDS = \[(.*?)\]", src)
    assert match, "BIBLE_REGENERATE_FIELDS not found in BibleStep.jsx"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_bible_regenerate_targets_match_the_prompt_catalogue_exactly():
    fields = _bible_regenerate_fields()
    assert fields == set(prompts.REGENERATE_TARGETS), (fields, set(prompts.REGENERATE_TARGETS))


def test_every_regenerate_call_in_the_bible_step_uses_one_of_those_fields():
    src = BIBLE_STEP.read_text(encoding="utf-8")
    targets = set(re.findall(r"regenerate\('([a-z_]+)',", src))
    assert targets, "no regenerate(...) call sites found in BibleStep.jsx"
    assert targets <= _bible_regenerate_fields()


# --------------------------------------------------------- ConceptsStep.jsx: choose

def test_choose_concept_sends_concept_id():
    src = CONCEPTS_STEP.read_text(encoding="utf-8")
    assert re.search(r"chooseConcept\(\s*storyId,\s*\{\s*concept_id\s*:", src), (
        "chooseConcept(...) does not send { concept_id: ... }"
    )


# ------------------------------------------------- StyleStep.jsx: overridesAgainst
#
# Phone-review follow-up (spec 10, finding 5): only a path whose value
# differs from the *chosen template's* own value belongs in `overrides` --
# otherwise the lock's `overrides` no longer says what the user actually
# changed. A named helper does the diffing, and `styleParams`' `overrides`
# is built by calling it, rather than by listing the six paths
# unconditionally (which is the bug this guards against).

def test_style_step_diffs_overrides_against_the_template():
    src = STYLE_STEP.read_text(encoding="utf-8")
    assert re.search(r"function overridesAgainst\(\s*template", src), (
        "no overridesAgainst(template, ...) helper found in StyleStep.jsx"
    )
    assert re.search(r"const overrides = overridesAgainst\(", src), (
        "styleParams' overrides is not built by calling overridesAgainst(...)"
    )


# ------------------------------------------------------ per-step error slots
#
# Phone-review follow-up (spec 10, finding 1): a step action's error must
# show next to the control that caused it, inside that step -- never only
# at the top of the page, a full screen away on a phone. Each step file
# carries at least one element in the `story-step-error` class.

def test_every_step_renders_its_own_error_slot():
    for path in (CONCEPTS_STEP, BIBLE_STEP, STYLE_STEP):
        src = path.read_text(encoding="utf-8")
        assert "story-step-error" in src, f"{path.name} has no story-step-error slot"
