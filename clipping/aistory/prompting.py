"""Prompt assembly for AI Story (spec 5, 2.3).

Every function here is pure: it takes a ``style_lock_v1``-shaped dict (the
same prose fields also live on a ``style_template_v1`` dict, so these
functions work equally well against ``templates.load_style(id)`` output)
plus a handful of scalar placeholders, and returns exactly one prompt
string. Nothing here calls a model, touches the filesystem, or reads the
clock — that keeps prompt assembly trivially testable and lets phase 2
inject it anywhere a prompt is needed.

Builders never take a character's name: spec 2.3 is explicit that prompts
reference appearance, never names, so a name leaking into an image prompt
would be a spec violation, not just a style nit.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re

from . import schemas

# --------------------------------------------------------------- constants

# Spec 5's common negative-prompt base; each template appends its own on top.
NEGATIVE_BASE = (
    "text, watermark, logo, signature, extra limbs, extra fingers, deformed hands, "
    "duplicated character, cropped face, blurry, low resolution, jpeg artifacts, "
    "out of frame, split screen, collage, frame border, caption"
)

# One phrase per framing in schemas.FRAMINGS (spec 5's per-shot table).
FRAMING_PHRASES = {
    "wide_establishing": "wide establishing shot, characters small in the frame",
    "medium_single": "medium shot, single character waist-up",
    "medium_two_shot": "medium two-shot, both characters waist-up",
    "close_up": "tight close-up on the face",
    "extreme_close_up": "extreme close-up, eyes and skin detail only",
    "over_shoulder": "over-the-shoulder shot",
    "low_angle": "low-angle shot looking up at the subject",
    "high_angle": "high-angle shot looking down at the subject",
    "insert_prop": "insert shot, tight on the object",
}

# Framings with a fixed lens regardless of the template's own camera text.
_WIDE_FRAMINGS = ("wide_establishing",)
_TIGHT_FRAMINGS = ("close_up", "extreme_close_up", "insert_prop")

# Matches e.g. "50mm look" or "50–85mm looks" (en dash or hyphen in a range).
_LENS_PATTERN = re.compile(r"\d+(?:[–-]\d+)?mm\s+looks?")


# ------------------------------------------------------------------ helpers

def _strip_trailing_period(text: str) -> str:
    """Strip surrounding whitespace and one trailing '.', if present.

    Used only where the caller's skeleton adds its own '.' right after the
    fragment, so a fragment that already ends in '.' never produces '..'.
    """
    text = text.strip()
    if text.endswith("."):
        text = text[:-1]
    return text


def _collapse_ws(text: str) -> str:
    """Collapse any run of whitespace to a single space and trim the ends."""
    return re.sub(r"\s+", " ", text).strip()


def _join_items(signature_items) -> str:
    items = list(signature_items)
    if not items:
        raise ValueError(
            "signature_items must not be empty: an identity sheet without "
            "signature items defeats the lock"
        )
    return ", ".join(items)


def _check_framing(framing: str) -> None:
    if framing not in schemas.FRAMINGS:
        raise ValueError(f"unknown framing: {framing!r}")


# -------------------------------------------------------------- primitives

def lens_phrase(style_lock: dict, framing: str) -> str:
    """The lens fragment for one framing, deterministic per spec 5.

    ``wide_establishing`` and the three tight framings get a fixed lens.
    Every other framing derives its lens from the template's own ``camera``
    text, never from the framing itself — the template's camera paragraph
    is otherwise never injected into a shot prompt (spec: it guides T1, not
    the image model), so this is the only place its wording leaks through,
    and only as a short extracted fragment.
    """
    _check_framing(framing)

    if framing in _WIDE_FRAMINGS:
        return "24mm equivalent, deep focus"
    if framing in _TIGHT_FRAMINGS:
        return "85mm look, shallow depth of field"

    camera_text = style_lock["camera"]
    match = _LENS_PATTERN.search(camera_text)
    if match:
        lens = re.sub(r"looks\b", "look", match.group(0))
    elif "macro" in camera_text.lower():
        lens = "macro lens look"
    else:
        lens = "natural lens"

    if "no depth of field" in camera_text:
        return f"{lens}, no depth of field"
    return f"{lens}, shallow depth of field"


def palette_line(style_lock: dict) -> str:
    return style_lock["palette"]["palette_line"]


# --------------------------------------------------------------- builders

def shot_prompt(
    style_lock: dict,
    *,
    subjects_block: str,
    action: str,
    place_block: str,
    time_variant: str,
    framing: str,
) -> str:
    """The common shot-prompt skeleton (spec 5): subject -> action -> setting
    -> style -> camera -> lighting -> quality, in that fixed order.
    """
    _check_framing(framing)
    framing_phrase = FRAMING_PHRASES[framing]
    lens = lens_phrase(style_lock, framing)

    subjects = _strip_trailing_period(subjects_block)
    act = _strip_trailing_period(action)
    place = place_block.strip()
    time_v = _strip_trailing_period(time_variant)
    rendering = _strip_trailing_period(style_lock["rendering"])
    palette = _strip_trailing_period(palette_line(style_lock))
    character_design_rules = style_lock["character_design_rules"].strip()
    lighting = _strip_trailing_period(style_lock["lighting"])
    quality_tail = style_lock["quality_tail"].strip()

    text = (
        f"{subjects}. {act}. Setting: {place}, {time_v}. "
        f"Style: {rendering}. Palette: {palette}. {character_design_rules} "
        f"Camera: {framing_phrase}, {lens}. Lighting: {lighting}. "
        "Vertical 9:16 composition, subject kept in the central safe area "
        "(leave the bottom 22% free of faces for subtitles). "
        f"{quality_tail}"
    )
    return _collapse_ws(text)


def negative_prompt(style_lock: dict) -> str:
    text = f"{NEGATIVE_BASE}, {style_lock['negative_prompt']}"
    return _collapse_ws(text)


def portrait_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Character portrait, {_strip_trailing_period(descriptor)}, wearing {items}. "
        "Neutral expression, looking at camera, three-quarter view, chest-up. "
        f"{style_lock['rendering']}. {style_lock['character_design_rules']} "
        f"Plain {style_lock['sheet_background']} background, even soft studio lighting, "
        "no props, no text. Vertical 9:16."
    )
    return _collapse_ws(text)


def turnaround_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Character design turnaround sheet of the same character: "
        f"{_strip_trailing_period(descriptor)}, {items}. "
        "Four full-body views side by side in one row: front, three-quarter, profile, back. "
        f"Identical proportions and outfit in every view. {style_lock['rendering']}. "
        f"{style_lock['character_design_rules']} Plain {style_lock['sheet_background']} "
        "background, flat even lighting, no text, no labels."
    )
    return _collapse_ws(text)


def expressions_prompt(style_lock: dict, *, descriptor: str, signature_items) -> str:
    items = _join_items(signature_items)
    text = (
        f"Expression sheet of the same character: {_strip_trailing_period(descriptor)}, {items}. "
        "Six head-and-shoulders portraits in a 3x2 grid: neutral, happy, angry, shocked, "
        "sad, scheming. Same face, same outfit, same lighting in every cell. "
        f"{style_lock['rendering']}. Plain {style_lock['sheet_background']} background, no text."
    )
    return _collapse_ws(text)


def master_plate_prompt(style_lock: dict, *, place_descriptor: str, time_variant: str) -> str:
    environment_rules = style_lock["environment_rules"].strip()
    if environment_rules and environment_rules[-1] not in ".!?":
        environment_rules += "."
    text = (
        f"Establishing wide shot of {_strip_trailing_period(place_descriptor)}, {time_variant}, "
        "no people, no characters. "
        f"{environment_rules} {style_lock['rendering']}. "
        f"Palette: {palette_line(style_lock)}. Camera: wide, eye level, 24mm equivalent. "
        f"Lighting: {style_lock['lighting']}. Vertical 9:16, horizon in the upper third, "
        f"foreground detail in the lower third. {style_lock['quality_tail']}"
    )
    return _collapse_ws(text)


def variant_prompt(style_lock: dict, *, place_descriptor: str, variant: str) -> str:
    """The master-plate prompt (spec 5) of one time variant of a place: the
    variant name is its time and weather (``night``, ``golden_hour`` ->
    "golden hour"). ``variant_prompt(..., variant="day")`` is the master plate
    itself. ValueError for a name that is not a variant name
    (``schemas.TIME_VARIANT_PATTERN``).
    """
    if not isinstance(variant, str) or re.fullmatch(schemas.TIME_VARIANT_PATTERN, variant) is None:
        raise ValueError(f"not a time variant name: {variant!r}")
    return master_plate_prompt(style_lock, place_descriptor=place_descriptor,
                               time_variant=variant.replace("_", " "))


def prop_image_prompt(style_lock: dict, *, descriptor: str) -> str:
    """A prop's reference image (spec 2.5): the object alone on the sheet
    background, in the style's rendering, with nobody holding it."""
    text = (
        f"Product shot of {_strip_trailing_period(descriptor)}, alone, centered, plain "
        f"{style_lock['sheet_background']} background. {_strip_trailing_period(style_lock['rendering'])}. "
        f"No people, no hands, no text. {style_lock['quality_tail']}"
    )
    return _collapse_ws(text)


