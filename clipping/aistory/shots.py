"""Shot resolution for AI Story (spec 5, 2.8, 6.3, 6.4): turns per-scene shot
plans -- from the T1 prompt's reply, or from the deterministic "fast" path
below -- into a complete, valid ``storyboard_v1`` document.

A *plan* is the shared shape both paths produce, the same shape T1's own
reply carries (``prompts.t1_schema``): ``{"framing", "camera_motion",
"modifiers", "action", "subjects", "lines"}``, where ``action`` is one English
sentence that names people, the place and props only by their tags
(``@char_x``, ``#place_y:variant``, ``%prop_z`` -- spec 2.8's grammar),
``subjects`` is the tags visible in the shot, and ``lines`` is which of the
scene's own numbered lines (1-indexed, Python's own line-id order) the shot
covers. Both paths hand their scene's plans to :func:`rule_pass`, then
:func:`build_storyboard` resolves every plan into a shot of the final
document: an English image prompt with no entity name in it (spec 2.3), a
negative prompt, a reference-image list, motion and a duration.

Every function here is pure: no clock, no disk, no network, no import outside
the standard library and this package (DEC-012; RC-P8). ``entities`` is
always ``{"characters": {id: doc}, "places": {id: doc}, "props": {id: doc}}``
-- the story's full rosters, the shape ``store.list_entities`` already reads
into. Nothing here ever writes a character's or place's or prop's *name*
into a prompt (spec 2.3): a character is described by its descriptor and
signature items, never named, and every entity name still found by accident
(a plan's action written against the rules) is stripped at the very end with
the shared :func:`names.without_names` (the same helper ``refimages.py``
uses, RC-E5).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import re

from . import defaults
from . import names as names_mod
from . import native_speech, prompting, schemas, timing

# --------------------------------------------------------------- constants

# The emotions that pull a speaking-turn shot to close_up in the fast path
# (spec 5's "reaction" framings).
_TIGHT_EMOTIONS = ("angry", "shocked", "fear", "tension", "sad")

# Scene functions whose zoom uses motion_rules.tier1.zoom.peak instead of
# .dialogue, and whose last speaking-turn gets a fast-path reaction shot.
_PEAK_FUNCTIONS = ("peak", "cliffhanger")
_REACTION_FUNCTIONS = ("peak", "turn")

# rule_pass (a): the later shot's framing substitution table (spec: a fixed
# table, never chosen at random). A framing missing here (insert_prop) is
# never a change target -- it is always the *other* shot that moves instead.
_FRAMING_SUBSTITUTES = {
    "close_up": "medium_single",
    "medium_single": "close_up",
    "medium_two_shot": "over_shoulder",
    "over_shoulder": "medium_two_shot",
    "wide_establishing": "high_angle",
    "high_angle": "wide_establishing",
    "low_angle": "medium_single",
    "extreme_close_up": "close_up",
}

_TIGHT_FRAMINGS = ("close_up", "extreme_close_up")

# The leading-phrase cut points for character_handles/prop_handles: whichever
# of these occurs earliest in the descriptor's first sentence ends the kept
# phrase (2026-09-27 revision: keeps the noun after the last comma, not the
# leading adjective list before it -- see _leading_phrase).
_HANDLE_CUT_MARKERS = (" serving as ", " with ", " wearing ", " who ", " that ", ";")
_LEADING_ARTICLES = ("a ", "an ", "the ")
_HANDLE_MAX_WORDS = 10

# resolve_action/_subjects_block/_reference_images: a tag anywhere in text.
_TAG_FINDER = re.compile(r"[@%#][a-z0-9_:]+")

_CHAR_TAG = re.compile(r"^@(char_[a-z0-9_]+)$")
_PROP_TAG = re.compile(r"^%(prop_[a-z0-9_]+)$")
_PLACE_TAG = re.compile(r"^#(place_[a-z0-9_]+):([a-z][a-z0-9_]*)$")

_NEUTRAL_WORDS = {"characters": "the character", "places": "the place", "props": "the object"}
_KIND_TO_ENTITY_KEY = {"char": "characters", "place": "places", "prop": "props"}


# ------------------------------------------------------------------ helpers

def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_period(text: str) -> str:
    text = text.strip()
    if text.endswith("."):
        text = text[:-1]
    return text


# -------------------------------------------------------------- tag grammar

def parse_tag(tag) -> tuple:
    """``(kind, entity_id, variant)`` for one subject tag: ``kind`` is
    ``"char"``, ``"place"`` or ``"prop"``; ``variant`` is the place's time
    variant, ``None`` for a character or prop. ``ValueError`` for anything
    that is not one of the three (the storyboard schema's own
    ``SUBJECT_TAG_PATTERN`` grammar)."""
    if not isinstance(tag, str):
        raise ValueError(f"not a tag: {tag!r}")
    match = _CHAR_TAG.match(tag)
    if match:
        return "char", match.group(1), None
    match = _PROP_TAG.match(tag)
    if match:
        return "prop", match.group(1), None
    match = _PLACE_TAG.match(tag)
    if match:
        return "place", match.group(1), match.group(2)
    raise ValueError(f"not a valid subject tag: {tag!r}")


# ------------------------------------------------------------------ handles

def _cut_descriptor(descriptor: str) -> str:
    """:func:`_leading_phrase`'s steps 1-3: the descriptor's first sentence,
    cut at the earliest marker, without its leading article (commas kept)."""
    text = descriptor.strip()

    end = text.find(". ")
    text = text[:end] if end != -1 else text.rstrip(".")

    positions = [text.find(marker) for marker in _HANDLE_CUT_MARKERS]
    positions = [p for p in positions if p != -1]
    if positions:
        text = text[: min(positions)]

    lowered = text.lower()
    for article in _LEADING_ARTICLES:
        if lowered.startswith(article):
            text = text[len(article):]
            break
    return text


def _leading_phrase(descriptor: str) -> str:
    """The descriptor's own head-noun phrase: (1) its first sentence (split
    at the first '. '); (2) cut at the earliest of ' serving as '/' with '/
    ' wearing '/' who '/' that '/';'; (3) drop a leading article; (4) keep
    only the text after that phrase's LAST comma, if it has one -- this
    drops the leading comma-separated adjective list a K1-written
    descriptor usually opens with ("A fuzzy, dark brown ripe kiwi fruit..."
    -> "dark brown ripe kiwi fruit"), rather than keeping the adjectives and
    losing the noun; (5) collapse whitespace, keep <= 10 words."""
    text = _cut_descriptor(descriptor)
    if "," in text:
        text = text.rsplit(",", 1)[1]

    words = text.strip().strip(",;").split()
    return " ".join(words[:_HANDLE_MAX_WORDS])


def _handle_from_descriptor(descriptor: str) -> str:
    phrase = _leading_phrase(descriptor)
    return f"the {phrase}".strip()


# Plan 25 stage 0 (D-5): a character whose descriptor gives no species noun --
# a human cast of plan 23 D2's universes ("..., athletic build, tailored
# charcoal blazer, leather loafers") -- is called by its proper name. Its
# leading phrase is then empty, opens on one of these words ("in a tailored
# navy suit", "wears oversized hoodies"), or its head noun (the last word
# before its first preposition, :data:`_HEAD_CUT`) is a body word ("slender
# frame") or a garment (the last word of an item of its own wardrobe, or one
# of :data:`_GARMENT_NOUNS`).
_NOT_A_NOUN_OPENERS = frozenset({"a", "an", "the", "in", "on", "at", "of", "by", "for", "from", "with", "and", "or",
                                 "who", "that", "is", "are", "has", "have", "having", "wears", "wear", "wearing",
                                 "dressed", "sporting", "carrying", "holding", "plus"})
_BODY_NOUNS = frozenset({"frame", "build", "body", "physique", "silhouette", "stature", "posture", "skin",
                         "complexion", "eyes", "hair", "curls", "bob", "face", "jawline", "cheekbones", "shoulders",
                         "legs", "features", "stubble", "beard", "glasses", "shadow"})
_GARMENT_NOUNS = frozenset({"blazer", "blazers", "suit", "suits", "dress", "dresses", "hoodie", "hoodies", "jacket",
                            "jackets", "coat", "coats", "shirt", "shirts", "blouse", "blouses", "trousers", "jeans",
                            "loafers", "shoes", "boots", "sneakers", "heels", "gown", "skirt", "sweater", "sweaters",
                            "tee", "uniform", "tuxedo", "joggers", "sweatpants", "pants", "slacks", "cardigan",
                            "overalls", "scrubs"})


# The head noun of a leading phrase is the last word before its first preposition
# ("fuzzy kiwi fruit head on a body" -> "head"; "slender frame in crisp ivory blazer" -> "frame").
_HEAD_CUT = re.compile(r"\s+(?:in|on|at|of|under|over|behind|beside|from|inside|atop|across|near|into)\s+")


def _garment_nouns(look) -> set:
    """The last word of every item of every wardrobe set of *look*, lower case."""
    nouns = set()
    sets = look.get("wardrobe_sets") if isinstance(look, dict) else None
    for wardrobe_set in sets or ():
        if not isinstance(wardrobe_set, dict):
            continue
        for item in str(wardrobe_set.get("items") or "").split(","):
            words = re.findall(r"[a-z][a-z'-]*", item.lower())
            if words:
                nouns.add(words[-1])
    return nouns


def _species_phrase(phrase, look) -> bool:
    """Whether *phrase* (:func:`_leading_phrase` of a descriptor) names a
    species -- a noun a character can be called by ("anthropomorphic
    kiwi") -- rather than nothing, a preposition or verb, a body word or a
    garment."""
    words = phrase.lower().split()
    if not words or words[0] in _NOT_A_NOUN_OPENERS:
        return False
    head = _HEAD_CUT.split(phrase.lower(), maxsplit=1)[0]
    nouns = re.findall(r"[a-z][a-z'-]*", head)
    if not nouns:
        return False
    noun = nouns[-1]
    return not (noun in _BODY_NOUNS or noun in _GARMENT_NOUNS or noun in _garment_nouns(look))


def named_character(doc) -> bool:
    """Whether character *doc* is called by its proper name in every prompt
    (plan 25 stage 0, D-5): it has a name, and its descriptor's leading
    phrase names no species (:func:`_species_phrase`). A creature or fruit
    cast never is: its handle stays the descriptor's noun phrase -- unless
    its look names a species (:func:`look_species`, read first): then it is
    always called by its name."""
    if not isinstance(doc, dict) or not _collapse_ws(str(doc.get("name") or "")):
        return False
    if look_species(doc.get("look")):
        # DEC-305 section 5 (plan 28 stage F4): a cast whose look names its species is a named cast in a species
        # world, whatever its descriptor opens on ("Pear-headed woman ..." read as a species noun turned the three
        # Dragon Fruit casts into creature casts and swept their names).
        return True
    return not _species_phrase(_leading_phrase(str(doc.get("descriptor") or "")), doc.get("look"))


def character_handles(characters: dict) -> dict:
    """``{char_id: handle}``: a short English handle per character, from the
    leading noun phrase of its own descriptor (never its name -- spec 2.3),
    except a character whose descriptor names no species
    (:func:`named_character`, plan 25 stage 0): its proper name.
    Two characters whose base handle collides both get
    ``" wearing " + their first signature item`` appended (two named ones
    do not); if that still collides, ``" (n)"`` in cast order (the order
    *characters* iterates). Deterministic."""
    order = list(characters.keys())
    named = {cid for cid in order if named_character(characters[cid])}
    base = {cid: _collapse_ws(characters[cid]["name"]) if cid in named
            else _handle_from_descriptor(characters[cid]["descriptor"]) for cid in order}

    counts = {}
    for cid in order:
        counts[base[cid]] = counts.get(base[cid], 0) + 1

    handles = dict(base)
    for cid in order:
        if counts[base[cid]] > 1 and cid not in named:
            items = characters[cid].get("signature_items") or []
            first_item = items[0] if items else ""
            handles[cid] = _collapse_ws(f"{base[cid]} wearing {first_item}")

    counts2 = {}
    for cid in order:
        counts2[handles[cid]] = counts2.get(handles[cid], 0) + 1

    seen = {}
    for cid in order:
        if counts2[handles[cid]] > 1:
            key = handles[cid]
            seen[key] = seen.get(key, 0) + 1
            handles[cid] = f"{key} ({seen[key]})"

    return handles


# DEC-305 section 5 (plan 28 stage F4): where a prop's head noun phrase ends -- a preposition, a relative, a
# conjunction or (from the third word) a trailing participle ("sleek USB drive | clipped to a backpack strap").
_PROP_ARTICLE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)
_PROP_PHRASE_MAX_WORDS = 6
_PROP_PHRASE_STOPS = frozenset({
    "with", "without", "of", "in", "on", "at", "for", "from", "to", "by", "like", "near", "under", "over", "inside",
    "atop", "beside", "behind", "that", "which", "who", "whose", "where", "and", "or", "but", "as", "than"})
_PROP_PARTICIPLE = re.compile(r"^[a-z]+(?:ed|ing)$")


def prop_head_phrase(descriptor) -> str:
    """A prop's head noun phrase, without its article: the words of the
    descriptor's first comma/period clause before the first preposition,
    relative, conjunction or trailing participle, at most 6 ("polished half
    coconut shell", "sleek USB drive"); '' when nothing is left. The shared
    rule of
    ``prompt_templates._prop_phrase`` and :func:`prop_handles` (a handle is a
    noun phrase, never the clause a cut left behind)."""
    clause = _PROP_ARTICLE.sub("", _collapse_ws(re.split(r"[,.;:]", str(descriptor or ""), maxsplit=1)[0]))
    words = []
    for index, word in enumerate(clause.split()):
        bare = word.lower().strip("()'\"")
        if bare in _PROP_PHRASE_STOPS or (index >= 2 and _PROP_PARTICIPLE.match(bare)):
            break
        words.append(word)
        if len(words) == _PROP_PHRASE_MAX_WORDS:
            break
    if not words:
        return ""
    first = words[0].lower() if words[0][1:] == words[0][1:].lower() else words[0]
    return " ".join([first] + words[1:])


def _broken_prop_phrase(phrase, *, after_comma) -> bool:
    """Whether a leading phrase is no noun phrase: empty, opening or ending
    on a function word, or -- when it is what followed the descriptor's
    last comma (*after_comma*) -- a participle that opens a trailing clause
    ("pulsing", "glinting in the dark"; "glowing lamp" is a noun phrase)."""
    words = phrase.lower().split()
    if not words:
        return True
    first, last = words[0].strip("()'\""), words[-1].strip("()'\"")
    if first in _PROP_PHRASE_STOPS or last in _PROP_PHRASE_STOPS:
        return True
    if after_comma and _PROP_PARTICIPLE.match(first):
        return len(words) == 1 or any(word.strip("()'\"") in _PROP_PHRASE_STOPS for word in words)
    return False


def _prop_handle(descriptor) -> str:
    """:func:`_handle_from_descriptor`, unless its phrase is no noun phrase
    (:func:`_broken_prop_phrase`: "the pulsing" from "A sleek USB drive
    clipped to a backpack strap, pulsing with soft cyan light"); then the
    descriptor's head noun phrase (:func:`prop_head_phrase`), else the
    neutral "the object"."""
    descriptor = str(descriptor or "")
    phrase = _leading_phrase(descriptor)
    if not _broken_prop_phrase(phrase, after_comma="," in _cut_descriptor(descriptor)):
        return f"the {phrase}"
    head = prop_head_phrase(descriptor)
    return f"the {head}" if head else _NEUTRAL_WORDS["props"]


def prop_handles(props: dict) -> dict:
    """``{prop_id: handle}``, the same way as :func:`character_handles` but
    without the "wearing" disambiguation: a collision goes straight to
    ``" (n)"`` in cast order. A handle is a noun phrase (:func:`_prop_handle`)."""
    order = list(props.keys())
    base = {pid: _prop_handle(props[pid]["descriptor"]) for pid in order}

    counts = {}
    for pid in order:
        counts[base[pid]] = counts.get(base[pid], 0) + 1

    handles = dict(base)
    seen = {}
    for pid in order:
        key = base[pid]
        if counts[key] > 1:
            seen[key] = seen.get(key, 0) + 1
            handles[pid] = f"{key} ({seen[key]})"

    return handles


# ------------------------------------------------------------- action text

def resolve_action(action, *, char_handles, prop_handles, place_names) -> str:
    """*action*'s tags replaced by their handle (``@char_x``, ``%prop_y``) or
    ``"the setting"`` (``#place_z:variant``). ``ValueError`` for a tag whose
    entity is not in the given handles/names -- an unknown tag is a bug, never
    silently left in place."""

    def _replace(match):
        tag = match.group(0)
        kind, eid, _variant = parse_tag(tag)
        if kind == "char":
            if eid not in char_handles:
                raise ValueError(f"resolve_action: unknown character tag {tag!r}")
            return char_handles[eid]
        if kind == "prop":
            if eid not in prop_handles:
                raise ValueError(f"resolve_action: unknown prop tag {tag!r}")
            return prop_handles[eid]
        if eid not in place_names:
            raise ValueError(f"resolve_action: unknown place tag {tag!r}")
        return "the setting"

    return _TAG_FINDER.sub(_replace, action)


# ------------------------------------------------------------- shot prompts

def _character_block(doc) -> str:
    descriptor = _strip_period(doc["descriptor"])
    items = list(doc.get("signature_items") or [])
    if not items:
        raise ValueError("a character's signature_items must not be empty to build a shot prompt")
    return f"{descriptor}, wearing {', '.join(items)}."


def _prop_block(doc) -> str:
    return f"{_strip_period(doc['descriptor'])}."


def _place_block(doc) -> str:
    descriptor = _strip_period(doc["descriptor"])
    layout = _strip_period(doc["layout_notes"])
    return _collapse_ws(f"{descriptor}. {layout}")


# ----------------------------------------------- the look in words (phase 7, A10)
#
# A v2 story's entities carry a structured ``look`` (``schemas.CHARACTER_LOOK_
# SCHEMA`` & co.); these render it into prompt words. Pure, like everything
# here, and never a name: another character is named by its handle
# (:func:`character_handles`), a prop by its own.

LOOK_MAX_WORDS = 45
# The parts of a character's look dropped, in this order, while it is over
# LOOK_MAX_WORDS: what the reference images show anyway goes first.
_LOOK_DROP_ORDER = ("palette", "skin_material", "silhouette", "hair", "items", "face")
# How many other characters a look's height is said against.
_HEIGHT_OTHERS_MAX = 2
_CLOSE_PLACE_FRAMINGS = ("close_up", "extreme_close_up", "insert_prop")
_LAYOUT_PHRASES = (("left", "on the left"), ("right", "on the right"), ("back", "at the back"),
                   ("foreground", "in the foreground"), ("centre", "in the centre"))
# The one background element a close framing keeps, first found.
_BACKGROUND_KEYS = ("back", "centre", "left", "right", "foreground")


def _and_join(items) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _normal(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _already_worn(item, worn) -> bool:
    """Whether the signature *item* is already said by the wardrobe text
    *worn*: as a phrase, or every word of it longer than two letters."""
    item_n, worn_n = _normal(item), _normal(worn)
    if not item_n:
        return True
    if f" {item_n} " in f" {worn_n} ":
        return True
    words = [word for word in item_n.split() if len(word) > 2]
    return bool(words) and set(words) <= set(worn_n.split())


def height_phrase(height_cm, other_height_cm, handle) -> str:
    """How a *height_cm* character reads next to one of *other_height_cm*
    named by *handle*: "about twice as tall as ...", "about half the height
    of ...", "about the same height as ..." and the steps between."""
    ratio = height_cm / other_height_cm
    if ratio >= 2.5:
        return f"more than twice as tall as {handle}"
    if ratio >= 1.75:
        return f"about twice as tall as {handle}"
    if ratio >= 1.15:
        return f"taller than {handle}"
    if ratio > 1 / 1.15:
        return f"about the same height as {handle}"
    if ratio > 1 / 1.75:
        return f"shorter than {handle}"
    if ratio >= 1 / 2.5:
        return f"about half the height of {handle}"
    return f"less than half the height of {handle}"


def _height_part(doc, others) -> str:
    own = doc["look"]["height_cm"]
    sized = [other for other in others
             if other.get("descriptor") and (other.get("look") or {}).get("height_cm")
             and other.get("char_id") != doc.get("char_id")][:_HEIGHT_OTHERS_MAX]
    if not sized:
        return ""
    roster = {other["char_id"]: other for other in sized}
    if doc.get("descriptor"):
        roster = {doc["char_id"]: doc, **roster}
    handles = character_handles(roster)
    return " and ".join(height_phrase(own, other["look"]["height_cm"], handles[other["char_id"]])
                        for other in sized)


def _wardrobe(look, wardrobe_set):
    sets = look["wardrobe_sets"]
    if wardrobe_set is not None:
        for entry in sets:
            if entry["id"] == wardrobe_set:
                return entry
    return sets[0]


def sheet_wardrobe(doc):
    """The wardrobe set (``{id, context, items}``) a character's sheets are
    drawn in -- its look's first: ``refimages`` renders the portrait, the
    turnaround and the expressions with ``render_look(doc)``, whose default
    is that set -- or None without a look (phase 8 stage B)."""
    look = doc.get("look")
    return _wardrobe(look, None) if look and look.get("wardrobe_sets") else None


def shot_wardrobe(doc, ledger, char_id):
    """The wardrobe set a character wears in a shot of an episode whose
    continuity *ledger* (``context.ledger_before``, None without a knowledge
    base) is given: the ledger's set when the look has it, else the first --
    :func:`_staging`'s own choice -- or None without a look."""
    look = doc.get("look")
    if not look or not look.get("wardrobe_sets"):
        return None
    return _wardrobe(look, ((ledger or {}).get(char_id) or {}).get("wardrobe_set"))


def _lower_first(text) -> str:
    """*text* with its first letter lower case when the rest of that word
    and of the text is lower case already ("Comically oversized glass" ->
    "comically oversized glass"; "DJ booth" is kept): a stored signature
    item said inside a sentence."""
    if len(text) > 1 and text[0].isupper() and text[1:] == text[1:].lower():
        return text[0].lower() + text[1:]
    return text


def look_species(look) -> str:
    """The fruit or vegetable a character's head is (plan 26 stage 7a:
    ``look.species``, "pear", "dragon fruit"), as written, its spaces
    collapsed; '' when the look does not say one (every reader then reads
    exactly as before)."""
    if not isinstance(look, dict) or not isinstance(look.get("species"), str):
        return ""
    return _strip_period(_collapse_ws(look["species"]))


def _a_or_an(text) -> str:
    return "an" if text[:1].lower() in "aeiou" else "a"


def species_head_sentence(species) -> str:
    """What a sheet says once of a character with a species (plan 26 stage
    7a): "The head is a whole pear, the face carved into it, never a human
    head." '' without one."""
    return (prompting.as_sentence(f"The head is a whole {species}, the face carved into it, never a human head")
            if species else "")


def render_look(doc, *, wardrobe_set=None, others=(), max_words=LOOK_MAX_WORDS) -> str:
    """A character's look in at most *max_words* words (default
    :data:`LOOK_MAX_WORDS`; a tighter cap ends on a whole part): its head
    first when the look names its species (plan 26 stage 7a: "pear head" --
    never dropped under budget), its presentation next when the look has
    one (A3: apparent age and gender -- never dropped under budget), then
    build, its height against *others*
    (other character documents in the same frame, named by their handle),
    silhouette, face, hair, skin or material, "wearing" the wardrobe set's
    items (*wardrobe_set* by id, default the first), "colours" the palette,
    then "with" each signature item the wardrobe does not already say (its
    first letter lower case inside the sentence, :func:`_lower_first`). Over
    the cap, parts are dropped in ``_LOOK_DROP_ORDER``. ``ValueError`` when
    *doc* has no look."""
    look = doc.get("look")
    if not look:
        raise ValueError("render_look: the character has no look")
    worn = _strip_period(_wardrobe(look, wardrobe_set)["items"])

    def said(value):
        value = _strip_period(value)
        return "" if _normal(value) in ("none", "no hair", "n a") else value

    extra = [_lower_first(_strip_period(item)) for item in doc.get("signature_items") or ()
             if not _already_worn(item, worn)]
    species = look_species(look)
    parts = {
        "species": f"{species} head" if species else "",
        "presentation": _strip_period(look["presentation"]) if look.get("presentation") else "",
        "build": _strip_period(look["build"]),
        "height": _height_part(doc, others),
        "silhouette": said(look["silhouette"]),
        "face": said(look["face"]),
        "hair": said(look["hair"]),
        "skin_material": said(look["skin_material"]),
        "wearing": f"wearing {worn}",
        "palette": f"colours {_and_join([_strip_period(c) for c in look['palette']])}",
        "items": f"with {_and_join(extra)}" if extra else "",
    }

    def text():
        return _collapse_ws(", ".join(value for value in parts.values() if value))

    for key in _LOOK_DROP_ORDER:
        if len(text().split()) <= max_words:
            break
        parts[key] = ""
    words = text().split()
    if max_words < LOOK_MAX_WORDS and len(words) > max_words:
        # A tighter cap (a layered shot prompt's, phase 7 stage 3b) ends on a
        # whole part, never inside one ("wearing stiff rectangular blue").
        return _with_delta(doc, prompting.fit_words(text(), max_words) or " ".join(words[:max_words]).rstrip(",;"))
    return _with_delta(doc, " ".join(words[:max_words]).rstrip(",;"))


# ------------------------------------------------- appearance variants (plan 23 stage D5)
#
# A character's named appearance variant ("ghost version", ``variants`` on its
# document) is worn per shot: a shot's ``variants`` (``{char_id: variant_id}``,
# inherited from its scene's ``states``) makes :func:`_layered` resolve the
# shot with each such character's *variant view* (:func:`variant_view`): its
# variant's sheets in its ``refs`` -- so the identity image sent is the
# variant's portrait, its turnaround and expressions the variant's or none --
# and its delta said after its look (:func:`render_look`). A shot or a scene
# with no ``variants`` resolves exactly as before. The view is never stored.

VARIANT_KEY = "variant_worn"
_VARIANT_SLOTS = ("portrait", "turnaround", "expressions")


def _with_delta(doc, text) -> str:
    """*text* (a character's rendered look), then ", now <delta>" when *doc*
    is a variant view: the delta is whole, whatever the look's cap."""
    variant = doc.get(VARIANT_KEY)
    if not variant:
        return text
    delta = _strip_period(_collapse_ws(variant["delta_text"]))
    return f"{text}, now {delta}" if text else f"now {delta}"


def variant_record(doc, variant_id):
    """The appearance variant *variant_id* of character *doc*, or None."""
    return next((variant for variant in (doc or {}).get("variants") or () if variant["variant_id"] == variant_id),
                None)


def variant_view(doc, variant_id):
    """Character *doc* as it looks in its variant *variant_id*: a copy whose
    ``refs`` hold the variant's sheets (a slot the variant has not made is
    empty: the base's sheet is never sent for it) and whose
    :data:`VARIANT_KEY` carries ``{variant_id, label, delta_text}``. *doc*
    itself when *variant_id* is None or names no variant of it (the
    keyframe refuses such a shot: :func:`variant_refusal`)."""
    variant = variant_record(doc, variant_id) if variant_id else None
    if variant is None:
        return doc
    view = dict(doc)
    refs = dict(doc.get("refs") or {})
    for slot in _VARIANT_SLOTS:
        refs[slot] = (variant.get("refs") or {}).get(slot)
    view["refs"] = refs
    view[VARIANT_KEY] = {key: variant[key] for key in ("variant_id", "label", "delta_text")}
    return view


def with_variants(characters, variants) -> dict:
    """*characters* (``{char_id: doc}``) with each one *variants* names in
    its variant view (:func:`variant_view`); *characters* itself without
    *variants*."""
    if not variants:
        return characters
    return {cid: variant_view(doc, variants.get(cid)) for cid, doc in characters.items()}


def speech_look(doc, *, max_words) -> str:
    """A speaking clip's look of character *doc* (plan 22's speech prompt,
    at most *max_words*): :func:`render_look` -- a named character
    (:func:`named_character`): :func:`named_look` --, or -- a variant view --
    the variant's delta ("now ..."), which the identity image cannot say: the
    keyframe already shows the rest."""
    variant = doc.get(VARIANT_KEY)
    if not variant:
        if named_character(doc):
            # Plan 25 stage 0: after a name, who the character is in a few words, never the build dump.
            text = named_look(doc.get("look") or {})
            return prompting.fit_words(text, max_words) or " ".join(text.split()[:max_words]).rstrip(",;")
        return render_look(doc, max_words=max_words)
    text = f"now {_strip_period(_collapse_ws(variant['delta_text']))}"
    return prompting.fit_words(text, max_words) or " ".join(text.split()[:max_words]).rstrip(",;")


def shot_variants(scene, subject_tags) -> dict:
    """The variants a new shot of *scene* inherits (``scene.states``),
    for the characters it frames (*subject_tags*); {} for none."""
    states = scene.get("states") or {}
    if not states:
        return {}
    framed = [tag[1:] for tag in subject_tags if tag.startswith("@")]
    return {cid: states[cid] for cid in framed if cid in states}


def variant_refusal(shot, characters):
    """Why *shot*'s keyframe cannot be made for the variants it names (its
    ``variants``), in one sentence, or None: a variant its character does
    not have, or one not approved yet (its sheets not all made and
    approved) -- never a silent fallback to the base look."""
    for cid, variant_id in (shot.get("variants") or {}).items():
        doc = characters.get(cid) or {}
        name = doc.get("name") or cid
        variant = variant_record(doc, variant_id)
        if variant is None:
            return (f"shot {shot['shot_id']} shows {name} as {variant_id!r}, a variant {name} does not have: set "
                    "the shot back to the base look, or pick one of the character's variants")
        if not variant.get("approved_at"):
            return (f"shot {shot['shot_id']} shows {name} as '{variant['label']}', a variant not approved yet: make "
                    f"its sheets and approve it (variant:{cid}:{variant_id}), or set the shot back to the base look")
    return None


# ------------------------------------------- the anchors of an action prompt (plan 23 D6)
#
# ``generation_profile.prompt_style: "action"`` names a character at every
# mention by the same colour/species phrase, built from its look and nothing
# else: "the green-yellow female strawberry character in a dirty burlap
# dress". Deterministic: the same document always gives the same words, and
# the handle (:func:`character_handles`, already unique in a cast) is in it
# whole, so two characters never share an anchor.

_ANCHOR_FEMALE = frozenset({"female", "woman", "girl", "lady"})
_ANCHOR_MALE = frozenset({"male", "man", "boy", "gentleman"})
_ANCHOR_ARTICLES = frozenset({"a", "an", "the", "his", "her", "its", "their", "some", "two", "three", "four", "pair"})
ANCHOR_OUTFIT_MAX_WORDS = 5
# A handle that already says one of these keeps its own colour (the palette adds none).
_ANCHOR_COLOUR_WORDS = frozenset({"red", "orange", "yellow", "green", "blue", "purple", "violet", "pink", "brown",
                                  "black", "white", "grey", "gray", "golden", "gold", "silver", "turquoise", "beige",
                                  "cream", "crimson", "scarlet", "teal", "magenta", "tan"})


def _anchor_gender(presentation) -> str:
    words = set(re.findall(r"[a-z]+", str(presentation or "").lower()))
    if words & _ANCHOR_FEMALE:
        return "female"
    if words & _ANCHOR_MALE:
        return "male"
    return ""


def _anchor_colour(palette, noun) -> str:
    """The palette's first one or two colours as "green-yellow" ("green" when
    one has more than a word); '' when *noun* already says a colour (a
    handle that says its colour keeps it)."""
    said = set(re.findall(r"[a-z]+", noun.lower()))
    picks = [_strip_period(str(colour)).strip().lower() for colour in (palette or ())[:2] if str(colour).strip()]
    if not picks or said & (set(picks[0].split()) | _ANCHOR_COLOUR_WORDS):
        return ""
    if len(picks) == 2 and all(len(pick.split()) == 1 for pick in picks):
        return "-".join(picks)
    return picks[0]


def _anchor_outfit(look) -> str:
    """The first item of the look's first wardrobe set, with its article, in at most
    :data:`ANCHOR_OUTFIT_MAX_WORDS` words ("a dirty burlap dress"); ''."""
    sets = look.get("wardrobe_sets") or ()
    first = _collapse_ws(_strip_period(str(sets[0].get("items") or ""))).split(",")[0] if sets else ""
    cut = first.lower().find(" with ")
    if cut > 0:
        first = first[:cut]
    words = first.split()[:ANCHOR_OUTFIT_MAX_WORDS]
    if not words:
        return ""
    text = " ".join(words).strip(" ,;")
    if words[0].lower() in _ANCHOR_ARTICLES:
        return _lower_first(text)
    plural = words[-1].lower().endswith("s") and not words[-1].lower().endswith("ss")
    return _lower_first(text) if plural else f"{'an' if text[0].lower() in 'aeiou' else 'a'} {_lower_first(text)}"


_PRESENTATION_MAX_WORDS = 6


def _anchor_presentation(presentation) -> str:
    """A look's presentation as said after a name: its first clause, lower
    case, with an article ("Woman in her thirties" -> "a woman in her
    thirties"; "man in his forties, authoritative presence" -> "a man in his
    forties"), at most :data:`_PRESENTATION_MAX_WORDS` words after it; ''."""
    text = _collapse_ws(re.split(r"[,;.(]", str(presentation or ""), maxsplit=1)[0])
    words = text.split()
    if not words:
        return ""
    if words[0].lower() in ("a", "an", "the"):
        article, words = words[0].lower(), words[1:]
    else:
        article = ""
    words = words[:_PRESENTATION_MAX_WORDS]
    if not words:
        return ""
    body = _lower_first(" ".join(words))
    article = article or ("an" if body[0].lower() in "aeiou" else "a")
    return f"{article} {body}"


def named_look(look) -> str:
    """Who a named character is in a few words (plan 25 stage 0): its
    presentation (:func:`_anchor_presentation`) and "in " + its first outfit
    item (:func:`_anchor_outfit`) -- "a woman in her thirties in a charcoal
    blazer" --, each left out when the look does not say it; ''.

    A look that names its species (plan 26 stage 7a, :func:`look_species`)
    says the head after the presentation: "a woman in her thirties with a
    pear head, in a charcoal blazer"."""
    look = look if isinstance(look, dict) else {}
    outfit = _anchor_outfit(look) if look else ""
    species = look_species(look)
    if species:
        who = " ".join(part for part in (_anchor_presentation(look.get("presentation")),
                                         f"with {_a_or_an(species)} {species} head") if part)
        return ", ".join(part for part in (who, f"in {outfit}" if outfit else "") if part)
    return " ".join(part for part in (_anchor_presentation(look.get("presentation")),
                                      f"in {outfit}" if outfit else "") if part)


def character_anchor(doc, handle) -> str:
    """The colour/species anchor of a character, said at every mention in an
    action prompt in place of its bare *handle*: "the {colour} {gender}
    {species} character in {outfit}" -- the colour from its look's palette,
    the gender from its presentation, the species the handle's own noun
    phrase, the outfit its first wardrobe item. Words a character's look does
    not give are left out; with no look, or a handle already told apart by
    its outfit or number (:func:`character_handles`), the handle itself.

    A named character (:func:`named_character`, its *handle* its name: plan
    25 stage 0) is "{Name}, {named_look}" -- "Marie-Jeanne, a woman in her
    thirties in a charcoal blazer" --, no colour or species word; its name
    alone when its look says neither. A look that names its species (plan 26
    stage 7a) says the head: "Marie-Jeanne, a woman in her thirties with a
    pear head, in a charcoal blazer" (:func:`named_look`); an unnamed cast
    whose handle does not say it already, "with a pear head" after
    "character"."""
    if isinstance(doc, dict) and handle and handle == _collapse_ws(str(doc.get("name") or "")) and (
            named_character(doc)):
        described = named_look(doc.get("look") or {})
        return f"{handle}, {described}" if described else handle
    look = doc.get("look") if isinstance(doc, dict) else None
    if not look or " wearing " in handle or handle.endswith(")"):
        return handle
    noun = handle[4:] if handle.lower().startswith("the ") else handle
    parts = [_anchor_colour(look.get("palette"), noun), _anchor_gender(look.get("presentation")), noun,
             "" if noun.lower().endswith("character") else "character"]
    head = "the " + " ".join(part for part in parts if part)
    species = look_species(look)
    if species and species.lower() not in noun.lower():
        head = f"{head} with {_a_or_an(species)} {species} head"
    outfit = _anchor_outfit(look)
    return f"{head} in {outfit}" if outfit else head


def character_anchors(characters) -> dict:
    """``{char_id: anchor}`` for every character (:func:`character_anchor` of its handle)."""
    handles = character_handles(characters) if characters else {}
    return {cid: character_anchor(doc, handles[cid]) for cid, doc in characters.items()}


def worn_anchors(characters, variants=None) -> dict:
    """:func:`character_anchors`, each character a shot's *variants* names
    (plan 23 stage D5: ``{char_id: variant_id}``) followed by its variant's
    delta -- "the brown female kiwi character in green sundress (now a
    translucent glowing ghost)" -- at every mention, as the anchor is. Without
    *variants*, exactly :func:`character_anchors`."""
    anchors = character_anchors(characters)
    for cid, variant_id in (variants or {}).items():
        variant = variant_record(characters.get(cid), variant_id)
        if variant is not None and cid in anchors:
            anchors[cid] = f"{anchors[cid]} (now {_strip_period(_collapse_ws(variant['delta_text']))})"
    return anchors


def action_text(shot, entities, anchors) -> str:
    """What *shot* does, for an action prompt: its ``clip_motion`` (T1 v2's: what the
    characters do during the clip) or its ``action``, every character tag
    resolved to its anchor (*anchors*, ``{char_id: anchor}``) and every entity
    name swept; a tag that does not resolve falls back to the stored
    ``video_action`` with each handle swapped for its anchor."""
    characters = entities.get("characters") or {}
    props = entities.get("props") or {}
    places = entities.get("places") or {}
    name_map = _v2_name_map(entities)
    raw = shot.get("clip_motion") or shot.get("action") or ""
    try:
        text = resolve_action(raw, char_handles=anchors, prop_handles=prop_handles(props) if props else {},
                              place_names={pid: doc.get("name") for pid, doc in places.items()})
    except ValueError:
        handles = character_handles(characters) if characters else {}
        text = prompting.swap_phrases(shot.get("video_action") or raw, {handles[cid]: anchors[cid] for cid in handles
                                                                         if cid in anchors})
    text = _collapse_ws(names_mod.without_names(text, name_map))
    # Plan 25 stage 0: a named character's anchor is said at its first mention, its name after it.
    for cid, doc in characters.items():
        anchor, name = anchors.get(cid), _collapse_ws(str((doc or {}).get("name") or ""))
        if anchor and name and anchor != name and anchor.startswith(name + ",") and named_character(doc):
            first = text.find(anchor)
            if first != -1:
                cut = first + len(anchor)
                text = text[:cut] + text[cut:].replace(anchor, name)
    return text


def _place_light(look, variant) -> str:
    light = (look.get("lighting") or {}).get(variant)
    return _strip_period(light) if light else f"{variant.replace('_', ' ')} light"


def render_prop(doc, *, short=False, for_reference=False) -> str:
    """A prop in words: its descriptor, material, colour and real-scale
    phrase; *short* (set dressing on a plate): its handle with colour,
    material and scale. *for_reference* (A1, the prop's own reference image):
    the scale phrase left out -- a reference image shows the object alone,
    with nothing in frame to judge scale against, so "real scale" wording
    there invited a hand holding the object; a keyframe, which has the scene
    to judge scale against, keeps the full text. Without a look, the
    descriptor (or the handle)."""
    descriptor = _strip_period(doc["descriptor"])
    look = doc.get("look")
    if short:
        handle = _handle_from_descriptor(descriptor)
        if not look:
            return handle
        return (f"{handle} ({_strip_period(look['colour'])} {_strip_period(look['material'])}, "
                f"{_strip_period(look['scale_phrase'])})")
    if not look:
        return descriptor
    text = f"{descriptor}, {_strip_period(look['material'])}, {_strip_period(look['colour'])}"
    if not for_reference:
        text = f"{text}, {_strip_period(look['scale_phrase'])}"
    return _collapse_ws(text)


def render_place(doc, variant, framing, *, props=()) -> str:
    """A place in words for *variant* and *framing*: the descriptor, the
    layout map ("on the left ...", ...), the variant's light, the scale note
    and *props* (prop documents living there) as set dressing; a close
    framing (close-up, extreme close-up, insert) keeps only the light and
    one background element. ``ValueError`` when *doc* has no look or the
    framing is unknown."""
    if framing not in schemas.FRAMINGS:
        raise ValueError(f"unknown framing: {framing!r}")
    look = doc.get("look")
    if not look:
        raise ValueError("render_place: the place has no look")
    layout = look["layout_map"]
    light = _place_light(look, variant)
    if framing in _CLOSE_PLACE_FRAMINGS:
        behind = next((_strip_period(layout[key]) for key in _BACKGROUND_KEYS if layout[key].strip()), "")
        return f"Light: {light}." + (f" Behind: {behind}." if behind else "")
    sentences = [_strip_period(doc["descriptor"])]
    sides = [f"{phrase} {_strip_period(layout[key])}" for key, phrase in _LAYOUT_PHRASES if layout[key].strip()]
    if sides:
        sentences.append("Layout: " + ", ".join(sides))
    sentences.append(f"Light: {light}")
    sentences.append(f"Scale: {_strip_period(look['scale_note'])}")
    dressing = [render_prop(prop, short=True) for prop in props if prop.get("descriptor")]
    if dressing:
        sentences.append("Set dressing: " + ", ".join(dressing))
    return _collapse_ws(". ".join(sentences) + ".")


def _subjects_block(subject_tags, *, characters, props) -> str:
    """Spec 5's ``subjects_block``: every character tag's descriptor +
    signature items (WITHOUT the style's character_design_rules --
    ``shot_prompt`` injects those itself, once), in subject order; with no
    character tag, the props' descriptors in subject order; with neither,
    "No people in frame."."""
    char_parts = []
    for tag in subject_tags:
        if tag.startswith("@"):
            _kind, cid, _variant = parse_tag(tag)
            doc = characters.get(cid)
            if doc is None:
                raise ValueError(f"_subjects_block: unknown character tag {tag!r}")
            char_parts.append(_character_block(doc))
    if char_parts:
        return _collapse_ws(" ".join(char_parts))

    prop_parts = []
    for tag in subject_tags:
        if tag.startswith("%"):
            _kind, pid, _variant = parse_tag(tag)
            doc = props.get(pid)
            if doc is None:
                raise ValueError(f"_subjects_block: unknown prop tag {tag!r}")
            prop_parts.append(_prop_block(doc))
    if prop_parts:
        return _collapse_ws(" ".join(prop_parts))

    return "No people in frame."


def _reference_images(subject_tags, *, scene, characters, places, props) -> list:
    """Spec 5's reference list: characters in subject order (portrait), then
    the place's own variant image (falling back to the day plate), then
    props in subject order (image); entries with no image on the entity are
    skipped; capped to 8."""
    refs = []

    for tag in subject_tags:
        if tag.startswith("@"):
            _kind, cid, _variant = parse_tag(tag)
            doc = characters.get(cid)
            if doc is None:
                continue
            ref = (doc.get("refs") or {}).get("portrait")
            if ref and ref.get("name"):
                refs.append(f"characters/{cid}/refs/{ref['name']}")

    place_doc = places.get(scene["place_id"])
    if place_doc is not None:
        variants = place_doc.get("time_variants") or {}
        ref = variants.get(scene["time_variant"])
        if not ref:
            ref = variants.get(schemas.MASTER_PLATE_VARIANT)
        if ref and ref.get("name"):
            refs.append(f"places/{scene['place_id']}/refs/{ref['name']}")

    for tag in subject_tags:
        if tag.startswith("%"):
            _kind, pid, _variant = parse_tag(tag)
            doc = props.get(pid)
            if doc is None:
                continue
            ref = doc.get("image")
            if ref and ref.get("name"):
                refs.append(f"props/{pid}/refs/{ref['name']}")

    return refs[:8]


def _without_called_names(names, entities) -> dict:
    """*names* without the name of a character called by it
    (:func:`named_character`, plan 25 stage 0): that name is its handle, and
    sweeping it would garble every mention -- even where a place or prop
    shares it. *names* itself (the same dict) for a cast with none."""
    called = {_collapse_ws(str(doc["name"])).lower() for doc in (entities.get("characters") or {}).values()
              if named_character(doc)}
    if not called:
        return names
    return {name: word for name, word in names.items() if _collapse_ws(str(name)).lower() not in called}


def _story_name_map(entities) -> dict:
    """``{name: neutral word}`` for every character, place and prop of
    *entities* -- a character's word wins on a name shared with a place or
    prop (mirrors ``refimages._entity_names``); a character called by its
    name (:func:`named_character`) keeps it (:func:`_without_called_names`)."""
    names = {}
    for kind in ("characters", "places", "props"):
        for doc in entities.get(kind, {}).values():
            names.setdefault(doc["name"], _NEUTRAL_WORDS[kind])
    return _without_called_names(names, entities)


# Words a name may carry beside its noun without being a proper name.
_ARTICLES = frozenset({"a", "an", "the", "of", "le", "la", "les", "l", "un", "une", "des", "du", "de", "d"})


def _own_words(value) -> set:
    """Every lower-case word of *value* (a string, or the strings of a look's
    nested dicts and lists)."""
    if isinstance(value, str):
        return set(re.findall(r"\w+", value.lower()))
    items = value.values() if isinstance(value, dict) else value if isinstance(value, (list, tuple)) else ()
    words = set()
    for item in items:
        words |= _own_words(item)
    return words


def _descriptive_name(doc) -> bool:
    """Whether an entity's name only says what it is: every word of it,
    articles aside, is a word of its own descriptor or look ("Monocle" for
    "a golden monocle on a thin chain"). Such a name is the thing's noun,
    not a proper name, and stripping it garbles the entity's own handle."""
    words = [word for word in re.findall(r"\w+", str(doc.get("name") or "").lower()) if word not in _ARTICLES]
    own = _own_words(doc.get("descriptor") or "") | _own_words(doc.get("look") or {})
    return bool(words) and all(word in own for word in words)


def _v2_name_map(entities) -> dict:
    """:func:`_story_name_map` without the descriptive names
    (:func:`_descriptive_name`), for a v2 story (the W-mid walk: "lifts the
    golden the object on a thin chain"). Every proper name is still in it."""
    names = {}
    for kind in ("characters", "places", "props"):
        for doc in entities.get(kind, {}).values():
            if not _descriptive_name(doc):
                names.setdefault(doc["name"], _NEUTRAL_WORDS[kind])
    return _without_called_names(names, entities)


# ------------------------------------------------- layered shots (phase 7, A8/A9)
#
# A v2 story's shot is resolved into the layered prompt of
# ``prompting.layered_shot_prompt`` and its clip prompt
# (``prompting.layered_clip_prompt``); a legacy story's never reaches here.

# The most reference images a v2 shot carries: the smallest per-link limit
# ``steps/assets.REFERENCE_LIMITS`` knows, so what the role text names is
# always what is sent.
V2_MAX_REFERENCES = 10
# Phase 8 stage B: the slot of a v2 shot's ``reference_images`` that stands
# for the previous keyframe of its scene -- not a file of the story's media,
# so the stored list never names an episode image that a redraw replaces;
# ``steps/assets.request_parts`` sends that shot's image in its place, and
# keeps it out of the prompt hash (a redrawn previous keyframe never makes
# this shot stale).
CONTINUITY_REFERENCE = "continuity/previous_shot"
# Words of a wardrobe set a reference role says (phase 8 stage B): the
# staging says the whole set again.
_OUTFIT_ROLE_WORDS = 12
_EXPRESSION_FRAMINGS = ("close_up", "extreme_close_up")
# Where the characters of a frame stand, in subject order.
_POSITIONS = {1: ("In the centre",), 2: ("On the left", "On the right"),
              3: ("On the left", "In the centre", "On the right"),
              4: ("Far left", "Centre left", "Centre right", "Far right")}
# Where a T1 v2 staging entry puts its subject (phase 7 stage 4).
_STAGED_POSITIONS = {"left": "On the left", "centre": "In the centre", "right": "On the right",
                     "back": "In the background"}
# An emotion said as an adjective ("neutral" is not said).
_EMOTION_WORDS = {"tension": "tense", "fear": "afraid", "triumph": "triumphant", "neutral": ""}
_DELIVERY_MAX_WORDS = 8
# The word budgets tried in turn until a keyframe prompt fits its cap:
# (each look's words, the place's words -- None: whole --, the rendering's).
# The rendering goes first (the references show the style), then the look
# and the place shrink in alternate steps, so neither is emptied for the other.
_LAYERED_BUDGETS = ((LOOK_MAX_WORDS, None, 30), (LOOK_MAX_WORDS, None, 8)) + tuple(
    (look, place, 8) for look, place in ((40, None), (40, 50), (34, 50), (34, 42), (28, 42), (28, 36), (24, 36),
                                         (24, 30), (21, 30), (21, 26), (18, 26), (18, 22), (14, 22), (14, 18)))
# Past that ladder (the W-mid walk's crowded keyframes still ran 234-243
# words, a three-character shot with two props 308): the reference roles said
# compactly and the props in their short form, then the looks, the place and
# the rendering cut further; last, what the sent images already show -- the
# layout (the set image) and the prop sentences (each prop's image, named in
# the roles and the beat) -- is left out. Looks keep 4 words: a look's
# presentation leads it (stage 3d). A prompt that fits earlier never reaches
# these rungs. Each: (look words, place words, rendering words, props).
_LAYERED_LAST_RUNGS = ((14, 18, 8, "short"), (12, 14, 0, "short"), (10, 12, 0, "short"), (8, 10, 0, "short"),
                       (6, 8, 0, "short"), (4, 6, 0, "short"), (4, 0, 0, "none"))
_REFERENCES_MODE = "references"

# ------------------------------------------- the context layers (stage F2)
#
# What a keyframe says beyond the roles, the beat, the staging, the
# composition, the place and the style (the human, 2026-10-02: "more prompt
# context is more accuracy and details"), each a sentence group written by
# the functions below, in the order the budget ladder drops them -- the
# least valuable first: what stands between two characters (the writers'
# words), the time of day (the place's light says most of it), each
# character's bearing, what changed since the previous shot (the continuity
# image shows it; the words keep the model from copying its positions), and
# last the beat's temperature with the faces and who is hurt. The ladder
# (:func:`_rungs`) cuts the rendering first, as before (the references show
# the style), then these, then the looks and the place.
_CONTEXT_DROP_ORDER = ("between", "when", "bearing", "since", "mood")
# A scene's function as the mood's first words.
_FUNCTION_WORDS = {"recap": "a recap", "hook": "the opening hook", "setup": "a quiet setup", "rising": "rising tension",
                   "peak": "the peak of the episode", "turn": "the turning point", "cliffhanger": "the cliffhanger"}
_INJURY_MAX_WORDS = 8
_RELATIONSHIP_MAX_WORDS = 15
_BEARING_MAX_WORDS = schemas.LOOK_BEARING_MAX_WORDS
_BEFORE_MAX_WORDS = 14
# The sheets' cues (:func:`visual_cues`): the words K1 and U1 use for a
# character's distinctive marks (U1's ask: "accessories, distinctive
# marks") -- a descriptor clause holding one is a mark the sheets must show
# --, the words such a clause may open with, and the most the marks take.
_MARK_WORDS = frozenset(
    "scar scars scarred mark marks birthmark tattoo tattoos freckle freckles freckled mole moles patch eyepatch "
    "chipped chip cracked crack dented dent missing bandage bandaged stitch stitches stitched notch notched torn "
    "burn burnt burned scratch scratched stain stained wart warts bruise bruised limp hunched crooked bent broken "
    "scuffed faded peeling rust rusty mended frayed".split())
_CUE_OPENERS = ("with", "and", "wearing", "sporting", "bearing", "has", "having", "showing")
_MARKS_MAX_WORDS = 20
_CLAUSE_SPLIT = re.compile(r"[,;.!?]\s+|[.!?]$")
# The clip's camera intent per camera motion (``prompting.CAMERA_PHRASES``'
# keys), and the scene functions whose shot lands the beat.
_CAMERA_INTENT = {"hold": "letting the moment breathe", "push_in": "closing on the emotion",
                  "pull_out": "revealing the whole scene", "pan_lr": "following the exchange left to right",
                  "pan_rl": "following the exchange right to left", "pan_ud": "settling on the detail below",
                  "pan_du": "rising to the face"}
_LANDING_FUNCTIONS = ("peak", "turn", "cliffhanger")
# DEC-252 (the human, 2026-10-03: "the video does not say things or do
# movements, boring"): how the framed characters who do not speak react to a
# line said with this emotion (``schemas.EMOTIONS``; neutral is not one: the
# scene's emotion is read instead, then its function, :data:`_FUNCTION_REACTIONS`).
# Each reads after "reacts visibly, ...".
_REACTIONS = {"shocked": "stepping back, eyes widening", "tension": "leaning in, jaw set",
              "scheming": "narrowing the eyes, a slow smile", "angry": "recoiling, then glaring back",
              "sad": "lowering the eyes, shoulders sinking", "happy": "breaking into a grin",
              "tender": "softening, leaning closer", "fear": "flinching back, hands rising",
              "triumph": "lifting the chin, eyes bright"}
_FUNCTION_REACTIONS = {"recap": "turning sharply toward the speaker", "hook": "turning sharply toward the speaker",
                       "setup": "nodding, eyes on the speaker", "rising": "leaning in, jaw set",
                       "peak": "stepping back, eyes widening", "turn": "freezing, then turning away",
                       "cliffhanger": "freezing, eyes widening"}


class PromptOverBudget(ValueError):
    """A v2 shot's prompt that is over its word budget even on the ladder's
    last rung (phase 7 follow-up, stage F2): refused, never sent cut or over
    its link's limit (until this stage the last rung was sent anyway).
    ``kind`` (``"keyframe"`` or ``"clip"``), ``words`` (the shortest it got),
    ``budget`` (``prompting.Budgets``' number for the kind); ``shot_id`` and
    ``link`` once the caller knows them (:func:`build_storyboard`,
    :func:`refresh_prompts` -- :meth:`named`). ``str()`` says all of it and
    what to do. A ``ValueError``, so a caller that turns a shot it cannot
    resolve into its own message still does."""

    def __init__(self, kind, words, budget, *, shot_id=None, link=None):
        self.kind, self.words, self.budget, self.shot_id, self.link = kind, words, budget, shot_id, link
        super().__init__(self.sentence())

    def named(self, shot_id, link) -> "PromptOverBudget":
        """The same refusal, naming the shot and the link it was built for."""
        return PromptOverBudget(self.kind, self.words, self.budget, shot_id=shot_id, link=link)

    def sentence(self) -> str:
        who = f"shot {self.shot_id}'s" if self.shot_id else "the shot's"
        where = f"{self.link}'s" if self.link else "its link's"
        shorten = "its looks or its place" if self.kind == "keyframe" else "its motion"
        what = "images" if self.kind == "keyframe" else "clips"
        return (f"{who} {self.kind} prompt cannot fit {where} budget of {self.budget} words: {self.words} words at "
                f"its shortest. Shorten the shot's action, {shorten}, or make the episode's {what} on a link that "
                f"accepts a longer prompt ('prompt-limits' lists each link's); nothing was sent.")


def _frame_characters(subject_tags, characters) -> list:
    """``[(char_id, doc), ...]`` of the character tags, in subject order,
    each once. ``ValueError`` for a tag no character of the story has."""
    out, seen = [], set()
    for tag in subject_tags:
        if tag.startswith("@"):
            _kind, cid, _variant = parse_tag(tag)
            if cid not in characters:
                raise ValueError(f"resolve_shot: unknown character tag {tag!r}")
            if cid not in seen:
                seen.add(cid)
                out.append((cid, characters[cid]))
    return out


def _frame_props(subject_tags, props) -> list:
    out, seen = [], set()
    for tag in subject_tags:
        if tag.startswith("%"):
            _kind, pid, _variant = parse_tag(tag)
            if pid not in props:
                raise ValueError(f"resolve_shot: unknown prop tag {tag!r}")
            if pid not in seen:
                seen.add(pid)
                out.append((pid, props[pid]))
    return out


def _reference_images_v2(subject_tags, *, scene, framing, characters, places, props, char_handles,
                         prop_handles, continuity=False) -> list:
    """A v2 shot's references as ``[(path, role, handle), ...]``, in the
    order they are sent (A9): one identity sheet per character in subject
    order (its full-body portrait; the expression sheet instead on a
    close-up or extreme close-up when it has one), the place's own variant
    (falling back to the day plate), with *continuity* (phase 8 stage B: the
    shot is not its scene's first) the previous keyframe of the scene
    (:data:`CONTINUITY_REFERENCE`), each character's turnaround, then the
    props -- so the turnarounds and props are the ones the cap drops first;
    an entity with no such image is skipped; at most
    :data:`V2_MAX_REFERENCES`."""
    refs = []
    frame = _frame_characters(subject_tags, characters)
    for cid, doc in frame:
        own = doc.get("refs") or {}
        portrait, expressions = own.get("portrait"), own.get("expressions")
        if framing in _EXPRESSION_FRAMINGS and expressions and expressions.get("name"):
            refs.append((f"characters/{cid}/refs/{expressions['name']}", prompting.ROLE_EXPRESSIONS,
                         char_handles[cid]))
        elif portrait and portrait.get("name"):
            refs.append((f"characters/{cid}/refs/{portrait['name']}", prompting.ROLE_IDENTITY, char_handles[cid]))

    place_doc = places.get(scene["place_id"])
    if place_doc is not None:
        variants = place_doc.get("time_variants") or {}
        ref = variants.get(scene["time_variant"]) or variants.get(schemas.MASTER_PLATE_VARIANT)
        if ref and ref.get("name"):
            refs.append((f"places/{scene['place_id']}/refs/{ref['name']}", prompting.ROLE_SET, ""))

    if continuity:
        refs.append((CONTINUITY_REFERENCE, prompting.ROLE_CONTINUITY, ""))

    for cid, doc in frame:
        turnaround = (doc.get("refs") or {}).get("turnaround")
        if turnaround and turnaround.get("name"):
            refs.append((f"characters/{cid}/refs/{turnaround['name']}", prompting.ROLE_TURNAROUND,
                         char_handles[cid]))

    for pid, doc in _frame_props(subject_tags, props):
        image = doc.get("image")
        if image and image.get("name"):
            refs.append((f"props/{pid}/refs/{image['name']}", prompting.ROLE_PROP, prop_handles[pid]))

    return refs[:V2_MAX_REFERENCES]


def _shot_lines(plan, scene) -> list:
    """The script lines a plan covers: its ``lines`` are 1-based numbers into
    the scene's lines (a plan) or line ids (a storyboard shot); one the
    scene no longer has is left out."""
    by_id = {line["line_id"]: line for line in scene.get("lines") or ()}
    ordered = list(scene.get("lines") or ())
    out = []
    for entry in plan.get("lines") or ():
        if isinstance(entry, int) and not isinstance(entry, bool):
            if 1 <= entry <= len(ordered):
                out.append(ordered[entry - 1])
        elif entry in by_id:
            out.append(by_id[entry])
    return out


def _delivery_clause(lines, char_handles) -> str:
    """The first spoken line's emotion and delivery as one short clause
    ("the cylinder looks shocked while speaking (slow, monotone)"), or ''."""
    for line in lines:
        handle = char_handles.get(line.get("speaker"))
        if not handle:
            continue
        emotion = line.get("emotion") or ""
        feeling = _EMOTION_WORDS.get(emotion, emotion)
        delivery = prompting.fit_words(line.get("delivery") or "", _DELIVERY_MAX_WORDS)
        delivery = _lower_first(delivery) if delivery else ""
        if feeling and delivery:
            return f"{handle} looks {feeling} while speaking ({delivery})"
        if feeling:
            return f"{handle} looks {feeling} while speaking"
        if delivery:
            return f"{handle} speaks ({delivery})"
        return ""
    return ""


def _plain_look(doc, max_words) -> str:
    """A character with no look yet, in words: its descriptor and the
    signature items it does not already say."""
    descriptor = _strip_period(doc["descriptor"])
    extra = [_lower_first(_strip_period(item)) for item in doc.get("signature_items") or ()
             if not _already_worn(item, descriptor)]
    text = f"{descriptor}, with {_and_join(extra)}" if extra else descriptor
    return prompting.fit_words(text, max_words) or " ".join(text.split()[:max_words])


def _holder(prop_doc, frame_ids, char_handles, ledger=None) -> str:
    """Who in the frame holds the prop: the ledger's (phase 7 stage 5c: a
    character whose ``possessions`` list it) when it says, else the prop
    look's ``where_when``; '' when no one in the frame does."""
    for cid, state in (ledger or {}).items():
        if cid in frame_ids and prop_doc.get("prop_id") in (state.get("possessions") or ()):
            return char_handles[cid]
    for entry in (prop_doc.get("look") or {}).get("where_when") or ():
        holder = entry.get("holder_char_id")
        if holder in frame_ids:
            return char_handles[holder]
    return ""


def _staged(entry, resolve) -> str:
    """A T1 v2 staging entry's expression and facing as one parenthesis
    (" (angry, facing the mango)"), or ''."""
    parts = []
    expression = _collapse_ws(resolve(entry.get("expression") or ""))
    facing = _collapse_ws(resolve(entry.get("facing") or ""))
    if expression:
        parts.append(_strip_period(expression))
    if facing:
        parts.append(f"facing {_strip_period(facing)}")
    return f" ({', '.join(parts)})" if parts else ""


def _staging(frame, frame_props, *, char_handles, look_words, staging=(), resolve=lambda text: text,
             ledger=None, props="full") -> str:
    """Each character of the frame by its handle, where it stands (left,
    right, centre in subject order) and its look -- its height said against
    the others in the frame --, two facing each other; then each prop of
    the shot with its look and who holds it.

    *staging* (phase 7 stage 4: T1 v2's entries, ``{subject, position,
    facing, expression}``) places each subject it names where it says, with
    its expression and facing; a subject it does not name keeps the default
    place, and two characters are said to face each other only when nothing
    is staged. *resolve* turns the tags of those texts into handles.

    *ledger* (phase 7 stage 5c: ``{char_id: state}`` when the episode
    starts, ``context.ledger_before``) dresses each character in its
    current wardrobe set and says who holds each prop; None: the look's
    first set, the prop's ``where_when``, as before. *props* (a crowded
    keyframe, its prop images sent): ``"full"`` (``render_prop``),
    ``"short"`` (its short form) or ``"none"`` (no prop sentence)."""
    sentences = []
    n = len(frame)
    positions = _POSITIONS.get(n) or tuple(f"Position {i} from the left" for i in range(1, n + 1))
    staged = {entry["subject"]: entry for entry in staging or () if entry.get("subject")}
    docs = [doc for _cid, doc in frame]
    for (cid, doc), where in zip(frame, positions):
        others = [other for other in docs if other is not doc]
        wardrobe_set = ((ledger or {}).get(cid) or {}).get("wardrobe_set")
        look = (render_look(doc, wardrobe_set=wardrobe_set, others=others, max_words=look_words) if doc.get("look")
                else _plain_look(doc, look_words))
        entry = staged.get(f"@{cid}")
        if entry is not None:
            where = _STAGED_POSITIONS.get(entry.get("position"), where)
            sentences.append(prompting.as_sentence(f"{where}, {char_handles[cid]}{_staged(entry, resolve)}: {look}"))
        else:
            sentences.append(prompting.as_sentence(f"{where}, {char_handles[cid]}: {look}"))
    if n == 2 and not any(f"@{cid}" in staged for cid, _doc in frame):
        sentences.append("They face each other.")
    frame_ids = {cid for cid, _doc in frame}
    for pid, doc in frame_props if props != "none" else ():
        held = _holder(doc, frame_ids, char_handles, ledger)
        text = render_prop(doc, short=props == "short")
        entry = staged.get(f"%{pid}")
        if entry is not None and entry.get("position") in _STAGED_POSITIONS:
            text = f"{_STAGED_POSITIONS[entry['position']]}, {text}"
        sentences.append(prompting.as_sentence(f"{text}, held by {held}" if held else text))
    return " ".join(sentence for sentence in sentences if sentence)


def _place_slice(place_doc, variant, framing, props) -> str:
    """The place in words for the framing (``render_place``): with a look,
    the full layout on a wide or medium framing, the light and one
    background element on a close one; without a look, its descriptor (and
    its layout notes on a wide or medium framing) and the variant's light."""
    if place_doc.get("look"):
        here = [props[pid] for pid in place_doc["look"].get("props_here") or () if pid in props]
        text = render_place(place_doc, variant, framing, props=here)
        return text if framing in _CLOSE_PLACE_FRAMINGS else f"Setting: {text}"
    light = f"Light: {variant.replace('_', ' ')} light."
    if framing in _CLOSE_PLACE_FRAMINGS:
        return f"Setting: {prompting.fit_words(place_doc['descriptor'], 20)}. {light}"
    return f"Setting: {_place_block(place_doc)}. {light}"


# The sentences of a place slice, by what they start with, in the order a
# tight budget keeps them: where things are and the light before the
# descriptor (the set image shows it), the scale and the dressing last.
_PLACE_SENTENCE_START = re.compile(r"(?<=\.) (?=(?:Layout|Light|Scale|Set dressing|Behind): )")
_PLACE_KEEP_ORDER = ("Layout:", "Light:", "Behind:", "Setting:", "Scale:", "Set dressing:")


def _fit_place(text, max_words) -> str:
    """The place slice *text* in at most *max_words* words: its sentences kept
    whole in ``_PLACE_KEEP_ORDER`` while they fit (the first one that does
    not is cut at a clause boundary), said in their own order."""
    sentences = _PLACE_SENTENCE_START.split(text)
    rank = {sentence: next((i for i, start in enumerate(_PLACE_KEEP_ORDER) if sentence.startswith(start)),
                           len(_PLACE_KEEP_ORDER)) for sentence in sentences}
    kept, left = {}, max_words
    for sentence in sorted(sentences, key=lambda sentence: rank[sentence]):
        words = len(sentence.split())
        if words <= left:
            kept[sentence] = sentence
            left -= words
            continue
        cut = prompting.fit_words(sentence, left)
        if len(cut.split()) > 1:
            kept[sentence] = prompting.as_sentence(cut)
            left -= len(cut.split())
        break
    return " ".join(kept[sentence] for sentence in sentences if sentence in kept)


# The words a hard cut never ends on (``_cut``): a gist cut after "to" or
# "and" reads as a broken sentence.
_DANGLING = frozenset("a an the and or but to at in on of for with from by into over under toward towards as while "
                      "her his its their his my our your than then".split())


def _cut(text, max_words) -> str:
    """*text* in at most *max_words* words: at a clause boundary when one
    falls inside, else the first words, never ending on a dangling
    article, preposition or conjunction."""
    text = _strip_period(_collapse_ws(text))
    fitted = prompting.fit_words(text, max_words)
    if fitted:
        return fitted
    words = text.split()[:max_words]
    while len(words) > 1 and words[-1].lower().strip(",;") in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(",;")


def _feeling(emotion) -> str:
    return _EMOTION_WORDS.get(emotion or "", emotion or "")


def _mood(scene, lines, frame, char_handles, ledger) -> str:
    """The beat's temperature and the characters' state (stage F2): the
    scene's function and emotion ("Mood: the opening hook, shocked."), the
    first framed speaker mid-sentence -- asking a question, exclaiming or
    speaking, never the words, which a model would draw as lettering -- and
    who listens, then who is hurt (the ledger's ``injuries``, drawn)."""
    feeling = _feeling(scene.get("emotion"))
    function = _FUNCTION_WORDS.get(scene.get("function"), scene.get("function") or "")
    sentences = [prompting.as_sentence(f"Mood: {function}" + (f", {feeling}" if feeling else ""))] if function else []
    frame_ids = [cid for cid, _doc in frame]
    speakers = [line["speaker"] for line in lines if line.get("speaker") in frame_ids]
    if speakers:
        first = speakers[0]
        text = next((line.get("text") or "" for line in lines if line.get("speaker") == first), "").strip()
        kind = "asking a question" if text.endswith("?") else "exclaiming" if text.endswith("!") else "speaking"
        face = f"{char_handles[first]} is mid-sentence, {kind}"
        listeners = [char_handles[cid] for cid in frame_ids if cid not in speakers]
        if listeners:
            face += f"; {_and_join(listeners)} {'listens' if len(listeners) == 1 else 'listen'}"
        sentences.append(prompting.as_sentence(face))
    for cid in frame_ids:
        hurt = ((ledger or {}).get(cid) or {}).get("injuries")
        if hurt:
            sentences.append(prompting.as_sentence(
                f"{char_handles[cid]} is hurt: {_cut(str(hurt), _INJURY_MAX_WORDS)}"))
    return " ".join(sentences)


def _relationship_now(doc, other_id) -> str:
    for item in ((doc.get("dossier") or {}).get("relationships") or ()):
        if item.get("with") == other_id and item.get("now"):
            return _strip_period(item["now"])
    return ""


def _between(frame, char_handles) -> str:
    """What stands between the first two framed characters whose dossiers
    relate them (stage F2; the first one's ``now``, else the other's -- the
    writers' words, so the model draws the body language of it): "Between
    the kiwi and the mango: rivals who pretend to be friends." '' with none."""
    for i, (cid, doc) in enumerate(frame):
        for other, other_doc in frame[i + 1:]:
            now = _relationship_now(doc, other) or _relationship_now(other_doc, cid)
            if now:
                return prompting.as_sentence(f"Between {char_handles[cid]} and {char_handles[other]}: "
                                             f"{_cut(now, _RELATIONSHIP_MAX_WORDS)}")
    return ""


def _positions(plan, frame) -> dict:
    """``{char_id: "left" | "centre" | "right" | "background" | ...}``: where
    each framed character of *plan* stands -- its staging entry's position,
    else its place in subject order (``_POSITIONS``' last word)."""
    staged = {entry["subject"]: entry for entry in plan.get("staging") or () if entry.get("subject")}
    n = len(frame)
    defaults = _POSITIONS.get(n) or tuple(f"position {i}" for i in range(1, n + 1))
    out = {}
    for (cid, _doc), where in zip(frame, defaults):
        entry = staged.get(f"@{cid}")
        position = entry.get("position") if entry is not None and entry.get("position") in _STAGED_POSITIONS else None
        out[cid] = position or where.split()[-1].lower()
    return out


def _since(previous_plan, plan, frame, frame_props, *, characters, props, char_handles, prop_handles, resolve) -> str:
    """What changed since the previous shot of the scene (stage F2;
    *previous_plan*, None for a scene's first shot: ''): who moved where
    (the staging positions, else subject order), who and which prop came
    into frame, and what happened just before (the previous action's gist,
    its tags resolved -- never the words a model would letter)."""
    if previous_plan is None:
        return ""
    before = _frame_characters(previous_plan.get("subjects") or (), characters)
    was, now = _positions(previous_plan, before), _positions(plan, frame)
    changes = []
    for cid, _doc in frame:
        if cid not in was:
            changes.append(f"{char_handles[cid]} has come into frame")
        elif was[cid] != now[cid]:
            changes.append(f"{char_handles[cid]} has moved to the {now[cid]}")
    held_before = {pid for pid, _doc in _frame_props(previous_plan.get("subjects") or (), props)}
    changes.extend(f"{prop_handles[pid]} is now in frame" for pid, _doc in frame_props if pid not in held_before)
    sentences = []
    if changes:
        sentences.append(prompting.as_sentence("Since the previous shot: " + "; ".join(changes)))
    action = _collapse_ws(previous_plan.get("action") or "")
    if action:
        gist = _lower_first(_cut(resolve(action), _BEFORE_MAX_WORDS))
        sentences.append(prompting.as_sentence(f"Just before, {gist}"))
    return " ".join(sentences)


def _bearing(frame, char_handles) -> str:
    """Each framed character's bearing (stage F2: ``look.bearing`` --
    posture, how they hold themselves -- when the look has one): "Bearing:
    the kiwi stands rigidly straight, chin up; the mango slouches." """
    parts = []
    for cid, doc in frame:
        bearing = ((doc.get("look") or {}).get("bearing") or "").strip()
        if bearing:
            parts.append(f"{char_handles[cid]} {_lower_first(_cut(bearing, _BEARING_MAX_WORDS))}")
    return prompting.as_sentence("Bearing: " + "; ".join(parts)) if parts else ""


def visual_cues(doc) -> str:
    """A character's visual cues for its sheets (stage F2;
    ``refimages.character_image`` hands them to the v2 sheet builders): the
    distinctive marks its descriptor names -- a clause with a word of
    :data:`_MARK_WORDS` -- that its look and signature items do not say
    already (:func:`_already_worn`), then its ``look.bearing``: "Distinctive:
    a deep scar through the left eyebrow. Bearing: stands rigidly straight,
    chin up." '' without a look (the legacy sheets), or with neither. A look
    that names its species (plan 26 stage 7a) says the head first, once
    (:func:`species_head_sentence`)."""
    look = doc.get("look")
    if not look:
        return ""
    said = " ".join([render_look(doc)] + list(doc.get("signature_items") or ()))
    marks = []
    for clause in _CLAUSE_SPLIT.split(doc.get("descriptor") or ""):
        words = clause.split()
        while words and words[0].lower() in _CUE_OPENERS:
            words = words[1:]
        clause = " ".join(words).strip()
        if not clause or not (set(_normal(clause).split()) & _MARK_WORDS) or _already_worn(clause, said):
            continue
        marks.append(_lower_first(clause))
    sentences = []
    head = species_head_sentence(look_species(look))
    if head:
        sentences.append(head)
    if marks:
        sentences.append(prompting.as_sentence("Distinctive: " + _cut(", ".join(marks), _MARKS_MAX_WORDS)))
    bearing = (look.get("bearing") or "").strip()
    if bearing:
        sentences.append(prompting.as_sentence(f"Bearing: {_lower_first(_cut(bearing, _BEARING_MAX_WORDS))}"))
    return " ".join(sentences)


def _when(place_doc, variant) -> str:
    """The time of day and weather of the place variant (stage F2), for a
    place with a look -- whose slice says the variant's light in its own
    words, not the variant; without a look the slice says "Light: night
    light" already, so nothing is added."""
    return prompting.as_sentence(f"Time: {variant.replace('_', ' ')}") if place_doc.get("look") else ""


def _clip_emotion(scene, lines, frame, char_handles) -> str:
    """The beat's emotion for the clip (stage F2): the scene's ("The mood is
    tense"), and the first framed speaker's when its line's differs ("; the
    kiwi looks shocked"); '' when both are neutral."""
    scene_feeling = _feeling(scene.get("emotion"))
    frame_ids = [cid for cid, _doc in frame]
    line = next((line for line in lines if line.get("speaker") in frame_ids), None)
    line_feeling = _feeling(line.get("emotion")) if line else ""
    parts = [f"The mood is {scene_feeling}"] if scene_feeling else []
    if line_feeling and line_feeling != scene_feeling:
        parts.append(f"{char_handles[line['speaker']]} looks {line_feeling}")
    return "; ".join(parts)


def _reaction(scene, lines) -> str:
    """How the listeners of the shot react (DEC-252, :data:`_REACTIONS`): to
    its first line said with an emotion other than neutral, else to the
    scene's emotion, else as its function asks."""
    for emotion in [line.get("emotion") for line in lines] + [scene.get("emotion")]:
        if emotion in _REACTIONS:
            return _REACTIONS[emotion]
    return _FUNCTION_REACTIONS.get(scene.get("function"), "reacting with clear, readable gestures")


def _performance(scene, lines, frame, char_handles) -> str:
    """What the framed characters perform during the clip (DEC-252, in place
    of stage F2's "reacts with a small natural movement"): whoever speaks
    one of the shot's lines in frame speaks with the mouth moving on the
    words -- never the words themselves (DEC-201: the TTS is the voice, and
    quoted words would be drawn as lettering) --, the others react visibly
    (:func:`_reaction`); with no line in the shot, everyone in frame acts
    the moment out. '' with no one in frame."""
    frame_ids = [cid for cid, _doc in frame]
    speakers = [cid for cid in dict.fromkeys(line.get("speaker") for line in lines) if cid in frame_ids]
    others = [cid for cid in frame_ids if cid not in speakers]
    parts = []
    if speakers:
        who = _and_join([char_handles[cid] for cid in speakers])
        parts.append(f"{who} speaks with the mouth moving on the words, face and brows carrying the emotion"
                     if len(speakers) == 1 else
                     f"{who} speak in turn, mouths moving on the words, faces and brows carrying the emotion")
    if others:
        who = _and_join([char_handles[cid] for cid in others])
        one = len(others) == 1
        if lines:
            parts.append(f"{who} {'reacts' if one else 'react'} visibly, {_reaction(scene, lines)}")
        else:
            parts.append(f"{who} {'acts' if one else 'act'} the moment out with clear gestures, "
                         f"{_reaction(scene, lines)}")
    return "; ".join(parts)


def _gestures(frame, frame_props, staged, char_handles, prop_handles, resolve, ledger=None) -> str:
    """The framed characters' gestures for the clip (DEC-252, in the slot of
    stage F2's micro-actions -- "breathes visibly, hands shift slightly"):
    each one T1 v2 staged turns toward what it faces with its expression on
    the face, and each prop a framed character holds is in its hands
    (:func:`_holder`). '' when the staging and the props say nothing."""
    staging = {entry["subject"]: entry for entry in staged or () if entry.get("subject")}
    frame_ids = {cid for cid, _doc in frame}
    parts = []
    for cid, _doc in frame:
        entry = staging.get(f"@{cid}") or {}
        facing = _strip_period(_collapse_ws(resolve(entry.get("facing") or "")))
        expression = _strip_period(_collapse_ws(resolve(entry.get("expression") or "")))
        bits = ([f"turns toward {facing}"] if facing else []) + ([f"face {expression}"] if expression else [])
        if bits:
            parts.append(f"{char_handles[cid]} {', '.join(bits)}")
    for pid, doc in frame_props:
        holder = _holder(doc, frame_ids, char_handles, ledger)
        if holder:
            parts.append(f"{holder}'s hands work {prop_handles[pid]}")
    return "Gestures: " + "; ".join(parts) if parts else ""


def _camera_intent(camera_motion, function) -> str:
    """The camera's intent for the clip (stage F2): what its motion is for,
    and that it lands the beat on a peak, a turn or the cliffhanger."""
    intent = _CAMERA_INTENT.get(camera_motion, "")
    return f"{intent} to land the beat" if intent and function in _LANDING_FUNCTIONS else intent


def _rungs() -> list:
    """The budget ladder of :func:`_layered`, each rung ``(look words, place
    words, rendering words, props, context layers kept)``: the rendering cut
    first (the references show the style), then the context layers dropped
    one by one in :data:`_CONTEXT_DROP_ORDER`, then the looks and the place
    shrunk in turn (``_LAYERED_BUDGETS``), then the last rungs."""
    every = _CONTEXT_DROP_ORDER
    first, second = _LAYERED_BUDGETS[0], _LAYERED_BUDGETS[1]
    rungs = [first + ("full", every), second + ("full", every)]
    rungs += [second + ("full", every[k:]) for k in range(1, len(every) + 1)]
    rungs += [budget + ("full", ()) for budget in _LAYERED_BUDGETS[2:]]
    rungs += [rung + ((),) for rung in _LAYERED_LAST_RUNGS]
    return rungs


def _outfits(frame, char_handles, ledger) -> dict:
    """``{handle: wardrobe items}`` of each character of *frame* the shot
    dresses in another set than its sheets show (:func:`sheet_wardrobe`,
    :func:`shot_wardrobe`; phase 8 stage B): what the reference roles say it
    wears here instead of "outfit exactly"."""
    outfits = {}
    for cid, doc in frame:
        sheet, worn = sheet_wardrobe(doc), shot_wardrobe(doc, ledger, cid)
        if sheet is not None and worn is not None and worn["id"] != sheet["id"]:
            items = _strip_period(worn["items"])
            outfits[char_handles[cid]] = (prompting.fit_words(items, _OUTFIT_ROLE_WORDS)
                                          or " ".join(items.split()[:_OUTFIT_ROLE_WORDS]))
    return outfits


def _layered(plan, *, scene, entities, style_lock, consistency_mode, video_action, char_handles,
             prop_handles, name_map, ledger=None, continuity=False, budgets=None, previous_plan=None) -> dict:
    """The v2 half of :func:`resolve_shot`: ``image_prompt`` (layered,
    within its budget -- the context layers, the looks, the place and the
    rendering shortened in turn until it fits, :func:`_rungs`),
    ``video_prompt``, ``reference_images`` and ``prompt_layout``.
    *continuity*: the previous keyframe of the scene is one of the
    references (phase 8 stage B). *budgets* (``prompting.Budgets``, stage
    F2: each prompt's words, from the links the episode's images and clips
    go to; None: the fixed numbers). *previous_plan* (stage F2): the plan of
    the shot before this one in its scene, what changed since it is said
    (:func:`_since`); None for a scene's first shot.
    :class:`PromptOverBudget` when even the ladder's last rung is over the
    keyframe's budget, or the clip's fixed parts alone are over its own."""
    budgets = budgets or prompting.Budgets()
    # Plan 23 stage D5: each character the plan's ``variants`` names in its variant view.
    characters = with_variants(entities.get("characters", {}), plan.get("variants"))
    places = entities.get("places", {})
    props = entities.get("props", {})
    framing = plan["framing"]
    frame = _frame_characters(plan["subjects"], characters)
    frame_props = _frame_props(plan["subjects"], props)
    handles = [char_handles[cid] for cid, _doc in frame]
    held = [prop_handles[pid] for pid, _doc in frame_props]

    refs = _reference_images_v2(plan["subjects"], scene=scene, framing=framing, characters=characters,
                                places=places, props=props, char_handles=char_handles, prop_handles=prop_handles,
                                continuity=continuity)
    # The roles are written from the very list stored and sent (capped to the
    # smallest per-link limit), so image N of the text is image N of the request.
    sent = [(role, handle) for _path, role, handle in refs]
    references = consistency_mode == _REFERENCES_MODE
    # Phase 8 stage B: a character dressed in another set than its sheet's is
    # never asked to keep the sheet's outfit too.
    outfits = _outfits(frame, char_handles, ledger)
    # Plan 23 stage D4: the identity images are front+back sheets (``entities["sheet_mode"]``, set
    # by ``episode_common.load_context`` on a two-view story only): drawn once, not twice.
    two_view = entities.get("sheet_mode") in (defaults.SHEET_TWO_VIEW, defaults.SHEET_TWO_VIEW_EXPRESSIONS)
    roles = prompting.role_text(sent, outfits=outfits, two_view=two_view) if references else ""
    compact_roles = (prompting.role_text(sent, compact=True, outfits=outfits, two_view=two_view)
                     if references else "")

    lines = _shot_lines(plan, scene)
    clause = _delivery_clause(lines, char_handles)
    beat = " ".join(part for part in (prompting.as_sentence(video_action), prompting.as_sentence(clause)) if part)
    beat = names_mod.without_names(beat, name_map)
    composition = prompting.as_sentence(
        f"Camera: {prompting.layered_framing_phrase(framing, characters=handles, props=held)}, "
        f"{prompting.layered_lens_phrase(style_lock, framing)}")
    place_doc = places[scene["place_id"]]
    place_full = _place_slice(place_doc, scene["time_variant"], framing, props)
    constraints = (prompting.CONSTRAINTS_KEYFRAME if frame else prompting.CONSTRAINTS_KEYFRAME_NO_PEOPLE)

    place_names = {pid: doc.get("name") for pid, doc in places.items()}

    def resolve(text):
        return resolve_action(text, char_handles=char_handles, prop_handles=prop_handles, place_names=place_names)

    staged = plan.get("staging") or ()
    # The context layers (stage F2), each swept of names like the beat; the ladder keeps what fits.
    context = {
        "mood": names_mod.without_names(_mood(scene, lines, frame, char_handles, ledger), name_map),
        "between": names_mod.without_names(_between(frame, char_handles), name_map),
        "since": names_mod.without_names(
            _since(previous_plan, plan, frame, frame_props, characters=characters, props=props,
                   char_handles=char_handles, prop_handles=prop_handles, resolve=resolve), name_map),
        "bearing": names_mod.without_names(_bearing(frame, char_handles), name_map),
        "when": _when(place_doc, scene["time_variant"]),
    }
    for look_words, place_words, rendering_words, props_said, kept in _rungs():
        staging = names_mod.without_names(
            _staging(frame, frame_props, char_handles=char_handles, look_words=look_words, staging=staged,
                     resolve=resolve, ledger=ledger, props=props_said), name_map)
        place_text = place_full if place_words is None else _fit_place(place_full, place_words)
        image_prompt = prompting.layered_shot_prompt(
            style_lock, roles_text=roles if props_said == "full" else compact_roles, beat=beat, staging=staging,
            composition=composition, place_text=place_text, constraints=constraints,
            rendering_words=rendering_words, context={name: context[name] for name in kept})
        if len(image_prompt.split()) <= budgets.keyframe:
            break
    else:
        # Stage F2: past the last rung the prompt is refused, never sent anyway.
        raise PromptOverBudget("keyframe", len(image_prompt.split()), budgets.keyframe)

    camera_motion = plan.get("camera_motion")
    if camera_motion not in prompting.CAMERA_PHRASES:
        camera_motion = motion_for(framing, None, scene["function"], style_lock)["type"]
    subject = _and_join(handles) or (held[0] if held else "the set")
    # T1 v2's motion (phase 7 stage 4: what the characters do during the clip,
    # in tags) when the plan has one, else the resolved action as before.
    clip_motion = plan.get("clip_motion")
    motion = names_mod.without_names(_collapse_ws(resolve(clip_motion) if clip_motion else video_action), name_map)
    modifiers = [prompting.MODIFIER_PHRASES[m] for m in plan.get("modifiers") or () if m in prompting.MODIFIER_PHRASES]
    video_prompt = prompting.layered_clip_prompt(
        style_lock, subject=subject, motion=_strip_period(motion),
        camera_phrase=prompting.CAMERA_PHRASES[camera_motion], modifiers=modifiers, budget=budgets.clip,
        # Stage F2: the beat's emotion, the gestures and the camera's intent, dropped first when over;
        # DEC-252: then the performance (who speaks with the mouth moving, who reacts), never idleness.
        emotion=names_mod.without_names(_clip_emotion(scene, lines, frame, char_handles), name_map),
        performance=names_mod.without_names(_performance(scene, lines, frame, char_handles), name_map),
        micro=names_mod.without_names(
            _gestures(frame, frame_props, staged, char_handles, prop_handles, resolve, ledger), name_map),
        intent=_camera_intent(camera_motion, scene["function"]))
    if len(video_prompt.split()) > budgets.clip:
        # The motion is cut to the budget; the fixed parts (camera, identity clause, suffix) cannot be.
        raise PromptOverBudget("clip", len(video_prompt.split()), budgets.clip)

    return {
        "image_prompt": image_prompt,
        "video_prompt": video_prompt,
        "reference_images": [path for path, _role, _handle in refs],
        "prompt_layout": prompting.LAYERED_V1,
    }


def resolve_shot(shot, *, scene, entities, style_lock, consistency_mode, v2=False, ledger=None,
                 continuity=False, budgets=None, previous_plan=None) -> dict:
    """*shot* (a plan: ``framing``/``action``/``subjects``) resolved into
    ``{"image_prompt", "video_action", "negative_prompt", "reference_images",
    "consistency"}``. Every entity name is stripped from the resolved action
    alone (``video_action``, which also feeds ``image_prompt``'s action
    sentence), even one a plan's action wrongly spelled out instead of using
    a tag (spec 2.3) -- never from the whole assembled ``image_prompt``, so
    a place's own name recurring as ordinary words inside its own descriptor
    (phase 7 D2) survives untouched. ``prompt_override`` is never touched
    here -- the caller decides whether to use it instead of
    ``image_prompt``.

    *v2* (``media_policy.is_v2`` of the story, phase 7 stage 3b): the shot
    is resolved into the layered prompt instead (:func:`_layered`):
    ``image_prompt`` is the layered text, ``reference_images`` the v2 list
    (:func:`_reference_images_v2`), and the result also holds
    ``video_prompt`` (the clip prompt) and ``prompt_layout``
    (``"layered_v1"``). The plan may then carry ``lines`` (numbers or line
    ids), ``camera_motion``, ``modifiers``, ``clip_motion`` and ``staging``
    (T1 v2's, phase 7 stage 4). Off, the result
    is exactly the legacy one (RC-Q1).

    *ledger* (v2 only, phase 7 stage 5c: ``context.ledger_before`` of the
    episode, None without a knowledge base): each character is drawn in its
    current wardrobe set and each prop held by whoever holds it now.

    *continuity* (v2 only, phase 8 stage B: the shot is not the first of its
    scene, :func:`continues_scene`): the previous keyframe of the scene is
    one of its references (:data:`CONTINUITY_REFERENCE`), with its role.

    *budgets* (v2 only, stage F2: ``prompting.Budgets``, the word budget of
    the keyframe prompt and of the clip prompt from the links the episode's
    images and clips go to -- ``steps/clips.episode_budgets``; None: the
    fixed numbers every v2 prompt was built to before). A prompt that cannot
    fit even on the ladder's last rung raises :class:`PromptOverBudget`.

    *previous_plan* (v2 only, stage F2: the plan of the shot before this one
    in its scene -- :func:`previous_plan` reads it off a storyboard -- None
    for a scene's first shot): the keyframe says what changed since it."""
    characters = entities.get("characters", {})
    places = entities.get("places", {})
    props = entities.get("props", {})

    char_handles_map = character_handles(characters)
    prop_handles_map = prop_handles(props)
    place_names = {pid: doc.get("name") for pid, doc in places.items()}

    resolved_action = resolve_action(
        shot["action"], char_handles=char_handles_map, prop_handles=prop_handles_map, place_names=place_names,
    )
    # Phase 7 D1/D2: every entity name swept from the resolved action (tags
    # already resolved above) -- this is both the text an I2V clip prompt is
    # built from (video_plan.build_video_prompt) and the action sentence
    # image_prompt is assembled with, so neither a raw tag nor a leaked name
    # ever reaches either prompt. A v2 story keeps a descriptive name (a
    # prop's own noun) so a handle is never garbled (_v2_name_map).
    name_map = _v2_name_map(entities) if v2 else _story_name_map(entities)
    video_action = names_mod.without_names(resolved_action, name_map)
    if v2:
        layered = _layered(shot, scene=scene, entities=entities, style_lock=style_lock,
                           consistency_mode=consistency_mode, video_action=video_action,
                           char_handles=char_handles_map, prop_handles=prop_handles_map,
                           name_map=name_map, ledger=ledger, continuity=continuity, budgets=budgets,
                           previous_plan=previous_plan)
        return {
            "image_prompt": layered["image_prompt"],
            "video_action": video_action,
            "negative_prompt": prompting.negative_prompt(style_lock),
            "reference_images": layered["reference_images"],
            "consistency": consistency_mode,
            "video_prompt": layered["video_prompt"],
            "prompt_layout": layered["prompt_layout"],
        }
    subjects_block = _subjects_block(shot["subjects"], characters=characters, props=props)

    place_doc = places[scene["place_id"]]
    place_block = _place_block(place_doc)

    image_prompt = prompting.shot_prompt(
        style_lock,
        subjects_block=subjects_block,
        action=video_action,
        place_block=place_block,
        time_variant=scene["time_variant"],
        framing=shot["framing"],
    )

    return {
        "image_prompt": image_prompt,
        "video_action": video_action,
        "negative_prompt": prompting.negative_prompt(style_lock),
        "reference_images": _reference_images(shot["subjects"], scene=scene, characters=characters,
                                              places=places, props=props),
        "consistency": consistency_mode,
    }


# ------------------------------------------------------------------- motion

def _clamp_zoom(value, zoom_max):
    return round(min(max(value, 1.0), zoom_max), 3)


def motion_for(framing, camera_motion, scene_function, style_lock, *, v2=False) -> dict:
    """``{"type", "zoom_from", "zoom_to", "pan"}`` for one shot (spec 5's
    tier-1 motion): the TYPE is ``by_function[framing]`` if present, else
    ``by_function[scene_function]`` if present, else the given
    *camera_motion*, else the style's own ``default_motion``. ``push_in``
    zooms from ``zoom.dialogue`` to itself (``zoom.peak`` on a peak or
    cliffhanger scene); ``pull_out`` is the reverse; both clamp to
    ``zoom.max``. A ``pan_*`` type holds the zoom at 1.0 and pans that way;
    ``hold`` holds the zoom at 1.0 with no pan.

    *v2* (DEC-252: a v2 shot is a clip, its camera T1 v2's choice, never the
    previous shot's): a given *camera_motion* comes before the scene
    function's rule ("push-in on peaks" made every peak, hook and
    cliffhanger of fruit_drama a push-in, two in a row on a two-beat peak);
    a framing's own rule (a wide shot's pan) still comes first."""
    tier1 = style_lock["motion_rules"]["tier1"]
    by_function = tier1["by_function"]

    if framing in by_function:
        motion_type = by_function[framing]
    elif v2 and camera_motion is not None:
        motion_type = camera_motion
    elif scene_function in by_function:
        motion_type = by_function[scene_function]
    elif camera_motion is not None:
        motion_type = camera_motion
    else:
        motion_type = tier1["default_motion"]

    zoom = tier1["zoom"]
    zoom_max = zoom["max"]

    if motion_type in ("push_in", "pull_out"):
        lo, hi = zoom["peak"] if scene_function in _PEAK_FUNCTIONS else zoom["dialogue"]
        zoom_from, zoom_to = (lo, hi) if motion_type == "push_in" else (hi, lo)
        pan = "none"
    elif motion_type in ("pan_lr", "pan_rl", "pan_ud", "pan_du"):
        zoom_from, zoom_to = 1.0, 1.0
        pan = motion_type[len("pan_"):]
    else:  # hold
        zoom_from, zoom_to = 1.0, 1.0
        pan = "none"

    return {
        "type": motion_type,
        "zoom_from": _clamp_zoom(zoom_from, zoom_max),
        "zoom_to": _clamp_zoom(zoom_to, zoom_max),
        "pan": pan,
    }


# ---------------------------------------------------------------- fast path

def _plan(*, framing, camera_motion, modifiers, action, subjects, lines) -> dict:
    return {
        "framing": framing, "camera_motion": camera_motion, "modifiers": list(modifiers),
        "action": action, "subjects": list(subjects), "lines": list(lines),
    }


def _base_modifiers(scene, style_lock) -> list:
    allowed = set(style_lock["motion_rules"]["tier1"].get("modifiers") or [])
    mods = []
    if "jitter_stopmotion" in allowed:
        mods.append("jitter_stopmotion")
    if scene["emotion"] in ("tension", "fear") and "handheld" in allowed:
        mods.append("handheld")
    return mods


def _group_speaking_turns(lines) -> list:
    """``[(start, end, speaker), ...]`` (0-based, inclusive), one per run of
    consecutive lines by the same speaker."""
    turns = []
    i, n = 0, len(lines)
    while i < n:
        j = i
        speaker = lines[i]["speaker"]
        while j + 1 < n and lines[j + 1]["speaker"] == speaker:
            j += 1
        turns.append((i, j, speaker))
        i = j + 1
    return turns


def fast_plan(scene, *, lines, entities, episode_defaults, style_lock, first_at_place) -> list:
    """A deterministic (no LLM call) shot list for one scene, in the T1 reply
    shape: an insert-prop shot first on an ``insert_prop`` hook with a prop
    present, an opening shot (a wordless wide establishing when
    *first_at_place*, else the first speaking turn forced to
    medium_two_shot/medium_single), one shot per speaking turn (close_up on
    an angry/shocked/fear/tension/sad line, else medium_single), a reaction
    close_up after the last line on a peak/turn scene, or -- with no lines at
    all -- a wide_establishing plus one medium shot. Clamped to
    ``episode_defaults["shots_per_scene"]`` by merging speaking shots from
    the end (over) or adding a reaction/establishing shot (under)."""
    style_modifiers = style_lock["motion_rules"]["tier1"].get("modifiers") or []
    default_motion = style_lock["motion_rules"]["tier1"]["default_motion"]
    mods = _base_modifiers(scene, style_lock)

    chars = list(scene["characters"])
    props = list(scene["props"])
    place_tag = f"#{scene['place_id']}:{scene['time_variant']}"
    char_tags = [f"@{c}" for c in chars]

    plans = []

    if scene["function"] == "hook" and episode_defaults["hook_style"] == "insert_prop" and props:
        prop_tag = f"%{props[0]}"
        plans.append(_plan(
            framing="insert_prop", camera_motion=default_motion, modifiers=mods,
            action=f"{prop_tag} is shown in tight close-up, stating the episode's premise.",
            subjects=[prop_tag], lines=[],
        ))

    if not lines:
        plans.append(_plan(
            framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
            action=(f"Wide view of {place_tag} with {', '.join(char_tags)} present." if char_tags
                    else f"Wide view of {place_tag}, empty."),
            subjects=[place_tag] + char_tags, lines=[],
        ))
        second_framing = "medium_two_shot" if len(chars) >= 2 else "medium_single"
        plans.append(_plan(
            framing=second_framing, camera_motion=default_motion, modifiers=mods,
            action=(f"{', '.join(char_tags)} in a quiet moment, no dialogue." if char_tags
                    else "A quiet moment, no dialogue."),
            subjects=char_tags or [place_tag], lines=[],
        ))
    else:
        opening_framing = None
        if first_at_place:
            plans.append(_plan(
                framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
                action=(f"Wide view of {place_tag} with {', '.join(char_tags)} present." if char_tags
                        else f"Wide view of {place_tag}, empty."),
                subjects=[place_tag] + char_tags, lines=[],
            ))
        else:
            opening_framing = "medium_two_shot" if len(chars) >= 2 else "medium_single"

        turns = _group_speaking_turns(lines)
        for turn_index, (start, end, speaker) in enumerate(turns):
            turn_lines = lines[start:end + 1]
            line_numbers = list(range(start + 1, end + 2))
            emotional = any(l["emotion"] in _TIGHT_EMOTIONS for l in turn_lines)
            if turn_index == 0 and opening_framing is not None:
                framing = opening_framing
            else:
                framing = "close_up" if emotional else "medium_single"

            speaker_tag = f"@{speaker}" if speaker != "narrator" else None
            if framing == "medium_two_shot":
                subjects = char_tags
            elif speaker_tag is not None:
                subjects = [speaker_tag]
            else:
                subjects = char_tags

            emotion = turn_lines[-1]["emotion"]
            if speaker_tag is None:
                action = f"Narration plays over the scene, {emotion}."
            else:
                others = [t for t in char_tags if t != speaker_tag]
                action = (f"{speaker_tag} speaks, {emotion}, facing {others[0]}." if others
                          else f"{speaker_tag} speaks, {emotion}.")

            plans.append(_plan(
                framing=framing, camera_motion=default_motion, modifiers=mods,
                action=action, subjects=subjects, lines=line_numbers,
            ))

        if scene["function"] in _REACTION_FUNCTIONS and turns:
            last_speaker = turns[-1][2]
            other_chars = [c for c in chars if c != last_speaker]
            reaction_char = other_chars[0] if other_chars else (chars[0] if chars else None)
            if reaction_char is not None:
                reaction_tag = f"@{reaction_char}"
                plans.append(_plan(
                    framing="close_up", camera_motion=default_motion, modifiers=mods,
                    action=f"{reaction_tag} reacts silently.", subjects=[reaction_tag], lines=[],
                ))

    lo, hi = episode_defaults["shots_per_scene"]
    plans = _clamp_shot_count(plans, lo, hi, chars=chars, place_tag=place_tag,
                              default_motion=default_motion, mods=mods)
    return plans


def _clamp_shot_count(plans, lo, hi, *, chars, place_tag, default_motion, mods) -> list:
    plans = list(plans)

    while len(plans) > hi:
        speaking = [i for i, p in enumerate(plans) if p["lines"]]
        if len(speaking) >= 2:
            later_i, earlier_i = speaking[-1], speaking[-2]
            earlier, later = plans[earlier_i], plans[later_i]
            earlier["lines"] = earlier["lines"] + later["lines"]
            earlier["subjects"] = list(dict.fromkeys(earlier["subjects"] + later["subjects"]))
            del plans[later_i]
            continue
        # No more speaking shots to merge into one another: drop a trailing
        # wordless shot (a reaction close_up) instead, never the scene's own
        # first shot (its opening/establishing coverage).
        if len(plans) <= 1:
            break
        removable = [i for i in range(1, len(plans)) if not plans[i]["lines"]]
        if removable:
            del plans[removable[-1]]
        else:
            del plans[-1]

    while len(plans) < lo:
        if chars:
            tag = f"@{chars[0]}"
            plans.append(_plan(
                framing="close_up", camera_motion=default_motion, modifiers=mods,
                action=f"{tag} reacts silently.", subjects=[tag], lines=[],
            ))
        else:
            plans.append(_plan(
                framing="wide_establishing", camera_motion=default_motion, modifiers=mods,
                action=f"Wide view of {place_tag}.", subjects=[place_tag], lines=[],
            ))

    return plans


# ---------------------------------------------------------------- rule pass

def _flatten(plans_by_scene) -> list:
    """``[(scene_index, shot_index, scene, plan), ...]`` across every scene,
    in order."""
    out = []
    for si, (scene, plans) in enumerate(plans_by_scene):
        for pi, plan in enumerate(plans):
            out.append((si, pi, scene, plan))
    return out


def _is_protected(shot_index, plan) -> bool:
    """Never changed by rule (a): an insert_prop shot, or a scene's own
    opening wide_establishing shot."""
    return plan["framing"] == "insert_prop" or (shot_index == 0 and plan["framing"] == "wide_establishing")


def _movable(shot_index, plan) -> bool:
    """Whether rule (a) may change *plan*'s framing at all."""
    return not _is_protected(shot_index, plan) and plan["framing"] in _FRAMING_SUBSTITUTES


def _apply_no_repeat_framing(plans_by_scene, notes, pinned=frozenset()) -> bool:
    """One pass of rule (a); returns whether anything changed. *pinned*
    (``{(scene_id, shot_index)}``: a shot kept from the previous storyboard,
    its keyframe and clip with it) moves only when the other shot of the
    pair cannot: with nothing pinned, the later shot moves, else the earlier
    one, as always."""
    items = _flatten(plans_by_scene)
    changed = False
    for k in range(1, len(items)):
        _si, pi, scene, plan = items[k]
        _psi, ppi, pscene, pplan = items[k - 1]
        if plan["framing"] != pplan["framing"]:
            continue
        later = _movable(pi, plan)
        earlier = _movable(ppi, pplan)
        if pinned and later and earlier:
            later_pinned = (scene["scene_id"], pi) in pinned
            earlier_pinned = (pscene["scene_id"], ppi) in pinned
            if later_pinned and not earlier_pinned:
                later = False
        if later:
            new_framing = _FRAMING_SUBSTITUTES[plan["framing"]]
            notes.append(
                f"rule_pass: scene {scene['scene_id']} shot {pi + 1}: framing changed "
                f"{plan['framing']!r} -> {new_framing!r} (repeated the previous shot's framing)"
            )
            plan["framing"] = new_framing
            changed = True
        elif earlier:
            new_framing = _FRAMING_SUBSTITUTES[pplan["framing"]]
            notes.append(
                f"rule_pass: scene {pscene['scene_id']} shot {ppi + 1}: framing changed "
                f"{pplan['framing']!r} -> {new_framing!r} (repeated the next shot's framing)"
            )
            pplan["framing"] = new_framing
            changed = True
        else:
            # Both shots are protected (or have no substitute): the repeat
            # is left in place -- reported, never silently dropped.
            note = (
                f"rule_pass: scene {pscene['scene_id']} shot {ppi + 1} and scene {scene['scene_id']} shot "
                f"{pi + 1}: both share framing {plan['framing']!r} and neither can be changed "
                "(protected or no substitute); left unresolved"
            )
            if note not in notes:
                notes.append(note)
    return changed


def _apply_close_up_window(plans_by_scene, notes, pinned=frozenset()) -> None:
    """Rule (b): every window of 3 consecutive scenes has a close_up or
    extreme_close_up somewhere in it; otherwise the last non-insert_prop shot
    of the window's third scene becomes one. With *pinned* shots
    (:func:`_apply_no_repeat_framing`'s): the last such shot not pinned, of
    the third scene, else the second, else the first; every one pinned, the
    third scene's as always."""
    n = len(plans_by_scene)
    for start in range(0, max(0, n - 2)):
        window = plans_by_scene[start:start + 3]
        has_tight = any(p["framing"] in _TIGHT_FRAMINGS for _scene, plans in window for p in plans)
        if has_tight:
            continue
        third_scene, third_plans = window[2]
        candidates = [p for p in third_plans if p["framing"] != "insert_prop"]
        if not candidates:
            continue
        target, which = candidates[-1], "its last shot"
        if pinned:
            free = [(scene, plan) for scene, plans in reversed(window)
                    for index, plan in reversed(list(enumerate(plans)))
                    if plan["framing"] != "insert_prop" and (scene["scene_id"], index) not in pinned]
            if free and free[0][1] is not target:
                (third_scene, target), which = free[0], "a shot not kept from the previous storyboard"
        target["framing"] = "close_up"
        has_char_tag = any(t.startswith("@") for t in target["subjects"])
        if not has_char_tag and third_scene["characters"]:
            target["subjects"] = list(target["subjects"]) + [f"@{third_scene['characters'][0]}"]
        notes.append(
            f"rule_pass: scene {third_scene['scene_id']}: no close_up/extreme_close_up in this 3-scene "
            f"window, {which} was forced to close_up"
        )


def _apply_motion_precedence(plans_by_scene, style_lock, notes, *, v2=False) -> None:
    """Rule (c): each plan's camera_motion becomes whatever motion_for's
    precedence would pick for it -- the "push-in on peaks" rule (on *v2*,
    the plan's own motion before the scene function's, DEC-252)."""
    for scene, plans in plans_by_scene:
        for plan in plans:
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock, v2=v2)
            if motion["type"] != plan["camera_motion"]:
                notes.append(
                    f"rule_pass: scene {scene['scene_id']}: camera motion changed "
                    f"{plan['camera_motion']!r} -> {motion['type']!r}"
                )
                plan["camera_motion"] = motion["type"]


def rule_pass(plans_by_scene, style_lock, *, v2=False, pinned=frozenset()) -> tuple:
    """The cross-scene rules applied to every scene's shot plans, on both the
    T1 and the fast path (spec 5): (a) no two consecutive shots in the whole
    episode share a framing (never changing an insert_prop shot or a scene's
    own opening wide_establishing -- the earlier shot moves instead); (b)
    every window of 3 consecutive scenes has a close_up or extreme_close_up,
    re-checking (a) afterwards; (c) each shot's camera motion follows
    :func:`motion_for`'s precedence (*v2*: its own). *pinned*
    (``{(scene_id, shot_index)}``): the shots a re-plan keeps from the
    previous storyboard (:func:`build_storyboard`'s *keep*) -- (a) and (b)
    move another shot instead whenever one can be moved, so a kept keyframe
    stays the shot it was drawn for; empty, every rule is as it always was.
    Returns ``(plans_by_scene, notes)``, a new structure -- *plans_by_scene*
    itself and its plan dicts are not mutated."""
    plans_by_scene = [(scene, [dict(p) for p in plans]) for scene, plans in plans_by_scene]
    notes = []

    guard = len(_flatten(plans_by_scene)) + 2
    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes, pinned):
            break

    _apply_close_up_window(plans_by_scene, notes, pinned)

    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes, pinned):
            break

    _apply_motion_precedence(plans_by_scene, style_lock, notes, v2=v2)

    return plans_by_scene, notes


REPEATED_FRAMING = "two shots in a row would repeat a framing"
NO_CLOSE_UP = "three scenes in a row would hold no close-up"


def rule_moves(storyboard, script, style_lock) -> dict:
    """``{shot_id: (framing, framing the rules give it, why)}``: the shots
    :func:`rule_pass` would move were *storyboard* built again from its own
    plans (:func:`plans_from_storyboard`) -- *why* is
    :data:`REPEATED_FRAMING` (rule a) or :data:`NO_CLOSE_UP` (rule b). A
    storyboard that keeps the rules gives ``{}``. An edit that makes one
    breaks them (phase 5 stage 7: refused, never a neighbour moved
    silently). Pure; *storyboard* is not changed."""
    plans = plans_from_storyboard(storyboard, script)
    ordered = [(scene, plans[scene["scene_id"]]) for scene in script["scenes"] if scene["scene_id"] in plans]
    by_scene: dict = {}
    for shot in storyboard["shots"]:
        by_scene.setdefault(shot["scene_id"], []).append(shot["shot_id"])
    ids = [shot_id for scene, _plans in ordered for shot_id in by_scene[scene["scene_id"]]]
    start = [plan["framing"] for _scene, scene_plans in ordered for plan in scene_plans]

    repeats = [(scene, [dict(plan) for plan in scene_plans]) for scene, scene_plans in ordered]
    for _ in range(len(start) + 2):
        if not _apply_no_repeat_framing(repeats, []):
            break
    after_a = [plan["framing"] for _scene, scene_plans in repeats for plan in scene_plans]
    moved, _notes = rule_pass(ordered, style_lock)
    end = [plan["framing"] for _scene, scene_plans in moved for plan in scene_plans]
    return {ids[i]: (start[i], end[i], REPEATED_FRAMING if after_a[i] != start[i] else NO_CLOSE_UP)
            for i in range(len(start)) if end[i] != start[i]}


# ------------------------------------------------------------- storyboard

def _collect_resolved_from(resolved_from, subject_tags, scene, entities) -> None:
    for tag in subject_tags:
        kind, eid, _variant = parse_tag(tag)
        doc = entities.get(_KIND_TO_ENTITY_KEY[kind], {}).get(eid)
        if doc is not None:
            resolved_from[eid] = doc["updated_at"]
    place_doc = entities.get("places", {}).get(scene["place_id"])
    if place_doc is not None:
        resolved_from[scene["place_id"]] = place_doc["updated_at"]


def _time_shots(shots, transitions, script, *, template, language, style_lock, skip=(), whole_frames) -> list:
    """Every shot's ``duration_s``, in place, the one way a storyboard is
    timed: each scene's EPISODE-LEVEL timing -- :func:`timing.episode_pass`
    over the whole *script* beside these very shots and transitions, the
    same call the script's stored ``timing`` comes from
    (``episode_common.retime``), window pass and shot floor included --
    split across the scene's shots by :func:`timing.allocate_shots`. So a
    scene's shots always sum to ``script.timing.scenes[sid].duration_s``.
    *whole_frames*: the episode timing and the split are in whole frames
    (DEC-142 as amended by phase 5 stage 6); off, a storyboard timed before
    is kept in its old timing. A scene in *skip* keeps its shots' durations. Returns the notes (a scene
    whose shots sit at the floor length -- never expected: the shot floor
    already grew every scene to what its shots need)."""
    notes = []
    _timing, scene_timings = timing.episode_pass(
        script, template, language, style_lock=style_lock, storyboard={"shots": shots, "transitions": transitions},
        whole_frames=whole_frames)
    shots_by_scene: dict = {}
    for shot in shots:
        shots_by_scene.setdefault(shot["scene_id"], []).append(shot)

    for scene in script["scenes"]:
        sid = scene["scene_id"]
        if sid in skip or sid not in shots_by_scene:
            continue
        scene_t = scene_timings[sid]
        scene_shots = shots_by_scene[sid]
        scene_plans_for_alloc = [{"lines": shot["lines"]} for shot in scene_shots]
        durations, extra_hold = timing.allocate_shots(scene, scene_t, scene_plans_for_alloc, template,
                                                      whole_frames=whole_frames)
        for shot, duration in zip(scene_shots, durations):
            shot["duration_s"] = duration
        if extra_hold > 0:
            notes.append(f"build_storyboard: scene {sid}: extra_hold_s={extra_hold} (shots at the floor length)")
    return notes


def _speech_fields(shot, plan, kept) -> None:
    """A native board's shot (plan 22): ``speaks`` and ``clip_s`` from its
    plan, its length its clip's -- or, a kept shot whose clip is current
    (plan 27), the ``clip_s`` it was made at and the length its take gave it
    (``native_speech.shot_seconds``): nothing made turns stale when the
    window of lengths moves; only a shot without a clip takes the plan's."""
    clip_s = int(plan["clip_s"])
    shot["speaks"] = bool(plan.get("speaks"))
    shot["clip_s"] = clip_s
    shot["duration_s"] = float(clip_s)
    if plan.get("speakers"):
        # Plan 27 stage 3: an exchange's speakers, in the order they first speak.
        shot["speakers"] = list(plan["speakers"])
    clip = ((kept or {}).get("assets") or {}).get("clip") or {}
    if kept is not None and clip.get("state") == "current":
        if int(kept.get("clip_s") or 0) > 0:
            shot["clip_s"] = int(kept["clip_s"])
        shot["duration_s"] = float(kept["duration_s"])


def retime_storyboard(storyboard, script, *, template, language, style_lock) -> bool:
    """*storyboard*'s shot durations recomputed in place from *script*'s
    lines as they are now (measured, or estimated), exactly as
    :func:`build_storyboard` times the same plans -- and nothing else moves:
    the plans, prompts, ids, transitions, the scenes' entries, the revision
    and the approval stay as they are (a duration is derived, like the
    script's ``timing``). A scene planned from another revision of its
    scene, or marked stale, keeps its durations until it is planned again:
    its shots may name lines it no longer has. Returns whether any duration
    changed. The other scenes follow the episode-level timing the script is
    stored with (:func:`_time_shots`): a change in one scene can move
    another one's durations through the episode's window pass.

    A scene marked ``retime_only`` (phase 5 stage 7: a text-only edit kept
    its plan, and its entry follows the new revision) is no stale one: it is
    re-timed with the rest, in place, and its mark is dropped once every one
    of its lines is measured for its words (returned as a change, so the
    caller writes it).

    Whole frames (phase 5 stage 6): a storyboard flagged ``whole_frames`` is
    re-timed in whole frames. One timed before keeps its old timing while
    any of its shots keeps its duration (a skipped scene's shots would
    disagree with a whole-frame episode timing at render); its first re-time
    that re-cuts every shot switches it to whole frames and sets the flag
    (returned as a change, so the caller writes it -- and must re-time the
    script beside it again, ``episode_common.retime``)."""
    if native_speech.is_native_board(storyboard):
        return _retime_native(storyboard, script, language=language)
    scenes_in_order = [scene for scene in script["scenes"] if scene["scene_id"] in storyboard["scenes"]]
    line_ids = {scene["scene_id"]: {line["line_id"] for line in scene["lines"]} for scene in scenes_in_order}
    skip = {scene["scene_id"] for scene in script["scenes"] if scene["scene_id"] not in storyboard["scenes"]}
    for scene in scenes_in_order:
        entry = storyboard["scenes"][scene["scene_id"]]
        if entry.get("stale") or entry.get("script_rev") != scene["rev"]:
            skip.add(scene["scene_id"])
    for shot in storyboard["shots"]:
        known = line_ids.get(shot["scene_id"])
        if known is not None and any(line_id not in known for line_id in shot["lines"]):
            skip.add(shot["scene_id"])
    if _non_cut_inside_a_scene(storyboard):
        # Transitions that break spec 6.3: the episode cannot be timed
        # (timing._boundary_from_storyboard refuses them). Left as it is:
        # the storyboard's own validation (schemas.storyboard_errors)
        # refuses it too, and a PATCH that made it is refused whole.
        return False
    before = [shot["duration_s"] for shot in storyboard["shots"]]
    whole_frames = timing.board_whole_frames(storyboard) or not any(
        shot["scene_id"] in skip for shot in storyboard["shots"])
    _time_shots(storyboard["shots"], storyboard["transitions"], script, template=template,
                language=language, style_lock=style_lock, skip=skip, whole_frames=whole_frames)
    changed = [shot["duration_s"] for shot in storyboard["shots"]] != before
    if whole_frames and not storyboard.get("whole_frames"):
        storyboard["whole_frames"] = True
        changed = True
    for scene in scenes_in_order:
        entry = storyboard["scenes"][scene["scene_id"]]
        if entry.get("retime_only") and scene["scene_id"] not in skip and all(
                _measured_for_its_words(line) for line in scene["lines"]):
            del entry["retime_only"]
            changed = True
    return changed


def _retime_native(storyboard, script, *, language) -> bool:
    """:func:`retime_storyboard` of a native board (plan 22): its shots last
    what their clips last, so nothing moves -- but a narrator's silent shot
    whose clip is not bought yet, sized to its narration
    (``native_speech.narrator_clip_s``), follows the narration as it is now
    measured. Returns whether one moved."""
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    changed = False
    for shot in storyboard["shots"]:
        if shot.get("speaks") or not shot["lines"]:
            continue
        if ((shot.get("assets") or {}).get("clip") or {}).get("state") == "current":
            continue
        narration = [lines[line_id] for line_id in shot["lines"] if line_id in lines]
        if not narration:
            continue
        seconds = sum(timing.line_duration(line, language)[0] for line in narration)
        clip_s = native_speech.narrator_clip_s(seconds)
        if clip_s != shot.get("clip_s") or float(shot["duration_s"]) != float(clip_s):
            shot["clip_s"], shot["duration_s"] = clip_s, float(clip_s)
            changed = True
    return changed


def _measured_for_its_words(line) -> bool:
    """Whether *line*'s timing is a measurement of its words as they are now
    (not an estimate, not a take of other words): the part of
    ``voice_lines.is_measured`` a pure function can see."""
    current = line.get("timing") or {}
    return current.get("source") not in (None, "estimated") and current.get("text_hash") == timing.text_hash(
        line["text"])


def _non_cut_inside_a_scene(storyboard) -> bool:
    """Whether a transition other than ``cut`` joins two shots of the same
    scene (spec 6.3; ``schemas.storyboard_errors`` names it)."""
    shots = storyboard["shots"]
    same_scene = {prev["shot_id"]: prev["scene_id"] == nxt["scene_id"] for prev, nxt in zip(shots, shots[1:])}
    return any(t["type"] != "cut" and same_scene.get(t["after"]) for t in storyboard["transitions"])


def _keep_t1_v2(target, source) -> None:
    """Copy *source*'s T1 v2 fields (phase 7 stage 4: ``clip_motion``,
    ``staging``) onto *target* -- a plan onto its shot, a shot back onto its
    plan. A fast or v1 T1 plan has neither, so its shot is unchanged."""
    if source.get("clip_motion"):
        target["clip_motion"] = source["clip_motion"]
    if isinstance(source.get("staging"), list):
        target["staging"] = [dict(entry) for entry in source["staging"]]


def continues_scene(shots_in_order, index) -> bool:
    """Whether shot *index* of *shots_in_order* (storyboard shots, or plans
    with their ``scene_id``) follows a shot of its own scene: the shots a v2
    story resolves with the previous keyframe as a reference (phase 8 stage
    B, :data:`CONTINUITY_REFERENCE`). The first shot of a scene never does."""
    return index > 0 and shots_in_order[index - 1]["scene_id"] == shots_in_order[index]["scene_id"]


def previous_plan(shots_in_order, index):
    """The plan (:func:`plan_of`, v2) of the shot before shot *index* when
    it is of the same scene (:func:`continues_scene`), else None: what
    :func:`resolve_shot` says changed since it (stage F2)."""
    if not continues_scene(shots_in_order, index):
        return None
    return plan_of(shots_in_order[index - 1], v2=True)


def plan_of(shot, *, v2=False) -> dict:
    """The plan a storyboard *shot* resolves from (:func:`resolve_shot`):
    its framing, action and subjects; on *v2* its lines, camera motion,
    modifiers and T1 v2's ``clip_motion`` and ``staging`` too
    (:func:`refresh_prompts`' own)."""
    plan = {"framing": shot["framing"], "action": shot["action"], "subjects": shot["subject_tags"]}
    if v2:
        plan.update(lines=list(shot["lines"]), camera_motion=shot["camera_motion"],
                    modifiers=list(shot["modifiers"]))
        _keep_t1_v2(plan, shot)
        if shot.get("variants"):
            # Plan 23 stage D5: the appearance variants the shot's characters wear.
            plan["variants"] = dict(shot["variants"])
    return plan


def name_map(entities, *, v2=False) -> dict:
    """``{name: neutral word}`` of every entity name an image prompt never
    says: :func:`_v2_name_map` on a v2 story (a descriptive name is kept, so
    a handle is never garbled), else :func:`_story_name_map` -- what
    :func:`resolve_shot` strips from the action."""
    return _v2_name_map(entities) if v2 else _story_name_map(entities)


def build_storyboard(script, plans, sources, *, entities, style_lock, template, language, consistency_mode,
                     now, previous=None, v2=False, shots_per_scene=None, ledger=None, budgets=None, keep=None,
                     reserved=(), timing_mode=None) -> tuple:
    """*plans* (``{scene_id: [plan, ...]}``) and *sources* (``{scene_id:
    "t1"|"fast"}``) resolved into a complete ``storyboard_v1`` document:
    scenes in the script's own order (only the ones *plans* covers), the
    cross-scene :func:`rule_pass`, stable shot ids (below), every shot resolved
    (:func:`resolve_shot`), durations from the episode-level
    :func:`timing.episode_pass` + :func:`timing.allocate_shots`
    (:func:`_time_shots`) in whole frames (the document says so:
    ``whole_frames: true``), and transitions from
    :func:`timing.plan_transitions`. Raises ``ValueError`` (never writes a
    document that fails its own validation -- a bug, not a user error) when
    the result does not pass ``schemas.storyboard_errors`` and
    ``schemas.storyboard_context_errors``. Returns ``(document, notes)``.
    *v2*: every shot is resolved layered (:func:`resolve_shot`).
    *shots_per_scene*: the episode's effective ``[lo, hi]``
    (``EpisodeContext.episode_defaults``: the template's over the style's,
    phase 7 stage 4); None reads the style lock's own, as before. A plan's
    ``clip_motion`` and ``staging`` (T1 v2's) are kept on its shot.
    *ledger*: :func:`resolve_shot`'s (v2: wardrobe sets and holders).
    On *v2* every shot but the first of its scene carries the previous
    keyframe of the scene as a reference (phase 8 stage B). *budgets*:
    :func:`resolve_shot`'s (v2, stage F2); a shot whose prompt cannot fit
    raises :class:`PromptOverBudget` naming it and the link.

    Shot ids are stable keys (walk follow-up F5): a shot's position on the
    timeline is its place in ``shots`` (and its ``order``), never its id.
    *keep* (``{scene_id: [shot_id | None, ...]}``, one entry per plan of the
    scene; only with *previous*) names the shot of *previous* each plan IS
    -- a plan that was not planned again: that shot keeps its id, its
    ``assets``, ``prompt_override``, ``keep_still`` and every other key the
    build does not derive, while its plan-derived fields (framing, prompts,
    references, motion, duration, order) are resolved again exactly as a
    new shot's are (unchanged when nothing around it changed; a prompt that
    moved makes its image outdated, never lost). :func:`rule_pass` moves
    another shot rather than a kept one whenever it can. Every other shot is
    new: its id is the next free number after the highest id of *previous*
    and of *reserved* (ids other per-shot records still name), never one
    used before, given in timeline order. With no *previous* and nothing
    reserved, the ids are ``sh01..`` in order, as they always were.

    *timing_mode* ``"native_speech"`` (plan 22, :func:`speech_shot_plan`'s
    plans): every shot carries ``speaks`` and ``clip_s`` from its plan and
    lasts its clip (``duration_s == clip_s``; a kept shot whose clip is
    current keeps the length its take gave it), every boundary is a cut,
    and the board says ``timing_mode`` -- it is timed by
    ``timing.native_pass``, never cut to the episode's window. Absent:
    everything above, byte for byte.
    """
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    scenes_in_order = [scene for scene in script["scenes"] if scene["scene_id"] in plans]
    previous_shots = {shot["shot_id"]: shot for shot in previous["shots"]} if previous is not None else {}
    carried = {}
    for sid, ids in (keep or {}).items():
        for index, shot_id in enumerate(ids or ()):
            shot = previous_shots.get(shot_id)
            if shot is not None and shot["scene_id"] == sid and index < len(plans.get(sid) or ()):
                carried[(sid, index)] = shot
    next_number = max((shot_number(shot_id) for shot_id in [*previous_shots, *reserved]
                       if is_shot_id(shot_id)), default=0) + 1

    plans_by_scene = [(scene, plans[scene["scene_id"]]) for scene in scenes_in_order]
    plans_by_scene, notes = rule_pass(plans_by_scene, style_lock, v2=v2, pinned=frozenset(carried))

    shots = []
    resolved_from = {}
    order = 0
    for scene, scene_plans in plans_by_scene:
        for index, plan in enumerate(scene_plans):
            order += 1
            kept = carried.get((scene["scene_id"], index))
            if kept is not None:
                shot_id = kept["shot_id"]
            else:
                shot_id = shot_id_for(next_number)
                next_number += 1
            line_ids = [scene["lines"][n - 1]["line_id"] for n in plan["lines"]]
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock, v2=v2)
            # Plan 23 stage D5: a kept shot wears the variants it has (the human's override kept);
            # a new one inherits its scene's states. Neither: the plan as it always was.
            variants = (kept["variants"] if kept is not None and "variants" in kept
                        else shot_variants(scene, plan["subjects"]))
            if variants:
                plan = dict(plan, variants=dict(variants))
            try:
                resolved = resolve_shot(plan, scene=scene, entities=entities, style_lock=style_lock,
                                        consistency_mode=consistency_mode, v2=v2, ledger=ledger,
                                        continuity=v2 and index > 0, budgets=budgets,
                                        previous_plan=scene_plans[index - 1] if v2 and index > 0 else None)
            except PromptOverBudget as exc:
                raise _named(exc, shot_id, budgets) from None
            _collect_resolved_from(resolved_from, plan["subjects"], scene, entities)
            shot = {
                "shot_id": shot_id, "scene_id": scene["scene_id"], "order": order,
                "framing": plan["framing"], "camera_motion": motion["type"],
                "modifiers": list(plan["modifiers"]), "subject_tags": list(plan["subjects"]),
                "action": plan["action"], "lines": line_ids,
                "image_prompt": resolved["image_prompt"], "video_action": resolved["video_action"],
                "negative_prompt": resolved["negative_prompt"],
                "prompt_override": None, "reference_images": resolved["reference_images"],
                "consistency": resolved["consistency"], "duration_s": 0.0, "keep_still": False,
                "motion": motion, "video_prompt": resolved.get("video_prompt"),
                "assets": {"image": None, "video": None, "seed": None, "provider": None, "approved": False},
            }
            if "prompt_layout" in resolved:
                shot["prompt_layout"] = resolved["prompt_layout"]
            _keep_t1_v2(shot, plan)
            if variants:
                shot["variants"] = dict(variants)
            if timing_mode is not None:
                _speech_fields(shot, plan, kept)
            if kept is not None:
                shot = _carried_shot(kept, shot)
                if variants and "variants" not in shot:
                    shot["variants"] = dict(variants)
            shots.append(shot)

    if timing_mode is not None:
        transitions = timing.plan_transitions(shots, scenes_by_id, template, cuts_only=True)
    else:
        transitions = timing.plan_transitions(shots, scenes_by_id, template)
        notes.extend(_time_shots(shots, transitions, script, template=template, language=language,
                                 style_lock=style_lock, whole_frames=True))

    doc = {
        "$schema": schemas.STORYBOARD_SCHEMA_NAME,
        "ep": script["ep"],
        "shots": shots,
        "transitions": transitions,
        "scenes": {
            scene["scene_id"]: {"source": sources[scene["scene_id"]], "script_rev": scene["rev"], "stale": False}
            for scene in scenes_in_order
        },
        "resolved_from": resolved_from,
        # Timed in whole frames (phase 5 stage 6): the timeline and every
        # re-time read it (timing.board_whole_frames).
        "whole_frames": True,
        "approved_at": None,
        "rev": (previous["rev"] + 1) if previous is not None else 1,
        "created_at": previous["created_at"] if previous is not None else now,
        "updated_at": now,
    }
    if timing_mode is not None:
        doc["timing_mode"] = timing_mode

    if shots_per_scene is None:
        shots_per_scene = style_lock["episode_defaults"]["shots_per_scene"]
    errors = schemas.storyboard_errors(doc, min_shot_s=template["min_shot_s"])
    errors += schemas.storyboard_context_errors(doc, script, shots_per_scene=shots_per_scene)
    if errors:
        raise ValueError(f"build_storyboard produced an invalid storyboard: {'; '.join(errors)}")

    return doc, notes


