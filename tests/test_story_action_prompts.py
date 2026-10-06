"""Action-dense clip prompts, per story (plan 23, stage D6).

``generation_profile.prompt_style`` is ``studio`` (absent: today's prompts,
byte for byte) or ``action``: a clip's prompt is ONE continuous physical
action in the present tense, every character named at every mention by the
same colour/species anchor built from its look (``shots.character_anchor``),
the place said once in a few words, the sounds inline, exactly one camera
phrase; the quoted line and the closing sentences are never cut. The switch
sits in ``prompting`` + ``clips.speech_request_parts`` / ``clip_request_parts``
-- the clip's ``prompt_hash`` comes from them, so a story changing style makes
its current clips stale (uploads included), and the story PATCH says how many.

Offline and hermetic (the assets step's fixtures); stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_episode_steps as eps
import test_story_manual_link as tml
import test_story_native_speech_clips as nsc
import test_story_native_speech_plan as nsp
from test_stories_api import api  # noqa: F401 - the API fixture (routes mounted over the tmp outputs)
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_clip_estimate import timings_path  # noqa: F401 - the measured clip timings under tmp_path

from clipping.aistory import defaults, media_policy, prompting, shots

NOW = tas.NOW
ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"
# Every clip prompt of the fixtures' stories as ``main`` built them before this stage (studio).
# re-pinned 2026-10-06, plan 32 stage 3: the Pixar-style cartoon look of fruit_drama (DEC-315): only the clips'
# negative prompts and the hashes they feed moved (no prompt text did).
GOLDEN = ROOT / "tests" / "fixtures" / "aistory_action_prompts" / "studio_prompts.json"
FAST, LITE, VEO = nsc.FAST, nsc.LITE, "gemini/veo-3.1-lite"
NOTES = (None, "Slower push-in on the face")

STRAWBERRY = {
    "descriptor": "A plump strawberry with a sly smile",
    "signature_items": ["rope belt"],
    "look": {"build": "plump", "silhouette": "round", "face": "dot eyes", "hair": "green leaf crown",
             "skin_material": "glossy fruit skin", "height_cm": 150, "palette": ["green", "yellow"],
             "presentation": "young adult woman",
             "wardrobe_sets": [{"id": "daily", "context": "every day",
                                "items": "a dirty burlap dress with a frayed hem, rope belt"}],
             "season_change": None},
}


# ================================================================ the anchor

def test_a_character_s_anchor_is_its_colour_gender_species_and_first_outfit_item():
    assert shots.character_anchor(STRAWBERRY, "the strawberry") == (
        "the green-yellow female strawberry character in a dirty burlap dress")
    # deterministic: the same document, the same words
    assert shots.character_anchor(STRAWBERRY, "the strawberry") == shots.character_anchor(
        json.loads(json.dumps(STRAWBERRY)), "the strawberry")


def test_an_anchor_leaves_out_what_the_look_does_not_say_and_never_repeats_a_handle_s_own_colour():
    bare = {"look": dict(STRAWBERRY["look"], presentation=None, palette=["green"], wardrobe_sets=[
        {"id": "daily", "context": "x", "items": "tattered robes"}])}
    bare["look"].pop("presentation")
    assert shots.character_anchor(bare, "the strawberry") == "the green strawberry character in tattered robes"
    # the handle already says its colour: the palette adds none
    assert shots.character_anchor(STRAWBERRY, "the dark brown ripe kiwi fruit") == (
        "the female dark brown ripe kiwi fruit character in a dirty burlap dress")
    # no look: the handle; a handle told apart by its outfit or number is kept as it is
    assert shots.character_anchor({"descriptor": "x"}, "the strawberry") == "the strawberry"
    assert shots.character_anchor(STRAWBERRY, "the strawberry wearing a cape") == "the strawberry wearing a cape"
    assert shots.character_anchor(STRAWBERRY, "the strawberry (2)") == "the strawberry (2)"
    # a plural outfit takes no article
    male = {"look": dict(STRAWBERRY["look"], presentation="man in his forties", palette=["red"], wardrobe_sets=[
        {"id": "daily", "context": "x", "items": "worn leather boots, a hat"}])}
    assert shots.character_anchor(male, "the chilli") == "the red male chilli character in worn leather boots"


def test_the_place_anchor_is_the_descriptor_s_head_in_at_most_ten_words():
    assert prompting.place_anchor("A dimly lit tropical wooden confession booth with a carved bamboo chair") == (
        "a dimly lit tropical wooden confession booth")
    assert prompting.place_anchor("A luxurious turquoise swimming pool surrounded by white wooden loungers.") == (
        "a luxurious turquoise swimming pool")
    long = prompting.place_anchor("A vast, echoing, moss covered, candle lit, abandoned cathedral nave, cold and damp")
    assert len(long.split()) <= 10 and long.startswith("a vast")
    assert prompting.place_anchor("") == "" and prompting.place_anchor(None) == ""


# ================================================================ the fixtures

def _dress(store, story_id):
    """Every speaking character a voice and a look (palette, presentation, outfit): the anchors."""
    script = eps._script(store, story_id)
    looks = [(["green", "yellow"], "young adult female", "a dirty burlap dress, a rope belt"),
             (["red", "orange"], "adult male", "a stiff blue cape, round glasses"),
             (["brown", "black"], "woman in her thirties", "tattered robes")]
    chars = sorted({line["speaker"] for scene in script["scenes"] for line in scene["lines"]} - {"narrator"})
    for number, char_id in enumerate(chars):
        palette, presentation, items = looks[number % len(looks)]
        doc = store.read_entity(story_id, "characters", char_id)
        doc["voice_hints"] = dict(nsc.HINTS)
        doc["look"] = {"build": "small", "silhouette": "round", "face": "dot eyes", "hair": "none",
                       "skin_material": "matte fruit skin", "height_cm": 150, "palette": palette,
                       "presentation": presentation,
                       "wardrobe_sets": [{"id": "daily", "context": "every day", "items": items}],
                       "season_change": None}
        store.write_entity(story_id, "characters", doc, now=NOW)


def _style(store, story_id, style):
    store.update(story_id, lambda doc: doc["generation_profile"].update(prompt_style=style), now=NOW)


def _native(store, style=None):
    """``(story_id, ec, script, board)`` of a planned native-speech story, its characters dressed."""
    story_id = nsp.planned_story(store)
    _dress(store, story_id)
    if style is not None:
        _style(store, story_id, style)
    return story_id, tas._ec(store, story_id), eps._script(store, story_id), tas._board(store, story_id)


def _parts(ec, script, shot, *, link=None, note=None, tier=3, flags=None):
    from clipping.aistory.steps import clips

    return clips.clip_request_parts(ec, shot, script, tier=tier, flags=flags or {}, note=note,
                                    link=link if link is not None else (FAST if shot.get("speaks") else LITE))


def _anchors(ec):
    return shots.character_anchors(ec.entities["characters"])


def _handles(ec):
    return shots.character_handles(ec.entities["characters"])


def _without(prompt, anchors):
    """*prompt* with every anchor taken out, whatever its capital."""
    for anchor in sorted(anchors, key=len, reverse=True):
        prompt = re.sub(re.escape(anchor), "", prompt, flags=re.IGNORECASE)
    return prompt


def _mentions(prompt, anchor):
    return len(re.findall(re.escape(anchor), prompt, flags=re.IGNORECASE))


# ================================================================ studio is today's

def _dump(ec, script, board, tier, link_for, flags_for):
    from clipping.aistory.steps import clips

    out = {}
    for shot in board["shots"]:
        for note in NOTES:
            link = link_for(shot)
            parts = clips.clip_request_parts(ec, shot, script, tier=tier, flags=flags_for(shot), note=note, link=link)
            out[f"{shot['shot_id']}|link={link}|note={note}"] = {
                key: parts.get(key) for key in ("prompt", "negative", "native_audio", "hash", "over", "refit")}
    return out


def _studio_dumps(store, tmp_path, *, explicit):
    """The four dumps the golden holds, as ``main`` made them; *explicit*: the story says
    ``prompt_style: studio`` in its profile (it must change nothing)."""
    data = {}
    story_id = nsp.planned_story(store)
    script = eps._script(store, story_id)
    for char_id in {line["speaker"] for scene in script["scenes"] for line in scene["lines"]} - {"narrator"}:
        nsc._give_voice_hints(store, story_id, char_id)
    if explicit:
        _style(store, story_id, "studio")
    ec, board = tas._ec(store, story_id), tas._board(store, story_id)
    data["native"] = _dump(ec, script, board, 3, lambda s: FAST if s["speaks"] else LITE, lambda s: {})
    sid = tas._episode(store, tmp_path)
    tce._tier(store, sid, tier=3, budget_profile="quality")
    store.update(sid, lambda doc: doc["generation_profile"].update(pipeline="v2"), now=NOW)
    if explicit:
        _style(store, sid, "studio")
    ec, board, script = tas._ec(store, sid), tas._board(store, sid), eps._script(store, sid)
    data["ambience"] = _dump(ec, script, board, 3, lambda s: VEO, lambda s: {})
    data["ambience_nolink"] = _dump(ec, script, board, 3, lambda s: None, lambda s: {})
    store.update(sid, lambda doc: doc["generation_profile"].update(tier=2, budget_profile="one_dollar"), now=NOW)
    ec = tas._ec(store, sid)
    data["tier2"] = _dump(ec, script, board, 2, lambda s: "fal/seedance-1-pro-fast", lambda s: {})
    return data


@pytest.mark.parametrize("explicit", [False, True])
def test_studio_prompts_and_hashes_are_byte_identical_to_the_ones_main_built(store, tmp_path, explicit):
    """The pin: the speaking prompts (native-speech story), the ambience prompts (layered shots of a
    native-speech story's silent shots, legacy ones of a quality story), the silent tier-2 prompts, each
    with and without a re-animate note and a link -- prompt, negative, hash, over, refit -- are the golden
    captured from ``main`` before this stage, with the key absent AND with ``studio`` said."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    data = _studio_dumps(store, tmp_path, explicit=explicit)
    assert set(data) == set(golden) and all(len(data[name]) == 48 for name in data)
    for name in golden:
        assert data[name] == golden[name], name


# ================================================================ the key

def test_the_profile_key_is_wired_like_the_others(tmp_path):
    from clipping.aistory import schemas, store as store_mod

    assert defaults.PROMPT_STYLES == ("studio", "action")
    assert "prompt_style" not in defaults.default_generation_profile()
    merged = store_mod._merge_generation_profile({"pipeline": "v2", "prompt_style": "action"})
    assert merged["prompt_style"] == "action"
    for bad in ({"prompt_style": "cinematic"}, {"prompt_style": 1}):
        with pytest.raises(ValueError):
            store_mod._merge_generation_profile(bad)
    assert "prompt_style" not in store_mod._merge_generation_profile({"prompt_style": None})
    assert schemas._GENERATION_PROFILE_SCHEMA["properties"]["prompt_style"] == {
        "type": "string", "enum": ["studio", "action"]}
    stories = store_mod.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story = stories.create(language="fr", generation_profile={"pipeline": "v2", "prompt_style": "action"}, now=NOW)
    assert story["generation_profile"]["prompt_style"] == "action"


def test_the_api_model_carries_the_key_and_drops_the_absent_one():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert "prompt_style" not in GenerationProfileModel().model_dump()
    assert GenerationProfileModel(prompt_style="action").model_dump()["prompt_style"] == "action"


def test_the_accessor_reads_the_key_only_on_a_v2_story():
    v2 = {"generation_profile": {"pipeline": "v2", "prompt_style": "action"}}
    assert media_policy.prompt_style(v2) == "action" and media_policy.action_prompts(v2) is True
    assert media_policy.prompt_style({"generation_profile": {"pipeline": "v2"}}) == "studio"
    assert media_policy.prompt_style({"generation_profile": {"prompt_style": "action"}}) == "studio"
    assert media_policy.prompt_style({"generation_profile": {"pipeline": "v2", "prompt_style": "x"}}) == "studio"
    assert media_policy.prompt_style(None) == "studio" and media_policy.action_prompts({}) is False


# ================================================================ the speaking prompt

def _speaking(board):
    return [shot for shot in board["shots"] if shot["speaks"]]


def test_every_mention_of_a_character_is_its_anchor_and_the_bare_handle_is_never_said(store):
    from clipping.aistory.steps import clips

    story_id, ec, script, board = _native(store, "action")
    anchors, handles = _anchors(ec), _handles(ec)
    assert all(anchor != handles[cid] and handles[cid][4:] in anchor for cid, anchor in anchors.items())
    for shot in _speaking(board):
        prompt = _parts(ec, script, shot)["prompt"]
        _scene, line = clips.speech_line(script, shot)
        speaker = anchors[line["speaker"]]
        # the speaker: its own sentence and the Audio sentence (and the action, when it is about it)
        assert f"Audio: only {speaker}'s voice speaking French" in prompt
        assert _mentions(prompt, speaker) >= 2
        # no handle of any character is said outside an anchor
        bare = _without(prompt, anchors.values()).lower()
        for handle in handles.values():
            assert handle.lower() not in bare, (shot["shot_id"], handle)
        # an anchor is always said whole: the species is always followed by its outfit
        for anchor in anchors.values():
            species = anchor.split(" in ")[0]
            assert _mentions(prompt, species) == _mentions(prompt, anchor)


def test_the_listener_is_named_by_its_anchor_too(store):
    story_id, ec, script, board = _native(store, "action")
    anchors = _anchors(ec)
    shot = next(item for item in _speaking(board) if len(item["subject_tags"]) >= 3)
    prompt = _parts(ec, script, shot)["prompt"]
    listens = re.search(r"([A-Za-z][^.]*?) listens without speaking, mouth closed", prompt)
    assert listens and f"{listens.group(1)}".lower() in {anchor.lower() for anchor in anchors.values()}


def test_the_speaking_prompt_is_one_action_with_the_place_once_and_one_camera_move(store):
    story_id, ec, script, board = _native(store, "action")
    shot = next(item for item in _speaking(board) if item["camera_motion"] == "push_in")
    prompt = _parts(ec, script, shot)["prompt"]
    cameras = [phrase for phrase in prompting.CAMERA_PHRASES.values() if phrase in prompt.lower()]
    assert cameras == [prompting.CAMERA_PHRASES["push_in"]] and prompt.lower().count("push-in") == 1
    assert prompt.startswith("Slow push-in toward the subject. In a dimly lit tropical wooden confession booth, ")
    place = "dimly lit tropical wooden confession booth"
    assert prompt.count(place) == 1 and "carved bamboo chair" not in prompt
    # the studio layers are not there: no identity clause, no style suffix, no "ambient noise" line
    assert "Keep every character's look" not in prompt and "Expressive character animation" not in prompt
    assert "Ambient noise" not in prompt and "Sounds: the day ambience of the setting." in prompt
    # one action sentence, in the present tense, between the camera and the quoted line
    action = prompt.split(". ")[1]
    assert " speaks, " in action and not re.search(r"\b(spoke|said|looked|walked)\b", prompt)


def test_the_line_is_quoted_in_the_speaker_s_voice_and_the_closing_sentences_end_the_prompt(store):
    from clipping.aistory.steps import clips

    story_id, ec, script, board = _native(store, "action")
    for shot in _speaking(board):
        _scene, line = clips.speech_line(script, shot)
        parts = _parts(ec, script, shot)
        prompt = parts["prompt"]
        voice = prompting.voice_line(nsc.HINTS)
        assert f'says in French, in {voice}, "{line["text"]}"' in prompt
        assert prompt.endswith("Audio: only " + _anchors(ec)[line["speaker"]] + "'s voice speaking French, close and "
                               "clear, lips in sync with the words. No music, no narrator, no other voice. "
                               "No subtitles, no captions, no on-screen text.")
        assert parts["native_audio"] is True and parts["over"] is None and parts["refit"] is None
        assert len(prompt.split()) <= prompt_budget(FAST)
        names = {doc["name"] for doc in ec.entities["characters"].values()}
        assert not any(name in prompt.replace(line["text"], "") for name in names)


def prompt_budget(link):
    from clipping.aistory import prompt_budgets

    return prompt_budgets.speech_clip_words(link)


def _speech_kwargs(**over):
    base = dict(speaker="the green-yellow female strawberry character in a dirty burlap dress",
                listener="the red male chilli character in worn leather boots",
                action="the green-yellow female strawberry character in a dirty burlap dress slams the cracked "
                       "phone on the table, then leans over it, trembling, jaw set, eyes wide",
                language="fr", voice=prompting.voice_line(nsc.HINTS),
                line="Tu ne l'ouvres jamais, parce que tu as peur de ce que tout le monde verra.",
                reaction="stepping back, eyes widening", camera_phrase=prompting.CAMERA_PHRASES["push_in"],
                place="A luxurious turquoise swimming pool surrounded by white wooden loungers",
                sfx=["phone slam", "water lapping"], ambience="the night ambience of the setting")
    base.update(over)
    return base


@pytest.mark.parametrize("budget", [200, 140, 100, 80, 60, 40, 10])
def test_the_line_the_voice_and_the_closing_sentences_survive_any_budget_and_the_budget_holds_when_it_can(budget):
    kwargs = _speech_kwargs()
    prompt = prompting.speech_clip_prompt_action(budget=budget, **kwargs)
    line, who = kwargs["line"], kwargs["speaker"]
    assert f'"{line}"' in prompt and kwargs["voice"] in prompt
    assert f"Audio: only {who}'s voice speaking French, close and clear, lips in sync with the words." in prompt
    assert prompt.endswith("No music, no narrator, no other voice. No subtitles, no captions, no on-screen text.")
    kept = prompting.speech_prompt_sentences(prompt)
    assert kept["line"] == line and kept["no_other_sound"] and kept["no_on_screen_text"]
    closing_floor = len((f'{who} says in French, in {kwargs["voice"]}, "{line}" ' + kept["audio"] + " "
                         + prompting.SPEECH_NO_OTHER_SOUND + " " + prompting.NO_ON_SCREEN_TEXT).split())
    if budget >= closing_floor + 12:
        assert len(prompt.split()) <= budget


def test_a_tight_budget_drops_the_sounds_first_then_the_reaction_the_place_the_listener_and_last_the_action():
    roomy = prompting.speech_clip_prompt_action(budget=200, **_speech_kwargs())
    assert "Sounds: phone slam, water lapping, the night ambience of the setting." in roomy
    assert "stepping back" in roomy and "swimming pool" in roomy and "slams the cracked phone" in roomy
    n = len(roomy.split())
    no_sounds = prompting.speech_clip_prompt_action(budget=n - 1, **_speech_kwargs())
    assert "Sounds:" not in no_sounds and "stepping back" in no_sounds and "swimming pool" in no_sounds
    tight = prompting.speech_clip_prompt_action(budget=140, **_speech_kwargs())
    assert "swimming pool" not in tight and "stepping back" not in tight and "Sounds:" not in tight
    assert "listens without speaking" in tight and prompting.CAMERA_PHRASES["push_in"] in tight.lower()
    tighter = prompting.speech_clip_prompt_action(budget=120, **_speech_kwargs())
    assert "listens without speaking" not in tighter and "slams the cracked phone" in tighter


def test_a_long_action_is_cut_at_a_clause_never_inside_one_and_never_leaves_a_dangling_word():
    anchor = "the green-yellow female strawberry character in a dirty burlap dress"
    long = (f"{anchor} slams the cracked phone on the table, then leans over it, trembling, jaw set, eyes wide, "
            "mouth tight, breath ragged, hands shaking, knuckles white, teeth bared, leaf crown trembling, tears welling, "
            "voice cracking. A second sentence is never said")
    cut = prompting.action_sentence(long)
    assert len(cut.split()) <= prompting.ACTION_MAX_WORDS and long.startswith(cut)
    assert long[len(cut)] == "," and "second sentence" not in cut
    short = prompting.action_sentence(long, prompting.ACTION_SHORT_WORDS, clauses=True)
    assert len(short.split()) <= prompting.ACTION_SHORT_WORDS and long.startswith(short) and len(short) < len(cut)
    # no clause fits: a clause-only cut is empty; the other cuts at the word and drops what dangles
    unbroken = f"{anchor} slams the cracked phone onto the table and leans over it while the whole booth shakes loudly"
    assert prompting.action_sentence(unbroken, 13, clauses=True) == ""
    word_cut = prompting.action_sentence(unbroken, 13)
    assert unbroken.startswith(word_cut) and len(word_cut.split()) <= 13 and word_cut.split()[-1] not in ("the", "a")
    assert prompting.action_sentence(f"{anchor} slams the cracked phone", 12) == f"{anchor} slams"
    assert prompting.action_sentence("") == "" and prompting.action_sentence(None) == ""


def test_the_speech_budget_is_the_links_and_an_over_budget_prompt_is_never_sent_on_a_small_link(store):
    story_id, ec, script, board = _native(store, "action")
    shot = _speaking(board)[0]
    small = _parts(ec, script, shot, link="gemini/veo-3.1-fast")
    assert len(small["prompt"].split()) <= prompt_budget("gemini/veo-3.1-fast")
    # with no link known the budget is the speech clip's own 200 words
    from clipping.aistory.steps import clips

    assert len(clips.speech_request_parts(ec, shot, script)["prompt"].split()) <= prompting.SPEECH_CLIP_MAX_WORDS


# ================================================================ the silent / ambience prompt

def test_a_silent_shot_of_a_native_speech_story_is_one_action_with_its_sounds_and_silent_speakers(store):
    story_id, ec, script, board = _native(store, "action")
    anchors = _anchors(ec)
    shot = next(item for item in board["shots"] if not item["speaks"])
    parts = _parts(ec, script, shot)
    prompt = parts["prompt"]
    assert parts["native_audio"] is True and parts["refit"] is None
    assert prompt.startswith("Slow push-in toward the subject. In a dimly lit tropical wooden confession booth, ")
    assert "Sounds: the day ambience of the setting." in prompt
    assert prompt.endswith(prompting.AUDIO_CLOSING)
    assert '"' not in prompt and prompt.count("confession booth") == 1
    assert "Keep every character's look" not in prompt
    assert len([phrase for phrase in prompting.CAMERA_PHRASES.values() if phrase in prompt.lower()]) == 1
    _style(store, story_id, "studio")
    assert _parts(tas._ec(store, story_id), script, shot)["prompt"] != prompt


def test_a_silent_prompt_keeps_every_speaker_silent_and_names_it_by_its_anchor():
    anchor = "the green-yellow female strawberry character in a dirty burlap dress"
    prompt = prompting.clip_prompt_action(
        action=f'{anchor} shouts "Never!" and slams the phone on the table', camera_phrase="static camera, locked-off "
        "shot", place="A busy diner with neon signs", sfx=["phone slam"], ambience="the night ambience of the setting",
        speakers=[anchor], sounds=True, note="Faster", budget=140)
    assert prompt.startswith("Static camera, locked-off shot. In a busy diner, ")
    assert '"' not in prompt and "shouts silently" in prompt and "Never!" not in prompt
    assert f"{anchor[0].upper() + anchor[1:]} speaks silently: their words are not heard." in prompt
    assert "Sounds: phone slam, the night ambience of the setting." in prompt and "Faster." in prompt
    assert prompt.endswith(prompting.AUDIO_CLOSING)


@pytest.mark.parametrize("budget", [140, 70, 45, 20])
def test_the_silent_closing_and_speakers_survive_any_budget(budget):
    anchor = "the strawberry"
    prompt = prompting.clip_prompt_action(
        action="the strawberry walks to the door, opens it slowly, glances back over its shoulder and steps out "
               "into the rain without a word", camera_phrase="slow pan from left to right",
        place="A busy diner with neon signs and a long counter", sfx=["door slam"], ambience="the day ambience",
        speakers=[anchor], sounds=True, budget=budget)
    assert prompt.endswith(prompting.AUDIO_CLOSING) and "The strawberry speaks silently" in prompt
    if budget >= 45:
        assert len(prompt.split()) <= budget


def test_a_clip_whose_sound_is_discarded_has_no_sound_sentences(store, tmp_path):
    """Tier 2 (the one-dollar profile): the action, the camera and the place, nothing about sound."""
    sid = tas._episode(store, tmp_path)
    tce._tier(store, sid, tier=2, budget_profile="one_dollar")
    store.update(sid, lambda doc: doc["generation_profile"].update(pipeline="v2", prompt_style="action"), now=NOW)
    ec, board, script = tas._ec(store, sid), tas._board(store, sid), eps._script(store, sid)
    shot = board["shots"][0]
    parts = _parts(ec, script, shot, tier=2, link="fal/seedance-1-pro-fast")
    assert parts["native_audio"] is False and "Sounds:" not in parts["prompt"] and "Audio:" not in parts["prompt"]
    assert parts["prompt"].startswith(prompting.CAMERA_PHRASES[shot["camera_motion"]][0].upper())
    assert len([p for p in prompting.CAMERA_PHRASES.values() if p in parts["prompt"].lower()]) == 1
    assert "no morphing" not in parts["prompt"] and parts["negative"]


def test_an_ambience_story_s_clip_in_the_action_style_asks_for_its_sound(store, tmp_path):
    sid = tas._episode(store, tmp_path)
    tce._tier(store, sid, tier=3, budget_profile="quality")
    store.update(sid, lambda doc: doc["generation_profile"].update(pipeline="v2", prompt_style="action"), now=NOW)
    ec, board, script = tas._ec(store, sid), tas._board(store, sid), eps._script(store, sid)
    from clipping.aistory import prompt_budgets

    for shot in board["shots"]:
        parts = _parts(ec, script, shot, link=VEO)
        assert parts["native_audio"] is True and parts["prompt"].endswith(prompting.AUDIO_CLOSING)
        assert len(parts["prompt"].split()) <= prompt_budgets.clip_audio_words(VEO)
        assert parts["over"] is None and parts["refit"] is None


# ================================================================ the hash and the stale clips

def test_the_hash_is_unchanged_under_studio_and_changed_under_action_for_every_shot(store):
    from clipping.aistory.steps import clips

    story_id, ec, script, board = _native(store)
    studio = {shot["shot_id"]: _parts(ec, script, shot)["hash"] for shot in board["shots"]}
    _style(store, story_id, "action")
    action_ec = tas._ec(store, story_id)
    action = {shot["shot_id"]: _parts(action_ec, script, shot)["hash"] for shot in board["shots"]}
    assert set(studio) == set(action) and all(studio[sid] != action[sid] for sid in studio)
    _style(store, story_id, "studio")
    again = tas._ec(store, story_id)
    assert {shot["shot_id"]: _parts(again, script, shot)["hash"] for shot in board["shots"]} == studio
    # the speech parts and the clip parts agree on a speaking shot
    shot = _speaking(board)[0]
    assert clips.speech_request_parts(again, shot, script, link=FAST)["hash"] == studio[shot["shot_id"]]


def _upload_a_clip(store, story_id, shot_id):
    """A current uploaded clip (the manual link) for *shot_id*, its prompt hash as ``studio`` makes it."""
    from clipping.aistory import manual_uploads
    from clipping.aistory.steps import assets, clips
    from clipping.providers import generation as gen

    ec = tas._ec(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == shot_id)
    parts = clips.clip_request_parts(ec, shot, script, tier=3, flags={}, link=gen.MANUAL_LINK)
    name = manual_uploads.manual_clip_name(shot_id)
    path = store.episode_asset_path(story_id, 1, "clips", name, create=True)
    with open(path, "wb") as fh:
        fh.write(b"not a real video")
    shot["assets"]["clip"] = {
        "state": "current", "link": gen.MANUAL_LINK, "route": "manual", "clip_s": 4, "est_usd": 0.0,
        "prompt_hash": parts["hash"], "cache_key": None, "generated_at": NOW, "note": None,
        "image_sha256": assets._canonical_sha256({"manual": shot_id, "image": shot["assets"].get("image")}),
        "sha256": "a" * 64, "uploaded_at": NOW, "duration_s": 4.0, "filename": "take.mp4"}
    shot["assets"]["video"] = manual_uploads.manual_clip_rel(shot_id)
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return shot


def _stale(store, story_id, shot_id):
    from clipping.aistory.steps import clips

    ec = tas._ec(store, story_id)
    script, board = eps._script(store, story_id), tas._board(store, story_id)
    shot = next(item for item in board["shots"] if item["shot_id"] == shot_id)
    return clips.clip_state(ec, shot, script, link="manual/upload", tier=3, flags={}, image_sha=None)


def test_an_uploaded_clip_goes_stale_when_its_story_changes_style_and_current_again_when_it_changes_back(store):
    story_id = tml.manual_story(store)
    speaking = next(shot for shot in tas._board(store, story_id)["shots"] if shot["speaks"])
    _upload_a_clip(store, story_id, speaking["shot_id"])
    assert _stale(store, story_id, speaking["shot_id"]) == "current"
    _style(store, story_id, "action")
    assert _stale(store, story_id, speaking["shot_id"]) == "stale"
    _style(store, story_id, "studio")
    assert _stale(store, story_id, speaking["shot_id"]) == "current"


# ================================================================ the PATCH warning

def _references(store, story_id):
    """The fixtures' v2 story is prompt-only; the real PATCH holds a v2 story to references."""
    store.update(story_id, lambda doc: doc["generation_profile"].update(consistency_mode="references"), now=NOW)


def test_the_change_warning_counts_the_current_clips_that_will_go_stale_uploads_included(store):
    from clipping.aistory import workflow

    story_id = tml.manual_story(store)
    _references(store, story_id)
    board = tas._board(store, story_id)
    speaking = [shot for shot in board["shots"] if shot["speaks"]][:2]
    silent = next(shot for shot in board["shots"] if not shot["speaks"])
    for shot in (*speaking, silent):
        _upload_a_clip(store, story_id, shot["shot_id"])
    change = workflow.prompt_style_change(store, story_id, {"generation_profile": {"prompt_style": "action"}})
    assert change["stale_clips"] == 3
    assert "3 current clips" in change["warning"] and "uploads included" in change["warning"]
    # not a change: nothing to say
    assert workflow.prompt_style_change(store, story_id, {"generation_profile": {"prompt_style": "studio"}}) is None
    assert workflow.prompt_style_change(store, story_id, {"generation_profile": {"tier": 3}}) is None
    assert workflow.prompt_style_change(store, story_id, {"title": "x"}) is None
    # a stale clip is not counted again: with the style changed, going back counts the clips the change makes stale
    _style(store, story_id, "action")
    back = workflow.prompt_style_change(store, story_id, {"generation_profile": {"prompt_style": None}})
    assert back["stale_clips"] == 0 and back["warning"] is None
    # nothing was written by asking
    assert store.get(story_id)["generation_profile"]["prompt_style"] == "action"


def test_the_story_patch_answers_with_the_warning_and_a_story_with_no_clips_with_a_count_of_zero(api, store):
    story_id = tml.manual_story(store)
    _references(store, story_id)
    shot = next(item for item in tas._board(store, story_id)["shots"] if item["speaks"])
    _upload_a_clip(store, story_id, shot["shot_id"])
    reply = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"prompt_style": "action"}})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["stale_clips"] == 1 and "1 current clip " in body["warning"]
    assert body["generation_profile"]["prompt_style"] == "action" and body["story_id"] == story_id
    # a patch that does not change the style carries neither key
    other = api.client.patch(f"/api/stories/{story_id}", json={"title": "Another"})
    assert other.status_code == 200 and "warning" not in other.json() and "stale_clips" not in other.json()
    # the clip is stale now, and going back says nothing is lost
    back = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"prompt_style": None}})
    assert back.json()["stale_clips"] == 0 and back.json()["warning"] is None
    assert "prompt_style" not in back.json()["generation_profile"]
    bad = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"prompt_style": "cinematic"}})
    assert bad.status_code == 400


