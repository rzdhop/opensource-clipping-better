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
    prompting.place_prompt_block,
    prompting.prop_prompt_block,
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
        # Re-pinned on purpose (DEC-305, plan 28): fruit_drama's environment_rules said "photographed like a
        # reality-TV show or a live-action comedy", against the style's animated medium; now a 3D animated villa set.
        "sets staged like a reality-TV villa — manor gates, gravel courtyards, "
        "derelict interiors with chandeliers, beach camps, villa kitchens, a "
        "pool, confessional corners, restaurants, bold set dressing — rendered "
        "as a stylised 3D animated set, never photographed, never live-action, "
        "props at human scale; exteriors in golden hour, interiors cold "
        "blue-grey with "
        # Re-pinned on purpose (DEC-220, phase 7 stage 1): environment_rules now
        # ends with a period before the rendering text.
        "warm candle or lamp practicals. photorealistic 3D render of "
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
        prompting.character_prompt_block(FRUIT_DRAMA, descriptor=DESCRIPTOR, signature_items=SIGNATURE_ITEMS),
    ]
    for output in outputs:
        assert ".." not in output
        assert "  " not in output
        assert not output.endswith(" ")


def test_sheet_and_plate_prompts_have_no_period_comma():
    """D3 fix (phase 7 stage 1): a descriptor that already ends in "." must
    not produce a run-on like "...mouth., wearing ..." once the builder's
    own skeleton appends ", wearing ..." right after it. Also: the
    environment_rules sentence feeding master_plate_prompt/variant_prompt
    must be terminated before the rendering text follows -- never a run-on
    like "...practicals photorealistic 3D render...". """
    descriptor = "a tired-looking kiwi sitting on a park bench with faded green paint."
    place_descriptor = "a weathered wooden bench beneath an old oak tree."

    portrait = prompting.portrait_prompt(FRUIT_DRAMA, descriptor=descriptor, signature_items=SIGNATURE_ITEMS)
    turnaround = prompting.turnaround_prompt(FRUIT_DRAMA, descriptor=descriptor, signature_items=SIGNATURE_ITEMS)
    expressions = prompting.expressions_prompt(FRUIT_DRAMA, descriptor=descriptor, signature_items=SIGNATURE_ITEMS)
    block = prompting.character_prompt_block(FRUIT_DRAMA, descriptor=descriptor, signature_items=SIGNATURE_ITEMS)
    master_plate = prompting.master_plate_prompt(FRUIT_DRAMA, place_descriptor=place_descriptor, time_variant="day")
    variant = prompting.variant_prompt(FRUIT_DRAMA, place_descriptor=place_descriptor, variant="night")

    for output in (portrait, turnaround, expressions, block, master_plate, variant):
        assert ".," not in output

    # Sanity: the fixture style's own environment_rules has no trailing
    # period (true of every shipped style template, spec 5) -- the bug this
    # guards is specifically a MISSING period, not a doubled one.
    env_rules = FRUIT_DRAMA["environment_rules"].strip()
    assert not env_rules.endswith((".", "!", "?"))
    assert f"{env_rules}. {FRUIT_DRAMA['rendering']}" in master_plate
    assert f"{env_rules}. {FRUIT_DRAMA['rendering']}" in variant


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


# ----------------------------------------------------------- place_prompt_block

PLACE_DESCRIPTOR = (
    "a sprawling open-air market square with cracked terracotta tiles and "
    "faded striped awnings"
)
LAYOUT_NOTES = (
    "the fruit stalls line the left side, the stage sits at the back, the "
    "fountain anchors the foreground"
)


