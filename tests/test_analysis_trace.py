"""What the analysis decided, kept rather than only printed.

Every rejection already has a reason and every window already has a status;
both went to the log and were lost. The log is also truncated on purpose -- it
prints five snap rejections and a count -- so the one place the detail existed
was a console nobody kept.

This is the data behind a future "why this clip, and why not that one" view.
No route and no UI in this stage.

Stdlib only (DEC-012).
"""

import json

from clipping.analysis import analyzer, cache as cache_mod
from test_analysis_windows import META, candidates, scripted, segmen

TRACE = analyzer.TRACE_FILENAME


class Cfg:
    jumlah_clip = 3
    platform = "tiktok"
    durasi_hook = 3
    use_broll = False
    pexels_api_key = ""
    hook_v2 = False
    no_segment_trim = False
    output_language = "en"
    analysis_budget_seconds = 900
    analysis_cache = True

    def __init__(self, outputs_dir):
        self.outputs_dir = str(outputs_dir)


def run(answers, cfg, segments=None, chain=None):
    logs = []
    clips = analyzer.analyze(
        segments or segmen(),
        cfg,
        chain=chain or [("groq", "m")],
        keys={"groq": "k"},
        on_log=logs.append,
        run_chain=scripted(*answers),
    )
    return clips, logs


FULL = [
    candidates((0, 3, 90), (20, 23, 70)),
    candidates((45, 48, 85)),
    {"ranked": [{"id": 0, "score": 95, "topic": "a"},
                {"id": 1, "score": 80, "topic": "b"},
                {"id": 2, "score": 70, "topic": "c"}]},
    META, META, META,
]


def trace_of(tmp_path):
    return json.loads((tmp_path / TRACE).read_text(encoding="utf-8"))


# ----------------------------------------------------------------- the windows

def test_the_trace_records_every_window_and_its_status(tmp_path):
    cfg = Cfg(tmp_path)
    answers = [
        candidates((0, 3, 90)),
        RuntimeError("provider exploded"),
        {"ranked": [{"id": 0, "score": 95, "topic": "a"}]},
        META,
    ]
    run(answers, cfg)

    windows = trace_of(tmp_path)["windows"]
    assert [w["status"] for w in windows] == ["answered", "failed"]
    assert "provider exploded" in windows[1]["error"]
    assert windows[0]["error"] is None


def test_the_trace_keeps_each_window_s_candidates(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    windows = trace_of(tmp_path)["windows"]
    first = windows[0]["candidates"]
    assert len(first) == 2
    assert {"b0", "b1", "score", "gist", "kind"} <= set(first[0])
    assert first[0]["gist"] == "a thing happens"


def test_a_window_names_the_beats_it_covered(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    window = trace_of(tmp_path)["windows"][0]
    assert window["index"] == 1
    assert window["lo"] == 0
    assert window["hi"] > window["lo"]


def test_a_cached_window_is_marked_cached(tmp_path):
    """A cache hit is not the same event as a fresh answer, and a trace that
    called them both "answered" would make a rerun unreadable."""
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    run([
        {"ranked": [{"id": 0, "score": 95, "topic": "a"},
                    {"id": 1, "score": 80, "topic": "b"},
                    {"id": 2, "score": 70, "topic": "c"}]},
        META, META, META,
    ], cfg)

    assert [w["status"] for w in trace_of(tmp_path)["windows"]] == ["cached", "cached"]


# -------------------------------------------------------------- the rejections

def test_the_trace_records_snap_rejections_in_full(tmp_path):
    """The log prints five and a count. Truncation is right for a console and
    wrong for the record.

    Every candidate here is refused: the transcript is three sentences, far
    short of the 15s tiktok minimum, with nothing adjacent to grow into. So
    the analysis fails -- which is exactly the run whose reasons someone will
    come looking for.
    """
    import pytest

    cfg = Cfg(tmp_path)
    eight = {"candidates": [
        {"b0": i, "b1": i, "score": 60, "gist": "g", "kind": "story"}
        for i in range(3)
    ]}

    with pytest.raises(analyzer.AnalysisError):
        run([eight], cfg, segments=segmen(3))

    trace = trace_of(tmp_path)
    assert trace["error"] is True
    assert len(trace["rejections"]) == 3
    assert all(r["reason"] for r in trace["rejections"])
    assert "15s" in trace["rejections"][0]["reason"] or "of speech" in trace["rejections"][0]["reason"]


def test_the_trace_survives_a_run_where_nothing_was_read(tmp_path):
    """DEC-055's case: every window failed. The trace says which, and why."""
    import pytest

    cfg = Cfg(tmp_path)
    with pytest.raises(analyzer.AnalysisError):
        run([RuntimeError("provider exploded"),
             RuntimeError("provider exploded again")], cfg)

    trace = trace_of(tmp_path)
    assert [w["status"] for w in trace["windows"]] == ["failed", "failed"]
    assert trace["stats"]["answered"] == 0
    assert "provider exploded" in trace["windows"][0]["error"]


# --------------------------------------------------------------- what shipped

def test_the_trace_records_what_was_selected(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    selected = trace_of(tmp_path)["selected"]
    assert len(selected) == 3
    assert [s["rank"] for s in selected] == [1, 2, 3]
    for entry in selected:
        assert entry["end"] > entry["start"]
        assert entry["gist"] == "a thing happens"
        assert entry["kind"] == "story"


def test_the_trace_records_the_scan_stats_and_the_question(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    trace = trace_of(tmp_path)
    assert trace["stats"]["total"] == 2
    assert trace["stats"]["answered"] == 2
    assert trace["preset"] == "tiktok"
    assert trace["chain"] == "groq/m"
    assert trace["prompt_version"]


# --------------------------------------------------------------- it must not bite

def test_an_unwritable_outputs_dir_does_not_fail_the_run(tmp_path):
    """A trace is a record, never a gate."""
    blocked = tmp_path / "file-not-a-dir"
    blocked.write_text("x", encoding="utf-8")

    cfg = Cfg(blocked)
    clips, _ = run(FULL, cfg)
    assert len(clips) == 3


def test_no_outputs_dir_means_no_trace(tmp_path):
    cfg = Cfg(tmp_path)
    del cfg.outputs_dir

    clips, _ = run(FULL, cfg)
    assert len(clips) == 3
    assert not (tmp_path / TRACE).exists()


def test_a_failed_run_still_leaves_no_half_written_trace(tmp_path):
    """Analysis raising must not leave a file that reads as a finished run."""
    import pytest

    cfg = Cfg(tmp_path)
    with pytest.raises(analyzer.AnalysisError):
        run([{"candidates": []}, {"candidates": []}], cfg)

    if (tmp_path / TRACE).exists():
        json.loads((tmp_path / TRACE).read_text(encoding="utf-8"))
