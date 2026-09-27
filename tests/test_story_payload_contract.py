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

from clipping.aistory import prompts, refimages, schemas, workflow
from clipping.aistory.steps import regenerate as regenerate_step

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
STORY_SRC = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story"
NEW_STORY_WIZARD = STORY_SRC / "NewStoryWizard.jsx"
STYLE_STEP = STORY_SRC / "steps" / "StyleStep.jsx"
BIBLE_STEP = STORY_SRC / "steps" / "BibleStep.jsx"
CONCEPTS_STEP = STORY_SRC / "steps" / "ConceptsStep.jsx"
CAST_STEP = STORY_SRC / "steps" / "CastStep.jsx"
PLACES_STEP = STORY_SRC / "steps" / "PlacesStep.jsx"
SEASON_STEP = STORY_SRC / "steps" / "SeasonStep.jsx"
FIELDS = STORY_SRC / "fields.jsx"


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


# ============================================================ CastStep.jsx (phase 2)
#
# The cast step (stage 9) gained its own request literals: NoCastYet's
# `castParams` (POST /steps/cast), every CharacterCard `patchCharacter(...)`
# call site, the regenerate targets it builds, and the voice payload it
# sends when a user picks a voice. Same reasoning as the phase-1 guards
# above: a Pydantic/closed-list model is default-strict or a fixed shape, so
# a key or a shape it does not know silently does nothing.

def _cast_params() -> set[str]:
    src = CAST_STEP.read_text(encoding="utf-8")
    match = re.search(r"const castParams = \{([^}]*)\}", src)
    assert match, "castParams object literal not found in CastStep.jsx"
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


def test_cast_params_matches_workflow_cast_params_exactly():
    sent = _cast_params()
    assert sent == set(workflow.CAST_PARAMS), (sent, workflow.CAST_PARAMS)


