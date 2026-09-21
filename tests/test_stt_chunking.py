"""Audio chunk planning for a hosted transcription API.

The size ceiling binds before any duration limit: a 20-minute video at 16 kHz
mono FLAC lands around 19-23 MB against Groq's 25 MB free-tier cap, so "it fits"
is not safe for a 25-minute one. ffmpeg is never invoked here — the prober is
injected.
"""

import pytest

from clipping.providers import audio


MB = 1024 * 1024


def plan(duration, size_mb, **kwargs):
    kwargs.setdefault("silences", [])
    return audio.plan_chunks(
        "fake.flac", duration=duration, size_bytes=int(size_mb * MB), **kwargs
    )


# ------------------------------------------------------------- when it fits

def test_a_file_under_the_cap_is_one_chunk():
    assert plan(600, 10) == [(0.0, 600.0)]


def test_a_file_exactly_at_the_cap_is_one_chunk():
    assert len(plan(1200, 24)) == 1


def test_a_20_minute_video_near_the_cap_is_still_one_request():
    """The measured shape of the real case: ~19-23 MB for 20 minutes."""
    assert len(plan(1200, 21)) == 1


# ---------------------------------------------------------- when it does not

def test_an_oversized_file_is_split():
    assert len(plan(1800, 60)) > 1


def test_every_chunk_would_fit_under_the_cap():
    """The property that matters: each request must be uploadable."""
    duration, size_mb = 3600.0, 90.0
    bytes_per_second = size_mb * MB / duration
    for start, end in plan(duration, size_mb):
        assert (end - start) * bytes_per_second <= audio.DEFAULT_MAX_BYTES


def test_the_rate_is_derived_from_the_file_not_assumed():
    """FLAC is variable-rate: a quiet interview compresses to a fraction of a
    music-heavy video. Assuming a rate splits files that never needed it."""
    quiet = plan(3600, 20)     # 3600s, well under the cap
    loud = plan(3600, 200)     # same duration, ten times the data
    assert len(quiet) == 1
    assert len(loud) > len(quiet)


def test_chunks_cover_the_whole_file():
    bounds = plan(3600, 90)
    assert bounds[0][0] == 0.0
    assert bounds[-1][1] == pytest.approx(3600.0)
    for (_, end), (next_start, _) in zip(bounds, bounds[1:]):
        assert next_start <= end, "a gap would lose speech entirely"


def test_chunks_overlap_so_no_word_is_cut_in_half():
    bounds = plan(3600, 90)
    assert len(bounds) > 1
    for (_, end), (next_start, _) in zip(bounds, bounds[1:]):
        assert end - next_start == pytest.approx(audio.DEFAULT_OVERLAP, abs=0.01)


def test_boundaries_are_monotonic():
    bounds = plan(7200, 200)
    for (start, end) in bounds:
        assert end > start
    for (s1, _), (s2, _) in zip(bounds, bounds[1:]):
        assert s2 > s1


def test_a_stub_final_chunk_is_folded_into_the_one_before_it():
    """Measured on the real 20-minute video: the arithmetic left a 10.8-second
    tail, which is its own upload, its own round trip and its own seam to
    stitch — and Groq bills a 10-second minimum per request regardless."""
    bounds = plan(1211.3, 39.3, target_seconds=600)
    assert len(bounds) == 2
    assert bounds[-1][1] - bounds[-1][0] > audio.MIN_TAIL_SECONDS


def test_a_stub_tail_is_kept_when_merging_would_not_fit():
    """A short chunk beats a rejected one."""
    bounds = plan(1000.0, 100.0, target_seconds=480)
    duration, size_mb = 1000.0, 100.0
    rate = size_mb * MB / duration
    for start, end in bounds:
        assert (end - start) * rate <= audio.DEFAULT_MAX_BYTES


def test_absorbing_a_tail_never_loses_coverage():
    bounds = plan(1211.3, 39.3, target_seconds=600)
    assert bounds[0][0] == 0.0
    assert bounds[-1][1] == pytest.approx(1211.3)


# ------------------------------------------------------------------ silence

def test_a_boundary_moves_to_nearby_silence():
    """So a cut does not land mid-word."""
    bounds = plan(3600, 90, target_seconds=600, silences=[605.0])
    assert bounds[0][1] == pytest.approx(605.0)


def test_a_distant_silence_is_ignored():
    bounds = plan(3600, 90, target_seconds=600, silences=[100.0])
    assert bounds[0][1] != pytest.approx(100.0)


def test_no_silences_still_produces_a_valid_plan():
    bounds = plan(3600, 90, silences=[])
    assert len(bounds) > 1
    assert bounds[-1][1] == pytest.approx(3600.0)


def test_snap_never_moves_a_boundary_before_the_chunk_start():
    assert audio._snap_to_silence(600.0, [10.0], floor=100.0, ceiling=3600.0) == 600.0


# ------------------------------------------------------------------- errors

def test_a_zero_duration_file_is_refused():
    with pytest.raises(audio.AudioError):
        plan(0, 5)


def test_a_missing_binary_is_reported_clearly():
    with pytest.raises(audio.AudioError) as info:
        audio._run(["definitely-not-a-real-binary-xyz"])
    assert "not installed" in str(info.value)


# ------------------------------------------------- silence parsing (no ffmpeg)

def test_silence_midpoints_are_parsed_from_ffmpeg_stderr(monkeypatch):
    stderr = (
        b"[silencedetect @ 0x1] silence_start: 12.5\n"
        b"[silencedetect @ 0x1] silence_end: 13.5 | silence_duration: 1.0\n"
        b"[silencedetect @ 0x1] silence_start: 40.0\n"
        b"[silencedetect @ 0x1] silence_end: 41.0 | silence_duration: 1.0\n"
    )

    class FakeProc:
        pass

    proc = FakeProc()
    proc.stderr = stderr
    proc.stdout = b""
    proc.returncode = 0
    monkeypatch.setattr(audio, "_run", lambda *a, **kw: proc)

    assert audio.find_silences("x.flac") == [13.0, 40.5]


def test_an_unclosed_silence_is_ignored(monkeypatch):
    class FakeProc:
        stderr = b"silence_start: 12.5\n"
        stdout = b""
        returncode = 0

    monkeypatch.setattr(audio, "_run", lambda *a, **kw: FakeProc())
    assert audio.find_silences("x.flac") == []
