"""The story-writing prompt catalogue: concepts and bible (spec 4, 4.1, 4.2).

Same pattern as ``clipping/analysis/prompts.py`` (DEC-062, DEC-064): English
instructions, data before the ask, one pure ``build_<id>(pack, ...) -> (system,
user, schema)`` per prompt, no network and nothing random, so every one of
them is golden-string tested. Every builder is handed a ``context.Pack``
(built once per call by the step that owns the story) rather than a raw
``story.json``/concept/template, which is what keeps this module free of any
dependency on ``store.py`` or ``templates.py``.

C1/B1/B2/B3 (concepts and the story bible, phase 1), K1/P0/P1/R1/S1/S2/U1
(cast, places, props and the season arc, phase 2) and E1/E2/E3/E4/T1/T1r
(the episode script and its storyboard, phase 3) are here.
M1/S3/F1/N1/V1/V2 (spec 4.2) are later phases, built the same way against
the same ``Pack``.

Stdlib only (DEC-012); the one import outside this package is
``clipping.analysis.analyzer`` for the two shared temperature constants
(reused rather than redefined, per the task).
"""

from __future__ import annotations

import re

from clipping.analysis.analyzer import ANALYTIC_TEMPERATURE, WRITING_TEMPERATURE

from . import context, prompting, schemas

# Bumped whenever the wording of a prompt below changes in a way that could
# change an answer -- same convention as clipping.analysis.prompts.PROMPT_VERSION.
# s2: C1 asks for one concept per call instead of two.
# s3: phase 2 adds K1/P0/P1/R1/S1/S2/U1, and SYSTEM_TEMPLATE gains the
# "Fields marked (English)" sentence those prompts rely on -- a wording
# change to every prompt built here, not just the new ones.
# s4: phase 3 adds E1/E2/E3/E4/T1/T1r (the episode script and its
# storyboard); the catalogue grows the same way it did for s3 (RC-E1: the
# K1...U1 builders' own output is unchanged -- see
# tests/test_story_prompts.py's byte-identical fixture test).
PROMPT_VERSION = "s4"

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
#
# E1/E2/E3/E4/T1 (stage 4, DEC-107's French-cap method): each started at the
# spec's own suggested cap (600/300/350/350/250) and was raised only as far
# as tests/test_story_prompts_episode.py's own largest-French-reply fixture
# proved necessary -- the largest reply each prompt's own ask text allows
# (every stated word/line/shot limit hit exactly) needs ~1374/533/664/728/530
# tokens respectively, so the caps below give each a working margin without
# padding past what the prompt can actually produce. T1r's spec cap (150)
# already covers its own worst case (~118) and was left alone.
MAX_TOKENS = {
    "C1": 700, "B1": 400, "B2": 520, "B3": 300,
    "K1": 750, "P0": 420, "P1": 260, "R1": 100, "S1": 950, "S2": 350, "U1": 120,
    "E1": 1450, "E2": 600, "E3": 720, "E4": 800, "T1": 580, "T1r": 150,
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
    "E1": WRITING_TEMPERATURE,
    "E2": WRITING_TEMPERATURE,
    "E3": WRITING_TEMPERATURE,
    "E4": ANALYTIC_TEMPERATURE,
    "T1": WRITING_TEMPERATURE,
    "T1r": WRITING_TEMPERATURE,
}
SCHEMA_NAMES = {
    "C1": "story_concepts", "B1": "bible_core", "B2": "bible_world", "B3": "bible_values",
    "K1": "character_write", "P0": "places_props_proposal", "P1": "place_write",
    "R1": "prop_write", "S1": "season_arc_skeleton", "S2": "season_arc_entry",
    "U1": "vision_appearance",
    "E1": "episode_beat_sheet", "E2": "episode_scene_dialogue", "E3": "episode_framing_scenes",
    "E4": "episode_consistency_check", "T1": "storyboard_shots", "T1r": "storyboard_shot_replan",
}

# E4's input is the whole script, not a small pack -- it needs a wider
# budget of its own; every other new prompt fits context.PACK_TOKEN_BUDGET
# (checked by its own French worst-case fixture test, like phase 1/2's
# truncation guard). The number below is sized on a 12-scene French
# worst-case fixture -- every line at its 22-word cap, the true per-function
# line-count ceiling (1/2/4/1 for recap/hook/body/cliffhanger), 5 cast
# members at their own 25-word speech_style cap, and series memory at
# _E4_MEMORY_MAX_*'s own caps -- which measures ~3,755 tokens
# (test_story_prompts_episode.py); 3,900 leaves it a margin while staying
# under the 4,000-token ceiling the spec sets.
#
# Stage 6 measured every episode prompt on live-sized data (a scratch copy of
# the live story b1104ec66b05: its cast, places and prop text, with a
# 12-scene French episode 2 at the limits -- 22-word lines, 4 lines per body
# scene, 15-word summaries, a 60-word note, the arc entry and memory at their
# caps; tests/test_story_episode_prompt_budgets.py rebuilds it with filler of
# the same lengths). Worst cases, chars/4: E1 1,102, E2 1,440 (with a note),
# E3 2,050 (in full; its partials 1,249-1,400), E4 3,523, T1 1,100, T1r
# 1,218 -- E1..T1r at or past 85 % of the 1,200-token pack budget, so each
# gets its own: the worst case + 15 %, rounded up to ten. E4's 3,900 still
# holds (+11 %) and stays under the spec's 4,000 ceiling. Nothing is trimmed
# to fit: a prompt over its budget still raises.
INPUT_BUDGET = {"E1": 1270, "E2": 1660, "E3": 2360, "E4": 3900, "T1": 1270, "T1r": 1410}

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
    """The character's own sketch lines. A generated concept's sketch and a
    custom character have no archetype and no signature hint: the part of
    the line they would fill is left out, never rendered as "None"."""
    archetype = character.get("archetype")
    label = f"{character['role']}, {archetype}" if archetype else character["role"]
    lines = [f"Character to write: {character['name']} ({label})", f"One line: {character['one_line']}"]
    hint = character.get("signature_hint")
    if hint:
        lines.append(f"Signature hint: {hint}")
    return "\n".join(lines)


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


# ==================================================================== E1/E2/E3/E4/T1/T1r
#
# Phase 3 (spec 2.7, 2.8, 4.2): the episode script and its storyboard. Same
# data-first-then-task shape as every builder above (DEC-062), one small
# artifact per call (DEC-107: E1 plans the whole episode's scenes but never
# writes a line; E2 writes one body scene's lines; E3 writes only the framing
# scenes E2 never touches; T1 plans one scene's shots; T1r re-plans one shot).
#
# Unlike phase 1/2 (where an existing cast/place/prop is referenced by NAME,
# spec 2.3, and Python resolves name -> id when it saves), the documents this
# phase writes (``episode_script_v1``, ``storyboard_v1``) store the story's
# own entity ids directly (``schemas.CHAR_ID_PATTERN`` etc.) -- E1's
# ``characters``/``props``/``place_id``, T1's ``@char_x``/``%prop_x``/
# ``#place_x:variant`` tags. So every id a builder below may hand back is
# shown to the model with its name right next to it (never a bare id), and
# every LLM output schema constrains that field to the ids it was actually
# offered. Python still owns every *structural* id (scene_id, line_id,
# shot_id): none of those ever appears in an output schema here.
#
# The LLM output schemas and their post-validators live here, not in
# schemas.py: they reuse that module's closed lists and id patterns
# (``schemas.SCENE_FUNCTIONS`` etc.) but define their own strict-mode
# object shapes, the same "subset only, lengths and counts are the prompt
# text's and the post-validator's job" rule phase 1/2 already follow.


