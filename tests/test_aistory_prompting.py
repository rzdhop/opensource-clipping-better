"""Tests for clipping.aistory.prompting (spec 5, 2.3).

Golden strings are asserted literally: prompting.py assembles fixed
skeletons around template fields, so for a fixed set of inputs the output
is fully deterministic and worth pinning exactly, not just approximately.
"""

from __future__ import annotations

import inspect

import pytest

from clipping.aistory import prompting, schemas, templates

FRUIT_DRAMA = templates.load_style("fruit_drama")
FAMILY_3D = templates.load_style("family_3d")

DESCRIPTOR = (
    "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh "
    "visible at the mouth"
)
SIGNATURE_ITEMS = ["thin gold chain", "white linen shirt", "left-eyebrow scar"]

BUILDER_FUNCTIONS = [
    prompting.shot_prompt,
    prompting.negative_prompt,
    prompting.portrait_prompt,
    prompting.turnaround_prompt,
    prompting.expressions_prompt,
    prompting.master_plate_prompt,
    prompting.character_prompt_block,
]

BUILDERS_WITH_SIGNATURE_ITEMS = [
    prompting.portrait_prompt,
    prompting.turnaround_prompt,
    prompting.expressions_prompt,
    prompting.character_prompt_block,
]


# ------------------------------------------------------------- golden strings

def test_shot_prompt_fruit_drama_golden():
    result = prompting.shot_prompt(
        FRUIT_DRAMA,
        subjects_block="an anthropomorphic kiwi in a white linen shirt faces a mango in a red dress",
        action="the kiwi holds up a signed contract",
        place_block="the manor's gravel courtyard",
        time_variant="late afternoon golden hour",
        framing="medium_two_shot",
    )
    expected = (
        "an anthropomorphic kiwi in a white linen shirt faces a mango in a red "
        "dress. the kiwi holds up a signed contract. Setting: the manor's gravel courtyard, late afternoon golden "
        "hour. Style: photorealistic 3D render of anthropomorphic fruits and "
        "vegetables with expressive human-like faces (eyes, brows, mouths) on "
        "realistic fruit heads, human-proportioned bodies in real fabric outfits, "
        "subsurface scattering on fruit skin, visible pores and fuzz, glossy "
        "highlights, high-end CGI commercial quality, Octane-style render. "
        "Palette: saturated natural fruit colours against warm neutral sets. "
        "The head is one recognisable whole fruit or vegetable at human head "
        "scale; the face (eyes, brows, mouth with teeth) is carved into its "
        "surface, not pasted on. Bodies are human, dressed in realistic "
        "contemporary clothes that carry the character's signature items and "
        "tell their social status (a torn tee and backpack vs a black suit and "
        "tie). No hands as fruit — hands are human. Keep exact fruit species, "
        "ripeness, colour and outfit identical in every image. Camera: medium "
        "two-shot, both characters waist-up, 50mm look, shallow depth of field. "
        "Lighting: warm key light with a soft cool fill, golden-hour or practical "
        "interior lamps, dramatic rim light on reveals. Vertical 9:16 composition, "
        "subject kept in the central safe area (leave the bottom 22% free of "
        "faces for subtitles). ultra detailed, 8k, sharp focus"
    )
    assert result == expected


def test_shot_prompt_family_3d_golden():
    result = prompting.shot_prompt(
        FAMILY_3D,
        subjects_block="Mama Coco kneels beside the broken lamp",
        action="she cradles it like a wound",
        place_block="the family's cramped living room",
        time_variant="just after sundown",
        framing="close_up",
    )
    expected = (
        "Mama Coco kneels beside the broken lamp. she cradles it like a wound. "
        "Setting: the family's cramped living room, just after sundown. Style: "
        "high-end 3D animated feature film look, stylised proportions with large "
        "expressive eyes, soft rounded shapes, subsurface skin, detailed fabric "
        "and fur textures, global illumination, cinematic depth of field. "
        "Palette: muted grey-blue environment with one warm saturated accent on "
        "the character, soft pastel shadows. Each character has one "
        "silhouette-defining shape, one primary colour and 2–3 signature items "
        "that never change. Eyes are large and readable at phone size. "
        "Expressions are broad and clear. The character carries the only warm, "
        "saturated colour in the frame; the environment stays muted. Camera: "
        "tight close-up on the face, 85mm look, shallow depth of field. "
        "Lighting: soft warm key, bounced fill, glowing rim light, volumetric "
        "sunlight through windows or leaves. Vertical 9:16 composition, subject "
        "kept in the central safe area (leave the bottom 22% free of faces for "
        "subtitles). highly detailed, rendered in 4k"
    )
    assert result == expected


