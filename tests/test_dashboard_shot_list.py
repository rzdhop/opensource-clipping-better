"""The manual link in the dashboard (plan 22, stage 5): text contracts over the
JSX (CI has no node) and the payload contract of the fields the pages read.

The episode studio's "Shot list" pane reads the shot brief (``GET
.../episodes/{ep}/brief?platform=``): every field it reads off a brief or a
shot entry is one the server writes; its platform choices are the presets;
its state badges are the brief's states; each row uploads to the entry's
own ``upload_slot``. The Generate button reads "Waiting for N clips" while a
job of the episode is paused ``awaiting_uploads``; the Agent run card shows
the pause and the brief; the wizard and the profile card offer the manual
profile and its images switch; the cast, places and props tiles carry an
upload slot on the paths the API serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import test_story_assets_step as tas
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"
PANE = STORY / "episode" / "ShotListPane.jsx"
SLOT = STORY / "ManualUploadSlot.jsx"
STUDIO = STORY / "EpisodeStudio.jsx"
AGENT = STORY / "AgentRunCard.jsx"
WIZARD = STORY / "NewStoryWizard.jsx"
CARD = STORY / "GenerationProfileCard.jsx"
API = SRC / "api.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_the_shot_list_reads_only_what_the_brief_writes(store):
    """Fail-first: the pane did not exist. Every ``entry.<field>`` and
    ``brief.<field>`` the pane reads is a key the server's brief carries."""
    from clipping.aistory.steps import brief as brief_mod

    story_id = nsp.planned_story(store, profile="native_speech_manual")
    brief = brief_mod.shot_brief(tas._ec(store, story_id), platform="flow")
    src = _read(PANE)
    entry_keys = set().union(*(set(entry) for entry in brief["shots"]))
    read = set(re.findall(r"\bentry\.([a-z_]+)", src))
    assert read and read <= entry_keys, read - entry_keys
    top = set(re.findall(r"\bbrief\.([a-z_]+)", src))
    assert top <= set(brief) | {"zip_url", "files"}, top - set(brief)
    take_keys = set(re.findall(r"\bentry\.take\.([a-z_]+)", src))
    assert take_keys <= {"state", "matched", "heard", "start_s", "end_s", "reason"}


def test_the_shot_list_s_choices_are_the_presets_and_the_states():
    from clipping.aistory import platforms
    from clipping.aistory.steps import brief as brief_mod

    src = _read(PANE)
    assert set(re.findall(r"\{ id: '([a-z]+)', label:", src)) == set(platforms.PLATFORMS)
    states = re.search(r"export const SHOT_STATES = \{(.*?)\n\}", src, re.DOTALL).group(1)
    assert set(re.findall(r"^\s*([a-z_]+):", states, re.MULTILINE)) == set(brief_mod.STATES)
    assert "slot={entry.upload_slot}" in src and "navigator.clipboard.writeText(entry.prompt)" in src
    assert "downloadShotBriefZip(storyId, ep, platform)" in src and "Download brief (zip)" in src
    assert "clip${counts.total === 1 ? '' : 's'} uploaded" in src  # "7 of 12 clips uploaded"
    api = _read(API)
    for name in ("fetchShotBrief", "downloadShotBriefZip", "uploadToSlot", "fetchImageBrief"):
        assert f"export async function {name}(" in api or f"export function {name}(" in api, name
    assert "/brief?platform=" in api and "/brief.zip?platform=" in api and "/image-brief" in api


def test_the_studio_routes_the_pane_and_waits_while_clips_are_missing():
    src = _read(STUDIO)
    assert "'shots'" in src and "<ShotListPane" in src and "id=\"episode-pane-shots\"" in src
    assert "job.status === 'awaiting_uploads'" in src
    assert "waiting ? waitingLabel(waiting) : 'Generate episode'" in src
    assert "return `Waiting for ${parts.join(' and ')}`" in src
    assert "budget_profile === MANUAL_PROFILE" in src and "const MANUAL_PROFILE = 'native_speech_manual'" in src


def test_the_agent_run_card_shows_the_pause_and_the_brief():
    src = _read(AGENT)
    assert "job.status === 'awaiting_uploads'" in src and "uploads.message" in src
    assert "downloadShotBriefZip(storyId, EPISODE)" in src and "#shots" in src


def test_the_wizard_and_the_card_offer_your_own_clips_and_images():
    wizard, card = _read(WIZARD), _read(CARD)
    for src in (wizard, card):
        assert '<option value="native_speech_manual">' in src
        assert "value === 'native_speech' || value === 'native_speech_manual'" in src
    # plan 25 stage 5: the checkbox became the "How images are made" control, independent of the clips' mode
    assert "...(imagesManual ? { images: 'manual' } : {})" in wizard
    assert "offer.native_speech_manual" in wizard
    assert "save(own ? { images: 'manual' } : { images: null })" in card


def test_the_tiles_upload_to_the_paths_the_api_serves():
    from clipping.aistory.steps import brief as brief_mod

    src = _read(SLOT)
    for kind, js in (("characters", "/api/stories/${storyId}/cast/${eid}/sheet?which="),
                     ("places", "/api/stories/${storyId}/places/${eid}/plate?variant="),
                     ("props", "/api/stories/${storyId}/props/${eid}/image")):
        assert js in src, js
        python = brief_mod.entity_image_slot("S", kind, "E", "x")
        assert python.startswith(js.replace("${storyId}", "S").replace("${eid}", "E"))
    for step in ("CastStep.jsx", "PlacesStep.jsx"):
        text = _read(STORY / "steps" / step)
        assert "imagesManual(story) && (" in text and "<EntityImageSlots" in text


def test_a_variant_tile_uploads_to_the_variant_slot_the_api_serves():
    """Plan 23 D5 follow-up: on a manual-images story each variant row has
    an upload tile per sheet slot (the base tiles' component), on the sheet
    route with ``&variant=<vid>`` -- the image brief's own ``upload_slot``."""
    from clipping.aistory.steps import brief as brief_mod

    slot = _read(SLOT)
    assert "export function entityImageSlot(storyId, kind, eid, slot, variantId = null)" in slot
    assert "`&variant=${encodeURIComponent(variantId)}`" in slot
    assert brief_mod.entity_image_slot("S", "characters", "E", "portrait", variant_id="ghost") == \
        "/api/stories/S/cast/E/sheet?which=portrait&variant=ghost"
    cast = _read(STORY / "steps" / "CastStep.jsx")
    assert "entityImageSlot(storyId, 'characters', character.char_id, slot, variant.variant_id)" in cast
    assert "manualImages={imagesManual(story)}" in cast
    row = re.search(r"function VariantRow\(\{(.*?)\n\}\n", cast, re.DOTALL).group(1)
    assert "manualImages && (" in row and "<ManualUploadSlot" in row
