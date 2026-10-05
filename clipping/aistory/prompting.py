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
from collections import namedtuple

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
#
# Phase 7 follow-up, stage F2: every cap below is the budget a prompt gets
# when its link is not known -- the number every v2 prompt was built to until
# then. A caller that knows the link passes its own ``budget``
# (``prompt_budgets``: the link's limit bounded by a quality ceiling), and
# the builder fills it the same way, the whole skeleton first.

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


def _styled(cap: int, *, before: str, after: str, rendering: str, rules: str = "", cues: str = "") -> str:
    """``before`` + *cues* + the rendering + the design rules + ``after``, at
    most *cap* words: *cues* (stage F2: a character's marks and bearing,
    ``shots.visual_cues`` -- identity, so ahead of the style) whole when
    they leave the rendering its least (``_RENDERING_V2_MIN_WORDS``), else
    left out; the rendering cut to what is left (at most
    ``_RENDERING_V2_MAX_WORDS``); then the whole rule sentences that still
    fit."""
    left = cap - _word_count(before) - _word_count(after) - 1  # "Style:"
    cue = ""
    if cues:
        # The cues only while the rendering still gets its least after them (a rendering whose first
        # clause no longer fits is dropped whole by _fit).
        with_cues = _fit(rendering, min(left - _word_count(cues), _RENDERING_V2_MAX_WORDS))
        if _word_count(with_cues) >= _RENDERING_V2_MIN_WORDS:
            cue = cues
    left -= _word_count(cue)
    style = _fit(rendering, min(left, _RENDERING_V2_MAX_WORDS))
    left -= _word_count(style)
    kept = []
    for sentence in _pieces(_collapse_ws(rules), ".!?"):
        if _word_count(sentence) > left:
            break
        kept.append(sentence)
        left -= _word_count(sentence)
    parts = [before, cue, f"Style: {style}." if style else "", " ".join(kept), after]
    return _collapse_ws(" ".join(part for part in parts if part))


def _sheet_v2(style_lock, *, head, look_text, signature_items, tail, constraints, rules=True,
              budget=SHEET_V2_MAX_WORDS, cues="") -> str:
    look = _with_items(look_text, signature_items)
    before = f"{head}: {look}."
    after = f"{tail} {constraints}"
    return _styled(budget, before=before, after=after, rendering=style_lock["rendering"],
                   rules=style_lock["character_design_rules"] if rules else "", cues=cues)


