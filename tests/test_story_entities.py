"""The phase-2 story documents on disk (AI Story phase 2, stage 1).

``clipping/aistory/schemas.py`` gains the character, place, prop, season-arc
and places-proposal documents; ``clipping/aistory/store.py`` keeps each entity
in ``<story>/<kind>/<id>/`` and extends the status chain to
``cast_approved -> places_approved -> ready``. What is pinned here:

- each document validates in its good shape and refuses each rule broken once;
  every fixed object is closed;
- entity ids are ASCII slugs of the name, deduplicated, always within their
  kind's pattern;
- an entity id, kind or media name is checked before any path is built from
  it; every level below the story is a real directory; a symlink is kept and
  never followed; an invalid entity document is skipped and reported, never
  deleted;
- media files are written atomically and served only under their kind's names;
- ``approvals.cast``/``places`` are folded from the entities, so un-approving
  or deleting one drops the status back; ``status`` stays a contiguous prefix;
- a phase-1 ``story.json`` (three approvals) loads, saves, and gains the three
  new keys as null.

Stdlib + pytest only (DEC-012): this file runs in the CI environment. Every
file lives under ``tmp_path``. Nothing new is referenced at import time, so
against the parent commit each test fails on its own.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import shutil

import pytest

from clipping.aistory import defaults, schemas, store

NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
LATEST = "2026-09-26T12:00:00+00:00"

UPLOAD = "0123456789abcdef0123456789abcdef.png"


# ------------------------------------------------------------------ documents

def _image(name, consistency="base", *, source="pollinations/flux", seed=7):
    return {"name": name, "consistency": consistency, "source": source, "seed": seed, "created_at": NOW}


def _character(**changes):
    doc = {
        "$schema": "character_v1",
        "char_id": "char_kiwilo",
        "name": "Kiwilo",
        "role": "lead",
        "archetype": "charming schemer",
        "one_line": "A kiwi who lied about the coconut phone.",
        "descriptor": None,
        "signature_items": [],
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {},
        "voice": None,
        "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None,
        "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "sketch",
        "approved_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _written_character(**changes):
    doc = _character(
        descriptor=("an anthropomorphic kiwi with fuzzy brown skin, bright green flesh "
                    "visible at the mouth, a thin gold chain, a white linen shirt"),
        signature_items=["thin gold chain", "white linen shirt", "left-eyebrow scar"],
        personality={"traits": ["charming", "evasive"], "wants": "to win the island",
                     "fears": "being found out", "speech_style": "short sentences, always a joke"},
        relationships={"char_mangella": "secret ex", "char_broccolia": "owes her money"},
        voice={"provider": "edge", "voice_id": "fr-FR-HenriNeural", "rate": "+5%", "pitch": "-2Hz",
               "direction": "smug, warm, slightly nasal", "sample_line": "Moi, mentir ? Jamais sur cette île."},
        refs={
            "portrait": _image("portrait.png", "base", seed=123456789),
            "turnaround": _image("turnaround.png", "references"),
            "expressions": _image("expressions.webp", "prompt_only"),
            "extra": [_image("extra_01.png", "references")],
            "uploads": [{"name": UPLOAD, "description": "a pencil sketch of a kiwi", "uploaded_at": NOW}],
        },
        ref_seed=123456789,
        prompt_block="an anthropomorphic kiwi, thin gold chain, white linen shirt",
        state={"alive": True, "location": "place_beach_camp", "arc_notes": ["ep01: lied about the coconut"]},
        source="custom",
        approved_at=LATER,
    )
    doc.update(changes)
    return doc


def _place(**changes):
    doc = {
        "$schema": "place_v1",
        "place_id": "place_beach_camp",
        "name": "Beach camp",
        "one_line": "Where the couples sleep and plot.",
        "descriptor": None,
        "layout_notes": None,
        "time_variants": {"day": None},
        "prompt_block": None,
        "approved_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _written_place(**changes):
    doc = _place(
        descriptor="a crescent of white sand with palm-leaf huts and a stone bonfire ring",
        layout_notes="huts on the left, the sea on the right, the bonfire ring at the back",
        time_variants={
            "day": _image("variant_day.png", "base"),
            "night": _image("variant_night.jpg", "references"),
            "golden_hour": _image("variant_golden_hour.png", "prompt_only"),
            "rain": None,
        },
        prompt_block="a crescent of white sand",
        approved_at=LATER,
    )
    doc.update(changes)
    return doc


def _prop(**changes):
    doc = {
        "$schema": "prop_v1",
        "prop_id": "prop_coconut_phone",
        "name": "Coconut phone",
        "one_line": "The only line off the island.",
        "descriptor": None,
        "owner_char_id": None,
        "image": None,
        "prompt_block": None,
        "approved_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _written_prop(**changes):
    doc = _prop(descriptor="a hollow coconut with a curly cord and a brass dial",
                owner_char_id="char_kiwilo", image=_image("image.png", "base"),
                prompt_block="a hollow coconut", approved_at=LATER)
    doc.update(changes)
    return doc


_ARC_FUNCTIONS = ["setup", "escalation", "complication", "midpoint_twist", "crisis", "climax_and_reset"]


def _arc(episodes):
    return [
        {"ep": ep, "function": _ARC_FUNCTIONS[min(ep - 1, 5)], "summary": f"Episode {ep}: the vote looms.",
         "open_hooks_in": [] if ep == 1 else ["who stole the coconut phone"],
         "open_hooks_out": ["who stole the coconut phone"], "characters": ["char_kiwilo"]}
        for ep in episodes
    ]


def _season(**changes):
    doc = {
        "$schema": "season_arc_v1",
        "episodes_planned": 8,
        "arc": [],
        "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
        "audience_feedback": [],
        "approved_at": None,
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _proposal(**changes):
    doc = {
        "$schema": "places_proposal_v1",
        "places": [{"name": "Beach camp", "one_line": "Where the couples sleep."},
                   {"name": "Vote hut", "one_line": "Where someone leaves every week."}],
        "props": [{"name": "Coconut phone", "one_line": "The only line off the island.", "owner": "char_kiwilo"},
                  {"name": "Tiki torch", "one_line": "Snuffed when you are out.", "owner": None}],
        "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _words(n):
    return " ".join(["mot"] * n)


def _at(doc, path):
    *parents, last = path.split(".")
    for key in parents:
        doc = doc[int(key)] if isinstance(doc, list) else doc[key]
    return doc, (int(last) if isinstance(doc, list) else last)


def _set(path, value):
    def change(doc):
        parent, key = _at(doc, path)
        parent[key] = copy.deepcopy(value)
    return change


def _drop(path):
    def change(doc):
        parent, key = _at(doc, path)
        del parent[key]
    return change


def _changed(doc, change):
    doc = copy.deepcopy(doc)
    change(doc)
    return doc


# --------------------------------------------------------------- schema: good

@pytest.mark.parametrize("errors, build", [
    ("character_errors", _character),
    ("character_errors", _written_character),
    ("place_errors", _place),
    ("place_errors", _written_place),
    ("prop_errors", _prop),
    ("prop_errors", _written_prop),
    ("season_arc_errors", _season),
    ("season_arc_errors", lambda: _season(arc=_arc(range(1, 9)), approved_at=LATER)),
    ("season_arc_errors", lambda: _season(episodes_planned=3, arc=_arc(range(1, 4)))),
    ("season_arc_errors", lambda: _season(episodes_planned=12, arc=_arc(range(1, 13)))),
    ("places_proposal_errors", _proposal),
    ("places_proposal_errors", lambda: _proposal(places=[], props=[])),
], ids=lambda value: getattr(value, "__name__", value) if not isinstance(value, str) else value)
def test_a_good_document_validates(errors, build):
    assert getattr(schemas, errors)(build()) == []


@pytest.mark.parametrize("change", [
    _set("signature_items", []),
    _set("signature_items", ["one"]),
    _set("signature_items", ["one", "two", "three"]),
    _set("archetype", ""),
    _set("role", "guest"),
    _set("role", "recurring"),
    _set("source", "custom"),
])
def test_an_unwritten_character_may_have_up_to_three_items(change):
    """2-3 signature items only once the descriptor is written."""
    assert schemas.character_errors(_changed(_character(), change)) == []


# The keys phase 5 adds to objects phase 2 already wrote (plan 11, stage 1):
# optional, so a season on disk without them still validates. Every other
# property of every object stays required.
PHASE5_OPTIONAL = {
    # Plan 20 stage 2, re-pinned on purpose: the plot archetypes a v2 story's
    # S1 chose (the season's, and each entry's), absent on every season before.
    # (plan 21 stage 1 adds ``approved_by`` to the same object: one entry, two keys)
    ("SEASON_ARC_SCHEMA", "$"): {"archetypes", "approved_by"},
    ("SEASON_ARC_SCHEMA", "$.properties.arc.items"): {"history", "archetype"},
    ("SEASON_ARC_SCHEMA", "$.properties.series_memory"): {"entries"},
    ("SEASON_ARC_SCHEMA", "$.properties.audience_feedback.items"): {"stats", "digest", "directions",
                                                                     "chosen_direction"},
    # Phase 7 stage 3a (DEC-226): the structured look and the dossier are
    # optional blocks, absent on every stored story.
    # Plan 21 stage 1, re-pinned on purpose: ``approved_by`` -- the agent run's
    # mark beside an approval it gave -- is optional on each, absent on every
    # approval a human gave.
    # Plan 23 stages D5 + B4, re-pinned on purpose: the appearance variants and
    # the voice reference (its consent, DEC-281) are optional on the character
    # (absent on every character written before them), and in a variant's refs
    # the sheets its story's sheet mode may not draw.
    # Plan 28 F3 (DEC-305 section 5), re-pinned on purpose: the sheet judge's
    # verdicts (``sheet_checks``) are optional on each entity, absent on every
    # entity whose images were made before the rule.
    ("CHARACTER_SCHEMA", "$"): {"look", "dossier", "approved_by", "variants", "voice_reference", "sheet_checks"},
    ("CHARACTER_SCHEMA", "$.properties.variants.items.properties.refs"): {"turnaround", "expressions"},
    ("PLACE_SCHEMA", "$"): {"look", "approved_by", "sheet_checks"},
    ("PROP_SCHEMA", "$"): {"look", "approved_by", "sheet_checks"},
    # Fix A3 (phase 7 quality overhaul): apparent age and gender presentation,
    # optional on the character look -- absent on every look written before it;
    # phase 7 follow-up, stage F2: the bearing (posture), the same way; plan 26
    # stage 7a: the head's species, the same way.
    ("CHARACTER_SCHEMA", "$.properties.look"): {"presentation", "bearing", "species"},
}


@pytest.mark.parametrize("schema_name", [
    "CHARACTER_SCHEMA", "PLACE_SCHEMA", "PROP_SCHEMA", "SEASON_ARC_SCHEMA", "PLACES_PROPOSAL_SCHEMA",
])
def test_every_fixed_object_is_closed(schema_name):
    def objects(schema, path="$"):
        if isinstance(schema, dict):
            if "properties" in schema:
                yield path, schema
            for key, value in schema.items():
                yield from objects(value, f"{path}.{key}")
        elif isinstance(schema, list):
            for i, item in enumerate(schema):
                yield from objects(item, f"{path}[{i}]")

    found = list(objects(getattr(schemas, schema_name)))
    assert found and found[0][0] == "$"
    for path, obj in found:
        assert obj.get("additionalProperties") is False, path
        optional = PHASE5_OPTIONAL.get((schema_name, path), set())
        assert optional <= set(obj["properties"]), path
        assert set(obj["required"]) == set(obj["properties"]) - optional, path
    assert {path for name, path in PHASE5_OPTIONAL if name == schema_name} <= {path for path, _ in found}


def test_the_consistency_labels():
    assert schemas.CONSISTENCY == ("base", "references", "prompt_only")


# ----------------------------------------------------------- schema: refused

CHARACTER_BREAKS = {
    "extra key": _set("surprise", 1),
    "no updated_at": _drop("updated_at"),
    "wrong $schema": _set("$schema", "character_v2"),
    "id uppercase": _set("char_id", "char_Kiwilo"),
    "id without prefix": _set("char_id", "kiwilo"),
    "id of a place": _set("char_id", "place_kiwilo"),
    "id empty slug": _set("char_id", "char_"),
    "id too long": _set("char_id", "char_" + "a" * 41),
    "id newline": _set("char_id", "char_kiwilo\n"),
    "id traversal": _set("char_id", "char_../x"),
    "name empty": _set("name", ""),
    "name blank": _set("name", "   "),
    "name too long": _set("name", "K" * 61),
    "role": _set("role", "villain"),
    "archetype too long": _set("archetype", "a" * 61),
    "one_line empty": _set("one_line", ""),
    "one_line too long": _set("one_line", "a" * 201),
    "descriptor empty": _set("descriptor", ""),
    "descriptor too many words": _set("descriptor", _words(46)),
    "descriptor blank": _set("descriptor", "   "),
    "written with one item": _set("signature_items", ["gold chain"]),
    "written with no item": _set("signature_items", []),
    "four items": _set("signature_items", ["a", "b", "c", "d"]),
    "item too long": _set("signature_items", ["a" * 61, "b"]),
    "item empty": _set("signature_items", ["", "b"]),
    "personality extra key": _set("personality.mood", "sunny"),
    "personality missing fears": _drop("personality.fears"),
    "six traits": _set("personality.traits", ["t"] * 6),
    "trait too long": _set("personality.traits", ["t" * 41]),
    "wants too long": _set("personality.wants", "w" * 201),
    "speech_style not text": _set("personality.speech_style", 3),
    "relationship to a non-id": _set("relationships", {"mangella": "secret ex"}),
    "relationship to itself": _set("relationships", {"char_kiwilo": "himself"}),
    "relationship empty": _set("relationships", {"char_mangella": ""}),
    "relationship too long": _set("relationships", {"char_mangella": "x" * 121}),
    "relationship not text": _set("relationships", {"char_mangella": 5}),
    "relationships not an object": _set("relationships", ["char_mangella"]),
    "voice extra key": _set("voice.speed", 1.2),
    "voice missing sample_line": _drop("voice.sample_line"),
    "voice provider": _set("voice.provider", "Edge"),
    "voice_id empty": _set("voice.voice_id", ""),
    "rate without sign": _set("voice.rate", "5%"),
    "rate without percent": _set("voice.rate", "+5"),
    "rate four digits": _set("voice.rate", "+1000%"),
    "rate non-ascii digit": _set("voice.rate", "+\u0665%"),
    "pitch lowercase hz": _set("voice.pitch", "-2hz"),
    "pitch without unit": _set("voice.pitch", "-2"),
    "direction too long": _set("voice.direction", "d" * 201),
    "sample line thirteen words": _set("voice.sample_line", _words(13)),
    "sample line too long": _set("voice.sample_line", "s" * 121),
    "refs extra key": _set("refs.sketch", None),
    "portrait named as turnaround": _set("refs.portrait.name", "turnaround.png"),
    "portrait made from references": _set("refs.portrait.consistency", "references"),
    "portrait prompt_only": _set("refs.portrait.consistency", "prompt_only"),
    "turnaround labelled base": _set("refs.turnaround.consistency", "base"),
    "expressions named as portrait": _set("refs.expressions.name", "portrait.png"),
    "image ref extra key": _set("refs.portrait.path", "refs/portrait.png"),
    "image ref missing seed": _drop("refs.portrait.seed"),
    "image ref negative seed": _set("refs.portrait.seed", -1),
    "image ref text seed": _set("refs.portrait.seed", "7"),
    "image ref empty source": _set("refs.portrait.source", ""),
    "image ref unknown consistency": _set("refs.portrait.consistency", "prompt-only"),
    "image ref gif": _set("refs.portrait.name", "portrait.gif"),
    "image ref traversal": _set("refs.portrait.name", "../portrait.png"),
    "image ref a path": _set("refs.portrait.name", "refs/portrait.png"),
    "extra named as portrait": _set("refs.extra", [_image("portrait.png", "references")]),
    "extra listed twice": _set("refs.extra", [_image("extra_01.png", "references")] * 2),
    "five uploads": _set("refs.uploads", [
        {"name": f"{n:032x}.png", "description": None, "uploaded_at": NOW} for n in range(5)]),
    "upload not a uuid": _set("refs.uploads.0.name", "kiwi.png"),
    "upload uppercase hex": _set("refs.uploads.0.name", UPLOAD.upper()),
    "upload a jpeg": _set("refs.uploads.0.name", UPLOAD[:-4] + ".jpg"),
    "upload description too long": _set("refs.uploads.0.description", "d" * 401),
    "upload listed twice": _set("refs.uploads", [
        {"name": UPLOAD, "description": None, "uploaded_at": NOW}] * 2),
    "ref_seed negative": _set("ref_seed", -1),
    "ref_seed float": _set("ref_seed", 1.5),
    "state extra key": _set("state.mood", "sunny"),
    "state alive text": _set("state.alive", "yes"),
    "state location not a place": _set("state.location", "char_kiwilo"),
    "state note empty": _set("state.arc_notes", [""]),
    "source": _set("source", "imported"),
    "approved_at empty": _set("approved_at", ""),
    "created_at empty": _set("created_at", ""),
}


@pytest.mark.parametrize("change", list(CHARACTER_BREAKS.values()), ids=list(CHARACTER_BREAKS))
def test_a_broken_character_is_refused(change):
    assert schemas.character_errors(_written_character()) == []
    assert schemas.character_errors(_changed(_written_character(), change)) != []


PLACE_BREAKS = {
    "extra key": _set("surprise", 1),
    "a separate master_plate": _set("master_plate", _image("variant_day.png")),
    "wrong $schema": _set("$schema", "place_v2"),
    "id of a character": _set("place_id", "char_beach"),
    "id uppercase": _set("place_id", "place_Beach"),
    "name too long": _set("name", "n" * 61),
    "name blank": _set("name", " "),
    "one_line too long": _set("one_line", "o" * 201),
    "descriptor too many words": _set("descriptor", _words(46)),
    "layout notes too many words": _set("layout_notes", _words(61)),
    "layout notes empty": _set("layout_notes", ""),
    "no day": _drop("time_variants.day"),
    "variants not an object": _set("time_variants", [None]),
    "variant uppercase": _set("time_variants.Night", None),
    "variant too long": _set("time_variants." + "n" * 21, None),
    "variant starts with a digit": _set("time_variants.1am", None),
    "variant not an image ref": _set("time_variants.night", "variant_night.png"),
    "variant image extra key": _set("time_variants.night.path", "x"),
    "variant named for another": _set("time_variants.night.name", "variant_rain.png"),
    "variant named as a portrait": _set("time_variants.night.name", "portrait.png"),
    "master plate made from references": _set("time_variants.day.consistency", "references"),
    "variant labelled base": _set("time_variants.night.consistency", "base"),
    "approved_at empty": _set("approved_at", ""),
}


@pytest.mark.parametrize("change", list(PLACE_BREAKS.values()), ids=list(PLACE_BREAKS))
def test_a_broken_place_is_refused(change):
    assert schemas.place_errors(_written_place()) == []
    assert schemas.place_errors(_changed(_written_place(), change)) != []


PROP_BREAKS = {
    "extra key": _set("surprise", 1),
    "wrong $schema": _set("$schema", "prop_v2"),
    "id of a place": _set("prop_id", "place_phone"),
    "name too long": _set("name", "n" * 61),
    "one_line empty": _set("one_line", ""),
    "descriptor too many words": _set("descriptor", _words(31)),
    "owner not a character": _set("owner_char_id", "place_beach_camp"),
    "image named as a portrait": _set("image.name", "portrait.png"),
    "image made from references": _set("image.consistency", "references"),
    "image extra key": _set("image.path", "refs/image.png"),
    "no created_at": _drop("created_at"),
}


@pytest.mark.parametrize("change", list(PROP_BREAKS.values()), ids=list(PROP_BREAKS))
def test_a_broken_prop_is_refused(change):
    assert schemas.prop_errors(_written_prop()) == []
    assert schemas.prop_errors(_changed(_written_prop(), change)) != []


SEASON_BREAKS = {
    "extra key": _set("surprise", 1),
    "wrong $schema": _set("$schema", "season_arc_v2"),
    "two episodes": _set("episodes_planned", 2),
    "thirteen episodes": _set("episodes_planned", 13),
    "episodes as text": _set("episodes_planned", "8"),
    "episodes as a boolean": _set("episodes_planned", True),
    "entry extra key": _set("arc.0.twist", "x"),
    "entry missing hooks": _drop("arc.0.open_hooks_in"),
    "episode zero": _set("arc.0.ep", 0),
    "unknown function": _set("arc.0.function", "finale"),
    "summary too many words": _set("arc.0.summary", _words(61)),
    "summary empty": _set("arc.0.summary", ""),
    "hook too long": _set("arc.0.open_hooks_out", ["h" * 121]),
    "character not an id": _set("arc.0.characters", ["kiwilo"]),
    "arc incomplete": _set("arc", _arc(range(1, 8))),
    "arc out of order": _set("arc", _arc([2, 1, 3, 4, 5, 6, 7, 8])),
    "arc with a gap": _set("arc", _arc([1, 2, 3, 4, 5, 6, 7, 9])),
    "arc with a repeat": _set("arc", _arc([1, 1, 2, 3, 4, 5, 6, 7])),
    "arc longer than planned": _set("arc", _arc(range(1, 10))),
    "arc not starting at one": _set("arc", _arc(range(2, 10))),
    "memory missing recaps": _drop("series_memory.recaps"),
    "memory extra key": _set("series_memory.cliffhangers", []),
    "feedback not a list": _set("audience_feedback", {}),
    "an empty arc approved": _set("arc", []),
    "approved_at empty": _set("approved_at", ""),
}


# Phase 5 (plan 11, stage 1): series_memory.entries (one S3 entry per
# episode, the spec-2.6 fields folded from them), typed audience_feedback
# items and an arc entry's history. Each break below differs from
# ``_phase5_good`` (which validates) by one rule.

_PHONE = "who stole the coconut phone"


def _memory_entry(**changes):
    entry = {"recap": "Kiwilo hid the coconut phone.", "hooks_opened": [_PHONE], "hooks_closed": [],
             "relationship_deltas": {"char_kiwilo|char_mangella": "rivals"}, "script_rev": 1, "at": NOW,
             "approved_at": None}
    entry.update(changes)
    return entry


def _memory(entry, key="ep01"):
    """series_memory holding one entry, the derived fields folded by hand
    (one entry: its recap, its hooks, its deltas)."""
    def change(doc):
        doc["series_memory"] = {
            "recaps": {key: entry.get("recap")},
            "open_hooks": list(entry.get("hooks_opened") or []),
            "relationship_state": dict(entry.get("relationship_deltas") or {}),
            "introduced": {},
            "entries": {key: copy.deepcopy(entry)},
        }
    return change


def _without(entry, key):
    entry = copy.deepcopy(entry)
    del entry[key]
    return entry


def _then(*changes):
    def change(doc):
        for each in changes:
            each(doc)
    return change


_HISTORY = {"summary": "Episode 1: the vote looms.", "open_hooks_out": [_PHONE], "replaced_at": LATER,
            "source": "proposal"}
_FEEDBACK = {"ep": 1, "pasted_at": NOW, "text": "top comments", "digest": "They love Mangella."}

SEASON_BREAKS.update({
    "memory entries not an object": _set("series_memory.entries", []),
    "memory entry key ep1": _memory(_memory_entry(), key="ep1"),
    "memory entry key ep00": _memory(_memory_entry(), key="ep00"),
    "memory entry extra key": _memory(_memory_entry(cliffhanger="x")),
    "memory entry missing script_rev": _memory(_without(_memory_entry(), "script_rev")),
    "memory entry missing approved_at": _memory(_without(_memory_entry(), "approved_at")),
    "memory recap over 40 words": _memory(_memory_entry(recap=_words(41))),
    "memory recap blank": _memory(_memory_entry(recap="  ")),
    "memory hook over 120 characters": _memory(_memory_entry(hooks_opened=["h" * 121])),
    "memory four hooks opened": _memory(_memory_entry(hooks_opened=["a", "b", "c", "d"])),
    "memory pair key unsorted": _memory(_memory_entry(relationship_deltas={"char_mangella|char_kiwilo": "x"})),
    "memory pair key one character": _memory(_memory_entry(relationship_deltas={"char_kiwilo|char_kiwilo": "x"})),
    "memory pair key not ids": _memory(_memory_entry(relationship_deltas={"kiwilo|mangella": "x"})),
    "memory script_rev zero": _memory(_memory_entry(script_rev=0)),
    "memory closes a hook never opened": _memory(_memory_entry(hooks_closed=["never opened"])),
    "memory open_hooks disagree with entries": _then(_memory(_memory_entry()), _set("series_memory.open_hooks", [])),
    "memory recaps disagree with entries": _then(_memory(_memory_entry()), _set("series_memory.recaps", {})),
    "memory relationships disagree with entries": _then(_memory(_memory_entry()),
                                                        _set("series_memory.relationship_state", {})),
    "history not a list": _set("arc.0.history", _HISTORY),
    "history missing replaced_at": _set("arc.0.history", [_without(_HISTORY, "replaced_at")]),
    "history unknown source": _set("arc.0.history", [dict(_HISTORY, source="edit")]),
    "history summary too many words": _set("arc.0.history", [dict(_HISTORY, summary=_words(61))]),
    "history extra key": _set("arc.0.history", [dict(_HISTORY, ep=1)]),
    # Stage 4 made "stats" a field (the pasted stats F1 reads): another key is the extra one.
    "feedback item extra key": _set("audience_feedback", [dict(_FEEDBACK, views="x")]),
    "feedback text over 6000 characters": _set("audience_feedback", [dict(_FEEDBACK, text="t" * 6001)]),
    "feedback stats over 6000 characters": _set("audience_feedback", [dict(_FEEDBACK, stats="s" * 6001)]),
    "feedback stats blank": _set("audience_feedback", [dict(_FEEDBACK, stats="   ")]),
    "feedback two directions": _set("audience_feedback", [dict(_FEEDBACK, directions=["a", "b"])]),
    "feedback chosen without directions": _set("audience_feedback", [dict(_FEEDBACK, chosen_direction=0)]),
    "feedback chosen out of range": _set("audience_feedback", [dict(_FEEDBACK, directions=["a", "b", "c"],
                                                                     chosen_direction=3)]),
})


def _phase5_good():
    good = _season(arc=_arc(range(1, 9)), approved_at=LATER)
    _memory(_memory_entry())(good)
    good["arc"][0]["history"] = [copy.deepcopy(_HISTORY)]
    good["audience_feedback"] = [dict(_FEEDBACK), dict(_FEEDBACK, ep=2, stats="12 400 views",
                                                       directions=["a", "b", "c"], chosen_direction=None)]
    return good


def test_a_season_with_memory_entries_history_and_feedback_validates():
    # The good twin of every phase-5 break above.
    assert schemas.season_arc_errors(_phase5_good()) == []


@pytest.mark.parametrize("change", list(SEASON_BREAKS.values()), ids=list(SEASON_BREAKS))
def test_a_broken_season_is_refused(change):
    good = _season(arc=_arc(range(1, 9)), approved_at=LATER)
    assert schemas.season_arc_errors(good) == []
    assert schemas.season_arc_errors(_changed(good, change)) != []


def test_an_empty_arc_is_allowed_before_s1_runs():
    assert schemas.season_arc_errors(_season(arc=[], approved_at=None)) == []


PROPOSAL_BREAKS = {
    "extra key": _set("surprise", 1),
    "wrong $schema": _set("$schema", "places_proposal_v2"),
    "seven places": _set("places", [{"name": f"P{n}", "one_line": "x"} for n in range(7)]),
    "seven props": _set("props", [{"name": f"P{n}", "one_line": "x", "owner": None} for n in range(7)]),
    "place extra key": _set("places.0.owner", None),
    "prop missing owner": _drop("props.1.owner"),
    "owner not a character": _set("props.0.owner", "kiwilo"),
    "place name too long": _set("places.0.name", "n" * 61),
    "prop one_line too long": _set("props.0.one_line", "o" * 201),
    "place name blank": _set("places.0.name", "  "),
    "no updated_at": _drop("updated_at"),
}


@pytest.mark.parametrize("change", list(PROPOSAL_BREAKS.values()), ids=list(PROPOSAL_BREAKS))
def test_a_broken_places_proposal_is_refused(change):
    assert schemas.places_proposal_errors(_changed(_proposal(), change)) != []


# ------------------------------------------------------------- story schema

def _bible(**changes):
    doc = {
        "$schema": "story_bible_v1", "story_id": "0123456789ab", "title": "", "language": "fr",
        "seed_text": None, "concept_id": None, "concept": None, "logline": None, "premise": None,
        "tone": None, "genre_tags": [], "world": None, "themes_and_values": [], "audience": None,
        "why_come_back": [], "cast_ids": ["char_kiwilo"], "place_ids": ["place_beach_camp"],
        "prop_ids": ["prop_coconut_phone"], "style_template_id": None,
        "episode_template_id": "serial_60s_v1",
        "generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references",
                               "budget_profile": "free"},
        "narrator": {"enabled": False, "voice": None},
        "approvals": {"concept": NOW, "bible": NOW, "style": NOW, "cast": NOW, "places": NOW, "season": NOW},
        "status": "ready", "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_a_story_with_entities_and_every_approval_validates():
    assert schemas.story_bible_errors(_bible()) == []


@pytest.mark.parametrize("changes", [
    {"cast_ids": ["kiwilo"]},
    {"cast_ids": ["place_beach_camp"]},
    {"cast_ids": ["char_../x"]},
    {"place_ids": ["char_kiwilo"]},
    {"prop_ids": ["place_beach_camp"]},
    {"prop_ids": ["prop_Phone"]},
    {"approvals": {"concept": NOW, "bible": NOW, "style": NOW, "cast": NOW, "places": NOW}},
    {"approvals": {"concept": NOW, "bible": NOW, "style": NOW, "cast": "", "places": None, "season": None}},
    {"approvals": {"concept": NOW, "bible": NOW, "style": NOW, "cast": None, "places": None,
                   "season": None, "episodes": None}},
])
def test_a_story_with_a_bad_entity_list_or_approvals_is_refused(changes):
    assert schemas.story_bible_errors(_bible()) == []
    assert schemas.story_bible_errors(_bible(**changes)) != []


# ------------------------------------------------------------ slugs and ids

@pytest.mark.parametrize("name, slug", [
    ("Mamie Figue", "mamie_figue"),
    ("Kiwilo", "kiwilo"),
    ("Éléonore", "eleonore"),
    ("Sœur Nœl", "soeur_noel"),
    ("L'île aux Ananas", "l_ile_aux_ananas"),
    ("  Coco--Phone 3000 !! ", "coco_phone_3000"),
    ("Straße", "strasse"),
    ("Ærø", "aero"),
    ("MANGELLA", "mangella"),
    ("", "item"),
    ("   ", "item"),
    ("!!!", "item"),
    ("東京", "item"),
    ("a" * 60, "a" * 40),
    ("ab " * 30, ("ab_" * 14)[:40].rstrip("_")),
])
def test_slugify(name, slug):
    assert schemas.slugify(name) == slug


@pytest.mark.parametrize("name", [None, 7, ["Kiwilo"]])
def test_slugify_needs_a_string(name):
    with pytest.raises(ValueError):
        schemas.slugify(name)


@pytest.mark.parametrize("kind, name, taken, expected", [
    ("characters", "Mamie Figue", (), "char_mamie_figue"),
    ("places", "Beach Camp", (), "place_beach_camp"),
    ("props", "Coconut phone", (), "prop_coconut_phone"),
    ("characters", "Kiwilo", {"char_kiwilo"}, "char_kiwilo_2"),
    ("characters", "Kiwilo", ["char_kiwilo", "char_kiwilo_2"], "char_kiwilo_3"),
    ("characters", "Kiwilo", {"place_kiwilo", "prop_kiwilo"}, "char_kiwilo"),
    ("characters", "", (), "char_item"),
    ("characters", "", {"char_item"}, "char_item_2"),
    ("characters", "?", {"char_item", "char_item_2"}, "char_item_3"),
    ("characters", "a" * 50, {"char_" + "a" * 40}, "char_" + "a" * 38 + "_2"),
    ("places", "ab " * 30, {"place_" + ("ab_" * 14)[:40].rstrip("_")}, "place_" + "ab_" * 13 + "2"),
])
def test_entity_id(kind, name, taken, expected):
    assert schemas.entity_id(kind, name, taken) == expected


def test_every_entity_id_matches_its_kind_pattern():
    patterns = {"characters": schemas.CHAR_ID_PATTERN, "places": schemas.PLACE_ID_PATTERN,
                "props": schemas.PROP_ID_PATTERN}
    names = ["Mamie Figue", "", "東京", "a" * 80, "x_" * 40, "É" * 45, "9 lives"]
    for kind, pattern in patterns.items():
        taken = set()
        for name in names * 12:
            new = schemas.entity_id(kind, name, taken)
            assert new not in taken
            assert schemas.validate(new, {"type": "string", "pattern": pattern}) == [], new
            taken.add(new)


@pytest.mark.parametrize("kind", ["character", "Characters", "episodes", "", None, ["characters"]])
def test_entity_id_of_an_unknown_kind_is_refused(kind):
    with pytest.raises(ValueError):
        schemas.entity_id(kind, "Kiwilo")


# ------------------------------------------------------------------- store

@pytest.fixture
def outputs(tmp_path):
    path = tmp_path / "outputs"
    path.mkdir()
    return path


@pytest.fixture
def logs():
    return []


@pytest.fixture
def stories(outputs, logs):
    return store.StoryStore(str(outputs), on_log=logs.append)


@pytest.fixture
def story_id(stories):
    return stories.create(language="fr", now=NOW)["story_id"]


def _doc_for(kind, eid, **changes):
    build = {"characters": _character, "places": _place, "props": _prop}[kind]
    field = {"characters": "char_id", "places": "place_id", "props": "prop_id"}[kind]
    return build(**{field: eid, **changes})


FIRST = {"characters": "char_kiwilo", "places": "place_beach_camp", "props": "prop_coconut_phone"}


@pytest.fixture
def seeded(stories, story_id):
    """A story holding one entity of each kind."""
    for kind, eid in FIRST.items():
        stories.write_entity(story_id, kind, _doc_for(kind, eid), now=NOW)
    return story_id


def _story_dir(outputs, story_id):
    return outputs / "stories" / story_id


def _temp_files(outputs):
    return [p for p in outputs.rglob("*") if p.name.startswith(".story-") or p.suffix == ".tmp"]


def _symlink(target, link):
    try:
        os.symlink(target, link, target_is_directory=pathlib.Path(target).is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")


def _approve_first_three(stories, story_id):
    stories.update(story_id, lambda doc: doc["approvals"].update(concept=NOW, bible=NOW, style=NOW), now=NOW)


# -------------------------------------------------------- store: write/read

@pytest.mark.parametrize("kind", ["characters", "places", "props"])
def test_write_entity_then_read_entity_round_trips(stories, outputs, story_id, kind):
    eid = FIRST[kind]
    doc = _doc_for(kind, eid)

    saved = stories.write_entity(story_id, kind, doc, now=LATER)

    assert saved == dict(doc, updated_at=LATER)
    assert doc["updated_at"] == NOW, "the caller's dict is not modified"
    assert stories.read_entity(story_id, kind, eid) == saved
    filename = {"characters": "character.json", "places": "place.json", "props": "prop.json"}[kind]
    path = _story_dir(outputs, story_id) / kind / eid / filename
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert os.stat(path).st_mode & 0o777 == 0o644
    assert stories.entity_dir(story_id, kind, eid) == os.path.realpath(path.parent)


def test_write_entity_lists_the_id_once_and_bumps_the_story(stories, outputs, story_id):
    stories.write_entity(story_id, "characters", _character(), now=LATER)
    stories.write_entity(story_id, "characters", _character(name="Kiwilo II"), now=LATEST)
    stories.write_entity(story_id, "places", _place(), now=LATEST)
    stories.write_entity(story_id, "props", _prop(), now=LATEST)

    story = stories.get(story_id)
    assert story["cast_ids"] == ["char_kiwilo"]
    assert story["place_ids"] == ["place_beach_camp"]
    assert story["prop_ids"] == ["prop_coconut_phone"]
    assert story["updated_at"] == LATEST
    index = json.loads((outputs / "stories.json").read_text(encoding="utf-8"))
    assert index["stories"][story_id]["updated_at"] == LATEST
    assert stories.read_entity(story_id, "characters", "char_kiwilo")["name"] == "Kiwilo II"


@pytest.mark.parametrize("kind, doc", [
    ("characters", _character(role="villain")),
    ("characters", _character(descriptor=_words(46))),
    ("characters", _place(place_id="char_kiwilo")),
    ("places", _character()),
    ("props", _prop(owner_char_id="kiwilo")),
])
def test_an_invalid_entity_writes_nothing(stories, outputs, story_id, kind, doc):
    before = (_story_dir(outputs, story_id) / "story.json").read_bytes()
    with pytest.raises(ValueError):
        stories.write_entity(story_id, kind, doc, now=LATER)
    assert not (_story_dir(outputs, story_id) / kind).exists()
    assert (_story_dir(outputs, story_id) / "story.json").read_bytes() == before


def test_a_caller_validator_can_refuse_the_write(stories, outputs, story_id):
    with pytest.raises(schemas.SchemaError):
        stories.write_entity(story_id, "characters", _character(), now=LATER,
                             validator=lambda doc: ["$.descriptor: K1 has not run"])
    assert not (_story_dir(outputs, story_id) / "characters").exists()
    assert stories.get(story_id)["cast_ids"] == []


def test_an_entity_of_an_unknown_or_invalid_story_is_refused(stories, outputs, story_id):
    with pytest.raises(KeyError):
        stories.write_entity("0123456789ab", "characters", _character(), now=LATER)
    assert not (outputs / "stories" / "0123456789ab").exists()

    path = _story_dir(outputs, story_id) / "story.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"language": "fr"', '"language": "de"'),
                    encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.write_entity(story_id, "characters", _character(), now=LATER)
    assert not (_story_dir(outputs, story_id) / "characters").exists()


def test_a_failed_entity_write_keeps_the_old_document(stories, outputs, seeded, monkeypatch):
    path = _story_dir(outputs, seeded) / "characters" / "char_kiwilo" / "character.json"
    before = path.read_bytes()

    def torn_dump(obj, fh, **kwargs):
        fh.write('{"$schema": "character_v1", ')
        raise OSError("No space left on device")

    monkeypatch.setattr(store.json, "dump", torn_dump)
    with pytest.raises(OSError):
        stories.write_entity(seeded, "characters", _character(name="Lost"), now=LATER)
    monkeypatch.undo()

    assert path.read_bytes() == before
    assert _temp_files(outputs) == []


def test_read_entity_of_a_missing_entity_is_a_key_error(stories, outputs, seeded):
    with pytest.raises(KeyError):
        stories.read_entity(seeded, "characters", "char_mangella")
    (_story_dir(outputs, seeded) / "characters" / "char_mangella").mkdir()
    with pytest.raises(KeyError):
        stories.read_entity(seeded, "characters", "char_mangella")


def test_an_invalid_entity_document_raises_and_is_not_repaired(stories, outputs, seeded):
    path = _story_dir(outputs, seeded) / "places" / "place_beach_camp" / "place.json"
    path.write_text('{"$schema": "place_v1", ', encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.read_entity(seeded, "places", "place_beach_camp")
    assert path.read_text(encoding="utf-8") == '{"$schema": "place_v1", '


def test_an_entity_document_copied_into_another_folder_is_refused(stories, outputs, seeded):
    folder = _story_dir(outputs, seeded) / "characters"
    (folder / "char_copy").mkdir()
    shutil.copy(folder / "char_kiwilo" / "character.json", folder / "char_copy" / "character.json")
    with pytest.raises(schemas.SchemaError):
        stories.read_entity(seeded, "characters", "char_copy")


def test_a_symlinked_entity_document_is_never_read(stories, outputs, seeded, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(_character(char_id="char_mangella")), encoding="utf-8")
    folder = _story_dir(outputs, seeded) / "characters" / "char_mangella"
    folder.mkdir()
    _symlink(secret, folder / "character.json")
    with pytest.raises(schemas.SchemaError):
        stories.read_entity(seeded, "characters", "char_mangella")


# ------------------------------------------------------------ store: list

def test_list_entities_is_oldest_first(stories, story_id):
    for eid, created in (("char_b", LATER), ("char_a", LATEST), ("char_c", NOW)):
        stories.write_entity(story_id, "characters", _character(char_id=eid, created_at=created), now=LATEST)

    assert [doc["char_id"] for doc in stories.list_entities(story_id, "characters")] == [
        "char_c", "char_b", "char_a"]
    assert stories.list_entities(story_id, "places") == []


def test_an_invalid_entity_folder_is_skipped_and_printed_never_deleted(stories, outputs, seeded, logs):
    folder = _story_dir(outputs, seeded) / "characters"
    (folder / "char_broken").mkdir()
    (folder / "char_broken" / "character.json").write_text("not json", encoding="utf-8")
    (folder / "char_wrong").mkdir()
    (folder / "char_wrong" / "character.json").write_text(
        json.dumps(_character(char_id="char_other")), encoding="utf-8")
    (folder / "char_empty").mkdir()
    (folder / "char_file").write_text("a file", encoding="utf-8")
    (folder / "notes").mkdir()
    del logs[:]

    listed = stories.list_entities(seeded, "characters")

    assert [doc["char_id"] for doc in listed] == ["char_kiwilo"]
    prefix = f"Skipped outputs/stories/{seeded}/characters/"
    assert [line.split(":")[0] for line in logs] == [
        f"{prefix}char_broken/", f"{prefix}char_empty/", f"{prefix}char_file/", f"{prefix}char_wrong/"]
    assert "character.json is invalid" in logs[0]
    assert (folder / "char_broken" / "character.json").read_text(encoding="utf-8") == "not json"
    assert (folder / "char_wrong" / "character.json").exists()
    assert (folder / "char_empty").is_dir() and (folder / "char_file").is_file()
    assert (folder / "notes").is_dir()


# ------------------------------------------------------------ store: delete

def test_delete_entity_removes_its_folder_and_its_id(stories, outputs, seeded):
    stories.write_entity(seeded, "characters", _character(char_id="char_mangella"), now=NOW)

    report = stories.delete_entity(seeded, "characters", "char_kiwilo", now=LATER)

    assert report == {"removed": [f"outputs/stories/{seeded}/characters/char_kiwilo/"], "kept": []}
    story = stories.get(seeded)
    assert story["cast_ids"] == ["char_mangella"] and story["updated_at"] == LATER
    assert not (_story_dir(outputs, seeded) / "characters" / "char_kiwilo").exists()
    assert stories.read_entity(seeded, "characters", "char_mangella")["char_id"] == "char_mangella"
    assert stories.read_entity(seeded, "places", "place_beach_camp")
    with pytest.raises(KeyError):
        stories.delete_entity(seeded, "characters", "char_kiwilo", now=LATEST)


def test_delete_entity_of_a_listed_id_without_a_folder(stories, outputs, seeded):
    shutil.rmtree(_story_dir(outputs, seeded) / "props" / "prop_coconut_phone")
    assert stories.delete_entity(seeded, "props", "prop_coconut_phone", now=LATER) == {"removed": [], "kept": []}
    assert stories.get(seeded)["prop_ids"] == []


def test_delete_entity_removes_a_folder_the_story_does_not_list(stories, outputs, seeded):
    stories.update(seeded, lambda doc: doc.update(place_ids=[]), now=LATER)
    report = stories.delete_entity(seeded, "places", "place_beach_camp", now=LATEST)
    assert report["removed"] == [f"outputs/stories/{seeded}/places/place_beach_camp/"]


def test_delete_entity_of_an_unknown_entity_is_a_key_error(stories, outputs, seeded):
    before = (_story_dir(outputs, seeded) / "story.json").read_bytes()
    with pytest.raises(KeyError):
        stories.delete_entity(seeded, "characters", "char_nobody", now=LATER)
    assert (_story_dir(outputs, seeded) / "story.json").read_bytes() == before


# ------------------------------------------------------- store: containment

BAD_ENTITY_IDS = [
    ("characters", "char_../x"),
    ("characters", "char_A"),
    ("characters", "char_Kiwilo"),
    ("places", "place_x/y"),
    ("characters", "../"),
    ("characters", "/etc/passwd"),
    ("characters", "/tmp/char_x"),
    ("characters", "char_x\n"),
    ("characters", "char_x/"),
    ("characters", "char_x\\y"),
    ("characters", ""),
    ("characters", "."),
    ("characters", ".."),
    ("characters", "char_"),
    ("characters", "char_" + "a" * 41),
    ("characters", "place_beach_camp"),
    ("places", "char_kiwilo"),
    ("props", "place_beach_camp"),
    ("characters", "kiwilo"),
    ("characters", None),
    ("characters", 7),
    ("characters", ["char_x"]),
]
BAD_KINDS = ["character", "Characters", "../characters", "characters/", "styles", "", ".", None, 7,
             ["characters"]]


class _Tripwire:
    """Stands in for os/shutil/tempfile in the store: any use is a failure."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        raise AssertionError(f"filesystem touched: {self._name}.{attr}")