def _patch_character_call_sites() -> list[str]:
    """The literal top-level key of every `patchCharacter(storyId,
    character.char_id, { ... })` call site in CastStep.jsx."""
    src = CAST_STEP.read_text(encoding="utf-8")
    keys = []
    for match in re.finditer(
            r"patchCharacter\(\s*storyId,\s*character\.char_id,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
        keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_character_call_sites():
    assert len(_patch_character_call_sites()) >= 5


def test_every_patch_character_call_site_sends_a_declared_field():
    declared = _class_fields("CharacterPatchRequest")
    sent = set(_patch_character_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchCharacter(...) call sites send a key CharacterPatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


def test_patch_character_fields_match_the_workflows_editable_character_fields():
    # CharacterPatchRequest and workflow.CHARACTER_PATCH_FIELDS must agree
    # (the model is what the route reads; the workflow is what patch_entity
    # actually applies), so the call-site guard above is checking against the
    # same list the backend enforces.
    assert _class_fields("CharacterPatchRequest") == set(workflow.CHARACTER_PATCH_FIELDS)


def _cast_regenerate_target_templates() -> set[str]:
    """Every `character:${...}:...` template literal used as a regenerate
    target in CastStep.jsx (a bare `character:${...}` is an *approve* doc,
    not a target, so it is excluded), interpolations normalized to `<x>` so
    a dynamic id or slot name can be compared against the fixed shapes."""
    src = CAST_STEP.read_text(encoding="utf-8")
    literals = re.findall(r"`(character:\$\{[^`]*?:[a-z]+(?::\$\{[^`]*?\})?)`", src)
    assert literals, "no `character:${...}:...` regenerate target found in CastStep.jsx"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_cast_step_regenerate_targets_match_the_entity_target_shapes():
    templates = _cast_regenerate_target_templates()
    assert templates == {"character:<x>:text", "character:<x>:image:<x>", "character:<x>:voice"}
    assert "character:<char_id>:text" in regenerate_step.ENTITY_TARGETS
    assert "character:<char_id>:voice" in regenerate_step.ENTITY_TARGETS
    assert any(shape.startswith("character:<char_id>:image:") for shape in regenerate_step.ENTITY_TARGETS)


def test_cast_step_image_slots_match_the_character_image_names():
    src = CAST_STEP.read_text(encoding="utf-8")
    match = re.search(r"const IMAGE_SLOTS = \[(.*?)\]", src)
    assert match, "IMAGE_SLOTS not found in CastStep.jsx"
    slots = set(re.findall(r"'([a-z]+)'", match.group(1)))
    assert slots == set(refimages.CHARACTER_IMAGES)


def test_cast_step_voice_payload_keys_match_the_voice_shape():
    src = CAST_STEP.read_text(encoding="utf-8")
    match = re.search(
        r"target:\s*`character:\$\{character\.char_id\}:voice`,\s*voice:\s*\{([^}]*)\}", src)
    assert match, "no regenerateStory(...) call site picking a voice found in CastStep.jsx"
    keys = set(re.findall(r"([a-z_]+):", match.group(1)))
    assert keys == {"provider", "voice_id"}


def test_cast_step_renders_its_own_error_slot():
    src = CAST_STEP.read_text(encoding="utf-8")
    assert "story-step-error" in src, "CastStep.jsx has no story-step-error slot"


# ================================================ PlacesStep.jsx / SeasonStep.jsx (phase 2, stage 10)
#
# Stage 10 gained places, props and the season arc: PlacesStep.jsx's
# `placesParams` (POST /steps/places), every `patchPlace`/`patchProp` call
# site, the regenerate targets it builds and its TIME_VARIANT_CHOICES list;
# SeasonStep.jsx's `seasonParams` (POST /steps/season) and its own regenerate
# targets. Same reasoning as the cast step's guards above.

def _places_params() -> set[str]:
    src = PLACES_STEP.read_text(encoding="utf-8")
    match = re.search(r"const placesParams = \{([^}]*)\}", src)
    assert match, "placesParams object literal not found in PlacesStep.jsx"
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


def test_places_params_matches_workflow_places_params_exactly():
    sent = _places_params()
    assert sent == set(workflow.PLACES_PARAMS), (sent, workflow.PLACES_PARAMS)


def _season_params() -> set[str]:
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const seasonParams = \{([^}]*)\}", src)
    assert match, "seasonParams object literal not found in SeasonStep.jsx"
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


def test_season_params_matches_workflow_season_params_exactly():
    sent = _season_params()
    assert sent == set(workflow.SEASON_PARAMS), (sent, workflow.SEASON_PARAMS)


def _patch_place_call_sites() -> list[str]:
    """The literal top-level key of every `patchPlace(storyId, place.place_id,
    { ... })` call site in PlacesStep.jsx."""
    src = PLACES_STEP.read_text(encoding="utf-8")
    keys = []
    for match in re.finditer(
            r"patchPlace\(\s*storyId,\s*place\.place_id,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
        keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_place_call_sites():
    assert len(_patch_place_call_sites()) >= 2


def test_every_patch_place_call_site_sends_a_declared_field():
    declared = _class_fields("PlacePatchRequest")
    sent = set(_patch_place_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchPlace(...) call sites send a key PlacePatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


def test_patch_place_fields_match_the_workflows_editable_place_fields():
    assert _class_fields("PlacePatchRequest") == set(workflow.PLACE_PATCH_FIELDS)


def _patch_prop_call_sites() -> list[str]:
    """The literal top-level key of every `patchProp(storyId, prop.prop_id,
    { ... })` call site in PlacesStep.jsx."""
    src = PLACES_STEP.read_text(encoding="utf-8")
    keys = []
    for match in re.finditer(
            r"patchProp\(\s*storyId,\s*prop\.prop_id,\s*\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}]", src):
        keys.append(match.group(1))
    return keys


def test_the_readers_see_patch_prop_call_sites():
    assert len(_patch_prop_call_sites()) >= 2


def test_every_patch_prop_call_site_sends_a_declared_field():
    declared = _class_fields("PropPatchRequest")
    sent = set(_patch_prop_call_sites())
    undeclared = sent - declared
    assert undeclared == set(), (
        "these patchProp(...) call sites send a key PropPatchRequest does not "
        f"declare, so pydantic drops it and the edit does nothing: {sorted(undeclared)}"
    )


def test_patch_prop_fields_match_the_workflows_editable_prop_fields():
    assert _class_fields("PropPatchRequest") == set(workflow.PROP_PATCH_FIELDS)


def _places_time_variant_choices() -> set[str]:
    src = PLACES_STEP.read_text(encoding="utf-8")
    match = re.search(r"const TIME_VARIANT_CHOICES = \[(.*?)\]", src)
    assert match, "TIME_VARIANT_CHOICES not found in PlacesStep.jsx"
    return set(re.findall(r"'([a-z]+)'", match.group(1)))


def test_places_step_time_variant_choices_match_the_schema():
    assert _places_time_variant_choices() == set(schemas.TIME_VARIANT_CHOICES)


def _place_regenerate_target_templates() -> set[str]:
    """Every `place:${...}:...` template literal used as a regenerate target
    in PlacesStep.jsx, interpolations normalized to `<x>`."""
    src = PLACES_STEP.read_text(encoding="utf-8")
    literals = re.findall(r"`(place:\$\{[^`]*?:[a-z]+(?::\$\{[^`]*?\})?)`", src)
    assert literals, "no `place:${...}:...` regenerate target found in PlacesStep.jsx"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_places_step_regenerate_targets_match_the_entity_target_shapes():
    templates = _place_regenerate_target_templates()
    assert templates == {"place:<x>:text", "place:<x>:image:<x>"}
    assert "place:<place_id>:text" in regenerate_step.ENTITY_TARGETS
    assert any(shape.startswith("place:<place_id>:image:") for shape in regenerate_step.ENTITY_TARGETS)


def _prop_regenerate_target_templates() -> set[str]:
    """Every `prop:${...}:...` template literal used as a regenerate target in
    PlacesStep.jsx, interpolations normalized to `<x>`."""
    src = PLACES_STEP.read_text(encoding="utf-8")
    literals = re.findall(r"`(prop:\$\{[^`]*?:[a-z]+)`", src)
    assert literals, "no `prop:${...}:...` regenerate target found in PlacesStep.jsx"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_places_step_prop_regenerate_targets_match_the_entity_target_shapes():
    templates = _prop_regenerate_target_templates()
    assert templates == {"prop:<x>:text", "prop:<x>:image"}
    assert "prop:<prop_id>:text" in regenerate_step.ENTITY_TARGETS
    assert "prop:<prop_id>:image" in regenerate_step.ENTITY_TARGETS


def test_places_step_renders_its_own_error_slot():
    src = PLACES_STEP.read_text(encoding="utf-8")
    assert "story-step-error" in src, "PlacesStep.jsx has no story-step-error slot"


def _season_regenerate_target_templates() -> set[str]:
    """Every `season:${...}` template literal used as a regenerate target in
    SeasonStep.jsx, interpolations normalized to `<x>`."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    literals = re.findall(r"`(season:\$\{[^`]*?\})`", src)
    assert literals, "no `season:${...}` regenerate target found in SeasonStep.jsx"
    return {re.sub(r"\$\{[^}]*\}", "<x>", literal) for literal in literals}


def test_season_step_regenerate_targets_match_the_entity_target_shapes():
    templates = _season_regenerate_target_templates()
    assert templates == {"season:<x>"}
    assert "season:<ep>" in regenerate_step.ENTITY_TARGETS


def test_season_step_renders_its_own_error_slot():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "story-step-error" in src, "SeasonStep.jsx has no story-step-error slot"


# =============================================== polish findings (Tier-2 walk)
#
# Four small fixes from the live phase-2 walk (CHECKPOINT.md's Tier-2 entry):
# the places estimate must reflect the list the user is editing, not only the
# saved proposal; an empty image slot must offer "Make <x>", not "Regenerate";
# and every icon-only button in the story pages needs an accessible name.

def test_the_places_estimate_call_sends_the_list_on_screen():
    """ProposalEditor's estimate chip (above "Create places & props") must be
    fetched with the names on screen (`placeNames`/`propNames`), not with no
    arguments at all -- the bug was that it always showed the saved
    places_proposal.json's estimate, even after the user trimmed the list."""
    src = PLACES_STEP.read_text(encoding="utf-8")
    match = re.search(
        r"fetchStoryEstimate\(\s*storyId,\s*'places',\s*\{\s*places:\s*placeNames,\s*props:\s*propNames\s*\}\s*\)",
        src)
    assert match, "ProposalEditor must call fetchStoryEstimate(storyId, 'places', { places: placeNames, props: propNames })"


def test_fetch_story_estimate_forwards_places_and_props_as_repeated_query_params():
    api_js = (PROJECT_ROOT / "web" / "dashboard" / "src" / "api.js").read_text(encoding="utf-8")
    match = re.search(r"export async function fetchStoryEstimate\(storyId, step, \{([^}]*)\}", api_js)
    assert match, "fetchStoryEstimate signature not found in api.js"
    destructured = {name.strip() for name in match.group(1).split(",") if name.strip()}
    assert {"places", "props"} <= destructured, destructured
    assert "params.append('place'" in api_js and "params.append('prop'" in api_js


def _regenerate_control_call_sites(src: str) -> list[str]:
    """Every `<RegenerateControl ... />` opening tag in *src*, as one string
    each (attributes may span several lines, and one of them --
    `estimateChip={<EstimateChip .../>}` -- self-closes too, so a naive
    "first `/>`" search stops inside it; this tracks brace depth instead, and
    closes the tag only at depth 0)."""
    sites = []
    for start in (m.start() for m in re.finditer(r"<RegenerateControl\b", src)):
        depth = 0
        i = start
        while i < len(src):
            ch = src[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == "/" and depth == 0 and src[i:i + 2] == "/>":
                sites.append(src[start:i + 2])
                break
            elif ch == ">" and depth == 0:
                sites.append(src[start:i + 1])
                break
            i += 1
    return sites


def test_regenerate_control_supports_an_empty_make_mode():
    """fields.jsx's shared control must read "Make <label>" (no note input)
    for an empty slot, and keep "Regenerate" (with a note) once an image
    exists -- the bug was offering "Regenerate" for a slot that was never
    made yet."""
    src = FIELDS.read_text(encoding="utf-8")
    assert re.search(r"export function RegenerateControl\(\{[^}]*\bempty\b[^}]*\blabel\b[^}]*\}\)", src), (
        "RegenerateControl must take `empty` and `label` props")
    assert "`Make ${label}`" in src
    assert "↻ Regenerate" in src


def test_cast_steps_missing_image_slot_uses_make_not_regenerate():
    src = CAST_STEP.read_text(encoding="utf-8")
    [call] = [c for c in _regenerate_control_call_sites(src) if "SLOT_LABELS[slot]" in c]
    assert "empty={!ref}" in call and "label={SLOT_LABELS[slot]}" in call


def test_places_steps_missing_variant_and_prop_image_use_make_not_regenerate():
    src = PLACES_STEP.read_text(encoding="utf-8")
    calls = _regenerate_control_call_sites(src)
    [variant_call] = [c for c in calls if "label={variantKey}" in c]
    assert "empty={!imageRef}" in variant_call
    [prop_call] = [c for c in calls if 'label="image"' in c]
    assert "empty={!prop.image}" in prop_call


def _icon_only_button_blocks(src: str) -> list[str]:
    """Every `<button ...>...</button>` block in *src* whose rendered text,
    right before the closing tag, is a single non-alphanumeric glyph (the
    icon-only "✕" remove buttons; a labelled button like "+ Add" or
    "Delete place" is left out)."""
    blocks = []
    for match in re.finditer(r"<button\b.*?</button>", src, re.S):
        block = match.group(0)
        tail = re.search(r">\s*([^\n<{]*?)\s*</button>\Z", block)
        visible = tail.group(1).strip() if tail else ""
        if visible and len(visible) <= 2 and not any(ch.isalnum() for ch in visible):
            blocks.append(block)
    return blocks


def test_icon_only_buttons_in_the_story_pages_have_an_accessible_name():
    found = 0
    for path in (CAST_STEP, PLACES_STEP, STYLE_STEP):
        src = path.read_text(encoding="utf-8")
        blocks = _icon_only_button_blocks(src)
        for block in blocks:
            assert "aria-label" in block, f"{path.name}: icon-only button has no aria-label: {block!r}"
        found += len(blocks)
    # Known icon-only buttons: CastStep's removeCustom, PlacesStep's
    # removePlace and removeProp, StyleStep's palette-color remove.
    assert found == 4, found