def character_prompt_block(style_lock: dict, *, descriptor: str, signature_items) -> str:
    """Spec 2.3: auto-assembled from descriptor + signature_items +
    style_lock.character_design_rules. Pure and deterministic.
    """
    items = _join_items(signature_items)
    text = f"{_strip_trailing_period(descriptor)}, wearing {items}. {style_lock['character_design_rules']}"
    return _collapse_ws(text)


def place_prompt_block(style_lock: dict, *, descriptor: str, layout_notes: str) -> str:
    """Spec 2.4: auto-assembled from descriptor + layout_notes +
    style_lock.environment_rules. Pure and deterministic, no names.
    """
    place = _strip_trailing_period(descriptor)
    layout = _strip_trailing_period(layout_notes)
    text = f"{place}. {layout}. {style_lock['environment_rules']}"
    return _collapse_ws(text)


def prop_prompt_block(style_lock: dict, *, descriptor: str) -> str:
    """Spec 2.5: auto-assembled from descriptor + style_lock.rendering.
    Pure and deterministic, no names.
    """
    text = f"{_strip_trailing_period(descriptor)}. {style_lock['rendering']}"
    return _collapse_ws(text)


# ============================================================ phase 7 (v2 sheets, plates, props)
#
# A v2 story's reference images are drawn from the entity's structured look
# (``shots.render_look/render_place/render_prop``), not from the legacy
# descriptor builders above, which stay byte for byte as they are (their
# golden strings). Each v2 builder holds a word cap (A8): the look and the
# fixed skeleton are kept whole, the style's rendering is cut at a clause
# boundary and its design rules keep only the whole sentences that fit. The
# image links of a v2 story take no negative prompt, so each ends on a short
# positive constraints clause instead (A7).

