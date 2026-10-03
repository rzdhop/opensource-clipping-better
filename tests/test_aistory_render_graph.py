"""Tests for the AI-Story renderer's sequence and audio graph (AI Story phase 4
stage 6; plan phase 4 stage 6, "Renderer" -> filtergraph.py's Audio mix /
Final pass bullets; spec 6.3, 6.5; DEC-157, DEC-158).

``filtergraph.sequence_plan``/``xfade_offsets`` (the frame arithmetic),
``filtergraph.final_pass_argv`` and ``filtergraph.audio_mix_argv`` are pure
argv builders: every graph here is a golden string or a property of one. No
ffmpeg is imported, run, or required (DEC-012); the golden render (stage 7)
proves the same topology on a real ffmpeg.

Timelines come from ``render.timeline.build_timeline`` itself, never
hand-typed: the stage-4 fixtures (``tests/test_aistory_render_timeline.py``:
FR fruit_drama ``hard_stop``, EN family_3d ``cut_to_black``) and small
three-scene scripts built with ``tests/test_story_timing.py``'s helpers
(:func:`_hand_built_timeline`), timed by ``timing.episode_pass``.

Sections, in order:

1. the DEC-157 constants and the transition -> xfade name map
2. xfade offset goldens: (a) all cuts, (b) a first-shot dissolve, (c) a mix
   of fadeblack and dissolve across scenes, (d) a card after fadeblack,
   (e) hard_stop; frame-derived starts vs the timeline
3. refusals of a timeline the sequence cannot render frame-exactly
4. no line inside a rendered transition window (graph level)
5. final_pass_argv goldens and properties
6. audio_mix_argv: the ducking golden, delays, the bed, tails, no BGM/SFX
7. no absolute path in any argv
"""

from __future__ import annotations

import copy
import re

import pytest

import test_aistory_render_timeline as rtt
import test_story_timing as tt
from clipping.aistory import schemas, timing
from clipping.aistory.render import filtergraph, profiles
from clipping.aistory.render import timeline as rt

TEMPLATE = rtt.TEMPLATE
EN = rtt.EN
STEMS = {"dialogue": "stems/dialogue.wav", "bgm": "stems/bgm.wav", "sfx": "stems/sfx.wav"}
# Every dialogue line, whatever its engine: 5 ms in at its file's start, 10 ms
# out at its file's real end (reversed, so not at a duration_s that is only
# the last word's end), so no line can click (phase 7 follow-up, a bug fix
# for every story).
LINE_FADES = "afade=t=in:d=0.005,areverse,afade=t=in:d=0.01,areverse"


# ================================================================ fixtures

def _hand_built_timeline(links, *, cut_to_black=False, split_first=False, sfx=False):
    """A three-scene EN script (one 2.0 s line per scene; s01/s02 share a
    place, s03 does not) and a covering board whose shot-to-shot
    transitions are *links* (``"cut"`` or a template transition name, one
    per boundary, in order). Shot durations are the scenes' own
    ``timing.episode_pass`` durations; *split_first* cuts s01 into two
    shots (1.5 s + the rest). *sfx* adds a cue at s01's start and one at
    l03. Returns ``build_timeline``'s timeline."""
    words = ["one two three", "four five six", "seven eight nine"]
    lines = [tt._timed_line(f"l0{i}", text, 2.0) for i, text in enumerate(words, 1)]
    scenes = [
        tt._scene("s01", "setup", place_id="place_a", lines=[lines[0]]),
        tt._scene("s02", "setup", place_id="place_a", lines=[lines[1]]),
        tt._scene("s03", "setup", place_id="place_b", lines=[lines[2]]),
    ]
    if sfx:
        scenes[0]["sfx_cues"] = [{"at": "start", "cue": "whoosh"}]
        scenes[2]["sfx_cues"] = [{"at": "l03", "cue": "sting"}]
    script = tt._episode_script(scenes, cut_to_black=cut_to_black)

    scene_of = ["s01", "s01", "s02", "s03"] if split_first else ["s01", "s02", "s03"]
    shots = [rtt._bare_shot(f"sh{i + 1:02d}", sid, 1.0, i + 1) for i, sid in enumerate(scene_of)]
    board = {"shots": shots, "transitions": [
        {"after": shots[i]["shot_id"], "type": kind, "duration_s": TEMPLATE["transitions_s"][kind]}
        for i, kind in enumerate(links) if kind != "cut"
    ]}
    real, _scene_t = timing.episode_pass(script, TEMPLATE, EN, style_lock=None, storyboard=board)
    for sid in ("s01", "s02", "s03"):
        own = [shot for shot in shots if shot["scene_id"] == sid]
        duration = real["scenes"][sid]["duration_s"]
        if len(own) == 2:
            own[0]["duration_s"], own[1]["duration_s"] = 1.5, round(duration - 1.5, 3)
        else:
            own[0]["duration_s"] = duration
    return rt.build_timeline(script, board, TEMPLATE, EN, style_lock=None)


def _all_cuts():
    return _hand_built_timeline(["cut", "cut", "cut"], split_first=True)


def _first_shot_dissolve():
    return _hand_built_timeline(["dissolve", "cut"])


