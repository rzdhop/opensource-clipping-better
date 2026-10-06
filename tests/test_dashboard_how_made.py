"""The wizard and the profile card say plainly how clips and images are made
(plan 25 stage 5, D-4): text contracts (CI has no node). Two segmented
controls, "How clips are made" (Auto / My own) and "How images are made"
(Auto / My own), sit under the Studio / Agent "Mode" and above the collapsed
Generation profile; they drive the same state as the Budget profile select
(My own clips = native_speech_manual, My own images = images: 'manual') and
add no payload key. Plan 28 stage S1: the new-story form asks "Who makes the
clips" as two plain cards instead (the same mapping: "Me" is
native_speech_manual, "The app" native_speech), its images choice under the
Advanced fold; the profile card keeps the two controls."""

from __future__ import annotations

from pathlib import Path

STORY = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story"
WIZARD = STORY / "NewStoryWizard.jsx"
CARD = STORY / "GenerationProfileCard.jsx"
CONTROLS = STORY / "HowMadeControls.jsx"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_the_two_controls_hold_the_plan_labels_options_and_explanations():
    src = _read(CONTROLS)
    assert "How clips are made" in src and "How images are made" in src
    # DEC-305 section 9 (plan 28 S2): "API links" is not a word the human uses.
    assert "The app (paid services)" in src and "My own (Flow, Higgsfield…)" in src
    assert "You paste the prompts into your provider and upload the clips; nothing is billed." in src
    assert "The app makes them with paid services, within your spending limits." in src
    # the images control is the one that can be disabled (a v1 story has no manual images)
    assert "imagesDisabled" in src


def test_the_wizard_places_the_controls_under_mode_and_above_the_details_and_maps_them():
    # Re-pinned on purpose (plan 28 stage S1, DEC-305 §9): the new-story form asks "Who makes the clips" as two
    # plain cards above the one collapsed Advanced fold, in place of the two segmented controls (still the
    # profile card's). The semantics are plan 25's: "Me" is the native_speech_manual profile, "The app" the
    # native_speech one; images your own -> images: 'manual' (under Advanced), only on v2.
    src = _read(WIZARD)
    assert "<HowMadeControls" not in src and "from './HowMadeControls'" not in src
    clips = src.index('aria-label="Who makes the clips"')
    details = src.index("<summary>Advanced</summary>")
    assert clips < details
    assert "const CLIP_MAKER_SETUPS = { me: 'native_speech_manual', app: 'native_speech' }" in src
    assert "const clipMaker = clipMakerOf(budgetProfile)" in src
    assert "...(imagesManual ? { images: 'manual' } : {})" in src
    assert "const imagesManual = pipeline === 'v2' && imagesOwn" in src
    assert 'data-choice="images-own"' in src and "onClick={() => handleImagesOwn(true)}" in src
    # the Spending plan select (the old Budget profile) stays under Advanced, with its five options
    assert 'Spending plan</label>' in src and '<option value="native_speech_manual">' in src
    assert "My own images too" not in src


def test_the_wizard_keeps_mode_and_drops_image_preference_when_images_are_manual():
    src = _read(WIZARD)
    # Re-pinned on purpose (plan 28 stage S1): the Mode choice is "How the story runs" under Advanced.
    assert '<label className="form-label">How the story runs</label>' in src
    assert ">\n                    Step by step\n" in src
    assert "...(pipeline === 'v2' && !imagesManual && imagePreference ? { image_preference: imagePreference } : {})" in src
    assert "{pipeline === 'v2' && !imagesManual && (" in src  # the Image provider select is hidden
    for key in ("budget_profile: budgetProfile", "speech_model: speechModel", "image_preference: imagePreference"):
        assert key in src


def test_the_profile_card_has_the_controls_first_and_patches_the_same_keys():
    src = _read(CARD)
    assert "<HowMadeControls" in src and "from './HowMadeControls'" in src
    assert src.index("<HowMadeControls") < src.index('htmlFor="story-profile-tier"')
    assert "save(own ? { images: 'manual' } : { images: null })" in src
    assert "handleBudgetProfile('native_speech_manual')" in src and "budget_profile: value, tier: 3" in src
    assert "profileBeforeManual" in src
    # a running step's 409 keeps the card's own handling (the select reverts, the error shows)
    assert "StepError message={error}" in src and "setError(err.message)" in src
    assert "My own images too" not in src