# The keys of a storyboard shot that are the shot's own record, never derived
# from its plan: a shot kept by a re-plan (build_storyboard's *keep*) carries
# them as they are -- with every key the build does not write at all.
_SHOT_RECORD_KEYS = ("shot_id", "scene_id", "prompt_override", "keep_still", "assets", "variants")
# The derived keys a build writes only sometimes: dropped from a kept shot
# when the build no longer writes them.
_SHOT_OPTIONAL_DERIVED_KEYS = ("prompt_layout", "clip_motion", "staging", "speakers")


def _carried_shot(previous_shot, built) -> dict:
    """*previous_shot* (a kept shot of the previous storyboard, not changed)
    with *built*'s derived fields over it: its own record
    (:data:`_SHOT_RECORD_KEYS`, and any key the build does not write) kept,
    in its own key order."""
    shot = copy.deepcopy(previous_shot)
    for key in _SHOT_OPTIONAL_DERIVED_KEYS:
        if key not in built:
            shot.pop(key, None)
    shot.update({key: value for key, value in built.items() if key not in _SHOT_RECORD_KEYS})
    return shot


SHOT_NUMBER_MAX = 999


def is_shot_id(value) -> bool:
    """Whether *value* is a shot id (``schemas.SHOT_ID_PATTERN``)."""
    return isinstance(value, str) and re.fullmatch(schemas.SHOT_ID_PATTERN, value) is not None


