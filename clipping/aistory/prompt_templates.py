"""The master prompt and the prompt templates of a v2 story (plan 26 stage 2;
the human, 2026-10-05: "the prompts are supposed to have very detailed
descriptions of personas, universe, context", "use templates; the handoff
prompt >= 500 words", "the longer the context the better: everything planned
before the generation is given to the provider, manual, API or local").

A generator is handed, before the text it has always been handed (the
*core*: today's hashed prompt -- the action, the quoted line, the Audio
sentence, the closing), everything the story's records say about what it is
drawing. The text is built from labelled paragraphs, one per
:class:`Section`, joined by blank lines:

1. ``SERIES`` -- title, logline, tone, genre, the world (setting, time
   period), the universe, the dialogue language; ``SERIES LORE`` its rules,
   motifs, themes and premise.
2. ``ART STYLE`` -- the style lock verbatim: rendering, character design
   rules, environment rules, palette line and forbidden colours;
   ``PALETTE COLOURS`` the hexes; ``CAMERA AND LIGHT`` the camera, lighting,
   quality tail, motion rules and voice direction.
3. ``CHARACTER`` -- one paragraph per character (species or head, a human
   says "a human"; build, silhouette, face, hair, skin, height, the wardrobe
   in use, signature items, bearing, colours; the speaker's voice
   direction); ``PERSONALITIES`` and ``RELATIONSHIPS`` of the ones in the
   shot.
4. ``PLACE`` -- descriptor, layout, scale, light of the time variant, props.
5. ``PROP`` -- descriptor, material, colour, size, owner.
6. ``AVOID`` -- the style's negative and the shot's, one sentence.

A shot adds ``SCENE SUMMARY`` and ``SCENE`` (staging, framing, camera, the
line's delivery -- never the line); the core comes last, unchanged.

**Hygiene** (Veo speaks quoted text; ``prompting.speech_prompt_sentences``
reads the core's quotes): the template holds no double quote, no
``Audio:``, no ``says in``, no dialogue line; every entity name a prompt
never says is swept (``shots.name_map`` + ``names.without_names``), a
creature cast becoming its handle and a cast called by its name
(``shots.named_character``, DEC-302) keeping it.

**Fit.** :func:`fit` drops sections in :data:`DROP_ORDER` (the last of a
rank first) until the text is within the link's words (and ``fits(text)``,
the link's own check) -- the style's rendering and rules, the series line,
the characters, place and props in the shot, the scene staging and the core
are never dropped, but for the last rung: the core alone, so the template
never adds a refusal that does not exist today.

Nothing here is hashed: the template is composed where a request is sent and
where the brief is built (plan 26 H1), so a record edit changes the next
request, never a made asset's state. Pure: no I/O, no clock; stdlib and the
package's own pure modules (DEC-012).
"""

from __future__ import annotations

import re
from collections import namedtuple

from . import names as names_mod
from . import prompting, shots

MIN_PROMPT_WORDS = 500

# ``rank`` is the section's position in DROP_ORDER, None for one never dropped (but by the core-alone rung).
Section = namedtuple("Section", "key label text rank")

# The ladder, first dropped first: plan 26's nine rungs, then -- before the core alone -- the camera and light
# of the style, the series line, the props in the shot, the place's layout and the characters' secondary
# details, so a link that cannot take the shot's whole context still gets its style, who is in it (what they
# are made of, face, hair, skin, outfit), the place and its light, and the staging. Past the last rung only the
# never-dropped sections are left (the style's rendering and rules, those looks, the place, the scene); when
# they do not fit either, the core alone.
DROP_ORDER = ("avoid", "characters_absent", "places_absent", "props_absent", "series_lore", "style_palette_hexes",
              "personality", "relationships", "scene_summary", "style_detail", "series", "props", "place_detail",
              "character_detail")

SHORT_WARNING = ("Short prompt: {words} words — the template expects at least {floor}; the cast and place records "
                 "are thin.")

_KINDS = ("characters", "places", "props")
_POSITIONS = {"left": "on the left", "right": "on the right", "centre": "in the centre", "center": "in the centre",
              "back": "at the back", "background": "in the background", "foreground": "in the foreground"}
_LAYOUT_ORDER = ("left", "centre", "right", "back", "foreground")
# Quotes a video model would speak, and the two phrases of the core the brief and the voice parse look for.
_QUOTES = re.compile(r"[\"“”„«»]")
_RESERVED = ((re.compile(r"\bAudio:", re.IGNORECASE), "Sound -"), (re.compile(r"\bsays in\b", re.IGNORECASE),
                                                                  "speaks in"))