SHEET_V2_MAX_WORDS = 130
PLATE_V2_MAX_WORDS = 150
PROP_V2_MAX_WORDS = 80
# The most of the rendering a v2 prompt keeps, and the least it is worth keeping.
_RENDERING_V2_MAX_WORDS = 30
_RENDERING_V2_MIN_WORDS = 8

ROLE_TEXT_PORTRAIT = "Image 1 is this character's reference: keep identity, proportions and outfit exactly."
# "lettering" (the W-mid walk, 2026-10-01: a 'P' badge on a vest despite "no
# logos"; DEC-237): no letter, initial or sign text drawn anywhere.
CONSTRAINTS_ONE_CHARACTER = "Clean frame: no captions, lettering, logos or watermarks; one character."
_CONSTRAINTS_SAME_CHARACTER = "Clean frame: no captions, lettering, logos or watermarks; the same single character throughout."
_CONSTRAINTS_NO_PEOPLE = "Clean frame: no captions, lettering, logos or watermarks; no people."
_CONSTRAINTS_OBJECT = ("Clean frame: no captions, lettering, logos or watermarks; no people, no hands; "
                       "objects have no faces.")


def _word_count(text: str) -> int:
    return len(text.split())


def _pieces(text: str, stops: str) -> list:
    """*text* split after each character of *stops* that is followed by a
    space, outside parentheses; every piece keeps its own punctuation."""
    pieces, start, depth = [], 0, 0
    for i, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char in stops and depth == 0 and i + 1 < len(text) and text[i + 1] == " ":
            pieces.append(text[start:i + 1].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return pieces


def _fit(text: str, limit: int, *, stops: str = ",;.") -> str:
    """*text* cut to at most *limit* words at a piece boundary (:func:`_pieces`),
    with no trailing '.', ',' or ';'; empty when not even the first piece fits."""
    text = _collapse_ws(text)
    kept, count = [], 0
    for piece in _pieces(text, stops):
        words = _word_count(piece)
        if count + words > limit:
            break
        kept.append(piece)
        count += words
    return " ".join(kept).rstrip(",;. ")


def _not_in_look(look_text: str, signature_items) -> list:
    """The signature items *look_text* does not already say (case-folded)."""
    said = look_text.lower()
    return [_strip_trailing_period(item) for item in signature_items
            if _strip_trailing_period(item).lower() not in said]


def _with_items(look_text: str, signature_items) -> str:
    look = _strip_trailing_period(_collapse_ws(look_text))
    extra = _not_in_look(look, signature_items or ())
    if extra:
        look = f"{look}, with {', '.join(extra)}"
    return look


def _styled(cap: int, *, before: str, after: str, rendering: str, rules: str = "") -> str:
    """``before`` + the rendering + the design rules + ``after``, at most *cap*
    words: the rendering cut to what is left (at most
    ``_RENDERING_V2_MAX_WORDS``), then the whole rule sentences that still fit."""
    left = cap - _word_count(before) - _word_count(after) - 1  # "Style:"
    style = _fit(rendering, min(left, _RENDERING_V2_MAX_WORDS))
    left -= _word_count(style)
    kept = []
    for sentence in _pieces(_collapse_ws(rules), ".!?"):
        if _word_count(sentence) > left:
            break
        kept.append(sentence)
        left -= _word_count(sentence)
    parts = [before, f"Style: {style}." if style else "", " ".join(kept), after]
    return _collapse_ws(" ".join(part for part in parts if part))


def _sheet_v2(style_lock, *, head, look_text, signature_items, tail, constraints, rules=True) -> str:
    look = _with_items(look_text, signature_items)
    before = f"{head}: {look}."
    after = f"{tail} {constraints}"
    return _styled(SHEET_V2_MAX_WORDS, before=before, after=after, rendering=style_lock["rendering"],
                   rules=style_lock["character_design_rules"] if rules else "")


def portrait_prompt_v2(style_lock: dict, *, look_text: str, signature_items) -> str:
    """A v2 character's base reference: full body, head to toe, front
    three-quarter, neutral pose, from its rendered look (``shots.render_look``)
    -- at most ``SHEET_V2_MAX_WORDS`` words. A signature item the look does not
    say yet is added with "with"."""
    return _sheet_v2(
        style_lock,
        head=("Full-body character reference sheet, head to toe, front three-quarter view, neutral standing "
              "pose, arms relaxed"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, even soft studio light. Vertical 9:16.",
        constraints=CONSTRAINTS_ONE_CHARACTER,
    )


def turnaround_prompt_v2(style_lock: dict, *, look_text: str, signature_items) -> str:
    """The v2 turnaround, an edit of the portrait (image 1): four full-body
    views of the same character, at most ``SHEET_V2_MAX_WORDS`` words."""
    return _sheet_v2(
        style_lock,
        head=(f"{ROLE_TEXT_PORTRAIT} Turnaround sheet of this character, four full-body views side by side "
              "in one row, front, three-quarter, profile and back, head to toe in each"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, flat even light, no labels.",
        constraints=_CONSTRAINTS_SAME_CHARACTER,
    )


def expressions_prompt_v2(style_lock: dict, *, look_text: str, signature_items) -> str:
    """The v2 expression sheet, an edit of the portrait (image 1): six
    head-and-shoulders portraits, at most ``SHEET_V2_MAX_WORDS`` words. A2:
    a sentence right after the role text pins every cell to image 1's head,
    and the grid count is spelled out ("exactly six cells") -- one sheet had
    drifted to a human face in one cell, another came out with five cells."""
    return _sheet_v2(
        style_lock,
        head=(f"{ROLE_TEXT_PORTRAIT} Every cell shows the same head as image 1, never a different face. "
              "Expression sheet of this character, exactly six cells in a 3 by 2 grid, each a "
              "head-and-shoulders portrait: neutral, happy, angry, shocked, sad and scheming, same face and "
              "outfit in every cell"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, even soft light, no labels.",
        constraints=_CONSTRAINTS_SAME_CHARACTER, rules=False,
    )


def plate_prompt_v2(style_lock: dict, *, place_text: str, variant: str) -> str:
    """A v2 place's plate for one time variant: the place in words
    (``shots.render_place``: descriptor, layout map, the variant's light,
    the props that live there), no people, a wide camera, the style's
    rendering and palette -- at most ``PLATE_V2_MAX_WORDS`` words."""
    if not isinstance(variant, str) or re.fullmatch(schemas.TIME_VARIANT_PATTERN, variant) is None:
        raise ValueError(f"not a time variant name: {variant!r}")
    head = f"Establishing wide shot of an empty set, {variant.replace('_', ' ')}, no people, no characters:"
    after = (f"Camera: wide, eye level, 24mm equivalent, deep focus. "
             f"Palette: {_strip_trailing_period(palette_line(style_lock))}. Vertical 9:16. {_CONSTRAINTS_NO_PEOPLE}")
    room = PLATE_V2_MAX_WORDS - _word_count(head) - _word_count(after) - _RENDERING_V2_MIN_WORDS - 1
    place = _fit(place_text, room)
    before = f"{head} {place}." if place else f"{head[:-1]}."
    return _styled(PLATE_V2_MAX_WORDS, before=before, after=after, rendering=style_lock["rendering"])


def prop_prompt_v2(style_lock: dict, *, prop_text: str) -> str:
    """A v2 prop's reference image (``shots.render_prop(..., for_reference=True)``:
    look, no scale -- A1, a scale phrase here invited a hand holding the
    object for a size reference), the object alone on a plain surface,
    nothing holding it -- at most ``PROP_V2_MAX_WORDS``."""
    head = "Reference image of the object alone on a plain surface, nothing holding it, centred"
    after = (f"Plain {style_lock['sheet_background']} background, even soft studio light. "
             f"{_CONSTRAINTS_OBJECT}")
    room = PROP_V2_MAX_WORDS - _word_count(head) - _word_count(after) - _RENDERING_V2_MIN_WORDS - 2
    prop = _fit(prop_text, room)
    before = f"{head}: {prop}." if prop else f"{head}."
    return _styled(PROP_V2_MAX_WORDS, before=before, after=after, rendering=style_lock["rendering"])


# ============================================================ phase 7 (v2 keyframes and clips, stage 3b)
#
# A v2 shot's keyframe prompt is layered (A8), in this order: the reference
# roles (what each image sent is for, A9), the beat, the staging, the
# composition, the place slice, the style tail and the constraints clause
# (A7). Its clip prompt is the subject and what moves, one secondary motion,
# the camera, a stays-still clause and the style's motion suffix. The texts
# are rendered by ``shots.resolve_shot`` and stored on the shot, so what the
# shot card shows is what is sent.

LAYERED_V1 = schemas.STORYBOARD_PROMPT_LAYOUT_V1
KEYFRAME_V2_MAX_WORDS = 220
KEYFRAME_V2_MIN_WORDS = 130
CLIP_V2_MAX_WORDS = 80
CONSTRAINTS_KEYFRAME = "Clean frame: no captions, lettering, logos or watermarks; each character appears once."
CONSTRAINTS_KEYFRAME_NO_PEOPLE = _CONSTRAINTS_NO_PEOPLE
STAYS_STILL = "The set, the lighting and every character's look stay exactly as in the first frame."

# Exactly ``schemas.CAMERA_MOTIONS`` (spec 6.3's closed list), each mapped to
# a short English phrase a hosted or local image-to-video model is prompted
# with. ``pan_ud``/``pan_du`` follow ``render/motion.py``'s own reading of
# the same tokens (``ud``: low to high y, i.e. top to bottom; ``du``: the
# reverse) so Tier 1's rendered motion and Tier 2's requested motion agree.
# Kept here, pure, so ``shots`` can write a v2 clip prompt at resolve time;
# ``video_plan`` serves the same dicts.
CAMERA_PHRASES = {
    "hold": "static camera, locked-off shot",
    "push_in": "slow push-in toward the subject",
    "pull_out": "slow pull-out from the subject",
    "pan_lr": "slow pan from left to right",
    "pan_rl": "slow pan from right to left",
    "pan_ud": "slow pan from top to bottom",
    "pan_du": "slow pan from bottom to top",
}

# Exactly ``schemas.MODIFIERS`` (also a closed list, spec 6.3).
MODIFIER_PHRASES = {
    "handheld": "handheld camera with subtle shake",
    "jitter_stopmotion": "subtle stop-motion jitter between frames",
}

# The roles a reference image of a v2 shot plays (shots._reference_images_v2).
ROLE_IDENTITY, ROLE_EXPRESSIONS, ROLE_SET, ROLE_TURNAROUND, ROLE_PROP = (
    "identity", "expressions", "set", "turnaround", "prop")


def as_sentence(text: str) -> str:
    """*text* as one sentence: whitespace collapsed, its first letter upper
    case, ending on exactly one '.' (or the '!'/'?' it has)."""
    text = _strip_trailing_period(_collapse_ws(text)).rstrip(",;: ")
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in "!?" else f"{text}."


def fit_words(text: str, limit: int) -> str:
    """*text* cut to at most *limit* words at a clause boundary (',', ';',
    '.'), with no trailing punctuation; empty when not even its first
    clause fits."""
    return _fit(text, limit)


def role_text(roles, *, compact=False) -> str:
    """What each reference image is, in the order the images are sent:
    *roles* is ``[(role, handle), ...]`` -- ``identity`` (a character's
    full-body sheet), ``expressions`` (its expression sheet, a close-up's
    identity image), ``set`` (the place's plate), ``turnaround`` (said
    against the character's own identity image) or ``prop``. Empty when
    nothing is sent.

    *compact* (a crowded keyframe past its budget ladder): one sentence,
    each image numbered with a few words and the keep-exactly rule said once
    for all of them -- about half the words of the sentence per image."""
    if compact:
        return _compact_role_text(roles)
    sentences, identity_of = [], {}
    for number, (role, handle) in enumerate(roles, start=1):
        if role == ROLE_IDENTITY:
            identity_of.setdefault(handle, number)
            sentences.append(f"Image {number} is {handle}'s reference (keep identity, proportions and outfit "
                             "exactly).")
        elif role == ROLE_EXPRESSIONS:
            identity_of.setdefault(handle, number)
            sentences.append(f"Image {number} is {handle}'s expression sheet (keep identity, face and outfit "
                             "exactly).")
        elif role == ROLE_SET:
            sentences.append(f"Image {number} is the set (keep layout and light).")
        elif role == ROLE_TURNAROUND:
            own = identity_of.get(handle)
            sentences.append(f"Image {number} is image {own}'s turnaround." if own
                             else f"Image {number} is {handle}'s turnaround.")
        elif role == ROLE_PROP:
            sentences.append(f"Image {number} is {handle} (keep its shape and colour).")
        else:
            raise ValueError(f"unknown reference role: {role!r}")
    return " ".join(sentences)


def _compact_role_text(roles) -> str:
    """:func:`role_text`'s *compact* form."""
    parts, identity_of = [], {}
    for number, (role, handle) in enumerate(roles, start=1):
        if role in (ROLE_IDENTITY, ROLE_EXPRESSIONS):
            identity_of.setdefault(handle, number)
            parts.append(f"{number} {handle}{'' if role == ROLE_IDENTITY else ' (expressions)'}")
        elif role == ROLE_SET:
            parts.append(f"{number} the set")
        elif role == ROLE_TURNAROUND:
            own = identity_of.get(handle)
            parts.append(f"{number} image {own}'s turnaround" if own else f"{number} {handle}'s turnaround")
        elif role == ROLE_PROP:
            parts.append(f"{number} {handle}")
        else:
            raise ValueError(f"unknown reference role: {role!r}")
    if not parts:
        return ""
    return (f"Reference images: {', '.join(parts)}; keep each identity, outfit, shape, layout and light "
            "exactly.")


def layered_lens_phrase(style_lock: dict, framing: str) -> str:
    """The lens of a layered shot: :func:`lens_phrase`, except that a tight
    framing never forces an 85mm shallow focus on a style whose camera says
    it has no depth of field (and a wide one says only its deep focus)."""
    _check_framing(framing)
    flat = "no depth of field" in style_lock["camera"]
    if framing in _TIGHT_FRAMINGS and flat:
        return "flat staging, no depth of field"
    if framing in _WIDE_FRAMINGS and flat:
        return "deep focus"
    return lens_phrase(style_lock, framing)


def _group(count: int) -> str:
    return "both characters" if count == 2 else f"all {count} characters"


def layered_framing_phrase(framing: str, *, characters=(), props=()) -> str:
    """The framing in words, coerced to what the frame holds: *characters*
    and *props* are the handles of the shot's subjects, in subject order. A
    single framing with several characters reads as a two-shot (or a group
    shot), a two-shot of one character as a single, an over-the-shoulder
    shot names whose shoulder (the second character's, facing the first),
    a close framing of two holds both faces."""
    _check_framing(framing)
    n = len(characters)
    first = characters[0] if characters else ""
    obj = props[0] if props else ""

    if framing == "wide_establishing":
        if n == 0:
            return "wide establishing shot of the whole set"
        return f"wide establishing shot, {'the character' if n == 1 else _group(n)} small in the frame"
    if framing in ("medium_single", "medium_two_shot", "over_shoulder") and n == 0:
        return f"medium shot of {obj}" if obj else "medium shot of the set"
    if framing in ("medium_single", "medium_two_shot", "over_shoulder") and n == 1:
        return f"medium shot of {first}, framed from mid-body up"
    if framing in ("medium_single", "medium_two_shot"):
        return ("medium two-shot, both characters side by side in the frame" if n == 2
                else f"medium group shot, {_group(n)} in the frame")
    if framing == "over_shoulder":
        return f"over-the-shoulder shot past {characters[1]}'s shoulder in the foreground, looking at {first}"
    if framing == "close_up":
        if n == 0:
            return f"close-up on {obj}" if obj else "close-up on one detail of the set"
        if n == 1:
            return f"tight close-up on {first}'s face"
        return f"tight close two-shot, {_group(n)}' faces filling the frame"
    if framing == "extreme_close_up":
        if n == 0:
            return f"extreme close-up on {obj}" if obj else "extreme close-up on one detail of the set"
        return (f"extreme close-up on {first}'s eyes and expression" if n == 1
                else f"extreme close-up on {_group(n)}' faces")
    if framing in ("low_angle", "high_angle"):
        way = "up" if framing == "low_angle" else "down"
        target = first if n == 1 else (_group(n) if n else (obj or "the set"))
        return f"{framing.replace('_', '-')} shot looking {way} at {target}"
    # insert_prop
    if obj:
        return f"insert shot, tight on {obj}"
    return "insert shot, tight on the detail the action names"


def layered_style_tail(style_lock: dict, *, rendering_words: int = _RENDERING_V2_MAX_WORDS) -> str:
    """The style tail of a layered prompt: the rendering (cut at a clause
    boundary to *rendering_words*) and the palette, nothing else."""
    rendering = _fit(style_lock["rendering"], rendering_words)
    palette = _strip_trailing_period(palette_line(style_lock))
    return _collapse_ws(" ".join(part for part in (
        f"Style: {rendering}." if rendering else "", f"Palette: {palette}." if palette else "") if part))


def layered_shot_prompt(style_lock: dict, *, roles_text: str, beat: str, staging: str, composition: str,
                        place_text: str, constraints: str,
                        rendering_words: int = _RENDERING_V2_MAX_WORDS) -> str:
    """A v2 keyframe prompt (A8), its layers in this fixed order: the
    reference roles (:func:`role_text`), the beat, the staging, the
    composition, the place, the style tail (:func:`layered_style_tail`) and
    the constraints clause. Each layer is one or more whole sentences; an
    empty one is left out. The caller keeps it within
    ``KEYFRAME_V2_MAX_WORDS`` (``shots.resolve_shot`` shortens the looks and
    the place first)."""
    layers = (roles_text, beat, staging, composition, place_text,
              layered_style_tail(style_lock, rendering_words=rendering_words), constraints)
    text = _collapse_ws(" ".join(_collapse_ws(layer) for layer in layers if layer and layer.strip()))
    return text.replace(".,", ",").replace("..", ".")


def layered_clip_prompt(style_lock: dict, *, subject: str, motion: str, camera_phrase: str, modifiers=(),
                        secondary: str = "") -> str:
    """A v2 shot's clip prompt (A8), at most ``CLIP_V2_MAX_WORDS`` words: the
    subject and what moves (*motion*: the shot's resolved, name-free action,
    or the planned motion; it is said as it is when it already names the
    *subject*), one *secondary* motion, the camera phrase and its *modifiers*,
    the stays-still clause, then the style's ``tier2_prompt_suffix``. The
    motion is cut at a clause boundary when the whole would be longer."""
    suffix = as_sentence(style_lock["motion_rules"]["tier2_prompt_suffix"])
    camera = as_sentence(", ".join([_strip_trailing_period(camera_phrase)]
                                 + [_strip_trailing_period(m) for m in modifiers if m]))
    tail = [as_sentence(secondary), camera, STAYS_STILL, suffix]
    room = CLIP_V2_MAX_WORDS - sum(_word_count(part) for part in tail)
    moving = _collapse_ws(motion)
    if subject and subject.lower() not in moving.lower():
        moving = f"{subject}: {moving}"
    lead = as_sentence(_fit(moving, room, stops=",;.") if _word_count(moving) > room else moving)
    return _collapse_ws(" ".join(part for part in [lead] + tail if part))