def _small_cut_to_black():
    return _hand_built_timeline(["dissolve", "fadeblack"], cut_to_black=True, sfx=True)


def _fr_hard_stop():
    script, board = rtt._fr_fruit_drama_fixture()
    return rt.build_timeline(script, board, TEMPLATE, rtt.FR, style_lock=rtt.FRUIT_DRAMA)


def _en_cut_to_black():
    script, board = rtt._en_family_3d_fixture()
    return rt.build_timeline(script, board, TEMPLATE, EN, style_lock=rtt.FAMILY_3D)


TIMELINES = [
    pytest.param(_all_cuts, id="all_cuts"),
    pytest.param(_first_shot_dissolve, id="first_shot_dissolve"),
    pytest.param(_small_cut_to_black, id="small_cut_to_black"),
    pytest.param(_fr_hard_stop, id="fr_fruit_drama_hard_stop"),
    pytest.param(_en_cut_to_black, id="en_family_3d_cut_to_black"),
]


def _ending(tl):
    return "cut_to_black" if tl["end_card"] is not None else "hard_stop"


def _final(tl, profile=profiles.FINAL, **overrides):
    kwargs = dict(
        shot_inputs={shot["shot_id"]: f"cache/{shot['shot_id']}.mp4" for shot in tl["shots"]},
        end_card_input="cache/end_card.mp4" if tl["end_card"] is not None else None,
        ass_rel="subtitles.ass", fontsdir_rel="fonts", mix_rel="mix.wav", profile=profile,
        out_rel="episode_pre.mkv",
    )
    kwargs.update(overrides)
    return filtergraph.final_pass_argv(tl, **kwargs)


def _mix(tl, **overrides):
    kwargs = dict(
        line_inputs={line["line_id"]: f"in/{line['line_id']}.mp3" for line in tl["lines"]},
        sfx_inputs={anchor["cue"]: f"in/{anchor['cue']}.wav" for anchor in tl["sfx_anchors"]},
        bgm_input="in/bgm.mp3", ending=_ending(tl), out_rel="mix.wav", stems_rel=dict(STEMS),
    )
    kwargs.update(overrides)
    return filtergraph.audio_mix_argv(tl, **kwargs)


def _graph(argv):
    return argv[argv.index("-filter_complex") + 1]


def _join_rows(joins):
    return [(j["after"], j["into"], j["xfade"], j["offset_frames"], j["duration_frames"], j["offset"])
            for j in joins]


# ================================================ 1. constants, name map

def test_audio_constants_are_dec_157s_exact_values():
    assert profiles.MIX_WEIGHTS == (("dialogue", 1.0), ("bgm", 0.30), ("sfx", 0.8))
    assert (profiles.DUCK_THRESHOLD, profiles.DUCK_RATIO, profiles.DUCK_ATTACK_MS, profiles.DUCK_RELEASE_MS) == (
        0.03, 8, 20, 300)
    assert profiles.BED_FADE_OUT_S == {"cut_to_black": 0.5, "hard_stop": 0.05}
    assert profiles.ENDINGS == ("cut_to_black", "hard_stop")
    assert profiles.AUDIO_RATE == 48000 == profiles.FINAL.audio_rate
    assert (profiles.AUDIO_CHANNEL_LAYOUT, profiles.MIX_CODEC) == ("stereo", "pcm_f32le")


def test_transition_names_map_to_xfade_names():
    assert set(filtergraph.XFADE_TRANSITIONS) == set(schemas.TRANSITIONS) - {"cut"}
    assert filtergraph.XFADE_TRANSITIONS["dissolve"] == "fade"
    for name in ("fadeblack", "fadewhite", "wipeleft", "wiperight", "slideup"):
        assert filtergraph.XFADE_TRANSITIONS[name] == name


def test_every_template_transition_is_a_whole_number_of_frames():
    """The offset rule needs whole-frame transitions: the shipped template's
    own durations (0.4 s / 0.3 s) are 12 / 9 frames at 30 fps."""
    for kind, seconds in TEMPLATE["transitions_s"].items():
        frames = filtergraph._transition_frames({"type": kind, "duration_s": seconds}, after="x", fps=profiles.FPS)
        assert frames == round(seconds * profiles.FPS)


# ======================================================== 2. xfade offsets

def test_xfade_offsets_all_cuts_is_one_concat_and_no_xfade():
    tl = _all_cuts()
    plan = filtergraph.sequence_plan(tl)
    assert filtergraph.xfade_offsets(tl) == []
    assert plan["segments"] == [{"items": ["sh01", "sh02", "sh03", "sh04"], "frames": 420}]
    assert plan["start_frames"] == {"sh01": 0, "sh02": 45, "sh03": 150, "sh04": 270}
    assert plan["total_frames"] == tl["total_frames"] == 420


def test_xfade_offsets_first_shot_dissolve():
    tl = _first_shot_dissolve()
    assert _join_rows(filtergraph.xfade_offsets(tl)) == [("sh01", "sh02", "fade", 138, 12, "4.6")]
    plan = filtergraph.sequence_plan(tl)
    assert plan["segments"] == [{"items": ["sh01"], "frames": 150}, {"items": ["sh02", "sh03"], "frames": 270}]
    assert plan["start_frames"] == {"sh01": 0, "sh02": 138, "sh03": 258}
    assert plan["total_frames"] == 408 == tl["total_frames"]


