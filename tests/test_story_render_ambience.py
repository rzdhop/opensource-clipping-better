"""Tier-3 ambience in the render (AI Story phase 7 follow-up, stage E; the
human's choice of 2026-10-02: the video model's sound is AMBIENCE + SFX
ONLY, never dialogue).

On an ambience story (``media_policy.ambience``) every shot cut from a
current clip that has a sound track gives the audio mix one more stem: the
clip's own sound, placed at the sample of its shot's first frame, trimmed to
its frames, faded in and out over ``profiles.AMBIENCE_FADE_S`` (never more
than half the shot), summed, lowered (``AMBIENCE_GAIN``), ducked by the
dialogue with its own gentler values (``AMBIENCE_DUCK_*``) and mixed on the
SFX bus -- UNDER the dialogue: every line is heard, in its TTS voice. A clip
with no sound track adds nothing, said in the feed. Without ambience the
mix's argv is byte for byte today's.

Three halves, as the native-audio tests (``test_story_render_native_audio``):
the pure graph, what is heard (the tier-2 golden's tiny episode with a 1 kHz
clip on ``sh02``, its A stage run with the real ffmpeg and measured with a
Goertzel filter), and what the render step decides. Stdlib + pytest
(DEC-012); the new behaviour is reached inside the tests, so on the parent
commit each test fails on its own.
"""

from __future__ import annotations

import pytest

import test_aistory_render_graph as trg
import test_story_assets_step as tas
import test_story_clip_estimate as tce
import test_story_render_native_audio as tna
import test_story_render_step as trs
import test_story_video_phase as tvp
from clipping.aistory import schemas
from clipping.aistory.render import filtergraph, profiles
from clipping.aistory.render import golden_tier2 as gt2
from clipping.aistory.render import plan as plan_mod
from clipping.aistory.render import runner
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_video_phase import timings_path  # noqa: F401 -- the measured clip timings under tmp_path

AMBIENT_SHOT = tna.NATIVE_SHOT  # sh02, the second shot: its start is not the episode's
CLIP_HZ = tna.CLIP_HZ


# ================================================================= the graph

def test_the_ambience_constants_are_gentler_than_the_beds():
    """The clip's sound sits under the dialogue: lower than the bed's own
    reduction would leave it, ducked less hard and more slowly than the
    music bed (DEC-157's values stay as they are), its fades longer than the
    replace mode's 10 ms."""
    assert 0 < profiles.AMBIENCE_GAIN < 1
    assert profiles.AMBIENCE_DUCK_RATIO < profiles.DUCK_RATIO
    assert profiles.AMBIENCE_DUCK_THRESHOLD > profiles.DUCK_THRESHOLD
    assert profiles.AMBIENCE_DUCK_ATTACK_MS > profiles.DUCK_ATTACK_MS
    assert profiles.AMBIENCE_DUCK_RELEASE_MS > profiles.DUCK_RELEASE_MS
    assert 0.06 <= profiles.AMBIENCE_FADE_S <= 0.08 and profiles.AMBIENCE_FADE_S > filtergraph.NATIVE_FADE_S
    assert (profiles.DUCK_THRESHOLD, profiles.DUCK_RATIO, profiles.DUCK_ATTACK_MS, profiles.DUCK_RELEASE_MS) == (
        0.03, 8, 20, 300)


def test_guard_without_ambience_the_mix_argv_is_todays_byte_for_byte():
    """``ambience`` None or empty: the very argv of every existing golden
    (``test_aistory_render_graph``'s ``_mix``)."""
    for build in (trg._small_cut_to_black, trg._first_shot_dissolve, trg._all_cuts):
        timeline = build()
        assert trg._mix(timeline, ambience=None) == trg._mix(timeline) == trg._mix(timeline, ambience={})
        assert trg._mix(timeline, bgm_input=None, ambience={}) == trg._mix(timeline, bgm_input=None)