def test_place_prompt_block_fruit_drama_golden():
    result = prompting.place_prompt_block(
        FRUIT_DRAMA, descriptor=PLACE_DESCRIPTOR, layout_notes=LAYOUT_NOTES
    )
    expected = (
        "a sprawling open-air market square with cracked terracotta tiles and "
        "faded striped awnings. the fruit stalls line the left side, the "
        "stage sits at the back, the fountain anchors the foreground. "
        # Re-pinned on purpose (DEC-305, plan 28): fruit_drama's environment_rules said "photographed like a
        # reality-TV show or a live-action comedy", against the style's animated medium; now a 3D animated villa set.
        "sets staged like a reality-TV villa — manor gates, gravel courtyards, "
        "derelict interiors with chandeliers, beach camps, villa kitchens, a "
        "pool, confessional corners, restaurants, bold set dressing — rendered "
        "as a stylised 3D animated set, never photographed, never live-action, "
        "props at human scale; exteriors in golden hour, interiors cold "
        "blue-grey with warm candle or lamp practicals"
    )
    assert result == expected


def test_place_prompt_block_strips_trailing_periods_before_joining():
    with_periods = prompting.place_prompt_block(
        FRUIT_DRAMA, descriptor=PLACE_DESCRIPTOR + ".", layout_notes=LAYOUT_NOTES + "."
    )
    without_periods = prompting.place_prompt_block(
        FRUIT_DRAMA, descriptor=PLACE_DESCRIPTOR, layout_notes=LAYOUT_NOTES
    )
    assert with_periods == without_periods
    assert ".." not in with_periods


def test_place_prompt_block_is_pure_and_deterministic():
    result1 = prompting.place_prompt_block(FRUIT_DRAMA, descriptor=PLACE_DESCRIPTOR, layout_notes=LAYOUT_NOTES)
    result2 = prompting.place_prompt_block(FRUIT_DRAMA, descriptor=PLACE_DESCRIPTOR, layout_notes=LAYOUT_NOTES)
    assert result1 == result2


# ------------------------------------------------------------ prop_prompt_block

PROP_DESCRIPTOR = "a small tarnished brass telephone shaped like a hollowed coconut shell"


def test_prop_prompt_block_fruit_drama_golden():
    result = prompting.prop_prompt_block(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR)
    expected = (
        "a small tarnished brass telephone shaped like a hollowed coconut "
        "shell. photorealistic 3D render of anthropomorphic fruits and "
        "vegetables with expressive human-like faces (eyes, brows, mouths) "
        "on realistic fruit heads, human-proportioned bodies in real fabric "
        "outfits, subsurface scattering on fruit skin, visible pores and "
        "fuzz, glossy highlights, high-end CGI commercial quality, "
        "Octane-style render"
    )
    assert result == expected


def test_prop_prompt_block_strips_trailing_period_before_joining():
    with_period = prompting.prop_prompt_block(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR + ".")
    without_period = prompting.prop_prompt_block(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR)
    assert with_period == without_period
    assert ".." not in with_period


def test_prop_prompt_block_is_pure_and_deterministic():
    result1 = prompting.prop_prompt_block(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR)
    result2 = prompting.prop_prompt_block(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR)
    assert result1 == result2


def test_place_and_prop_prompt_block_have_no_name_parameter():
    assert "name" not in inspect.signature(prompting.place_prompt_block).parameters
    assert "name" not in inspect.signature(prompting.prop_prompt_block).parameters


# ----------------------------------------------- prop image and time variants (phase 2, stage 5)

def test_prop_image_prompt_fruit_drama_golden():
    result = prompting.prop_image_prompt(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR)
    expected = (
        "Product shot of a small tarnished brass telephone shaped like a hollowed "
        "coconut shell, alone, centered, plain light grey background. "
        "photorealistic 3D render of anthropomorphic fruits and vegetables with "
        "expressive human-like faces (eyes, brows, mouths) on realistic fruit "
        "heads, human-proportioned bodies in real fabric outfits, subsurface "
        "scattering on fruit skin, visible pores and fuzz, glossy highlights, "
        "high-end CGI commercial quality, Octane-style render. No people, no "
        "hands, no text. ultra detailed, 8k, sharp focus"
    )
    assert result == expected
    assert prompting.prop_image_prompt(FRUIT_DRAMA, descriptor=PROP_DESCRIPTOR + ".") == expected