@pytest.fixture
def tripwired(outputs, monkeypatch):
    stories = store.StoryStore(str(outputs), on_log=lambda _: None)
    for module in ("os", "shutil", "tempfile"):
        monkeypatch.setattr(store, module, _Tripwire(module))

    def no_open(*args, **kwargs):
        raise AssertionError("filesystem touched: open")

    monkeypatch.setattr(store, "open", no_open, raising=False)
    return stories


def _every_entity_call(stories, story_id, kind, eid, name="portrait.png"):
    return [
        lambda: stories.entity_dir(story_id, kind, eid),
        lambda: stories.entity_dir(story_id, kind, eid, create=True),
        lambda: stories.read_entity(story_id, kind, eid),
        lambda: stories.delete_entity(story_id, kind, eid, now=NOW),
        lambda: stories.refs_dir(story_id, kind, eid, create=True),
        lambda: stories.uploads_dir(story_id, kind, eid, create=True),
        lambda: stories.media_path(story_id, kind, eid, name),
        lambda: stories.write_media(story_id, kind, eid, name, "/etc/hostname"),
    ]


@pytest.mark.parametrize("kind, eid", BAD_ENTITY_IDS)
def test_a_malformed_entity_id_is_refused_before_any_filesystem_access(tripwired, kind, eid):
    for call in _every_entity_call(tripwired, "0123456789ab", kind, eid):
        with pytest.raises(KeyError):
            call()
    field = {"characters": "char_id", "places": "place_id", "props": "prop_id"}[kind]
    with pytest.raises(ValueError):
        tripwired.write_entity("0123456789ab", kind, _doc_for(kind, "char_x", **{field: eid}), now=NOW)