def test_negative_prompt_fruit_drama_golden():
    expected = (
        "text, watermark, logo, signature, extra limbs, extra fingers, deformed "
        "hands, duplicated character, cropped face, blurry, low resolution, jpeg "
        "artifacts, out of frame, split screen, collage, frame border, caption, "
        "cartoon, 2D, flat shading, anime, fruit with stick limbs, fruit bowl, "
        "food photography, human head"
    )
    assert prompting.negative_prompt(FRUIT_DRAMA) == expected


def test_portrait_prompt_fruit_drama_golden():
    result = prompting.portrait_prompt(
        FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS
    )
    expected = (
        "Character portrait, an anthropomorphic kiwi with fuzzy brown skin and "
        "bright green flesh visible at the mouth, wearing thin gold chain, white "
        "linen shirt, left-eyebrow scar. Neutral expression, looking at camera, "
        "three-quarter view, chest-up. photorealistic 3D render of anthropomorphic "
        "fruits and vegetables with expressive human-like faces (eyes, brows, "
        "mouths) on realistic fruit heads, human-proportioned bodies in real "
        "fabric outfits, subsurface scattering on fruit skin, visible pores and "
        "fuzz, glossy highlights, high-end CGI commercial quality, Octane-style "
        "render. The head is one recognisable whole fruit or vegetable at human "
        "head scale; the face (eyes, brows, mouth with teeth) is carved into its "
        "surface, not pasted on. Bodies are human, dressed in realistic "
        "contemporary clothes that carry the character's signature items and "
        "tell their social status (a torn tee and backpack vs a black suit and "
        "tie). No hands as fruit — hands are human. Keep exact fruit species, "
        "ripeness, colour and outfit identical in every image. Plain light grey "
        "background, even soft studio lighting, no props, no text. Vertical 9:16."
    )
    assert result == expected


def test_turnaround_prompt_fruit_drama_golden():
    result = prompting.turnaround_prompt(
        FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS
    )
    expected = (
        "Character design turnaround sheet of the same character: an "
        "anthropomorphic kiwi with fuzzy brown skin and bright green flesh "
        "visible at the mouth, thin gold chain, white linen shirt, left-eyebrow "
        "scar. Four full-body views side by side in one row: front, "
        "three-quarter, profile, back. Identical proportions and outfit in "
        "every view. photorealistic 3D render of anthropomorphic fruits and "
        "vegetables with expressive human-like faces (eyes, brows, mouths) on "
        "realistic fruit heads, human-proportioned bodies in real fabric "
        "outfits, subsurface scattering on fruit skin, visible pores and fuzz, "
        "glossy highlights, high-end CGI commercial quality, Octane-style "
        "render. The head is one recognisable whole fruit or vegetable at human "
        "head scale; the face (eyes, brows, mouth with teeth) is carved into its "
        "surface, not pasted on. Bodies are human, dressed in realistic "
        "contemporary clothes that carry the character's signature items and "
        "tell their social status (a torn tee and backpack vs a black suit and "
        "tie). No hands as fruit — hands are human. Keep exact fruit species, "
        "ripeness, colour and outfit identical in every image. Plain light grey "
        "background, flat even lighting, no text, no labels."
    )
    assert result == expected