def test_xfade_offsets_fadeblack_and_dissolve_across_scenes():
    """(c) the FR fixture: dissolves between same-place scenes, fadeblacks
    where the place changes; each offset is the accumulated frame count
    minus the transition's own 12 frames."""
    tl = _fr_hard_stop()
    assert _join_rows(filtergraph.xfade_offsets(tl)) == [
        ("sh03", "sh04", "fade", 93, 12, "3.1"),
        ("sh05", "sh06", "fadeblack", 201, 12, "6.7"),
        ("sh08", "sh09", "fade", 429, 12, "14.3"),
        ("sh10", "sh11", "fadeblack", 537, 12, "17.9"),
        ("sh12", "sh13", "fade", 645, 12, "21.5"),
        ("sh14", "sh15", "fade", 753, 12, "25.1"),
        ("sh16", "sh17", "fade", 861, 12, "28.7"),
    ]


def test_xfade_offsets_card_after_fadeblack():
    """(d) the EN fixture (``cut_to_black``): the last join is the end card,
    by fadeblack, and the card's own 30 frames end the file."""
    tl = _en_cut_to_black()
    joins = filtergraph.xfade_offsets(tl)
    assert _join_rows(joins) == [
        ("sh02", "sh03", "fade", 93, 12, "3.1"),
        ("sh04", "sh05", "fadeblack", 201, 12, "6.7"),
        ("sh07", "sh08", "fade", 429, 12, "14.3"),
        ("sh09", "sh10", "fadeblack", 537, 12, "17.9"),
        ("sh11", "sh12", "fade", 645, 12, "21.5"),
        ("sh13", "sh14", "fade", 753, 12, "25.1"),
        ("sh15", "sh16", "fade", 861, 12, "28.7"),
        ("sh17", "end_card", "fadeblack", 984, 12, "32.8"),
    ]
    plan = filtergraph.sequence_plan(tl)
    assert plan["segments"][-1] == {"items": ["end_card"], "frames": 30}
    assert joins[-1]["offset_frames"] + 30 == plan["total_frames"] == tl["total_frames"] == 1014
    assert plan["start_frames"]["end_card"] / profiles.FPS == pytest.approx(tl["end_card"]["start_s"], abs=1e-9)


def test_xfade_offsets_hard_stop_ends_on_the_last_shot():
    """(e) ``hard_stop``: no card, no join after the last shot; the last
    shot's own frames end the file."""
    tl = _fr_hard_stop()
    plan = filtergraph.sequence_plan(tl)
    assert tl["end_card"] is None
    assert all(join["into"] != filtergraph.END_CARD_ID for join in plan["joins"])
    last = tl["shots"][-1]
    assert plan["segments"][-1]["items"][-1] == last["shot_id"]
    assert plan["start_frames"][last["shot_id"]] + last["frames"] == plan["total_frames"] == tl["total_frames"]


def test_a_six_decimal_offset_rounds_back_to_its_exact_frame():
    """An offset of k/30 s that is not a whole number of hundredths is
    written with 6 decimals; ffmpeg rescales it to the stream's 1/30 time
    base rounding to the nearest, which lands on frame k exactly."""
    tl = {
        "total_s": 5.033, "fps": 30, "total_frames": 151, "lines": [], "sfx_anchors": [], "end_card": None,
        "shots": [
            {"shot_id": "sh01", "scene_id": "s01", "start_s": 0.0, "duration_s": 3.433, "frames": 103,
             "transition_after": {"type": "dissolve", "duration_s": 0.4}},
            {"shot_id": "sh02", "scene_id": "s02", "start_s": 3.033, "duration_s": 2.0, "frames": 60,
             "transition_after": None},
        ],
    }
    (join,) = filtergraph.xfade_offsets(tl)
    assert (join["offset_frames"], join["offset"]) == (91, "3.033333")
    assert round(float(join["offset"]) * 30) == 91
    assert "xfade=transition=fade:duration=0.4:offset=3.033333" in _graph(_final(tl))


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_frame_derived_starts_are_within_half_a_frame_of_the_timeline(builder):
    """The offsets come from the shot clips' integer frame counts, never
    from float seconds -- and still land within half a frame of every
    shot's (and the card's) own timeline ``start_s``, however many joins
    precede it."""
    tl = builder()
    plan = filtergraph.sequence_plan(tl)
    expected = {shot["shot_id"]: shot["start_s"] for shot in tl["shots"]}
    if tl["end_card"] is not None:
        expected[filtergraph.END_CARD_ID] = tl["end_card"]["start_s"]
    assert set(plan["start_frames"]) == set(expected)
    for item_id, start_s in expected.items():
        assert abs(plan["start_frames"][item_id] / profiles.FPS - start_s) <= 0.5 / profiles.FPS + 1e-9, item_id


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_sequence_frames_equal_the_timeline_total_frames(builder):
    tl = builder()
    plan = filtergraph.sequence_plan(tl)
    assert plan["total_frames"] == tl["total_frames"] == round(tl["total_s"] * profiles.FPS)
    # each join: the accumulated stream so far minus the transition, and the
    # joined stream's length is offset + the incoming segment
    accumulated = plan["segments"][0]["frames"]
    for join, segment in zip(plan["joins"], plan["segments"][1:]):
        assert join["offset_frames"] == accumulated - join["duration_frames"]
        accumulated = join["offset_frames"] + segment["frames"]
    assert accumulated == plan["total_frames"]