def _llm_obj(properties, required=None) -> dict:
    """Object schema for a strict-mode LLM call: every property required
    unless *required* says otherwise, ``additionalProperties`` always False.

    Mirrors ``schemas._llm_obj``; kept local (rather than imported) so a
    post-validator addition here never needs a ``schemas.py`` change.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


def _word_count(text) -> int:
    return len(text.split())


def _text_errors(errors, path, value, *, max_words=None) -> None:
    """Mirrors ``schemas._check_text``; kept local for the same reason as
    ``_llm_obj`` above."""
    if not (isinstance(value, str) and value.strip()):
        errors.append(f"{path}: must be a non-empty string")
        return
    if max_words is not None and _word_count(value) > max_words:
        errors.append(f"{path}: {_word_count(value)} words, expected at most {max_words}")


def _nullable_text_errors(errors, path, value, max_words) -> None:
    if value is not None:
        _text_errors(errors, path, value, max_words=max_words)


def _id_name_block(entities, id_key) -> str:
    """"<id> — <name>" per entity: every id the model may choose from is
    shown with its name right next to it (spec 2.7 above)."""
    return "\n".join(f"- {e[id_key]} — {e['name']}" for e in entities)


def _personality_block(cast) -> str:
    """The scene's own present cast, personality only -- never the visual
    descriptor, which is for image prompts, not dialogue (spec 4.2, E2/E3)."""
    lines = []
    for c in cast:
        p = c["personality"]
        lines.append(
            f"- {c['char_id']} — {c['name']}: wants {p['wants']}; fears {p['fears']}; "
            f"speaks: {p['speech_style']}"
        )
    return "\n".join(lines)


# ------------------------------------------------------- F1: French elisions

# The one sentence every episode ask (E1/E2/E3, never E4 -- it writes no new
# prose) carries when the story's language is French: a free-tier reply has
# been seen writing an elision as two words with the apostrophe simply
# dropped ("l alliance", "d Etat", "m échappent"), so the ask spells out the
# form wanted instead of assuming it.
_FR_ELISION_SENTENCE = "Write French elisions with their apostrophe (l'eau, d'État, qu'il), never a space."


def _french_block(pack) -> str:
    """*_FR_ELISION_SENTENCE* plus the blank line that follows it in an ask
    built by string concatenation (E1/E2); blank for anything else.
    ``context.LANGUAGE_NAMES`` has exactly ``fr``/``en`` (schemas.LANGUAGES),
    so comparing the display name is exact, never a guess."""
    return f"{_FR_ELISION_SENTENCE}\n\n" if pack.language_name == "French" else ""


# The elidable words this repairs (spec 4.2, F1): le/la, de, je, ce, ne, me,
# se (one letter once their own vowel is dropped) and que (only its "e"
# drops, not the "u"). Matched case-insensitively and standalone -- neither
# lookaround uses ``\w`` loosely: "des" and "quand" never match, only a
# whole word spelled exactly "d" or "qu" -- followed by whitespace and a
# word starting with a vowel or "h" (accented vowels included). "y", "a" and
# "à" are never treated as a vowel-starting word to elide *into* (the
# human's own choice: "il y a" is a different word, not a dropped
# apostrophe, and is not worth the false positives).
_FR_ELIDABLE_RE = re.compile(r"(?<!\w)(qu|[ldjcnms])(?!\w)([ \t]+)(\S+)", re.IGNORECASE)
_FR_VOWEL_OR_H = set("aeiouAEIOUhH" "àâäæçéèêëîïôöœùûü" "ÀÂÄÆÇÉÈÊËÎÏÔÖŒÙÛÜ")
_FR_NEVER_ELIDED = {"y", "a", "à"}


def _fr_lead_word(token: str) -> str:
    """The leading run of letters of *token* (stops at the first digit,
    punctuation mark or apostrophe): what the elision check itself reads,
    a trailing comma or period never part of the question."""
    match = re.match(r"[^\W\d_]+", token)
    return match.group(0) if match else ""


def _fr_elision_sub(match) -> str:
    prefix, word = match.group(1), match.group(3)
    lead = _fr_lead_word(word)
    if not lead or lead.lower() in _FR_NEVER_ELIDED or lead[0] not in _FR_VOWEL_OR_H:
        return match.group(0)
    return f"{prefix}'{word}"


def repair_fr_elisions(text: str) -> str:
    """Deterministic repair of a French reply's dropped elision apostrophe
    (spec 4.2, F1): ``"l alliance"`` -> ``"l'alliance"``, ``"d Etat"`` ->
    ``"d'Etat"``, ``"m échappent"`` -> ``"m'échappent"``. No call, cannot
    fail (pure text -> text), and never touches *text* that already carries
    an apostrophe anywhere, straight or curly -- a reply with one correct
    elision and one dropped one is left exactly as it is, the safer of the
    two ways for this to be wrong.

    Applied by the script step (never here) to every model-written field
    that ends up in ``script.json`` when the story's language is ``"fr"``.
    """
    if "'" in text or "’" in text:
        return text
    return _FR_ELIDABLE_RE.sub(_fr_elision_sub, text)


# ------------------------------------------------------------------------- E1

_HOOK_STYLE_LINES = {
    "insert_prop": "insert_prop: a close shot of a diegetic object, sign or screen that states the premise",
    "shocking_image": "shocking_image: no text at all, just the single strongest, most striking image of the episode",
    "text_overlay": "text_overlay: on-screen text, at most 6 words, stating the premise from the first frame",
}

_CLIFFHANGER_STYLE_LINES = {
    "hard_stop": "hard_stop: end mid-confrontation, no resolution, no line that wraps it up",
    "cut_to_black": "cut_to_black: land the reveal, then cut to black for the end card",
}

_E1_ASK_TEMPLATE = (
    "Write the beat sheet for episode {ep}.\n\n"
    "Give:\n"
    "- title: the episode's own title, at most 8 words\n"
    "- scenes: exactly {n} entries, one for each of these, in order:\n"
    "{scene_list}\n\n"
    "Each scene:\n"
    "- function: one of {functions}\n"
    "- place_id: one of the existing places, at most {max_places} distinct places across the whole episode\n"
    "- time_variant: one of that place's own listed variants\n"
    "- characters: 0 to 6 of the existing cast\n"
    "- props: 0 to 4 of the existing props\n"
    "- summary: at most 15 words\n"
    "- emotion: one of {emotions}\n"
    "- target_duration_s: a hint inside its own slot's range -- {slot_ranges}\n\n"
    "Aim for the upper half of each range so the scenes sum near {target_s} s.\n\n"
    "Across the body scenes: open with setup, escalate with rising, include at least one peak, and land a "
    "turn right before the cliffhanger; one of them may be a quiet scene with no dialogue.\n\n"
    "The hook scene: {hook_style_line}.\n\n"
    "The cliffhanger scene: {cliffhanger_style_line}; it should leave one of this episode's own hooks open.\n\n"
    "{french_line}"
    "Never use real people, brands, studio names or copyrighted characters."
)


def _e1_scene_list(slots) -> str:
    """The numbered "Scene N -- kind" list E1's ask spells out, one line per
    entry of *slots* (:func:`timing.episode_slots`): an exact, positional
    list rather than the range ("8 to 12 scenes") the live bench found a
    model would settle short of every time (stage 12b)."""
    lines = []
    for i, slot in enumerate(slots, start=1):
        if slot == "body":
            lines.append(f"Scene {i} — body: choose setup, rising, peak or turn")
        else:
            lines.append(f"Scene {i} — {slot}")
    return "\n".join(lines)


def _slot_duration_range(function, template):
    """The ``(lo, hi)`` duration range of whichever slot holds *function*
    (mirrors ``timing.slot_name``/``slot_range``, reimplemented locally so
    this module stays free of a ``timing`` import -- every phase-3 builder
    that needs a word budget instead receives it pre-computed, see
    ``build_e2``/``build_e3``'s own ``word_budget``/``word_budgets``)."""
    for slot in template["slots"].values():
        if function in slot["functions"]:
            return tuple(slot["duration_s"])
    raise ValueError(f"no slot in the template holds function {function!r}")


def _slot_ranges_line(template) -> str:
    slots = template["slots"]
    return (
        f"recap {slots['recap']['duration_s'][0]:g}-{slots['recap']['duration_s'][1]:g}s, "
        f"hook {slots['hook']['duration_s'][0]:g}-{slots['hook']['duration_s'][1]:g}s, "
        f"body (setup/rising/peak/turn) {slots['body']['duration_s'][0]:g}-{slots['body']['duration_s'][1]:g}s "
        "each, "
        f"cliffhanger {slots['cliffhanger']['duration_s'][0]:g}-{slots['cliffhanger']['duration_s'][1]:g}s"
    )


def _place_variant_block(places) -> str:
    lines = []
    for place in places:
        variants = ", ".join(place["time_variants"])
        lines.append(f"- {place['place_id']} — {place['name']} (variants: {variants})")
    return "\n".join(lines)


def _arc_entry_block(arc_entry, *, label="This episode's arc entry") -> str:
    lines = [f"{label} ({arc_entry['function']}): {arc_entry['summary']}"]
    if arc_entry.get("open_hooks_in"):
        lines.append("Hooks this episode resolves: " + "; ".join(arc_entry["open_hooks_in"]))
    if arc_entry.get("open_hooks_out"):
        lines.append("Hooks this episode should leave open: " + "; ".join(arc_entry["open_hooks_out"]))
    return "\n".join(lines)


def e1_schema(cast_ids, place_ids, prop_ids) -> dict:
    """The E1 output schema (spec 2.7, 4.2, row E1): the beat sheet. No
    scene_id field -- Python assigns one to every scene in the order the
    model returns them (spec: the model never outputs an id Python owns)."""
    char_items = {"type": "string", "enum": list(cast_ids)} if cast_ids else {"type": "string"}
    prop_items = {"type": "string", "enum": list(prop_ids)} if prop_ids else {"type": "string"}
    scene = _llm_obj({
        "function": {"type": "string", "enum": list(schemas.SCENE_FUNCTIONS)},
        "place_id": {"type": "string", "enum": list(place_ids)} if place_ids else {"type": "string"},
        "time_variant": {"type": "string", "description": "one of that place's own listed variants"},
        "characters": {"type": "array", "description": "0-6 of the existing cast", "items": char_items},
        "props": {"type": "array", "description": "0-4 of the existing props", "items": prop_items},
        "summary": {"type": "string", "description": "at most 15 words"},
        "emotion": {"type": "string", "enum": list(schemas.EMOTIONS)},
        "target_duration_s": {"type": "number", "description": "a hint inside the scene's own slot range"},
    })
    return _llm_obj({
        "title": {"type": "string", "description": "at most 8 words"},
        "scenes": {"type": "array", "description": "one per beat, in order", "items": scene},
    })


def build_e1(pack, *, ep, arc_entry, template, episode_defaults, cast, places, props, memory, slots):
    """The episode's beat sheet (spec 2.7, 4.2, row E1): every scene stub
    (function, place, time variant, cast, props, a one-line summary, an
    emotion and a duration hint), in the order the episode template wants,
    expanding the arc entry the season already committed to.

    *slots* is the exact, ordered list of slot kinds the reply must fill
    (:func:`timing.episode_slots`, computed by the caller from *template*
    and *ep* -- this module stays free of a ``timing`` import): the ask
    spells it out as a numbered list and pins the count (stage 12b -- a
    range ask, "8 to 12 scenes", left the live bench at E1 0/3 on both free
    links, a model settling short every time).

    *cast*/*places*/*props* are the story's full rosters: each item at
    least ``{"char_id"/"place_id"/"prop_id", "name"}`` (*places* also
    ``"time_variants"``, the list of variant names already chosen for it).
    *memory* is the season document (``season.json``); episode 1 needs none
    of it (:func:`context.memory_section`).
    """
    memory_text, was_cut = context.memory_section(memory, ep)
    if was_cut:
        pack.trimmed.append("memory")

    user = _arc_entry_block(arc_entry) + "\n\n"
    user += f"{memory_text}\n\n"
    if cast:
        user += "Existing cast:\n" + _id_name_block(cast, "char_id") + "\n\n"
    if places:
        user += "Existing places:\n" + _place_variant_block(places) + "\n\n"
    if props:
        user += "Existing props:\n" + _id_name_block(props, "prop_id") + "\n\n"

    user += _E1_ASK_TEMPLATE.format(
        ep=ep, n=len(slots), scene_list=_e1_scene_list(slots),
        functions=", ".join(schemas.SCENE_FUNCTIONS),
        max_places=episode_defaults["max_places"],
        emotions=", ".join(schemas.EMOTIONS),
        slot_ranges=_slot_ranges_line(template),
        target_s=template["target_s"],
        hook_style_line=_HOOK_STYLE_LINES[episode_defaults["hook_style"]],
        cliffhanger_style_line=_CLIFFHANGER_STYLE_LINES[episode_defaults["cliffhanger_style"]],
        french_line=_french_block(pack),
    )

    cast_ids = [c["char_id"] for c in cast]
    place_ids = [p["place_id"] for p in places]
    prop_ids = [p["prop_id"] for p in props]
    return _system(pack), user, e1_schema(cast_ids, place_ids, prop_ids)


def _e1_slot_bounds(template, has_recap):
    """``(scenes_lo, scenes_hi, body_lo, body_hi)``: the episode template's
    own total scene count, and the body slot's count narrowed to whatever
    the fixed slots (hook 1, cliffhanger 1, recap 0 or 1) leave inside it
    (spec 6.2) -- the overlap of the body slot's own range and "everything
    the episode total allows once the fixed slots are paid for".
    """
    scenes_lo, scenes_hi = template["scenes"]
    body_lo, body_hi = template["slots"]["body"]["count"]
    fixed = 2 + (1 if has_recap else 0)
    lo = max(body_lo, scenes_lo - fixed)
    hi = min(body_hi, scenes_hi - fixed)
    return scenes_lo, scenes_hi, lo, hi


def validate_e1(reply, *, ep, template, episode_defaults, cast_ids, places, prop_ids) -> list:
    """Post-validation for an E1 reply, beyond what its schema can express
    (spec 2.7, 6.2): scene/body counts, the function order (an optional
    recap first, exactly one hook right after it, exactly one cliffhanger
    last, everything between them a body function), place/variant/
    character/prop references, the places-per-episode cap, word caps, and
    each scene's duration hint against its own slot's range.

    Stage 12b follow-up: ``build_e1``'s ask still requests an exact count
    (``len(timing.episode_slots(template, ep))``, aiming at the template's
    own default), but this validator accepts any LEGAL count instead of
    demanding that exact one -- a range/aggregate check, restored from
    before the stage-12b-first-cut's exact positional one. The live re-bench
    (stage 12b) found free-tier models settle for fewer scenes than asked
    even against an explicit numbered list (NVIDIA nemotron-3.5-lightning:
    8-9 of 10 asked, all legal), and a shorter-but-legal reply should not be
    rejected outright -- rejecting it would just repeat the 0/3 the exact
    check produced live, without the model ever being able to comply.

    *places* maps place_id -> its own iterable of time-variant names (the
    same shape ``schemas.episode_script_context_errors`` already uses).
    """
    cast_ids = list(cast_ids)
    prop_ids = list(prop_ids)
    schema = e1_schema(cast_ids, list(places), prop_ids)
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    _text_errors(errors, "$.title", reply["title"], max_words=8)

    scenes = reply["scenes"]
    has_recap = ep >= template["recap_from_episode"]
    scenes_lo, scenes_hi, body_lo, body_hi = _e1_slot_bounds(template, has_recap)
    if not (scenes_lo <= len(scenes) <= scenes_hi):
        errors.append(f"$.scenes: {len(scenes)} scene(s), expected {scenes_lo}-{scenes_hi}")

    functions = [s["function"] for s in scenes]
    expected_prefix = (["recap"] if has_recap else []) + ["hook"]
    if functions[: len(expected_prefix)] != expected_prefix:
        errors.append(f"$.scenes: must start with {expected_prefix}, got {functions[:len(expected_prefix)]}")
    if not functions or functions[-1] != "cliffhanger":
        errors.append("$.scenes: the last scene must have function 'cliffhanger'")
    if functions.count("hook") != 1:
        errors.append(f"$.scenes: exactly one 'hook' scene expected, got {functions.count('hook')}")
    if functions.count("cliffhanger") != 1:
        errors.append(f"$.scenes: exactly one 'cliffhanger' scene expected, got {functions.count('cliffhanger')}")
    if has_recap and functions.count("recap") != 1:
        errors.append(f"$.scenes: episode {ep} (>= recap_from_episode) needs exactly one 'recap' scene")
    if not has_recap and "recap" in functions:
        errors.append(f"$.scenes: episode {ep} must not have a 'recap' scene")

    body_start = len(expected_prefix)
    body_end = len(functions) - 1 if functions and functions[-1] == "cliffhanger" else len(functions)
    body_functions = functions[body_start:body_end]
    if not (body_lo <= len(body_functions) <= body_hi):
        errors.append(f"$.scenes: {len(body_functions)} body scene(s), expected {body_lo}-{body_hi}")
    for i, fn in enumerate(body_functions):
        if fn not in schemas.BODY_FUNCTIONS:
            errors.append(
                f"$.scenes[{body_start + i}].function: {fn!r} is not a body function {schemas.BODY_FUNCTIONS}"
            )

    used_places = set()
    for i, scene in enumerate(scenes):
        path = f"$.scenes[{i}]"
        place_id = scene["place_id"]
        used_places.add(place_id)
        variants = set(places.get(place_id) or ())
        if scene["time_variant"] not in variants:
            errors.append(f"{path}.time_variant: {scene['time_variant']!r} is not a variant of {place_id!r}")
        _text_errors(errors, f"{path}.summary", scene["summary"], max_words=15)
        lo, hi = _slot_duration_range(scene["function"], template)
        duration = scene["target_duration_s"]
        if not (lo <= duration <= hi):
            errors.append(
                f"{path}.target_duration_s: {duration} is outside its {scene['function']} slot's {lo}-{hi}s range"
            )

    max_places = episode_defaults["max_places"]
    if len(used_places) > max_places:
        errors.append(f"$.scenes: {len(used_places)} distinct place(s), more than max_places ({max_places})")

    return errors


# ------------------------------------------------------------------------- E2

_E2_ASK_TEMPLATE = (
    "Write this scene's dialogue.\n\n"
    "Give:\n"
    "- lines: 1 to 4 lines (use 2-3 lines when two or more characters are present), each with speaker "
    "(one of {speakers}), text (story language, at most 22 words; reference lines run 3-8 words), emotion "
    "(one of {emotions}), delivery (English, at most 12 words; the story's voice performance is "
    "{voice_direction})\n"
    "- sfx_cues: 0 or more, each with at ('start' or a line number 1-n) and cue (one of {sfx_cues})\n"
    "- on_screen_text: null unless the scene truly needs one (at most 6 words, story language)\n\n"
    "Write {word_budget_lo}-{word_budget_hi} words of dialogue in total (not fewer than {word_budget_lo}).\n\n"
    "{french_line}"
    "Never use real people, brands, studio names or copyrighted characters."
)

# The ask's own lower bound (spec 4.2, F3): ~0.7 of the scene's word budget,
# never below 3 -- the budget itself (``timing.word_budget``) never goes
# below 3 either, so the range is never inverted. The validator below is
# more lenient than this (half the budget, not 0.7 of it): the ask states
# the range it actually wants, the post-validator only the floor a reply
# must clear to be usable at all, leaving room for the existing retry-once
# path to ask again without every near-miss being rejected outright.
def _e2_word_range(word_budget: int) -> tuple:
    return max(3, round(0.7 * word_budget)), word_budget


# The prefix every "too few words" validator error starts with (never any
# other ``validate_e2`` message): the script step's own retry policy
# (``steps.script.write_body_scene``) reads it to tell this one error apart
# from a genuinely broken reply, so a second attempt that is merely a bit
# short can be accepted instead of failing the whole scene (spec 4.2, F3).
E2_WORD_FLOOR_PREFIX = "$.lines: too few words"


def _line_schema(speakers) -> dict:
    return _llm_obj({
        "speaker": {"type": "string", "enum": list(speakers)} if speakers else {"type": "string"},
        "text": {"type": "string", "description": "story language, at most 22 words"},
        "emotion": {"type": "string", "enum": list(schemas.EMOTIONS)},
        "delivery": {"type": "string", "description": "English, at most 12 words"},
    })


def _scene_stub_line(scene) -> str:
    return f"Scene ({scene['function']}, emotion: {scene['emotion']}): {scene['summary']}"


def e2_schema(speakers, sfx_cue_names) -> dict:
    """The E2 output schema (spec 2.7, 4.2, row E2): one body scene's lines,
    sfx cues and optional on-screen text."""
    cue_type = {"type": "string", "enum": list(sfx_cue_names)} if sfx_cue_names else {"type": "string"}
    sfx = _llm_obj({
        "at": {"type": "string", "description": "'start' or a line number 1-n"},
        "cue": cue_type,
    })
    return _llm_obj({
        "lines": {"type": "array", "description": "1-4 lines", "items": _line_schema(speakers)},
        "sfx_cues": {"type": "array", "items": sfx},
        "on_screen_text": {"type": ["string", "null"], "description": "at most 6 words, or null"},
    })


def build_e2(pack, *, scene, scene_number, outline, previous, word_budget, cast, place, props, sfx_cues,
             narrator_enabled, voice_direction, note=None):
    """One body scene's dialogue (spec 2.7, 4.2, row E2): 1-4 lines within
    *word_budget* words total (``timing.word_budget``, computed by the
    caller so this module stays free of a ``timing`` import), optional sfx
    cues and on-screen text. E2 never writes the hook, cliffhanger or recap
    scenes -- :func:`build_e3` does (one small artifact per request,
    DEC-107).

    *scene* is E1's own stub for this scene, already carrying a real
    ``scene_id`` (Python assigns one to every E1 scene before any E2/E3/T1
    call). *cast*/*props* are only this scene's own present entities, each
    at least ``{"char_id"/"prop_id", "name"}`` (*cast* also
    ``"personality"``: traits/wants/fears/speech_style, never a visual
    descriptor -- that is for image prompts, not dialogue). *place* is
    ``{"place_id", "name", "layout_notes"}``. *sfx_cues* is the story's own
    cue names (``style_lock.audio.sfx_cues``). *previous* is ``None`` for
    the episode's first body scene, else ``{"summary", "speaker_name",
    "text"}`` for the immediately preceding scene's last line. *note* is the
    author's note of a ``scene:<ep>:<sid>`` regenerate, shown the way
    :func:`build_e3` and :func:`build_t1r` show theirs (none: the prompt is
    byte-identical to one built without it).
    """
    names = {c["char_id"]: c["name"] for c in cast}
    user = context.outline_section(outline, names) + "\n\n"
    if previous is None:
        user += "Previous scene: none -- this is the episode's first body scene.\n\n"
    else:
        user += (
            f"Previous scene: {previous['summary']}\n"
            f"Its last line -- {previous['speaker_name']}: {previous['text']}\n\n"
        )
    user += _scene_stub_line(scene) + "\n\n"
    if cast:
        user += "Characters present:\n" + _personality_block(cast) + "\n\n"
    user += f"Place: {place['name']} -- {place['layout_notes']}\n\n"
    if props:
        user += "Props present:\n" + _id_name_block(props, "prop_id") + "\n\n"

    if note:
        user += f"Follow the author's note: {note}\n\n"

    speakers = [c["char_id"] for c in cast] + (["narrator"] if narrator_enabled else [])
    sfx_cue_names = list(sfx_cues)
    lo, hi = _e2_word_range(word_budget)
    user += _E2_ASK_TEMPLATE.format(
        speakers=", ".join(speakers),
        emotions=", ".join(schemas.EMOTIONS),
        sfx_cues=", ".join(sfx_cue_names) if sfx_cue_names else "none available for this story",
        word_budget_lo=lo, word_budget_hi=hi,
        voice_direction=voice_direction,
        french_line=_french_block(pack),
    )
    return _system(pack), user, e2_schema(speakers, sfx_cue_names)


def validate_e2(reply, *, scene, narrator_enabled, sfx_cues, word_budget=None) -> list:
    """Post-validation for an E2 reply (spec 2.7, 4.2): line count and caps,
    a speaker that is one of the scene's own characters (or ``"narrator"``
    when enabled), sfx cue references against a valid line number, and the
    on-screen text cap.

    The scene's total dialogue must stay within its call's ``word_budget``
    -- a soft target ``build_e2``'s own prompt states; this function does
    not re-derive it (that needs the episode template and style lock it is
    never given) and instead relies on the 22-word per-line cap it does
    check below. When *word_budget* is given (the caller's own
    ``timing.word_budget``), a reply whose total dialogue falls under half
    of it is one error more (:data:`E2_WORD_FLOOR_PREFIX`, spec 4.2, F3): a
    floor well below the ask's own range (:func:`_e2_word_range`), leaving
    slack so the existing retry-once path (``steps.script``) has room to
    fix a merely-short reply instead of every near-miss being rejected.
    *word_budget* stays ``None`` (no floor check) for a caller that has none
    to give.
    """
    speakers = list(scene["characters"]) + (["narrator"] if narrator_enabled else [])
    sfx_cue_names = list(sfx_cues)
    schema = e2_schema(speakers, sfx_cue_names)
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    lines = reply["lines"]
    if not (1 <= len(lines) <= 4):
        errors.append(f"$.lines: {len(lines)} line(s), expected 1-4")
    n = len(lines)
    for i, line in enumerate(lines):
        path = f"$.lines[{i}]"
        _text_errors(errors, f"{path}.text", line["text"], max_words=22)
        _text_errors(errors, f"{path}.delivery", line["delivery"], max_words=12)

    for i, cue in enumerate(reply["sfx_cues"]):
        at = cue["at"]
        if at != "start" and not (at.isdigit() and 1 <= int(at) <= n):
            errors.append(f"$.sfx_cues[{i}].at: {at!r} is not 'start' or a line number 1-{n}")

    _nullable_text_errors(errors, "$.on_screen_text", reply["on_screen_text"], 6)

    if word_budget is not None:
        total_words = sum(_word_count(line["text"]) for line in lines)
        floor = (word_budget + 1) // 2  # ceil(word_budget / 2), stdlib-only
        if total_words < floor:
            errors.append(
                f"{E2_WORD_FLOOR_PREFIX}: {total_words} in total, expected at least {floor} "
                f"(half of the {word_budget}-word budget)"
            )
    return errors


# ------------------------------------------------------------------------- E3

_E3_KEY_ASKS = {
    "hook": (
        "- hook: lines (1-2 lines, speaker one of {speakers}, text story language at most 22 words, emotion "
        "one of {emotions}, delivery English at most 12 words) and on_screen_text (story language, null unless "
        "the hook style needs one)"
    ),
    "cliffhanger": (
        "- cliffhanger: reveal (story language, at most 40 words) and lines (0-1 lines, same shape as a hook "
        "line)"
    ),
    "recap": (
        "- recap: lines (0-1 lines, same shape as a hook line) and on_screen_text (story language, at most 6 "
        "words, null unless needed)"
    ),
    "teaser": "- teaser: one sentence about the next episode, at most 15 words, story language",
}


def _e3_keys(part, ep) -> list:
    """Which top-level keys an E3 call asks for: every framing key it
    writes for *part=None*, or just *part* alone -- the same "regenerate one
    thing" shape ``build_b1``'s ``regenerate`` uses for one field, here for
    one key of this multi-key artifact instead (spec 9.2).
    """
    if part is not None:
        if part == "recap" and ep < 2:
            raise ValueError("a recap variant only applies from episode 2 on")
        return [part]
    return (["recap"] if ep >= 2 else []) + ["hook", "cliffhanger", "teaser"]


def _e3_hook_schema(speakers) -> dict:
    return _llm_obj({
        "lines": {"type": "array", "description": "1-2 lines", "items": _line_schema(speakers)},
        "on_screen_text": {"type": ["string", "null"], "description": "at most 6 words, or null"},
    })


def _e3_cliffhanger_schema(speakers) -> dict:
    return _llm_obj({
        "reveal": {"type": "string", "description": "at most 40 words"},
        "lines": {"type": "array", "description": "0-1 lines", "items": _line_schema(speakers)},
    })


def _e3_recap_schema(speakers) -> dict:
    return _llm_obj({
        "lines": {"type": "array", "description": "0-1 lines", "items": _line_schema(speakers)},
        "on_screen_text": {"type": ["string", "null"], "description": "at most 6 words, or null"},
    })


def e3_schema(part, ep, speakers) -> dict:
    """The E3 output schema (spec 2.7, 4.2, row E3), reduced to just *part*
    when it is given; ``"recap"`` is present only for ``ep >= 2``."""
    properties = {}
    for key in _e3_keys(part, ep):
        if key == "hook":
            properties["hook"] = _e3_hook_schema(speakers)
        elif key == "cliffhanger":
            properties["cliffhanger"] = _e3_cliffhanger_schema(speakers)
        elif key == "recap":
            properties["recap"] = _e3_recap_schema(speakers)
        else:
            properties["teaser"] = {"type": "string", "description": "at most 15 words"}
    return _llm_obj(properties)


def _e3_hook_block(hook_scene, first_body_line, episode_defaults, word_budget) -> str:
    lines = [_scene_stub_line(hook_scene).replace("Scene (", "Hook scene (")]
    lines.append(f"Hook style: {_HOOK_STYLE_LINES[episode_defaults['hook_style']]}")
    if first_body_line is None:
        lines.append("The next scene has no line yet.")
    else:
        lines.append(f"The next scene opens with -- {first_body_line['speaker_name']}: {first_body_line['text']}")
    if word_budget is not None:
        lines.append(f"Keep the hook's dialogue within {word_budget} words.")
    return "\n".join(lines)


def _e3_cliffhanger_block(cliffhanger_scene, last_body_line, arc_entry, episode_defaults, word_budget) -> str:
    lines = [_scene_stub_line(cliffhanger_scene).replace("Scene (", "Cliffhanger scene (")]
    lines.append(f"Cliffhanger style: {_CLIFFHANGER_STYLE_LINES[episode_defaults['cliffhanger_style']]}")
    if last_body_line is None:
        lines.append("The scene right before it has no line yet.")
    else:
        lines.append(
            f"The scene right before it ends with -- {last_body_line['speaker_name']}: {last_body_line['text']}"
        )
    if arc_entry.get("open_hooks_out"):
        lines.append("Leave one of these hooks open: " + "; ".join(arc_entry["open_hooks_out"]))
    if word_budget is not None:
        lines.append(f"Keep its line within {word_budget} words.")
    return "\n".join(lines)


def _e3_recap_block(recap_scene, word_budget) -> str:
    lines = [_scene_stub_line(recap_scene).replace("Scene (", "Recap scene (")]
    if word_budget is not None:
        lines.append(f"Keep its line within {word_budget} words.")
    return "\n".join(lines)


def _e3_teaser_block(next_arc_entry) -> str:
    if next_arc_entry is None:
        return "This is the season finale: there is no next episode to tease."
    return _arc_entry_block(next_arc_entry, label="Next episode's arc entry")


def _e3_ask(keys, speakers, *, french_line="") -> str:
    lines = ["Write " + ", ".join(keys) + ".", "", "Give:"]
    for key in keys:
        lines.append(_E3_KEY_ASKS[key].format(speakers=", ".join(speakers), emotions=", ".join(schemas.EMOTIONS)))
    lines.append("")
    if french_line:
        lines.append(french_line)
        lines.append("")
    lines.append("Never use real people, brands, studio names or copyrighted characters.")
    return "\n".join(lines)


def build_e3(pack, *, ep, part=None, note=None, hook_scene, cliffhanger_scene, recap_scene, outline,
             first_body_line, last_body_line, arc_entry, next_arc_entry, memory, episode_defaults,
             word_budgets, cast, narrator_enabled):
    """The framing scenes E2 never writes (spec 2.7, 4.2, row E3): the hook,
    the cliffhanger, the recap (episode >= 2 only) and the next-episode
    teaser, each written from the scene stub E1 already gave it plus the
    line right before/after it in the body.

    *part* narrows the ask (and the schema) to one key, an optional *note*
    guiding it -- the same "regenerate one thing" shape as ``build_b1``'s
    ``regenerate`` (spec 9.2), just one key of a multi-key artifact instead
    of one field of a single-object one (:func:`_e3_keys`). ``part ==
    "recap"`` needs ``ep >= 2``; asking for it below that raises
    ``ValueError`` before any text is built, the same defensive check
    ``prompting._check_framing`` makes for an unknown framing.

    *hook_scene*/*cliffhanger_scene*/*recap_scene* are E1's own stubs for
    those scenes (*recap_scene* is ``None`` below episode 2); *cast* is the
    union of characters any of them may speak as, shaped like E2's own
    *cast* (id, name, personality). *first_body_line*/*last_body_line* are
    ``None`` or ``{"speaker_name", "text"}``: the first line the episode's
    body speaks (so the hook does not contradict it) and the last one
    before the cliffhanger (so the cliffhanger continues from it).
    *word_budgets* is ``{"hook": n, "cliffhanger": n, "recap": n}`` (the
    caller's own ``timing.word_budget`` calls, one per framing scene this
    call writes; a missing key is treated as "no budget hint"). *memory* is
    the season document, read the same way :func:`build_e1` reads it.
    """
    keys = _e3_keys(part, ep)
    names = {c["char_id"]: c["name"] for c in cast}
    speakers = [c["char_id"] for c in cast] + (["narrator"] if narrator_enabled else [])
    word_budgets = word_budgets or {}

    user = context.outline_section(outline, names) + "\n\n"

    if "hook" in keys:
        user += _e3_hook_block(hook_scene, first_body_line, episode_defaults, word_budgets.get("hook")) + "\n\n"
    if "cliffhanger" in keys:
        user += _e3_cliffhanger_block(
            cliffhanger_scene, last_body_line, arc_entry, episode_defaults, word_budgets.get("cliffhanger"),
        ) + "\n\n"
    if "recap" in keys:
        memory_text, was_cut = context.memory_section(memory, ep)
        if was_cut:
            pack.trimmed.append("memory")
        user += f"{memory_text}\n\n"
        user += _e3_recap_block(recap_scene, word_budgets.get("recap")) + "\n\n"
    if "teaser" in keys:
        user += _e3_teaser_block(next_arc_entry) + "\n\n"

    if cast:
        user += "Characters who may speak:\n" + _personality_block(cast) + "\n\n"
    if note:
        user += f"Follow the author's note: {note}\n\n"

    user += _e3_ask(keys, speakers, french_line=_FR_ELISION_SENTENCE if pack.language_name == "French" else "")
    return _system(pack), user, e3_schema(part, ep, speakers)


def _line_field_errors(errors, path, line, allowed_speakers) -> None:
    if allowed_speakers and line["speaker"] not in allowed_speakers:
        errors.append(f"{path}.speaker: {line['speaker']!r} is not one of {sorted(allowed_speakers)}")
    _text_errors(errors, f"{path}.text", line["text"], max_words=22)
    _text_errors(errors, f"{path}.delivery", line["delivery"], max_words=12)


def validate_e3(reply, *, ep, part, hook_scene, cliffhanger_scene, recap_scene, narrator_enabled,
                 episode_defaults) -> list:
    """Post-validation for an E3 reply (spec 2.7, 4.2), scoped to whichever
    keys *part* asked for (:func:`_e3_keys`; every key, for ``part=None``):
    line counts and caps, a speaker that belongs to the scene actually being
    written, the cliffhanger's reveal cap, the teaser cap, and the hook's
    on-screen text rule per ``hook_style`` (spec 6.2: required and <= 5
    words for ``insert_prop``, required for ``text_overlay``, optional
    otherwise, <= 6 words in every case).
    """
    keys = _e3_keys(part, ep)

    def speakers_for(scene):
        return list(scene["characters"]) + (["narrator"] if narrator_enabled else [])

    schema_speakers = set()
    for key, scene in (("hook", hook_scene), ("cliffhanger", cliffhanger_scene), ("recap", recap_scene)):
        if key in keys and scene is not None:
            schema_speakers.update(speakers_for(scene))
    schema = e3_schema(part, ep, sorted(schema_speakers))
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    if "hook" in keys:
        hook = reply["hook"]
        allowed = set(speakers_for(hook_scene))
        if not (1 <= len(hook["lines"]) <= 2):
            errors.append(f"$.hook.lines: {len(hook['lines'])} line(s), expected 1-2")
        for i, line in enumerate(hook["lines"]):
            _line_field_errors(errors, f"$.hook.lines[{i}]", line, allowed)

        hook_style = episode_defaults["hook_style"]
        text = hook["on_screen_text"]
        if hook_style == "text_overlay" and text is None:
            errors.append("$.hook.on_screen_text: required when hook_style is 'text_overlay'")
        elif hook_style == "insert_prop" and text is None:
            errors.append(
                "$.hook.on_screen_text: required when hook_style is 'insert_prop' (the diegetic object's text)"
            )
        max_words = 5 if hook_style == "insert_prop" else 6
        _nullable_text_errors(errors, "$.hook.on_screen_text", text, max_words)

    if "cliffhanger" in keys:
        cliff = reply["cliffhanger"]
        allowed = set(speakers_for(cliffhanger_scene))
        _text_errors(errors, "$.cliffhanger.reveal", cliff["reveal"], max_words=40)
        if len(cliff["lines"]) > 1:
            errors.append(f"$.cliffhanger.lines: {len(cliff['lines'])} line(s), expected 0-1")
        for i, line in enumerate(cliff["lines"]):
            _line_field_errors(errors, f"$.cliffhanger.lines[{i}]", line, allowed)

    if "recap" in keys:
        recap = reply["recap"]
        allowed = set(speakers_for(recap_scene)) if recap_scene else set()
        if len(recap["lines"]) > 1:
            errors.append(f"$.recap.lines: {len(recap['lines'])} line(s), expected 0-1")
        for i, line in enumerate(recap["lines"]):
            _line_field_errors(errors, f"$.recap.lines[{i}]", line, allowed)
        _nullable_text_errors(errors, "$.recap.on_screen_text", recap["on_screen_text"], 6)

    if "teaser" in keys:
        _text_errors(errors, "$.teaser", reply["teaser"], max_words=15)

    return errors


# ------------------------------------------------------------------------- E4

_E4_SYSTEM_TEMPLATE = (
    "You are a meticulous continuity editor for a serialized vertical-video fiction series. You do not write "
    "new material: you read a finished episode script against the story's bible, cast and series memory, and "
    "report only what is actually inconsistent. Reply with JSON only, matching the schema. Write every fix in "
    "{language_name}."
)

_E4_ASK = (
    "Check this script for consistency.\n\n"
    "Look for: continuity errors against the bible and the series memory above; a character speaking out of "
    "character (against their own personality or speech_style); a scene's action contradicting its own "
    "place.\n\n"
    "Give:\n"
    "- passed: true only when you found no issue\n"
    "- issues: at most 6, each with scene_id (one of the script's own scene ids, or null when the issue is "
    "not tied to one scene), kind (one of continuity, character, place, series_memory, other), and fix (at "
    "most 40 words, in the story language)"
)


def _e4_system(pack) -> str:
    return _E4_SYSTEM_TEMPLATE.format(language_name=pack.language_name)


def _e4_cast_block(cast) -> str:
    """The cast, speech_style only -- E4's character check is "speaking out
    of character *against their own ... speech_style*" (spec 4.2, the
    ``_E4_ASK`` text below); wants/fears drive what a character says in
    E2/E3's own ``_personality_block``, not whether a line reads as them, so
    E4 leaves them out to keep the whole-script call's cast section small.
    """
    return "\n".join(
        f"- {c['char_id']} — {c['name']}: speaks {c['personality']['speech_style']}" for c in cast
    )


# E4 reads the whole script already (its digest dominates the call), so its
# own context sections stay small and bounded regardless of how long the
# season has run -- most recent first, the same "never grow unboundedly"
# rule ``context.cast_block``/``places_block`` apply to a pack's own cast
# and places (spec 4.1).
_E4_MEMORY_MAX_RECAPS = 2
_E4_MEMORY_MAX_HOOKS = 4
_E4_MEMORY_MAX_RELATIONSHIPS = 6
_RECAP_KEY = re.compile(r"^ep[0-9]{2}$")


def _e4_memory_block(memory) -> str:
    """The season's accumulated memory (spec 2.6), for the whole-script
    check -- unlike E1/E3's :func:`context.memory_section`, E4 is not asked
    from inside one particular episode's ep-gated view: it checks an
    already-written episode against what the season remembers so far, most
    recent first, capped so a long-running season never grows this section
    without bound (the caps above)."""
    series_memory = (memory or {}).get("series_memory") or {}
    recaps = series_memory.get("recaps") or {}
    open_hooks = series_memory.get("open_hooks") or []
    pairs = context.relationship_pairs(series_memory.get("relationship_state"))

    if not (recaps or open_hooks or pairs):
        return "Series memory: none recorded yet."

    lines = ["Series memory:"]
    # Keyed "ep01", "ep02", ... (spec 2.6); a key of any other shape is not
    # an episode's recap and is left out.
    numbered = {int(key[2:]): key for key in recaps if _RECAP_KEY.fullmatch(str(key))}
    recent_eps = sorted(numbered, reverse=True)[:_E4_MEMORY_MAX_RECAPS]
    for ep in sorted(recent_eps):
        lines.append(f"- Episode {ep} recap: {recaps[numbered[ep]]}")
    if open_hooks:
        lines.append("- Open hooks: " + "; ".join(open_hooks[:_E4_MEMORY_MAX_HOOKS]))
    if pairs:
        lines.append("- Relationships: " + "; ".join(
            f"{a}/{b}: {text}" for a, b, text in pairs[:_E4_MEMORY_MAX_RELATIONSHIPS]))
    return "\n".join(lines)


def script_digest(script, entities) -> str:
    """The whole script rendered as plain text for E4 (spec 4.2, row E4):
    one block per scene -- its id, function, place name and time variant,
    its characters' names, its summary, then every line as ``Name: text``
    -- built the same way here and by the step runner that calls
    :func:`build_e4`, so the reply's ``scene_id``s and the human reviewing
    "approve anyway" read the exact same script.

    *entities* is ``{"places": {place_id: name}, "cast": {char_id: name}}``;
    a ``"narrator"`` speaker is rendered as ``"Narrator"`` without a lookup.
    """
    places = entities.get("places", {})
    cast = entities.get("cast", {})

    def name_of(char_id):
        return "Narrator" if char_id == "narrator" else cast.get(char_id, char_id)

    blocks = []
    for scene in script["scenes"]:
        place_name = places.get(scene["place_id"], scene["place_id"])
        chars = ", ".join(name_of(cid) for cid in scene["characters"]) or "none"
        header = (
            f"Scene {scene['scene_id']} ({scene['function']}) -- {place_name}, {scene['time_variant']} -- "
            f"characters: {chars}\n{scene['summary']}"
        )
        line_texts = [f"{name_of(line['speaker'])}: {line['text']}" for line in scene["lines"]]
        blocks.append(header if not line_texts else header + "\n" + "\n".join(line_texts))
    return "\n\n".join(blocks)


def e4_schema() -> dict:
    """The E4 output schema (spec 4.2, 4.3, row E4): a pass/fail plus up to
    6 issues. ``scene_id`` is the one field in this whole phase that reads
    an id *back* from the model instead of only ever handing one to it --
    it is left an unconstrained nullable string here (a strict enum would
    need every scene id known at schema-build time, which the ``scene_id``
    the model names as broken is exactly one of); :func:`validate_e3`'s
    sibling here, :func:`validate_e4`, checks it is actually one of the
    script's own ids.
    """
    issue = _llm_obj({
        "scene_id": {"type": ["string", "null"], "description": "one of the script's own scene ids, or null"},
        "kind": {"type": "string", "enum": ["continuity", "character", "place", "series_memory", "other"]},
        "fix": {"type": "string", "description": "at most 40 words, in the story language"},
    })
    return _llm_obj({
        "passed": {"type": "boolean"},
        "issues": {"type": "array", "description": "at most 6 issues", "items": issue},
    })


def build_e4(pack, *, script_digest, cast, places, memory):
    """Analytic consistency check over the whole script (spec 2.7, 4.2, row
    E4): continuity against the bible and the series memory, characters
    speaking out of character (personality/speech_style), a scene's action
    contradicting its own place.

    *script_digest* is :func:`script_digest`'s own rendering of the script
    being checked. *cast* is the story's full cast, shaped like E2/E3's own
    *cast* (personality, never a visual descriptor); *places* is
    ``[{"place_id", "name"}, ...]``. *memory* is the season document.
    """
    user = _data_block(pack, ("bible",))
    if cast:
        user += "Cast (speech style, for the character check):\n" + _e4_cast_block(cast) + "\n\n"
    if places:
        user += "Places (for the place check):\n" + _id_name_block(places, "place_id") + "\n\n"
    user += _e4_memory_block(memory) + "\n\n"
    user += f"{script_digest}\n\n"
    user += _E4_ASK
    return _e4_system(pack), user, e4_schema()


def validate_e4(reply, *, scene_ids) -> list:
    """Post-validation for an E4 reply (spec 4.3): at most 6 issues, each
    ``kind`` from the closed list, each ``scene_id`` either null or one of
    the script's own scene ids, each ``fix`` capped, and ``passed`` true
    exactly when there is no issue."""
    schema = e4_schema()
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    scene_id_set = set(scene_ids)
    issues = reply["issues"]
    if len(issues) > 6:
        errors.append(f"$.issues: {len(issues)} issue(s), expected at most 6")
    for i, issue in enumerate(issues):
        path = f"$.issues[{i}]"
        scene_id = issue["scene_id"]
        if scene_id is not None and scene_id not in scene_id_set:
            errors.append(f"{path}.scene_id: {scene_id!r} is not one of the script's scene ids")
        _text_errors(errors, f"{path}.fix", issue["fix"], max_words=40)

    if reply["passed"] != (len(issues) == 0):
        errors.append(f"$.passed: {reply['passed']!r} does not agree with {len(issues)} issue(s)")

    return errors


# ------------------------------------------------------------------------- T1/T1r

_T1_ASK_TEMPLATE = (
    "Break this scene into shots.\n\n"
    "Give 'shots': {lo} to {hi} entries, each with:\n"
    "- framing: one of {framings}\n"
    "- camera_motion: one of {camera_motions}\n"
    "- modifiers: zero or more of {modifiers} (an empty array if none apply)\n"
    "- action (English): one sentence, at most 30 words, describing what is visible; refer to people, the "
    "place and objects only by their tags ({tag_examples}), never by name\n"
    "- subjects: every tag visible in this shot, from {tags}\n"
    "- lines: which of this scene's numbered lines (1-{n_lines}) are spoken during this shot, in order; each "
    "line belongs to at most one shot\n\n"
    "Vary the framing: no two consecutive shots use the same framing, including against the previous shots "
    "below.{insert_prop_note}\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)

_T1R_ASK_TEMPLATE = (
    "Replace shot {index}, keeping it covering exactly the same lines ({lines}).\n\n"
    "Give 'shot' with:\n"
    "- framing: one of {framings}\n"
    "- camera_motion: one of {camera_motions}\n"
    "- modifiers: zero or more of {modifiers} (an empty array if none apply)\n"
    "- action (English): one sentence, at most 30 words, describing what is visible; refer to people, the "
    "place and objects only by their tags ({tag_examples}), never by name\n"
    "- subjects: every tag visible in this shot, from {tags}\n"
    "- lines: the same line numbers as the shot it replaces\n\n"
    "Never use real people, brands, studio names or copyrighted characters."
)

_TAG_PATTERN = re.compile(r"[@%#][a-z0-9_:]+")


def _framings_with_meanings() -> str:
    return ", ".join(f"{f} ({prompting.FRAMING_PHRASES[f]})" for f in schemas.FRAMINGS)


def _numbered_lines_block(lines, names) -> str:
    rendered = []
    for i, line in enumerate(lines, start=1):
        speaker = "Narrator" if line["speaker"] == "narrator" else names.get(line["speaker"], line["speaker"])
        rendered.append(f"{i}. {speaker}: {line['text']} ({line['emotion']})")
    return "\n".join(rendered)


def _t1_tags(characters, place, props, time_variant):
    """``(tag_by_char, place_tag, prop_tags, tags_allowed)`` -- the exact
    tag vocabulary this call's ``subjects``/``action`` may use (spec 2.8):
    ``@char_x`` per present character, one ``#place_x:variant`` for the
    scene's own place and variant, ``%prop_x`` per present prop."""
    tag_by_char = {c["char_id"]: f"@{c['char_id']}" for c in characters}
    place_tag = f"#{place['place_id']}:{time_variant}"
    prop_tags = [f"%{p['prop_id']}" for p in props]
    tags_allowed = list(tag_by_char.values()) + [place_tag] + prop_tags
    return tag_by_char, place_tag, prop_tags, tags_allowed


def _shot_schema(modifiers_allowed, tags_allowed) -> dict:
    return _llm_obj({
        "framing": {"type": "string", "enum": list(schemas.FRAMINGS)},
        "camera_motion": {"type": "string", "enum": list(schemas.CAMERA_MOTIONS)},
        "modifiers": {"type": "array", "items": {"type": "string", "enum": list(modifiers_allowed)}},
        "action": {"type": "string", "description": "English, one sentence, at most 30 words, tags only"},
        "subjects": {
            "type": "array", "description": "the tags visible in this shot",
            "items": {"type": "string", "enum": list(tags_allowed)} if tags_allowed else {"type": "string"},
        },
        "lines": {"type": "array", "description": "this scene's line numbers spoken in this shot, in order",
                  "items": {"type": "integer"}},
    })


def t1_schema(shots_per_scene, modifiers_allowed, tags_allowed) -> dict:
    """The T1 output schema (spec 2.8, 4.2, row T1): one scene's shots."""
    lo, hi = shots_per_scene
    return _llm_obj({
        "shots": {"type": "array", "description": f"{lo}-{hi} shots", "items": _shot_schema(modifiers_allowed, tags_allowed)},
    })


def build_t1(pack, *, scene, lines, characters, place, props, previous_shots, shots_per_scene, camera,
             modifiers_allowed, hook_style):
    """One scene's shot list (spec 2.8, 4.2, row T1): 2-4 shots, each a
    framing, a camera motion, optional modifiers, an English action
    sentence that names only tags, the tags visible, and which of the
    scene's numbered lines it covers.

    *characters*/*props* are only this scene's own present entities, each
    at least ``{"char_id"/"prop_id", "descriptor", "name"}`` (an English
    descriptor, for T1's own visual reasoning -- unlike E2, T1 never sees
    personality). *place* is ``{"place_id", "layout_notes"}``; its variant
    comes from ``scene["time_variant"]``. *lines* is the scene's own
    already-written lines in order, each ``{"speaker", "text", "emotion"}``
    (speaker a cast id or ``"narrator"``); they are shown 1-indexed, the
    same numbering the shot's own ``lines`` field and :func:`validate_t1`
    use. *previous_shots* is the last (up to two) shots of the episode so
    far, each ``{"framing", "camera_motion"}``, for the no-repeat-framing
    rule. *camera* is the style's own camera paragraph -- T1 is the only
    prompt in this whole catalogue that sees it (it guides shot planning,
    never an image prompt directly).
    """
    tag_by_char, place_tag, prop_tags, tags_allowed = _t1_tags(characters, place, props, scene["time_variant"])
    names = {c["char_id"]: c["name"] for c in characters}

    user = f"Camera: {camera}\n\n"
    user += _scene_stub_line(scene) + "\n\n"
    if characters:
        user += "Characters (tag -- descriptor -- name):\n" + "\n".join(
            f"- {tag_by_char[c['char_id']]} — {c['descriptor']} — {c['name']}" for c in characters
        ) + "\n\n"
    user += f"Place: {place_tag} — {place['layout_notes']}\n\n"
    if props:
        user += "Props (tag -- descriptor):\n" + "\n".join(
            f"- {t} — {p['descriptor']}" for t, p in zip(prop_tags, props)
        ) + "\n\n"
    user += "Numbered lines:\n" + (_numbered_lines_block(lines, names) or "none") + "\n\n"
    if previous_shots:
        user += "Previous shots:\n" + "\n".join(
            f"- {s['framing']} / {s['camera_motion']}" for s in previous_shots
        ) + "\n\n"

    insert_prop_note = ""
    if scene["function"] == "hook" and hook_style == "insert_prop":
        insert_prop_note = " This is the hook scene: exactly one shot must use framing insert_prop."

    lo, hi = shots_per_scene
    user += _T1_ASK_TEMPLATE.format(
        lo=lo, hi=hi,
        framings=_framings_with_meanings(),
        camera_motions=", ".join(schemas.CAMERA_MOTIONS),
        modifiers=", ".join(modifiers_allowed) if modifiers_allowed else "none available for this story",
        tag_examples=f"{next(iter(tag_by_char.values()), '@char_x')}, {prop_tags[0] if prop_tags else '%prop_x'}",
        tags=", ".join(tags_allowed),
        n_lines=len(lines),
        insert_prop_note=insert_prop_note,
    )
    return _system(pack), user, t1_schema((lo, hi), modifiers_allowed, tags_allowed)


def _t1_shot_errors(errors, path, shot, *, names, previous_framing) -> None:
    """The per-shot checks :func:`validate_t1` and :func:`validate_t1r`
    share: the action's word cap, its tags all listed in ``subjects``, no
    character name leaking into it, and no repeat of *previous_framing*."""
    _text_errors(errors, f"{path}.action", shot["action"], max_words=30)

    subjects = set(shot["subjects"])
    for tag in _TAG_PATTERN.findall(shot["action"]):
        if tag not in subjects:
            errors.append(f"{path}.action: tag {tag!r} is used but not listed in subjects")

    lowered = shot["action"].lower()
    for name in names.values():
        if re.search(rf"\b{re.escape(name.lower())}\b", lowered):
            errors.append(f"{path}.action: names the character {name!r} instead of using a tag")

    if previous_framing is not None and shot["framing"] == previous_framing:
        errors.append(f"{path}.framing: {shot['framing']!r} repeats the previous shot's framing")


def validate_t1(reply, *, scene, shots_per_scene, modifiers_allowed, tags_allowed, n_lines, names) -> list:
    """Post-validation for a T1 reply (spec 2.8, 6.2-6.3): shot count,
    every ``@``/``%``/``#`` tag used in ``action`` also listed in
    ``subjects``, no character name inside ``action`` (case-insensitive,
    whole word), line numbers valid, each used at most once, ascending
    across shots, and no two consecutive shots sharing a framing."""
    lo, hi = shots_per_scene
    schema = t1_schema((lo, hi), modifiers_allowed, tags_allowed)
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    shots = reply["shots"]
    if not (lo <= len(shots) <= hi):
        errors.append(f"$.shots: {len(shots)} shot(s), expected {lo}-{hi}")

    used_lines = []
    previous_framing = None
    for i, shot in enumerate(shots):
        path = f"$.shots[{i}]"
        _t1_shot_errors(errors, path, shot, names=names, previous_framing=previous_framing)
        previous_framing = shot["framing"]

        for line_no in shot["lines"]:
            if not (1 <= line_no <= n_lines):
                errors.append(f"{path}.lines: {line_no} is not a valid line number (1-{n_lines})")
            elif line_no in used_lines:
                errors.append(f"{path}.lines: line {line_no} is used in more than one shot")
            used_lines.append(line_no)

    if used_lines != sorted(used_lines):
        errors.append(f"$.shots: line numbers {used_lines} do not ascend across shots")

    return errors


def t1r_schema(modifiers_allowed, tags_allowed) -> dict:
    """The T1r output schema (spec 2.8, 4.2, row T1r): one replacement shot."""
    return _llm_obj({"shot": _shot_schema(modifiers_allowed, tags_allowed)})


def build_t1r(pack, *, scene, shots, index, note, lines, characters, place, props, shots_per_scene, camera,
              modifiers_allowed, hook_style):
    """Re-plan one shot of an already-planned scene (spec 2.8, row T1r),
    keeping every other shot: the same context as :func:`build_t1`
    (*lines*/*characters*/*place*/*props*/*shots_per_scene*/*camera*/
    *modifiers_allowed*/*hook_style*), scoped to the single shot at *index*
    (0-based) of *shots* -- the scene's own shot list so far, each at least
    ``{"framing", "camera_motion", "lines"}`` -- which must still cover
    exactly the same line numbers it already does. *note* is an optional
    free-text steer, the same "regenerate with a note" shape as
    ``build_b1``'s ``regenerate``.
    """
    old_shot = shots[index]
    tag_by_char, place_tag, prop_tags, tags_allowed = _t1_tags(characters, place, props, scene["time_variant"])
    names = {c["char_id"]: c["name"] for c in characters}

    user = f"Camera: {camera}\n\n"
    user += _scene_stub_line(scene) + "\n\n"
    if characters:
        user += "Characters (tag -- descriptor -- name):\n" + "\n".join(
            f"- {tag_by_char[c['char_id']]} — {c['descriptor']} — {c['name']}" for c in characters
        ) + "\n\n"
    user += f"Place: {place_tag} — {place['layout_notes']}\n\n"
    if props:
        user += "Props (tag -- descriptor):\n" + "\n".join(
            f"- {t} — {p['descriptor']}" for t, p in zip(prop_tags, props)
        ) + "\n\n"
    user += "Numbered lines:\n" + (_numbered_lines_block(lines, names) or "none") + "\n\n"
    user += "Shots in this scene so far:\n" + "\n".join(
        f"- shot {i + 1}{' <- replace this one' if i == index else ''}: {s['framing']} / {s['camera_motion']}, "
        f"lines {s['lines'] or 'none'}"
        for i, s in enumerate(shots)
    ) + "\n\n"
    if note:
        user += f"Follow the author's note: {note}\n\n"

    user += _T1R_ASK_TEMPLATE.format(
        index=index + 1,
        lines=old_shot["lines"] or "none",
        framings=_framings_with_meanings(),
        camera_motions=", ".join(schemas.CAMERA_MOTIONS),
        modifiers=", ".join(modifiers_allowed) if modifiers_allowed else "none available for this story",
        tag_examples=f"{next(iter(tag_by_char.values()), '@char_x')}, {prop_tags[0] if prop_tags else '%prop_x'}",
        tags=", ".join(tags_allowed),
    )
    return _system(pack), user, t1r_schema(modifiers_allowed, tags_allowed)


def validate_t1r(reply, *, scene, shots, index, modifiers_allowed, tags_allowed, n_lines, names) -> list:
    """Post-validation for a T1r reply (spec 2.8, row T1r): the same
    per-shot checks as :func:`validate_t1`, plus the one rule unique to a
    replacement -- the new shot must cover exactly the same line numbers as
    the shot at *index* it replaces (a re-plan keeps every other shot, so
    the scene's line coverage cannot shift under it) -- and its framing
    must not repeat either of its new neighbours' (the shots on either side
    of *index* in *shots*, themselves unaffected by the replacement).
    """
    schema = t1r_schema(modifiers_allowed, tags_allowed)
    errors = schemas.validate(reply, schema)
    if errors:
        return errors

    errors = []
    shot = reply["shot"]
    previous_framing = shots[index - 1]["framing"] if index > 0 else None
    _t1_shot_errors(errors, "$.shot", shot, names=names, previous_framing=previous_framing)

    for line_no in shot["lines"]:
        if not (1 <= line_no <= n_lines):
            errors.append(f"$.shot.lines: {line_no} is not a valid line number (1-{n_lines})")

    old_lines = shots[index]["lines"]
    if shot["lines"] != old_lines:
        errors.append(f"$.shot.lines: {shot['lines']} does not cover the same lines as the replaced shot {old_lines}")

    if index < len(shots) - 1 and shot["framing"] == shots[index + 1]["framing"]:
        errors.append(f"$.shot.framing: {shot['framing']!r} repeats the next shot's framing")

    return errors
