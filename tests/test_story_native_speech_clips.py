"""Native speech: each shot's link by its class, and the speaking clip's
prompt (plan 22, stage 4).

A native-speech story buys a speaking shot's clip on its speech link (the
profile's ``speech_links[speech_model]``: Veo 3.1 Fast by default) and a
silent shot's on its silent link (Veo 3.1 lite), each link sticky on its own
(``assets.json``'s ``links.video_speech`` and ``links.video``: RC-V5 amended
on purpose -- two links an episode, one a class of shot, both in the
estimate). A speaking clip's prompt names the speaker by its handle, the
voice it speaks in -- the same words in every clip of that character -- and
quotes the line; the line, the voice, the Audio sentence and the closing
sentences are never cut to fit. A silent shot keeps the ambience prompt.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

NOW = tas.NOW
FAST, LITE = nsp.FAST, nsp.LITE
PAID = {"GEMINI_PAID_API_KEY": "test-gemini-paid-key", "ALLOW_PAID": "1", "PER_EPISODE_CAP_USD": "40",
        "DAILY_CAP_USD": "80", "PER_STORY_CAP_USD": "200"}
HINTS = {"gender": "female", "age": "young", "style_tags": ["haughty", "sharp"], "direction": "Clipped and cold",
         "sample_line": "Rends-la moi."}


def _video(store, story_id, **settings):
    return tce._units(store, story_id, tas._settings(**{**PAID, **settings}))["video"]


def _give_voice_hints(store, story_id, char_id, hints=HINTS):
    doc = store.read_entity(story_id, "characters", char_id)
    doc["voice_hints"] = dict(hints)
    store.write_entity(story_id, "characters", doc, now=NOW)


# ================================================================ the links

def test_speaking_shots_are_priced_on_the_speech_link_and_silent_ones_on_the_silent_link(store):
    """Fail-first. The estimate prices each speaking shot's planned clip at
    Veo 3.1 Fast's $0.10 a second and each silent one at lite's $0.05, plus
    the retake budget ($1.00) -- the episode's two links named, both in the
    estimate; a premium story prices its speech at $0.40."""
    story_id = nsp.planned_story(store)
    board = tas._board(store, story_id)
    video = _video(store, story_id)
    speech = video["speech"]
    assert (speech["speech_link"], speech["silent_link"]) == (FAST, LITE)
    rows = {row["shot_id"]: row for row in video["plan"]}
    for shot in board["shots"]:
        row = rows[shot["shot_id"]]
        assert row["link"] == (FAST if shot["speaks"] else LITE)
        assert row["clip_s"] == shot["clip_s"]
        assert row["est_usd"] == pytest.approx(shot["clip_s"] * (0.10 if shot["speaks"] else 0.05))
    speaking = sum(shot["clip_s"] for shot in board["shots"] if shot["speaks"])
    silent = sum(shot["clip_s"] for shot in board["shots"] if not shot["speaks"])
    assert (speech["speech_seconds"], speech["silent_seconds"]) == (speaking, silent)
    assert speech["retake_usd"] == 1.0
    assert video["est_usd"] == pytest.approx(speaking * 0.10 + silent * 0.05 + 1.0)
    assert video["ready"] and video["refused"] is None
    assert {row["link"] for row in video["links"]} == {FAST, LITE}
    assert f"speaking ({speaking} s on {FAST}" in video["message"] and f"silent ({silent} s on {LITE}" in video[
        "message"]

    store.update(story_id, lambda doc: doc["generation_profile"].update(speech_model="premium"), now=NOW)
    premium = _video(store, story_id)["speech"]
    assert (premium["speech_link"], premium["speech_price"]) == ("gemini/veo-3.1", 0.40)


def test_a_speech_link_that_cannot_run_yet_refuses_the_plan_naming_it(store, monkeypatch, tmp_path):
    """Resolved by name: a link with no provider yet is a plan shown and
    refused, never a silent switch. Re-pinned on purpose (plan 22 stage 5):
    ``manual/upload`` has its provider now, so a provider-less label stands
    in for it (``nowhere/upload``)."""
    import json

    from clipping.providers import budget as budget_mod

    data = budget_mod.load_profiles()
    data["profiles"]["native_speech"]["speech_links"]["fast"] = "nowhere/upload"
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(budget_mod, "PROFILES_PATH", str(path))
    story_id = nsp.planned_story(store)
    video = _video(store, story_id)
    assert video["ready"] is False and video["refused"].startswith("nowhere/upload: ")


def test_each_class_of_shot_keeps_its_own_sticky_link_and_its_own_switch(store):
    """RC-V5 amended on purpose: a native-speech episode records its silent
    clips' link as ``links.video`` and its speaking clips' as
    ``links.video_speech``; each switches only to a link its profile names."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import assets, sticky_link

    story_id = nsp.planned_story(store)
    ec = tas._ec(store, story_id)
    errors = []
    assert assets.link_switch(ec, {"video_speech": "gemini/veo-3.1", "video": LITE}, env={}, errors=errors) == {
        "video_speech": "gemini/veo-3.1", "video": LITE}
    assert errors == []
    assets.link_switch(ec, {"video_speech": "fal/seedance-1-pro-fast"}, env={}, errors=errors)
    assert errors and "links.video_speech" in errors[0] and "native_speech budget profile" in errors[0]

    workflow.patch_assets(store, story_id, 1, {"links": {"video_speech": "gemini/veo-3.1"}}, now=NOW)
    doc = tas._assets_doc(store, story_id)
    assert sticky_link.recorded(doc, sticky_link.VIDEO_SPEECH)["link"] == "gemini/veo-3.1"
    assert sticky_link.recorded(doc, sticky_link.VIDEO) is None
    assert _video(store, story_id)["speech"]["speech_link"] == "gemini/veo-3.1"


