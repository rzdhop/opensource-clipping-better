"""The story-writing prompt catalogue: concepts and bible (spec 4, 4.1, 4.2).

Same pattern as ``clipping/analysis/prompts.py`` (DEC-062, DEC-064): English
instructions, data before the ask, one pure ``build_<id>(pack, ...) -> (system,
user, schema)`` per prompt, no network and nothing random, so every one of
them is golden-string tested. Every builder is handed a ``context.Pack``
(built once per call by the step that owns the story) rather than a raw
``story.json``/concept/template, which is what keeps this module free of any
dependency on ``store.py`` or ``templates.py``.

C1/B1/B2/B3 (concepts and the story bible, phase 1) and K1/P0/P1/R1/S1/S2/U1
(cast, places, props and the season arc, phase 2) are here.
E1-E4/T1/M1/S3/F1/N1/V1/V2 (spec 4.2) are later phases, built the same way
against the same ``Pack``.

Stdlib only (DEC-012); the one import outside this package is
``clipping.analysis.analyzer`` for the two shared temperature constants
(reused rather than redefined, per the task).
"""

from __future__ import annotations

from clipping.analysis.analyzer import ANALYTIC_TEMPERATURE, WRITING_TEMPERATURE

from . import context, schemas

# Bumped whenever the wording of a prompt below changes in a way that could
# change an answer -- same convention as clipping.analysis.prompts.PROMPT_VERSION.
# s2: C1 asks for one concept per call instead of two.
# s3: phase 2 adds K1/P0/P1/R1/S1/S2/U1, and SYSTEM_TEMPLATE gains the
# "Fields marked (English)" sentence those prompts rely on -- a wording
# change to every prompt built here, not just the new ones.
PROMPT_VERSION = "s3"

# Concepts are the one place the model is asked to be genuinely inventive;
# everything else in the bible is writing *from* a chosen concept, which
# wants less randomness so re-rolls stay recognisably the same story.
IDEATION_TEMPERATURE = 0.9

# "Generate 10 more" is C1_CALLS calls of C1_CONCEPTS_PER_CALL concept each.
# One card per call: on 2026-09-26 every two-card French reply was cut off
# mid-JSON at the old 500 cap (two full French cards need ~900-1,100 output
# tokens), and the chain then fell through to a paid link.
C1_CONCEPTS_PER_CALL = schemas.C1_CONCEPTS_PER_CALL
C1_CALLS = 10

# Output caps. French runs ~1.3x longer than English: the live English bible
# used B1 ~133/250, B2 ~223/250, B3 ~111/200, too tight for French at the old
# caps. The truncation guard of tests/test_story_prompts.py measures each cap
# against the largest French reply its prompt allows (S1 at 12 episodes, its
# maximum -- spec 2.6 ``EPISODES_PLANNED_MAX``).
MAX_TOKENS = {
    "C1": 700, "B1": 400, "B2": 520, "B3": 300,
    "K1": 750, "P0": 420, "P1": 260, "R1": 100, "S1": 950, "S2": 350, "U1": 120,
}
TEMPERATURE = {
    "C1": IDEATION_TEMPERATURE,
    "B1": WRITING_TEMPERATURE,
    "B2": WRITING_TEMPERATURE,
    "B3": WRITING_TEMPERATURE,
    "K1": WRITING_TEMPERATURE,
    "P0": WRITING_TEMPERATURE,
    "P1": WRITING_TEMPERATURE,
    "R1": WRITING_TEMPERATURE,
    "S1": WRITING_TEMPERATURE,
    "S2": WRITING_TEMPERATURE,
    "U1": ANALYTIC_TEMPERATURE,
}
SCHEMA_NAMES = {
    "C1": "story_concepts", "B1": "bible_core", "B2": "bible_world", "B3": "bible_values",
    "K1": "character_write", "P0": "places_props_proposal", "P1": "place_write",
    "R1": "prop_write", "S1": "season_arc_skeleton", "S2": "season_arc_entry",
    "U1": "vision_appearance",
}