# ================================================================ no request, no cent (RC-N4)

def test_a_manual_link_never_sends_a_request_or_books_a_cent_in_either_style(store):
    """The brief and the upload read the same prompt builders; neither calls a provider."""
    story_id = tml.manual_story(store)
    _style(store, story_id, "action")
    tml._planted(store, story_id)
    brief = tml._brief(store, story_id, "flow")
    speaking = next(item for item in brief["shots"] if item["speaks"])
    assert speaking["prompt"].count("Audio: only ") == 1 and "listens without speaking" in speaking["prompt"]
    assert speaking["prompt"].endswith("No subtitles, no captions, no on-screen text.")
    silent = next(item for item in brief["shots"] if not item["speaks"])
    assert silent["prompt"].count(prompting.AUDIO_CLOSING) == 1
    assert tas._ledger(store, story_id) == []


# ================================================================ the dashboard

def test_the_wizard_and_the_card_offer_the_select_and_send_nothing_for_the_default():
    wizard = (STORY_SRC / "NewStoryWizard.jsx").read_text(encoding="utf-8")
    card = (STORY_SRC / "GenerationProfileCard.jsx").read_text(encoding="utf-8")
    for source in (wizard, card):
        assert "studio (default)" in source and "one continuous action (Flow / Seedance style)" in source
        assert "Clip prompts" in source
    assert 'id="new-story-prompt-style"' in wizard and 'id="story-profile-prompt-style"' in card
    # the wizard's default sends no key; the card clears it with null
    assert "promptStyle !== 'studio' ? { prompt_style: promptStyle }" in wizard
    assert "prompt_style: value === 'studio' ? null : value" in card
    # the card shows the server's warning as a toast
    assert "saved.warning" in card and "toast.info(" in card
    assert wizard.count("PROMPT_STYLES") >= 2 and card.count("PROMPT_STYLES") >= 2


