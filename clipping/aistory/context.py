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
(the ``providers`` package imports no SDK at module scope). The phase-7
slices (stage 5c, at the end) read ``shots`` and ``series_memory`` of this
package, imported where used: neither imports this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from clipping.providers.pacing import estimate_tokens

# Spec 4.1: "Token-budget the pack (<= ~1,200 input tokens)".
PACK_TOKEN_BUDGET = 1200

LANGUAGE_NAMES = {"fr": "French", "en": "English"}

_SEED_WORD_LIMIT = 120
_NOTE_WORD_LIMIT = 60
_BIBLE_WORD_LIMIT = 120
# C1's "do not repeat" titles: the library's (14 since plan 20 stage 2) and
# the 9 cards one "Generate 10 more" run writes before its last call all fit
# (23), so a first run never cuts; was 20 with the ten-concept library.
_AVOID_TITLE_LIMIT = 24

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


def check_budget(system, user, *, budget=PACK_TOKEN_BUDGET) -> int:
    """The estimated input-token count for one call; raises ``ValueError``
    naming the count when it is over *budget* (``PACK_TOKEN_BUDGET`` unless
    the caller names a wider one, e.g. E4's ``prompts.INPUT_BUDGET``) -- a bug
    in a builder or an oversized pack, never something to trim silently here.
    """
    tokens = estimate_tokens(system, user)
    if tokens > budget:
        raise ValueError(
            f"prompt is {tokens} estimated tokens, over the {budget}-token budget"
        )
    return tokens


# ============================================================ phase 3 (spec 4.2)
#
# The two sections E1-E4/T1/T1r need beyond what ``build_pack`` already
# renders (spec 2.6, 4.1): the season's cross-episode memory, and a compact
# reading of the episode's own scene list. Neither goes through ``Pack`` --
# each phase-3 builder takes its raw source (``season.json``, the episode's
# scene list) as its own keyword argument and calls these directly, the same
# way ``prompts._cast_section``/``_places_section`` call ``cast_block``/
# ``places_block`` above rather than routing through a ``Pack`` field.

_MEMORY_WORD_LIMIT = 150
_MEMORY_NONE_YET = "none yet"
# series_memory.relationship_state (spec 2.6): a flat object, one entry per
# pair of characters, keyed "<char_a>|<char_b>", valued with a short text.
_RELATIONSHIP_KEY = re.compile(r"^(char_[a-z0-9_]{1,40})\|(char_[a-z0-9_]{1,40})$")


def relationship_pairs(relationship_state) -> list:
    """``[(char_a, char_b, text), ...]`` of a spec-2.6 ``relationship_state``
    (``{"char_kiwilo|char_mangella": "publicly enemies, secretly allies"}``),
    in its own order. An entry of any other shape -- a key that is not two
    character ids joined by ``|``, or a value that is not a non-empty
    string -- is not a relationship and is left out."""
    pairs = []
    for key, text in (relationship_state or {}).items():
        match = _RELATIONSHIP_KEY.fullmatch(key) if isinstance(key, str) else None
        if match is None or not (isinstance(text, str) and text.strip()):
            continue
        pairs.append((match.group(1), match.group(2), text.strip()))
    return pairs


def previous_recap(season, ep):
    """The recap of the episode before *ep* in *season*'s ``series_memory``
    (``recaps["ep01"]`` for episode 2; spec 2.6, the memory step writes it),
    or None: for episode 1, or when none is recorded."""
    if ep < 2:
        return None
    memory = (season or {}).get("series_memory") or {}
    # Keyed "ep01", "ep02", ... (spec 2.6; the memory step writes them).
    return (memory.get("recaps") or {}).get(f"ep{ep - 1:02d}")


def archetype_line(season):
    """The line "- Plot archetypes: <primary> (primary), <secondary>
    (secondary)" for a season whose S1 chose them (plan 20 stage 2), else
    None. English labels, the language the arc functions are named in in
    every prompt; an id the library no longer ships is shown as the id."""
    chosen = (season or {}).get("archetypes")
    if not chosen:
        return None
    from . import templates  # the library; a lazy import keeps this module's load light

    names = []
    for key in ("primary", "secondary"):
        archetype_id = chosen.get(key)
        if not archetype_id:
            continue
        try:
            label = templates.load_archetype(archetype_id)["label"]["en"]
        except KeyError:
            label = archetype_id
        names.append(f"{label} ({key})")
    return "- Plot archetypes: " + ", ".join(names)


