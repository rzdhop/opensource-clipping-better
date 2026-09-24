"""The diagnostic's request is the scan's request, and its judge can tell.

DEC-058: a model that answered a fabricated transcript fast and schema-valid
answered every real one with nothing. A probe earns its name only if it sends
what a job sends and can fail a model that does not do the job.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from clipping.analysis import analyzer, diagnostic, presets, prompts, schema
from clipping.analysis.beats import render_beats

ROOT = Path(__file__).resolve().parents[1]


def test_the_diagnostic_sends_the_real_pass_a_request():
    work = diagnostic.diagnostic_work()

    assert work["system"] is prompts.SYSTEM
    assert work["schema"] is schema.CANDIDATES_SCHEMA
    assert work["schema_name"] == "candidates"
    assert work["max_tokens"] == schema.MAX_TOKENS_CANDIDATES
    assert work["user"] == prompts.candidates_prompt(
        render_beats(diagnostic.FIXTURE_BEATS),
        max_candidates=analyzer.MAX_CANDIDATES_PER_WINDOW,
        preset=presets.get(presets.DEFAULT_PRESET),
        language="en",
        total_seconds=diagnostic.FIXTURE_BEATS[-1]["end"],
        topic="",
    )


def _vtt(path):
    lines = ["WEBVTT", ""]
    t = 0.0
    for i in range(60):
        lines += [
            f"00:{int(t // 60):02d}:{t % 60:06.3f} --> 00:{int((t + 2.5) // 60):02d}:{(t + 2.5) % 60:06.3f}",
            f"Sentence number {i} says something worth hearing.",
            "",
        ]
        t += 2.8
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def test_the_preflight_builds_its_request_with_the_same_function(tmp_path, monkeypatch):
    """One builder, so the preflight, the bench and the settings test cannot
    drift: the preflight's own request must come out of ``pass_a_work``."""
    from clipping import config

    seen = []
    real = diagnostic.pass_a_work

    def spy(*args, **kwargs):
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(diagnostic, "pass_a_work", spy)
    cfg = SimpleNamespace(
        transcript_path=_vtt(tmp_path / "t.vtt"),
        outputs_dir=str(tmp_path),
        platform="tiktok",
        output_language="en",
        topic="",
    )
    work, _seed = config._preflight_work(cfg, chain=[])

    assert len(seen) == 1
    assert work == real(*seen[0][0], **seen[0][1])
    assert seen[0][1]["preset"] == presets.get("tiktok")


def test_the_fixtures_moment_fits_the_default_preset():
    preset = presets.get(presets.DEFAULT_PRESET)
    lo, hi = diagnostic.STORY_SPAN
    beats = {b["i"]: b for b in diagnostic.FIXTURE_BEATS}
    length = beats[hi]["end"] - beats[lo]["start"]
    assert preset.min <= length <= preset.max


def test_the_fixture_is_contiguous_and_numbered_like_a_scan_window():
    beats = diagnostic.FIXTURE_BEATS
    assert [b["i"] for b in beats] == list(range(len(beats)))
    for before, after in zip(beats, beats[1:]):
        assert before["end"] == after["start"]
        assert before["start"] < before["end"]


def test_no_two_fixture_beats_are_the_same():
    """Repetition is the fabricated input DEC-058 warns about."""
    texts = [b["text"].lower() for b in diagnostic.FIXTURE_BEATS]
    assert len(set(texts)) == len(texts)


def test_the_story_is_not_the_prompts_own_worked_example():
    story = " ".join(
        b["text"] for b in diagnostic.FIXTURE_BEATS
        if diagnostic.STORY_SPAN[0] <= b["i"] <= diagnostic.STORY_SPAN[1]
    ).lower()
    assert "forty thousand" not in story
    assert "forty thousand" in prompts.candidates_prompt("", preset=None).lower()


def _answer(*spans):
    return {"candidates": [
        {"b0": b0, "b1": b1, "score": 80, "gist": "g", "kind": "story"}
        for b0, b1 in spans
    ]}


def test_judge_counts_only_in_window_candidates_and_spots_the_moment():
    hit = diagnostic.judge(_answer((4, 9)))
    assert hit == (1, True, "")

    # One beat either side still found it.
    assert diagnostic.judge(_answer((3, 10))).found_moment
    assert diagnostic.judge(_answer((5, 8))).found_moment

    nothing = diagnostic.judge({"candidates": []})
    assert nothing.candidates == 0 and not nothing.found_moment
    assert "found no moment" in nothing.note

    housekeeping = diagnostic.judge(_answer((0, 1), (12, 13)))
    assert housekeeping.candidates == 2 and not housekeeping.found_moment
    assert "intro and outro" in housekeeping.note

    elsewhere = diagnostic.judge(_answer((6, 11)))
    assert elsewhere.candidates == 1 and not elsewhere.found_moment
    assert "not the story" in elsewhere.note

    # An id outside the window is the model guessing; the scan drops it, so
    # the judge does not count it either.
    invented = diagnostic.judge(_answer((4, 40)))
    assert invented.candidates == 0 and not invented.found_moment


def test_judge_survives_a_malformed_answer():
    assert diagnostic.judge(None).candidates == 0
    assert diagnostic.judge({"candidates": [{"b0": "x"}]}).candidates == 0


def _bench():
    spec = importlib.util.spec_from_file_location(
        "bench_llm", ROOT / "tools" / "bench_llm.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bench_sends_the_diagnostics_request():
    bench = _bench()
    requests = bench.build_requests()
    assert len(requests) == 1
    assert requests[0].work == diagnostic.diagnostic_work()
    assert requests[0].judge is diagnostic.judge
    # The fabricated input is gone, not merely unused.
    assert not hasattr(bench, "BEATS")
    assert not hasattr(bench, "SCHEMA")


def test_the_bench_spreads_real_windows_across_a_transcript(tmp_path):
    bench = _bench()
    path = tmp_path / "long.vtt"
    lines = ["WEBVTT", ""]
    t = 0.0
    for i in range(400):
        lines += [
            f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f} --> "
            f"{int((t + 2.5) // 3600):02d}:{int((t + 2.5) % 3600 // 60):02d}:{(t + 2.5) % 60:06.3f}",
            f"Line {i} is a separate thought with its own words.",
            "",
        ]
        t += 2.8
    path.write_text("\n".join(lines), encoding="utf-8")

    requests = bench.build_requests(transcript=str(path), windows=3, language="en")

    assert len(requests) == 3
    assert requests[0].label.startswith("window 1/")
    total = requests[0].label.split("/")[1].split(" ")[0]
    assert requests[-1].label.startswith(f"window {total}/")
    for request in requests:
        assert request.judge is None
        assert request.work["schema"] is schema.CANDIDATES_SCHEMA
        assert "BEATS:" in request.work["user"]


def test_the_bench_reads_the_dashboards_saved_keys(tmp_path, monkeypatch):
    """A job resolves the dashboard's saved key before the environment; a
    bench that read only .env would measure keys a job never uses."""
    bench = _bench()
    store = tmp_path / "settings.json"
    store.write_text('{"OPENROUTER_API_KEY": "saved-value"}', encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-value")
    monkeypatch.setenv("GROQ_API_KEY", "env-groq")

    keys = bench._keys(str(store))

    assert keys["openrouter"] == "saved-value"
    assert keys["groq"] == "env-groq"