def portrait_prompt_v2(style_lock: dict, *, look_text: str, signature_items, budget=SHEET_V2_MAX_WORDS,
                       cues: str = "") -> str:
    """A v2 character's base reference: full body, head to toe, front
    three-quarter, neutral pose, from its rendered look (``shots.render_look``)
    -- at most *budget* words (``SHEET_V2_MAX_WORDS``, or the link's own,
    stage F2). A signature item the look does not say yet is added with
    "with". *cues* (stage F2, ``shots.visual_cues``: the character's
    distinctive marks and bearing) follow the look when they fit."""
    return _sheet_v2(
        style_lock,
        head=("Full-body character reference sheet, head to toe, front three-quarter view, neutral standing "
              "pose, arms relaxed"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, even soft studio light. Vertical 9:16.",
        constraints=CONSTRAINTS_ONE_CHARACTER, budget=budget, cues=cues,
    )


def turnaround_prompt_v2(style_lock: dict, *, look_text: str, signature_items, budget=SHEET_V2_MAX_WORDS,
                         cues: str = "") -> str:
    """The v2 turnaround, an edit of the portrait (image 1): four full-body
    views of the same character, at most *budget* words; *cues* as
    :func:`portrait_prompt_v2`'s."""
    return _sheet_v2(
        style_lock,
        head=(f"{ROLE_TEXT_PORTRAIT} Turnaround sheet of this character, four full-body views side by side "
              "in one row, front, three-quarter, profile and back, head to toe in each"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, flat even light, no labels.",
        constraints=_CONSTRAINTS_SAME_CHARACTER, budget=budget, cues=cues,
    )


def expressions_prompt_v2(style_lock: dict, *, look_text: str, signature_items, budget=SHEET_V2_MAX_WORDS,
                          cues: str = "") -> str:
    """The v2 expression sheet, an edit of the portrait (image 1): six
    head-and-shoulders portraits, at most *budget* words; *cues* as
    :func:`portrait_prompt_v2`'s. A2: a sentence right after the role text
    pins every cell to image 1's head, and the grid count is spelled out
    ("exactly six cells") -- one sheet had drifted to a human face in one
    cell, another came out with five cells."""
    return _sheet_v2(
        style_lock,
        head=(f"{ROLE_TEXT_PORTRAIT} Every cell shows the same head as image 1, never a different face. "
              "Expression sheet of this character, exactly six cells in a 3 by 2 grid, each a "
              "head-and-shoulders portrait: neutral, happy, angry, shocked, sad and scheming, same face and "
              "outfit in every cell"),
        look_text=look_text, signature_items=signature_items,
        tail=f"Plain {style_lock['sheet_background']} background, even soft light, no labels.",
        constraints=_CONSTRAINTS_SAME_CHARACTER, rules=False, budget=budget, cues=cues,
    )


# Plan 23 stage D4: the two-view character sheet (the creators' template): ONE
# 9:16 image with the character's front and back side by side, written into
# the portrait slot. The layout is said once, in the head; the look follows
# it; the dress rule, the plain background and the proportions close it.
# Its skeleton alone (the layout, the dress rule, the closing) is about 150 words,
# so it gets more room than the other sheets: the style's rendering and the body
# rules need what is left (Seedream took 220-word prompts on the walks).
TWO_VIEW_V2_MAX_WORDS = 260
TWO_VIEW_HEAD = (
    "A character reference sheet showing two full-body views of the same character side by side, separated by a "
    "clean vertical line in the centre. LEFT HALF: full frontal view head to toe facing the camera. RIGHT HALF: "
    "full back view head to toe. Never cut the character in half across the line. Never more than two views. "
    "Never crop a view. Character")
DRESS_RULE = ("Fully dressed from shoulders to feet: a complete top, a complete bottom (trousers, or a skirt or "
              "dress below the knee) and shoes; no bare legs, no visible underwear.")
# What a keyframe says about a character's identity image when it is a two-view sheet (the
# failure to guard: the character drawn twice in the frame).
TWO_VIEW_ROLE = "Image {number} shows this one character twice, front and back: draw them once."
_TWO_VIEW_ROLE_COMPACT = "image {number} shows {handle} twice, front and back: draw them once"


def two_view_prompt_v2(style_lock: dict, *, look_text: str, signature_items, budget=TWO_VIEW_V2_MAX_WORDS,
                       cues: str = "") -> str:
    """A v2 character's two-view sheet (``sheet_mode`` ``two_view``): one
    vertical 9:16 image, the front on the left half and the back on the
    right, head to toe, from its rendered look (``shots.render_look``) --
    built through :func:`_sheet_v2` like the other sheets, at most *budget*
    words (:data:`TWO_VIEW_V2_MAX_WORDS`, or the link's own): the layout, the
    look and the dress rule are the skeleton (never cut), the style's
    rendering and design rules fill what the *budget* leaves. *cues* as
    :func:`portrait_prompt_v2`'s."""
    return _sheet_v2(
        style_lock,
        head=TWO_VIEW_HEAD,
        look_text=look_text, signature_items=signature_items,
        tail=(f"{DRESS_RULE} Plain {style_lock['sheet_background']} background, soft uniform light, no text, no "
              "grid, no labels. Adult proportions, never chibi. Vertical 9:16."),
        constraints=_CONSTRAINTS_SAME_CHARACTER, budget=budget, cues=cues,
    )


def plate_prompt_v2(style_lock: dict, *, place_text: str, variant: str, budget=PLATE_V2_MAX_WORDS) -> str:
    """A v2 place's plate for one time variant: the place in words
    (``shots.render_place``: descriptor, layout map, the variant's light,
    the props that live there), no people, a wide camera, the style's
    rendering and palette -- at most *budget* words (``PLATE_V2_MAX_WORDS``,
    or the link's own, stage F2). The style's ``environment_rules``, as one
    sentence, when the budget has room for it whole after the rendering."""
    if not isinstance(variant, str) or re.fullmatch(schemas.TIME_VARIANT_PATTERN, variant) is None:
        raise ValueError(f"not a time variant name: {variant!r}")
    head = f"Establishing wide shot of an empty set, {variant.replace('_', ' ')}, no people, no characters:"
    after = (f"Camera: wide, eye level, 24mm equivalent, deep focus. "
             f"Palette: {_strip_trailing_period(palette_line(style_lock))}. Vertical 9:16. {_CONSTRAINTS_NO_PEOPLE}")
    room = budget - _word_count(head) - _word_count(after) - _RENDERING_V2_MIN_WORDS - 1
    place = _fit(place_text, room)
    before = f"{head} {place}." if place else f"{head[:-1]}."
    return _styled(budget, before=before, after=after, rendering=style_lock["rendering"],
                   rules=as_sentence(style_lock["environment_rules"]))


def prop_prompt_v2(style_lock: dict, *, prop_text: str, budget=PROP_V2_MAX_WORDS) -> str:
    """A v2 prop's reference image (``shots.render_prop(..., for_reference=True)``:
    look, no scale -- A1, a scale phrase here invited a hand holding the
    object for a size reference), the object alone on a plain surface,
    nothing holding it -- at most *budget* words (``PROP_V2_MAX_WORDS``, or
    the link's own, stage F2)."""
    head = "Reference image of the object alone on a plain surface, nothing holding it, centred"
    after = (f"Plain {style_lock['sheet_background']} background, even soft studio light. "
             f"{_CONSTRAINTS_OBJECT}")
    room = budget - _word_count(head) - _word_count(after) - _RENDERING_V2_MIN_WORDS - 2
    prop = _fit(prop_text, room)
    before = f"{head}: {prop}." if prop else f"{head}."
    return _styled(budget, before=before, after=after, rendering=style_lock["rendering"])


# ============================================================ phase 7 (v2 keyframes and clips, stage 3b)
#
# A v2 shot's keyframe prompt is layered (A8), in this order: the reference
# roles (what each image sent is for, A9), the beat, the staging, the
# composition, the place slice, the style tail and the constraints clause
# (A7). Its clip prompt is the subject and what moves, the performance (who
# speaks, who reacts: DEC-252), the camera, the identity clause and the
# style's v2 motion suffix. The texts
# are rendered by ``shots.resolve_shot`` and stored on the shot, so what the
# shot card shows is what is sent.

LAYERED_V1 = schemas.STORYBOARD_PROMPT_LAYOUT_V1
KEYFRAME_V2_MAX_WORDS = 220
KEYFRAME_V2_MIN_WORDS = 130
CLIP_V2_MAX_WORDS = 80

# Phase 7 follow-up, stage F2: the word budgets a v2 shot's two prompts are
# built to, with the links they were derived from (``prompt_budgets.for_links``
# derives them; ``shots`` reads the numbers, and names the link in a refusal).
# The defaults are the fixed budgets above: what a shot gets when its links
# are not known yet.
Budgets = namedtuple("Budgets", "keyframe clip image_link video_link",
                     defaults=(KEYFRAME_V2_MAX_WORDS, CLIP_V2_MAX_WORDS, None, None))
CONSTRAINTS_KEYFRAME = "Clean frame: no captions, lettering, logos or watermarks; each character appears once."
CONSTRAINTS_KEYFRAME_NO_PEOPLE = _CONSTRAINTS_NO_PEOPLE
STAYS_STILL = "The set, the lighting and every character's look stay exactly as in the first frame."
# DEC-252: what a v2 clip says in place of STAYS_STILL (the human on the first
# fully animated episode, 2026-10-03: "the video does not say things or do
# movements"): the looks, the set and the light are kept, and the characters
# are let move. STAYS_STILL itself is kept, unchanged, for anything reading it.
IDENTITY_KEEPS = ("Keep every character's look, the set and the light as in the first frame; "
                  "the characters move freely within it.")

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
# ``continuity`` (phase 8 stage B): the previous keyframe of the same scene.
ROLE_IDENTITY, ROLE_EXPRESSIONS, ROLE_SET, ROLE_TURNAROUND, ROLE_PROP, ROLE_CONTINUITY = (
    "identity", "expressions", "set", "turnaround", "prop", "continuity")


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


def role_text(roles, *, compact=False, outfits=None, two_view=False) -> str:
    """What each reference image is, in the order the images are sent:
    *roles* is ``[(role, handle), ...]`` -- ``identity`` (a character's
    full-body sheet), ``expressions`` (its expression sheet, a close-up's
    identity image), ``set`` (the place's plate), ``continuity`` (the
    previous keyframe of the same scene, phase 8 stage B), ``turnaround``
    (said against the character's own identity image) or ``prop``. Empty
    when nothing is sent.

    *outfits* (phase 8 stage B: ``{handle: wardrobe items}``) names the
    characters this shot dresses in another wardrobe set than the one their
    sheets were drawn in (``shots.sheet_wardrobe``): their sheet keeps who
    they are -- face, hair, build, proportions -- and the text says what they
    wear here instead of "outfit exactly", so the prompt never asks for the
    sheet's outfit and another one at once. None or empty: the text it
    always was.

    *compact* (a crowded keyframe past its budget ladder): one sentence,
    each image numbered with a few words and the keep-exactly rule said once
    for all of them -- about half the words of the sentence per image.

    *two_view* (plan 23 stage D4: the story's identity images are front+back
    sheets, ``sheet_mode`` ``two_view``): each ``identity`` image also says
    it shows its one character twice, front and back, to be drawn once
    (:data:`TWO_VIEW_ROLE`). False: the text it always was."""
    outfits = outfits or {}
    if compact:
        return _compact_role_text(roles, outfits, two_view)
    sentences, identity_of = [], {}
    for number, (role, handle) in enumerate(roles, start=1):
        worn = outfits.get(handle) if role in (ROLE_IDENTITY, ROLE_EXPRESSIONS) else None
        here = f"; here they wear {worn}, not the outfit shown" if worn else ""
        if role == ROLE_IDENTITY:
            identity_of.setdefault(handle, number)
            keep = "face, hair, build and proportions" if worn else "identity, proportions and outfit"
            sentences.append(f"Image {number} is {handle}'s reference (keep {keep} exactly{here}).")
            if two_view:
                sentences.append(TWO_VIEW_ROLE.format(number=number))
        elif role == ROLE_EXPRESSIONS:
            identity_of.setdefault(handle, number)
            keep = "identity and face" if worn else "identity, face and outfit"
            sentences.append(f"Image {number} is {handle}'s expression sheet (keep {keep} exactly{here}).")
        elif role == ROLE_SET:
            sentences.append(f"Image {number} is the set (keep layout and light).")
        elif role == ROLE_CONTINUITY:
            sentences.append(f"Image {number} is the previous shot of this scene (keep the set, the light, the "
                             "wardrobe and where everyone stands continuous).")
        elif role == ROLE_TURNAROUND:
            own = identity_of.get(handle)
            sentences.append(f"Image {number} is image {own}'s turnaround." if own
                             else f"Image {number} is {handle}'s turnaround.")
        elif role == ROLE_PROP:
            sentences.append(f"Image {number} is {handle} (keep its shape and colour).")
        else:
            raise ValueError(f"unknown reference role: {role!r}")
    return " ".join(sentences)


def _compact_role_text(roles, outfits, two_view=False) -> str:
    """:func:`role_text`'s *compact* form."""
    parts, identity_of, continuity, changed, twice = [], {}, False, [], []
    for number, (role, handle) in enumerate(roles, start=1):
        if role in (ROLE_IDENTITY, ROLE_EXPRESSIONS):
            identity_of.setdefault(handle, number)
            parts.append(f"{number} {handle}{'' if role == ROLE_IDENTITY else ' (expressions)'}")
            if two_view and role == ROLE_IDENTITY:
                twice.append(_TWO_VIEW_ROLE_COMPACT.format(number=number, handle=handle))
            if outfits.get(handle) and handle not in changed:
                changed.append(handle)
        elif role == ROLE_SET:
            parts.append(f"{number} the set")
        elif role == ROLE_CONTINUITY:
            continuity = True
            parts.append(f"{number} the previous shot of this scene")
        elif role == ROLE_TURNAROUND:
            own = identity_of.get(handle)
            parts.append(f"{number} image {own}'s turnaround" if own else f"{number} {handle}'s turnaround")
        elif role == ROLE_PROP:
            parts.append(f"{number} {handle}")
        else:
            raise ValueError(f"unknown reference role: {role!r}")
    if not parts:
        return ""
    tail = ""
    if continuity:
        tail += ", and continuous with the previous shot"
    if changed:
        tail += ", except that " + " and ".join(f"{handle} wears {outfits[handle]}" for handle in changed)
    if twice:
        tail += "; " + "; ".join(twice)
    return (f"Reference images: {', '.join(parts)}; keep each identity, outfit, shape, layout and light "
            f"exactly{tail}.")


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
                        rendering_words: int = _RENDERING_V2_MAX_WORDS, context=None) -> str:
    """A v2 keyframe prompt (A8), its layers in this fixed order: the
    reference roles (:func:`role_text`), the beat, the staging, the
    composition, the place, the style tail (:func:`layered_style_tail`) and
    the constraints clause. Each layer is one or more whole sentences; an
    empty one is left out. The caller keeps it within its budget
    (``shots.resolve_shot`` shortens the looks and the place first).

    *context* (stage F2: ``shots._layered``'s context layers, by name, the
    ones its budget keeps): ``mood`` (the beat's temperature, the faces, who
    is hurt), ``between`` (what stands between two characters) and
    ``since`` (what changed since the previous shot) after the beat;
    ``bearing`` (each character's posture) after the staging; ``when`` (the
    time of day) after the place. None or empty: the layers it always had."""
    context = context or {}
    layers = (roles_text, beat, context.get("mood", ""), context.get("between", ""), context.get("since", ""),
              staging, context.get("bearing", ""), composition, place_text, context.get("when", ""),
              layered_style_tail(style_lock, rendering_words=rendering_words), constraints)
    text = _collapse_ws(" ".join(_collapse_ws(layer) for layer in layers if layer and layer.strip()))
    return text.replace(".,", ",").replace("..", ".")


# The clip prompt's context layers (stage F2), in the order they are dropped
# when the whole is over budget -- the least valuable first: the camera's
# intent, then the gestures (the slot stage F2 gave the micro-actions), then
# the beat's emotion, then the performance (DEC-252: who speaks, who reacts);
# only then is the motion cut.
_CLIP_DROP_ORDER = ("intent", "micro", "emotion", "performance")


def clip_motion_suffix(style_lock: dict) -> str:
    """The style's motion suffix a v2 clip prompt ends on (DEC-252): its
    ``motion_rules.tier2_prompt_suffix_v2`` -- lively, no stillness, no
    camera --, else its ``tier2_prompt_suffix`` (a lock without the new
    key). The legacy clip prompt (``video_plan.build_video_prompt``) never
    reads this: it keeps ``tier2_prompt_suffix``, byte for byte (RC-Q1)."""
    rules = style_lock["motion_rules"]
    return rules.get("tier2_prompt_suffix_v2") or rules["tier2_prompt_suffix"]


def layered_clip_prompt(style_lock: dict, *, subject: str, motion: str, camera_phrase: str, modifiers=(),
                        secondary: str = "", budget: int = CLIP_V2_MAX_WORDS, emotion: str = "", micro: str = "",
                        intent: str = "", performance: str = "") -> str:
    """A v2 shot's clip prompt (A8), at most *budget* words
    (``CLIP_V2_MAX_WORDS``, or the link's own -- stage F2): the subject and
    what moves (*motion*: the shot's resolved, name-free action, or the
    planned motion; it is said as it is when it already names the
    *subject*), the beat's *emotion*, the *performance* (DEC-252: who
    speaks with the mouth moving, who reacts and how), a fixed *secondary*
    sentence when one is given, the characters' gestures (*micro*), the
    camera phrase with its *modifiers* and the camera's *intent*, the
    identity clause (:data:`IDENTITY_KEEPS`: looks, set and light kept, the
    characters free to move), then the style's v2 motion suffix
    (:func:`clip_motion_suffix`). Over the budget, the four context layers
    go first (:data:`_CLIP_DROP_ORDER`: the intent, the gestures, the
    emotion, the performance), then the motion is cut at a clause
    boundary."""
    suffix = as_sentence(clip_motion_suffix(style_lock))
    moving = _collapse_ws(motion)
    if subject and subject.lower() not in moving.lower():
        moving = f"{subject}: {moving}"
    extras = {"emotion": as_sentence(emotion), "performance": as_sentence(performance), "micro": as_sentence(micro),
              "intent": _strip_trailing_period(_collapse_ws(intent))}
    for dropped in range(len(_CLIP_DROP_ORDER) + 1):
        kept = {name: text for name, text in extras.items() if text and name not in _CLIP_DROP_ORDER[:dropped]}
        camera = as_sentence(", ".join([_strip_trailing_period(camera_phrase)]
                                     + [_strip_trailing_period(m) for m in modifiers if m]
                                     + ([kept["intent"]] if "intent" in kept else [])))
        tail = [kept.get("emotion", ""), kept.get("performance", ""), as_sentence(secondary), kept.get("micro", ""),
                camera, IDENTITY_KEEPS, suffix]
        room = budget - sum(_word_count(part) for part in tail)
        if _word_count(moving) <= room or dropped == len(_CLIP_DROP_ORDER):
            break
    lead = as_sentence(_fit(moving, room, stops=",;.") if _word_count(moving) > room else moving)
    return _collapse_ws(" ".join(part for part in [lead] + tail if part))


# ============================================ phase 7 follow-up, stage E (a clip's sound)
#
# An ambience story's clip (``media_policy.ambience``: the video model's own
# sound kept as ambience under the dialogue, never as dialogue) is asked what
# its shot sounds like, inside its prompt's own word budget: the visual
# prompt, the place's ambience, the shot's sound effects, who speaks
# silently, and a closing sentence that says what must not be heard -- Veo
# 3.1 lite takes no negative prompt (A-103), so the exclusions are said in
# the prompt. A talking character is still shown (its TTS line plays over the
# shot): the prompt never asks the model to voice words.

# The whole prompt's cap: the visual's own (``CLIP_V2_MAX_WORDS``, 80) plus the
# sound's, well under Veo's 1024 tokens (A-103). Stage F2: the sound's share
# (60 words) is what an ambience clip's budget adds to its link's clip budget
# (``prompt_budgets.clip_audio_words``); 140 is the whole with no link known.
CLIP_AUDIO_MAX_WORDS = 140
CLIP_AUDIO_SHARE_WORDS = CLIP_AUDIO_MAX_WORDS - CLIP_V2_MAX_WORDS
# The most the place's ambience and the sound effects take, and the least the
# visual leaves the ambience when it must be cut.
AMBIENCE_MAX_WORDS = 26
SFX_MAX_WORDS = 14
_AMBIENCE_MIN_WORDS = 10
AMBIENCE_HEAD = "Sound: the natural ambience of"
AUDIO_CLOSING = "Audio: ambience and sound effects only; no music, no voices, nobody speaks or sings, no narration."

# The verbs of speech made silent in a clip's visual text (:func:`silent_speech`).
_SPEECH_VERBS = re.compile(
    r"\b(say|says|said|speak|speaks|spoke|speaking|talk|talks|talked|talking|whisper|whispers|whispered|"
    r"whispering|shout|shouts|shouted|shouting|yell|yells|yelled|yelling|scream|screams|screamed|screaming|"
    r"mutter|mutters|muttered|muttering|murmur|murmurs|murmured|murmuring|exclaim|exclaims|exclaimed|sing|sings|"
    r"sang|singing|ask|asks|asked|asking|reply|replies|replied|replying|answer|answers|answered|answering)\b"
    r"(?!\s+silently)", re.IGNORECASE)
# Quoted words a model would voice: "...", “...”, «...».
_QUOTED = re.compile(r"\s*(?:\"[^\"]*\"|“[^”]*”|«[^»]*»)")


def silent_speech(text: str) -> str:
    """*text* with nothing a video model would voice: every quotation left
    out and every verb of speech followed by "silently" ("says silently",
    "whispers silently to ..."). The mouths may move -- the TTS line plays
    over the shot -- but no word is asked for."""
    text = _QUOTED.sub("", text)
    text = _SPEECH_VERBS.sub(lambda match: f"{match.group(1)} silently", text)
    return _collapse_ws(text).replace(" ,", ",").replace(" .", ".")


def _silent_speakers(speakers) -> str:
    """Who speaks in the shot, made silent: one sentence, or ''."""
    who = [speaker for speaker in dict.fromkeys(speakers) if speaker]
    if not who:
        return ""
    names = who[0] if len(who) == 1 else ", ".join(who[:-1]) + " and " + who[-1]
    return as_sentence(f"{names} {'speaks' if len(who) == 1 else 'speak'} silently: their words are not heard")


def clip_prompt_with_audio(visual: str, *, place: str, sfx=(), speakers=(), note: str = "",
                           budget: int = CLIP_AUDIO_MAX_WORDS) -> str:
    """An ambience clip's prompt, at most *budget* words
    (:data:`CLIP_AUDIO_MAX_WORDS`, or the link's own -- stage F2): the
    *visual* prompt with its speech made silent (:func:`silent_speech`),
    the *note* (a re-animate's direction), the natural ambience of *place*
    (its words, time of day and light: the model hears room tone, weather,
    crowd, traffic or nature from them), "Sound effects:" *sfx*, *speakers*
    speaking silently, then :data:`AUDIO_CLOSING`. The closing and the
    silence are never cut; the visual is cut at a clause boundary only when
    it would leave the ambience under its least; the place and the effects
    are cut to what is left (at most :data:`AMBIENCE_MAX_WORDS` and
    :data:`SFX_MAX_WORDS`)."""
    silent = _silent_speakers(speakers)
    note_text = as_sentence(note) if note else ""
    room = budget - _word_count(silent) - _word_count(AUDIO_CLOSING) - _word_count(note_text)
    lead = silent_speech(visual)
    head = _word_count(AMBIENCE_HEAD)
    if _word_count(lead) > room - head - _AMBIENCE_MIN_WORDS:
        lead = _fit(lead, room - head - _AMBIENCE_MIN_WORDS)
    lead = as_sentence(lead)
    room -= _word_count(lead)
    ambience = _fit(place, min(room - head, AMBIENCE_MAX_WORDS))
    ambience = as_sentence(f"{AMBIENCE_HEAD} {ambience}") if ambience else ""
    room -= _word_count(ambience)
    effects = _fit(", ".join(str(cue) for cue in sfx if cue), min(room - 2, SFX_MAX_WORDS)) if sfx else ""
    effects = as_sentence(f"Sound effects: {effects}") if effects else ""
    parts = [lead, note_text, ambience, effects, silent, AUDIO_CLOSING]
    return _collapse_ws(" ".join(part for part in parts if part))


# ================================================== plan 22 (native speech)
#
# A native-speech story's speaking clip (``media_policy.native_speech``): the
# video model speaks the shot's one line itself, lips and voice one
# performance. Shaped as Google's Veo prompting guide writes dialogue -- the
# camera first, the speaker with a voice description and the line in quotes,
# the ambience on its own line -- with the sentences that must reach the
# model whole: the quoted line, the voice, what is heard and what is never
# burned in.

SPEECH_CLIP_MAX_WORDS = 200
SPEECH_LOOK_MAX_WORDS = 12
SPEECH_ACTION_MAX_WORDS = 24
SPEECH_PLACE_MAX_WORDS = 18
SPEECH_REACTION_MAX_WORDS = 10
NO_ON_SCREEN_TEXT = "No subtitles, no captions, no on-screen text."
SPEECH_NO_OTHER_SOUND = "No music, no narrator, no other voice."
LANGUAGE_NAMES = {"fr": "French", "en": "English"}
# The speech prompt's context layers, in the order they are dropped when the
# whole is over budget -- the least valuable first; the line, the voice, the
# Audio sentence and the closing sentences are never dropped.
_SPEECH_DROP_ORDER = ("context", "style", "identity", "reaction", "camera", "action", "look")

_AGE_WORDS = {"child": "a child", "young": "a young", "adult": "an adult", "elder": "an elderly"}
_GENDER_WORDS = {"female": "woman", "male": "man", "neutral": "person"}


def voice_line(voice_hints) -> str:
    """How a character sounds, in one phrase, from its ``voice_hints``
    (gender, age, style tags, direction): built from them alone, so it is the
    same words in every clip of that character (the voice is asked the same
    way each time). ``"a clear voice"`` with no hints."""
    hints = voice_hints if isinstance(voice_hints, dict) else {}
    tags = [str(tag).strip() for tag in hints.get("style_tags") or () if str(tag).strip()]
    head = f"a {', '.join(tags)} voice" if tags else "a clear voice"
    age, gender = hints.get("age"), hints.get("gender")
    if age == "child":
        whose = "a child"
    elif age in _AGE_WORDS or gender in _GENDER_WORDS:
        whose = f"{_AGE_WORDS.get(age, 'a')} {_GENDER_WORDS.get(gender, 'person')}"
    else:
        whose = ""
    text = f"{head} of {whose}" if whose else head
    direction = _fit(_collapse_ws(str(hints.get("direction") or "")), 12)
    if direction:
        direction = _strip_trailing_period(direction)
        if direction[:1].isupper() and direction[1:2].islower():
            direction = direction[0].lower() + direction[1:]
        text += f", {direction}"
    return _collapse_ws(text)


def speech_clip_prompt(style_lock: dict, *, speaker: str, look: str, action: str, listener: str, language: str,
                       voice: str, line: str, reaction: str, camera_phrase: str, place: str, ambience: str,
                       budget: int = SPEECH_CLIP_MAX_WORDS, note: str = "") -> str:
    """A speaking clip's prompt (plan 22), at most *budget* words
    (:data:`SPEECH_CLIP_MAX_WORDS`, or the link's own):

    "{Camera}. {Speaker}, {look}, {action}, looks at {listener} and says in
    {language}, in {voice}, "{line}". {Listener} listens without speaking,
    mouth closed, {reaction}. {Place}. {IDENTITY_KEEPS} {style motion
    suffix} Audio: only {speaker}'s voice speaking {language}, close and
    clear, lips in sync with the words. Ambient noise: {ambience}, low
    underneath. No music, no narrator, no other voice. No subtitles, no
    captions, no on-screen text."

    *speaker* and *listener* are handles, never names (spec 2.3); *listener*
    empty: the speaker talks straight ahead. The quoted *line* (in the
    story's *language*), the *voice* phrase (:func:`voice_line`), the Audio
    sentence and the two closing sentences are never cut; over the budget
    the context layers go first (:data:`_SPEECH_DROP_ORDER`)."""
    lang = LANGUAGE_NAMES.get(language, language)
    quoted = _collapse_ws(str(line)).replace('"', "'")
    who = _collapse_ws(speaker) or "the character"
    other = _collapse_ws(listener)
    heard = (f"Audio: only {who}'s voice speaking {lang}, close and clear, lips in sync with the words.")
    ambient = as_sentence(f"Ambient noise: {_fit(ambience, SPEECH_PLACE_MAX_WORDS) or 'the room tone'}, low underneath")
    closing = [heard, ambient, SPEECH_NO_OTHER_SOUND, NO_ON_SCREEN_TEXT]
    note_text = as_sentence(note) if note else ""
    layers = {
        "camera": as_sentence(camera_phrase),
        "look": _strip_trailing_period(_fit(_collapse_ws(look), SPEECH_LOOK_MAX_WORDS)),
        "action": _strip_trailing_period(_fit(_collapse_ws(action), SPEECH_ACTION_MAX_WORDS)),
        "reaction": _strip_trailing_period(_fit(_collapse_ws(reaction), SPEECH_REACTION_MAX_WORDS)),
        "context": as_sentence(_fit(_collapse_ws(place), SPEECH_PLACE_MAX_WORDS)),
        "identity": IDENTITY_KEEPS,
        "style": as_sentence(clip_motion_suffix(style_lock)),
    }

    def build(kept):
        head = [who[0].upper() + who[1:]]
        for name in ("look", "action"):
            if kept.get(name):
                head.append(kept[name])
        target = f"looks at {other}" if other else "looks straight ahead"
        end = "" if quoted[-1:] in ".!?…" else "."
        speech = f"{', '.join(head)}, {target} and says in {lang}, in {voice}, \"{quoted}\"{end}"
        listens = ""
        if other:
            listens = f"{other[0].upper() + other[1:]} listens without speaking, mouth closed"
            listens = as_sentence(f"{listens}, {kept['reaction']}" if kept.get("reaction") else listens)
        parts = [kept.get("camera", ""), speech, listens, kept.get("context", ""), note_text,
                 kept.get("identity", ""), kept.get("style", "")] + closing
        return _collapse_ws(" ".join(part for part in parts if part))

    for dropped in range(len(_SPEECH_DROP_ORDER) + 1):
        kept = {name: text for name, text in layers.items() if text and name not in _SPEECH_DROP_ORDER[:dropped]}
        prompt = build(kept)
        if _word_count(prompt) <= budget:
            return prompt
    return prompt


def speech_prompt_sentences(prompt: str) -> dict:
    """The never-dropped parts of a speech clip *prompt*, found in it (for
    the brief and the checks): the quoted line, the Audio sentence, the
    no-other-sound and the no-on-screen-text sentences."""
    quoted = re.search(r'"([^"]*)"', prompt)
    audio = re.search(r"Audio: [^.]*\.", prompt)
    return {"line": quoted.group(1) if quoted else None, "audio": audio.group(0) if audio else None,
            "no_other_sound": SPEECH_NO_OTHER_SOUND in prompt, "no_on_screen_text": NO_ON_SCREEN_TEXT in prompt}


# ============================== plan 23 stage D6 (action-dense clip prompts)
#
# ``generation_profile.prompt_style: "action"`` (``media_policy.prompt_style``):
# the way Flow / Seedance prompts are written by hand -- present tense, ONE
# continuous physical action, a colour/species anchor per character repeated
# at every mention (``shots.character_anchor``: never the bare handle), the
# place named once in a few words, the sounds inline, exactly one camera
# phrase. No layered context, no style suffix, no identity clause: the
# anchors and the reference images carry the look. Absent (``studio``) every
# prompt above is untouched; these two builders are reached only through
# ``steps/clips.speech_request_parts`` / ``clip_request_parts``.

ACTION_MAX_WORDS = 40
ACTION_PLACE_MAX_WORDS = 10
# The action's shorter rung over a tight budget: a whole clause of at most this many words, or none.
ACTION_SHORT_WORDS = 24
ACTION_SOUNDS_MAX_WORDS = 16
# What cuts a place's descriptor down to its head ("a swimming pool surrounded by loungers").
_PLACE_CUT_MARKERS = (" with ", " surrounded by ", " serving as ", " featuring ", " filled with ", " where ",
                      " that ", " which ", ";")


def _decap(text: str) -> str:
    """*text* with its first letter lower case when the rest of that word is lower case already."""
    word = text.split(" ", 1)[0]
    if word[:1].isupper() and word[1:] == word[1:].lower() and word != "I":
        return word[0].lower() + text[1:]
    return text


def place_anchor(descriptor, max_words: int = ACTION_PLACE_MAX_WORDS) -> str:
    """A place in at most *max_words* words, from its descriptor: its first
    sentence cut at the first ' with ' / ' surrounded by ' / ... and, still
    over, at a clause boundary ("A dimly lit tropical wooden confession
    booth with a carved bamboo chair" -> "a dimly lit tropical wooden
    confession booth"). '' without a descriptor."""
    text = _collapse_ws(str(descriptor or ""))
    end = text.find(". ")
    text = (text[:end] if end != -1 else text).rstrip(". ")
    cuts = [text.find(marker) for marker in _PLACE_CUT_MARKERS if text.find(marker) > 0]
    if cuts:
        text = text[:min(cuts)]
    if _word_count(text) > max_words:
        text = _fit(text, max_words) or " ".join(text.split()[:max_words])
    return _decap(text.strip(" ,;"))


def swap_phrases(text: str, mapping: dict) -> str:
    """*text* with every key of *mapping* replaced by its value, as a whole
    phrase whatever its case, in one pass (longest key first, so a value
    holding its own key is never swapped twice); a value takes the capital
    its key had. The handles of a resolved action become the characters'
    anchors with it."""
    keys = sorted((key for key in mapping if key), key=len, reverse=True)
    if not keys or not text:
        return text
    lowered = {key.lower(): value for key, value in mapping.items() if key}
    pattern = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(key) for key in keys) + r")(?!\w)", re.IGNORECASE)

    def swap(match):
        value = lowered[match.group(0).lower()]
        return value[0].upper() + value[1:] if match.group(0)[:1].isupper() and value else value

    return pattern.sub(swap, text)


