#!/usr/bin/env python3
"""Measure each link of an LLM chain on the request a job will actually send.

Free tiers differ by more than an order of magnitude in generation speed, and
speed is the constraint that actually decides this pipeline's design: NVIDIA NIM
was measured at ~12-13 tokens/s behind a gateway that cuts a request off at
~300s, which is why the old single-request analysis could never finish. Rather
than trusting a published number, measure.

**And measure whether it finds clips, not only whether it replies (DEC-058).**
Every request here is the real pass-A request, built by the same function the
preflight uses (``clipping.analysis.diagnostic.pass_a_work``). By default it is
sent on a 14-beat test transcript with exactly one clip in it, and each answer
is judged on whether it found that clip. ``--transcript`` sends real windows of
a real transcript instead. This tool used to send 45 copies of one sentence
under a hand-copied schema -- the fabricated input a useless model once passed.

Every row is ONE http request per sample (``max_retries=0``), so the elapsed
time is the provider's, not a hidden retry ladder's.

    python tools/bench_llm.py
    python tools/bench_llm.py --chain "gemini/gemini-3.5-flash-lite,openrouter/meta-llama/llama-3.3-70b-instruct"
    python tools/bench_llm.py --transcript uploads/subtitles.vtt --windows 3
    python tools/bench_llm.py --samples 5 --json results.json
    python tools/bench_llm.py --nim-shortlist      # re-pick the NIM default

Keys come from the environment (and .env, via clipping.config) and from the web
dashboard's saved settings (``data/settings.json``), which win, exactly as they
do for a job. Prints no key material. Read-only: it writes nothing unless
--json is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import namedtuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipping.analysis import diagnostic  # noqa: E402
from clipping.providers import errors, llm, pacing  # noqa: E402
from clipping.providers.registry import (  # noqa: E402
    DEFAULT_LLM_CHAIN,
    PROVIDERS,
    describe,
    env_key_for,
    parse_chain,
)

# The NIM models worth re-measuring when the default dies again, so the next
# re-pick starts from measured ground rather than from the catalogue.
#
# **Measure whether it FINDS CLIPS, not whether it replies.** Measured
# 2026-09-22 against the real Pass-A request on two real transcripts:
#
#   nvidia/nemotron-3.5-lightning-30b-a3b  20-90s  2 candidates/window  <- default
#   deepseek-ai/deepseek-v4.1-flash         8-31s  ZERO candidates, every window,
#                                                  both transcripts, at every
#                                                  structured-output level
#   z-ai/glm-5.3-flash, z-ai/glm-5.3               timed out at 240s
#   nvidia/nemotron-3-super-120b-a12b        6s    malformed JSON
#   google/gemma-4-31b-it, openai/gpt-oss-20b     hang on an 8-token request
#
# Three traps this list exists to remember:
#
# 1. **Fast, schema-valid and useless is still useless.** deepseek-v4.1-flash was
#    briefly shipped as the default on latency and schema-validity alone. It
#    answers `{"candidates": []}` in seven tokens on every real transcript,
#    including one that had previously yielded seven clips. Only a benchmark that
#    counts candidates catches that, which is what --nim-shortlist does below.
# 2. **GET /v1/models lists far more than an account can call.** Ten of the
#    models below answer 404 "Function <uuid>: Not found for account <id>". They
#    are kept here on purpose: a 404 is a result, and rediscovering it costs an
#    hour.
# 3. **The good candidates are reasoning models and are unusable with thinking
#    ON.** ``llm._NIM_REASONING_FAMILIES`` turns it off for the families measured
#    to need it. nemotron-3.5-lightning was once rejected as "reasoning prose,
#    unparseable" purely because it was missing from that list -- a model
#    disqualified by a flag. Check that list before judging a new candidate.
NIM_SHORTLIST = (
    "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/deepseek-ai/deepseek-v4.1-flash",
    "nvidia/z-ai/glm-5.3-flash",
    "nvidia/nvidia/nemotron-3-super-120b-a12b",
    "nvidia/openai/gpt-oss-20b",
    "nvidia/google/gemma-4-31b-it",
    "nvidia/moonshotai/kimi-k3",
)

# One thing to send: a label for the report, the work dict, and a judge that
# says whether the answer found the clip (``None`` when nothing knows the
# right answer, as for a real transcript's window).
Request = namedtuple("Request", "label work judge")


def build_requests(*, transcript=None, windows=3, language=None, preset=None):
    """The requests each link is sent, in order.

    Without *transcript*: the diagnostic's fixture, judged. With one: up to
    *windows* real pass-A windows spread across it, the way the scan sees them.
    """
    if not transcript:
        return [Request("fixture", diagnostic.diagnostic_work(), diagnostic.judge)]

    from clipping.analysis import analyzer, beats as beats_mod
    from clipping.analysis import presets as presets_mod
    from clipping.analysis.langdetect import detect
    from clipping.transcript import load_transcript

    text, data_segmen = load_transcript(transcript)
    all_beats = beats_mod.build_beats(data_segmen)
    ranges = beats_mod.windows(
        all_beats, size=analyzer.WINDOW_BEATS, overlap=analyzer.WINDOW_OVERLAP
    )
    if not ranges:
        raise SystemExit(f"{transcript}: no beats could be built from it.")
    if language in (None, "", "auto"):
        language, _confidence = detect(text)

    # Spread across the video: the first window is often an intro, and a model
    # that only ever sees intros is being judged on the easy case.
    count = max(1, min(int(windows or 1), len(ranges)))
    if count == 1:
        picked = [0]
    else:
        step = (len(ranges) - 1) / (count - 1)
        picked = sorted({round(k * step) for k in range(count)})

    out = []
    for index in picked:
        lo, hi = ranges[index]
        work = diagnostic.pass_a_work(
            beats_mod.render_beats(all_beats, lo, hi),
            preset=presets_mod.get(preset or presets_mod.DEFAULT_PRESET),
            language=language,
            total_seconds=all_beats[-1]["end"],
        )
        out.append(Request(f"window {index + 1}/{len(ranges)} (#{lo}-#{hi})", work, None))
    return out


def _keys(settings_file=None):
    """Provider -> key, resolved the way a web job resolves it.

    The dashboard's saved settings win over the environment, because that is
    the order ``config_adapter.resolve_provider_keys`` applies -- a bench that
    read only ``.env`` would measure keys a job never uses.
    """
    try:
        from clipping import config  # noqa: F401  (loads .env as a side effect)
    except Exception:
        pass
    saved = {}
    try:
        from web.api import settings_store

        saved = settings_store.load(settings_file)
    except Exception:  # noqa: BLE001 - a missing store just means env only
        saved = {}
    return {
        name: (saved.get(provider.env_key) or os.environ.get(provider.env_key, "")).strip()
        for name, provider in PROVIDERS.items()
    }


def bench_link(link, key, requests, *, samples, max_tokens, timeout, verbose):
    """Return a result dict for one link: every request, *samples* times."""
    label = describe(link)
    row = {
        "link": label,
        "ok": 0,
        "failed": 0,
        "latencies": [],
        "out_tokens": [],
        "structured": None,
        "error": None,
        "candidates": [],
        "found": 0,
        "judged": 0,
        "truncated": 0,
        "notes": [],
    }

    # A fresh limiter per link: pacing between samples is desirable, but the
    # process-wide one would also carry state from a previous link.
    limiter = pacing.Limiter(
        link.provider,
        rpm=PROVIDERS[link.provider].rpm,
        tpm=PROVIDERS[link.provider].tpm,
    )
    client = llm.LlmClient(
        link,
        api_key=key,
        timeout=timeout or None,
        limiter=limiter,
        on_log=(lambda *a: None) if not verbose else print,
    )

    for _ in range(samples):
        for request in requests:
            work = request.work
            budget = max_tokens or work["max_tokens"]
            started = time.monotonic()
            try:
                value = client.complete_json(
                    system=work["system"],
                    user=work["user"],
                    schema=work["schema"],
                    schema_name=work["schema_name"],
                    max_tokens=budget,
                    temperature=0.2,
                )
            except Exception as exc:  # noqa: BLE001 - reported, never raised
                elapsed = time.monotonic() - started
                row["failed"] += 1
                if row["error"] is None:
                    row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                    row["classified"] = errors.classify(exc)
                row["latencies"].append(elapsed)
                continue

            elapsed = time.monotonic() - started
            usage = client.last_usage
            out = getattr(usage, "completion_tokens", None)
            row["ok"] += 1
            row["latencies"].append(elapsed)
            if out:
                row["out_tokens"].append(out)
                # At the cap the JSON was cut off, or thinking ate the budget;
                # either way the model did not finish the job it was given.
                if out >= budget:
                    row["truncated"] += 1
            row["structured"] = llm.negotiated_level(link)
            if request.judge is not None:
                verdict = request.judge(value)
                row["judged"] += 1
                row["candidates"].append(verdict.candidates)
                row["found"] += int(verdict.found_moment)
                if verdict.note and verdict.note not in row["notes"]:
                    row["notes"].append(verdict.note)
            else:
                n = len(value.get("candidates", [])) if isinstance(value, dict) else 0
                row["candidates"].append(n)

    return row


def _fmt(row):
    lat = row["latencies"]
    best = min(lat) if lat else 0.0
    mean = sum(lat) / len(lat) if lat else 0.0
    tps = ""
    if row["ok"] and row["out_tokens"] and mean:
        tps = f"{sum(row['out_tokens']) / sum(lat[: row['ok']] or [1]):6.1f}"
    status = "ok" if row["ok"] and not row["failed"] else (
        "partial" if row["ok"] else "FAIL"
    )
    cands = ",".join(str(n) for n in row.get("candidates") or []) or "-"
    found = f"{row['found']}/{row['judged']}" if row.get("judged") else "-"
    trunc = str(row.get("truncated") or 0)
    detail = row["error"] or "; ".join(row.get("notes") or [])
    return (
        f"{row['link'][:44]:<44} {status:<8} "
        f"{best:6.1f}s {mean:6.1f}s {tps:>7} {str(row['structured'] or '-'):<12} "
        f"{cands:<10} {found:<6} {trunc:<5} {detail[:70]}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--chain",
        default=os.environ.get("LLM_CHAIN", "").strip() or DEFAULT_LLM_CHAIN,
        help="Comma-separated <provider>/<model> links to benchmark.",
    )
    parser.add_argument(
        "--nim-shortlist",
        action="store_true",
        help="Benchmark NIM_SHORTLIST instead of --chain, to re-pick the NIM default.",
    )
    parser.add_argument("--transcript", metavar="PATH",
                        help="Send real pass-A windows of this transcript instead "
                             "of the built-in test transcript.")
    parser.add_argument("--windows", type=int, default=3,
                        help="With --transcript: how many windows, spread across "
                             "the video (default 3).")
    parser.add_argument("--language", default="auto",
                        help="With --transcript: the language code for the prompt "
                             "(default: detected).")
    parser.add_argument("--samples", type=int, default=3,
                        help="Times each request is sent per link (default 3).")
    parser.add_argument("--max-tokens", type=int, default=0,
                        help="Output budget per request, 0 = the scan's own (default).")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Per-request timeout, 0 = the provider's default.")
    parser.add_argument("--settings-file", metavar="PATH",
                        help="The dashboard's settings file (default: data/settings.json).")
    parser.add_argument("--json", metavar="PATH", help="Also write raw results here.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    chain = parse_chain(NIM_SHORTLIST if args.nim_shortlist else args.chain)
    keys = _keys(args.settings_file)
    requests = build_requests(
        transcript=args.transcript, windows=args.windows, language=args.language
    )

    first = requests[0].work
    budget = args.max_tokens or first["max_tokens"]
    print(f"Request: the real pass-A request, ~"
          f"{pacing.estimate_tokens(first['system'], first['user'])} tokens in, "
          f"{budget} max out.")
    print(f"Sending {len(requests)} request(s) x {args.samples} sample(s) per link: "
          + ", ".join(r.label for r in requests))
    print("Each is ONE http request (the SDK's own retries are off). "
          "'found' = answers that found the test transcript's clip; "
          "'trunc' = answers that hit the output cap.\n")
    print(f"{'link':<44} {'status':<8} {'best':>7} {'mean':>7} {'tok/s':>7} "
          f"{'structured':<12} {'cands':<10} {'found':<6} {'trunc':<5} detail")
    print("-" * 150)

    rows = []
    for link in chain:
        key = keys.get(link.provider, "")
        if not key:
            print(f"{describe(link)[:44]:<44} {'no key':<8} "
                  f"{'':>7} {'':>7} {'':>7} {'-':<12} "
                  f"set {env_key_for(link)}")
            rows.append({"link": describe(link), "ok": 0, "failed": 0,
                         "error": f"{env_key_for(link)} not set", "latencies": [],
                         "out_tokens": [], "structured": None})
            continue

        row = bench_link(
            link, key, requests,
            samples=args.samples,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            verbose=args.verbose,
        )
        rows.append(row)
        print(_fmt(row))

    working = [r for r in rows if r["ok"]]
    print()
    if working:
        fastest = min(working, key=lambda r: min(r["latencies"]))
        print(f"Fastest that answered: {fastest['link']} "
              f"({min(fastest['latencies']):.1f}s best of {len(fastest['latencies'])}).")
        useful = [r for r in working if not r.get("judged") or r["found"]]
        if len(useful) < len(working):
            print("Answered but never found the test clip (do not ship these): "
                  + ", ".join(r["link"] for r in working if r not in useful))
        print("Suggested LLM_CHAIN (fastest useful first):")
        order = sorted(useful or working, key=lambda r: min(r["latencies"]))
        print("  LLM_CHAIN=" + ",".join(r["link"] for r in order))
    else:
        print("No link answered. Check the keys named above.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nRaw results written to {args.json}")

    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(main())