def memory_section(season, ep, open_hooks=None):
    """``(text, was_cut)`` for E1/E3's series-memory block (spec 2.6, 4.2).

    Episode 1 opens a season with no history: the literal text
    ``"none yet"``, never cut. From episode 2 on, the previous episode's
    recap (:func:`previous_recap`), the open hooks and the season's current
    relationship state come from *season*'s ``series_memory`` (spec 2.6:
    ``recaps``, ``open_hooks``, ``relationship_state``, filled in by the S3
    step once an episode is approved) -- a season with none yet recorded (a
    fresh story, or ep 2 written before ep 1 was ever approved) says so per
    field rather than omitting it silently. Cut to ``_MEMORY_WORD_LIMIT``
    words like every other pack section, the cut named exactly as
    ``cast``/``places`` are.

    A season whose S1 chose plot archetypes (a v2 story, plan 20 stage 2)
    names them on one line right under the header, read-only
    (:func:`archetype_line`). A season without them renders as before.

    *open_hooks* (phase 5 stage 3) is the list the "Open hooks" line shows:
    the caller's -- the hooks open when episode *ep* starts,
    ``series_memory.open_hooks_before`` -- since the stored ``open_hooks``
    folds later episodes' entries too; an empty list shows none (E1 lists
    its own, enumerated, with the payoff ask). None reads the stored list,
    as every caller did before.
    """
    if ep < 2:
        return _MEMORY_NONE_YET, False

    memory = (season or {}).get("series_memory") or {}
    recap = previous_recap(season, ep)
    if open_hooks is None:
        open_hooks = memory.get("open_hooks") or []
    pairs = relationship_pairs(memory.get("relationship_state"))

    lines = ["Series memory:"]
    archetypes = archetype_line(season)
    if archetypes:
        lines.append(archetypes)
    lines.append(f"- Previous recap: {recap}" if recap else "- Previous recap: none recorded")
    if open_hooks:
        lines.append("- Open hooks: " + "; ".join(open_hooks))
    if pairs:
        lines.append("- Relationships: " + "; ".join(f"{a}/{b}: {text}" for a, b, text in pairs))

    return trim_words("\n".join(lines), _MEMORY_WORD_LIMIT)


def outline_section(scenes, names) -> str:
    """A compact "function: summary" line per scene of the episode being
    written (spec 4.2 E2/E3): what the rest of the episode already does, so
    a scene written on its own (one small artifact per request, DEC-107)
    still reads as part of one story.

    *names* maps a character id to its name; a scene's ``characters`` (ids)
    are shown by name when the id is in *names*, by the raw id otherwise --
    a builder only ever has the names of the characters its own call
    involves, not the whole story's cast, so a character from a scene
    outside that set is shown by id rather than guessed at.
    """
    lines = ["Episode outline:"]
    for scene in scenes:
        who = ", ".join(names.get(cid, cid) for cid in scene.get("characters", []))
        suffix = f" — {who}" if who else ""
        lines.append(f"- {scene['scene_id']} ({scene['function']}): {scene['summary']}{suffix}")
    return "\n".join(lines)


# ============================================================ phase 7 stage 5c (A13)
#
# The context slices a v2 story's writing calls read (E1v2/E2v2/E3v2, T1 v2):
# what the approved knowledge base (``knowledge.json``), the dossiers, the
# looks and the continuity ledger say about the scene being written -- only
# the entities present, never the whole story. Each part is word-capped (a
# cut part ends on "…", never dropped without a trace), so a slice is bounded
# by construction and its prompt's ``INPUT_BUDGET`` row is measured on it
# (DEC-138). Pure: the caller reads the documents (the knowledge base may be
# None -- a regenerate of a story whose base is gone still gets the dossiers).

SLICE_BEAT_WORDS = 35
SLICE_CAST_WORDS = 120
SLICE_CHARACTER_WORDS = 45
SLICE_RELATIONSHIPS_WORDS = 50
SLICE_KNOWS_WORDS = 50
SLICE_STATE_WORDS = 40
SLICE_PLACE_WORDS = 50
# What one character is said to know, at most: the latest facts first.
SLICE_KNOWS_PER_CHARACTER = 2
_SLICE_RECAP_WORDS = 25
_SLICE_HEADER = "Scene context (from the story's knowledge base):"
# The scene slice's words at most: its parts' caps and its header.
SCENE_SLICE_MAX_WORDS = (len(_SLICE_HEADER.split()) + SLICE_BEAT_WORDS + SLICE_CAST_WORDS
                         + SLICE_RELATIONSHIPS_WORDS + SLICE_KNOWS_WORDS + SLICE_STATE_WORDS + SLICE_PLACE_WORDS)