_DANGLING = frozenset({"a", "an", "the", "of", "to", "with", "and", "or", "in", "on", "at", "by", "for", "from",
                       "into", "onto", "over", "under", "his", "her", "its", "their", "then", "as", "while"})


def action_sentence(text, max_words: int = ACTION_MAX_WORDS, *, clauses: bool = False) -> str:
    """ONE continuous action from *text* (a shot's motion or action): its
    first sentence, at most *max_words* words, cut at a clause boundary;
    over *max_words* with no clause that fits, cut at the word and the
    function words it leaves dangling ("slams the") taken off -- unless
    *clauses*: then '' (a shorter action is a whole clause or none)."""
    text = _collapse_ws(str(text or ""))
    pieces = _pieces(text, ".!?")
    first = (pieces[0] if pieces else "").rstrip(".!? ")
    if _word_count(first) > max_words:
        cut = _fit(first, max_words)
        if not cut and not clauses:
            words = first.split()[:max_words]
            while words and words[-1].lower().strip(",;") in _DANGLING:
                words.pop()
            cut = " ".join(words)
        first = cut
    return first.strip(" ,;")


def _action_sounds(sfx, ambience: str, limit: int = ACTION_SOUNDS_MAX_WORDS) -> str:
    """"Sounds: a door slam, the night ambience of the setting." (inline
    sounds of a shot: its effects, then its place's ambience), or ''."""
    effects = [_collapse_ws(str(cue)) for cue in sfx or () if str(cue).strip()]
    heard = ", ".join(effects)
    if ambience:
        heard = f"{heard}, {ambience}" if heard else ambience
    heard = _fit(heard, limit) if _word_count(heard) > limit else heard
    return as_sentence(f"Sounds: {heard}") if heard else ""