def shot_number(shot_id) -> int:
    """The number of shot id *shot_id* (``sh07`` -> 7): a stable key, never a
    position on the timeline (the shot's place in ``shots`` is)."""
    return int(shot_id[2:])


def shot_id_for(number) -> str:
    """The shot id of *number* (7 -> ``sh07``, 120 -> ``sh120``)."""
    if not 1 <= number <= SHOT_NUMBER_MAX:
        raise ValueError(f"build_storyboard ran out of shot ids: sh{number:02d} is past sh{SHOT_NUMBER_MAX}")
    return f"sh{number:02d}"


def shot_ids_phrase(shots) -> str:
    """The ids of *shots* (storyboard shots, in order) for a sentence:
    ``sh01 to sh12`` when they are ``sh01..`` in order (every storyboard
    built before ids were stable keys, and any never re-planned) -- the
    words these messages always had -- else each run of three or more
    consecutive numbers that way, comma-separated, in timeline order
    (``sh01 to sh04, sh13, sh14, sh07 to sh12``)."""
    ids = [shot["shot_id"] for shot in shots]
    if ids == [f"sh{n:02d}" for n in range(1, len(ids) + 1)]:
        return f"sh01 to sh{len(ids):02d}"
    runs = []
    for shot_id in ids:
        if runs and shot_number(shot_id) == shot_number(runs[-1][-1]) + 1:
            runs[-1].append(shot_id)
        else:
            runs.append([shot_id])
    parts = []
    for run in runs:
        parts.extend([f"{run[0]} to {run[-1]}"] if len(run) > 2 else run)
    return ", ".join(parts)