# E1v2's episode slice: the episode's planned beats, who wants what, and
# where things stand before it (the memory block already says what is known).
SLICE_EPISODE_BEATS_WORDS = 240
_EPISODE_HEADER = "Episode plan (from the story's knowledge base):"
EPISODE_SLICE_MAX_WORDS = (len(_EPISODE_HEADER.split()) + SLICE_EPISODE_BEATS_WORDS + SLICE_CAST_WORDS
                           + SLICE_STATE_WORDS)

# The words a secret must share with the beat to be "relevant to it": a word
# of at least this many letters (short function words never match).
_SECRET_WORD_MIN = 5
# A line cut to fit its part keeps at least this many words, else it is left
# out whole (a lone "- Name:…" says nothing).
_CUT_LINE_MIN_WORDS = 4
_WORD = re.compile(r"\w+", re.UNICODE)


def _trim_lines(lines, limit):
    """*lines* kept whole while their words fit *limit*; the first that does
    not is cut (:func:`trim_words`, ending on "…") and the rest dropped.
    Returns the kept lines (a line of a part is one fact: cutting one in the
    middle is marked, never silent)."""
    kept, left = [], limit
    for line in lines:
        words = len(line.split())
        if words <= left:
            kept.append(line)
            left -= words
            continue
        if left >= _CUT_LINE_MIN_WORDS:
            kept.append(trim_words(line, left)[0])
        break
    return kept


def timeline_beats(knowledge, ep) -> list:
    """The beats the knowledge base's timeline plans for episode *ep* ([]
    without one)."""
    for entry in (knowledge or {}).get("timeline") or ():
        if entry.get("ep") == ep:
            return list(entry.get("beats") or ())
    return []


def ledger_before(knowledge, season, ep):
    """``{char_id: state}``: where every character stands when episode *ep*
    starts -- per character, the ledger of the latest series-memory entry
    before *ep* that has one for it (written after each episode, stage 5d),
    else the knowledge base's ``ledger_seed`` (``series_memory.fold_ledger``,
    stage 5d: this is that fold, with the knowledge base read here and
    nothing read -- not even the fold -- for a legacy story). None when
    there is no knowledge base (a legacy story: no ledger at all)."""
    if knowledge is None:
        return None
    from . import series_memory  # the fold; a lazy import keeps this module's load light

    return series_memory.fold_ledger(season, before_ep=ep, knowledge=knowledge)


def _content_words(text) -> set:
    return {word for word in _WORD.findall((text or "").lower()) if len(word) >= _SECRET_WORD_MIN}


def beat_for_scene(beats, scene):
    """``(index, beat)`` of the planned beat *scene* stages, or None: the beat
    sharing the most with it -- its place (2), each character (1), each
    object (1) -- the earliest on a tie; a beat sharing nothing never maps."""
    best, best_score = None, 0
    chars, props = set(scene.get("characters") or ()), set(scene.get("props") or ())
    for index, beat in enumerate(beats):
        score = (2 if beat.get("place_id") and beat.get("place_id") == scene.get("place_id") else 0)
        score += len(chars & set(beat.get("who") or ())) + len(props & set(beat.get("objects") or ()))
        if score > best_score:
            best, best_score = (index, beat), score
    return best


def known_facts(knowledge, ep, before_beat, char_ids) -> dict:
    """``{char_id: [fact, ...]}``: what each of *char_ids* knows when beat
    *before_beat* (an index, or None: the episode's start) of episode *ep*
    begins -- every ``knows_after`` of the beats of the episodes before,
    then of this episode's beats before that one, oldest first."""
    wanted = set(char_ids)
    facts = {cid: [] for cid in char_ids}
    for entry in sorted((knowledge or {}).get("timeline") or (), key=lambda item: item.get("ep", 0)):
        if entry.get("ep", 0) > ep:
            break
        beats = entry.get("beats") or ()
        if entry.get("ep") == ep:
            beats = beats[:before_beat] if before_beat is not None else ()
        for beat in beats:
            for cid, fact in (beat.get("knows_after") or {}).items():
                if cid in wanted:
                    facts[cid].append(fact)
    return facts


