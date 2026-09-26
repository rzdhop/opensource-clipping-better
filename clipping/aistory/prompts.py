"""The story-writing prompt catalogue: concepts and bible (spec 4, 4.1, 4.2).

Same pattern as ``clipping/analysis/prompts.py`` (DEC-062, DEC-064): English
instructions, data before the ask, one pure ``build_<id>(pack, ...) -> (system,
user, schema)`` per prompt, no network and nothing random, so every one of
them is golden-string tested. Every builder is handed a ``context.Pack``
(built once per call by the step that owns the story) rather than a raw
``story.json``/concept/template, which is what keeps this module free of any
dependency on ``store.py`` or ``templates.py``.

Only C1 (concepts) and B1/B2/B3 (the story bible) are here -- phase 1's
scope. K1/P1/R1/S1/S2/E1-E4/T1/M1/S3/F1/N1/V1/V2 (spec 4.2) are later phases,
built the same way against the same ``Pack``.

Stdlib only (DEC-012); the one import outside this package is
``clipping.analysis.analyzer`` for the two shared temperature constants
(reused rather than redefined, per the task).
"""

from __future__ import annotations

from clipping.analysis.analyzer import ANALYTIC_TEMPERATURE, WRITING_TEMPERATURE

from . import schemas

# Bumped whenever the wording of a prompt below changes in a way that could
# change an answer -- same convention as clipping.analysis.prompts.PROMPT_VERSION.
PROMPT_VERSION = "s1"

# Concepts are the one place the model is asked to be genuinely inventive;
# everything else in the bible is writing *from* a chosen concept, which
# wants less randomness so re-rolls stay recognisably the same story.
IDEATION_TEMPERATURE = 0.9

MAX_TOKENS = {"C1": 500, "B1": 250, "B2": 250, "B3": 200}
TEMPERATURE = {
    "C1": IDEATION_TEMPERATURE,
    "B1": WRITING_TEMPERATURE,
    "B2": WRITING_TEMPERATURE,
    "B3": WRITING_TEMPERATURE,
}
SCHEMA_NAMES = {"C1": "story_concepts", "B1": "bible_core", "B2": "bible_world", "B3": "bible_values"}

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

SYSTEM_TEMPLATE = (
    "You are the head writer of a serialized vertical-video fiction series "
    "for TikTok, YouTube Shorts and Instagram Reels. Each episode lasts "
    "about 60 seconds and ends on a cliffhanger, so every idea must pay off "
    "in seconds and make people come back. Reply with JSON only, matching "
    "the schema. Never output durations, timestamps or file paths. Never "
    "use real people, brands, studio names or copyrighted characters. Write "
    "all user-facing text in {language_name}."
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
        else:
            parts.append(value)
    block = "\n\n".join(parts)
    return f"{block}\n\n" if block else ""


# ------------------------------------------------------------------- C1

def build_c1(pack, *, style_ids, batch, of):
    """2 original concepts (spec 4.2, row C1)."""
    style_ids = list(style_ids)
    data_block = _data_block(pack, ("style", "seed", "avoid"))
    styles_list = ", ".join(style_ids)

    user = (
        f"{data_block}"
        "Invent exactly 2 original concepts for a new serialized "
        f"vertical-video fiction series (batch {batch} of {of}).\n\n"
        "For each concept give:\n"
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
        "The two concepts must differ from each other in world, cast and "
        "tone. Never use real people, brands, studio names or copyrighted "
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