@pytest.mark.parametrize("kind", BAD_KINDS)
def test_an_unknown_kind_is_refused_before_any_filesystem_access(tripwired, kind):
    for call in _every_entity_call(tripwired, "0123456789ab", kind, "char_kiwilo"):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        tripwired.list_entities("0123456789ab", kind)
    with pytest.raises(KeyError):
        tripwired.write_entity("0123456789ab", kind, _character(), now=NOW)


@pytest.mark.parametrize("story_id", ["../x", "ABCDEF123456", "0123456789ab\n", "", None, 7])
def test_a_malformed_story_id_is_refused_before_any_filesystem_access(tripwired, story_id):
    for call in _every_entity_call(tripwired, story_id, "characters", "char_kiwilo"):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        tripwired.list_entities(story_id, "characters")
    with pytest.raises(KeyError):
        tripwired.write_entity(story_id, "characters", _character(), now=NOW)
    with pytest.raises(KeyError):
        tripwired.recompute_group_approvals(story_id, now=NOW)


def test_the_entity_tripwire_does_trip(tripwired):
    """The tests above would pass vacuously if the tripwire never fired."""
    with pytest.raises(AssertionError):
        tripwired.entity_dir("0123456789ab", "characters", "char_kiwilo")


def test_entity_dir_needs_the_folder_unless_asked_to_create_it(stories, outputs, story_id):
    with pytest.raises(KeyError):
        stories.entity_dir(story_id, "characters", "char_kiwilo")
    assert not (_story_dir(outputs, story_id) / "characters").exists()

    made = stories.entity_dir(story_id, "characters", "char_kiwilo", create=True)

    assert made == os.path.realpath(_story_dir(outputs, story_id) / "characters" / "char_kiwilo")
    with pytest.raises(KeyError):
        stories.entity_dir("0123456789ab", "characters", "char_kiwilo", create=True)
    assert not (outputs / "stories" / "0123456789ab").exists()