def _named(exc, shot_id, budgets) -> PromptOverBudget:
    """*exc* (:class:`PromptOverBudget`) naming *shot_id* and the link its
    kind's budget came from (*budgets*; None: no link known)."""
    link = None
    if budgets is not None:
        link = budgets.image_link if exc.kind == "keyframe" else budgets.video_link
    return exc.named(shot_id, link)


def refresh_prompts(storyboard, script, *, entities, style_lock, consistency_mode, v2=False, ledger=None,
                    budgets=None) -> dict:
    """*storyboard* with every shot's ``image_prompt``/``video_action``/
    ``negative_prompt``/``reference_images``/``consistency`` and the
    document's ``resolved_from`` re-resolved from *entities* as they are now
    -- plans (framing, camera motion, modifiers, action, subject_tags,
    lines), durations, motion and transitions are left exactly as they were
    (used when an entity changes after the storyboard was built). *v2*:
    resolved layered, ``video_prompt`` and ``prompt_layout`` re-written too,
    every shot but a scene's first with its continuity reference (phase 8
    stage B); *ledger* and *budgets* as :func:`resolve_shot`'s (a shot whose
    prompt cannot fit raises :class:`PromptOverBudget` naming it)."""
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    resolved_from: dict = {}
    new_shots = []
    for index, shot in enumerate(storyboard["shots"]):
        scene = scenes_by_id[shot["scene_id"]]
        plan = plan_of(shot, v2=v2)
        try:
            resolved = resolve_shot(plan, scene=scene, entities=entities, style_lock=style_lock,
                                    consistency_mode=consistency_mode, v2=v2, ledger=ledger,
                                    continuity=v2 and continues_scene(storyboard["shots"], index), budgets=budgets,
                                    previous_plan=previous_plan(storyboard["shots"], index) if v2 else None)
        except PromptOverBudget as exc:
            raise _named(exc, shot["shot_id"], budgets) from None
        _collect_resolved_from(resolved_from, shot["subject_tags"], scene, entities)
        new_shot = dict(shot)
        new_shot["image_prompt"] = resolved["image_prompt"]
        new_shot["video_action"] = resolved["video_action"]
        new_shot["negative_prompt"] = resolved["negative_prompt"]
        new_shot["reference_images"] = resolved["reference_images"]
        new_shot["consistency"] = resolved["consistency"]
        if v2:
            new_shot["video_prompt"] = resolved["video_prompt"]
            new_shot["prompt_layout"] = resolved["prompt_layout"]
        new_shots.append(new_shot)

    new_doc = dict(storyboard)
    new_doc["shots"] = new_shots
    new_doc["resolved_from"] = resolved_from
    return new_doc


