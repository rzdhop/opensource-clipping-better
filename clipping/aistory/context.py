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

# Phase 2 (spec 4.1): the existing cast / places, rendered as short lines for
# continuity and visual distinctness (K1's relationships and "don't repeat
# this look", P0/R1's owners, S1/S2's characters). Capped the same way
# ``avoid`` is: a cut is never silent, it is named in ``Pack.trimmed``.
_CAST_MAX_MEMBERS = 12
_PLACES_MAX_ITEMS = 8
_ENTITY_DESCRIPTOR_WORDS = 12


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


def entity_lines(entities) -> str:
    """Render already-written cast/place entries as short lines (spec 4.1):
    name, role (when present), one-line (when present), a short (<=
    ``_ENTITY_DESCRIPTOR_WORDS``-word) clip of the descriptor once one is
    written (so a new entry reads as visually distinct), and signature
    items when present (P0 sources props from these). Each entity is a
    plain dict; only the keys it actually carries are rendered, so the same
    renderer serves K1's full cast entries and P0's name-plus-signature-only
    ones alike.
    """
    lines = []
    for entity in entities:
        line = f"- {entity['name']}"
        role = entity.get("role")
        if role:
            line += f" ({role})"
        one_line = entity.get("one_line")
        if one_line:
            line += f": {one_line}"
        descriptor = entity.get("descriptor")
        if descriptor:
            short, _ = trim_words(descriptor, _ENTITY_DESCRIPTOR_WORDS)
            line += f" — looks: {short}"
        signature_items = entity.get("signature_items")
        if signature_items:
            line += f" — signature: {', '.join(signature_items)}"
        lines.append(line)
    return "\n".join(lines)


def cast_block(cast):
    """``(text, was_cut)`` for the existing cast, capped at
    ``_CAST_MAX_MEMBERS`` members."""
    members = list(cast)
    cut = len(members) > _CAST_MAX_MEMBERS
    return entity_lines(members[:_CAST_MAX_MEMBERS]), cut


def places_block(places):
    """``(text, was_cut)`` for the existing places, capped at
    ``_PLACES_MAX_ITEMS`` entries."""
    items = list(places)
    cut = len(items) > _PLACES_MAX_ITEMS
    return entity_lines(items[:_PLACES_MAX_ITEMS]), cut


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
    # Phase 2 (spec 4.1): the style lock's character design rule (from
    # ``template``, alongside ``style`` -- K1 needs the rule itself, not
    # just the one-line style summary, so the look fits the chosen style).
    character_design_rules: str | None = None
    # Phase 2: existing cast / places, short lines (K1/P0/R1/S1/S2).
    cast: str | None = None
    places: str | None = None
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
    cast=None,
    places=None,
) -> Pack:
    """Assemble a ``Pack`` for one prompt call. Nothing here is a silent
    fallback: every section that had to be cut to fit is named in
    ``Pack.trimmed`` (spec 0: "No silent fallback, no silent shrinking")."""
    trimmed: list = []

    style = style_line(template) if template else None
    concept_text = concept_block(concept) if concept else None
    character_design_rules = template["character_design_rules"] if template else None

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

    cast_text = None
    if cast:
        cast_text, cut = cast_block(cast)
        if cut:
            trimmed.append("cast")

    places_text = None
    if places:
        places_text, cut = places_block(places)
        if cut:
            trimmed.append("places")

    return Pack(
        language_name=LANGUAGE_NAMES.get(language, language),
        style=style,
        concept=concept_text,
        bible=bible_text,
        world=world_text,
        seed=seed,
        note=note_text,
        avoid=avoid,
        character_design_rules=character_design_rules,
        cast=cast_text,
        places=places_text,
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
