"""F7 (the phase-3 live Tier-2 walk): a storyboard's shots are cut to the
EPISODE-LEVEL scene durations -- the ones the script's ``timing`` is stored
with, after the episode window pass -- never to a scene's own
``timing.scene_timing``.

Live evidence (story ``b1104ec66b05``, episode 1, script rev 4, 60 s
template, under the window): s01, s04 and s10 were held 1.0 s longer in
``script.timing`` than their shots summed, on the fast and the T1 path, and
s01's fast build logged ``extra_hold_s=0.61`` though its episode-level
length would have fitted its three shots. Phase 4 renders the audio against
the script's timing and the video against the shots: every such gap is a
desync.

The invariant checked here, for every scene with shots (a stale scene keeps
its durations until it is planned again, by contract)::

    sum(shot["duration_s"] for its shots) == script["timing"]["scenes"][sid]["duration_s"]

after a fast build, a T1 build (whole, partial and failed), a single-scene
re-plan, a one-shot re-plan, a voice measurement, a transition edit, and in
every window state (ok / under with holds extended / tightened / over).

Two levels: the step runners on a real ``StoryStore`` (the fixtures and the
fake LLM of ``tests/test_story_episode_steps.py``, the fake Edge TTS of
``tests/test_story_measure.py``), and ``shots``/``episode_common.retime``
directly on hand-built and generated scripts (``tests/test_story_shots.py``'s
cast, places and prop). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import functools
import random
from types import SimpleNamespace

import pytest

import test_story_episode_steps as eps
import test_story_measure as measure
import test_story_shots as sh
from clipping.aistory import schemas, shots, timing
from test_story_episode_steps import store  # noqa: F401 -- a StoryStore under tmp_path
from test_story_measure import hermetic  # noqa: F401 -- autouse: no key, chain, cap, TTS or network

EN = sh.EN
NOW = sh.NOW
TEMPLATE = sh.TEMPLATE
MIN_SHOT = TEMPLATE["min_shot_s"]
STATES = ("ok", "under", "tightened", "over")


# ------------------------------------------------------------------ helpers

def _sums(board) -> dict:
    sums: dict = {}
    for shot in board["shots"]:
        sums[shot["scene_id"]] = sums.get(shot["scene_id"], 0.0) + shot["duration_s"]
    return sums


def _counts(board) -> dict:
    counts: dict = {}
    for shot in board["shots"]:
        counts[shot["scene_id"]] = counts.get(shot["scene_id"], 0) + 1
    return counts


def _mismatches(board, script, *, skip=()) -> list:
    """``[(scene_id, shots' sum, the script's duration), ...]`` for every
    scene whose shots do not sum to the script's episode-level duration."""
    scene_timings = script["timing"]["scenes"]
    return [(sid, round(total, 3), scene_timings[sid]["duration_s"]) for sid, total in _sums(board).items()
            if sid not in skip and abs(total - scene_timings[sid]["duration_s"]) > 1e-3]


def _assert_shots_follow_the_script(board, script, *, skip=()):
    """Every scene's shots sum to the script's episode-level duration of it,
    and no shot is under the template's minimum."""
    assert _sums(board), "the storyboard has no shots"
    assert _mismatches(board, script, skip=skip) == [], "(scene, shots' sum, the script's duration)"
    assert all(shot["duration_s"] >= MIN_SHOT - 1e-9 for shot in board["shots"])


def _no_extra_hold(notes):
    assert [note for note in notes if "extra_hold_s" in note] == []


def _ec(style_lock):
    """What ``episode_common.retime`` reads of an ``EpisodeContext``."""
    return SimpleNamespace(template=TEMPLATE, language=EN, style_lock=style_lock)


def _retime(script, style_lock, board):
    from clipping.aistory.steps import episode_common

    return episode_common.retime(script, _ec(style_lock), board)


def _build(script, style_lock, plans=None, sources=None):
    if plans is None:
        plans, sources = sh._fast_plans_for_script(script, style_lock)
    board, notes = shots.build_storyboard(script, plans, sources, entities=sh.ENTITIES, style_lock=style_lock,
                                          template=TEMPLATE, language=EN, consistency_mode="references", now=NOW)
    _retime(script, style_lock, board)
    return board, notes