# ================================================================ 3. refusals

def test_a_transition_that_is_not_a_whole_number_of_frames_is_refused():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["shots"][0]["transition_after"]["duration_s"] = 0.35  # 10.5 frames
    with pytest.raises(filtergraph.GraphError, match="whole number of frames"):
        filtergraph.xfade_offsets(tl)


def test_a_cut_that_lasts_is_refused():
    tl = copy.deepcopy(_all_cuts())
    tl["shots"][0]["transition_after"]["duration_s"] = 0.4
    with pytest.raises(filtergraph.GraphError, match="a cut must last 0s"):
        filtergraph.sequence_plan(tl)


def test_a_shot_frame_count_that_disagrees_with_the_timeline_is_refused():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["shots"][1]["frames"] += 1
    with pytest.raises(filtergraph.GraphError, match="409 frames long but the timeline says 408"):
        filtergraph.sequence_plan(tl)


def test_a_transition_longer_than_a_shot_it_joins_is_refused():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["shots"][0]["frames"] = 10
    with pytest.raises(filtergraph.GraphError, match="longer than a shot it joins"):
        filtergraph.sequence_plan(tl)


def test_the_last_shots_transition_must_match_the_ending():
    hard = copy.deepcopy(_first_shot_dissolve())
    hard["shots"][-1]["transition_after"] = {"type": "fadeblack", "duration_s": 0.4}
    with pytest.raises(filtergraph.GraphError, match="hard_stop"):
        filtergraph.sequence_plan(hard)

    card = copy.deepcopy(_small_cut_to_black())
    card["shots"][-1]["transition_after"] = {"type": "dissolve", "duration_s": 0.4}
    with pytest.raises(filtergraph.GraphError, match="cut_to_black"):
        filtergraph.sequence_plan(card)


def test_an_inner_shot_without_a_transition_is_refused():
    tl = copy.deepcopy(_all_cuts())
    tl["shots"][1]["transition_after"] = None
    with pytest.raises(filtergraph.GraphError, match="not the last shot"):
        filtergraph.sequence_plan(tl)


def test_a_timeline_at_another_fps_is_refused():
    tl = copy.deepcopy(_all_cuts())
    tl["fps"] = 25
    with pytest.raises(filtergraph.GraphError, match="fps"):
        filtergraph.sequence_plan(tl)


# ============================================ 4. lines vs rendered windows

@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_no_line_inside_a_rendered_transition_window(builder):
    """The stage-4 invariant holds on the timeline (reused as is) AND on the
    windows the graph renders (``[offset, offset + duration]`` in frames),
    checked against the outgoing scene's lines with half a frame of slack
    for the frame quantisation -- and ``final_pass_argv`` agrees."""
    tl = builder()
    rt._assert_no_line_in_a_transition_window(tl["shots"], tl["lines"])  # the stage-4 check, unchanged

    slack = 0.5 / profiles.FPS + 1e-6
    for join in filtergraph.xfade_offsets(tl):
        win_start = join["offset_frames"] / profiles.FPS
        win_end = (join["offset_frames"] + join["duration_frames"]) / profiles.FPS
        for line in tl["lines"]:
            if line["scene_id"] != join["outgoing_scene_id"]:
                continue
            line_end = line["start_s"] + line["duration_s"]
            assert line_end <= win_start + slack or line["start_s"] >= win_end - slack, (line, join)
    _final(tl)  # must not raise


def test_a_line_inside_a_rendered_window_is_refused_at_the_graph_level():
    """A timeline whose outgoing scene's line runs into the dissolve the
    graph renders (frames 138-150, 4.6-5.0 s) never reaches ffmpeg."""
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["lines"][0]["start_s"] = 3.0  # l01 (s01) now runs 3.0-5.0 s
    with pytest.raises(filtergraph.GraphError, match="l01.*inside the rendered dissolve window after 'sh01'"):
        _final(tl)


def test_a_line_ending_exactly_at_the_window_start_is_not_a_violation():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["lines"][0]["start_s"] = 2.6  # l01 now ends at 4.6 s, where the dissolve starts
    _final(tl)  # must not raise


def test_the_incoming_scenes_first_line_may_start_inside_the_window():
    """Same scoping as stage 4: only the OUTGOING scene's lines are checked
    (an incoming scene's 0.35 s pre-roll is shorter than a 0.4 s fade)."""
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["lines"][1]["start_s"] = 4.7  # l02 (s02, incoming) starts inside 4.6-5.0 s
    _final(tl)  # must not raise


# ======================================================== 5. final pass