# ================================================================ the prompt

def _speaking_shot(store, story_id):
    board = tas._board(store, story_id)
    return next(shot for shot in board["shots"] if shot["speaks"])


def test_a_speaking_clips_prompt_quotes_its_line_in_its_speakers_voice_and_asks_for_that_voice_alone(store):
    from clipping.aistory import prompting
    from clipping.aistory.steps import clips

    story_id = nsp.planned_story(store)
    shot = _speaking_shot(store, story_id)
    script = eps._script(store, story_id)
    line = next(line for scene in script["scenes"] for line in scene["lines"] if line["line_id"] == shot["lines"][0])
    _give_voice_hints(store, story_id, line["speaker"])
    ec = tas._ec(store, story_id)
    parts = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=FAST)
    prompt = parts["prompt"]
    voice = prompting.voice_line(HINTS)
    assert voice == "a haughty, sharp voice of a young woman, clipped and cold"
    assert f'says in French, in {voice}, "{line["text"]}"' in prompt
    assert "listens without speaking, mouth closed" in prompt
    assert "lips in sync with the words" in prompt and prompt.count("Audio: only ") == 1
    assert prompt.endswith("No music, no narrator, no other voice. No subtitles, no captions, no on-screen text.")
    assert parts["native_audio"] is True and parts["over"] is None
    assert len(prompt.split()) <= 200
    names = {doc["name"] for doc in ec.entities["characters"].values()}
    assert not any(name in prompt for name in names)


def test_the_line_the_voice_and_the_audio_sentences_are_never_dropped_to_fit():
    from clipping.aistory import prompting

    lock = {"motion_rules": {"tier2_prompt_suffix": "x", "tier2_prompt_suffix_v2": "Bold cartoon acting."}}
    voice = prompting.voice_line(HINTS)
    line = "Tu ne l'ouvres jamais, parce que tu as peur de ce que tout le monde verra."
    long = " ".join(["word"] * 60)
    prompt = prompting.speech_clip_prompt(lock, speaker="a red lipstick", look=long, action=long, listener="a nude one",
                                          language="fr", voice=voice, line=line, reaction=long, camera_phrase=long,
                                          place=long, ambience="a marble hall", budget=60)
    kept = prompting.speech_prompt_sentences(prompt)
    assert kept["line"] == line and kept["no_other_sound"] and kept["no_on_screen_text"]
    assert kept["audio"] == "Audio: only a red lipstick's voice speaking French, close and clear, lips in sync with " \
                            "the words."
    assert voice in prompt and "Ambient noise: a marble hall, low underneath." in prompt
    assert "word word" not in prompt  # every context layer went first