def resolve_stored(shot, *, script, storyboard, entities, style_lock, consistency_mode, ledger=None, budgets=None,
                   continuity=None) -> dict:
    """A stored v2 *shot* resolved again, as :func:`refresh_prompts` resolves
    it -- its scene from *script*, its plan from the shot itself
    (:func:`plan_of`), the plan before it from *storyboard*'s order (None: no
    "since" layer) -- to *budgets* (``prompting.Budgets``). What a request
    re-fits its prompt with when the stored one cannot be sent as it is
    (DEC-249): with a note at its tail, to the room the note leaves, so the
    context layers make room and the note is kept whole; built to another
    link's budget, to the link's own. *continuity*: the previous keyframe
    among the references (None: as the stored shot has it). ``KeyError``
    for a scene *script* no longer has; :class:`PromptOverBudget` naming the
    shot when even the ladder's last rung is over."""
    scene = {scene["scene_id"]: scene for scene in script["scenes"]}[shot["scene_id"]]
    previous = None
    if storyboard is not None:
        ordered = storyboard["shots"]
        index = next((i for i, item in enumerate(ordered) if item["shot_id"] == shot["shot_id"]), None)
        previous = previous_plan(ordered, index) if index is not None else None
    if continuity is None:
        continuity = CONTINUITY_REFERENCE in (shot.get("reference_images") or ())
    try:
        return resolve_shot(plan_of(shot, v2=True), scene=scene, entities=entities, style_lock=style_lock,
                            consistency_mode=consistency_mode, v2=True, ledger=ledger, continuity=continuity,
                            budgets=budgets, previous_plan=previous)
    except PromptOverBudget as exc:
        raise _named(exc, shot["shot_id"], budgets) from None