def test_final_pass_argv_golden_first_shot_dissolve_hard_stop():
    assert _final(_first_shot_dissolve()) == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", "cache/sh01.mp4", "-i", "cache/sh02.mp4", "-i", "cache/sh03.mp4", "-i", "mix.wav",
        "-filter_complex",
        "[0:v]settb=AVTB,fps=30,format=yuv420p[v0];"
        "[1:v]settb=AVTB,fps=30,format=yuv420p[v1];"
        "[2:v]settb=AVTB,fps=30,format=yuv420p[v2];"
        "[v1][v2]concat=n=2:v=1:a=0,settb=1/30[seg1];"
        "[v0][seg1]xfade=transition=fade:duration=0.4:offset=4.6[x1];"
        "[x1]ass=subtitles.ass:fontsdir=fonts[vout]",
        "-map", "[vout]", "-map", "3:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "cfr",
        "-r", "30", "-c:a", "pcm_f32le", "episode_pre.mkv",
    ]


def test_final_pass_argv_golden_cut_to_black_golden_profile():
    assert _final(_small_cut_to_black(), profile=profiles.GOLDEN) == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-fflags", "+bitexact",
        "-i", "cache/sh01.mp4", "-i", "cache/sh02.mp4", "-i", "cache/sh03.mp4", "-i", "cache/end_card.mp4",
        "-i", "mix.wav",
        "-filter_complex",
        "[0:v]settb=AVTB,fps=30,format=yuv420p[v0];"
        "[1:v]settb=AVTB,fps=30,format=yuv420p[v1];"
        "[2:v]settb=AVTB,fps=30,format=yuv420p[v2];"
        "[3:v]settb=AVTB,fps=30,format=yuv420p[v3];"
        "[v0][v1]xfade=transition=fade:duration=0.4:offset=4.6[x1];"
        "[x1][v2]xfade=transition=fadeblack:duration=0.4:offset=8.2[x2];"
        "[x2][v3]xfade=transition=fadeblack:duration=0.4:offset=12.8[x3];"
        "[x3]ass=subtitles.ass:fontsdir=fonts[vout]",
        "-map", "[vout]", "-map", "4:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p", "-threads", "1",
        "-flags:v", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1",
        "-r", "30", "-c:a", "pcm_f32le", "episode_pre.mkv",
    ]


def test_final_pass_all_cuts_is_a_single_concat_with_no_xfade():
    graph = _graph(_final(_all_cuts()))
    assert "xfade" not in graph
    assert "[v0][v1][v2][v3]concat=n=4:v=1:a=0,settb=1/30[seg0];[seg0]ass=subtitles.ass:fontsdir=fonts[vout]" in graph


def test_final_pass_every_concat_is_followed_by_settb_and_every_input_normalised():
    """concat's output time base is 1/1000000 and xfade refuses mismatched
    time bases (ffmpeg 6.1), so each concat is re-stamped to 1/30."""
    tl = _en_cut_to_black()
    graph = _graph(_final(tl))
    assert graph.count("concat=") == graph.count("concat=n=")
    assert len(re.findall(r"concat=n=\d+:v=1:a=0,settb=1/30\[seg\d+\]", graph)) == graph.count("concat=") == 8
    assert graph.count("settb=AVTB,fps=30,format=yuv420p") == len(tl["shots"]) + 1  # + the end card


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_final_pass_xfades_are_the_plans_frame_exact_joins(builder):
    tl = builder()
    joins = filtergraph.xfade_offsets(tl)
    found = re.findall(r"xfade=transition=(\w+):duration=([0-9.]+):offset=([0-9.]+)", _graph(_final(tl)))
    assert found == [(j["xfade"], j["duration"], j["offset"]) for j in joins]
    for (_name, duration, offset), join in zip(found, joins):
        assert round(float(offset) * profiles.FPS) == join["offset_frames"]
        assert round(float(duration) * profiles.FPS) == join["duration_frames"]


def test_final_pass_muxes_the_pcm_mix_and_never_uses_shortest():
    argv = _final(_en_cut_to_black())
    assert "-shortest" not in argv
    mix_index = argv.index("mix.wav")
    n_inputs = argv[:mix_index].count("-i")
    assert argv[argv.index("-map") + 1] == "[vout]"
    assert argv[argv.index("-map", argv.index("-map") + 1) + 1] == f"{n_inputs - 1}:a"
    assert argv[argv.index("-c:a") + 1] == "pcm_f32le"
    assert argv[-1] == "episode_pre.mkv"


def test_final_pass_escapes_the_ass_and_fontsdir_paths():
    graph = _graph(_final(_all_cuts(), ass_rel="sub,s.ass", fontsdir_rel="fo:nts"))
    assert graph.endswith("ass=sub\\,s.ass:fontsdir=fo\\:nts[vout]")


def test_final_pass_input_validation():
    tl = _first_shot_dissolve()
    with pytest.raises(filtergraph.GraphError, match=r"missing inputs for \['sh03'\]"):
        _final(tl, shot_inputs={"sh01": "a.mp4", "sh02": "b.mp4"})
    with pytest.raises(filtergraph.GraphError, match=r"unexpected inputs \['sh99'\]"):
        _final(tl, shot_inputs={"sh01": "a.mp4", "sh02": "b.mp4", "sh03": "c.mp4", "sh99": "d.mp4"})
    with pytest.raises(filtergraph.GraphError, match="end_card_input must be None"):
        _final(tl, end_card_input="cache/end_card.mp4")
    with pytest.raises(filtergraph.GraphError, match="end_card_input is required"):
        _final(_small_cut_to_black(), end_card_input=None)