def test_every_clip_of_a_character_asks_for_the_same_voice(store):
    from clipping.aistory import prompting
    from clipping.aistory.steps import clips

    story_id = nsp.planned_story(store)
    script = eps._script(store, story_id)
    board = tas._board(store, story_id)
    for char_id in {line["speaker"] for scene in script["scenes"] for line in scene["lines"]} - {"narrator"}:
        _give_voice_hints(store, story_id, char_id)
    ec = tas._ec(store, story_id)
    by_speaker = {}
    for shot in board["shots"]:
        if not shot["speaks"]:
            continue
        _scene, line = clips.speech_line(script, shot)
        prompt = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=FAST)["prompt"]
        by_speaker.setdefault(line["speaker"], set()).add(prompt.split(" in a ", 1)[1].split(', "', 1)[0])
    assert by_speaker and all(voices == {prompting.voice_line(HINTS).split("a ", 1)[1]} for voices in
                              by_speaker.values())


def test_a_silent_shot_keeps_the_ambience_prompt_and_asks_for_its_sound(store):
    from clipping.aistory import prompting
    from clipping.aistory.steps import clips

    story_id = nsp.planned_story(store)
    shot = next(shot for shot in tas._board(store, story_id)["shots"] if not shot["speaks"])
    parts = clips.clip_request_parts(tas._ec(store, story_id), shot, eps._script(store, story_id), tier=3, flags={},
                                     link=LITE)
    assert parts["prompt"].endswith(prompting.AUDIO_CLOSING) and parts["native_audio"] is True
    assert "says in" not in parts["prompt"]


# ================================================================ the exchange (plan 27 stage 3)

CLOSING = "No music, no narrator, no other voice. No subtitles, no captions, no on-screen text."


def test_an_exchange_shots_prompt_quotes_every_line_in_turn_and_hears_both_voices(store):
    """Fail-first. The board's exchange shot (scene s02's two lines, one 8 s
    clip): its prompt quotes both lines in order -- the first speaker's in
    its voice, the answer in the other's --, says ``Audio: the voices of A
    and B only`` exactly once, ends with the two closings, fits the link's
    cap and names no character by its name. ``speech_prompt_sentences``
    finds the quotes in order."""
    from clipping.aistory import prompting
    from clipping.aistory.steps import clips

    story_id = nsp.exchange_story(store)
    shot = nsp.exchange_shot(store, story_id)
    script = eps._script(store, story_id)
    said = clips.speech_lines(script, shot)
    assert [line["line_id"] for line in said] == shot["lines"] and len(said) == 2
    for line in said:
        _give_voice_hints(store, story_id, line["speaker"])
    ec = tas._ec(store, story_id)
    parts = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=FAST)
    prompt = parts["prompt"]
    voice = prompting.voice_line(HINTS)
    handles = clips.shots_mod.character_handles(ec.entities["characters"])
    first, second = (handles[line["speaker"]] for line in said)
    assert f'looks at {second} and says in French, in {voice}, "{said[0]["text"]}"' in prompt
    assert f'{second[0].upper() + second[1:]} answers at once, in {voice}, "{said[1]["text"]}"' in prompt
    assert prompt.index(said[0]["text"]) < prompt.index(said[1]["text"])
    assert prompt.count(f"Audio: the voices of {first} and {second} only, speaking French in turn, lips in sync "
                        "with the words, no overlap.") == 1
    assert prompt.count("Audio:") == 1 and prompt.count("listens without speaking, mouth closed") == 1
    assert prompt.endswith(CLOSING)
    assert parts["native_audio"] is True and parts["over"] is None
    assert len(prompt.split()) <= clips.prompt_budgets.speech_clip_words(FAST)
    names = {doc["name"] for doc in ec.entities["characters"].values()}
    assert not any(name in prompt for name in names)
    kept = prompting.speech_prompt_sentences(prompt)
    assert kept["lines"] == [line["text"] for line in said] and kept["line"] == said[0]["text"]
    # a one-line shot of the same board keeps its one-line prompt
    single = next(item for item in tas._board(store, story_id)["shots"] if item["speaks"] and len(item["lines"]) == 1)
    one = clips.clip_request_parts(ec, single, script, tier=3, flags={}, link=FAST)["prompt"]
    assert one.count("Audio: only ") == 1 and "answers at once" not in one
    assert prompting.speech_prompt_sentences(one)["lines"] == [prompting.speech_prompt_sentences(one)["line"]]


