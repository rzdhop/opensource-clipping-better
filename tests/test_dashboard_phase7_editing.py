"""AI Story phase 7 stage 7 (A18, A19): the dashboard half.

- Settings' hardware card leads with the billed-preset advice (its price and
  keys, from ``GET /api/hardware``'s first recommendation) on a weak host.
- The new-story form's "Fully animated" line says what the preset costs
  (``new_story_offer``'s ``estimate``).
- The story page's Visual tier card gains a budget-profile select (log
  finding F7) and says why an estimate plans 0 clips (F6).
- Cast, Places and Knowledge edit what phase 7's writers produce: a
  character's dossier and look, a place's layout map and light per variant, a
  prop's look (v2 stories only), and the knowledge base inline, with save
  and the stale/approve state.

Text contracts over the sources (DEC-012: stdlib + pytest only, no JS
runner), in the style of tests/test_story_payload_contract.py: a key a
request model does not declare would be dropped silently.
"""

from __future__ import annotations

import ast
import pathlib
import re

from clipping.aistory import schemas, workflow
from clipping.providers import budget as budget_mod

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
API_JS = SRC / "api.js"
SETTINGS = SRC / "pages" / "Settings.jsx"
STORY = SRC / "pages" / "story"
WIZARD = STORY / "NewStoryWizard.jsx"
# The Visual tier card moved out of NewStoryWizard.jsx with the story workspace (DEC-255).
CARD = STORY / "GenerationProfileCard.jsx"
CAST = STORY / "steps" / "CastStep.jsx"
PLACES = STORY / "steps" / "PlacesStep.jsx"
KNOWLEDGE = STORY / "steps" / "KnowledgeStep.jsx"
MODELS = ROOT / "web" / "api" / "models.py"


def _read(path):
    return path.read_text(encoding="utf-8")


def _class_fields(name):
    tree = ast.parse(_read(MODELS))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}
    raise AssertionError(name)


def _call_keys(src, call, args):
    """The literal top-level key of every ``call(args, { <key>...`` site."""
    return re.findall(rf"{call}\(\s*{args},\s*\{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[:,}}]", src)


def _function(src, name):
    """The source of ``function <name>(`` up to the next top-level function."""
    start = src.index(f"function {name}(")
    following = re.search(r"\n(?:export default )?function \w+\(", src[start + 1:])
    return src[start:start + 1 + following.start()] if following else src[start:]


# ------------------------------------------------------------------ hardware

def test_the_hardware_card_leads_with_the_billed_preset_advice():
    panel = _function(_read(SETTINGS), "HardwarePanel")
    # The advice rows carry the estimate (hardware.billed_preset_row) and are
    # shown apart from -- before -- the local models.
    assert "r.estimate" in panel and "advice" in panel
    assert panel.index("advice.map(") < panel.index("Recommended locally")
    assert "estimate.assumptions" in panel and ".keys" in panel


# ------------------------------------------------------------------ new story

def test_the_fully_animated_line_says_what_the_preset_costs():
    form = _function(_read(WIZARD), "CreateStoryForm")
    assert "offer.estimate" in form and "estimate.summary" in form and "estimate.assumptions" in form


# ------------------------------------------------------------------ visual tier card

def test_the_visual_tier_card_has_a_budget_profile_select_and_says_why_no_clip_is_planned():
    card = _function(_read(CARD), "GenerationProfileCard")
    assert "save({ budget_profile: value })" in card
    options = set(re.findall(r'<option value="([a-z_]+)">', card))
    assert set(budget_mod.load_profiles()["profiles"]) <= options, options
    # F6: an estimate that plans no clip says why on the card, not only in a tooltip.
    assert "est.video.count === 0" in card
    # The per-route estimates are asked again when the profile changes.
    assert re.search(r"\}, \[storyId, tier, budgetProfile, nextEp\]\)", card)


# ------------------------------------------------------------------ api.js

def test_api_js_patches_the_knowledge_base():
    src = _read(API_JS)
    start = src.index("export async function patchKnowledge(storyId, payload)")
    block = src[start:start + 400]
    assert "`/stories/${storyId}/knowledge`" in block and "method: 'PATCH'" in block


# ------------------------------------------------------------------ cast