# ========================================================== 6. audio mix

def test_audio_mix_argv_golden_ducking_graph():
    """The whole graph, pinned: DEC-157's exact ducking values and weights,
    the silent ``total_s`` bases, the looped and re-stamped bed with its
    0.5 s ``cut_to_black`` fade, and the four WAV outputs."""
    wav = ["-c:a", "pcm_f32le", "-ar", "48000", "-ac", "2", "-map_metadata", "-1", "-fflags", "+bitexact",
           "-flags:a", "+bitexact"]
    norm = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    tl = _small_cut_to_black()
    assert _mix(tl, sfx_inputs={"whoosh": "in/whoosh.wav", "sting": "in/sting.wav"}) == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", "in/l01.mp3", "-i", "in/l02.mp3", "-i", "in/l03.mp3",
        "-i", "in/whoosh.wav", "-i", "in/sting.wav",
        "-stream_loop", "-1", "-i", "in/bgm.mp3",
        "-filter_complex",
        f"[0:a]{norm},{LINE_FADES},adelay=delays=350:all=1[l0];"
        f"[1:a]{norm},{LINE_FADES},adelay=delays=4950:all=1[l1];"
        f"[2:a]{norm},{LINE_FADES},adelay=delays=8550:all=1[l2];"
        "anullsrc=r=48000:cl=stereo,atrim=duration=13.8[dlg_base];"
        "[dlg_base][l0][l1][l2]amix=inputs=4:normalize=0:duration=first,asplit=3[dlg_mix][dlg_stem][dlg_sc];"
        f"[3:a]{norm},adelay=delays=0:all=1[x0];"
        f"[4:a]{norm},adelay=delays=8550:all=1[x1];"
        "anullsrc=r=48000:cl=stereo,atrim=duration=13.8[sfx_base];"
        "[sfx_base][x0][x1]amix=inputs=3:normalize=0:duration=first,asplit=2[sfx_mix][sfx_stem];"
        f"[5:a]{norm},asetpts=N/SR/TB,atrim=duration=13.8,afade=t=out:st=13.3:d=0.5[bed];"
        "[bed][dlg_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300,asplit=2[bgm_mix][bgm_stem];"
        "[dlg_mix][bgm_mix][sfx_mix]amix=inputs=3:weights=1 0.3 0.8:normalize=0:duration=first[mix]",
        "-map", "[mix]", *wav, "mix.wav",
        "-map", "[dlg_stem]", *wav, "stems/dialogue.wav",
        "-map", "[sfx_stem]", *wav, "stems/sfx.wav",
        "-map", "[bgm_stem]", *wav, "stems/bgm.wav",
    ]


def test_audio_mix_argv_golden_no_bgm_no_sfx():
    """No BGM and no SFX still make a correct graph: silent stems of the
    full length, no loop, no sidechain (so the dialogue splits in two, not
    three -- an unconnected pad is an ffmpeg error), the same 3-input amix."""
    norm = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    argv = _mix(_first_shot_dissolve(), sfx_inputs={}, bgm_input=None)
    assert "-stream_loop" not in argv
    assert argv[4:10] == ["-i", "in/l01.mp3", "-i", "in/l02.mp3", "-i", "in/l03.mp3"]
    assert _graph(argv) == (
        f"[0:a]{norm},{LINE_FADES},adelay=delays=350:all=1[l0];"
        f"[1:a]{norm},{LINE_FADES},adelay=delays=4950:all=1[l1];"
        f"[2:a]{norm},{LINE_FADES},adelay=delays=8950:all=1[l2];"
        "anullsrc=r=48000:cl=stereo,atrim=duration=13.6[dlg_base];"
        "[dlg_base][l0][l1][l2]amix=inputs=4:normalize=0:duration=first,asplit=2[dlg_mix][dlg_stem];"
        "anullsrc=r=48000:cl=stereo,atrim=duration=13.6,asplit=2[sfx_mix][sfx_stem];"
        "anullsrc=r=48000:cl=stereo,atrim=duration=13.6,asplit=2[bgm_mix][bgm_stem];"
        "[dlg_mix][bgm_mix][sfx_mix]amix=inputs=3:weights=1 0.3 0.8:normalize=0:duration=first[mix]"
    )


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
@pytest.mark.parametrize("ext", ["mp3", "wav"])
def test_every_line_fades_in_and_out_at_its_own_file_edges_and_no_sfx_does(builder, ext):
    """The fades sit between the resampling and the delay -- on the line's
    own file, before it is placed -- for every line of any engine; the fade
    out is done reversed, at the file's real end, never at ``duration_s``
    (which Edge sets to its last word's end)."""
    tl = builder()
    graph = _graph(_mix(tl, line_inputs={line["line_id"]: f"in/{line['line_id']}.{ext}" for line in tl["lines"]}))
    chains = re.findall(r"\[\d+:a\]([^;]*)\[l\d+\]", graph)
    assert len(chains) == len(tl["lines"])
    norm = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    for chain, line in zip(chains, tl["lines"]):
        assert chain == f"{norm},{LINE_FADES},adelay=delays={round(line['start_s'] * 1000)}:all=1"
        assert "st=" not in chain and str(line["duration_s"]) not in chain.split("adelay")[0]
    assert all("afade" not in chain for chain in re.findall(r"\[\d+:a\]([^;]*)\[x\d+\]", graph))
    assert filtergraph.LINE_FADE_IN_S == 0.005 and filtergraph.LINE_FADE_OUT_S == 0.01