def test_a_symlinked_entity_folder_is_kept_and_never_followed(stories, outputs, seeded, tmp_path, logs):
    folder = _story_dir(outputs, seeded) / "characters" / "char_kiwilo"
    stories.write_media(seeded, "characters", "char_kiwilo", "portrait.png", _source(tmp_path, b"PORTRAIT"))
    precious = tmp_path / "precious"
    shutil.move(str(folder), str(precious))
    (precious / "only_copy.txt").write_text("irreplaceable", encoding="utf-8")
    before = (precious / "character.json").read_bytes()
    _symlink(precious, folder)
    del logs[:]

    for call in _every_entity_call(stories, seeded, "characters", "char_kiwilo")[:3] + \
            _every_entity_call(stories, seeded, "characters", "char_kiwilo")[4:]:
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        stories.write_entity(seeded, "characters", _character(name="Through the link"), now=LATER)

    assert stories.list_entities(seeded, "characters") == []
    assert logs == [f"Skipped outputs/stories/{seeded}/characters/char_kiwilo/: "
                    "not a real directory inside characters/"]

    report = stories.delete_entity(seeded, "characters", "char_kiwilo", now=LATER)

    assert report == {"removed": [], "kept": [
        f"outputs/stories/{seeded}/characters/char_kiwilo/ (a symlink, never followed)"]}
    assert os.path.islink(folder)
    assert (precious / "character.json").read_bytes() == before
    assert (precious / "only_copy.txt").read_text(encoding="utf-8") == "irreplaceable"
    assert (precious / "refs" / "portrait.png").read_bytes() == b"PORTRAIT"
    assert sorted(p.name for p in precious.iterdir()) == ["character.json", "only_copy.txt", "refs"]
    assert stories.get(seeded)["cast_ids"] == []