def _scene_head(place: str, action: str) -> str:
    """"In a swimming pool, the red lipstick walks ..." (the place named once, then the action)."""
    place = _collapse_ws(place)
    action = _collapse_ws(action)
    if place and action:
        return as_sentence(f"In {place}, {action}")
    return as_sentence(action or (f"In {place}" if place else ""))


def speech_clip_prompt_action(*, speaker: str, listener: str, action: str, language: str, voice: str, line: str,
                              reaction: str, camera_phrase: str, place: str, sfx=(), ambience: str = "",
                              budget: int = SPEECH_CLIP_MAX_WORDS, note: str = "") -> str:
    """A speaking clip's prompt in the action style (plan 23 stage D6), at
    most *budget* words (``prompt_budgets.speech_clip_words(link)``):

    "{Camera}. In {place}, {action}. {Speaker} looks at {listener} and says
    in {language}, in {voice}, "{line}". {Listener} listens without
    speaking, mouth closed, {reaction}. Sounds: {sfx}, {ambience}. {note}
    Audio: only {speaker}'s voice speaking {language}, close and clear,
    lips in sync with the words. No music, no narrator, no other voice. No
    subtitles, no captions, no on-screen text."

    *speaker* and *listener* are the characters' anchors (``shots.character_anchor``),
    *action* the shot's motion with every character already named by its
    anchor (``steps/clips.action_inputs``), *place* a place's descriptor
    (:func:`place_anchor`: at most 10 words, said once). The quoted *line*,
    the speaker's sentence and the three closing sentences are never cut; over
    the budget the sounds go first, then the reaction, the place, the
    listener's sentence, and last the action (shorter, then not at all)."""
    lang = LANGUAGE_NAMES.get(language, language)
    quoted = _collapse_ws(str(line)).replace('"', "'")
    who = _collapse_ws(speaker) or "the character"
    other = _collapse_ws(listener)
    heard = f"Audio: only {who}'s voice speaking {lang}, close and clear, lips in sync with the words."
    closing = [heard, SPEECH_NO_OTHER_SOUND, NO_ON_SCREEN_TEXT]
    note_text = as_sentence(note) if note else ""
    where = place_anchor(place)
    moving = action_sentence(action)
    sounds = _action_sounds(sfx, ambience)
    camera = as_sentence(camera_phrase)
    react = _strip_trailing_period(_fit(_collapse_ws(reaction), SPEECH_REACTION_MAX_WORDS))
    end = "" if quoted[-1:] in ".!?…" else "."
    target = f"looks at {other}" if other else "looks straight ahead"
    speech = f"{who[0].upper() + who[1:]} {target} and says in {lang}, in {voice}, \"{quoted}\"{end}"

    def build(drop, action_words):
        scene = _scene_head("" if "place" in drop else where,
                            action_sentence(moving, action_words, clauses=action_words < ACTION_MAX_WORDS)
                            if action_words else "")
        listens = ""
        if other and "listens" not in drop:
            listens = f"{other[0].upper() + other[1:]} listens without speaking, mouth closed"
            listens = as_sentence(f"{listens}, {react}" if react and "reaction" not in drop else listens)
        parts = ["" if "camera" in drop else camera, scene, speech, listens, "" if "sounds" in drop else sounds,
                 note_text] + closing
        return _collapse_ws(" ".join(part for part in parts if part))

    ladder = (((), ACTION_MAX_WORDS), (("sounds",), ACTION_MAX_WORDS), (("sounds", "reaction"), ACTION_MAX_WORDS),
              (("sounds", "reaction", "place"), ACTION_MAX_WORDS),
              (("sounds", "reaction", "place", "listens"), ACTION_MAX_WORDS),
              (("sounds", "reaction", "place", "listens"), ACTION_SHORT_WORDS),
              (("sounds", "reaction", "place", "listens", "camera"), ACTION_SHORT_WORDS),
              (("sounds", "reaction", "place", "listens", "camera"), 0))
    for drop, action_words in ladder:
        prompt = build(drop, action_words)
        if _word_count(prompt) <= budget:
            return prompt
    return prompt


