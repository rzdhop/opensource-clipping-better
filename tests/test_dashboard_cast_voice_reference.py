"""Plan 23 stage B4 (DEC-281): the cast step's voice-recording slot.

Text contracts over the sources (DEC-012: stdlib + pytest only, no JS runner),
in the style of ``tests/test_dashboard_phase7_editing.py``: the slot lives in
its own component imported by ``CastStep.jsx``; the upload is behind a consent
checkbox and sends it to the route that refuses without it; the player reads
the one file name the media route serves; "Use as voice (chatterbox)" pins
``chatterbox/reference`` through the existing voice regenerate and is disabled
with the engine probe's own reason; the request paths match the routes.
"""

from __future__ import annotations

import pathlib
import re

from clipping.aistory import schemas, voice_reference

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
API_JS = (SRC / "api.js").read_text(encoding="utf-8")
CAST = (SRC / "pages" / "story" / "steps" / "CastStep.jsx").read_text(encoding="utf-8")
SLOT_PATH = SRC / "pages" / "story" / "steps" / "VoiceReferenceSlot.jsx"
STORIES_ROUTES = (ROOT / "web" / "api" / "routes" / "stories.py").read_text(encoding="utf-8")


def _slot() -> str:
    return SLOT_PATH.read_text(encoding="utf-8")


def test_the_slot_is_its_own_component_used_once_by_the_character_card():
    assert SLOT_PATH.is_file()
    assert "import VoiceReferenceSlot from './VoiceReferenceSlot'" in CAST
    assert len(re.findall(r"<VoiceReferenceSlot\b", CAST)) == 1
    assert "export default function VoiceReferenceSlot" in _slot()
    # Between the voice picker and the design references, which it must not replace.
    assert CAST.index("<VoiceSection") < CAST.index("<VoiceReferenceSlot") < CAST.index("<UploadsSection")


def test_the_upload_is_behind_the_consent_checkbox_and_says_what_it_means():
    slot = _slot()
    assert 'type="checkbox"' in slot
    assert "This is my voice, or I have the speaker's permission to use it." in slot
    # The file input is disabled until the box is ticked, and the consent travels with the file.
    assert re.search(r"type=\"file\"[\s\S]*?disabled=\{[^}]*!consent", slot)
    assert "uploadVoiceReference(storyId, character.char_id, file, consent)" in slot
    assert "5 to 30 seconds" in slot and "nothing is sent to a provider" in slot


def test_the_player_reads_the_one_name_the_media_route_serves():
    slot = _slot()
    assert f"'{voice_reference.MEDIA_NAME}'" in slot
    assert "fetchStoryMediaUrl(storyId, 'characters', character.char_id, MEDIA_NAME)" in slot
    assert "<audio controls" in slot
    assert "URL.revokeObjectURL" in slot
    assert re.fullmatch(schemas.VOICE_REFERENCE_NAME_PATTERN, voice_reference.MEDIA_NAME)


def test_use_as_voice_pins_the_reference_and_is_disabled_with_the_engines_reason():
    slot = _slot()
    assert "const REFERENCE_VOICE = { provider: 'chatterbox', voice_id: 'reference' }" in slot
    assert (voice_reference.REFERENCE_PROVIDER, voice_reference.REFERENCE_VOICE_ID) == ("chatterbox", "reference")
    assert "target: `character:${character.char_id}:voice`" in slot
    assert "Use as voice (chatterbox)" in slot
    assert re.search(r"disabled=\{[^}]*!engineReady[^}]*\}", slot)
    # The probe's sentence is both the tooltip and the visible hint.
    assert "title={engineReason" in slot and "{engineReason}</span>" in slot
    assert "engine.reason" in slot


def test_the_remove_button_is_disabled_while_the_voice_is_the_recording():
    slot = _slot()
    assert re.search(r"onClick=\{remove\}\s+disabled=\{[^}]*\bpinned\b", slot)
    assert "pick another voice first" in slot.lower()


def test_every_call_goes_to_a_route_that_exists():
    for name in ("fetchVoiceReference", "uploadVoiceReference", "deleteVoiceReference"):
        assert f"export async function {name}(" in API_JS, name
    assert "/voice-reference`" in API_JS or "/voice-reference?consent" in API_JS
    assert "?consent=${consent ? 'true' : 'false'}" in API_JS
    assert "formData.append('file', file)" in API_JS
    for route in ('@router.get("/{story_id}/characters/{char_id}/voice-reference")',
                  '@router.post("/{story_id}/characters/{char_id}/voice-reference", status_code=201)',
                  '@router.delete("/{story_id}/characters/{char_id}/voice-reference")'):
        assert route in STORIES_ROUTES, route
    # Every import the slot makes is an export of api.js.
    imported = re.search(r"import \{([^}]*)\} from '../../../api'", _slot()).group(1)
    for name in (n.strip() for n in imported.split(",") if n.strip()):
        assert re.search(rf"export (async )?function {name}\b", API_JS), name
