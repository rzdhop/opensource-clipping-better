"""Native speech in the dashboard (plan 22, stage 4): text contracts (CI has
no node). The new-story form offers "Native speech (Veo)" with its speaking
model and each model's estimate, naming the keys it still needs; the Visual
tier card switches the profile and the model; the Video card names both
links with their prices, and each shot's clip says whether it speaks and
what its take heard ("🗣 matched 92 %")."""

from __future__ import annotations

from pathlib import Path

STORY = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story"
WIZARD = STORY / "NewStoryWizard.jsx"
CARD = STORY / "GenerationProfileCard.jsx"
ASSETS = STORY / "episode" / "storyboard" / "AssetsCards.jsx"
CLIPS = STORY / "episode" / "storyboard" / "ClipControls.jsx"


def test_the_new_story_form_offers_native_speech_with_its_model_its_estimate_and_its_missing_keys():
    src = WIZARD.read_text(encoding="utf-8")
    assert '<option value="native_speech">' in src
    assert "speech.by_model[model.id]" in src and "...(nativeSpeech ? { speech_model: speechModel } : {})" in src
    assert "speech.missing_keys" in src and "speech.stt_missing_keys" in src and "(the speech check)" in src
    # native speech is a v2 story at tier 3
    assert "setPipeline('v2')" in src and "setTier(3)" in src


def test_the_visual_tier_card_switches_the_profile_and_the_speaking_model():
    src = CARD.read_text(encoding="utf-8")
    assert '<option value="native_speech">' in src
    assert "save({ speech_model: value })" in src
    assert "budget_profile: value, tier: 3" in src


def test_the_video_card_names_both_links_and_each_clip_its_take():
    assets, clips = ASSETS.read_text(encoding="utf-8"), CLIPS.read_text(encoding="utf-8")
    assert "video.speech.speech_link" in assets and "video.speech.silent_link" in assets
    assert "video.speech.speech_price" in assets and "video.speech.retake_usd" in assets
    assert "🗣 matched${matched}" in clips and "clip.take && <TakeBadge take={clip.take} />" in clips
    assert "clip.speaks ? 'speaks' : 'silent'" in clips
