"""Tests for the AI Story style-template and concept data (spec 5, 6.3, 7).

Three layers, in order:

1. The stdlib JSON-Schema subset validator in ``schemas.py`` (DEC-012: no
   ``jsonschema``/``pydantic`` -- pytest alone must be able to run this).
2. Every shipped style template in ``templates/styles/`` round-trips through
   ``templates.load_style()`` and satisfies ``style_template_errors()``. The
   "spec parity" tests re-read ``00-MASTER-SPEC.md`` directly and diff the
   JSON's prose fields against the spec's own quoted text, so a template can
   never silently drift from the document that authored it.
3. The ten curated concepts (spec 7), written by a parallel agent against
   ``CONCEPT_SCHEMA``. These fail loudly -- not skip -- if that work has not
   landed yet; that is the expected state while both halves of stage 1 are
   in flight.
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from clipping.aistory import schemas, templates

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / ".claude" / "plans" / "ai-story" / "00-MASTER-SPEC.md"


# ============================================================== 1. validator

def test_type_keyword_accepts_matching_and_rejects_mismatched():
    assert schemas.validate("x", {"type": "string"}) == []
    assert schemas.validate(1, {"type": "string"}) != []
    assert schemas.validate(1, {"type": ["string", "integer"]}) == []
    assert schemas.validate(None, {"type": ["string", "null"]}) == []


def test_boolean_is_not_an_integer_or_a_number():
    assert schemas.validate(True, {"type": "integer"}) != []
    assert schemas.validate(True, {"type": "number"}) != []
    assert schemas.validate(True, {"type": "boolean"}) == []
    assert schemas.validate(1, {"type": "integer"}) == []
    assert schemas.validate(1.5, {"type": "number"}) == []


def test_required_and_additional_properties():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    assert schemas.validate({"a": "x"}, schema) == []
    assert any("required" in e for e in schemas.validate({}, schema))
    assert any("additional property" in e for e in schemas.validate({"a": "x", "b": 1}, schema))


def test_additional_properties_true_or_absent_allows_extra_keys():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert schemas.validate({"a": "x", "b": 1}, schema) == []


def test_enum_and_const():
    assert schemas.validate("b", {"enum": ["a", "b"]}) == []
    assert schemas.validate("c", {"enum": ["a", "b"]}) != []
    assert schemas.validate("x", {"const": "x"}) == []
    assert schemas.validate("y", {"const": "x"}) != []


def test_pattern_uses_search_and_relies_on_its_own_anchors():
    assert schemas.validate("abc123", {"pattern": r"^[a-z]+\d+$"}) == []
    assert schemas.validate("xabc123y", {"pattern": r"[a-z]+\d+"}) == []
    assert schemas.validate("red", {"pattern": schemas.HEX_COLOUR}) != []


def test_min_and_max_length():
    assert schemas.validate("", {"minLength": 1}) != []
    assert schemas.validate("abcdef", {"maxLength": 3}) != []
    assert schemas.validate("abc", {"minLength": 1, "maxLength": 3}) == []


def test_items_min_items_max_items():
    schema = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}
    assert schemas.validate(["a"], schema) == []
    assert schemas.validate([], schema) != []
    assert schemas.validate(["a", "b", "c"], schema) != []
    assert schemas.validate([1], schema) != []


def test_minimum_and_maximum():
    assert schemas.validate(5, {"minimum": 0, "maximum": 10}) == []
    assert schemas.validate(-1, {"minimum": 0}) != []
    assert schemas.validate(11, {"maximum": 10}) != []


def test_nested_error_paths_are_readable():
    schema = {
        "type": "object",
        "properties": {
            "palette": {
                "type": "object",
                "properties": {
                    "primary": {"type": "array", "items": {"type": "string", "pattern": schemas.HEX_COLOUR}}
                },
            }
        },
    }
    doc = {"palette": {"primary": ["#FFFFFF", "red"]}}
    errors = schemas.validate(doc, schema)
    assert any(e.startswith("$.palette.primary[1]:") and "does not match" in e for e in errors), errors


def test_unknown_keyword_is_ignored():
    schema = {"type": "string", "description": "a free-text hint for an LLM", "title": "x"}
    assert schemas.validate("anything", schema) == []


def test_check_raises_schema_error_with_name_and_errors():
    with pytest.raises(schemas.SchemaError) as excinfo:
        schemas.check(1, {"type": "string"}, "my_doc")
    err = excinfo.value
    assert err.name == "my_doc"
    assert len(err.errors) == 1
    assert "my_doc" in str(err)


def test_check_passes_silently_when_valid():
    assert schemas.check("ok", {"type": "string"}, "my_doc") is None


def test_bilingual_helper():
    schema = schemas.bilingual(max_length=5)
    assert schemas.validate({"fr": "ok", "en": "ok"}, schema) == []
    assert schemas.validate({"fr": "toolongvalue", "en": "ok"}, schema) != []
    assert schemas.validate({"fr": "ok"}, schema) != []
    assert schemas.validate({"fr": "ok", "en": "ok", "de": "nope"}, schema) != []
    assert schemas.validate({"fr": "", "en": "ok"}, schemas.bilingual()) != []


# ==================================================== 2a. style templates

EXPECTED_STYLE_IDS = (
    "anime", "cartoon_flat", "cinematic_real", "claymation",
    "family_3d", "fruit_drama", "storybook_watercolor",
)


def test_exactly_the_seven_shipped_style_ids():
    assert tuple(templates.list_style_ids()) == EXPECTED_STYLE_IDS


@pytest.mark.parametrize("template_id", EXPECTED_STYLE_IDS)
def test_every_style_template_passes_its_schema(template_id):
    tpl = templates.load_style(template_id)
    assert schemas.style_template_errors(tpl) == []
    assert tpl["template_id"] == template_id
    assert tpl["version"] == 1


@pytest.mark.parametrize("bad_id", ["../x", "Fruit", "fruit_drama/../secret", "nope", "", "fruit drama"])
def test_load_style_rejects_bad_or_unknown_ids(bad_id):
    with pytest.raises(KeyError):
        templates.load_style(bad_id)


def test_load_style_returns_a_deep_copy_each_time():
    a = templates.load_style("fruit_drama")
    a["palette"]["primary"].append("#000000")
    b = templates.load_style("fruit_drama")
    assert b["palette"]["primary"] != a["palette"]["primary"]


def test_a_broken_by_function_motion_is_caught_as_an_extra_check():
    tpl = templates.load_style("fruit_drama")
    tpl["motion_rules"]["tier1"]["by_function"]["hook"] = "not_a_motion"
    errors = schemas.style_template_errors(tpl)
    assert any("by_function.hook" in e for e in errors), errors


def test_sfx_cues_must_equal_the_pack_they_are_declared_for():
    tpl = templates.load_style("fruit_drama")
    tpl["audio"]["sfx_cues"] = ["not_a_real_cue"]
    errors = schemas.style_template_errors(tpl)
    assert any("sfx_cues" in e for e in errors), errors


# ==================================================== 2b. spec parity

_HEADING_RE = re.compile(r"^#### 5\.\d+\s+`([a-z0-9_]+)`.*$", re.MULTILINE)


def _spec_text() -> str:
    assert SPEC_PATH.is_file(), f"spec not found at {SPEC_PATH}"
    return SPEC_PATH.read_text(encoding="utf-8")


@functools.lru_cache(maxsize=1)
def _style_blocks() -> dict:
    """Slice 00-MASTER-SPEC.md §5 into one text block per `#### 5.N `id`` section."""
    text = _spec_text()
    matches = list(_HEADING_RE.finditer(text))
    assert matches, "could not find any '#### 5.N `id`' heading in the spec"
    blocks = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else text.index("\n---\n", start)
        blocks[m.group(1)] = text[start:end]
    return blocks


def _normalize(text: str) -> str:
    """Collapse the spec's markdown line-wrapping back to one flat string.

    A hyphen at a wrap point is rejoined with no space ("human-\\n  proportioned"
    -> "human-proportioned"), matching how the JSON prose fields were written;
    every other run of whitespace collapses to a single space.
    """
    text = re.sub(r"-\s*\n\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_quoted(block: str, *label_patterns: str) -> str:
    """Find the first matching label pattern (each ends in a literal opening
    quote) and return the normalized text up to the next quote."""
    for pattern in label_patterns:
        m = re.search(pattern, block)
        if not m:
            continue
        end = block.index('"', m.end())
        return _normalize(block[m.end():end])
    raise AssertionError(f"none of {label_patterns!r} found in spec block")


def _extract_json_array(block: str, label: str) -> list:
    m = re.search(rf"{label}\s*`(\[[^`]*\])`", block)
    return json.loads(m.group(1)) if m else []


# json field -> spec label pattern(s), tried in order (some templates use a
# bold "**label**:" form, others a plain lowercase "label " form -- see spec 5.1
# vs 5.2-5.7).
_PROSE_FIELD_PATTERNS = {
    "rendering": [r"\*\*rendering\*\*:\s*\""],
    "camera": [r"\*\*camera\*\*:\s*\""],
    "lighting": [r"\*\*lighting\*\*:\s*\""],
    "character_design_rules": [r"\*\*character_design_rules\*\*:\s*\""],
    "environment_rules": [r"\*\*environment_rules\*\*:\s*\""],
    "negative_prompt": [r"\*\*negative\*\*[^\"]*\""],
    "sheet_background": [r"\*\*sheet_background\*\*:\s*\"", r"sheet_background\s*\""],
    "quality_tail": [r"\*\*quality_tail\*\*:\s*\"", r"quality_tail\s*\""],
}


@pytest.mark.parametrize("template_id", EXPECTED_STYLE_IDS)
def test_style_template_prose_fields_match_the_spec_verbatim(template_id):
    """The key test: every prose field copied "verbatim" from the spec (5) is
    re-derived here directly from 00-MASTER-SPEC.md and compared to the JSON.
    A wording change in either place breaks this test."""
    block = _style_blocks()[template_id]
    tpl = templates.load_style(template_id)
    for field, patterns in _PROSE_FIELD_PATTERNS.items():
        expected = _extract_quoted(block, *patterns)
        assert tpl[field] == expected, f"{template_id}.{field} drifted from the spec"

    palette_line = _extract_quoted(block, r"palette_line\s*\"")
    assert tpl["palette"]["palette_line"] == palette_line

    tier2 = _extract_quoted(block, r"\*\*tier2_prompt_suffix\*\*:\s*\"", r"tier2\s+suffix\s*\"")
    assert tpl["motion_rules"]["tier2_prompt_suffix"] == tier2

    voice_direction = _extract_quoted(block, r"voice_direction\s*\"")
    assert tpl["audio"]["voice_direction"] == voice_direction


@pytest.mark.parametrize("template_id", EXPECTED_STYLE_IDS)
def test_style_template_palette_hexes_match_the_spec(template_id):
    block = _style_blocks()[template_id]
    tpl = templates.load_style(template_id)
    assert tpl["palette"]["primary"] == _extract_json_array(block, "primary")
    assert tpl["palette"]["accents"] == _extract_json_array(block, "accents")
    assert tpl["palette"]["forbidden"] == _extract_json_array(block, "forbidden")


# A second, independent guard: hand-picked distinctive phrases from the spec's
# own prose, checked with plain substring containment -- unrelated to the
# regex-based extraction above, so a bug in that extraction would not hide a
# real drift from both tests at once.
_KEY_PHRASES = {
    "fruit_drama": [
        ("rendering", "subsurface scattering on fruit skin"),
        ("character_design_rules", "No hands as fruit"),
    ],
    "family_3d": [
        ("character_design_rules", "the environment stays muted"),
        ("rendering", "global illumination"),
    ],
    "anime": [
        ("camera", "extreme close-up on eyes for tension"),
        ("environment_rules", "cherry blossoms or neon as motifs"),
    ],
    "cinematic_real": [
        ("environment_rules", "night buses, diners, offices, parking garages"),
        ("camera", "over-the-shoulder for dialogue"),
    ],
    "cartoon_flat": [
        ("rendering", "clean vector look"),
        ("character_design_rules", "Line weight identical everywhere"),
    ],
    "storybook_watercolor": [
        ("rendering", "visible paper grain"),
        ("environment_rules", "cottages, forests, seasides, attics"),
    ],
    "claymation": [
        ("rendering", "visible fingerprints"),
        ("environment_rules", "cardboard buildings, felt grass"),
    ],
}


@pytest.mark.parametrize("template_id", EXPECTED_STYLE_IDS)
def test_style_template_key_phrases_survive_verbatim(template_id):
    tpl = templates.load_style(template_id)
    for field, phrase in _KEY_PHRASES[template_id]:
        assert phrase in tpl[field], f"{template_id}.{field} lost {phrase!r}"


# ==================================================== 3. concepts (spec 7)

EXPECTED_CONCEPT_IDS = (
    "tentafruit_island", "orchard_inheritance", "midnight_fridge", "last_bus_3am",
    "detective_dawn", "grandmas_rules", "two_minutes_heroes", "clay_town_confessions",
    "the_interview", "last_ramen_shop",
)

# Title (English) and Style column, transcribed from 00-MASTER-SPEC.md section
# 7's table. style_fit.default must equal styles[0], alternatives styles[1:].
_CONCEPT_TABLE = {
    "tentafruit_island": {"title_en": "Tentafruit Island", "styles": ["fruit_drama"]},
    "orchard_inheritance": {"title_en": "The Orchard Inheritance", "styles": ["fruit_drama", "family_3d"]},
    "midnight_fridge": {"title_en": "Midnight Fridge", "styles": ["family_3d", "fruit_drama"]},
    "last_bus_3am": {"title_en": "Last Bus at 3 AM", "styles": ["cinematic_real"]},
    "detective_dawn": {"title_en": "The Detective Who Forgets", "styles": ["anime"]},
    "grandmas_rules": {"title_en": "Grandma's Rules", "styles": ["storybook_watercolor"]},
    "two_minutes_heroes": {"title_en": "Two Minutes to Save the World", "styles": ["cartoon_flat"]},
    "clay_town_confessions": {"title_en": "Clay Town Confessions", "styles": ["claymation"]},
    "the_interview": {"title_en": "The Interview", "styles": ["cinematic_real"]},
    "last_ramen_shop": {"title_en": "The Last Ramen Shop", "styles": ["anime", "storybook_watercolor"]},
}

# Proper nouns the spec's "World & cast" column names explicitly, per concept.
# Purely generic descriptors in that column ("a receptionist who knows too
# much", "a suspicious pigeon") are not required verbatim -- checked loosely
# by keyword where a distinguishing noun exists, skipped where the spec gives
# no name at all (grandmas_rules, the_interview).
_REQUIRED_CAST_KEYWORDS = {
    "tentafruit_island": ["Kiwilo", "Mangella", "Broccolia", "Pepperino", "Avocardo"],
    "orchard_inheritance": ["Grandma Fig"],
    "midnight_fridge": ["Pickle", "Brie", "Yogurt", "Egg"],
    "last_bus_3am": ["Sam"],
    "detective_dawn": ["Rin", "Kaito"],
    "grandmas_rules": [],
    "two_minutes_heroes": ["Captain Obvious", "Overthink"],
    "clay_town_confessions": ["Mayor Dough", "Baker Pim", "Mailbox"],
    "the_interview": [],
    "last_ramen_shop": ["Hana", "Bo"],
}


def _concepts_by_id() -> dict:
    """No try/except and no skip: while the concepts are still being written
    by the parallel agent, these tests are meant to fail loudly, not hide it."""
    return {c["concept_id"]: c for c in templates.load_concepts()}


def test_exactly_the_ten_shipped_concept_ids():
    assert set(_concepts_by_id()) == set(EXPECTED_CONCEPT_IDS)


@pytest.mark.parametrize("concept_id", EXPECTED_CONCEPT_IDS)
def test_every_concept_passes_its_schema(concept_id):
    concept = _concepts_by_id()[concept_id]
    assert schemas.concept_errors(concept, templates.list_style_ids()) == []


@pytest.mark.parametrize("concept_id", EXPECTED_CONCEPT_IDS)
def test_concept_title_and_style_fit_match_the_spec_table(concept_id):
    concept = _concepts_by_id()[concept_id]
    expected = _CONCEPT_TABLE[concept_id]
    assert concept["title"]["en"] == expected["title_en"]
    assert concept["style_fit"]["default"] == expected["styles"][0]
    assert concept["style_fit"]["alternatives"] == expected["styles"][1:]


@pytest.mark.parametrize("concept_id", EXPECTED_CONCEPT_IDS)
def test_concept_cast_names_include_the_spec_named_characters(concept_id):
    concept = _concepts_by_id()[concept_id]
    names = [c["name"]["en"] for c in concept["cast_sketch"]]
    for keyword in _REQUIRED_CAST_KEYWORDS[concept_id]:
        assert any(keyword in name for name in names), f"{concept_id}: {keyword!r} not found in {names}"


def test_localize_concept_flattens_bilingual_fields_to_plain_strings():
    concept = _concepts_by_id()["tentafruit_island"]
    localized = templates.localize_concept(concept, "fr")
    assert isinstance(localized["title"], str)
    assert isinstance(localized["logline"], str)
    assert isinstance(localized["cast_sketch"][0]["name"], str)
    assert isinstance(localized["episode_seed"][0], str)
    # non-bilingual structure (ids, enums, hex-pattern ids) is untouched
    assert localized["style_fit"] == concept["style_fit"]
    assert localized["content_flags"] == concept["content_flags"]


def test_localize_concept_rejects_an_unsupported_language():
    concept = _concepts_by_id()["tentafruit_island"]
    with pytest.raises(ValueError):
        templates.localize_concept(concept, "de")


def test_a_dollar_anchor_does_not_accept_a_trailing_newline():
    # JSON Schema patterns are ECMA-262, where "$" is the very end of the
    # string; Python's "$" also matches just before a trailing newline, so
    # "fruit_drama\n" would pass as a template id.
    schema = {"type": "string", "pattern": r"^[a-z][a-z0-9_]*$"}
    assert schemas.validate("fruit_drama", schema) == []
    assert schemas.validate("fruit_drama\n", schema) != []
    assert schemas.validate("price $", {"type": "string", "pattern": r"\$"}) == []