_HEAD_PHRASE = re.compile(r"([a-z][a-z' -]*?)\s+head\b", re.IGNORECASE)
_HUMAN_WORD = re.compile(r"\bhuman\b(?![-\w])", re.IGNORECASE)
_HUMAN_TERMS = ("human head", "human face")
_FRUIT_WORDS = re.compile(r"\b(fruit|vegetable)s?\b", re.IGNORECASE)
# What a free-text head phrase says after the thing that is the head ("kiwi
# fruit serving as a human-scale" -> "kiwi fruit"): never spliced into the
# species sentence (plan 26 stage 7a).
_HEAD_ROLE = re.compile(r"\s+(?:(?:serving|used|acting|working)\s+as|as)\b.*$", re.IGNORECASE)
_HEAD_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.IGNORECASE)


def _rank(key):
    return DROP_ORDER.index(key) if key in DROP_ORDER else None


# ------------------------------------------------------------------ text helpers

def _ws(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean(text) -> str:
    """*text* safe for the template: one line, no double quote of any kind,
    no ``Audio:``, no ``says in``."""
    text = _QUOTES.sub("'", _ws(text))
    for pattern, word in _RESERVED:
        text = pattern.sub(word, text)
    return text


def _bare(text) -> str:
    return _ws(text).rstrip(" .;,")


def _sentence(text) -> str:
    """*text* as a sentence: ending on one '.', '!' or '?'; empty stays empty."""
    text = _bare(text)
    if not text:
        return ""
    return text if text[-1] in "!?" else f"{text}."


def _join(items, sep=", ") -> str:
    return sep.join(_bare(item) for item in items or () if _bare(item))


def _labelled(label, value) -> str:
    value = _bare(value)
    return f"{label}: {value}." if value else ""


def _paragraph(heading, parts) -> str:
    body = " ".join(part for part in parts if part)
    return f"{heading}: {body}" if body else ""


def _words(text) -> int:
    return len(text.split())


def _language(language) -> str:
    return prompting.LANGUAGE_NAMES.get(language, language or "")


# ------------------------------------------------------------------ names

class _Who:
    """How the template calls the story's entities: a character by its
    handle (its name for a named cast, ``shots.character_handles``), a prop
    by its handle, and the sweep of every name no prompt says."""

    def __init__(self, entities):
        entities = entities or {}
        self.entities = {kind: dict(entities.get(kind) or {}) for kind in _KINDS}
        characters = self.entities["characters"]
        self.chars = shots.character_handles(characters) if characters else {}
        props = {pid: doc for pid, doc in self.entities["props"].items() if doc.get("descriptor")}
        self.props = {pid: _prop_phrase(handle, props[pid])
                      for pid, handle in (shots.prop_handles(props) if props else {}).items()}
        try:
            names = dict(shots.name_map(self.entities, v2=True))
        except (KeyError, TypeError):
            names = {}
        for cid, doc in characters.items():
            # A creature's name, wherever free text says it, becomes its handle (a neutral word otherwise).
            if doc.get("name") and not shots.named_character(doc) and cid in self.chars:
                names[doc["name"]] = self.chars[cid]
        for pid, doc in self.entities["props"].items():
            # A prop's proper name becomes what it is ("backpack with a sleek USB drive", not "the object").
            if doc.get("name") in names and pid in self.props:
                names[doc["name"]] = self.props[pid]
        self.names = names

    def char(self, cid) -> str:
        return self.chars.get(cid) or "the character"

    def prop(self, pid) -> str:
        return self.props.get(pid) or "the object"

    def sweep(self, text) -> str:
        return names_mod.without_names(text, self.names) if self.names else text


_ARTICLE = re.compile(r"^(a|an|the)\s+", re.IGNORECASE)
_PHRASE_MAX_WORDS = 10


def _prop_phrase(handle, doc) -> str:
    """What the template calls a prop: "the " and its descriptor's first
    clause, at most 10 words ("the sleek USB drive clipped to a backpack
    strap") -- ``shots.prop_handles`` cuts at the descriptor's last comma and
    can keep a bare word ("the pulsing") -- or its handle without a
    descriptor."""
    clause = _ARTICLE.sub("", _ws(re.split(r"[,.;:]", str(doc.get("descriptor") or ""), maxsplit=1)[0]))
    words = clause.split()[:_PHRASE_MAX_WORDS]
    if not words:
        return handle
    return f"the {words[0].lower() if words[0][1:] == words[0][1:].lower() else words[0]} {' '.join(words[1:])}".strip()


# ------------------------------------------------------------------ species

def _head_thing(phrase) -> str:
    """The thing a free-text "<phrase> head" says the head is, whole words
    only: what comes before a role ("dark brown ripe kiwi fruit serving as a
    human-scale" -> "dark brown ripe kiwi fruit"), without its article; ''
    when nothing is left or it says human ("human-scale" included)."""
    thing = _HEAD_ARTICLE.sub("", _HEAD_ROLE.sub("", _ws(phrase))).strip(" ,;:-")
    return "" if not thing or re.search(r"human", thing, re.IGNORECASE) else thing


def _species(doc, style_lock):
    """``(human, sentence)``: whether character *doc* is a human, and what it
    is, in a sentence. A creature is said by the head its look names ("dragon
    fruit head") or the universe species its look says; a cast whose
    descriptor names a species (not ``shots.named_character``) by its
    descriptor; a named cast with neither, or whose look says human, is a
    human. A look that names its species (plan 26 stage 7a: ``look.species``,
    ``shots.look_species``) is read first and is never a human, whatever
    else the look says."""
    look = doc.get("look") or {}
    named_species = shots.look_species(look)
    if named_species:
        return False, (f"is an anthropomorphic character whose head is a whole {named_species}, the face carved "
                       "into its surface, never a human head")
    texts = [look.get(key) for key in ("face", "hair", "skin_material")] + [doc.get("descriptor")]
    texts = [_ws(text) for text in texts if _ws(text)]
    heads = [_head_thing(match.group(1)) for text in texts for match in _HEAD_PHRASE.finditer(text)]
    heads = [head for head in heads if head]
    universe = style_lock.get("universe") or {}
    species = next((word for word in universe.get("species") or () for text in texts
                    if re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE)), None)
    explicit_human = any(_HUMAN_WORD.search(_ws(look.get(key))) for key in ("face", "hair", "skin_material"))
    if heads and not explicit_human:
        head = heads[0]
        return False, (f"is an anthropomorphic character whose head is a whole {head}, the face carved into its "
                       "surface, never a human head")
    if species and not explicit_human:
        return False, f"is an anthropomorphic {species}"
    if not shots.named_character(doc) and doc.get("descriptor") and not explicit_human:
        return False, f"is {_bare(doc['descriptor'])}"
    if not explicit_human and _FRUIT_WORDS.search(" ".join(texts)):
        return False, "is an anthropomorphic character as described below"
    return True, ("is a human, with an ordinary human head and face; the head rule of the art style does not apply "
                  "to this character")