def _retime_both(board, script, style_lock) -> bool:
    """What the voice measurement does after each line: the script re-timed
    and written, and the storyboard's durations following it."""
    _retime(script, style_lock, board)
    return shots.retime_storyboard(board, script, template=TEMPLATE, language=EN, style_lock=style_lock)


def _measured(line_id, speaker, text, seconds, *, emotion="neutral"):
    line = sh._line(line_id, speaker, text, emotion=emotion)
    line["timing"] = {"source": "tts_word_timestamps", "duration_s": seconds, "text_hash": timing.text_hash(text),
                      "voice": "en-US-TestNeural", "audio": None}
    return line


def _script(scenes, *, cut_to_black=False):
    script = copy.deepcopy(sh.SCRIPT)
    script["scenes"] = scenes
    script["cliffhanger"] = dict(script["cliffhanger"], scene_id=scenes[-1]["scene_id"], cut_to_black=cut_to_black)
    return script


def _own_length(script, sid, style_lock):
    """The scene's own (pre-F7) ``scene_timing`` length: no window pass."""
    scene = next(scene for scene in script["scenes"] if scene["scene_id"] == sid)
    return timing.scene_timing(scene, TEMPLATE, EN, style_lock=style_lock)["duration_s"]


# A one-place, hard-stop episode: a hook, *n_body* body scenes of one line
# each lasting *body_s*, a cliffhanger. Its window state follows from body_s.
def _one_place_script(*, n_body, body_s, hook_text="A shocking secret is about to come out.", hook_s=2.5,
                      cliff_s=3.4):
    k, m, b = sh.CHAR_KIWILO, sh.CHAR_MANGELLA, sh.CHAR_BROCCOLIA
    place = sh.PLACE_PARLOIR
    scenes = [sh._scene("s01", "hook", place_id=place, characters=[k, m], props=[sh.PROP_PHONE],
                        lines=[_measured("l01", k, hook_text, hook_s, emotion="shocked")], emotion="shocked")]
    for i in range(2, 2 + n_body):
        speaker = (k, m, b)[i % 3]
        scenes.append(sh._scene(f"s{i:02d}", "setup", place_id=place, characters=[speaker],
                                lines=[_measured(f"l{i:02d}", speaker, "Nobody trusts anyone on this island.",
                                                 body_s)]))
    last = 2 + n_body
    scenes.append(sh._scene(f"s{last:02d}", "cliffhanger", place_id=place, characters=[k, m, b],
                            lines=[_measured(f"l{last:02d}", m, "It was you all along.", cliff_s, emotion="shocked")],
                            emotion="shocked"))
    return _script(scenes)


# ============================================================ 1. the step runners (real store)

def test_a_fast_build_under_the_window_cuts_every_held_scene_to_its_episode_length(store):
    """The live F7 shape: the fixture episode is under 55 s, so the window
    pass holds the cliffhanger and the first scene at each place longer."""
    m = eps._new()
    story_id = eps._written_script(store)
    log = eps.Log()

    board = m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=log)

    script = eps._script(store, story_id)
    assert board == eps._storyboard(store, story_id)
    assert script["timing"]["state"] == "under"
    lock = store.read_doc(story_id, "style_lock.json")
    ec = m.common.load_context(store, story_id, 1)
    held = [scene["scene_id"] for scene in script["scenes"]
            if script["timing"]["scenes"][scene["scene_id"]]["duration_s"] > timing.scene_timing(
                scene, ec.template, "fr", style_lock=lock)["duration_s"] + 1e-6]
    assert "s10" in held and len(held) >= 2  # the cliffhanger, then the first scene at a place
    _assert_shots_follow_the_script(board, script)
    assert not any("extra_hold_s" in line for line in log)