def _relevant_secret(secrets, texts):
    """The secret of *secrets* sharing the most content words with *texts*
    (the beat, the scene), or None when none shares one."""
    words = set().union(*(_content_words(text) for text in texts)) if texts else set()
    best, best_overlap = None, 0
    for secret in secrets or ():
        overlap = len(_content_words(secret) & words)
        if overlap > best_overlap:
            best, best_overlap = secret, overlap
    return best


def _names_of(ec, kind, ids) -> list:
    docs = ec.entities.get(kind, {})
    return [docs[eid]["name"] for eid in ids if eid in docs]


def _wardrobe_context(doc, set_id, *, items=True):
    """The wardrobe set *set_id* of a character's look said in words (its
    context, then its items unless *items* is False), or ''."""
    for entry in ((doc.get("look") or {}).get("wardrobe_sets") or ()):
        if entry.get("id") == set_id:
            context_text = entry["context"].strip().rstrip(".")
            return f"{context_text}: {entry['items'].strip().rstrip('.')}" if items else context_text
    return ""


def _state_line(ec, cid, state) -> str:
    doc = ec.entities["characters"][cid]
    parts = []
    location = state.get("location")
    if location and location in ec.entities.get("places", {}):
        parts.append(f"at {ec.entities['places'][location]['name']}")
    wearing = _wardrobe_context(doc, state.get("wardrobe_set"), items=False)
    if wearing:
        parts.append(f"dressed for {wearing}")
    held = _names_of(ec, "props", state.get("possessions") or ())
    if held:
        parts.append("holding " + ", ".join(held))
    if state.get("injuries"):
        parts.append(f"hurt: {state['injuries']}")
    return f"- {doc['name']}: {'; '.join(parts)}" if parts else ""


def _state_lines(ec, char_ids, ledger) -> list:
    if not ledger:
        return []
    lines = [_state_line(ec, cid, ledger[cid]) for cid in char_ids if cid in ledger]
    return [line for line in lines if line]


def _profile_line(doc, share, texts) -> str:
    """One character as a scene reads them: goal, the secret relevant to the
    beat (if any; *texts* None: never one), need, catchphrases -- at most
    *share* words, cut from the end."""
    dossier = doc.get("dossier")
    if not dossier:
        return ""
    parts = [f"goal: {dossier['goal']}"]
    secret = _relevant_secret(dossier.get("secrets"), texts) if texts is not None else None
    if secret:
        parts.append(f"hides: {secret}")
    parts.append(f"needs: {dossier['need']}")
    phrases = (dossier.get("voice") or {}).get("catchphrases") or ()
    if phrases:
        parts.append("says: " + " / ".join(f"“{phrase}”" for phrase in phrases))
    return trim_words(f"- {doc['name']}: " + "; ".join(parts), share)[0]