# ------------------------------------------------------------------ characters

def _wardrobe(look, set_id):
    sets = [entry for entry in look.get("wardrobe_sets") or () if isinstance(entry, dict)]
    if not sets:
        return None, []
    chosen = next((entry for entry in sets if entry.get("id") == set_id), sets[0])
    return chosen, [entry for entry in sets if entry is not chosen]


def _look_parts(doc, style_lock, who, *, wardrobe_id=None, all_outfits=False, speaker=False, image=False):
    """``(essential, detail)``: the sentences of a character's look (no
    heading) -- what it is made of, its age, build, face, hair, skin and
    outfit; then its silhouette, height, other outfits, signature items,
    bearing, colours and (the speaker's, not in an image) voice."""
    look = doc.get("look") or {}
    human, species = _species(doc, style_lock)
    parts = [_sentence(f"{who} {species}")]
    if not look and doc.get("descriptor") and human:
        parts.append(_labelled("Look", doc["descriptor"]))
    parts += [_labelled("Apparent age and gender", look.get("presentation")), _labelled("Build", look.get("build")),
              _labelled("Face", look.get("face")), _labelled("Hair", look.get("hair")),
              _labelled("Skin" if human else "Skin and surface", look.get("skin_material"))]
    detail = [_labelled("Silhouette", look.get("silhouette"))]
    if look.get("height_cm"):
        detail.append(f"Height: about {look['height_cm']} cm.")
    chosen, others = _wardrobe(look, wardrobe_id)
    if chosen:
        context = _bare(chosen.get("context"))
        parts.append(_labelled(f"Wearing ({context})" if context else "Wearing", chosen.get("items")))
    variant = doc.get(shots.VARIANT_KEY)
    if variant and variant.get("delta_text"):
        parts.append(_labelled(f"Appearance now ({_bare(variant.get('label'))})" if variant.get("label")
                               else "Appearance now", variant["delta_text"]))
    if all_outfits and others:
        detail.append(_labelled("Other outfits", "; ".join(
            f"{_bare(entry.get('context')) or entry.get('id')}: {_bare(entry.get('items'))}" for entry in others)))
    detail.append(_labelled("Signature items", _join(doc.get("signature_items"))))
    detail.append(_labelled("Bearing", look.get("bearing")))
    detail.append(_labelled("Character colours", _join(look.get("palette"))))
    if speaker and not image:
        voice = (doc.get("voice") or {}).get("direction") or (doc.get("voice_hints") or {}).get("direction")
        detail.append(_labelled("Voice direction", voice))
    return [part for part in parts if part], [part for part in detail if part]