def test_tier_3_lines_fade_the_same_way_heard_or_feeding_the_ducking_only():
    tl = _small_cut_to_black()
    shot = tl["shots"][0]["shot_id"]
    native = {shot: {"input": "in/clip.mp4", "lines": [tl["lines"][0]["line_id"]]}}
    graph = _graph(_mix(tl, native_audio=native))
    chains = re.findall(r"\[\d+:a\]([^;]*)\[[ls]\d+\]", graph)
    assert len(chains) == len(tl["lines"])  # with a bed, the replaced line still feeds the sidechain
    assert all(f",{LINE_FADES},adelay=delays=" in chain for chain in chains)
    without_bed = _graph(_mix(tl, native_audio=native, bgm_input=None))
    kept = re.findall(r"\[\d+:a\]([^;]*)\[l\d+\]", without_bed)
    assert len(kept) == len(tl["lines"]) - 1 and all(f",{LINE_FADES},adelay=" in chain for chain in kept)


def test_no_sfx_with_bgm_keeps_the_sidechain():
    graph = _graph(_mix(_small_cut_to_black(), sfx_inputs={}))
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=13.8,asplit=2[sfx_mix][sfx_stem]" in graph
    assert "asplit=3[dlg_mix][dlg_stem][dlg_sc]" in graph
    assert "[bed][dlg_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300" in graph


def test_no_bgm_with_sfx_has_no_sidechain():
    graph = _graph(_mix(_small_cut_to_black(), bgm_input=None))
    assert "sidechaincompress" not in graph and "[dlg_sc]" not in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=13.8,asplit=2[bgm_mix][bgm_stem]" in graph
    assert "[sfx_base][x0][x1]amix=inputs=3" in graph


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_dialogue_delays_are_the_timeline_starts_in_ms(builder):
    tl = builder()
    argv = _mix(tl)
    delays = [int(ms) for ms in re.findall(r"adelay=delays=(\d+):all=1\[l\d+\]", _graph(argv))]
    assert delays == [round(line["start_s"] * 1000) for line in tl["lines"]]
    # the k-th line's own file feeds the k-th delay
    for k, line in enumerate(tl["lines"]):
        assert f"[{k}:a]" in _graph(argv) and argv[argv.index(f"in/{line['line_id']}.mp3") - 1] == "-i"
        assert argv.index(f"in/{line['line_id']}.mp3") == 5 + 2 * k


@pytest.mark.parametrize("builder", [_fr_hard_stop, _en_cut_to_black, _small_cut_to_black],
                         ids=["fr", "en", "small"])
def test_sfx_delays_are_the_anchor_starts_in_ms(builder):
    tl = builder()
    delays = [int(ms) for ms in re.findall(r"adelay=delays=(\d+):all=1\[x\d+\]", _graph(_mix(tl)))]
    assert delays == [round(anchor["start_s"] * 1000) for anchor in tl["sfx_anchors"]]
    assert len(delays) == 2


def test_an_sfx_anchor_without_an_input_is_skipped():
    """spec 11: a cue the caller could not resolve is skipped (and reported
    by the caller), never a crash."""
    argv = _mix(_small_cut_to_black(), sfx_inputs={"sting": "in/sting.wav"})
    assert "in/whoosh.wav" not in argv
    assert re.findall(r"adelay=delays=(\d+):all=1\[x\d+\]", _graph(argv)) == ["8550"]


@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_the_bed_covers_the_full_length_with_no_gap(builder):
    """Looped (``-stream_loop -1`` right before its ``-i``), re-stamped by
    sample count so the loop seams cannot shorten it, trimmed to exactly
    ``total_s``, faded out over the ending's own fade ending at
    ``total_s``; every stem starts from a silent base of the same length."""
    tl = builder()
    argv = _mix(tl)
    graph = _graph(argv)
    total = filtergraph._num(tl["total_s"])
    fade = profiles.BED_FADE_OUT_S[_ending(tl)]

    bgm_at = argv.index("in/bgm.mp3")
    assert argv[bgm_at - 3:bgm_at] == ["-stream_loop", "-1", "-i"]
    bed = re.search(r"\[\d+:a\][^;]*\[bed\]", graph).group(0)
    assert f"asetpts=N/SR/TB,atrim=duration={total},afade=t=out:st=" in bed
    (st, d) = re.search(r"afade=t=out:st=([0-9.]+):d=([0-9.]+)", bed).groups()
    assert float(st) + float(d) == pytest.approx(tl["total_s"], abs=1e-9)
    assert float(d) == fade
    assert graph.count(f"anullsrc=r=48000:cl=stereo,atrim=duration={total}") == 2  # dialogue + sfx bases
    sfx_amix = ([f"amix=inputs={len(tl['sfx_anchors']) + 1}:normalize=0:duration=first,asplit=2"]
                if tl["sfx_anchors"] else [])  # no anchor: the SFX stem is the silent base itself
    assert re.findall(r"amix=[^;\[]*", graph) == [
        f"amix=inputs={len(tl['lines']) + 1}:normalize=0:duration=first,asplit=3",
        *sfx_amix,
        "amix=inputs=3:weights=1 0.3 0.8:normalize=0:duration=first",
    ]