# The ``bible:<field>`` grammar of spec 9.2: which prompt a regenerate note
# re-runs, and which of that prompt's fields it targets. "tone" also carries
# "genre_tags" because the two read as one editorial choice; "world" and
# "themes" cover a whole prompt's fields, since B2/B3 have no finer-grained
# regenerate unit in phase 1.
REGENERATE_TARGETS = {
    "logline": ("B1", ("logline",)),
    "premise": ("B1", ("premise",)),
    "tone": ("B1", ("tone", "genre_tags")),
    "world": ("B2", ("setting_summary", "rules", "time_period", "recurring_motifs")),
    "themes": ("B3", ("themes_and_values", "audience", "why_come_back")),
}

# Phase 2 (spec 9.2): "character:<cid>:text" -> K1, "place:<pid>:text" -> P1,
# "prop:<pid>:text" -> R1, "season:<ep>" -> S2. Unlike a bible field, an
# entity regenerate rewrites the whole entity's text (K1/P1/R1) or one arc
# entry (S2) in a single call, never one field among several, so this maps
# the grammar's kind straight to a prompt id -- no per-field tuple like
# REGENERATE_TARGETS above.
ENTITY_REGENERATE = {
    "character": "K1",
    "place": "P1",
    "prop": "R1",
    "season": "S2",
}

SYSTEM_TEMPLATE = (
    "You are the head writer of a serialized vertical-video fiction series "
    "for TikTok, YouTube Shorts and Instagram Reels. Each episode lasts "
    "about 60 seconds and ends on a cliffhanger, so every idea must pay off "
    "in seconds and make people come back. Reply with JSON only, matching "
    "the schema. Never output durations, timestamps or file paths. Never "
    "use real people, brands, studio names or copyrighted characters. Write "
    "all user-facing text in {language_name}. Fields marked (English) are "
    "for image and voice models: write them in English."
)


def _system(pack) -> str:
    return SYSTEM_TEMPLATE.format(language_name=pack.language_name)


# ------------------------------------------------------------------ helpers