def test_a_symlinked_kind_folder_is_never_followed(stories, outputs, story_id, tmp_path, logs):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _symlink(elsewhere, _story_dir(outputs, story_id) / "places")

    with pytest.raises(KeyError):
        stories.write_entity(story_id, "places", _place(), now=LATER)
    with pytest.raises(KeyError):
        stories.entity_dir(story_id, "places", "place_beach_camp", create=True)
    assert list(elsewhere.iterdir()) == []
    assert stories.get(story_id)["place_ids"] == []

    del logs[:]
    assert stories.list_entities(story_id, "places") == []
    assert logs == [f"Skipped outputs/stories/{story_id}/places/: not a real directory, never followed"]


# ------------------------------------------------------------------ media

def _source(tmp_path, data=b"\x89PNG fake", name="src.bin"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


MEDIA_NAMES = [
    ("characters", "portrait.png", "refs"),
    ("characters", "turnaround.jpg", "refs"),
    ("characters", "expressions.webp", "refs"),
    ("characters", "portrait.jpeg", "refs"),
    ("characters", "extra_01.png", "refs"),
    ("characters", "extra_99.webp", "refs"),
    ("characters", UPLOAD, "refs/uploads"),
    ("characters", "voice_sample.mp3", ""),
    ("characters", "voice_sample.wav", ""),
    ("places", "variant_day.png", "refs"),
    ("places", "variant_golden_hour.webp", "refs"),
    ("places", "variant_" + "n" * 20 + ".jpg", "refs"),
    ("props", "image.png", "refs"),
    ("props", "image.jpeg", "refs"),
]

REFUSED_MEDIA_NAMES = [
    ("characters", "portrait.gif"),
    ("characters", "Portrait.png"),
    ("characters", "portrait.PNG"),
    ("characters", "portrait.png\n"),
    ("characters", "../portrait.png"),
    ("characters", "refs/portrait.png"),
    ("characters", "uploads/" + UPLOAD),
    ("characters", "portrait"),
    ("characters", ".portrait.png"),
    ("characters", "character.json"),
    ("characters", "extra_1.png"),
    ("characters", "extra_001.png"),
    ("characters", "variant_day.png"),
    ("characters", "image.png"),
    ("characters", UPLOAD.upper()),
    ("characters", UPLOAD[1:]),
    ("characters", UPLOAD[:-4] + ".jpg"),
    ("characters", "voice_sample.ogg"),
    ("characters", "voice_sample.mp3.png"),
    ("characters", ""),
    ("characters", "."),
    ("characters", ".."),
    ("characters", "/etc/passwd"),
    ("characters", "C:\\portrait.png"),
    ("characters", None),
    ("characters", 7),
    ("places", "portrait.png"),
    ("places", "variant_Day.png"),
    ("places", "variant_.png"),
    ("places", "variant_1am.png"),
    ("places", "variant_" + "n" * 21 + ".png"),
    ("places", "variant_day.gif"),
    ("places", "voice_sample.mp3"),
    ("places", UPLOAD),
    ("places", "extra_01.png"),
    ("places", "place.json"),
    ("props", "portrait.png"),
    ("props", "variant_day.png"),
    ("props", "image.gif"),
    ("props", "voice_sample.wav"),
    ("props", UPLOAD),
]


@pytest.mark.parametrize("kind, name, folder", MEDIA_NAMES)
def test_an_accepted_media_name_is_written_to_its_folder_and_served(stories, outputs, seeded, tmp_path,
                                                                     kind, name, folder):
    eid = FIRST[kind]
    written = stories.write_media(seeded, kind, eid, name, _source(tmp_path, b"DATA"))

    expected = _story_dir(outputs, seeded) / kind / eid / folder / name
    assert written == os.path.realpath(expected)
    assert expected.read_bytes() == b"DATA"
    assert os.stat(expected).st_mode & 0o777 == 0o644
    assert stories.media_path(seeded, kind, eid, name) == os.path.realpath(expected)
    assert _temp_files(outputs) == []


@pytest.mark.parametrize("kind, name", REFUSED_MEDIA_NAMES)
def test_a_refused_media_name_is_never_written_or_served(tripwired, kind, name):
    eid = FIRST[kind]
    with pytest.raises(KeyError):
        tripwired.write_media("0123456789ab", kind, eid, name, "/etc/hostname")
    with pytest.raises(KeyError):
        tripwired.media_path("0123456789ab", kind, eid, name)


def test_the_media_patterns_of_a_kind_never_overlap():
    for kind, patterns in store.MEDIA_NAME_PATTERNS.items():
        for name in [n for k, n, _ in MEDIA_NAMES if k == kind]:
            assert sum(1 for p in patterns.values() if p.fullmatch(name)) == 1, (kind, name)


def test_media_needs_an_existing_entity(stories, outputs, story_id, tmp_path):
    with pytest.raises(KeyError):
        stories.write_media(story_id, "characters", "char_kiwilo", "portrait.png", _source(tmp_path))
    with pytest.raises(KeyError):
        stories.refs_dir(story_id, "characters", "char_kiwilo", create=True)
    assert not (_story_dir(outputs, story_id) / "characters").exists()


def test_refs_and_uploads_folders(stories, outputs, seeded):
    entity = _story_dir(outputs, seeded) / "characters" / "char_kiwilo"
    with pytest.raises(KeyError):
        stories.refs_dir(seeded, "characters", "char_kiwilo")

    assert stories.uploads_dir(seeded, "characters", "char_kiwilo", create=True) == \
        os.path.realpath(entity / "refs" / "uploads")
    assert stories.refs_dir(seeded, "characters", "char_kiwilo") == os.path.realpath(entity / "refs")
    with pytest.raises(KeyError):
        stories.uploads_dir(seeded, "places", "place_beach_camp", create=True)
    assert not (_story_dir(outputs, seeded) / "places" / "place_beach_camp" / "refs").exists()


def test_write_media_replaces_the_old_file_atomically(stories, outputs, seeded, tmp_path):
    stories.write_media(seeded, "props", "prop_coconut_phone", "image.png", _source(tmp_path, b"OLD"))
    stories.write_media(seeded, "props", "prop_coconut_phone", "image.png", _source(tmp_path, b"NEW"))
    path = _story_dir(outputs, seeded) / "props" / "prop_coconut_phone" / "refs" / "image.png"
    assert path.read_bytes() == b"NEW"
    assert sorted(p.name for p in path.parent.iterdir()) == ["image.png"]


def test_a_failed_media_write_keeps_the_old_file(stories, outputs, seeded, tmp_path, monkeypatch):
    stories.write_media(seeded, "characters", "char_kiwilo", "portrait.png", _source(tmp_path, b"OLD"))
    path = _story_dir(outputs, seeded) / "characters" / "char_kiwilo" / "refs" / "portrait.png"

    def torn_copy(source, out, *args, **kwargs):
        out.write(b"PAR")
        raise OSError("No space left on device")

    monkeypatch.setattr(store.shutil, "copyfileobj", torn_copy)
    with pytest.raises(OSError):
        stories.write_media(seeded, "characters", "char_kiwilo", "portrait.png", _source(tmp_path, b"NEW"))
    monkeypatch.undo()

    with pytest.raises(FileNotFoundError):
        stories.write_media(seeded, "characters", "char_kiwilo", "portrait.png", str(tmp_path / "missing"))

    assert path.read_bytes() == b"OLD"
    assert sorted(p.name for p in path.parent.iterdir()) == ["portrait.png"]
    assert _temp_files(outputs) == []


def test_media_through_a_symlink_is_never_written_or_served(stories, outputs, seeded, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"SECRET")
    refs = pathlib.Path(stories.refs_dir(seeded, "characters", "char_kiwilo", create=True))
    _symlink(secret, refs / "portrait.png")

    with pytest.raises(KeyError):
        stories.media_path(seeded, "characters", "char_kiwilo", "portrait.png")
    with pytest.raises(ValueError):
        stories.write_media(seeded, "characters", "char_kiwilo", "portrait.png", _source(tmp_path, b"NEW"))
    assert secret.read_bytes() == b"SECRET" and os.path.islink(refs / "portrait.png")

    (refs / "turnaround.png").mkdir()
    with pytest.raises(KeyError):
        stories.media_path(seeded, "characters", "char_kiwilo", "turnaround.png")
    with pytest.raises(ValueError):
        stories.write_media(seeded, "characters", "char_kiwilo", "turnaround.png", _source(tmp_path))

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "variant_day.png").write_bytes(b"ELSEWHERE")
    _symlink(elsewhere, _story_dir(outputs, seeded) / "places" / "place_beach_camp" / "refs")
    with pytest.raises(KeyError):
        stories.media_path(seeded, "places", "place_beach_camp", "variant_day.png")
    with pytest.raises(KeyError):
        stories.write_media(seeded, "places", "place_beach_camp", "variant_day.png", _source(tmp_path))
    assert (elsewhere / "variant_day.png").read_bytes() == b"ELSEWHERE"


