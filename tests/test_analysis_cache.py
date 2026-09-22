"""The per-window scan cache.

Pass A is the expensive pass: one request per ~45-beat window, and on the
slowest link in the shipped chain that is ~90s each. Every rerun of the same
video re-paid for all of it, and a rerun is the common operation -- Clone &
Rerun, a changed render flag, a different clip count.

The cache is keyed on the window's own text plus everything that could change
the answer, so a stale entry is not something that can be served: a reworded
prompt, a different chain, a different preset and a different
max-candidates-per-window all produce different keys.

Stdlib only (DEC-012): this imports ``clipping.analysis`` and nothing heavier.
"""

import json
import pathlib

import pytest

from clipping.analysis import analyzer, cache as cache_mod, prompts
from test_analysis_windows import META, candidates, scripted, segmen


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


def run(answers, cfg, segments=None):
    logs = []
    runner = scripted(*answers)
    clips = analyzer.analyze(
        segments or segmen(),
        cfg,
        chain=[("groq", "m")],
        keys={"groq": "k"},
        on_log=logs.append,
        run_chain=runner,
    )
    return clips, logs, runner


# Two windows of scan, one rerank, three metadata.
FULL = [
    candidates((0, 3, 90), (20, 23, 70)),
    candidates((45, 48, 85)),
    {"ranked": [{"id": 0, "score": 95, "topic": "a"},
                {"id": 1, "score": 80, "topic": "b"},
                {"id": 2, "score": 70, "topic": "c"}]},
    META, META, META,
]

# The same run with pass A already known: only B and C are asked for.
CACHED = [
    {"ranked": [{"id": 0, "score": 95, "topic": "a"},
                {"id": 1, "score": 80, "topic": "b"},
                {"id": 2, "score": 70, "topic": "c"}]},
    META, META, META,
]


def _scan_calls(runner):
    return [c for c in runner.calls if c["schema_name"] == "candidates"]


# ------------------------------------------------------------- the round trip

def test_a_second_run_makes_no_pass_a_requests(tmp_path):
    cfg = Cfg(tmp_path)

    _, _, first = run(FULL, cfg)
    assert len(_scan_calls(first)) == 2

    # scripted() raises if the analyzer asks for more than it was given, so
    # this second run proves the scan was skipped rather than merely repeated.
    clips, logs, second = run(CACHED, cfg)

    assert _scan_calls(second) == []
    assert len(clips) == 3
    assert sum("from cache" in line for line in logs) == 2


def test_the_cache_file_is_written_where_the_job_lives(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    path = tmp_path / cache_mod.CACHE_FILENAME
    assert path.is_file()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == cache_mod.CACHE_VERSION
    assert len(data["entries"]) == 2


def test_a_cached_window_counts_as_answered(tmp_path):
    """DEC-055: ``answered == 0`` means "nothing was ever read" and raises. A
    cached window WAS read, just not today."""
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    clips, _, _ = run(CACHED, cfg)
    assert len(clips) == 3


# --------------------------------------------------------------- invalidation

def test_a_changed_prompt_version_invalidates_every_entry(tmp_path, monkeypatch):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    monkeypatch.setattr(prompts, "PROMPT_VERSION", "different")
    _, _, runner = run(FULL, cfg)

    assert len(_scan_calls(runner)) == 2


def test_a_changed_chain_invalidates_the_entry(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    logs = []
    runner = scripted(*FULL)
    analyzer.analyze(
        segmen(), cfg,
        chain=[("nvidia", "other-model")],
        keys={"nvidia": "k"},
        on_log=logs.append,
        run_chain=runner,
    )
    assert len(_scan_calls(runner)) == 2


def test_a_changed_preset_invalidates_the_entry(tmp_path):
    """A different duration window is a different question: the prompt states
    the target length, and the answer is chosen to fit it."""
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    cfg.platform = "shorts"
    _, _, runner = run(FULL, cfg)
    assert len(_scan_calls(runner)) == 2


def test_a_different_transcript_does_not_hit(tmp_path):
    cfg = Cfg(tmp_path)
    run(FULL, cfg)

    _, _, runner = run(FULL, cfg, segments=segmen(90))
    assert _scan_calls(runner), "a different transcript must be re-scanned"


# --------------------------------------------------------------- it must not bite

def test_a_corrupt_cache_file_is_ignored_not_fatal(tmp_path):
    (tmp_path / cache_mod.CACHE_FILENAME).write_text("{[ not json", encoding="utf-8")
    cfg = Cfg(tmp_path)

    clips, _, runner = run(FULL, cfg)

    assert len(clips) == 3
    assert len(_scan_calls(runner)) == 2
    # And it was rewritten as something valid.
    json.loads((tmp_path / cache_mod.CACHE_FILENAME).read_text(encoding="utf-8"))


def test_a_cache_from_a_future_version_is_ignored(tmp_path):
    (tmp_path / cache_mod.CACHE_FILENAME).write_text(
        json.dumps({"version": cache_mod.CACHE_VERSION + 1, "entries": {}}),
        encoding="utf-8",
    )
    cfg = Cfg(tmp_path)
    clips, _, _ = run(FULL, cfg)
    assert len(clips) == 3


def test_no_outputs_dir_means_no_cache_and_no_crash(tmp_path):
    class Bare(Cfg):
        pass

    cfg = Bare(tmp_path)
    del cfg.outputs_dir

    clips, _, runner = run(FULL, cfg)
    assert len(clips) == 3
    assert len(_scan_calls(runner)) == 2


def test_the_cache_can_be_switched_off(tmp_path):
    cfg = Cfg(tmp_path)
    cfg.analysis_cache = False

    run(FULL, cfg)
    assert not (tmp_path / cache_mod.CACHE_FILENAME).exists()

    _, _, runner = run(FULL, cfg)
    assert len(_scan_calls(runner)) == 2


# ------------------------------------------------------------------- the store

def test_the_cache_is_bounded(tmp_path):
    """The CLI writes every run into one shared outputs/ directory, so without
    a cap this file grows for the life of the install."""
    path = tmp_path / "c.json"
    store = cache_mod.WindowCache(path, salt="s", limit=3)

    clock = [0.0]

    def tick():
        clock[0] += 1.0
        return clock[0]

    store = cache_mod.WindowCache(path, salt="s", limit=3, time_fn=tick)
    for i in range(5):
        store.put(f"window {i}", [{"b0": i, "b1": i + 1, "score": 50}])
    store.save()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 3

    # The three most recent survived; the two oldest went.
    reread = cache_mod.WindowCache(path, salt="s", limit=3)
    assert reread.get("window 0") is None
    assert reread.get("window 4") is not None


def test_an_unwritable_path_does_not_raise(tmp_path):
    store = cache_mod.WindowCache(
        tmp_path / "nope" / "\0bad" / "c.json", salt="s"
    )
    store.put("window", [])
    assert store.save() is False


def test_saving_nothing_writes_nothing(tmp_path):
    path = tmp_path / "c.json"
    store = cache_mod.WindowCache(path, salt="s")
    assert store.save() is False
    assert not path.exists()


def test_the_salt_separates_two_otherwise_identical_windows(tmp_path):
    a = cache_mod.WindowCache(tmp_path / "a.json", salt="one")
    b = cache_mod.WindowCache(tmp_path / "b.json", salt="two")
    assert a.key("same text") != b.key("same text")