def test_a_t1_build_cuts_every_scene_to_its_episode_length_after_every_write(store):
    m = eps._new()
    story_id = eps._written_script(store)
    seen = []

    def t1_then_check(call):
        # Each T1 call follows the write of the scenes accepted before it.
        board = eps._storyboard(store, story_id)
        if board is not None:
            _assert_shots_follow_the_script(board, eps._script(store, story_id))
            seen.append(len(board["scenes"]))
        return eps.t1_reply(call)

    eps._run(m.storyboard, store, story_id, llm=eps.FakeLLM(default={"T1": t1_then_check}), step="storyboard")

    board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
    assert {entry["source"] for entry in board["scenes"].values()} == {"t1"}
    assert seen == list(range(1, len(eps.ALL_SCENES)))  # partial storyboards, one more scene each time
    _assert_shots_follow_the_script(board, script)


def test_a_failed_t1_leaves_a_partial_storyboard_that_still_follows_the_script(store):
    m = eps._new()
    story_id = eps._written_script(store)
    queue = [eps.t1_reply, eps.t1_reply, eps.ProviderError("down", [("gemini/gemini-test", "HTTP 500")])]
    queue += [eps.t1_reply] * 7

    eps._failed(m.storyboard, store, story_id, llm=eps.FakeLLM(T1=queue), step="storyboard")

    board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
    assert "s03" not in board["scenes"]
    _assert_shots_follow_the_script(board, script)


def test_replanning_one_scene_or_one_shot_keeps_every_scene_at_its_episode_length(store):
    m = eps._new()
    story_id = eps._written_script(store)
    eps._run(m.storyboard, store, story_id, llm=eps.FakeLLM(default={"T1": eps.t1_reply}), step="storyboard")

    # s03 rewritten (shorter lines): stale until it is planned again...
    llm = eps.FakeLLM(E2=[functools.partial(eps.e2_reply, text="Je sais tout.")])
    eps._regenerate(store, story_id, "scene:1:s03", llm=llm)
    board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
    assert board["scenes"]["s03"]["stale"] is True

    # ...then the T1 run plans that one scene again: every scene follows.
    t1 = eps.FakeLLM(T1=[eps.t1_reply])
    eps._run(m.storyboard, store, story_id, llm=t1, step="storyboard")
    assert t1.prompts() == ["T1"]
    board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
    assert not any(entry["stale"] for entry in board["scenes"].values())
    _assert_shots_follow_the_script(board, script)

    # One shot planned again (T1r): the fifth, s03's first -- a new id since
    # s03 was planned again (walk follow-up F5), so it is named by position.
    fifth = board["shots"][4]["shot_id"]
    eps._regenerate(store, story_id, f"shot:1:{fifth}:plan", llm=eps.FakeLLM(T1r=[eps.t1r_reply]))
    _assert_shots_follow_the_script(eps._storyboard(store, story_id), eps._script(store, story_id))


def test_measuring_the_voices_keeps_every_scene_at_its_episode_length_after_every_line(store):
    """The script is re-timed and written after every measured line, and the
    storyboard follows it (``sync_storyboard``): checked on disk before each
    synthesis -- the episode crosses from under the window to inside it on
    the way."""
    m = eps._new()
    story_id = eps._written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    before = eps._storyboard(store, story_id)
    seen = []  # (window state, mismatches) on disk before each synthesis

    def check(_edge):
        board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
        seen.append((script["timing"]["state"], _mismatches(board, script)))

    measure._measure(store, story_id, adapters=measure._adapters(measure.Edge(on_call=check)))

    board, script = eps._storyboard(store, story_id), eps._script(store, story_id)
    assert script["timing"]["measured_lines"] > 0 and script["timing"]["estimated_lines"] == 0
    assert [shot["duration_s"] for shot in board["shots"]] != [shot["duration_s"] for shot in before["shots"]]
    assert len(seen) == script["timing"]["measured_lines"]
    assert "under" in {state for state, _ in seen}
    assert [mismatches for _, mismatches in seen if mismatches] == []
    _assert_shots_follow_the_script(board, script)


# ============================================================ 2. every window state