def test_media_path_of_a_missing_file_or_another_story_is_a_key_error(stories, seeded, tmp_path):
    with pytest.raises(KeyError):
        stories.media_path(seeded, "characters", "char_kiwilo", "portrait.png")
    stories.write_media(seeded, "characters", "char_kiwilo", "voice_sample.mp3", _source(tmp_path))
    other = stories.create(language="en", now=NOW)["story_id"]
    with pytest.raises(KeyError):
        stories.media_path(other, "characters", "char_kiwilo", "voice_sample.mp3")


# ------------------------------------------------------------ approvals fold

FULL = {"concept": NOW, "bible": NOW, "style": NOW, "cast": NOW, "places": NOW, "season": NOW}


STATUS_TABLE = [
    ({**FULL, "cast": None, "places": None, "season": None}, "style_approved"),
    ({**FULL, "places": None, "season": None}, "cast_approved"),
    ({**FULL, "season": None}, "places_approved"),
    (FULL, "ready"),
    # Not contiguous: a later approval without the earlier one does not count.
    ({**FULL, "cast": None}, "style_approved"),
    ({**FULL, "places": None}, "cast_approved"),
    ({**FULL, "style": None}, "bible_approved"),
    ({**FULL, "cast": ""}, "style_approved"),
    ({"concept": NOW, "bible": NOW, "style": NOW, "season": NOW}, "style_approved"),
]


def test_derive_status_through_ready():
    """One table, one test: the rows below style_approved alone would pass on
    the phase-1 chain too; together they pin the whole chain."""
    wrong = [(approvals, expected, store.derive_status(approvals))
             for approvals, expected in STATUS_TABLE if store.derive_status(approvals) != expected]
    assert wrong == []


def test_the_approval_chain_is_the_status_list_and_the_schema_order():
    assert [status for _, status in store._APPROVAL_STEPS] == list(defaults.STATUSES[1:])
    approvals = schemas.STORY_BIBLE_SCHEMA["properties"]["approvals"]
    assert [key for key, _ in store._APPROVAL_STEPS] == list(approvals["properties"])
    assert approvals["required"] == list(approvals["properties"])


@pytest.mark.parametrize("characters, cast", [
    ([], None),
    ([("char_a", "lead", NOW)], NOW),
    ([("char_a", "lead", None)], None),
    ([("char_a", "lead", NOW), ("char_b", "support", None)], None),
    ([("char_a", "lead", NOW), ("char_b", "support", NOW)], NOW),
    ([("char_a", "lead", NOW), ("char_b", "guest", None)], NOW),
    ([("char_a", "lead", NOW), ("char_b", "recurring", None)], NOW),
    ([("char_a", "lead", None), ("char_b", "guest", NOW)], None),
    # A cast needs a lead or a support: guests alone approve nothing.
    ([("char_a", "guest", NOW)], None),
    ([("char_a", "recurring", NOW), ("char_b", "guest", NOW)], None),
])
def test_the_cast_approval_table(stories, story_id, characters, cast):
    _approve_first_three(stories, story_id)
    for eid, role, approved in characters:
        stories.write_entity(story_id, "characters", _character(char_id=eid, role=role, approved_at=approved),
                             now=NOW)

    story = stories.recompute_group_approvals(story_id, now=NOW)

    assert story["approvals"]["cast"] == cast
    assert story["status"] == ("cast_approved" if cast else "style_approved")
    assert stories.get(story_id) == story


@pytest.mark.parametrize("places, props, approved", [
    ([], [], False),
    ([], [NOW], False),
    ([None], [], False),
    ([NOW], [], True),
    ([NOW], [None], False),
    ([NOW, None], [NOW], False),
    ([NOW, NOW], [NOW, NOW], True),
])
def test_the_places_approval_table(stories, story_id, places, props, approved):
    for n, approved_at in enumerate(places):
        stories.write_entity(story_id, "places", _place(place_id=f"place_{n}", approved_at=approved_at), now=NOW)
    for n, approved_at in enumerate(props):
        stories.write_entity(story_id, "props", _prop(prop_id=f"prop_{n}", approved_at=approved_at), now=NOW)

    story = stories.recompute_group_approvals(story_id, now=LATER)

    assert story["approvals"]["places"] == (NOW if approved else None)


def test_an_approval_that_still_holds_keeps_its_timestamp(stories, story_id):
    stories.write_entity(story_id, "characters", _character(approved_at=NOW), now=NOW)
    assert stories.get(story_id)["approvals"]["cast"] == NOW

    stories.write_entity(story_id, "characters", _character(char_id="char_b", role="support", approved_at=LATER),
                         now=LATER)
    assert stories.recompute_group_approvals(story_id, now=LATEST)["approvals"]["cast"] == NOW

    stories.write_entity(story_id, "characters", _character(char_id="char_b", role="support"), now=LATER)
    assert stories.get(story_id)["approvals"]["cast"] is None
    stories.write_entity(story_id, "characters", _character(char_id="char_b", role="support", approved_at=LATEST),
                         now=LATEST)
    assert stories.get(story_id)["approvals"]["cast"] == LATEST


def test_recompute_clears_an_approval_the_entities_do_not_hold(stories, story_id):
    _approve_first_three(stories, story_id)
    stories.update(story_id, lambda doc: doc["approvals"].update(cast=NOW, places=NOW, season=NOW), now=NOW)
    assert stories.get(story_id)["status"] == "ready"

    story = stories.recompute_group_approvals(story_id, now=LATER)

    assert story["approvals"] == {"concept": NOW, "bible": NOW, "style": NOW,
                                  "cast": None, "places": None, "season": NOW}
    assert story["status"] == "style_approved" and story["updated_at"] == LATER


def test_the_status_chain_up_to_ready_and_back(stories, story_id):
    _approve_first_three(stories, story_id)
    stories.write_entity(story_id, "characters", _character(approved_at=NOW), now=NOW)
    stories.write_entity(story_id, "characters", _character(char_id="char_guest", role="guest"), now=NOW)
    assert stories.get(story_id)["status"] == "cast_approved"

    stories.write_entity(story_id, "places", _place(approved_at=NOW), now=NOW)
    stories.write_entity(story_id, "props", _prop(), now=NOW)
    assert stories.get(story_id)["status"] == "cast_approved"
    stories.write_entity(story_id, "props", _prop(approved_at=NOW), now=NOW)
    assert stories.get(story_id)["status"] == "places_approved"

    stories.update(story_id, lambda doc: doc["approvals"].update(season=LATER), now=LATER)
    assert stories.get(story_id)["status"] == "ready"
    assert stories.list()[0]["status"] == "ready"

    # Un-approving a prop drops places; the season approval is kept but no longer counts.
    stories.write_entity(story_id, "props", _prop(approved_at=None), now=LATER)
    story = stories.get(story_id)
    assert story["approvals"]["places"] is None and story["approvals"]["season"] == LATER
    assert story["status"] == "cast_approved"

    stories.write_entity(story_id, "props", _prop(approved_at=LATER), now=LATEST)
    assert stories.get(story_id)["status"] == "ready"

    # Un-approving the lead drops the cast: back to style_approved.
    stories.write_entity(story_id, "characters", _character(approved_at=None), now=LATEST)
    story = stories.get(story_id)
    assert story["approvals"]["cast"] is None and story["status"] == "style_approved"
    assert stories.list()[0]["status"] == "style_approved"


def test_deleting_an_entity_can_drop_the_status(stories, story_id):
    _approve_first_three(stories, story_id)
    stories.write_entity(story_id, "characters", _character(approved_at=NOW), now=NOW)
    stories.write_entity(story_id, "places", _place(approved_at=NOW), now=NOW)
    assert stories.get(story_id)["status"] == "places_approved"

    stories.delete_entity(story_id, "places", "place_beach_camp", now=LATER)
    story = stories.get(story_id)
    assert story["approvals"]["places"] is None and story["status"] == "cast_approved"

    stories.delete_entity(story_id, "characters", "char_kiwilo", now=LATEST)
    story = stories.get(story_id)
    assert story["approvals"]["cast"] is None and story["status"] == "style_approved"