# ================================================================ a human cast is named (plan 25 stage 0, D-5)

def _human_speech():
    """``(ec, shot, script)`` of one speaking shot of a human cast shaped like e7412a3efcc6's: Marie-Jeanne
    speaks to Rida, both in frame (their stored ``video_action`` resolved with the names)."""
    import types

    import test_story_shots as tss

    cast = {cid: dict(tss.HUMANS[cid], voice_hints=dict(nsc.HINTS)) for cid in ("char_marie_jeanne", "char_rida")}
    place = {"place_id": "place_bullpen", "name": "Bullpen", "descriptor": "A glass-walled open-plan bullpen",
             "look": {"lighting": {"day": "cold office light"}}}
    script = {"scenes": [{"scene_id": "s01", "place_id": "place_bullpen", "time_variant": "day", "emotion": "tension",
                          "function": "setup", "sfx_cues": [],
                          "lines": [{"line_id": "l1", "speaker": "char_marie_jeanne", "text": "Tu as signé ?",
                                     "emotion": "angry"}]}]}
    shot = {"shot_id": "sh01", "scene_id": "s01", "lines": ["l1"], "speaks": True, "camera_motion": "push_in",
            "subject_tags": ["@char_marie_jeanne", "@char_rida", "#place_bullpen:day"],
            "action": "@char_marie_jeanne grips the pen tightly while speaking",
            "clip_motion": "@char_marie_jeanne lifts the pen and speaks; @char_rida turns his head sharply",
            "video_action": "Marie-Jeanne grips the pen tightly while speaking"}
    ec = types.SimpleNamespace(entities={"characters": cast, "places": {"place_bullpen": place}, "props": {}},
                               language="fr", style_lock=defaults_style(), story={})
    return ec, shot, script


