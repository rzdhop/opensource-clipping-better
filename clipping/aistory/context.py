"""The context pack every story-writing prompt is built from (spec 4.1).

A prompt never reaches straight into a ``story.json`` or a concept file: it is
handed a small, pre-rendered ``Pack`` instead, so a builder in ``prompts.py``
stays a pure function of plain strings and never has to know the shape of the
documents in ``store.py``/``templates.py``. Every section is rendered once
here, token-budgeted (spec 4.1: "~1,200 input tokens so the free tiers' TPM
survive"), and nothing is ever shortened silently: a cut section is *named* in
``Pack.trimmed`` so the caller can print it, never dropped without a trace.

Stdlib only (DEC-012); the one cross-package import is
``clipping.providers.pacing.estimate_tokens``, which is itself stdlib-only
(the ``providers`` package imports no SDK at module scope).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clipping.providers.pacing import estimate_tokens

# Spec 4.1: "Token-budget the pack (<= ~1,200 input tokens)".
PACK_TOKEN_BUDGET = 1200

LANGUAGE_NAMES = {"fr": "French", "en": "English"}

_SEED_WORD_LIMIT = 120
_NOTE_WORD_LIMIT = 60
_BIBLE_WORD_LIMIT = 120
_AVOID_TITLE_LIMIT = 20


def trim_words(text, limit):
    """Cut *text* to at most *limit* words, appending "…" when it was cut.

    Returns ``(text, was_cut)``. Splitting on whitespace and rejoining with a
    single space is intentional: the result is prompt material, not a
    byte-for-byte quote, so normalising internal whitespace is harmless and
    keeps the word count exact.
    """
    words = text.split()
    if len(words) <= limit:
        return text, False
    return " ".join(words[:limit]) + "…", True


def style_line(template) -> str:
    """One line describing the visual style, from a style template or a
    frozen ``style_lock`` (spec 2.2) -- both carry ``palette.palette_line``
    and ``audio.voice_direction``; only the name field's key differs
    (``name`` on a template, ``template_name`` on a lock).

    The style's own name is always given in English (``name["en"]`` /
    ``template_name["en"]``): it is a proper noun for the pipeline, not
    user-facing prose, so it does not follow the story's language.
    """
    name = template.get("name", template.get("template_name"))
    if name is None:
        raise KeyError("template has neither 'name' nor 'template_name'")
    palette_line = template["palette"]["palette_line"]
    voice_direction = template["audio"]["voice_direction"]
    return f"Visual style: {name['en']} — {palette_line}. Performance: {voice_direction}."


def concept_block(concept) -> str:
    """Render a **localized** concept (a library concept after
    ``templates.localize_concept``, or a generated C1 card) as labelled
    lines (spec 4.1). ``main_line`` is a library-only field (a C1 card has
    none), so it is included only when present.
    """
    lines = [
        f"Title: {concept['title']}",
        f"Logline: {concept['logline']}",
        f"World: {concept['world']}",
    ]
    main_line = concept.get("main_line")
    if main_line:
        lines.append(f"Main line: {main_line}")
    lines.append("Cast:")
    for member in concept["cast_sketch"]:
        lines.append(f"- {member['name']} ({member['role']}): {member['one_line']}")
    lines.append(f"Hook formula: {concept['hook_formula']}")
    lines.append(f"Value: {concept['value']}")
    lines.append(f"Retention mechanics: {concept['retention_mechanics']}")
    return "\n".join(lines)


def _bible_text(story) -> str:
    parts = []
    logline = story.get("logline")
    if logline:
        parts.append(logline)
    premise = story.get("premise")
    if premise:
        parts.append(premise)
    tone = story.get("tone")
    if tone:
        parts.append(f"Tone: {tone}.")
    return " ".join(parts)


def bible_summary(story) -> str:
    """<= 120 words from ``logline`` + ``premise`` + ``tone`` (spec 4.1),
    whichever of the three are present."""
    text, _ = trim_words(_bible_text(story), _BIBLE_WORD_LIMIT)
    return text


def _world_text(story) -> str:
    world = story.get("world")
    if not world:
        return ""
    lines = []
    setting_summary = world.get("setting_summary")
    if setting_summary:
        lines.append(f"Setting: {setting_summary}")
    rules = world.get("rules")
    if rules:
        lines.append("Rules: " + "; ".join(rules))
    time_period = world.get("time_period")
    if time_period:
        lines.append(f"Time period: {time_period}")
    motifs = world.get("recurring_motifs")
    if motifs:
        lines.append("Recurring motifs: " + ", ".join(motifs))
    return "\n".join(lines)


@dataclass
class Pack:
    """The rendered sections a prompt builder draws from. Every section is
    ``None`` (or empty) when the caller had nothing to put there; a builder
    checks before using one, never assumes it is set."""

    language_name: str
    style: str | None = None
    concept: str | None = None
    bible: str | None = None
    world: str | None = None
    seed: str | None = None
    note: str | None = None
    avoid: str | None = None
    trimmed: list = field(default_factory=list)


def build_pack(
    *,
    language,
    story=None,
    concept=None,
    template=None,
    seed_text=None,
    note=None,
    avoid_titles=None,
) -> Pack:
    """Assemble a ``Pack`` for one prompt call. Nothing here is a silent
    fallback: every section that had to be cut to fit is named in
    ``Pack.trimmed`` (spec 0: "No silent fallback, no silent shrinking")."""
    trimmed: list = []

    style = style_line(template) if template else None
    concept_text = concept_block(concept) if concept else None

    bible_text = None
    world_text = None
    if story is not None:
        raw_bible = _bible_text(story)
        if raw_bible:
            bible_text, cut = trim_words(raw_bible, _BIBLE_WORD_LIMIT)
            if cut:
                trimmed.append("bible")
        world_text = _world_text(story) or None

    seed = None
    if seed_text:
        seed, cut = trim_words(seed_text, _SEED_WORD_LIMIT)
        if cut:
            trimmed.append("seed")

    note_text = None
    if note:
        note_text, cut = trim_words(note, _NOTE_WORD_LIMIT)
        if cut:
            trimmed.append("note")

    avoid = None
    if avoid_titles:
        titles = list(avoid_titles)
        if len(titles) > _AVOID_TITLE_LIMIT:
            titles = titles[:_AVOID_TITLE_LIMIT]
            trimmed.append("avoid")
        avoid = ", ".join(titles)

    return Pack(
        language_name=LANGUAGE_NAMES.get(language, language),
        style=style,
        concept=concept_text,
        bible=bible_text,
        world=world_text,
        seed=seed,
        note=note_text,
        avoid=avoid,
        trimmed=trimmed,
    )


def check_budget(system, user) -> int:
    """The estimated input-token count for one call; raises ``ValueError``
    naming the count when it is over ``PACK_TOKEN_BUDGET`` -- a bug in a
    builder or an oversized pack, never something to trim silently here."""
    tokens = estimate_tokens(system, user)
    if tokens > PACK_TOKEN_BUDGET:
        raise ValueError(
            f"prompt is {tokens} estimated tokens, over the {PACK_TOKEN_BUDGET}-token budget"
        )
    return tokens