def test_variant_prompt_fruit_drama_golden():
    result = prompting.variant_prompt(
        FRUIT_DRAMA,
        place_descriptor="the manor's gravel courtyard with wrought iron gates",
        variant="golden_hour",
    )
    expected = (
        "Establishing wide shot of the manor's gravel courtyard with wrought "
        "iron gates, golden hour, no people, no characters. "
        # Re-pinned on purpose (DEC-305, plan 28): fruit_drama's environment_rules said "photographed like a
        # reality-TV show or a live-action comedy", against the style's animated medium; now a 3D animated villa set.
        "sets staged like a reality-TV villa — manor gates, gravel courtyards, "
        "derelict interiors with chandeliers, beach camps, villa kitchens, a "
        "pool, confessional corners, restaurants, bold set dressing — rendered "
        "as a stylised 3D animated set, never photographed, never live-action, "
        "props at human scale; exteriors in golden hour, interiors cold "
        "blue-grey with "
        # Re-pinned on purpose (DEC-220, phase 7 stage 1): environment_rules now
        # ends with a period before the rendering text.
        "warm candle or lamp practicals. photorealistic 3D render of "
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


def test_variant_prompt_is_the_master_plate_prompt_with_the_variant_as_time():
    for variant, time_variant in (("day", "day"), ("night", "night"), ("heavy_rain", "heavy rain")):
        assert prompting.variant_prompt(FRUIT_DRAMA, place_descriptor=PLACE_DESCRIPTOR, variant=variant) == (
            prompting.master_plate_prompt(FRUIT_DRAMA, place_descriptor=PLACE_DESCRIPTOR,
                                          time_variant=time_variant))


@pytest.mark.parametrize("variant", ["Night", "", "night time", "../day", "a" * 21, None, 3])
def test_variant_prompt_refuses_a_name_that_is_not_a_variant(variant):
    with pytest.raises(ValueError):
        prompting.variant_prompt(FRUIT_DRAMA, place_descriptor=PLACE_DESCRIPTOR, variant=variant)


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_prop_and_variant_prompts_hygiene_for_every_template(style_id):
    style_lock = templates.load_style(style_id)
    outputs = [
        prompting.prop_image_prompt(style_lock, descriptor=PROP_DESCRIPTOR),
        prompting.variant_prompt(style_lock, place_descriptor=PLACE_DESCRIPTOR, variant="golden_hour"),
    ]
    for output in outputs:
        assert ".." not in output
        assert "  " not in output
        assert not output.endswith(" ")
        assert style_lock["quality_tail"].strip() in output


def test_prop_and_variant_prompts_have_no_name_parameter():
    assert "name" not in inspect.signature(prompting.prop_image_prompt).parameters
    assert "name" not in inspect.signature(prompting.variant_prompt).parameters


# ------------------------------------------------------- phase 7 (v2 sheets)

LOOK_TEXT = ("lean human body with a fuzzy round kiwi head, about the same height as the sly mango, "
             "fuzzy brown kiwi skin, wearing white linen shirt, thin gold chain, colours brown and green, "
             "with left-eyebrow scar.")


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_portrait_v2_full_body_from_look(style_id):
    style_lock = templates.load_style(style_id)
    result = prompting.portrait_prompt_v2(style_lock, look_text=LOOK_TEXT, signature_items=SIGNATURE_ITEMS)
    assert "full-body" in result.lower() and "head to toe" in result.lower()
    assert "chest-up" not in result
    # The look, its period stripped before the skeleton goes on.
    assert "wearing white linen shirt, thin gold chain, colours brown and green, with left-eyebrow scar" in result
    assert result.endswith("Clean frame: no captions, lettering, logos or watermarks; one character.")
    assert "Vertical 9:16." in result and f"Plain {style_lock['sheet_background']} background" in result
    assert len(result.split()) <= 130
    assert ".," not in result and ".." not in result and "  " not in result
    # A signature item the look does not mention yet is added with "with", never "wearing".
    extra = prompting.portrait_prompt_v2(style_lock, look_text=LOOK_TEXT,
                                         signature_items=SIGNATURE_ITEMS + ["tiny brass whistle"])
    assert "with tiny brass whistle" in extra and "wearing tiny brass whistle" not in extra
    assert len(extra.split()) <= 130
    # The turnaround and the expressions are edits of the portrait: the role text comes first.
    for builder in (prompting.turnaround_prompt_v2, prompting.expressions_prompt_v2):
        sheet = builder(style_lock, look_text=LOOK_TEXT, signature_items=SIGNATURE_ITEMS)
        assert sheet.startswith("Image 1 is this character's reference: keep identity, proportions and outfit "
                                "exactly.")
        assert len(sheet.split()) <= 130 and ".," not in sheet and ".." not in sheet


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_expressions_v2_pins_every_cell_to_the_same_head(style_id):
    """A2: one cell of a character's expression sheet drifted to a human
    boy's face on the walk, and another sheet came out with five cells
    instead of six. Right after the role text, every cell is pinned to
    image 1's head; the grid count is spelled out ("exactly six cells")."""
    style_lock = templates.load_style(style_id)
    sheet = prompting.expressions_prompt_v2(style_lock, look_text=LOOK_TEXT, signature_items=SIGNATURE_ITEMS)
    assert sheet.startswith(
        "Image 1 is this character's reference: keep identity, proportions and outfit exactly. "
        "Every cell shows the same head as image 1, never a different face.")
    assert "exactly six cells" in sheet and "3 by 2 grid" in sheet
    assert len(sheet.split()) <= 130 and ".," not in sheet and ".." not in sheet


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_plate_and_prop_v2_prompts_hold_their_caps(style_id):
    style_lock = templates.load_style(style_id)
    place_text = ("a city square with a clock tower; on the left a newsstand, on the right a lamppost, at the back "
                  "the clock tower; a wide square, the tower ten people high; light: orange street lamps; "
                  "set dressing: chrome toaster (silver polished chrome, as big as a suitcase).")
    plate = prompting.plate_prompt_v2(style_lock, place_text=place_text, variant="night")
    assert "no people, no characters" in plate and "on the left a newsstand" in plate
    assert len(plate.split()) <= 150 and ".," not in plate and ".." not in plate
    prop = prompting.prop_prompt_v2(style_lock, prop_text="a chrome toaster, polished chrome, silver, as big as a "
                                                          "suitcase.")
    assert "as big as a suitcase" in prop and "alone" in prop
    assert len(prop.split()) <= 80 and ".," not in prop and ".." not in prop


@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_prop_reference_prompt_v2_carries_no_scale_or_hand_wording(style_id):
    """A1: the prop reference image prompt (fed ``shots.render_prop(...,
    for_reference=True)``'s text, which has no scale phrase) must itself
    carry no scale or hand wording -- the walk's first paid images showed a
    human hand holding the monocle because the old head phrase ("shown at
    its real scale") and "fits in one hand"-style scale phrases invite one."""
    style_lock = templates.load_style(style_id)
    # No scale wording in the input text either -- shots.render_prop(for_reference=True)'s contract.
    prop_text = "a brass monocle, polished brass, gold"
    prop = prompting.prop_prompt_v2(style_lock, prop_text=prop_text)
    lowered = prop.lower()
    assert "scale" not in lowered
    assert "real scale" not in lowered and "fits in" not in lowered and "one hand" not in lowered
    assert "the object alone on a plain surface, nothing holding it" in lowered
    # "no hands" is the one constraint phrase that may say "hand(s)" -- a style's own rendering text
    # may legitimately use the word too ("hand-painted", "handmade"), which this does not forbid.
    assert "no hands" in lowered
    assert prop.endswith(prompting._CONSTRAINTS_OBJECT)
    assert len(prop.split()) <= 80 and ".," not in prop and ".." not in prop