def test_hard_stop_and_cut_to_black_bed_tails():
    hard = _graph(_mix(_fr_hard_stop()))
    card = _graph(_mix(_en_cut_to_black()))
    assert "afade=t=out:st=33.15:d=0.05[bed]" in hard   # 33.2 s, 50 ms
    assert "afade=t=out:st=33.3:d=0.5[bed]" in card     # 33.8 s, 0.5 s


def test_the_mix_weights_follow_the_amix_input_order():
    graph = _graph(_mix(_en_cut_to_black()))
    assert graph.endswith("[dlg_mix][bgm_mix][sfx_mix]amix=inputs=3:weights=1 0.3 0.8:normalize=0:duration=first[mix]")


def test_audio_mix_writes_the_mix_and_three_stems_as_48k_stereo_pcm():
    argv = _mix(_en_cut_to_black())
    outputs = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-map"]
    assert outputs == ["[mix]", "[dlg_stem]", "[sfx_stem]", "[bgm_stem]"]
    for target in ("mix.wav", *STEMS.values()):
        at = argv.index(target)
        assert argv[at - 12:at] == ["-c:a", "pcm_f32le", "-ar", "48000", "-ac", "2", "-map_metadata", "-1",
                                    "-fflags", "+bitexact", "-flags:a", "+bitexact"]


def test_audio_mix_input_validation():
    tl = _first_shot_dissolve()
    with pytest.raises(filtergraph.GraphError, match=r"missing inputs for \['l03'\]"):
        _mix(tl, line_inputs={"l01": "in/a.mp3", "l02": "in/b.mp3"})
    with pytest.raises(filtergraph.GraphError, match="disagrees with the timeline"):
        _mix(tl, ending="cut_to_black")
    with pytest.raises(filtergraph.GraphError, match="unknown ending"):
        _mix(tl, ending="fade_out")
    with pytest.raises(filtergraph.GraphError, match="stems_rel"):
        _mix(tl, stems_rel={"dialogue": "d.wav", "bgm": "b.wav"})
    with pytest.raises(filtergraph.GraphError, match="sfx_inputs"):
        _mix(tl, sfx_inputs=None)


def test_overlapping_lines_are_refused():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["lines"][1]["start_s"] = 2.0  # l01 runs 0.35-2.35 s
    with pytest.raises(filtergraph.GraphError, match="overlap"):
        _mix(tl)


def test_a_line_past_the_end_is_refused():
    tl = copy.deepcopy(_first_shot_dissolve())
    tl["lines"][2]["start_s"] = 12.0  # 12.0 + 2.0 > 13.6
    with pytest.raises(filtergraph.GraphError, match="outside the episode"):
        _mix(tl)


# ================================================== 7. no absolute paths

@pytest.mark.parametrize("builder", [p.values[0] for p in TIMELINES], ids=[p.id for p in TIMELINES])
def test_no_absolute_path_in_any_stage_6_argv(builder):
    tl = builder()
    argvs = [_final(tl), _final(tl, profile=profiles.GOLDEN), _mix(tl), _mix(tl, bgm_input=None, sfx_inputs={})]
    for argv in argvs:
        for arg in argv:
            assert not arg.startswith("/"), argv
            assert not (len(arg) >= 2 and arg[1] == ":" and arg[0].isalpha()), argv


@pytest.mark.parametrize("kwarg,value", [
    ("ass_rel", "/abs/subtitles.ass"), ("fontsdir_rel", "/abs/fonts"), ("mix_rel", "/abs/mix.wav"),
    ("out_rel", "/abs/episode_pre.mkv"), ("end_card_input", "/abs/end_card.mp4"),
    ("shot_inputs", "abs-shot"),
])
def test_final_pass_refuses_absolute_paths(kwarg, value):
    tl = _small_cut_to_black()
    if kwarg == "shot_inputs":
        value = {shot["shot_id"]: f"/abs/{shot['shot_id']}.mp4" for shot in tl["shots"]}
    with pytest.raises(ValueError, match="relative"):
        _final(tl, **{kwarg: value})


@pytest.mark.parametrize("kwarg", ["line_inputs", "sfx_inputs", "bgm_input", "out_rel", "stems_rel"])
def test_audio_mix_refuses_absolute_paths(kwarg):
    tl = _small_cut_to_black()
    values = {
        "line_inputs": {line["line_id"]: f"/abs/{line['line_id']}.mp3" for line in tl["lines"]},
        "sfx_inputs": {"whoosh": "C:\\sfx\\whoosh.wav", "sting": "in/sting.wav"},
        "bgm_input": "/abs/bgm.mp3",
        "out_rel": "/abs/mix.wav",
        "stems_rel": dict(STEMS, bgm="/abs/bgm_stem.wav"),
    }
    with pytest.raises(ValueError, match="relative"):
        _mix(tl, **{kwarg: values[kwarg]})