def _personality_text(doc, who, *, image=False, traits_only=False) -> str:
    personality = doc.get("personality") or {}
    bits = []
    traits = _join(personality.get("traits"))
    if traits:
        bits.append(traits)
    if not traits_only:
        for key, word in (("wants", "wants"), ("fears", "fears")):
            if _bare(personality.get(key)):
                bits.append(f"{word} {_bare(personality[key])}")
        if not image and _bare(personality.get("speech_style")):
            bits.append(f"speech style {_bare(personality['speech_style'])}")
    return f"{who}: {'; '.join(bits)}." if bits else ""


def _relationship_lines(cid, doc, who, others) -> list:
    """``"{who} and {other}: {text}."`` for each character of *others*
    (``{id: handle}``) *doc*'s relationships say something about."""
    lines = []
    own = doc.get("relationships") or {}
    dossier = {entry.get("with"): entry for entry in (doc.get("dossier") or {}).get("relationships") or ()
               if isinstance(entry, dict)}
    for other_id, other in others.items():
        if other_id == cid:
            continue
        text = own.get(other_id) if isinstance(own, dict) else None
        if not _bare(text) and other_id in dossier:
            text = dossier[other_id].get("now")
        if _bare(text):
            lines.append(f"{who} and {other}: {_bare(text)}.")
    return lines


# ------------------------------------------------------------------ places and props

def _layout(look, place) -> str:
    layout = look.get("layout_map") if isinstance(look.get("layout_map"), dict) else None
    if layout:
        keys = [key for key in _LAYOUT_ORDER if _bare(layout.get(key))]
        keys += [key for key in layout if key not in _LAYOUT_ORDER and _bare(layout.get(key))]
        return _join([f"{_POSITIONS.get(key, key)} {_bare(layout[key])}" for key in keys], "; ")
    return _bare(place.get("layout_notes"))


def _place_parts(place, who, *, variant=None) -> tuple:
    """``(essential, detail)``: a place's descriptor and light (of *variant*,
    else every one it has); its layout, scale and the props usually there."""
    look = place.get("look") or {}
    lighting = look.get("lighting") if isinstance(look.get("lighting"), dict) else {}
    parts = [_sentence(place.get("descriptor") or place.get("one_line"))]
    if variant and _bare(lighting.get(variant)):
        parts.append(_labelled(f"Light ({variant})", lighting[variant]))
    elif lighting:
        parts += [_labelled(f"Light ({name})", text) for name, text in lighting.items() if _bare(text)]
    here = [who.prop(pid) for pid in look.get("props_here") or () if pid in who.entities["props"]]
    detail = [_labelled("Layout", _layout(look, place)), _labelled("Scale", look.get("scale_note")),
              _labelled("Props usually here", _join(here))]
    return [part for part in parts if part], [part for part in detail if part]


def _prop_text(prop, who, *, present=False) -> str:
    look = prop.get("look") or {}
    size = _bare(look.get("scale_phrase"))
    if look.get("scale_cm"):
        size = f"{size}, about {look['scale_cm']} cm" if size else f"about {look['scale_cm']} cm"
    parts = [_sentence(prop.get("descriptor") or prop.get("one_line")), _labelled("Material", look.get("material")),
             _labelled("Colour", look.get("colour")), _labelled("Size", size)]
    owner = prop.get("owner_char_id")
    if owner and owner in who.entities["characters"]:
        parts.append(_sentence(f"It belongs to {who.char(owner)}"))
    return _paragraph("PROP (in this shot)" if present else "PROP", parts)


# ------------------------------------------------------------------ the master

def _series_sections(story, style_lock, *, language, image, droppable=False):
    world = story.get("world") or {}
    universe = style_lock.get("universe") or {}
    parts = []
    if not image and _bare(story.get("title")):
        parts.append(_sentence(f"{_bare(story['title'])}, a short vertical drama series"))
    parts += [_labelled("Logline", story.get("logline")), _labelled("Tone", story.get("tone")),
              _labelled("Genre", _join(story.get("genre_tags"))),
              _labelled("Setting", world.get("setting_summary")), _labelled("Time period", world.get("time_period"))]
    if universe:
        label = universe.get("label")
        label = label.get("en") if isinstance(label, dict) else label
        phrase = _bare(universe.get("subject_phrase"))
        parts.append(_sentence(f"Universe: {_bare(label) or universe.get('id')}"
                               + (f" -- every character is {phrase}" if phrase else "")
                               + (f", one of {_join(universe.get('species'))}" if universe.get("species") else "")))
    if not image and language:
        parts.append(f"Dialogue language: {_language(language)}.")
    lore = [_labelled("World rules", _join(world.get("rules"), "; ")),
            _labelled("Recurring motifs", _join(world.get("recurring_motifs"), "; ")),
            _labelled("Themes", _join(story.get("themes_and_values"), "; ")),
            _labelled("Premise", story.get("premise"))]
    return [Section("series", "series", _paragraph("SERIES", parts), _rank("series") if droppable else None),
            Section("series_lore", "series lore", _paragraph("SERIES LORE", lore), _rank("series_lore"))]