def test_deleting_an_unapproved_lead_can_approve_the_cast(stories, story_id):
    _approve_first_three(stories, story_id)
    stories.write_entity(story_id, "characters", _character(approved_at=NOW), now=NOW)
    stories.write_entity(story_id, "characters", _character(char_id="char_b"), now=NOW)
    assert stories.get(story_id)["status"] == "style_approved"

    stories.delete_entity(story_id, "characters", "char_b", now=LATER)

    story = stories.get(story_id)
    assert story["approvals"]["cast"] == LATER and story["status"] == "cast_approved"


# ------------------------------------------------------------- phase-1 story

# story.json exactly as phase 1 wrote it: three approvals.
PHASE1_STORY_JSON = """{
  "$schema": "story_bible_v1",
  "story_id": "b1104ec66b05",
  "title": "Tentafruit Island",
  "language": "fr",
  "seed_text": null,
  "concept_id": "tentafruit_island",
  "concept": {"title": "Tentafruit Island"},
  "logline": "Des fruits en couple survivent au vote hebdomadaire d'une île de téléréalité.",
  "premise": "Chaque semaine, les couples de fruits affrontent le vote du public.",
  "tone": "mélodramatique, rapide",
  "genre_tags": ["soap", "comédie"],
  "world": {
    "setting_summary": "Une île tropicale de téléréalité.",
    "rules": ["Un vote public élimine un couple chaque semaine."],
    "time_period": "contemporain",
    "recurring_motifs": ["le téléphone-coco"]
  },
  "themes_and_values": ["la loyauté contre l'ambition"],
  "audience": {"age": "13+", "platforms": ["tiktok"]},
  "why_come_back": ["Le vote.", "Une alliance se brise.", "Le téléphone-coco."],
  "cast_ids": [],
  "place_ids": [],
  "prop_ids": [],
  "style_template_id": "fruit_drama",
  "episode_template_id": "serial_60s_v1",
  "generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references", "budget_profile": "free"},
  "narrator": {"enabled": false, "voice": null},
  "approvals": {
    "concept": "2026-09-26T10:00:00+00:00",
    "bible": "2026-09-26T10:00:00+00:00",
    "style": "2026-09-26T10:00:00+00:00"
  },
  "status": "style_approved",
  "created_at": "2026-09-26T10:00:00+00:00",
  "updated_at": "2026-09-26T10:00:00+00:00"
}
"""
PHASE1_ID = "b1104ec66b05"


@pytest.fixture
def phase1(outputs):
    folder = outputs / "stories" / PHASE1_ID
    folder.mkdir(parents=True)
    path = folder / "story.json"
    path.write_text(PHASE1_STORY_JSON, encoding="utf-8")
    return path


def test_a_phase1_story_is_refused_by_the_schema_alone():
    """The schema is strict; only the store's read upgrades a phase-1 story."""
    assert schemas.story_bible_errors(json.loads(PHASE1_STORY_JSON)) != []


def test_a_phase1_story_loads_with_the_new_approvals_as_null(stories, phase1, logs):
    story = stories.get(PHASE1_ID)

    assert story["approvals"] == {"concept": NOW, "bible": NOW, "style": NOW,
                                  "cast": None, "places": None, "season": None}
    assert list(story["approvals"]) == ["concept", "bible", "style", "cast", "places", "season"]
    assert story["status"] == "style_approved"
    assert schemas.story_bible_errors(story) == []
    assert phase1.read_text(encoding="utf-8") == PHASE1_STORY_JSON, "a read never writes"

    assert [entry["story_id"] for entry in stories.list()] == [PHASE1_ID]
    assert not [line for line in logs if line.startswith("Skipped ")]


def test_a_phase1_story_is_saved_with_the_new_approvals(stories, phase1):
    saved = stories.update(PHASE1_ID, lambda doc: doc.update(title="Tentafruit Island 2"), now=LATER)

    on_disk = json.loads(phase1.read_text(encoding="utf-8"))
    assert on_disk == saved
    assert on_disk["approvals"] == {"concept": NOW, "bible": NOW, "style": NOW,
                                    "cast": None, "places": None, "season": None}
    assert on_disk["status"] == "style_approved" and on_disk["title"] == "Tentafruit Island 2"
    assert schemas.story_bible_errors(on_disk) == []


def test_a_phase1_story_takes_entities_and_documents(stories, phase1):
    stories.write_entity(PHASE1_ID, "characters", _character(approved_at=LATER), now=LATER)

    on_disk = json.loads(phase1.read_text(encoding="utf-8"))
    assert on_disk["cast_ids"] == ["char_kiwilo"]
    assert on_disk["approvals"]["cast"] == LATER and on_disk["status"] == "cast_approved"

    phase1.write_text(PHASE1_STORY_JSON, encoding="utf-8")
    stories.write_doc(PHASE1_ID, "season.json", _season(), now=LATEST)
    assert "season" in json.loads(phase1.read_text(encoding="utf-8"))["approvals"]