def _format_current_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(_format_current_value(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_format_current_value(v)}" for k, v in value.items())
    return str(value)


def _regenerate_block(regenerate) -> str:
    """The "current values / rewrite only this field" block (spec 3, 9.2).

    The step applies only the target keys of ``REGENERATE_TARGETS`` back onto
    the document regardless of what else the model returns, so this is
    guidance for a better answer, not something relied on for correctness.
    """
    field = regenerate["field"]
    current = regenerate.get("current") or {}
    note = regenerate.get("note")

    lines = ["Current values:"]
    for key, value in current.items():
        lines.append(f"- {key}: {_format_current_value(value)}")

    instruction = f"Rewrite only `{field}`"
    if note:
        instruction += f", following the author's note: {note}"
    instruction += ", and keep every other field exactly as it is."

    lines.append("")
    lines.append(instruction)
    return "\n".join(lines) + "\n\n"


def _data_block(pack, sections) -> str:
    """The pack's sections, data first (DEC-062), in the order requested."""
    parts = []
    for name in sections:
        value = getattr(pack, name)
        if not value:
            continue
        if name == "style":
            parts.append(value)
        elif name == "concept":
            parts.append(f"Chosen concept:\n{value}")
        elif name == "bible":
            parts.append(f"Bible written so far:\n{value}")
        elif name == "world":
            parts.append(f"World written so far:\n{value}")
        elif name == "seed":
            parts.append(f"Seed idea from the user: {value}")
        elif name == "avoid":
            parts.append(f"Do not repeat or closely imitate these existing titles: {value}")
        elif name == "character_design_rules":
            parts.append(f"Character design rule: {value}")
        else:
            parts.append(value)
    block = "\n\n".join(parts)
    return f"{block}\n\n" if block else ""


# ------------------------------------------------------------------- C1

def build_c1(pack, *, style_ids, batch, of):
    """One original concept (spec 4.2, row C1): call *batch* of *of*.

    The concepts of one "Generate 10 more" differ because each call is sent
    every title written so far as "do not repeat" (the pack's ``avoid``).
    """
    style_ids = list(style_ids)
    data_block = _data_block(pack, ("style", "seed", "avoid"))
    styles_list = ", ".join(style_ids)

    user = (
        f"{data_block}"
        "Invent exactly 1 original concept for a new serialized "
        f"vertical-video fiction series (call {batch} of {of}).\n\n"
        "Give:\n"
        "- title: at most 8 words\n"
        "- logline: one sentence, at most 30 words\n"
        "- world: the setting and premise, at most 60 words\n"
        "- cast_sketch: 3 to 5 characters, each with a name, a role (one of "
        "lead, support, recurring, guest), and a one-line description, at "
        "most 25 words\n"
        "- hook_formula: what makes someone stop scrolling on episode 1\n"
        "- value: the real substance this story carries (a dilemma, a "
        "lesson, a truth about people)\n"
        "- retention_mechanics: why someone comes back for episode 2\n"
        f"- style_fit: the visual style that best fits this concept, one of "
        f"{styles_list}\n\n"
        "Never use real people, brands, studio names or copyrighted "
        "characters."
    )
    return _system(pack), user, schemas.c1_schema(style_ids)


# --------------------------------------------------------------- B1/B2/B3

_B1_ASK = (
    "Write the story bible's core fields for this concept.\n\n"
    "Give:\n"
    "- logline: one sentence, at most 30 words\n"
    "- premise: 2 to 6 sentences, at most 120 words\n"
    "- tone: at most 15 words\n"
    "- genre_tags: 2 to 5 tags, each at most 3 words\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)

_B2_ASK = (
    "Write the story bible's world fields for this concept.\n\n"
    "Give:\n"
    "- setting_summary: at most 80 words\n"
    "- rules: 4 to 6 rules, each at most 25 words\n"
    "- time_period: at most 6 words\n"
    "- recurring_motifs: exactly 3 motifs\n\n"
    "Stay consistent with the bible already written above. Never use real "
    "people, brands, studio names or copyrighted characters."
)

_B3_ASK = (
    "Write the story bible's values fields for this concept.\n\n"
    "Give:\n"
    "- themes_and_values: 2 to 4 themes, each at most 12 words\n"
    "- audience: an age rating (one of all, 10+, 13+, 16+) and 1 to 3 "
    "platforms (from tiktok, shorts, reels), no duplicates\n"
    "- why_come_back: exactly 3 lines, each at most 20 words, saying why "
    "someone comes back for episode 2\n\n"
    "Stay consistent with the bible and world already written above. Never "
    "use real people, brands, studio names or copyrighted characters."
)


def build_b1(pack, *, regenerate=None):
    """Logline, premise, tone, genre tags (spec 4.2, row B1)."""
    user = _data_block(pack, ("concept",))
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _B1_ASK
    return _system(pack), user, schemas.B1_SCHEMA


def build_b2(pack, *, regenerate=None):
    """World: setting, rules, time period, motifs (spec 4.2, row B2)."""
    user = _data_block(pack, ("concept", "bible"))
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _B2_ASK
    return _system(pack), user, schemas.B2_SCHEMA


def build_b3(pack, *, regenerate=None):
    """Themes/values, audience, why-come-back (spec 4.2, row B3)."""
    user = _data_block(pack, ("concept", "bible", "world"))
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _B3_ASK
    return _system(pack), user, schemas.B3_SCHEMA


# ============================================================ K1/P0/P1/R1/S1/S2/U1
#
# Phase 2 (spec 2.3-2.6, 4.2): cast, places, props and the season arc. Same
# data-first-then-task shape as C1/B1/B2/B3 above (DEC-062). Every prompt
# below marks the fields that feed an image or voice model "(English)" --
# the rest follows the story language, per SYSTEM_TEMPLATE's added sentence
# (PROMPT_VERSION s3). None of these builders take a name for anything an
# image model will see (spec 2.3): a sketch entry or an existing cast/place
# is only ever rendered by its own fields, never smuggled in as an opaque id.

def _cast_names(entities) -> list:
    return [entity["name"] for entity in entities]


def _cast_section(cast) -> str:
    """"Existing cast" block, rendered straight from the raw list handed to
    the builder (spec 4.1) -- not through ``Pack.cast``, since each builder
    that needs it also needs the same raw names for its own schema (an
    enum of valid relationship/owner/character targets), so it renders the
    text itself rather than trusting a second copy the caller put in the pack.
    """
    if not cast:
        return ""
    text, _ = context.cast_block(cast)
    return f"Existing cast:\n{text}\n\n"


def _places_section(places) -> str:
    """"Existing places" block; same rationale as ``_cast_section``."""
    if not places:
        return ""
    text, _ = context.places_block(places)
    return f"Existing places:\n{text}\n\n"


# ------------------------------------------------------------------------- K1

_K1_ASK = (
    "Write this character for the story.\n\n"
    "Give:\n"
    "- descriptor (English): appearance only, at most 45 words, never the character's name\n"
    "- signature_items (English): 2 to 3 recognisable items or marks, each at most 8 words\n"
    "- personality: 2 to 5 traits (each at most 4 words), and wants, fears, speech_style, "
    "each at most 25 words\n"
    "- voice: gender (one of female, male, neutral), age (one of child, young, adult, elder), "
    "1 to 3 style_tags (from warm, bright, deep, raspy, soft, fast, slow, smug, nervous, "
    "authoritative, playful, calm), direction (English, at most 20 words), and sample_line "
    "(at most 12 words, in character, no name)\n"
    "- relationships: 0 to 5 entries to the existing cast, each with `with` (an existing cast "
    "member) and `relation` (at most 15 words)\n\n"
    "Never use real people, brands, studio names or copyrighted characters. Never mention the "
    "character's own name in the descriptor, the signature items or the sample line."
)


def _character_sketch_block(character) -> str:
    return (
        f"Character to write: {character['name']} ({character['role']}, {character['archetype']})\n"
        f"One line: {character['one_line']}\n"
        f"Signature hint: {character['signature_hint']}"
    )


def _upload_notes_block(upload_notes) -> str:
    return f"Design reference supplied by the author: {upload_notes}; follow it."


def build_k1(pack, *, character, cast_so_far, upload_notes=None, regenerate=None):
    """One character from its cast-sketch entry (spec 4.2, row K1)."""
    user = _data_block(pack, ("bible", "style", "character_design_rules"))
    user += _cast_section(cast_so_far)
    user += _character_sketch_block(character) + "\n\n"
    if upload_notes:
        user += _upload_notes_block(upload_notes) + "\n\n"
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _K1_ASK
    return _system(pack), user, schemas.k1_schema(_cast_names(cast_so_far))


# ------------------------------------------------------------------------- P0

_P0_ASK = (
    "Propose places and props for this story.\n\n"
    "Give:\n"
    "- places: 2 to 3 places, each with a name (at most 5 words) and a one_line description "
    "(at most 20 words)\n"
    "- props: 0 to 3 props drawn from the cast's signature items or the bible's recurring "
    "motifs, each with a name (at most 5 words), a one_line description (at most 20 words), "
    "and an owner (an existing cast member, or null when it belongs to no one in particular)\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)


def build_p0(pack, *, cast):
    """Propose 2-3 places and 0-3 props (spec plan 1.2, extended for phase 2)."""
    user = _data_block(pack, ("bible", "world"))
    user += _cast_section(cast)
    user += _P0_ASK
    return _system(pack), user, schemas.p0_schema(_cast_names(cast))


# ------------------------------------------------------------------------- P1

_P1_ASK = (
    "Write this place for the story.\n\n"
    "Give:\n"
    "- descriptor (English): the place alone, at most 45 words, no people, no characters\n"
    "- layout_notes (English): what is left, right, back and foreground, at most 60 words, "
    "for continuity across shots\n"
    "- time_variants: 1 to 3 variants from day, night, dusk, rain, dawn, always including day\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)


def _place_sketch_block(place) -> str:
    return f"Place to write: {place['name']}\nOne line: {place['one_line']}"


def build_p1(pack, *, place, places_so_far, regenerate=None):
    """One place from its sketch entry (spec 4.2, row P1)."""
    user = _data_block(pack, ("bible", "world", "style"))
    user += _places_section(places_so_far)
    user += _place_sketch_block(place) + "\n\n"
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _P1_ASK
    return _system(pack), user, schemas.p1_schema()


# ------------------------------------------------------------------------- R1

_R1_ASK = (
    "Write this prop for the story.\n\n"
    "Give:\n"
    "- descriptor (English): the object alone, at most 30 words\n"
    "- owner: an existing cast member this prop belongs to, or null if it belongs to no one "
    "in particular\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)


def _prop_sketch_block(prop) -> str:
    return f"Prop to write: {prop['name']}\nOne line: {prop['one_line']}"


def build_r1(pack, *, prop, cast, regenerate=None):
    """One prop from its sketch entry (spec 4.2, row R1)."""
    user = _data_block(pack, ("bible", "style"))
    user += _cast_section(cast)
    user += _prop_sketch_block(prop) + "\n\n"
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _R1_ASK
    return _system(pack), user, schemas.r1_schema(_cast_names(cast))


# ------------------------------------------------------------------------- S1

_S1_ASK_TEMPLATE = (
    "Write the season arc skeleton for {episodes} episodes.\n\n"
    "Give exactly {episodes} entries in `arc`, one per episode, each with:\n"
    "- ep: the episode number, 1 to {episodes}\n"
    "- function: one of {functions}\n"
    "- summary: at most 25 words\n\n"
    "Episode 1 must be `setup`. Episode {episodes} must be `climax_and_reset`. Exactly one "
    "episode near the middle must be `midpoint_twist`.\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)


def build_s1(pack, *, episodes, cast, places):
    """Season arc skeleton for N episodes (spec 4.2, row S1)."""
    user = _data_block(pack, ("bible", "world"))
    user += _cast_section(cast)
    user += _places_section(places)
    functions = ", ".join(schemas.ARC_FUNCTIONS)
    user += _S1_ASK_TEMPLATE.format(episodes=episodes, functions=functions)
    return _system(pack), user, schemas.s1_schema(episodes)


# ------------------------------------------------------------------------- S2

_S2_ASK = (
    "Expand this arc entry into full detail.\n\n"
    "Give:\n"
    "- summary: at most 60 words\n"
    "- open_hooks_in: 0 to 3 hooks this episode resolves, each at most 15 words\n"
    "- open_hooks_out: 1 to 3 hooks this episode leaves open, each at most 15 words\n"
    "- characters: 1 to 5 of the existing cast involved in this episode\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)


def _arc_overview_block(arc, ep) -> str:
    lines = ["Season arc so far:"]
    for item in arc:
        marker = "  <- expand this one" if item["ep"] == ep else ""
        lines.append(f"- ep{item['ep']} ({item['function']}): {item['summary']}{marker}")
    return "\n".join(lines)


def build_s2(pack, *, entry, arc, cast, regenerate=None):
    """Expand one arc entry into full detail (spec 4.2, row S2)."""
    user = _data_block(pack, ("bible",))
    user += _cast_section(cast)
    user += _arc_overview_block(arc, entry["ep"]) + "\n\n"
    if regenerate is not None:
        user += _regenerate_block(regenerate)
    user += _S2_ASK
    return _system(pack), user, schemas.s2_schema(_cast_names(cast))


# ------------------------------------------------------------------------- U1

_U1_SYSTEM_TEMPLATE = (
    "You are a careful visual describer for a stylised character design "
    "pipeline. Reply with JSON only, matching the schema. Never name or "
    "identify any real person. The story is written in {language_name}, "
    "but appearance_notes is always in English: it feeds an image model, "
    "never the reader."
)

_U1_USER = (
    "Describe this design reference for a stylised character as appearance "
    "notes (English, at most 40 words): body shape, colours, clothing, "
    "accessories, distinctive marks. Do not name or identify any real "
    "person; if it is a photo of a real person, describe only clothing and "
    "colours."
)


def build_u1(*, language):
    """Describe an uploaded design reference (spec 4.2, row U1).

    The caller (the VISION chain) attaches the image itself; this only
    builds the surrounding system/user text, so it takes a language code
    directly rather than a full ``Pack`` -- there is no bible, style or
    cast to draw on for a single reference image. *language* only
    reassures the model that ``appearance_notes`` stays English even
    though the story itself is not.
    """
    language_name = context.LANGUAGE_NAMES.get(language, language)
    system = _U1_SYSTEM_TEMPLATE.format(language_name=language_name)
    return system, _U1_USER, schemas.u1_schema()