def _style_sections(style_lock, *, image):
    palette = style_lock.get("palette") or {}
    forbidden = _join(palette.get("forbidden"), " or ")
    style = [_sentence(style_lock.get("rendering")),
             _labelled("Character design rules", style_lock.get("character_design_rules")),
             _labelled("Environment rules", style_lock.get("environment_rules")),
             _labelled("Palette", palette.get("palette_line")),
             _sentence(f"Never use {forbidden}") if forbidden else ""]
    hexes = [_labelled("Primary", _join(palette.get("primary"))), _labelled("Accents", _join(palette.get("accents")))]
    detail = [_labelled("Camera", style_lock.get("camera")), _labelled("Lighting", style_lock.get("lighting")),
              _labelled("Finish", style_lock.get("quality_tail"))]
    if not image:
        detail.append(_labelled("Motion", (style_lock.get("motion_rules") or {}).get("tier2_prompt_suffix_v2")))
        detail.append(_labelled("Voice direction", (style_lock.get("audio") or {}).get("voice_direction")))
    return [Section("style", "art style", _paragraph("ART STYLE", style), None),
            Section("style_palette_hexes", "palette hexes", _paragraph("PALETTE COLOURS", hexes),
                    _rank("style_palette_hexes")),
            Section("style_detail", "camera and light", _paragraph("CAMERA AND LIGHT", detail), _rank("style_detail"))]


def _avoid(style_lock, negative, style_human) -> str:
    terms, seen = [], set()
    for source in (style_lock.get("negative_prompt"), negative):
        for term in str(source or "").split(","):
            term = _bare(term)
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)
    if style_human:
        # A human of the cast has a human head: the style's "human head" would contradict its own paragraph.
        terms = [term for term in terms if term.lower() not in _HUMAN_TERMS]
    return f"AVOID: {', '.join(terms)}." if terms else ""


def master_sections(story, style_lock, entities, *, language, present=(), place_ids=(), prop_ids=(), speaker=None,
                    image=False, negative="", episode=None, variants=None, wardrobe=None,
                    time_variant=None) -> list:
    """The master block's sections, in their order in the text.

    *entities* is the story's ``{"characters", "places", "props"}`` (every
    name in it is swept); *episode* (``{kind: [ids]}``) narrows what is
    listed to the episode's own. With a shot (*present* characters,
    *place_ids*, *prop_ids*) the ones in it are never dropped and marked
    "(in this shot)", the others drop first; the personalities and the
    relationships of the present ones are their own sections. Without one
    (the series master) every listed entity is a full paragraph.
    *speaker*: the character whose voice direction is said (never with
    *image*). *image*: no title, no dialogue language, no motion, no voice.
    *negative*: the shot's negative, added to the style's in AVOID.
    *variants* (``{char_id: variant_id}``, a shot's appearance variants) and
    *wardrobe* (``{char_id: wardrobe set id}``) choose what each wears (the
    first set otherwise); *time_variant* the places' light."""
    style_lock = style_lock or {}
    story = story or {}
    who = _Who(entities)
    listed = {kind: list(who.entities[kind]) for kind in _KINDS}
    if episode:
        for kind in _KINDS:
            if episode.get(kind) is not None:
                wanted = set(episode[kind]) | set(present if kind == "characters" else place_ids if kind == "places"
                                                  else prop_ids)
                listed[kind] = [eid for eid in listed[kind] if eid in wanted]
    shot = bool(present or place_ids or prop_ids)
    present = [cid for cid in present if cid in who.entities["characters"]]
    sections = _series_sections(story, style_lock, language=language, image=image, droppable=shot)
    style = _style_sections(style_lock, image=image)
    sections += style[:2]

    characters = who.entities["characters"]
    docs = {cid: shots.variant_view(characters[cid], (variants or {}).get(cid)) for cid in listed["characters"]}
    order = ([cid for cid in present if cid in docs] + [cid for cid in docs if cid not in present]) if shot \
        else list(docs)
    humans = False
    personalities, relationships = [], []
    in_shot = {cid: who.char(cid) for cid in present}
    for cid in order:
        doc = docs[cid]
        handle = who.char(cid)
        is_present = cid in present
        human, _what = _species(doc, style_lock)
        humans = humans or human
        parts, detail = _look_parts(doc, style_lock, handle + (" (in this shot)" if is_present else ""),
                                    wardrobe_id=(wardrobe or {}).get(cid), all_outfits=not shot,
                                    speaker=cid == speaker, image=image)
        name = _bare(doc.get("name")) or cid
        if shot and is_present:
            personalities.append(_personality_text(doc, handle, image=image))
            relationships += _relationship_lines(cid, doc, handle, in_shot)
            sections.append(Section(f"characters:{cid}", name, _paragraph("CHARACTER", parts), None))
            if detail:
                sections.append(Section(f"character_detail:{cid}", f"{name} details",
                                        _paragraph(f"CHARACTER DETAIL, {handle}", detail), _rank("character_detail")))
            continue
        others = {other: who.char(other) for other in order}
        parts += detail
        parts.append(_labelled("Personality", _personality_text(doc, handle, image=image).split(": ", 1)[-1]))
        parts.append(_labelled("Relationships", " ".join(_relationship_lines(cid, doc, handle, others))))
        key, rank = ("characters", None) if not shot else ("characters_absent", _rank("characters_absent"))
        sections.append(Section(f"{key}:{cid}", name, _paragraph("CHARACTER", parts), rank))
    if personalities:
        sections.append(Section("personality", "personality",
                                _paragraph("PERSONALITIES", personalities), _rank("personality")))
    if relationships:
        sections.append(Section("relationships", "relationships",
                                _paragraph("RELATIONSHIPS", relationships), _rank("relationships")))

    places = who.entities["places"]
    for pid in ([p for p in place_ids if p in listed["places"] or p in places]
                + [p for p in listed["places"] if p not in place_ids]):
        if pid not in places:
            continue
        is_present = pid in place_ids
        name = _bare(places[pid].get("name")) or pid
        parts, detail = _place_parts(places[pid], who, variant=time_variant if is_present else None)
        if shot and is_present:
            sections.append(Section(f"places:{pid}", name, _paragraph("PLACE (in this shot)", parts), None))
            sections.append(Section(f"place_detail:{pid}", f"{name} layout", _paragraph("PLACE LAYOUT", detail),
                                    _rank("place_detail")))
            continue
        key, rank = ("places", None) if not shot else ("places_absent", _rank("places_absent"))
        sections.append(Section(f"{key}:{pid}", name, _paragraph("PLACE", parts + detail), rank))
    props = who.entities["props"]
    for pid in ([p for p in prop_ids if p in props] + [p for p in listed["props"] if p not in prop_ids]):
        if pid not in props:
            continue
        is_present = pid in prop_ids
        key, rank = ("props", None) if not shot else ("props", _rank("props")) if is_present \
            else ("props_absent", _rank("props_absent"))
        sections.append(Section(f"{key}:{pid}", _bare(props[pid].get("name")) or pid,
                                _prop_text(props[pid], who, present=is_present and shot), rank))

    sections.append(style[2])
    sections.append(Section("avoid", "avoid", _avoid(style_lock, negative, humans), _rank("avoid")))
    return _finish(sections, who)


