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

import re

from . import names as names_mod
from . import prompting, schemas, timing

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

def _leading_phrase(descriptor: str) -> str:
    """The descriptor's own head-noun phrase: (1) its first sentence (split
    at the first '. '); (2) cut at the earliest of ' serving as '/' with '/
    ' wearing '/' who '/' that '/';'; (3) drop a leading article; (4) keep
    only the text after that phrase's LAST comma, if it has one -- this
    drops the leading comma-separated adjective list a K1-written
    descriptor usually opens with ("A fuzzy, dark brown ripe kiwi fruit..."
    -> "dark brown ripe kiwi fruit"), rather than keeping the adjectives and
    losing the noun; (5) collapse whitespace, keep <= 10 words."""
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

    if "," in text:
        text = text.rsplit(",", 1)[1]

    words = text.strip().strip(",;").split()
    return " ".join(words[:_HANDLE_MAX_WORDS])


def _handle_from_descriptor(descriptor: str) -> str:
    phrase = _leading_phrase(descriptor)
    return f"the {phrase}".strip()


def character_handles(characters: dict) -> dict:
    """``{char_id: handle}``: a short English handle per character, from the
    leading noun phrase of its own descriptor (never its name -- spec 2.3).
    Two characters whose base handle collides both get
    ``" wearing " + their first signature item`` appended; if that still
    collides, ``" (n)"`` in cast order (the order *characters* iterates).
    Deterministic."""
    order = list(characters.keys())
    base = {cid: _handle_from_descriptor(characters[cid]["descriptor"]) for cid in order}

    counts = {}
    for cid in order:
        counts[base[cid]] = counts.get(base[cid], 0) + 1

    handles = dict(base)
    for cid in order:
        if counts[base[cid]] > 1:
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


def prop_handles(props: dict) -> dict:
    """``{prop_id: handle}``, the same way as :func:`character_handles` but
    without the "wearing" disambiguation: a collision goes straight to
    ``" (n)"`` in cast order."""
    order = list(props.keys())
    base = {pid: _handle_from_descriptor(props[pid]["descriptor"]) for pid in order}

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