def test_an_ambience_stem_starts_at_its_shots_first_frame_faded_ducked_and_on_the_sfx_bus():
    """Fail-first. One stem per ambience shot, read inside the graph
    (``amovie``, as the native stems: ffmpeg 7.1.5), re-stamped, trimmed to
    the shot's samples, faded in and out over AMBIENCE_FADE_S, delayed to the
    sample of the shot's first frame; the stems summed over a silent base,
    lowered, ducked by the dialogue as heard, and mixed on the SFX bus. The
    dialogue's own chains are untouched; the final mix takes the dialogue
    from the split that fed the ducking."""
    timeline = trg._small_cut_to_black()
    shot = timeline["shots"][1]
    plain = trg._graph(trg._mix(timeline, sfx_inputs={}))
    argv = trg._mix(timeline, ambience={shot["shot_id"]: "in/clip.mp4"}, sfx_inputs={})
    graph = trg._graph(argv)

    rate = profiles.AUDIO_RATE
    samples = round(shot["frames"] * rate / profiles.FPS)
    fade = round(profiles.AMBIENCE_FADE_S * rate)
    start = round(filtergraph.sequence_plan(timeline)["start_frames"][shot["shot_id"]] * rate / profiles.FPS)
    norm = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    assert (f"amovie=in/clip.mp4,{norm},asetpts=N/SR/TB,atrim=end_sample={samples},afade=t=in:ss=0:ns={fade},"
            f"afade=t=out:ss={samples - fade}:ns={fade},adelay=delays={start}S:all=1[amb0]") in graph
    assert "in/clip.mp4" not in argv  # never an -i input
    total = filtergraph._num(timeline["total_s"])
    assert (f"anullsrc=r=48000:cl=stereo,atrim=duration={total}[amb_base];"
            f"[amb_base][amb0]amix=inputs=2:normalize=0:duration=first,"
            f"volume={filtergraph._num(profiles.AMBIENCE_GAIN)}[amb_bed]") in graph
    assert "[dlg_mix]asplit=2[dlg_heard][amb_sc]" in graph
    assert (f"[amb_bed][amb_sc]sidechaincompress=threshold={filtergraph._num(profiles.AMBIENCE_DUCK_THRESHOLD)}:"
            f"ratio={filtergraph._num(profiles.AMBIENCE_DUCK_RATIO)}:"
            f"attack={filtergraph._num(profiles.AMBIENCE_DUCK_ATTACK_MS)}:"
            f"release={filtergraph._num(profiles.AMBIENCE_DUCK_RELEASE_MS)}[amb]") in graph
    assert "[sfx_base][x0][x1][amb]amix=inputs=4:normalize=0:duration=first,asplit=2[sfx_mix][sfx_stem]" in \
        trg._graph(trg._mix(timeline, ambience={shot["shot_id"]: "in/clip.mp4"},
                            sfx_inputs={"whoosh": "in/whoosh.wav", "sting": "in/sting.wav"}))
    assert "[sfx_base][amb]amix=inputs=2:normalize=0:duration=first,asplit=2[sfx_mix][sfx_stem]" in graph
    assert graph.endswith("[dlg_heard][bgm_mix][sfx_mix]amix=inputs=3:weights=1 0.3 0.8:normalize=0:"
                          "duration=first[mix]")
    # The dialogue section is today's, chain for chain; the bed's ducking too.
    dialogue = plain[:plain.index("asplit=3[dlg_mix][dlg_stem][dlg_sc]")]
    assert graph.startswith(dialogue)
    assert "[bed][dlg_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300" in graph
    # Three stems still: the ambience is on the SFX bus.
    assert [argv[i + 1] for i, token in enumerate(argv) if token == "-map"] == [
        "[mix]", "[dlg_stem]", "[sfx_stem]", "[bgm_stem]"]