def _finish(sections, who) -> list:
    """*sections* without the empty ones, each swept of names and cleaned."""
    out = []
    for section in sections:
        if not section.text:
            continue
        out.append(section._replace(text=_clean(who.sweep(section.text))))
    return out


def master_prompt(story, style_lock, entities, *, language) -> dict:
    """The story's master prompt (no shot): every section, unfitted --
    ``{text, words, sections: [{key, label, words}]}``."""
    sections = master_sections(story, style_lock, entities, language=language)
    text = "\n\n".join(section.text for section in sections)
    return {"text": text, "words": _words(text),
            "sections": [{"key": section.key, "label": section.label, "words": _words(section.text)}
                         for section in sections]}


# ------------------------------------------------------------------ fitting

def fit(sections, core, *, limit_words, fits=None) -> dict:
    """*sections* then *core*, joined by blank lines, within *limit_words*
    (None: no bound) and *fits* (``fits(text) -> bool``, the link's own
    check; None: words only): sections are dropped in :data:`DROP_ORDER`, the
    last of a rank first, until the text fits; when even the never-dropped
    ones do not, the core alone (every section listed as dropped).
    ``{text, words, full_words, limit, dropped: [label]}``."""
    sections = [section for section in sections if section.text]
    core = core or ""

    def build(kept):
        return "\n\n".join([section.text for section in kept] + ([core] if core else []))

    def ok(text):
        return (limit_words is None or _words(text) <= limit_words) and (fits is None or bool(fits(text)))

    kept = list(sections)
    text = build(kept)
    full_words = _words(text)
    dropped = []
    if not ok(text):
        ladder = sorted((index for index, section in enumerate(sections) if section.rank is not None),
                        key=lambda index: (sections[index].rank, -index))
        for index in ladder:
            kept = [section for section in kept if section is not sections[index]]
            dropped.append(sections[index].label)
            text = build(kept)
            if ok(text):
                break
        else:
            dropped += [section.label for section in kept]
            text = core
    return {"text": text, "words": _words(text), "full_words": full_words, "limit": limit_words,
            "dropped": dropped}


