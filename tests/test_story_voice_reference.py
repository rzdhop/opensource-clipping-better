"""A character's voice reference (plan 23 stage B4; DEC-281).

``clipping/aistory/voice_reference.py`` is stdlib only: ffprobe and ffmpeg run
through its *run* seam, replaced here by ``FakeTools`` (a fake ffprobe that
reports a stream list and a duration, a fake ffmpeg that writes a real WAV of
that length), so the file runs in the pytest-only CI environment (DEC-012) and
needs neither tool. Hermetic: nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import os
import re
import wave
from types import SimpleNamespace

import pytest

from clipping.aistory import schemas, store as store_mod, voice_reference, voices
from clipping.aistory.store import StoryStore

NOW = "2026-10-05T10:00:00+00:00"
LATER = "2026-10-05T11:00:00+00:00"


@pytest.fixture
def stories(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


@pytest.fixture
def story_id(stories):
    return stories.create(language="fr", seed_text="x", now=NOW)["story_id"]


def _char(char_id, *, name=None, role="lead", voice=None, voice_reference_entry=None):
    doc = {
        "$schema": schemas.CHARACTER_SCHEMA_NAME, "char_id": char_id, "name": name or char_id, "role": role,
        "archetype": "x", "one_line": "x", "descriptor": None, "signature_items": [],
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {}, "voice": voice, "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None, "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "custom", "approved_at": None, "created_at": NOW, "updated_at": NOW,
    }
    if voice_reference_entry is not None:
        doc["voice_reference"] = voice_reference_entry
    return doc


@pytest.fixture
def kiwi(stories, story_id):
    doc = _char("char_kiwi", name="Kiwi")
    stories.write_entity(story_id, "characters", doc, now=NOW, validator=schemas.character_errors)
    return "char_kiwi"


class FakeTools:
    """ffprobe and ffmpeg in one callable (the *run* seam)."""

    def __init__(self, duration=12.0, *, streams=("audio",), probe_returncode=0, ffmpeg_returncode=0,
                 missing=None):
        self.duration = duration
        self.streams = streams
        self.probe_returncode = probe_returncode
        self.ffmpeg_returncode = ffmpeg_returncode
        self.missing = missing
        self.argvs = []

    def __call__(self, argv, **kwargs):
        self.argvs.append(list(argv))
        if self.missing == argv[0]:
            raise FileNotFoundError(argv[0])
        if argv[0] == "ffprobe":
            if self.probe_returncode:
                return SimpleNamespace(returncode=self.probe_returncode, stdout="", stderr="Invalid data")
            body = {"streams": [{"codec_type": kind} for kind in self.streams],
                    "format": {"duration": str(self.duration)}}
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        assert argv[0] == "ffmpeg", argv
        if not self.ffmpeg_returncode:
            with wave.open(argv[-1], "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(24000)
                out.writeframes(b"\x00\x01" * int(self.duration * 24000))
        return SimpleNamespace(returncode=self.ffmpeg_returncode, stdout="", stderr="")


def _upload(stories, story_id, char_id="char_kiwi", *, body=b"RIFFfake-recording", consent=True, tools=None,
            **kwargs):
    return voice_reference.accept_voice_reference(
        stories, story_id, char_id, io.BytesIO(body), consent=consent, now=LATER,
        run=tools or FakeTools(), **kwargs)


def _folder(stories, story_id, char_id="char_kiwi"):
    return stories.entity_dir(story_id, "characters", char_id)


def _files(stories, story_id, char_id="char_kiwi"):
    return sorted(os.listdir(_folder(stories, story_id, char_id)))


# --------------------------------------------------------------- accepting

def test_a_recording_is_accepted_re_encoded_and_recorded(stories, story_id, kiwi):
    entry = _upload(stories, story_id, tools=FakeTools(12.0))

    path = stories.media_path(story_id, "characters", kiwi, "voice_reference.wav")
    with wave.open(path, "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
    import hashlib

    assert entry == {"name": "voice_reference.wav", "sha256": hashlib.sha256(open(path, "rb").read()).hexdigest(),
                     "duration_s": 12.0, "uploaded_at": LATER, "consent": True}
    doc = stories.read_entity(story_id, "characters", kiwi)
    assert doc["voice_reference"] == entry
    assert schemas.character_errors(doc) == []
    # Only the re-encoded file is left: the received body and the temp files are gone.
    assert _files(stories, story_id) == ["character.json", "voice_reference.wav"]


def test_a_new_recording_replaces_the_old_one(stories, story_id, kiwi):
    first = _upload(stories, story_id, tools=FakeTools(8.0))
    second = _upload(stories, story_id, tools=FakeTools(9.0))

    assert second["duration_s"] == 9.0 and second["sha256"] != first["sha256"]
    assert stories.read_entity(story_id, "characters", kiwi)["voice_reference"] == second
    assert _files(stories, story_id) == ["character.json", "voice_reference.wav"]


def test_a_new_recording_of_a_pinned_reference_voice_drops_the_stale_sample_and_the_approval(stories, story_id):
    pinned = {"provider": "chatterbox", "voice_id": "reference", "rate": None, "pitch": None,
              "direction": "d", "sample_line": "Salut !"}
    doc = _char("char_kiwi", voice=pinned)
    doc["approved_at"] = NOW
    stories.write_entity(story_id, "characters", doc, now=NOW, validator=schemas.character_errors)
    sample = os.path.join(_folder(stories, story_id), "voice_sample.wav")
    with open(sample, "wb") as fh:
        fh.write(b"old")

    _upload(stories, story_id)

    after = stories.read_entity(story_id, "characters", "char_kiwi")
    assert after["approved_at"] is None and after["voice"] == pinned
    assert not os.path.exists(sample)


def test_the_re_encode_command_is_mono_24k_16_bit_with_no_metadata(stories, story_id, kiwi):
    tools = FakeTools()
    _upload(stories, story_id, tools=tools)

    probe, ffmpeg = tools.argvs
    assert probe[0] == "ffprobe" and "format=duration" in " ".join(probe)
    assert ffmpeg[0] == "ffmpeg"
    joined = " ".join(ffmpeg)
    for part in ("-map 0:a:0", "-map_metadata -1", "-map_chapters -1", "-ac 1", "-ar 24000", "-c:a pcm_s16le",
                 "-f wav", "+bitexact", "-t 30"):
        assert part in joined, part
    # The source is the hidden temp file in the character's folder, never a name the user sent.
    assert os.path.basename(ffmpeg[ffmpeg.index("-i") + 1]).startswith(".upload-")
    assert voice_reference.reencode_argv("a.m4a", "b.wav")[-1] == "b.wav"


# ----------------------------------------------------------------- consent

class NeverRead:
    def read(self, size=-1):
        raise AssertionError("the body was read before consent was checked")


@pytest.mark.parametrize("consent", [False, None, "true", 1, 0])
def test_without_consent_nothing_is_read_and_nothing_is_kept(stories, story_id, kiwi, consent):
    before = stories.read_entity(story_id, "characters", kiwi)

    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        voice_reference.accept_voice_reference(stories, story_id, kiwi, NeverRead(), consent=consent, now=LATER,
                                               run=FakeTools())

    assert excinfo.value.code == "no_consent" and excinfo.value.http_status == 400
    assert "permission" in str(excinfo.value) and str(excinfo.value).count(".") == 1
    assert stories.read_entity(story_id, "characters", kiwi) == before
    assert _files(stories, story_id) == ["character.json"]


def test_the_stored_consent_is_always_true(stories, story_id, kiwi):
    entry = _upload(stories, story_id)
    assert entry["consent"] is True
    refused = dict(entry, consent=False)
    assert schemas.validate(refused, schemas.VOICE_REFERENCE_SCHEMA) != []
    assert schemas.validate(entry, schemas.VOICE_REFERENCE_SCHEMA) == []
    # Optional: a character with none is as valid as it always was.
    assert schemas.character_errors(_char("char_x")) == []


# ---------------------------------------------------------------- the window

@pytest.mark.parametrize("duration,accepted", [(4.9, False), (5.0, True), (17.5, True), (30.0, True),
                                               (30.1, False), (0.0, False)])
def test_the_recording_lasts_five_to_thirty_seconds(stories, story_id, kiwi, duration, accepted):
    before = stories.read_entity(story_id, "characters", kiwi)
    tools = FakeTools(duration)
    if accepted:
        assert _upload(stories, story_id, tools=tools)["duration_s"] == duration
        return
    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        _upload(stories, story_id, tools=tools)
    assert excinfo.value.code == "bad_duration" and excinfo.value.http_status == 400
    assert "5 to 30 seconds" in str(excinfo.value)
    assert stories.read_entity(story_id, "characters", kiwi) == before
    assert _files(stories, story_id) == ["character.json"]
    assert all(argv[0] == "ffprobe" for argv in tools.argvs)  # refused before any re-encode


def test_a_file_with_no_audio_track_or_that_ffprobe_cannot_read_is_415(stories, story_id, kiwi):
    for tools in (FakeTools(streams=("video",)), FakeTools(probe_returncode=1)):
        with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
            _upload(stories, story_id, tools=tools)
        assert excinfo.value.code == "not_audio" and excinfo.value.http_status == 415
    assert _files(stories, story_id) == ["character.json"]


def test_a_failed_re_encode_is_415_and_leaves_nothing(stories, story_id, kiwi):
    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        _upload(stories, story_id, tools=FakeTools(ffmpeg_returncode=1))
    assert excinfo.value.http_status == 415
    assert _files(stories, story_id) == ["character.json"]


def test_a_server_without_ffprobe_says_so_503(stories, story_id, kiwi):
    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        _upload(stories, story_id, tools=FakeTools(missing="ffprobe"))
    assert excinfo.value.http_status == 503 and "ffprobe is not installed" in str(excinfo.value)


def test_an_empty_body_is_not_audio(stories, story_id, kiwi):
    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        _upload(stories, story_id, body=b"")
    assert excinfo.value.code == "not_audio"
    assert _files(stories, story_id) == ["character.json"]


# ---------------------------------------------------------------------- cap

class Counting:
    def __init__(self, size):
        self.left, self.requested = size, 0

    def read(self, size=-1):
        take = min(size, self.left)
        self.requested += take
        self.left -= take
        return b"x" * take


def test_the_body_is_cut_at_the_cap_and_never_read_a_byte_past_it(stories, story_id, kiwi):
    assert voice_reference.MAX_UPLOAD_BYTES == 10 * 1024 * 1024
    source = Counting(5 * 1024 * 1024)

    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        voice_reference.accept_voice_reference(stories, story_id, kiwi, source, consent=True, now=LATER,
                                               run=FakeTools(), max_bytes=2 * 1024 * 1024)

    assert excinfo.value.code == "too_large" and excinfo.value.http_status == 413
    assert "2 MB" in str(excinfo.value)
    assert source.requested == 2 * 1024 * 1024 + 1
    assert _files(stories, story_id) == ["character.json"]


def test_an_unknown_character_is_a_key_error_before_the_body_is_read(stories, story_id):
    with pytest.raises(KeyError):
        voice_reference.accept_voice_reference(stories, story_id, "char_nobody", NeverRead(), consent=True,
                                               now=LATER, run=FakeTools())


# ------------------------------------------------------------ the name

def test_only_the_one_name_can_be_played_back():
    pattern = re.compile(schemas.VOICE_REFERENCE_NAME_PATTERN)
    assert pattern.fullmatch("voice_reference.wav")
    for bad in ("voice_reference.mp3", "voice_reference.wav.bak", "../voice_reference.wav", "voice_reference",
                "my_voice_reference.wav", "voice_reference.WAV", "voice_reference.wav\n"):
        assert pattern.fullmatch(bad) is None, bad
    assert store_mod.MEDIA_NAME_PATTERNS["characters"]["voice_reference"].pattern == \
        schemas.VOICE_REFERENCE_NAME_PATTERN
    assert store_mod.MEDIA_DIRS["voice_reference"] == ()


def test_the_media_route_store_serves_it_only_when_it_is_a_real_file(stories, story_id, kiwi):
    with pytest.raises(KeyError):
        stories.media_path(story_id, "characters", kiwi, "voice_reference.wav")
    _upload(stories, story_id)
    assert stories.media_path(story_id, "characters", kiwi, "voice_reference.wav").endswith("voice_reference.wav")
    with pytest.raises(KeyError):
        stories.media_path(story_id, "places", "place_x", "voice_reference.wav")
    assert voice_reference.reference_path(stories, story_id, kiwi) == stories.media_path(
        story_id, "characters", kiwi, "voice_reference.wav")
    with pytest.raises(KeyError):
        voice_reference.reference_path(stories, story_id, "char_nobody")


# ------------------------------------------------------------------ delete

def _pin_reference(stories, story_id, char_id="char_kiwi"):
    doc = stories.read_entity(story_id, "characters", char_id)
    doc["voice"] = {"provider": "chatterbox", "voice_id": "reference", "rate": None, "pitch": None,
                    "direction": "d", "sample_line": "Salut !"}
    stories.write_entity(story_id, "characters", doc, now=NOW)


def test_the_reference_cannot_be_removed_while_the_voice_is_pinned_to_it(stories, story_id, kiwi):
    entry = _upload(stories, story_id)
    _pin_reference(stories, story_id)

    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        voice_reference.delete_voice_reference(stories, story_id, kiwi, now=LATER)

    assert excinfo.value.code == "pinned" and excinfo.value.http_status == 409
    assert "pick another voice first" in str(excinfo.value)
    assert stories.read_entity(story_id, "characters", kiwi)["voice_reference"] == entry
    assert "voice_reference.wav" in _files(stories, story_id)


def test_a_reference_is_removed_with_its_entry_once_the_voice_is_another(stories, story_id, kiwi):
    _upload(stories, story_id)

    result = voice_reference.delete_voice_reference(stories, story_id, kiwi, now=LATER)

    assert result == {"name": "voice_reference.wav", "entry_removed": True, "file_removed": True}
    assert "voice_reference" not in stories.read_entity(story_id, "characters", kiwi)
    assert _files(stories, story_id) == ["character.json"]
    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        voice_reference.delete_voice_reference(stories, story_id, kiwi, now=LATER)
    assert excinfo.value.http_status == 404


def test_a_symlink_in_the_files_place_is_refused_and_kept(stories, story_id, kiwi, tmp_path):
    target = tmp_path / "elsewhere.wav"
    target.write_bytes(b"not ours")
    os.symlink(target, os.path.join(_folder(stories, story_id), "voice_reference.wav"))

    with pytest.raises(voice_reference.VoiceReferenceError) as excinfo:
        voice_reference.delete_voice_reference(stories, story_id, kiwi, now=LATER)

    assert excinfo.value.code == "storage" and target.read_bytes() == b"not ours"


# --------------------------------------------------- pinning the reference

def test_the_pin_shape_is_recognised():
    assert voice_reference.is_reference_voice({"provider": "chatterbox", "voice_id": "reference"})
    assert not voice_reference.is_reference_voice({"provider": "chatterbox", "voice_id": "zero-shot"})
    assert not voice_reference.is_reference_voice({"provider": "edge", "voice_id": "reference"})
    assert not voice_reference.is_reference_voice(None)


def test_the_reference_voice_needs_its_recording_and_chatterbox(stories, story_id, kiwi, monkeypatch):
    from clipping.providers import tts

    character = stories.read_entity(story_id, "characters", kiwi)
    with pytest.raises(voices.VoiceError, match="no voice reference"):
        voices.reference_voice(stories, story_id, character)

    _upload(stories, story_id)
    character = stories.read_entity(story_id, "characters", kiwi)
    monkeypatch.setattr(tts, "_installed", lambda name: False)
    with pytest.raises(voices.VoiceError) as excinfo:
        voices.reference_voice(stories, story_id, character)
    assert "chatterbox is not installed" in str(excinfo.value) and "local-tts" in str(excinfo.value)
    assert voices.reference_engine()[0] is False

    monkeypatch.setattr(tts, "_installed", lambda name: True)
    voice = voices.reference_voice(stories, story_id, character)
    assert (voice.provider, voice.voice_id) == ("chatterbox", "reference")
    assert voice.link.provider == "local" and voice.link.model == "chatterbox" and voice.paid is False
    assert voices.pin(character, voice)["voice_id"] == "reference"


def test_the_pre_job_check_lets_each_character_pin_its_own_reference(stories, story_id, monkeypatch):
    from clipping.aistory import workflow
    from clipping.providers import tts

    monkeypatch.setattr(tts, "_installed", lambda name: True)
    for char_id in ("char_kiwi", "char_mango"):
        stories.write_entity(story_id, "characters", _char(char_id), now=NOW, validator=schemas.character_errors)
        _upload(stories, story_id, char_id)
    _pin_reference(stories, story_id, "char_kiwi")
    story = stories.get(story_id)
    pin = {"provider": "chatterbox", "voice_id": "reference"}

    # Another lead already speaks with *its* recording: that is no conflict.
    assert workflow.check_voice_choice(stories, story, "char_mango", dict(pin), env={}) == pin
    assert workflow.character_voices(stories, story, "char_mango", env={})["taken"] == []

    stories.write_entity(story_id, "characters", _char("char_fig"), now=NOW, validator=schemas.character_errors)
    with pytest.raises(workflow.WorkflowError, match="no voice reference"):
        workflow.check_voice_choice(stories, story, "char_fig", dict(pin), env={})

    monkeypatch.setattr(tts, "_installed", lambda name: False)
    with pytest.raises(workflow.WorkflowError, match="chatterbox"):
        workflow.check_voice_choice(stories, story, "char_mango", dict(pin), env={})


# ----------------------------------------- the line measurement hands the file over

def test_a_measured_line_of_a_reference_voice_is_spoken_with_the_characters_file(stories, story_id, monkeypatch):
    """``LineMeasurement.measure_line`` gives ``synthesize_line`` the real path of the speaker's
    ``voice_reference.wav`` -- and, when the file is gone, ``None`` (a sentence, never the default voice)."""
    from clipping.aistory.steps import voice_lines

    pinned = {"provider": "chatterbox", "voice_id": "reference", "rate": None, "pitch": None,
              "direction": "d", "sample_line": "Salut !"}
    doc = _char("char_kiwi", name="Kiwi", voice=pinned)
    stories.write_entity(story_id, "characters", doc, now=NOW, validator=schemas.character_errors)
    edge = _char("char_mango", name="Mango", voice={**pinned, "provider": "edge", "voice_id": "fr-FR-HenriNeural"})
    stories.write_entity(story_id, "characters", edge, now=NOW, validator=schemas.character_errors)
    seen = []

    def fake_synthesize_line(gates, **kwargs):
        seen.append(kwargs)
        raise voices.VoiceError("stop here")

    monkeypatch.setattr(voices, "synthesize_line", fake_synthesize_line)
    run = voice_lines.LineMeasurement()
    run.ec = SimpleNamespace(store=stories, story_id=story_id, ep=1, story={}, language="fr",
                             names={"char_kiwi": "Kiwi", "char_mango": "Mango"},
                             entities={"characters": {"char_kiwi": doc, "char_mango": edge}})
    run.ctx = SimpleNamespace(on_log=lambda line: None, cancel=SimpleNamespace(check=lambda: None))
    run.tools = SimpleNamespace(adapters=None, transport=None)
    run.voice_failed = []

    def measure(speaker):
        run.measure_line(None, {"line_id": "l01", "speaker": speaker, "text": "Bonjour"})
        return seen[-1]

    assert measure("char_kiwi")["reference"] is None  # no file yet
    path = _upload(stories, story_id) and stories.media_path(story_id, "characters", "char_kiwi",
                                                             "voice_reference.wav")
    assert measure("char_kiwi")["reference"] == path
    assert "reference" not in measure("char_mango")  # another voice: today's call