def plans_from_storyboard(storyboard, script) -> dict:
    """``{scene_id: [plan, ...]}``: the plans a storyboard was built from,
    read back from its shots (the reverse of :func:`build_storyboard`'s
    resolution) so a step can rebuild the storyboard with some scenes
    re-planned and every other scene kept as it is. Each shot's line ids
    become the 1-based numbers of its scene's lines as they are now; a line
    id the scene no longer has is left out (its scene is stale, and the
    caller knows it), and a shot of a scene the script no longer has is
    dropped. The framings and motions are the rule pass's own (running the
    rule pass on them again changes nothing that did not change around
    them)."""
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    plans: dict = {}
    for shot in storyboard["shots"]:
        scene = scenes_by_id.get(shot["scene_id"])
        if scene is None:
            continue
        numbers = {line["line_id"]: n for n, line in enumerate(scene["lines"], start=1)}
        plan = _plan(
            framing=shot["framing"], camera_motion=shot["camera_motion"], modifiers=list(shot["modifiers"]),
            action=shot["action"], subjects=list(shot["subject_tags"]),
            lines=[numbers[line_id] for line_id in shot["lines"] if line_id in numbers],
        )
        _keep_t1_v2(plan, shot)
        plans.setdefault(scene["scene_id"], []).append(plan)
    return plans