def defaults_style():
    from clipping.aistory import templates

    return templates.load_style("fruit_drama")


def test_a_human_cast_s_speech_prompts_name_the_speaker_and_the_listener():
    from clipping.aistory.steps import clips

    ec, shot, script = _human_speech()
    action = prompting.speech_clip_prompt_action(**clips.action_inputs(ec, shot, script))
    studio = prompting.speech_clip_prompt(ec.style_lock, **clips.speech_prompt_inputs(ec, shot, script))
    for prompt in (action, studio):
        assert "looks at Rida and says in French" in prompt
        assert "Rida listens without speaking" in prompt
        assert "Audio: only Marie-Jeanne's voice" in prompt
        assert "leather loafers" not in prompt and "the and says" not in prompt and "at the and" not in prompt
        # the build dump is never said after the name
        assert "Athletic" not in prompt and "toned frame" not in prompt
    # the action style: the speaker's anchor once, then the name; never the colour/species words
    assert action.count("Marie-Jeanne, a woman in her thirties in a charcoal blazer") == 1
    assert "female" not in action and "character in" not in action
    assert "Rida, a man in late twenties in a torn black hoodie turns his head sharply" in action
    # studio: the name, then the short look, then the action without the name again
    assert ("Slow push-in toward the subject. Marie-Jeanne, a woman in her thirties in a charcoal blazer, grips the "
            "pen tightly while speaking, looks at Rida") in studio


def test_a_species_cast_s_anchor_is_unchanged_beside_a_named_human():
    import test_story_shots as tss

    strawberry = dict(STRAWBERRY, name="Fraisette")
    cast = {"char_fraise": strawberry, "char_sam": tss.HUMANS["char_sam"]}
    anchors = shots.character_anchors(cast)
    assert anchors["char_fraise"] == "the green-yellow female plump strawberry character in a dirty burlap dress"
    assert anchors["char_sam"] == "Sam, a man in late twenties in an oversized charcoal hoodie"