def test_expressions_prompt_fruit_drama_golden():
    result = prompting.expressions_prompt(
        FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS
    )
    expected = (
        "Expression sheet of the same character: an anthropomorphic kiwi with "
        "fuzzy brown skin and bright green flesh visible at the mouth, thin gold "
        "chain, white linen shirt, left-eyebrow scar. Six head-and-shoulders "
        "portraits in a 3x2 grid: neutral, happy, angry, shocked, sad, scheming. "
        "Same face, same outfit, same lighting in every cell. photorealistic 3D "
        "render of anthropomorphic fruits and vegetables with expressive "
        "human-like faces (eyes, brows, mouths) on realistic fruit heads, "
        "human-proportioned bodies in real fabric outfits, subsurface scattering "
        "on fruit skin, visible pores and fuzz, glossy highlights, high-end CGI "
        "commercial quality, Octane-style render. Plain light grey background, "
        "no text."
    )
    assert result == expected


def test_master_plate_prompt_fruit_drama_golden():
    result = prompting.master_plate_prompt(
        FRUIT_DRAMA,
        place_descriptor="the manor's gravel courtyard with wrought iron gates",
        time_variant="late afternoon golden hour",
    )
    expected = (
        "Establishing wide shot of the manor's gravel courtyard with wrought "
        "iron gates, late afternoon golden hour, no people, no characters. "
        "real-world sets — manor gates, gravel courtyards, derelict interiors "
        "with chandeliers, beach camps, villa kitchens, restaurants — "
        "photographed like a reality-TV show or a live-action comedy, props at "
        "human scale; exteriors in golden hour, interiors cold blue-grey with "
        "warm candle or lamp practicals photorealistic 3D render of "
        "anthropomorphic fruits and vegetables with expressive human-like faces "
        "(eyes, brows, mouths) on realistic fruit heads, human-proportioned "
        "bodies in real fabric outfits, subsurface scattering on fruit skin, "
        "visible pores and fuzz, glossy highlights, high-end CGI commercial "
        "quality, Octane-style render. Palette: saturated natural fruit colours "
        "against warm neutral sets. Camera: wide, eye level, 24mm equivalent. "
        "Lighting: warm key light with a soft cool fill, golden-hour or "
        "practical interior lamps, dramatic rim light on reveals. Vertical "
        "9:16, horizon in the upper third, foreground detail in the lower "
        "third. ultra detailed, 8k, sharp focus"
    )
    assert result == expected


# --------------------------------------------------------------- lens_phrase

def test_lens_phrase_resolved_per_template():
    expected = {
        "anime": "natural lens, shallow depth of field",
        "cartoon_flat": "natural lens, no depth of field",
        "cinematic_real": "50–85mm look, shallow depth of field",
        "claymation": "macro lens look, shallow depth of field",
        "family_3d": "35mm look, shallow depth of field",
        "fruit_drama": "50mm look, shallow depth of field",
        "storybook_watercolor": "natural lens, shallow depth of field",
    }
    for style_id, expected_lens in expected.items():
        style_lock = templates.load_style(style_id)
        assert prompting.lens_phrase(style_lock, "medium_single") == expected_lens


def test_lens_phrase_fixed_for_wide_and_tight_framings():
    for style_id in templates.list_style_ids():
        style_lock = templates.load_style(style_id)
        assert prompting.lens_phrase(style_lock, "wide_establishing") == "24mm equivalent, deep focus"
        for framing in ("close_up", "extreme_close_up", "insert_prop"):
            assert prompting.lens_phrase(style_lock, framing) == "85mm look, shallow depth of field"


# ---------------------------------------------------------- FRAMING_PHRASES

def test_framing_phrases_cover_every_framing():
    assert set(prompting.FRAMING_PHRASES.keys()) == set(schemas.FRAMINGS)


def test_framing_phrases_examples_verbatim():
    assert prompting.FRAMING_PHRASES["medium_two_shot"] == "medium two-shot, both characters waist-up"
    assert prompting.FRAMING_PHRASES["close_up"] == "tight close-up on the face"


def test_shot_prompt_unknown_framing_raises():
    with pytest.raises(ValueError):
        prompting.shot_prompt(
            FRUIT_DRAMA,
            subjects_block="a",
            action="b",
            place_block="c",
            time_variant="d",
            framing="nonexistent_framing",
        )