def test_under_the_window_the_held_scenes_shots_take_the_hold_and_retime_keeps_it():
    style = sh.FRUIT_DRAMA
    script = copy.deepcopy(sh.SCRIPT)

    board, notes = _build(script, style)

    assert script["timing"]["state"] in ("under", "ok")
    held = [sid for sid, scene_t in script["timing"]["scenes"].items()
            if scene_t["duration_s"] > _own_length(script, sid, style) + 1e-6]
    assert "s08" in held  # the cliffhanger is held first
    _no_extra_hold(notes)
    _assert_shots_follow_the_script(board, script)

    # A measurement shortens every line: the window pass moves, the shots follow.
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line["timing"] = dict(line["timing"], source="tts_word_timestamps",
                                  duration_s=round(line["timing"]["duration_s"] * 0.6, 3))
    assert _retime_both(board, script, style) is True
    _assert_shots_follow_the_script(board, script)
    assert _retime_both(board, script, style) is False


def test_a_tightened_episode_cuts_the_shots_to_the_shortened_tails():
    style = sh.FRUIT_DRAMA
    script = _one_place_script(n_body=9, body_s=6.95, cliff_s=3.4)

    board, notes = _build(script, style)

    assert script["timing"]["state"] == "tightened"
    cliff = script["scenes"][-1]["scene_id"]
    assert script["timing"]["scenes"][cliff]["duration_s"] < _own_length(script, cliff, style) - 1e-6
    _no_extra_hold(notes)
    _assert_shots_follow_the_script(board, script)


def test_an_episode_over_the_window_cuts_the_shots_to_the_tightened_scenes():
    style = sh.FRUIT_DRAMA
    script = _one_place_script(n_body=9, body_s=9.0)

    board, notes = _build(script, style)

    assert script["timing"]["state"] == "over"
    assert any(scene_t["state"] == "over" for scene_t in script["timing"]["scenes"].values())
    tightened = [sid for sid, scene_t in script["timing"]["scenes"].items()
                 if scene_t["duration_s"] < _own_length(script, sid, style) - 1e-6]
    assert tightened
    _no_extra_hold(notes)
    _assert_shots_follow_the_script(board, script)


# ============================================================ 3. the shot floor

def test_a_scene_too_short_for_its_shots_alone_fits_once_the_window_pass_holds_it():
    """The live s01: on its own the hook lasts less than its three fast shots
    need (3 x 0.8 s), but the window pass holds it long enough -- so no
    extra_hold, the shots sum to its episode-level length, and building the
    storyboard does not move that length."""
    style = sh.FRUIT_DRAMA
    script = copy.deepcopy(sh.SCRIPT)
    hook = script["scenes"][0]
    hook["lines"] = [_measured("l04", sh.CHAR_KIWILO, "Run.", 0.84, emotion="shocked")]
    alone = _own_length(script, "s01", style)
    without_board = timing.episode_timing(script, TEMPLATE, EN, style_lock=style)

    board, notes = _build(script, style)

    n = _counts(board)["s01"]
    assert n == 3 and alone < n * MIN_SHOT  # 1.79 s alone, 2.4 s needed
    assert without_board["scenes"]["s01"]["duration_s"] >= n * MIN_SHOT
    assert script["timing"]["scenes"]["s01"]["duration_s"] == without_board["scenes"]["s01"]["duration_s"]
    assert script["timing"]["total_s"] == without_board["total_s"]
    _no_extra_hold(notes)
    _assert_shots_follow_the_script(board, script)


def test_a_scene_the_window_pass_does_not_hold_is_held_to_its_shots_floor_in_the_script_too():
    """Inside the window nothing is held, and a 1.5 s hook cannot show three
    0.8 s shots: the script's own timing holds it to 2.4 s (not the
    storyboard alone), so audio and video still agree."""
    style = sh.FRUIT_DRAMA
    script = _one_place_script(n_body=8, body_s=6.0, hook_text="Run.", hook_s=0.5)
    assert timing.episode_timing(script, TEMPLATE, EN, style_lock=style)["state"] == "ok"

    board, notes = _build(script, style)

    n = _counts(board)["s01"]
    assert _own_length(script, "s01", style) < n * MIN_SHOT
    assert script["timing"]["scenes"]["s01"]["duration_s"] == pytest.approx(n * MIN_SHOT)
    assert script["timing"]["scenes"]["s01"]["hold_s"] > 0
    _no_extra_hold(notes)
    _assert_shots_follow_the_script(board, script)