# ================================================= plan 22 (native speech)

class SpeechLineTooLong(ValueError):
    """A character line no clip length can speak (``native_speech.line_refusal``)."""


def speech_line_refusal(script, lengths=native_speech.SPEECH_LENGTHS):
    """Why *script* cannot be planned as speaking clips -- each character
    line one clip of at most the longest of *lengths* -- naming every such
    line and the fix, or None (the storyboard step's check before any call)."""
    refused = [native_speech.line_refusal(line, lengths) for scene in script["scenes"] for line in scene["lines"]
               if line["speaker"] != "narrator"]
    refused = [reason for reason in refused if reason]
    if not refused:
        return None
    return "; ".join(refused)


def _listener(scene, index, speaker):
    """Who the character speaking line *index* of *scene* looks at: the
    speaker of the line before when it is another character, else of the
    line after, else the scene's first other character; None when alone."""
    lines = scene["lines"]
    for other in (lines[index - 1] if index > 0 else None, lines[index + 1] if index + 1 < len(lines) else None):
        if other is not None and other["speaker"] not in ("narrator", speaker):
            return other["speaker"]
    return next((cid for cid in scene["characters"] if cid != speaker), None)


# Framings that cannot show a speaker's face speaking: replaced on a speaking shot.
_SPEECH_FRAMING_OVER = {"wide_establishing": "medium_two_shot", "insert_prop": "medium_single"}