def test_an_ambience_stem_never_fades_longer_than_half_its_shot_and_is_refused_where_it_cannot_be():
    timeline = trg._small_cut_to_black()
    stems = filtergraph._ambience_stems(timeline, {shot["shot_id"]: f"in/{shot['shot_id']}.mp4"
                                                   for shot in timeline["shots"]})
    assert [stem["shot_id"] for stem in stems] == [shot["shot_id"] for shot in timeline["shots"]]
    for stem in stems:
        assert stem["fade"] == min(round(profiles.AMBIENCE_FADE_S * profiles.AUDIO_RATE), stem["samples"] // 2)
    # A 3-frame shot (100 ms) fades over half of itself, never longer.
    assert filtergraph._fade_samples(round(3 * profiles.AUDIO_RATE / profiles.FPS)) == 2400

    with pytest.raises(filtergraph.GraphError, match="not shots of the timeline"):
        trg._mix(timeline, ambience={"sh99": "in/clip.mp4"})
    with pytest.raises(ValueError):
        trg._mix(timeline, ambience={timeline["shots"][0]["shot_id"]: "/abs/clip.mp4"})
    lines = timeline["lines"]
    with pytest.raises(filtergraph.GraphError, match="one tier-3 audio mode"):
        trg._mix(timeline, ambience={timeline["shots"][1]["shot_id"]: "in/clip.mp4"},
                 native_audio={timeline["shots"][0]["shot_id"]: {"input": "in/c0.mp4",
                                                                 "lines": [lines[0]["line_id"]]}})


# ============================================================ what is heard

def _ambient_mix(workdir, *, ambient):
    """The tiny episode's plan (GOLDEN), ``sh02`` cut from a clip with a sound
    track -- *ambient*: its sound kept as ambience, as the render step hands
    it, or not (tier 2) -- its A stage run with the real ffmpeg."""
    import shutil
    import subprocess

    workdir.mkdir(parents=True, exist_ok=True)
    docs = tna._documents()
    inputs = gt2.write_sources(workdir)
    clip = workdir / "sources" / "clips" / "shot_02.mp4"
    tna.make_clip_with_sound(clip)
    inputs["videos"] = {AMBIENT_SHOT: runner.file_record(clip, "fixture/clips/shot_02.mp4")}
    if ambient:
        inputs["ambience"] = [AMBIENT_SHOT]
    plan = plan_mod.build_render_plan(**docs, story=dict(gt2.STORY), ep=gt2.EP, inputs=inputs,
                                      ffmpeg={"version": "here", "machine": "here"}, profile="golden")
    render_dir = workdir / "render"
    for sub in plan_mod.WORK_DIRS:
        (render_dir / sub).mkdir(parents=True, exist_ok=True)
    for item in plan["inputs"]:
        shutil.copyfile(item["path"], render_dir / item["staged"])
    stage = next(stage for stage in plan["stages"] if stage["id"] == "A")
    result = subprocess.run(stage["argv"], cwd=render_dir, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr[-1500:]
    return render_dir, plan


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    from clipping.aistory.render import timeline as rt

    tna._require_ffmpeg()
    root = tmp_path_factory.mktemp("ambience")
    docs = tna._documents()
    timeline = rt.build_timeline(docs["script"], docs["storyboard"], docs["template"], gt2.STORY["language"],
                                 style_lock=docs["style_lock"])
    return {"ambience": _ambient_mix(root / "ambience", ambient=True),
            "tier2": _ambient_mix(root / "tier2", ambient=False), "timeline": timeline}


def test_the_clips_sound_is_heard_under_its_shots_lines_from_its_first_frame_ducked(renders):
    """Fail-first. The clip's 1 kHz tone sounds from ``sh02``'s first frame
    (its fade-in reaching half within half the fade, plus a frame) to its
    last, nothing of it before or after; ``sh02``'s lines (440 and 550 Hz)
    are heard, the dialogue stem and the ducked bed byte for byte tier 2's;
    under a line the tone is ducked well below its level before the first
    line. The plan stages the clip again as ``clip_audio`` (read inside the
    graph) and labels the shot ``video_ambience``; the video stage keeps
    ``-an``."""
    render_dir, plan = renders["ambience"]
    samples = tna._pcm(render_dir / plan_mod.MIX_REL)
    start_s, end_s, tones = tna._spans(renders["timeline"])
    onset = tna._onset(samples, CLIP_HZ, start_s)
    print(f"ambience onset {onset:.4f} s, shot's first frame {start_s:.4f} s")
    assert start_s - tna.ONE_FRAME_S <= onset <= start_s + profiles.AMBIENCE_FADE_S / 2 + tna.ONE_FRAME_S
    assert tna._level(samples, CLIP_HZ, 0.05, start_s - 0.02) < tna.ABSENT
    assert tna._level(samples, CLIP_HZ, end_s + 0.02, renders["timeline"]["total_s"] - 0.01) < tna.ABSENT

    for line_id in tna.NATIVE_LINES + ["l01"]:
        heard = tna._level(samples, tna.LINE_HZ[line_id], *tones[line_id])
        assert heard > tna.PRESENT, (line_id, heard)
    tier2_dir, _tier2 = renders["tier2"]
    for kind in ("dialogue", "bgm"):
        rel = plan_mod.STEMS_REL[kind]
        assert (render_dir / rel).read_bytes() == (tier2_dir / rel).read_bytes(), kind

    first_line = tones[tna.NATIVE_LINES[0]]
    free = tna._level(samples, CLIP_HZ, start_s + 0.1, min(start_s + 0.3, first_line[0] - 0.05))
    ducked = tna._level(samples, CLIP_HZ, *first_line)
    print(f"ambience before the first line {free:.5f}, under it {ducked:.5f}")
    assert free > tna.PRESENT and ducked < 0.5 * free
    sfx = tna._pcm(render_dir / plan_mod.STEMS_REL["sfx"])
    assert tna._level(sfx, CLIP_HZ, start_s + 0.1, start_s + 0.3) > tna.PRESENT

    assert plan["shot_modes"] == {"sh01": "motion", AMBIENT_SHOT: "video_ambience"}
    by_role = {(item["role"], item["id"]): item for item in plan["inputs"]}
    shot_clip, clip_audio = by_role[("shot", AMBIENT_SHOT)], by_role[("clip_audio", AMBIENT_SHOT)]
    assert (clip_audio["source"], clip_audio["staged"], clip_audio["sha256"]) == (
        shot_clip["source"], shot_clip["staged"], shot_clip["sha256"])
    stages = {stage["id"]: stage for stage in plan["stages"]}
    assert "-an" in stages[f"S:{AMBIENT_SHOT}"]["argv"]
    mix_argv = stages["A"]["argv"]
    assert clip_audio["staged"] not in mix_argv
    assert f"amovie={clip_audio['staged']}," in mix_argv[mix_argv.index("-filter_complex") + 1]


def test_guard_at_tier_2_the_clips_sound_is_nowhere(renders):
    render_dir, plan = renders["tier2"]
    assert plan["shot_modes"] == {"sh01": "motion", AMBIENT_SHOT: "video"}
    samples = tna._pcm(render_dir / plan_mod.MIX_REL)
    assert tna._level(samples, CLIP_HZ, 0.0, renders["timeline"]["total_s"]) < tna.ABSENT


def test_a_shot_that_keeps_ambience_must_be_cut_from_its_clip(tmp_path):
    docs = tna._documents()
    inputs = gt2.write_sources(tmp_path)
    inputs["ambience"] = [AMBIENT_SHOT]  # no clip given for it
    with pytest.raises(plan_mod.PlanError, match="ambience"):
        plan_mod.build_render_plan(**docs, story=dict(gt2.STORY), ep=gt2.EP, inputs=inputs,
                                   ffmpeg={"version": "here", "machine": "here"}, profile="golden")


# ============================================== what the render step decides

def _ambient_episode(store, tmp_path, monkeypatch, *, tier):
    """The render step's episode at *tier* on a story in ambience mode
    (``media_policy.ambience`` forced on, before any clip is asked, so the
    clips are made with the brief), its planned clips made by stage 8's fake
    video adapter; ``loud``'s clip then replaced by a real one with a sound
    track, ``mute``'s left without. The shots the plan leaves out are kept
    still (plan 33 stage 4: else the render refuses them). Its assets
    approved."""
    from clipping.aistory import media_policy

    monkeypatch.setattr(media_policy, "ambience", lambda story: True)
    settings = tce._settings(**tvp.PAID)
    story_id = tvp._keyframes(store, tmp_path, settings=settings)
    tce._tier(store, story_id, tier=tier)
    planned = [row["shot_id"] for row in tvp._plan(store, story_id, settings, tvp._adapters(tvp.FakeVideo()))["plan"]]
    assert len(planned) >= 2, planned
    loud, mute = planned[:2]
    # An opt-in pin is not read in ambience mode: the lines stay TTS.
    tce._patch(store, story_id, {"shot_id": loud, "keep_native_audio": True})
    tvp.keep_unplanned_still(store, story_id, planned)
    tas._run(store, story_id, adapters=tvp._adapters(tvp.FakeVideo()), settings=settings)
    shots = {shot["shot_id"]: shot for shot in tas._shots(store, story_id)}
    assert shots[loud]["assets"]["clip"]["state"] == shots[mute]["assets"]["clip"]["state"] == "current"
    tna.make_clip_with_sound(trs.ep_dir(store, story_id) / shots[loud]["assets"]["video"], seconds=1.0)
    trs.approve_assets(store, story_id)
    return story_id, loud, mute


@pytest.mark.parametrize("tier", [3, 2])
def test_an_ambience_story_renders_each_clips_sound_under_its_lines_and_says_when_a_clip_has_none(
        store, tmp_path, monkeypatch, tier):
    """Fail-first. Tier 3: the shot whose clip has a sound track is
    ``video_ambience`` (its clip staged again as ``clip_audio``, the mix's
    ``ambience``), never ``video_native_audio`` -- its ``keep_native_audio``
    pin is not read; the shot whose clip has none is plain ``video`` and
    the feed says it adds no ambience. Tier 2: neither, nothing said."""
    tna._require_ffmpeg()
    story_id, loud, mute = _ambient_episode(store, tmp_path, monkeypatch, tier=tier)
    seen = tna._spy_mix(monkeypatch)

    summary, log, _fake = trs.render(store, story_id, tmp_path=tmp_path)

    assert summary["state"] == "completed" and len(seen) == 1
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    assert schemas.render_manifest_errors(manifest) == []
    clip_audio = [item for item in manifest["inputs"] if item["role"] == "clip_audio"]
    notes = [line for line in log if mute in line and "ambience" in line]
    assert not seen[0].get("native_audio")
    if tier == 3:
        assert (manifest["shot_modes"][loud], manifest["shot_modes"][mute]) == ("video_ambience", "video")
        assert [item["id"] for item in clip_audio] == [loud]
        assert seen[0]["ambience"] == {loud: clip_audio[0]["staged"]}
        assert len(notes) == 1 and "no sound track" in notes[0] and loud not in notes[0], log
    else:
        assert (manifest["shot_modes"][loud], manifest["shot_modes"][mute]) == ("video", "video")
        assert clip_audio == [] and not seen[0].get("ambience") and notes == []