def short_warning(full_words):
    """The short-prompt warning of a template of *full_words* words (its
    unfitted count), or None from :data:`MIN_PROMPT_WORDS` up."""
    if full_words >= MIN_PROMPT_WORDS:
        return None
    return SHORT_WARNING.format(words=full_words, floor=MIN_PROMPT_WORDS)


# ------------------------------------------------------------------ the shot templates

def _shot_subjects(shot):
    """``(characters, places, props, place variant)`` of *shot*'s subject tags."""
    chars, places, props, variant = [], [], [], None
    for tag in shot.get("subject_tags") or ():
        try:
            kind, eid, var = shots.parse_tag(tag)
        except ValueError:
            continue
        if kind == "char" and eid not in chars:
            chars.append(eid)
        elif kind == "place" and eid not in places:
            places.append(eid)
            variant = variant or var
        elif kind == "prop" and eid not in props:
            props.append(eid)
    return chars, places, props, variant


def _scene_of(script, shot):
    scenes = (script or {}).get("scenes") or []
    for index, scene in enumerate(scenes):
        if scene.get("scene_id") == shot.get("scene_id"):
            return scene, index + 1, len(scenes)
    return {}, None, len(scenes)


def _shot_line(scene, shot):
    """The shot's one line (``clips.speech_line``'s choice), or None."""
    return next((line for line in scene.get("lines") or () if line.get("line_id") in (shot.get("lines") or ())),
                None)


def _episode(script, chars, places, props) -> dict:
    """``{kind: [ids]}`` of the episode's own entities: every scene's, plus the shot's."""
    out = {"characters": list(chars), "places": list(places), "props": list(props)}
    for scene in (script or {}).get("scenes") or ():
        for cid in scene.get("characters") or ():
            if cid not in out["characters"]:
                out["characters"].append(cid)
        if scene.get("place_id") and scene["place_id"] not in out["places"]:
            out["places"].append(scene["place_id"])
        for pid in scene.get("props") or ():
            if pid not in out["props"]:
                out["props"].append(pid)
    return out


def _resolve_tag(tag, who) -> str:
    try:
        kind, eid, _variant = shots.parse_tag(tag)
    except ValueError:
        return _bare(tag)
    if kind == "char":
        return who.char(eid)
    if kind == "prop":
        return who.prop(eid)
    return "the set"


def _scene_sections(ec, shot, script, *, image) -> tuple:
    """``(sections, speaker)``: the shot's SCENE SUMMARY and SCENE, and its speaker's id (None if it speaks none)."""
    scene, number, total = _scene_of(script, shot)
    who = _Who(getattr(ec, "entities", None))
    chars, places, _props, variant = _shot_subjects(shot)
    line = _shot_line(scene, shot) if shot.get("speaks") else None
    speaker = line.get("speaker") if line and line.get("speaker") in who.entities["characters"] else None

    ep = getattr(ec, "ep", None)
    head = []
    if number:
        head.append(f"Episode {ep}, scene {number} of {total}." if ep else f"Scene {number} of {total}.")
    if _bare(scene.get("function")):
        head.append(_labelled("Scene function", str(scene["function"]).replace("_", " ")))
    if _bare(scene.get("emotion")):
        head.append(_labelled("Mood", scene["emotion"]))
    time = variant or scene.get("time_variant")
    if _bare(time):
        head.append(_labelled("Time", time))
    if _bare(shot.get("framing")):
        head.append(_labelled("Framing", str(shot["framing"]).replace("_", " ")))
    staged = []
    for entry in shot.get("staging") or ():
        if not isinstance(entry, dict) or not entry.get("subject"):
            continue
        bits = [_resolve_tag(entry["subject"], who)]
        position = _bare(entry.get("position"))
        if position:
            bits.append(_POSITIONS.get(position.lower(), position))
        if _bare(entry.get("facing")) and _bare(entry.get("facing")).lower() != "none":
            bits.append(f"facing {_bare(entry['facing'])}")
        if _bare(entry.get("expression")) and _bare(entry.get("expression")).lower() != "none":
            bits.append(_bare(entry["expression"]))
        staged.append(" ".join(bits[:2]) + "".join(f", {bit}" for bit in bits[2:]))
    if not staged and chars:
        staged = [f"{who.char(cid)} in frame" for cid in chars]
    head.append(_labelled("Staging", "; ".join(staged)))
    camera = shot.get("camera_motion")
    if camera:
        head.append(_labelled("Camera", prompting.CAMERA_PHRASES.get(camera, str(camera).replace("_", " "))))
    if speaker and not image:
        delivery = _bare(line.get("delivery"))
        emotion = _bare(line.get("emotion"))
        said = who.char(speaker)
        if delivery:
            head.append(_labelled(f"Delivery of the line by {said}", delivery))
        if emotion:
            head.append(_labelled(f"Emotion of {said} while speaking", emotion))
    summary = _paragraph("SCENE SUMMARY", [_sentence(scene.get("summary"))])
    sections = [Section("scene_summary", "scene summary", summary, _rank("scene_summary")),
                Section("scene", "scene", _paragraph("SCENE", head), None)]
    return _finish(sections, who), speaker