# ============================================================ 4. edits and stale scenes

def test_a_transition_edit_moves_the_shots_with_the_scripts_timing():
    style = sh.FRUIT_DRAMA
    script = copy.deepcopy(sh.SCRIPT)
    board, _notes = _build(script, style)
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    order = [shot["shot_id"] for shot in board["shots"]]
    between = [t for t in board["transitions"]
               if by_id[t["after"]]["scene_id"] != by_id[order[order.index(t["after"]) + 1]]["scene_id"]]

    for transition in between:
        transition.update(type="cut", duration_s=TEMPLATE["transitions_s"]["cut"])
    _retime_both(board, script, style)

    _assert_shots_follow_the_script(board, script)


def test_a_stale_scene_keeps_its_durations_while_every_other_scene_follows():
    style = sh.FRUIT_DRAMA
    script = copy.deepcopy(sh.SCRIPT)
    board, _notes = _build(script, style)
    before = copy.deepcopy(board)

    # s03 rewritten (a new revision: stale), and every other line measured
    # shorter, so the window pass moves the other scenes too.
    s03 = script["scenes"][2]
    s03["rev"] += 1
    s03["lines"] = s03["lines"][:1]
    board["scenes"]["s03"]["stale"] = True
    for scene in script["scenes"]:
        for line in scene["lines"]:
            line["timing"] = dict(line["timing"], source="tts_word_timestamps",
                                  duration_s=round(line["timing"]["duration_s"] * 0.7, 3))

    assert _retime_both(board, script, style) is True

    kept = [shot["duration_s"] for shot in board["shots"] if shot["scene_id"] == "s03"]
    assert kept == [shot["duration_s"] for shot in before["shots"] if shot["scene_id"] == "s03"]
    _assert_shots_follow_the_script(board, script, skip={"s03"})
    assert board["scenes"] == dict(before["scenes"], s03=dict(before["scenes"]["s03"], stale=True))


# ============================================================ 5. property: generated scripts

_CAST = (sh.CHAR_KIWILO, sh.CHAR_MANGELLA, sh.CHAR_BROCCOLIA)
_PLACES = (sh.PLACE_PARLOIR, sh.PLACE_PISCINE)
_WORDS = "the phone rings again tonight nobody trusts anyone here secret vote island".split()
_EMOTIONS = ("neutral", "shocked", "tension", "angry", "scheming", "sad")
_BODY = ("setup", "rising", "peak", "turn")
_FRAMINGS = ("close_up", "medium_single", "medium_two_shot", "over_shoulder", "wide_establishing", "high_angle")


def _generated_script(rng):
    scale = rng.choice((0.5, 1.0, 1.6, 2.2, 3.0))
    functions = ["hook"] + [rng.choice(_BODY) for _ in range(rng.randint(5, 9))] + ["cliffhanger"]
    scenes, n_line = [], 0
    for i, function in enumerate(functions, start=1):
        chars = rng.sample(_CAST, rng.randint(1, 3))
        props = [sh.PROP_PHONE] if function == "hook" and rng.random() < 0.7 else []
        lines = []
        for _ in range(rng.choice((0, 1, 1, 2, 3))):
            n_line += 1
            text = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 9))).capitalize() + "."
            lines.append(_measured(f"l{n_line:02d}", rng.choice(chars), text, round(rng.uniform(0.4, 3.0) * scale, 3),
                                   emotion=rng.choice(_EMOTIONS)))
        scenes.append(sh._scene(f"s{i:02d}", function, place_id=rng.choice(_PLACES), characters=chars, props=props,
                                lines=lines, emotion=rng.choice(_EMOTIONS),
                                target_duration_s=round(rng.uniform(1.0, 9.0), 1)))
    return _script(scenes, cut_to_black=rng.random() < 0.5)


