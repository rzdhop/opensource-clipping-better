"""Plan 28 stage B1 (DEC-305): a new native-speech story has no generated voice.

The human: "no need for voices generation, do not use edge" and, on the
narrator, "remove". A story created on a native-speech profile from now on
carries ``generation_profile.voices: "none"`` and the narrator off: the cast
step pins no voice (character or narrator) and synthesises no sample, a
character is complete without them, the estimate books no speech, and the
Cast step shows no voice UI. Every story made before keeps "tts" (the field
absent) and its voices. A story whose lines are voiced by TTS cannot drop
them. Essential tests only (DEC-234).

Stdlib + pytest (DEC-012), offline: the cast run reuses
``tests/test_story_look.py``'s fakes (no request leaves the process).
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest

import test_story_look as look
from clipping.aistory import defaults, media_policy, workflow
from clipping.aistory.store import StoryStore

NOW = "2026-10-05T21:00:00+00:00"
LATER = "2026-10-05T21:05:00+00:00"
ROOT = pathlib.Path(__file__).resolve().parents[1]
CAST_STEP = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "steps" / "CastStep.jsx"

hermetic = look.hermetic


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


def _native_story(store):
    """``test_story_look._story``'s locked Fruit Drama story, on the native-speech profile a new story gets
    with the quality keys and the ``voices: none`` ``store.create`` stamps on it -- its images the human's
    uploads, one portrait sheet a character (two_view), so the cast step calls the writer alone."""
    story_id = look._story(store, v2=True)
    profile = dict(defaults.manual_speech_generation_profile(), images="manual", sheet_mode="two_view",
                   voices=defaults.VOICES_NONE)
    store.update(story_id, lambda doc: doc.update(generation_profile=dict(doc["generation_profile"], **profile)),
                 now=NOW)
    return story_id


class NoTTS:
    """A TTS adapter no call may reach."""

    def estimate(self, link, request):
        raise AssertionError("a voice was estimated")

    def probe(self, link, **_):
        raise AssertionError("a voice was probed")

    def generate(self, link, request, **_):
        raise AssertionError("a voice was synthesised")


def _run_cast(store, story_id, llm):
    from clipping.aistory.steps import cast
    from clipping.cancel import CancelToken

    ctx = look.steps.StepContext(job_id="job000000001", story_id=story_id, step="cast", ep=None,
                                 params={"selected": ["Kiwilo", "Mangella"]}, cancel=CancelToken(),
                                 settings_env=dict(look.SETTINGS), outputs_dir=store.outputs_dir,
                                 on_log=lambda line: None)
    adapters = {("tts", "edge"): NoTTS(), ("tts", "gemini"): NoTTS()}
    return cast.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=lambda seconds: None, adapters=adapters)


def test_a_new_native_story_has_no_voices_and_no_narrator(store):
    story = store.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(), now=NOW)
    assert story["generation_profile"]["voices"] == defaults.VOICES_NONE
    assert story["narrator"] == {"enabled": False, "voice": None}
    assert media_policy.no_voices(story)
    # Asked for, a native story keeps its TTS voices; a story made before (no field) is "tts".
    kept = store.create(language="fr", now=NOW,
                        generation_profile=dict(defaults.manual_speech_generation_profile(), voices="tts"))
    assert kept["generation_profile"]["voices"] == defaults.VOICES_TTS and not media_policy.no_voices(kept)
    legacy = store.create(language="fr", now=NOW)
    assert "voices" not in legacy["generation_profile"] and not media_policy.no_voices(legacy)
    # The narrator cannot be turned on without voices.
    with pytest.raises(workflow.WorkflowError, match="cannot have a narrator"):
        workflow.patch_story(store, story["story_id"], {"narrator": {"enabled": True}}, now=LATER)


def test_voices_none_is_refused_on_a_story_whose_lines_are_voiced(store):
    with pytest.raises(ValueError, match="only on a native-speech story"):
        store.create(language="fr", generation_profile=dict(defaults.quality_generation_profile(), voices="none"),
                     now=NOW)
    story_id = store.create(language="fr", generation_profile=defaults.quality_generation_profile(),
                            now=NOW)["story_id"]
    with pytest.raises(workflow.WorkflowError, match="only on a native-speech story"):
        workflow.patch_story(store, story_id, {"generation_profile": {"voices": "none"}}, now=LATER)


def test_the_cast_of_a_no_voice_story_pins_nothing_books_no_speech_and_is_complete(store, tmp_path):
    from clipping.aistory.steps import entities

    story_id = _native_story(store)
    story = store.get(story_id)
    assert media_policy.no_voices(story) and story["narrator"]["enabled"] is False
    units = workflow.cast_units(store, story, selected=["Kiwilo", "Mangella"])
    assert units["tts_chars"] == 0 and units["llm_calls"] == 6

    events = look.Events()
    llm = look.FakeLLM(events, K1=[look._k1("a fuzzy kiwi", ["gold chain", "linen shirt"]),
                                   look._k1("a sly mango", ["red dress", "crown clip"])],
                       D1=[look._d1("Né sur la plage."), look._d1("Reine du parloir.")],
                       D2=[look._d2(175), look._d2(160, "mango")])  # DEC-305 F3: one species each
    # The sheets are the human's uploads, so the run stops on them -- and on nothing else: no voice, no sample.
    with pytest.raises(look.steps.StepFailed) as stopped:
        _run_cast(store, story_id, llm)
    assert "portrait failed" in str(stopped.value) and "voice" not in str(stopped.value)

    assert events == ["K1", "D1", "D2", "K1", "D1", "D2"]
    assert store.get(story_id)["narrator"] == {"enabled": False, "voice": None}
    assert workflow.progress(store, store.get(story_id), env={})["pick_voice"] == []
    # Written, no voice: only the uploaded sheet is missing -- then nothing, and the character is approved.
    src = tmp_path / "portrait.png"
    src.write_bytes(look.PNG)
    for doc in store.list_entities(story_id, "characters"):
        assert doc["voice"] is None and not entities.has_sample(store, story_id, doc["char_id"])
        assert workflow.character_missing(store, story_id, doc) == ["portrait"]
        store.write_media(story_id, "characters", doc["char_id"], "portrait.png", str(src))
        doc["refs"]["portrait"] = {"name": "portrait.png", "consistency": "base", "source": "manual/upload",
                                   "seed": 1, "created_at": NOW}
        store.write_entity(story_id, "characters", doc, now=NOW)
        assert workflow.character_missing(store, story_id, doc) == []
        workflow.approve_entity(store, story_id, "characters", doc["char_id"], now=LATER)
    assert workflow.cast_units(store, store.get(story_id)) == {"llm_calls": 0, "images": 0, "edit_images": 0,
                                                                "tts_chars": 0}


def test_a_no_voice_story_speaks_no_line_by_tts_and_labels_each_take_by_its_clip(store):
    """The assets step measures no line (no TTS), a native take is its character's line without any pinned
    voice (``clip/<char_id>``, what ``is_measured`` compares), and the fast track's estimate before a script
    waits for no voice. A story with TTS voices keeps its pinned voice's label."""
    from clipping.aistory.steps import fast_track, native_take, voice_lines

    story = store.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(), now=NOW)
    line = {"line_id": "l01", "speaker": "char_kiwilo", "text": "Je gagne toujours.",
            "timing": {"source": "estimate", "duration_s": 1.5, "text_hash": "x", "voice": None, "audio": None}}
    ec = SimpleNamespace(story=story, store=store, story_id=story["story_id"], ep=1, narrator=False,
                         cast=[{"char_id": "char_kiwilo"}], entities={"characters": {"char_kiwilo": {"voice": None}}},
                         names={"char_kiwilo": "Kiwilo"})
    assert voice_lines.lines_to_measure(ec, {"scenes": [{"lines": [line]}]}) == []
    assert native_take.pinned_voice(ec, "char_kiwilo") == "clip/char_kiwilo"
    predicted = fast_track._predicted_voices(ec, 300, env={}, adapters={})
    assert predicted["unvoiced"] == [] and predicted["voices"] == [] and predicted["ready"] is True

    voiced = dict(story, generation_profile=dict(story["generation_profile"], voices=defaults.VOICES_TTS))
    pinned = {"provider": "gemini", "voice_id": "Kore"}
    ec_tts = SimpleNamespace(**dict(vars(ec), story=voiced,
                                    entities={"characters": {"char_kiwilo": {"voice": pinned}}}))
    assert native_take.pinned_voice(ec_tts, "char_kiwilo") == "gemini/Kore"