def _three_lines():
    return [{"speaker_id": "char_a", "speaker": "a red lipstick", "voice_line": "a sharp voice", "text": "Tu mens."},
            {"speaker_id": "char_b", "speaker": "a nude lipstick", "voice_line": "a soft voice",
             "text": "Prouve-le, alors."},
            {"speaker_id": "char_a", "speaker": "a red lipstick", "voice_line": "a sharp voice",
             "text": "Je l'ai vu"}]


def test_an_exchange_of_three_lines_keeps_every_quote_and_each_voice_once_whatever_the_budget():
    from clipping.aistory import prompting

    lock = {"motion_rules": {"tier2_prompt_suffix": "x", "tier2_prompt_suffix_v2": "Bold cartoon acting."}}
    long = " ".join(["word"] * 60)
    lines = _three_lines()
    for budget in (200, 110):
        prompt = prompting.speech_exchange_clip_prompt(
            lock, lines=lines, speaker="a red lipstick", look=long, action=long, listener="a nude lipstick",
            language="fr", voice="a sharp voice", line="Tu mens.", reaction=long, camera_phrase=long, place=long,
            ambience="a marble hall", budget=budget)
        assert prompting.speech_prompt_sentences(prompt)["lines"] == ["Tu mens.", "Prouve-le, alors.", "Je l'ai vu"]
        assert 'says in French, in a sharp voice, "Tu mens."' in prompt
        assert 'A nude lipstick answers at once, in a soft voice, "Prouve-le, alors."' in prompt
        assert 'A red lipstick answers at once, "Je l\'ai vu".' in prompt
        assert prompt.count("Audio: the voices of a red lipstick and a nude lipstick only") == 1
        assert prompt.count("a sharp voice") == 1 and prompt.endswith(CLOSING)
    assert len(prompt.split()) <= 110 and "word word" not in prompt  # every context layer went first
    # the same speaker going on, and the action style
    lines[2]["speaker_id"], lines[1]["speaker_id"] = "char_a", "char_a"
    lines[1].update(speaker="a red lipstick", voice_line="a sharp voice")
    alone = prompting.speech_exchange_clip_prompt(
        lock, lines=lines, speaker="a red lipstick", look="", action="", listener="a nude lipstick", language="fr",
        reaction="", camera_phrase="Slow push in", place="", ambience="")
    assert 'A red lipstick goes on, "Prouve-le, alors."' in alone
    assert "Audio: only a red lipstick's voice speaking French" in alone
    assert "A nude lipstick listens without speaking, mouth closed." in alone
    action = prompting.speech_exchange_clip_prompt_action(
        lines=_three_lines(), speaker="a red lipstick", listener="a nude lipstick", action="@x grips the pen",
        language="fr", reaction="", camera_phrase="Slow push in", place="a marble hall", ambience="the ambience")
    assert prompting.speech_prompt_sentences(action)["lines"] == ["Tu mens.", "Prouve-le, alors.", "Je l'ai vu"]
    assert action.count("Audio: the voices of a red lipstick and a nude lipstick only") == 1
    assert action.endswith(CLOSING)