def planned_line_entries(scene, notes=None) -> list:
    """The stored line plan's entry (``timing.scene_plan``'s ``lines[i]``;
    plan 24 stage 4, D-2) for each of *scene*'s written lines, in order:
    an entry matches its line by kind (narrator or character) and speaker.
    ``[None] * n`` for a scene with no stored plan (today's path, no note);
    for a plan whose line count differs from the written lines, one note
    appended to *notes* and no entry at all; a line whose kind or speaker
    differs from its entry's gets None, with a note."""
    lines = scene["lines"]
    plan = scene.get("line_plan")
    entries = (plan or {}).get("lines") or []
    if not entries:
        return [None] * len(lines)
    sid = scene["scene_id"]
    if len(entries) != len(lines):
        if notes is not None:
            notes.append(f"scene {sid}: its line plan has {len(entries)} line(s) and the script {len(lines)}: "
                         "the plan is ignored, each line is planned by its own words")
        return [None] * len(lines)
    out = []
    for n, (line, entry) in enumerate(zip(lines, entries), 1):
        kind = "narrator" if line["speaker"] == "narrator" else "character"
        if entry.get("kind") == kind and entry.get("speaker") == line["speaker"]:
            out.append(entry)
            continue
        out.append(None)
        if notes is not None:
            notes.append(f"scene {sid} line {n}: its plan entry is for the {entry.get('kind')} "
                         f"{entry.get('speaker')}, not {line['speaker']}: planned by its own words")
    return out


def _planned_clip(entry):
    """*entry*'s ``clip_s`` as an int, or None (no entry, or a plan made
    for a story that is not native: no clip)."""
    if not entry or entry.get("clip_s") is None:
        return None
    return int(entry["clip_s"])


def planned_exchanges(scene, *, speech_lengths=native_speech.SPEECH_LENGTHS,
                      silent_lengths=native_speech.SPEECH_LENGTHS, links=("", ""), notes=None):
    """The shots *scene*'s stored line plan groups its lines into (plan 27
    stage 3: ``line_plan.shots``, stage 2's exchanges), as ``[(numbers,
    clip_s, speaks)]`` in line order -- *numbers* the 1-based numbers of its
    lines -- or None: no stored shots (a plan made before plan 27, a TTS
    story's), shots that no longer name the written lines in order or by
    their kind (a speaking shot holds character lines only, a silent one
    the narrator's one line), or a stored ``clip_s`` its link does not sell
    (*speech_lengths* for a speaking shot, *silent_lengths* for a silent
    one; *links* their labels ``(speech, silent)``) -- then ONE note in
    *notes* names it and each line is planned on its own, as before."""
    stored = (scene.get("line_plan") or {}).get("shots") or []
    if not stored:
        return None
    lines = scene["lines"]
    sid = scene["scene_id"]
    ids = [line["line_id"] for line in lines]
    named = [line_id for shot in stored for line_id in shot.get("line_ids") or ()]
    if named != ids:
        if notes is not None and len(named) == len(ids):
            # (a plan of another line count is named once already, by planned_line_entries)
            notes.append(f"scene {sid}: its planned shots name the lines {', '.join(named)}, the script holds "
                         f"{', '.join(ids)}: each line is planned on its own")
        return None
    out, position = [], 0
    for shot in stored:
        numbers = list(range(position + 1, position + len(shot["line_ids"]) + 1))
        position += len(numbers)
        speaks = bool(shot.get("speaks"))
        narrated = [lines[n - 1]["speaker"] == "narrator" for n in numbers]
        if (speaks and any(narrated)) or (not speaks and (len(numbers) != 1 or not narrated[0])):
            if notes is not None:
                notes.append(f"scene {sid}: its planned shot of lines {', '.join(shot['line_ids'])} no longer "
                             "matches who speaks them: each line is planned on its own")
            return None
        clip_s = int(shot["clip_s"])
        lengths, link = (speech_lengths, links[0]) if speaks else (silent_lengths, links[1])
        if clip_s not in tuple(lengths or ()):
            if notes is not None:
                notes.append(f"scene {sid}: the stored {clip_s} s plan is not sold on {link or 'its link'}; "
                             "replanned")
            return None
        out.append((numbers, clip_s, speaks))
    return out


def speech_shot_plan(scene, plans, *, language, speech_lengths=native_speech.SPEECH_LENGTHS,
                     silent_lengths=native_speech.SPEECH_LENGTHS, reaction_shots=(0, 1), notes=None,
                     whole_exchanges=True, links=("", "")) -> list:
    """A native-speech story's plans for *scene* (plan 22) from its beat
    *plans* (T1 v2's, or a fast plan; framing, action, camera and staging
    are theirs): one shot a character line, ``speaks: true``, the speaker
    its subject and whom it addresses its secondary subject, planned at the
    smallest of *speech_lengths* that can speak the line
    (``native_speech.speech_clip_s``); a narrator line, a silent shot sized
    to its narration (``native_speech.narrator_clip_s``) under which its
    TTS voice-over plays; up to ``reaction_shots[1]`` of the beat plans with
    no line kept as silent reaction shots of ``native_speech.REACTION_S``;
    a scene with no line at all, one silent shot at its target length.
    Every plan carries ``speaks`` and ``clip_s``. Idempotent: the plans it
    returns, given back, give the same plans. :class:`SpeechLineTooLong`
    for a character line no length can speak.

    Plan 24 stage 4 (D-2): a scene that carries its line plan
    (``planned_line_entries``) plans each line at the plan's ``clip_s`` --
    the shots are the plan -- when the written line still fits it (a
    character line's words within ``native_speech.capacity``, a narration
    within the clip less its lead) and the clip is one of the story's
    lengths; a line that no longer fits keeps the rule above and is
    named in *notes* (a list the caller owns). A scene with no plan is
    planned exactly as before.

    Plan 27 stage 3 (the exchange): a scene whose stored plan groups its
    lines into shots (:func:`planned_exchanges`) plans each planned shot of
    two or more lines as ONE speaking shot -- ``lines`` its line numbers in
    order, ``clip_s`` its planned clip (the smallest length that holds the
    exchange's words when they outgrew it, named in *notes*; split into one
    shot a line, named, when none does), the speaker of its first line its
    subject, the other speakers after it, and ``speakers`` (the character
    ids in the order they first speak). *whole_exchanges* (a scene planned
    now): an exchange is planned whole at the first beat plan naming one of
    its lines; False (a scene whose shots are kept from the board, built
    before): only a beat plan naming every line of an exchange makes it one
    shot, so a board that split it keeps its shots. A one-line planned shot
    is planned exactly as above; *links* (``(speech, silent)`` labels) name
    the link in the note of a stored length it does not sell."""
    lines = scene["lines"]
    entries = planned_line_entries(scene, notes)
    groups = planned_exchanges(scene, speech_lengths=speech_lengths, silent_lengths=silent_lengths, links=links,
                               notes=notes)
    exchange_of = {n: numbers for numbers, _clip, speaks in groups or () if speaks and len(numbers) > 1
                   for n in numbers}
    exchange_clip = {tuple(numbers): clip for numbers, clip, _speaks in groups or ()}
    lo, hi = (list(reaction_shots) + [0, 1])[:2] if reaction_shots else (0, 1)
    place_tags = [tag for plan in plans for tag in plan["subjects"] if not tag.startswith("@")]
    place_tags = list(dict.fromkeys(place_tags)) or [f"#{scene['place_id']}:{scene['time_variant']}"]
    characters = set(scene["characters"])

    def from_plan(plan, **changes):
        out = {key: (list(value) if isinstance(value, list) else value) for key, value in plan.items()
               if key != "speakers"}
        out.update(changes)
        return out

    def speaking(plan, n):
        line = lines[n - 1]
        speaker = line["speaker"]
        if speaker == "narrator":
            seconds = timing.line_duration(line, language)[0]
            clip_s = native_speech.narrator_clip_s(seconds, silent_lengths)
            planned = _planned_clip(entries[n - 1])
            if planned is not None and planned in silent_lengths:
                if planned >= clip_s:
                    clip_s = planned
                elif notes is not None:
                    notes.append(f"scene {scene['scene_id']} line {n}: the narration needs a {clip_s} s clip, "
                                 f"its plan gave {planned} s")
            return from_plan(plan, lines=[n], speaks=False, clip_s=clip_s)
        clip_s = native_speech.speech_clip_s(line["text"], speech_lengths)
        if clip_s is None:
            raise SpeechLineTooLong(native_speech.line_refusal(line, speech_lengths))
        planned = _planned_clip(entries[n - 1])
        if planned is not None and planned in speech_lengths:
            if native_speech.words_of(line["text"]) <= native_speech.capacity(planned):
                clip_s = planned
            elif notes is not None:
                notes.append(f"scene {scene['scene_id']} line {n}: {native_speech.words_of(line['text'])} words "
                             f"do not fit its planned {planned} s clip: planned at {clip_s} s")
        listener = _listener(scene, n - 1, speaker)
        if speaker in characters:
            others = [tag for tag in plan["subjects"] if not tag.startswith("@")] or place_tags
            subjects = [f"@{speaker}"] + ([f"@{listener}"] if listener and listener in characters else []) + others
        else:
            subjects = list(plan["subjects"])
        framing = _SPEECH_FRAMING_OVER.get(plan["framing"], plan["framing"])
        if framing == "medium_two_shot" and len([tag for tag in subjects if tag.startswith("@")]) < 2:
            framing = "medium_single"
        return from_plan(plan, lines=[n], subjects=subjects, framing=framing, speaks=True, clip_s=clip_s)

    def exchange(plan, numbers):
        """Plan 27 stage 3: the planned exchange of lines *numbers* as one speaking shot (a list of plans)."""
        said = [lines[n - 1] for n in numbers]
        words = sum(native_speech.words_of(line["text"]) for line in said)
        clip_s = exchange_clip[tuple(numbers)]
        if words > native_speech.capacity(clip_s):
            longer = [length for length in sorted(speech_lengths) if native_speech.capacity(length) >= words]
            where = f"scene {scene['scene_id']} lines {numbers[0]}-{numbers[-1]}"
            if not longer:
                if notes is not None:
                    notes.append(f"{where}: {words} words fit no clip as one exchange: one shot a line")
                return [speaking(plan, n) for n in numbers]
            if notes is not None:
                notes.append(f"{where}: {words} words do not fit their planned {clip_s} s exchange: "
                             f"planned at {longer[0]} s")
            clip_s = longer[0]
        speakers = list(dict.fromkeys(line["speaker"] for line in said))
        first = speakers[0]
        if first in characters:
            listener = _listener(scene, numbers[0] - 1, first)
            framed = [cid for cid in speakers if cid in characters]
            if listener and listener in characters and listener not in framed:
                framed.append(listener)
            others = [tag for tag in plan["subjects"] if not tag.startswith("@")] or place_tags
            subjects = [f"@{cid}" for cid in framed] + others
        else:
            subjects = list(plan["subjects"])
        framing = _SPEECH_FRAMING_OVER.get(plan["framing"], plan["framing"])
        if framing == "medium_two_shot" and len([tag for tag in subjects if tag.startswith("@")]) < 2:
            framing = "medium_single"
        return [from_plan(plan, lines=list(numbers), subjects=subjects, framing=framing, speaks=True, clip_s=clip_s,
                          speakers=speakers)]

    out, placed, reactions = [], set(), 0

    def place(plan, n, named):
        """Line *n* planned from *plan* (*named*: the lines *plan* itself names)."""
        group = exchange_of.get(n)
        if group is not None and (whole_exchanges or all(m in named for m in group)):
            out.extend(exchange(plan, group))
            placed.update(group)
        else:
            out.append(speaking(plan, n))
            placed.add(n)

    for plan in plans:
        numbers = [n for n in plan.get("lines") or () if 1 <= n <= len(lines) and n not in placed]
        if numbers:
            for n in sorted(numbers):
                if n not in placed:
                    place(plan, n, numbers)
        elif lines and reactions < hi:
            out.append(from_plan(plan, lines=[], speaks=False,
                                 clip_s=native_speech.silent_clip_s(native_speech.REACTION_S, silent_lengths)))
            reactions += 1
    template_plan = plans[0] if plans else _plan(framing="medium_single", camera_motion="hold", modifiers=[],
                                                 action="", subjects=place_tags, lines=[])
    for n in range(1, len(lines) + 1):
        if n not in placed:
            place(template_plan, n, range(1, len(lines) + 1))
    if not lines:
        target = float(scene.get("target_duration_s") or native_speech.REACTION_S)
        out = [from_plan(template_plan, lines=[], speaks=False,
                         clip_s=native_speech.silent_clip_s(target, silent_lengths))]
    elif reactions < lo and out:
        last = next((plan for plan in reversed(out) if plan["speaks"]), out[-1])
        while reactions < lo:
            out.append(from_plan(last, lines=[], speaks=False,
                                 clip_s=native_speech.silent_clip_s(native_speech.REACTION_S, silent_lengths)))
            reactions += 1
    return out