def _cast_lines(ec, char_ids, texts) -> list:
    characters = ec.entities["characters"]
    present = [characters[cid] for cid in char_ids if cid in characters and characters[cid].get("dossier")]
    if not present:
        return []
    share = min(SLICE_CHARACTER_WORDS, max(1, SLICE_CAST_WORDS // len(present)))
    return [line for line in (_profile_line(doc, share, texts) for doc in present) if line]


def _relationship_lines(ec, char_ids) -> list:
    """The relationship history among *char_ids*, from their dossiers (each
    pair once, the first one's side), with where it stands now from the
    series memory when it says so."""
    from . import series_memory  # the fold; a lazy import keeps this module's load light

    characters = ec.entities["characters"]
    present = [cid for cid in char_ids if cid in characters]
    try:
        state = series_memory.relationship_state_before(ec.season, ec.ep) if ec.ep and ec.ep >= 2 else {}
    except ValueError:
        state = {}
    lines, seen = [], set()
    for cid in present:
        for item in ((characters[cid].get("dossier") or {}).get("relationships") or ()):
            other = item.get("with")
            if other not in present or other == cid:
                continue
            pair = tuple(sorted((cid, other)))
            if pair in seen:
                continue
            seen.add(pair)
            now = state.get(f"{pair[0]}|{pair[1]}") or item["now"]
            lines.append(f"- {characters[cid]['name']} & {characters[other]['name']}: {item['history']}; now: {now}")
    return lines


def _knows_lines(ec, char_ids, facts) -> list:
    lines = []
    recap = previous_recap(ec.season, ec.ep) if ec.ep else None
    if recap:
        lines.append("- Last episode: " + trim_words(recap, _SLICE_RECAP_WORDS)[0])
    characters = ec.entities["characters"]
    for cid in char_ids:
        known = (facts.get(cid) or [])[-SLICE_KNOWS_PER_CHARACTER:]
        if known and cid in characters:
            lines.append(f"- {characters[cid]['name']} knows: " + "; ".join(known))
    return lines


def _place_text(ec, scene) -> str:
    """The scene's place as a wide framing reads it (its layout and the
    variant's light, ``shots.render_place``), or its descriptor and layout
    notes without a look."""
    from . import shots  # the renderers; shots imports nothing of this module

    place = ec.entities.get("places", {}).get(scene.get("place_id"))
    if place is None:
        return ""
    variant = scene["time_variant"]
    if place.get("look") and place.get("descriptor"):
        props = ec.entities.get("props", {})
        here = [props[pid] for pid in place["look"].get("props_here") or () if pid in props]
        # The light, then the layout, the scale and the set dressing; the
        # descriptor (what the set image shows) is left to the image calls.
        text = shots.render_place(place, variant, "wide_establishing", props=here)
        sentences = shots._PLACE_SENTENCE_START.split(text)
        light = [sentence for sentence in sentences if sentence.startswith("Light:")]
        rest = [sentence for sentence in sentences if sentence.startswith(("Layout:", "Scale:", "Set dressing:"))]
        return " ".join(light + rest)
    parts = [(place.get("descriptor") or "").strip().rstrip("."), (place.get("layout_notes") or "").strip().rstrip("."),
             f"Light: {variant.replace('_', ' ')} light"]
    return ". ".join(part for part in parts if part) + "."


def _part(label, lines, limit) -> list:
    """A labelled part (its label counted in *limit*), or [] when empty."""
    lines = [line for line in lines if line]
    if not lines:
        return []
    return _trim_lines([label] + lines, limit)


def slice_for_scene(ec, scene, *, knowledge) -> str:
    """The context block of one scene (A13) for E2v2/E3v2: only the
    characters present (``scene["characters"]``), each part word-capped --

    - the beat's purpose: "ep N, beat k of m: <what>" when the scene maps to
      a beat of the knowledge timeline (:func:`beat_for_scene`), else the
      scene's own function and summary;
    - who is here: each one's goal, need, the secret relevant to this beat
      (one sharing words with it, :func:`_relevant_secret`; none otherwise)
      and catchphrases, from the dossier;
    - the relationship history among those present (dossiers; where it
      stands now from the series memory when recorded);
    - what each knows so far: the previous episode's recap, and the
      timeline's ``knows_after`` up to this episode's previous beats
      (:func:`known_facts`);
    - where things stand: the ledger (:func:`ledger_before`): location,
      wardrobe set, possessions, injuries;
    - the place's layout and light (``shots.render_place``).

    *ec* is the episode's ``EpisodeContext`` (its ``entities``, ``ep`` and
    ``season``); *knowledge* the story's ``knowledge.json`` or None. At most
    :data:`SCENE_SLICE_MAX_WORDS` words; '' when nothing is known."""
    beats = timeline_beats(knowledge, ec.ep)
    mapped = beat_for_scene(beats, scene)
    if mapped is not None:
        index, beat = mapped
        purpose = f"ep {ec.ep}, beat {index + 1} of {len(beats)}: {beat['what']}"
        texts = [beat["what"], scene.get("summary") or ""] + list((beat.get("knows_after") or {}).values())
    else:
        index = None
        purpose = f"ep {ec.ep}, {scene['function']}: {scene.get('summary') or ''}"
        texts = [scene.get("summary") or ""]
    present = [cid for cid in dict.fromkeys(scene.get("characters") or ()) if cid in ec.entities["characters"]]
    facts = known_facts(knowledge, ec.ep, index, present)
    parts = [_trim_lines([f"Beat: {purpose}"], SLICE_BEAT_WORDS)]
    parts.append(_part("Who is here:", _cast_lines(ec, present, texts), SLICE_CAST_WORDS))
    parts.append(_part("Between them:", _relationship_lines(ec, present), SLICE_RELATIONSHIPS_WORDS))
    parts.append(_part("Known so far:", _knows_lines(ec, present, facts), SLICE_KNOWS_WORDS))
    parts.append(_part("Where things stand:",
                       _state_lines(ec, present, ledger_before(knowledge, ec.season, ec.ep)), SLICE_STATE_WORDS))
    place = _place_text(ec, scene)
    if place:
        parts.append([trim_words(f"Place: {place}", SLICE_PLACE_WORDS)[0]])
    lines = [line for part in parts for line in part]
    return "\n".join([_SLICE_HEADER] + lines)


def _beat_line(ec, n, beat) -> str:
    where = _names_of(ec, "places", [beat["place_id"]] if beat.get("place_id") else [])
    who = _names_of(ec, "characters", beat.get("who") or ())
    objects = _names_of(ec, "props", beat.get("objects") or ())
    details = []
    if where:
        details.append(f"at {where[0]}")
    if who:
        details.append(", ".join(who))
    if objects:
        details.append("objects: " + ", ".join(objects))
    return f"{n}. {beat['what']}" + (f" ({'; '.join(details)})" if details else "")


def slice_for_episode(ec, *, knowledge, char_ids) -> str:
    """E1v2's context block (A13): the episode's planned beats from the
    knowledge timeline, in order (where, who, which objects); who wants what
    (goal and need from the dossiers of *char_ids*, the characters E1 may
    use); where each stands before the episode (:func:`ledger_before`). The
    series memory is E1's own block already. At most
    :data:`EPISODE_SLICE_MAX_WORDS` words; '' when nothing is known."""
    beats = timeline_beats(knowledge, ec.ep)
    in_beats = [cid for beat in beats for cid in beat.get("who") or ()]
    cast = [cid for cid in dict.fromkeys(in_beats + list(char_ids)) if cid in char_ids]
    parts = [_part(f"Planned beats of episode {ec.ep}, in order:",
                   [_beat_line(ec, n, beat) for n, beat in enumerate(beats, start=1)], SLICE_EPISODE_BEATS_WORDS)]
    parts.append(_part("Who wants what:", _cast_lines(ec, cast, None), SLICE_CAST_WORDS))
    parts.append(_part("Where things stand before this episode:",
                       _state_lines(ec, cast, ledger_before(knowledge, ec.season, ec.ep)), SLICE_STATE_WORDS))
    lines = [line for part in parts for line in part]
    if not lines:
        return ""
    return "\n".join([_EPISODE_HEADER] + lines)


def _tag_names(subjects, kind) -> list:
    prefix = {"characters": "@", "props": "%"}[kind]
    return [tag[1:].split(":", 1)[0] for tag in subjects if tag.startswith(prefix)]


def slice_for_shot(ec, scene, plan, previous_shot, *, ledger) -> str:
    """What a shot-planning call (T1 v2) needs beyond what its own prompt
    already shows (the descriptors, the place, the lines with their
    delivery, the previous shots: ``prompts.build_t1_v2``): the ledger's
    facts for the subjects -- each character's current wardrobe set (by its
    tag) and who holds each prop (only a holder present in the scene). The
    subjects are *plan*'s (``subjects``, a re-plan) or, with *plan* None, the
    scene's characters and props. *previous_shot* (``{action, staging}``),
    when given, adds its action and staging for a caller whose prompt does
    not show it already. '' when the ledger says nothing about them."""
    if plan is not None:
        char_ids = _tag_names(plan.get("subjects") or (), "characters")
        prop_ids = _tag_names(plan.get("subjects") or (), "props")
    else:
        char_ids, prop_ids = list(scene.get("characters") or ()), list(scene.get("props") or ())
    characters = ec.entities.get("characters", {})
    present = set(scene.get("characters") or ())
    lines = []
    for cid in dict.fromkeys(char_ids):
        state = (ledger or {}).get(cid)
        wearing = _wardrobe_context(characters[cid], state.get("wardrobe_set")) if state and cid in characters else ""
        if wearing:
            lines.append(f"- @{cid} wears {trim_words(wearing, 16)[0]}")
    for pid in dict.fromkeys(prop_ids):
        holder = next((cid for cid, state in (ledger or {}).items()
                       if cid in present and pid in (state.get("possessions") or ())), None)
        if holder:
            lines.append(f"- %{pid} is held by @{holder}")
    if previous_shot and previous_shot.get("action"):
        staging = "; ".join(f"{entry['subject']} {entry['position']}" for entry in previous_shot.get("staging") or ())
        lines.append(f"- Previous shot: {previous_shot['action']}" + (f" Staging: {staging}" if staging else ""))
    if not lines:
        return ""
    return "\n".join(["Continuity now (keep it):"] + lines)