def render_look(doc, *, wardrobe_set=None, others=(), max_words=LOOK_MAX_WORDS) -> str:
    """A character's look in at most *max_words* words (default
    :data:`LOOK_MAX_WORDS`; a tighter cap ends on a whole part): its
    presentation first when the look has one (A3: apparent age and gender --
    never dropped under budget), then build, its height against *others*
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
    parts = {
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
        return prompting.fit_words(text(), max_words) or " ".join(words[:max_words]).rstrip(",;")
    return " ".join(words[:max_words]).rstrip(",;")


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


def _story_name_map(entities) -> dict:
    """``{name: neutral word}`` for every character, place and prop of
    *entities* -- a character's word wins on a name shared with a place or
    prop (mirrors ``refimages._entity_names``)."""
    names = {}
    for kind in ("characters", "places", "props"):
        for doc in entities.get(kind, {}).values():
            names.setdefault(doc["name"], _NEUTRAL_WORDS[kind])
    return names


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
    return names


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
             prop_handles, name_map, ledger=None, continuity=False, budgets=None) -> dict:
    """The v2 half of :func:`resolve_shot`: ``image_prompt`` (layered,
    within its budget -- the looks, the place and the rendering shortened
    in turn until it fits), ``video_prompt``, ``reference_images`` and
    ``prompt_layout``. *continuity*: the previous keyframe of the scene is
    one of the references (phase 8 stage B). *budgets*
    (``prompting.Budgets``, stage F2: each prompt's words, from the links
    the episode's images and clips go to; None: the fixed numbers).
    :class:`PromptOverBudget` when even the ladder's last rung is over the
    keyframe's budget, or the clip's fixed parts alone are over its own."""
    budgets = budgets or prompting.Budgets()
    characters = entities.get("characters", {})
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
    roles = prompting.role_text(sent, outfits=outfits) if references else ""
    compact_roles = prompting.role_text(sent, compact=True, outfits=outfits) if references else ""

    clause = _delivery_clause(_shot_lines(plan, scene), char_handles)
    beat = " ".join(part for part in (prompting.as_sentence(video_action), prompting.as_sentence(clause)) if part)
    beat = names_mod.without_names(beat, name_map)
    composition = prompting.as_sentence(
        f"Camera: {prompting.layered_framing_phrase(framing, characters=handles, props=held)}, "
        f"{prompting.layered_lens_phrase(style_lock, framing)}")
    place_full = _place_slice(places[scene["place_id"]], scene["time_variant"], framing, props)
    constraints = (prompting.CONSTRAINTS_KEYFRAME if frame else prompting.CONSTRAINTS_KEYFRAME_NO_PEOPLE)

    place_names = {pid: doc.get("name") for pid, doc in places.items()}

    def resolve(text):
        return resolve_action(text, char_handles=char_handles, prop_handles=prop_handles, place_names=place_names)

    staged = plan.get("staging") or ()
    rungs = [budget + ("full",) for budget in _LAYERED_BUDGETS] + list(_LAYERED_LAST_RUNGS)
    for look_words, place_words, rendering_words, props_said in rungs:
        staging = names_mod.without_names(
            _staging(frame, frame_props, char_handles=char_handles, look_words=look_words, staging=staged,
                     resolve=resolve, ledger=ledger, props=props_said), name_map)
        place_text = place_full if place_words is None else _fit_place(place_full, place_words)
        image_prompt = prompting.layered_shot_prompt(
            style_lock, roles_text=roles if props_said == "full" else compact_roles, beat=beat, staging=staging,
            composition=composition, place_text=place_text, constraints=constraints,
            rendering_words=rendering_words)
        if len(image_prompt.split()) <= budgets.keyframe:
            break
    else:
        # Stage F2: past the last rung the prompt is refused, never sent anyway.
        raise PromptOverBudget("keyframe", len(image_prompt.split()), budgets.keyframe)

    camera_motion = plan.get("camera_motion")
    if camera_motion not in prompting.CAMERA_PHRASES:
        camera_motion = motion_for(framing, None, scene["function"], style_lock)["type"]
    if len(handles) >= 2:
        secondary = f"{handles[1]} reacts with a small natural movement"
    elif handles:
        secondary = "small natural idle movements in between"
    else:
        secondary = ""
    subject = _and_join(handles) or (held[0] if held else "the set")
    # T1 v2's motion (phase 7 stage 4: what the characters do during the clip,
    # in tags) when the plan has one, else the resolved action as before.
    clip_motion = plan.get("clip_motion")
    motion = names_mod.without_names(_collapse_ws(resolve(clip_motion) if clip_motion else video_action), name_map)
    modifiers = [prompting.MODIFIER_PHRASES[m] for m in plan.get("modifiers") or () if m in prompting.MODIFIER_PHRASES]
    video_prompt = prompting.layered_clip_prompt(
        style_lock, subject=subject, motion=_strip_period(motion),
        camera_phrase=prompting.CAMERA_PHRASES[camera_motion], modifiers=modifiers, secondary=secondary,
        budget=budgets.clip)
    if len(video_prompt.split()) > budgets.clip:
        # The motion is cut to the budget; the fixed parts (camera, stays-still, suffix) cannot be.
        raise PromptOverBudget("clip", len(video_prompt.split()), budgets.clip)

    return {
        "image_prompt": image_prompt,
        "video_prompt": video_prompt,
        "reference_images": [path for path, _role, _handle in refs],
        "prompt_layout": prompting.LAYERED_V1,
    }


def resolve_shot(shot, *, scene, entities, style_lock, consistency_mode, v2=False, ledger=None,
                 continuity=False, budgets=None) -> dict:
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
    fit even on the ladder's last rung raises :class:`PromptOverBudget`."""
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
                           name_map=name_map, ledger=ledger, continuity=continuity, budgets=budgets)
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


def motion_for(framing, camera_motion, scene_function, style_lock) -> dict:
    """``{"type", "zoom_from", "zoom_to", "pan"}`` for one shot (spec 5's
    tier-1 motion): the TYPE is ``by_function[framing]`` if present, else
    ``by_function[scene_function]`` if present, else the given
    *camera_motion*, else the style's own ``default_motion``. ``push_in``
    zooms from ``zoom.dialogue`` to itself (``zoom.peak`` on a peak or
    cliffhanger scene); ``pull_out`` is the reverse; both clamp to
    ``zoom.max``. A ``pan_*`` type holds the zoom at 1.0 and pans that way;
    ``hold`` holds the zoom at 1.0 with no pan."""
    tier1 = style_lock["motion_rules"]["tier1"]
    by_function = tier1["by_function"]

    if framing in by_function:
        motion_type = by_function[framing]
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


def _apply_no_repeat_framing(plans_by_scene, notes) -> bool:
    """One pass of rule (a); returns whether anything changed."""
    items = _flatten(plans_by_scene)
    changed = False
    for k in range(1, len(items)):
        _si, pi, scene, plan = items[k]
        _psi, ppi, pscene, pplan = items[k - 1]
        if plan["framing"] != pplan["framing"]:
            continue
        if not _is_protected(pi, plan) and plan["framing"] in _FRAMING_SUBSTITUTES:
            new_framing = _FRAMING_SUBSTITUTES[plan["framing"]]
            notes.append(
                f"rule_pass: scene {scene['scene_id']} shot {pi + 1}: framing changed "
                f"{plan['framing']!r} -> {new_framing!r} (repeated the previous shot's framing)"
            )
            plan["framing"] = new_framing
            changed = True
        elif not _is_protected(ppi, pplan) and pplan["framing"] in _FRAMING_SUBSTITUTES:
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