@pytest.mark.parametrize("approvals", [
    {"concept": NOW, "bible": NOW, "style": NOW, "cast": None},
    {"concept": NOW, "bible": NOW, "style": NOW, "season": NOW},
    {"concept": NOW, "bible": NOW, "style": NOW, "episodes": None},
    {"concept": NOW, "bible": NOW},
])
def test_only_a_phase1_story_is_upgraded(stories, phase1, approvals):
    """Anything but exactly the phase-1 approvals is judged, never repaired."""
    assert stories.get(PHASE1_ID)["approvals"]["cast"] is None
    doc = json.loads(PHASE1_STORY_JSON)
    doc["approvals"] = approvals
    text = json.dumps(doc)
    phase1.write_text(text, encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.get(PHASE1_ID)
    assert phase1.read_text(encoding="utf-8") == text


def test_a_new_story_has_the_six_approvals(stories, story_id, outputs):
    on_disk = json.loads((_story_dir(outputs, story_id) / "story.json").read_text(encoding="utf-8"))
    assert on_disk["approvals"] == {"concept": None, "bible": None, "style": None,
                                    "cast": None, "places": None, "season": None}


# --------------------------------------------------- season.json / proposal

@pytest.mark.parametrize("name, doc", [
    ("season.json", _season()),
    ("season.json", _season(episodes_planned=3, arc=_arc(range(1, 4)), approved_at=NOW)),
    ("places_proposal.json", _proposal()),
])
def test_the_flat_phase2_documents_round_trip(stories, outputs, story_id, name, doc):
    saved = stories.write_doc(story_id, name, doc, now=LATER)

    assert saved == dict(doc, updated_at=LATER)
    assert stories.read_doc(story_id, name) == saved
    assert json.loads((_story_dir(outputs, story_id) / name).read_text(encoding="utf-8")) == saved
    assert name in store.DOC_NAMES


@pytest.mark.parametrize("name, doc", [
    ("season.json", _season(episodes_planned=2)),
    ("season.json", _season(arc=_arc(range(1, 4)))),
    ("season.json", {"$schema": "season_arc_v1"}),
    ("places_proposal.json", _proposal(places=[{"name": "x", "one_line": "y"}] * 7)),
    ("places_proposal.json", {"$schema": "places_proposal_v1"}),
])
def test_an_invalid_flat_phase2_document_is_never_written(stories, outputs, story_id, name, doc):
    with pytest.raises(schemas.SchemaError):
        stories.write_doc(story_id, name, doc, now=LATER)
    assert not (_story_dir(outputs, story_id) / name).exists()


@pytest.mark.parametrize("name", ["season.json", "places_proposal.json"])
def test_a_broken_flat_phase2_document_on_disk_raises_and_is_not_repaired(stories, outputs, story_id, name):
    path = _story_dir(outputs, story_id) / name
    text = json.dumps({"$schema": name.replace(".json", "_v1"), "updated_at": NOW})
    path.write_text(text, encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.read_doc(story_id, name)
    assert path.read_text(encoding="utf-8") == text



@pytest.mark.parametrize("kind, build, broken_id", [
    ("characters", lambda: _character(char_id="char_a", role="lead", approved_at=NOW), "char_b"),
    ("places", lambda: _place(place_id="place_a", approved_at=NOW), "place_b"),
    ("props", None, "prop_b"),
])
def test_an_unreadable_document_blocks_its_group_approval(stories, outputs, story_id, kind, build, broken_id):
    # A skipped folder is unknown state: it may be a lead or a place nobody
    # approved, so the group cannot count as approved around it.
    _approve_first_three(stories, story_id)
    stories.write_entity(story_id, "characters", _character(char_id="char_a", role="lead", approved_at=NOW), now=NOW)
    stories.write_entity(story_id, "places", _place(place_id="place_a", approved_at=NOW), now=NOW)
    group = "cast" if kind == "characters" else "places"
    assert stories.get(story_id)["approvals"][group] == NOW

    folder = _story_dir(outputs, story_id) / kind / broken_id
    folder.mkdir(parents=True)
    (folder / {"characters": "character.json", "places": "place.json", "props": "prop.json"}[kind]).write_text(
        "{not json", encoding="utf-8")

    story = stories.recompute_group_approvals(story_id, now=LATER)

    assert story["approvals"][group] is None
    assert (folder).exists()


# ------------------------------------------- store: references on delete

def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _entity_file(outputs, story_id, kind, eid):
    filename = {"characters": "character.json", "places": "place.json", "props": "prop.json"}[kind]
    return _story_dir(outputs, story_id) / kind / eid / filename


def _bytes_of(outputs, story_id, paths):
    return {path: (_story_dir(outputs, story_id) / path).read_bytes() for path in paths}


@pytest.fixture
def referenced(stories, story_id):
    """A cast of three pointing at each other, two props, a written and
    approved season arc and a places proposal -- most of it approved -- all
    naming ``char_kiwilo``; ``char_figuette`` is named by Mangella's
    relationships and the arc's ``introduced`` alone. The place and both
    props are approved."""
    _approve_first_three(stories, story_id)
    stories.write_entity(story_id, "characters", _written_character(
        relationships={"char_mangella": "secret ex"}), now=NOW)
    stories.write_entity(story_id, "characters", _written_character(
        char_id="char_mangella", name="Mangella", relationships={"char_kiwilo": "son ex secret",
                                                                 "char_figuette": "sa rivale"},
        state={"alive": True, "location": "place_vote_hut", "arc_notes": []}), now=NOW)
    stories.write_entity(story_id, "characters", _character(
        char_id="char_figuette", name="Figuette", role="support",
        relationships={"char_kiwilo": "his confidante"}), now=NOW)
    stories.write_entity(story_id, "places", _written_place(), now=NOW)
    stories.write_entity(story_id, "props", _written_prop(), now=NOW)  # owned by char_kiwilo, approved
    stories.write_entity(story_id, "props", _written_prop(prop_id="prop_tiki_torch", name="Tiki torch",
                                                          owner_char_id="char_mangella"), now=NOW)
    arc = _arc(range(1, 4))
    arc[1]["characters"] = ["char_mangella", "char_kiwilo"]
    arc[2]["characters"] = ["char_mangella"]
    memory = {"recaps": {}, "open_hooks": [], "relationship_state": {},
              "introduced": {"ep01": ["char_kiwilo", "char_mangella"], "ep02": ["char_figuette"]}}
    stories.write_doc(story_id, "season.json", _season(episodes_planned=3, arc=arc, series_memory=memory,
                                                       approved_at=LATER), now=NOW)
    stories.write_doc(story_id, "places_proposal.json", _proposal(), now=NOW)  # owner char_kiwilo, then None
    return story_id


def test_deleting_a_character_removes_every_reference_to_it(stories, outputs, referenced, logs):
    untouched = ("places/place_beach_camp/place.json", "props/prop_tiki_torch/prop.json")
    before = _bytes_of(outputs, referenced, untouched)
    del logs[:]

    report = stories.delete_entity(referenced, "characters", "char_kiwilo", now=LATEST)

    assert report == {"removed": [f"outputs/stories/{referenced}/characters/char_kiwilo/"], "kept": []}
    mangella = stories.read_entity(referenced, "characters", "char_mangella")
    assert mangella["relationships"] == {"char_figuette": "sa rivale"}
    assert stories.read_entity(referenced, "characters", "char_figuette")["relationships"] == {}
    phone = stories.read_entity(referenced, "props", "prop_coconut_phone")
    assert phone["owner_char_id"] is None
    season = stories.read_doc(referenced, "season.json")
    assert [entry["characters"] for entry in season["arc"]] == [[], ["char_mangella"], ["char_mangella"]]
    assert season["series_memory"]["introduced"] == {"ep01": ["char_mangella"], "ep02": ["char_figuette"]}
    proposal = stories.read_doc(referenced, "places_proposal.json")
    assert [prop["owner"] for prop in proposal["props"]] == [None, None]

    # Bookkeeping, not content: every approval stands, only updated_at moves.
    assert (mangella["approved_at"], phone["approved_at"], season["approved_at"]) == (LATER, LATER, LATER)
    assert (mangella["updated_at"], phone["updated_at"], season["updated_at"], proposal["updated_at"]) == (
        LATEST, LATEST, LATEST, LATEST)
    written = _written_character(char_id="char_mangella", name="Mangella", relationships={
        "char_figuette": "sa rivale"}, state={"alive": True, "location": "place_vote_hut", "arc_notes": []},
        updated_at=LATEST)
    assert mangella == written, "nothing but the dangling id changed"
    assert phone == _written_prop(owner_char_id=None, updated_at=LATEST)

    # Nothing that did not name it was written.
    assert _bytes_of(outputs, referenced, untouched) == before
    assert _temp_files(outputs) == []

    # One line per document written, naming it.
    cleaned = [line for line in logs if line.startswith("Cleaned ")]
    prefix = f"outputs/stories/{referenced}/"
    assert [line.split(":")[0] for line in cleaned] == [
        f"Cleaned {prefix}characters/char_figuette/character.json",
        f"Cleaned {prefix}characters/char_mangella/character.json",
        f"Cleaned {prefix}props/prop_coconut_phone/prop.json",
        f"Cleaned {prefix}season.json",
        f"Cleaned {prefix}places_proposal.json",
    ]
    assert all("char_kiwilo" in line for line in cleaned)


def test_deleting_a_character_keeps_the_approvals_it_touches_so_the_cast_can_approve(stories, referenced):
    # Kiwilo and Mangella (the two leads) are approved; Figuette (a support)
    # is not, and Mangella names her. Deleting Figuette edits Mangella -- and
    # Mangella's approval stands, so the cast is approved.
    assert stories.get(referenced)["approvals"]["cast"] is None

    stories.delete_entity(referenced, "characters", "char_figuette", now=LATEST)

    mangella = stories.read_entity(referenced, "characters", "char_mangella")
    assert mangella["relationships"] == {"char_kiwilo": "son ex secret"}
    assert mangella["approved_at"] == LATER
    kiwilo = stories.read_entity(referenced, "characters", "char_kiwilo")
    assert kiwilo["relationships"] == {"char_mangella": "secret ex"}
    season = stories.read_doc(referenced, "season.json")
    assert season["series_memory"]["introduced"] == {"ep01": ["char_kiwilo", "char_mangella"], "ep02": []}
    assert season["approved_at"] == LATER
    story = stories.get(referenced)
    assert story["approvals"]["cast"] == LATEST
    assert story["status"] == "places_approved"  # the place and both props were approved all along


def _memory_naming_kiwilo():
    """Two memory entries whose relationship deltas name char_kiwilo, and one
    pair that does not; the derived fields folded by hand."""
    ep01 = _memory_entry(relationship_deltas={"char_kiwilo|char_mangella": "rivals",
                                              "char_figuette|char_mangella": "sisters"})
    ep02 = _memory_entry(recap="Mangella found the phone.", hooks_opened=[], hooks_closed=[_PHONE],
                         relationship_deltas={"char_figuette|char_kiwilo": "confidants"}, script_rev=2,
                         approved_at=LATER)
    return {
        "recaps": {"ep01": ep01["recap"], "ep02": ep02["recap"]},
        "open_hooks": [],
        "relationship_state": {"char_figuette|char_kiwilo": "confidants", "char_figuette|char_mangella": "sisters",
                               "char_kiwilo|char_mangella": "rivals"},
        "introduced": {"ep01": ["char_kiwilo", "char_mangella"], "ep02": ["char_figuette"]},
        "entries": {"ep01": ep01, "ep02": ep02},
    }


def test_deleting_a_character_removes_its_relationship_keys_and_refolds_the_memory(stories, referenced, logs):
    season = stories.read_doc(referenced, "season.json")
    season["series_memory"] = _memory_naming_kiwilo()
    stories.write_doc(referenced, "season.json", season, now=NOW)
    del logs[:]

    stories.delete_entity(referenced, "characters", "char_kiwilo", now=LATEST)

    season = stories.read_doc(referenced, "season.json")  # validates: the derived fields match the entries
    expected = _memory_naming_kiwilo()
    expected["entries"]["ep01"]["relationship_deltas"] = {"char_figuette|char_mangella": "sisters"}
    expected["entries"]["ep02"]["relationship_deltas"] = {}
    expected["relationship_state"] = {"char_figuette|char_mangella": "sisters"}
    expected["introduced"] = {"ep01": ["char_mangella"], "ep02": ["char_figuette"]}
    # Only the pairs went: recaps, hooks, each entry's script_rev and approval stand, and the season's.
    assert season["series_memory"] == expected
    assert season["approved_at"] == LATER
    cleaned = [line for line in logs if line.startswith(f"Cleaned outputs/stories/{referenced}/season.json")]
    assert len(cleaned) == 1
    assert "char_figuette|char_kiwilo" in cleaned[0] and "char_kiwilo|char_mangella" in cleaned[0]
    assert "char_figuette|char_mangella" not in cleaned[0]


def test_deleting_a_character_removes_its_relationship_keys_from_a_memory_without_entries(stories, referenced,
                                                                                         logs):
    season = stories.read_doc(referenced, "season.json")
    season["series_memory"]["relationship_state"] = {
        "char_kiwilo|char_mangella": "rivals", "char_figuette|char_mangella": "sisters",
        "char_mangella|char_kiwilo": "an unsorted key, as a hand-edited file may hold"}
    stories.write_doc(referenced, "season.json", season, now=NOW)
    del logs[:]

    stories.delete_entity(referenced, "characters", "char_kiwilo", now=LATEST)

    memory = stories.read_doc(referenced, "season.json")["series_memory"]
    assert memory["relationship_state"] == {"char_figuette|char_mangella": "sisters"}
    assert "entries" not in memory, "a delete never adds entries"
    cleaned = [line for line in logs if line.startswith(f"Cleaned outputs/stories/{referenced}/season.json")]
    assert len(cleaned) == 1 and "char_mangella|char_kiwilo" in cleaned[0]


def test_deleting_a_place_clears_the_characters_located_there(stories, outputs, referenced, logs):
    untouched = ("characters/char_mangella/character.json", "characters/char_figuette/character.json",
                 "props/prop_coconut_phone/prop.json", "season.json", "places_proposal.json")
    before = _bytes_of(outputs, referenced, untouched)
    kiwilo_before = stories.read_entity(referenced, "characters", "char_kiwilo")
    assert kiwilo_before["state"]["location"] == "place_beach_camp"
    del logs[:]

    stories.delete_entity(referenced, "places", "place_beach_camp", now=LATEST)

    kiwilo = stories.read_entity(referenced, "characters", "char_kiwilo")
    assert kiwilo["state"] == dict(kiwilo_before["state"], location=None)
    assert kiwilo == dict(kiwilo_before, state=kiwilo["state"], updated_at=LATEST)
    assert kiwilo["approved_at"] == LATER
    assert _bytes_of(outputs, referenced, untouched) == before  # Mangella is at another place
    assert [line.split(":")[0] for line in logs if line.startswith("Cleaned ")] == [
        f"Cleaned outputs/stories/{referenced}/characters/char_kiwilo/character.json"]


def test_deleting_what_nothing_names_writes_no_other_document(stories, outputs, referenced, logs):
    everything = ("characters/char_kiwilo/character.json", "characters/char_mangella/character.json",
                  "characters/char_figuette/character.json", "places/place_beach_camp/place.json",
                  "props/prop_coconut_phone/prop.json", "season.json", "places_proposal.json")
    before = _bytes_of(outputs, referenced, everything)
    del logs[:]

    stories.delete_entity(referenced, "props", "prop_tiki_torch", now=LATEST)

    assert _bytes_of(outputs, referenced, everything) == before
    assert not [line for line in logs if line.startswith("Cleaned ")]


@pytest.mark.parametrize("name", ["season.json", "places_proposal.json"])
def test_an_unreadable_document_is_reported_and_never_rewritten_by_a_delete(stories, outputs, referenced, logs,
                                                                         name):
    path = _story_dir(outputs, referenced) / name
    text = json.dumps({"$schema": name.replace(".json", "_v1"), "note": "char_kiwilo", "updated_at": NOW})
    path.write_text(text, encoding="utf-8")
    del logs[:]

    report = stories.delete_entity(referenced, "characters", "char_kiwilo", now=LATEST)

    assert report["removed"] == [f"outputs/stories/{referenced}/characters/char_kiwilo/"]
    assert path.read_text(encoding="utf-8") == text
    assert stories.read_entity(referenced, "characters", "char_mangella")["relationships"] == {
        "char_figuette": "sa rivale"}
    assert stories.get(referenced)["cast_ids"] == ["char_mangella", "char_figuette"]
    kept = [line for line in logs if line.startswith("Kept ")]
    assert len(kept) == 1 and f"outputs/stories/{referenced}/{name}" in kept[0]
    assert "char_kiwilo" in kept[0]


def test_the_workflow_delete_cleans_references_too(stories, outputs, referenced):
    from clipping.aistory import workflow

    report = workflow.delete_entity(stories, referenced, "characters", "char_kiwilo", now=LATEST)

    assert report["removed"] == [f"outputs/stories/{referenced}/characters/char_kiwilo/"]
    assert stories.read_entity(referenced, "props", "prop_coconut_phone")["owner_char_id"] is None
    assert "char_kiwilo" not in stories.read_entity(referenced, "characters", "char_mangella")["relationships"]