def _shot_template(ec, shot, script, core, *, limit_words, fits, image, wardrobe=None) -> dict:
    chars, places, props, variant = _shot_subjects(shot)
    scene, _number, _total = _scene_of(script, shot)
    if not places and scene.get("place_id"):
        places = [scene["place_id"]]
    scene_sections, speaker = _scene_sections(ec, shot, script, image=image)
    master = master_sections(getattr(ec, "story", None), getattr(ec, "style_lock", None),
                             getattr(ec, "entities", None), language=getattr(ec, "language", None),
                             present=chars, place_ids=places, prop_ids=props, speaker=speaker, image=image,
                             negative=shot.get("negative_prompt") or "",
                             episode=_episode(script, chars, places, props), variants=shot.get("variants"),
                             wardrobe=wardrobe, time_variant=variant or scene.get("time_variant"))
    return fit(master + scene_sections, core, limit_words=limit_words, fits=fits)


def shot_clip_prompt(ec, shot, script, core, *, limit_words, fits=None, wardrobe=None) -> dict:
    """A shot's clip prompt: the master (the shot's characters, place and
    props present; its speaker's voice) + SCENE SUMMARY + SCENE (with the
    line's delivery and emotion) + *core* unchanged and last, fitted
    (:func:`fit`). *ec* is the ``EpisodeContext`` (``story``, ``style_lock``,
    ``entities``, ``language``, ``ep``); *wardrobe* ``{char_id: wardrobe set
    id}`` (the episode's ledger, as the core's look reads it)."""
    return _shot_template(ec, shot, script, core, limit_words=limit_words, fits=fits, image=False,
                          wardrobe=wardrobe)


def shot_keyframe_prompt(ec, shot, script, core, *, limit_words, fits=None, wardrobe=None) -> dict:
    """A shot's keyframe prompt: the image master (no title, no voice) +
    SCENE SUMMARY + SCENE (no line delivery) + *core*, fitted; *wardrobe*
    as :func:`shot_clip_prompt`'s."""
    return _shot_template(ec, shot, script, core, limit_words=limit_words, fits=fits, image=True,
                          wardrobe=wardrobe)


# ------------------------------------------------------------------ the entity templates

_ENTITY_KINDS = {"character": "characters", "place": "places", "prop": "props"}


def entity_prompt(story, style_lock, kind, doc, core, *, limit_words, fits=None, variant=None,
                  entities=None) -> dict:
    """A sheet's, plate's or prop image's prompt: SERIES (+ lore) + ART STYLE
    (+ hexes, camera and light) + that one entity's paragraph (*kind* one of
    ``character``, ``place``, ``prop``) + *core*, fitted. *variant*: a
    character's appearance variant id or wardrobe set id, a place's time
    variant. *entities*: the story's, for the name sweep and a prop's owner
    (without them only *doc*'s own name is swept)."""
    if kind not in _ENTITY_KINDS:
        raise ValueError(f"entity_prompt: unknown kind {kind!r}")
    story = story or {}
    style_lock = style_lock or {}
    plural = _ENTITY_KINDS[kind]
    id_key = {"characters": "char_id", "places": "place_id", "props": "prop_id"}[plural]
    eid = doc.get(id_key) or f"{kind}_doc"
    roster = {k: dict((entities or {}).get(k) or {}) for k in _KINDS}
    roster[plural][eid] = doc
    who = _Who(roster)
    sections = _series_sections(story, style_lock, language=story.get("language"), image=True, droppable=True)
    style = _style_sections(style_lock, image=True)
    sections += style
    label = _bare(doc.get("name")) or eid
    if kind == "character":
        view = shots.variant_view(doc, variant) if variant else doc
        wardrobe_id = variant if view is doc else None
        handle = who.char(eid)
        parts, detail = _look_parts(view, style_lock, handle, wardrobe_id=wardrobe_id, image=True)
        parts += detail
        sections.append(Section(f"characters:{eid}", label, _paragraph("CHARACTER", parts), None))
        sections.append(Section("personality", "personality",
                                _paragraph("PERSONALITY", [_personality_text(doc, handle, traits_only=True)]),
                                _rank("personality")))
    elif kind == "place":
        parts, detail = _place_parts(doc, who, variant=variant)
        sections.append(Section(f"places:{eid}", label, _paragraph("PLACE", parts + detail), None))
    else:
        sections.append(Section(f"props:{eid}", label, _prop_text(doc, who), None))
    return fit(_finish(sections, who), core, limit_words=limit_words, fits=fits)