def test_the_cast_step_edits_the_dossier_and_the_look_of_a_v2_story():
    src = _read(CAST)
    keys = set(_call_keys(src, "patchCharacter", r"storyId,\s*character\.char_id"))
    assert {"look", "dossier"} <= keys <= _class_fields("CharacterPatchRequest"), keys
    assert "pipeline === 'v2'" in src
    look = set(schemas.CHARACTER_LOOK_SCHEMA["properties"])
    dossier = set(schemas.CHARACTER_DOSSIER_SCHEMA["properties"])
    used_look = set(re.findall(r"\blook\.([a-z_]+)", src))
    used_dossier = set(re.findall(r"\bdossier\.([a-z_]+)", src))
    assert used_look and used_look <= look, used_look - look
    assert used_dossier and used_dossier <= dossier, used_dossier - dossier
    for key in ("build", "silhouette", "face", "hair", "skin_material", "height_cm", "palette", "wardrobe_sets"):
        assert f"look.{key}" in src or f"'{key}'" in src, key
    for key in ("backstory", "goal", "need", "fears", "secrets", "relationships", "arc"):
        assert f"dossier.{key}" in src or f"'{key}'" in src, key


def test_the_look_editor_has_a_species_head_field_over_the_universe_pool():
    """Plan 26 stage 7c: a select over the story's universe species plus "Other…", saved as ``look.species``."""
    src = _read(CAST)
    field = _function(src, "SpeciesField")
    assert "Species (head)" in field and "Other…" in field and "<select" in field
    assert "every head is a fruit" in field
    assert "species" in set(schemas.CHARACTER_LOOK_SCHEMA["properties"])
    assert "onSave={(value) => save({ species: value })}" in _function(src, "LookSection")
    # The pool is the story's universe (else its style's default) from the universes route, via the api helper.
    pool = _function(src, "useSpeciesPool")
    assert "fetchUniverses" in pool and "profile.universe || (styleUniverses ? styleUniverses.default : null)" in pool
    assert "entry.species" in pool and "fetchUniverses" in src.split("} from '../../../api'")[0]
    # The word cap is the schema's, and a blank value is never sent (the schema takes no empty or null species).
    assert f"SPECIES_MAX_WORDS = {schemas.LOOK_SPECIES_MAX_WORDS}" in src
    assert "if (!chosen || tooLong) return" in field


# ------------------------------------------------------------------ places and props

def test_the_places_step_edits_the_layout_map_the_light_per_variant_and_the_prop_look():
    src = _read(PLACES)
    assert "look" in set(_call_keys(src, "patchPlace", r"storyId,\s*place\.place_id"))
    assert "look" in set(_call_keys(src, "patchProp", r"storyId,\s*prop\.prop_id"))
    match = re.search(r"const LAYOUT_MAP_KEYS = \[(.*?)\]", src)
    assert match and re.findall(r"'([a-z]+)'", match.group(1)) == list(schemas.LAYOUT_MAP_KEYS)
    # One light a time variant the place has.
    assert "Object.keys(place.time_variants)" in src and "lighting" in src
    prop_look = set(schemas.PROP_LOOK_SCHEMA["properties"])
    place_look = set(schemas.PLACE_LOOK_SCHEMA["properties"])
    used = set(re.findall(r"\blook\.([a-z_]+)", src))
    assert used and used <= prop_look | place_look, used - (prop_look | place_look)
    assert "pipeline === 'v2'" in src


# ------------------------------------------------------------------ knowledge

def test_the_knowledge_step_edits_inline_and_shows_the_stale_state():
    src = _read(KNOWLEDGE)
    keys = set(_call_keys(src, "patchKnowledge", "storyId"))
    assert keys == set(workflow.KNOWLEDGE_PATCH_FIELDS) == _class_fields("KnowledgePatchRequest"), keys
    # A beat is named by its episode and 1-based position.
    assert re.search(r"beats: \[\{ ep: [a-zA-Z_.]+, beat: [a-zA-Z_]+", src)
    assert "Read-only" not in src
    # A saved edit moves rev: the step says the base must be approved again.
    assert "state === 'stale'" in src and "knowledge.approved_rev" in src and "knowledge.rev" in src