def _apply_close_up_window(plans_by_scene, notes) -> None:
    """Rule (b): every window of 3 consecutive scenes has a close_up or
    extreme_close_up somewhere in it; otherwise the last non-insert_prop shot
    of the window's third scene becomes one."""
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
        target = candidates[-1]
        target["framing"] = "close_up"
        has_char_tag = any(t.startswith("@") for t in target["subjects"])
        if not has_char_tag and third_scene["characters"]:
            target["subjects"] = list(target["subjects"]) + [f"@{third_scene['characters'][0]}"]
        notes.append(
            f"rule_pass: scene {third_scene['scene_id']}: no close_up/extreme_close_up in this 3-scene "
            "window, its last shot was forced to close_up"
        )


def _apply_motion_precedence(plans_by_scene, style_lock, notes) -> None:
    """Rule (c): each plan's camera_motion becomes whatever motion_for's
    precedence would pick for it -- the "push-in on peaks" rule."""
    for scene, plans in plans_by_scene:
        for plan in plans:
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock)
            if motion["type"] != plan["camera_motion"]:
                notes.append(
                    f"rule_pass: scene {scene['scene_id']}: camera motion changed "
                    f"{plan['camera_motion']!r} -> {motion['type']!r}"
                )
                plan["camera_motion"] = motion["type"]


def rule_pass(plans_by_scene, style_lock) -> tuple:
    """The cross-scene rules applied to every scene's shot plans, on both the
    T1 and the fast path (spec 5): (a) no two consecutive shots in the whole
    episode share a framing (never changing an insert_prop shot or a scene's
    own opening wide_establishing -- the earlier shot moves instead); (b)
    every window of 3 consecutive scenes has a close_up or extreme_close_up,
    re-checking (a) afterwards; (c) each shot's camera motion follows
    :func:`motion_for`'s precedence. Returns ``(plans_by_scene, notes)``, a
    new structure -- *plans_by_scene* itself and its plan dicts are not
    mutated."""
    plans_by_scene = [(scene, [dict(p) for p in plans]) for scene, plans in plans_by_scene]
    notes = []

    guard = len(_flatten(plans_by_scene)) + 2
    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes):
            break

    _apply_close_up_window(plans_by_scene, notes)

    for _ in range(guard):
        if not _apply_no_repeat_framing(plans_by_scene, notes):
            break

    _apply_motion_precedence(plans_by_scene, style_lock, notes)

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
    return plan


def name_map(entities, *, v2=False) -> dict:
    """``{name: neutral word}`` of every entity name an image prompt never
    says: :func:`_v2_name_map` on a v2 story (a descriptive name is kept, so
    a handle is never garbled), else :func:`_story_name_map` -- what
    :func:`resolve_shot` strips from the action."""
    return _v2_name_map(entities) if v2 else _story_name_map(entities)


def build_storyboard(script, plans, sources, *, entities, style_lock, template, language, consistency_mode,
                     now, previous=None, v2=False, shots_per_scene=None, ledger=None, budgets=None) -> tuple:
    """*plans* (``{scene_id: [plan, ...]}``) and *sources* (``{scene_id:
    "t1"|"fast"}``) resolved into a complete ``storyboard_v1`` document:
    scenes in the script's own order (only the ones *plans* covers), the
    cross-scene :func:`rule_pass`, ``sh01..`` ids, every shot resolved
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
    """
    scenes_by_id = {scene["scene_id"]: scene for scene in script["scenes"]}
    scenes_in_order = [scene for scene in script["scenes"] if scene["scene_id"] in plans]

    plans_by_scene = [(scene, plans[scene["scene_id"]]) for scene in scenes_in_order]
    plans_by_scene, notes = rule_pass(plans_by_scene, style_lock)

    shots = []
    resolved_from = {}
    order = 0
    for scene, scene_plans in plans_by_scene:
        for index, plan in enumerate(scene_plans):
            order += 1
            shot_id = f"sh{order:02d}"
            line_ids = [scene["lines"][n - 1]["line_id"] for n in plan["lines"]]
            motion = motion_for(plan["framing"], plan["camera_motion"], scene["function"], style_lock)
            try:
                resolved = resolve_shot(plan, scene=scene, entities=entities, style_lock=style_lock,
                                        consistency_mode=consistency_mode, v2=v2, ledger=ledger,
                                        continuity=v2 and index > 0, budgets=budgets)
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
            shots.append(shot)

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

    if shots_per_scene is None:
        shots_per_scene = style_lock["episode_defaults"]["shots_per_scene"]
    errors = schemas.storyboard_errors(doc, min_shot_s=template["min_shot_s"])
    errors += schemas.storyboard_context_errors(doc, script, shots_per_scene=shots_per_scene)
    if errors:
        raise ValueError(f"build_storyboard produced an invalid storyboard: {'; '.join(errors)}")

    return doc, notes


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
                                    continuity=v2 and continues_scene(storyboard["shots"], index), budgets=budgets)
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
