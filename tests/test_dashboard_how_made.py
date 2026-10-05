"""The wizard and the profile card say plainly how clips and images are made
(plan 25 stage 5, D-4): text contracts (CI has no node). Two segmented
controls, "How clips are made" (Auto / My own) and "How images are made"
(Auto / My own), sit under the Studio / Agent "Mode" and above the collapsed
Generation profile; they drive the same state as the Budget profile select
(My own clips = native_speech_manual, My own images = images: 'manual') and
add no payload key."""

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
    assert "Auto (API links)" in src and "My own (Flow, Higgsfield…)" in src
    assert "You paste the prompts into your provider and upload the clips; nothing is billed." in src
    assert "The app generates them on the API links; the caps and the daily cap apply." in src
    # the images control is the one that can be disabled (a v1 story has no manual images)
    assert "imagesDisabled" in src


def test_the_wizard_places_the_controls_under_mode_and_above_the_details_and_maps_them():
    src = _read(WIZARD)
    assert "<HowMadeControls" in src and "from './HowMadeControls'" in src
    mode = src.index('aria-label="Mode"')
    controls = src.index("<HowMadeControls")
    details = src.index('<summary>Generation profile</summary>')
    assert mode < controls < details
    # clips My own -> the manual profile; Auto -> the profile it had, else native_speech
    assert "handleBudgetProfile('native_speech_manual')" in src
    assert "profileBeforeManual" in src and "'native_speech'" in src
    # images My own -> images: 'manual', only on v2; the select in the details stays consistent
    assert "...(imagesManual ? { images: 'manual' } : {})" in src
    assert "const imagesManual = pipeline === 'v2' && imagesOwn" in src
    # the summary chip names the choice
    assert "Clips: {manualClips ? 'my own' : 'auto'}" in src
    assert "Images: {imagesManual ? 'my own' : 'auto'}" in src
    # the Budget profile select stays, with its five options, and the old checkbox is gone
    assert 'Budget profile</label>' in src and '<option value="native_speech_manual">' in src
    assert "My own images too" not in src


def test_the_wizard_keeps_mode_and_drops_image_preference_when_images_are_manual():
    src = _read(WIZARD)
    assert '<label className="form-label">Mode</label>' in src and ">\n                Studio\n" in src
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