def test_the_cast_estimate_says_nothing_about_voice_samples(store):
    pytest.importorskip("fastapi")
    from web.api.routes import stories as routes

    story = store.get(_native_story(store))
    units = workflow.cast_units(store, story, selected=["Kiwilo"])
    message = routes._generation_message(units, {"message": "1 portrait."}, {"ready": True, "message": "ok"}, [],
                                         story=story, llm="3 LLM calls.")
    assert "Voice samples" not in message
    voiced = dict(units, tts_chars=workflow.SAMPLE_CHARS_ESTIMATE)
    assert "Voice samples: up to 120 characters" in routes._generation_message(
        voiced, {"message": "1 portrait."}, {"ready": True, "message": "ok"}, [], story=story, llm="3 LLM calls.")


def test_the_cast_step_hides_the_voice_ui_on_a_no_voice_story():
    src = CAST_STEP.read_text(encoding="utf-8")
    assert "return profile.voices === 'none'" in src
    assert "const withoutVoices = noVoices(story)" in src
    # The tile's mic chip, the sample line, the picker and the recording slot are all behind it.
    assert "{withoutVoices ? null : voice" in src
    assert src.count("{!withoutVoices && (") == 2
    guarded = src[src.index("{!withoutVoices && (\n        <>"):]
    assert guarded.index("<VoiceSection") < guarded.index("<VoiceReferenceSlot") < guarded.index(")}")