def clip_prompt_action(*, action: str, camera_phrase: str, place: str, sfx=(), ambience: str = "", speakers=(),
                       sounds: bool = False, note: str = "", budget: int = CLIP_AUDIO_MAX_WORDS) -> str:
    """A silent shot's clip prompt in the action style (plan 23 stage D6),
    at most *budget* words:

    "{Camera}. In {place}, {action}. {note}" -- and, when the clip asks for
    its sound (*sounds*: an ambience story's, ``media_policy.ambience``, or a
    native-speech story's silent shot), also "Sounds: {sfx}, {ambience}.",
    "{speakers} speak silently: their words are not heard." (the TTS line
    plays over the shot: *action* is made silent, :func:`silent_speech`) and
    :data:`AUDIO_CLOSING`, which are never cut. *action* has every character
    already named by its anchor (``steps/clips.action_inputs``), *speakers*
    are anchors. Over the budget the sounds go first, then the place, then
    the action (shorter, then not at all), then the camera."""
    moving = action_sentence(silent_speech(action) if sounds else action)
    where = place_anchor(place)
    heard = _action_sounds(sfx, ambience) if sounds else ""
    silent = _silent_speakers(speakers) if sounds else ""
    camera = as_sentence(camera_phrase)
    note_text = as_sentence(note) if note else ""
    closing = AUDIO_CLOSING if sounds else ""

    def build(drop, action_words):
        scene = _scene_head("" if "place" in drop else where,
                            action_sentence(moving, action_words, clauses=action_words < ACTION_MAX_WORDS)
                            if action_words else "")
        parts = ["" if "camera" in drop else camera, scene, "" if "sounds" in drop else heard, silent, note_text,
                 closing]
        return _collapse_ws(" ".join(part for part in parts if part))

    ladder = (((), ACTION_MAX_WORDS), (("sounds",), ACTION_MAX_WORDS), (("sounds", "place"), ACTION_MAX_WORDS),
              (("sounds", "place"), ACTION_SHORT_WORDS), (("sounds", "place", "camera"), ACTION_SHORT_WORDS), (("sounds", "place", "camera"), 0))
    for drop, action_words in ladder:
        prompt = build(drop, action_words)
        if _word_count(prompt) <= budget:
            return prompt
    return prompt