def test_lens_phrase_unknown_framing_raises():
    with pytest.raises(ValueError):
        prompting.lens_phrase(FRUIT_DRAMA, "nonexistent_framing")


# ------------------------------------------------------------- empty items

@pytest.mark.parametrize("builder", BUILDERS_WITH_SIGNATURE_ITEMS)
def test_empty_signature_items_raises(builder):
    kwargs = {"descriptor": DESCRIPTOR, "signature_items": []}
    with pytest.raises(ValueError):
        builder(FRUIT_DRAMA, **kwargs)


# --------------------------------------------------------- builder signatures

@pytest.mark.parametrize("builder", BUILDER_FUNCTIONS)
def test_no_builder_takes_a_name_parameter(builder):
    assert "name" not in inspect.signature(builder).parameters


# ----------------------------------------------------------- order & hygiene

@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_shot_prompt_order_holds_for_every_template(style_id):
    style_lock = templates.load_style(style_id)
    for framing in schemas.FRAMINGS:
        result = prompting.shot_prompt(
            style_lock,
            subjects_block="Subject stands in frame",
            action="Subject reacts",
            place_block="a place",
            time_variant="a time",
            framing=framing,
        )
        idx_subject = result.index("Subject stands in frame")
        idx_action = result.index("Subject reacts")
        idx_setting = result.index("Setting:")
        idx_style = result.index("Style:")
        idx_camera = result.index("Camera:")
        idx_lighting = result.index("Lighting:")
        idx_quality = result.index(style_lock["quality_tail"])
        assert (
            idx_subject
            < idx_action
            < idx_setting
            < idx_style
            < idx_camera
            < idx_lighting
            < idx_quality
        )


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_camera_paragraph_never_leaks_into_shot_prompt(style_id):
    style_lock = templates.load_style(style_id)
    for framing in schemas.FRAMINGS:
        result = prompting.shot_prompt(
            style_lock,
            subjects_block="Subject stands in frame",
            action="Subject reacts",
            place_block="a place",
            time_variant="a time",
            framing=framing,
        )
        assert style_lock["camera"] not in result


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_shot_prompt_hygiene_and_verbatim_fields(style_id):
    style_lock = templates.load_style(style_id)
    result = prompting.shot_prompt(
        style_lock,
        subjects_block="Subject stands in frame",
        action="Subject reacts",
        place_block="a place",
        time_variant="a time",
        framing="medium_single",
    )
    assert ".." not in result
    assert "  " not in result
    assert not result.endswith(" ")
    assert style_lock["rendering"] in result
    assert style_lock["lighting"] in result


def test_golden_outputs_have_no_double_punctuation_or_spaces():
    outputs = [
        prompting.negative_prompt(FRUIT_DRAMA),
        prompting.portrait_prompt(FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS),
        prompting.turnaround_prompt(FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS),
        prompting.expressions_prompt(FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS),
        prompting.master_plate_prompt(
            FRUIT_DRAMA,
            place_descriptor="a place",
            time_variant="a time",
        ),
    ]
    for output in outputs:
        assert ".." not in output
        assert "  " not in output
        assert not output.endswith(" ")


# ------------------------------------------------------- character_prompt_block

def test_character_prompt_block_is_pure_and_deterministic():
    result1 = prompting.character_prompt_block(
        FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS
    )
    result2 = prompting.character_prompt_block(
        FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=list(SIGNATURE_ITEMS)
    )
    expected = (
        "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh "
        "visible at the mouth, wearing thin gold chain, white linen shirt, "
        "left-eyebrow scar. The head is one recognisable whole fruit or "
        "vegetable at human head scale; the face (eyes, brows, mouth with "
        "teeth) is carved into its surface, not pasted on. Bodies are human, "
        "dressed in realistic contemporary clothes that carry the character's "
        "signature items and tell their social status (a torn tee and backpack "
        "vs a black suit and tie). No hands as fruit — hands are human. Keep "
        "exact fruit species, ripeness, colour and outfit identical in every "
        "image."
    )
    assert result1 == result2 == expected