def _t1_shaped_plans(rng, scene):
    """A T1 reply's shape: 2 to 4 shots, the scene's lines split across them
    in order (some shots with none)."""
    n = rng.randint(2, 4)
    who = f"@{scene['characters'][0]}"
    plans = [{"framing": rng.choice(_FRAMINGS), "camera_motion": "hold", "modifiers": [],
              "action": f"{who} looks on.", "subjects": [who], "lines": []} for _ in range(n)]
    for number, index in zip(range(1, len(scene["lines"]) + 1), sorted(rng.randrange(n) for _ in scene["lines"])):
        plans[index]["lines"].append(number)
    return plans


def _covers(board, script) -> bool:
    sequence = []
    for shot in board["shots"]:
        if not sequence or sequence[-1] != shot["scene_id"]:
            sequence.append(shot["scene_id"])
    return sequence == [scene["scene_id"] for scene in script["scenes"]]


def _check_video_timeline(board, script):
    """With a storyboard covering every scene, its video runs exactly as long
    as the script's timing says the episode does."""
    if not _covers(board, script):
        return
    end_card = 0.0
    if script["cliffhanger"]["cut_to_black"]:
        end_card = TEMPLATE["end_card_s"] - TEMPLATE["transitions_s"]["fadeblack"]
    video = sum(shot["duration_s"] for shot in board["shots"]) \
        - sum(t["duration_s"] for t in board["transitions"]) + end_card
    assert video == pytest.approx(script["timing"]["total_s"], abs=1e-3 * len(board["shots"]))


def test_generated_scripts_shots_always_sum_to_the_scripts_scene_durations():
    rng = random.Random(20260927)
    states = set()
    for _ in range(120):
        style = rng.choice((sh.FRUIT_DRAMA, sh.FAMILY_3D))
        script = _generated_script(rng)
        plans, sources = sh._fast_plans_for_script(script, style)
        for scene in script["scenes"]:
            if rng.random() < 0.4:
                plans[scene["scene_id"]], sources[scene["scene_id"]] = _t1_shaped_plans(rng, scene), "t1"
        if rng.random() < 0.2:  # a partial storyboard (a T1 run part-way, or a failed scene)
            dropped = rng.choice(script["scenes"][1:])["scene_id"]
            del plans[dropped], sources[dropped]

        board, notes = _build(script, style, plans, sources)
        states.add(script["timing"]["state"])
        _no_extra_hold(notes)
        _assert_shots_follow_the_script(board, script)
        assert schemas.storyboard_errors(board, min_shot_s=MIN_SHOT) == []
        for sid, n in _counts(board).items():
            assert script["timing"]["scenes"][sid]["duration_s"] >= n * MIN_SHOT - 1e-9
        _check_video_timeline(board, script)

        # A transition edit.
        if _covers(board, script) and len(board["transitions"]) > 0:
            by_id = {shot["shot_id"]: shot for shot in board["shots"]}
            order = [shot["shot_id"] for shot in board["shots"]]
            between = [t for t in board["transitions"]
                       if by_id[t["after"]]["scene_id"] != by_id[order[order.index(t["after"]) + 1]]["scene_id"]]
            if between:
                kind = rng.choice(sorted(TEMPLATE["transitions_s"]))
                rng.choice(between).update(type=kind, duration_s=TEMPLATE["transitions_s"][kind])
                _retime_both(board, script, style)
                _assert_shots_follow_the_script(board, script)
                _check_video_timeline(board, script)

        # A voice measurement: every line gets another length.
        for scene in script["scenes"]:
            for line in scene["lines"]:
                line["timing"] = dict(line["timing"], duration_s=round(line["timing"]["duration_s"]
                                                                       * rng.uniform(0.6, 1.5), 3))
        _retime_both(board, script, style)
        states.add(script["timing"]["state"])
        _assert_shots_follow_the_script(board, script)
        _check_video_timeline(board, script)

    assert states == set(STATES), f"the generated scripts reach only {sorted(states)}"
