#!/usr/bin/env python3
"""Time each link of an LLM chain against its real endpoint.

Free tiers differ by more than an order of magnitude in generation speed, and
speed is the constraint that actually decides this pipeline's design: NVIDIA NIM
was measured at ~12-13 tokens/s behind a gateway that cuts a request off at
~300s, which is why the old single-request analysis could never finish. Rather
than trusting a published number, measure.

Every row is ONE http request (``max_retries=0``), so the elapsed time is the
provider's, not a hidden retry ladder's.

    python tools/bench_llm.py
    python tools/bench_llm.py --chain "groq/openai/gpt-oss-120b,groq/llama-3.3-70b-versatile"
    python tools/bench_llm.py --samples 5 --json results.json

Reads keys from the environment (and from .env, via clipping.config). Prints no
key material. Read-only: it writes nothing unless --json is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipping.providers import errors, llm, pacing  # noqa: E402
from clipping.providers.registry import (  # noqa: E402
    DEFAULT_LLM_CHAIN,
    PROVIDERS,
    describe,
    env_key_for,
    parse_chain,
)

# A transcript-shaped prompt of roughly the size Pass A actually sends, so the
# measurement reflects the real workload rather than a toy "hello".
BEATS = "\n".join(
    f"#{i} [{i * 7.5:.1f}-{i * 7.5 + 6.9:.1f}] "
    "and that is the moment everything changed for the team, because nobody "
    "expected the result to hold up under that much pressure"
    for i in range(45)
)

SYSTEM = (
    "You are a short-form video editor. Return JSON only, matching the schema."
)

USER = (
    "Pick the three strongest self-contained moments from these transcript "
    "beats. Answer with a JSON object holding a 'candidates' array; each item "
    "has integer b0 and b1 (beat ids), an integer score 1-100, a 'gist' of at "
    "most 12 words, and a 'kind' from story/insight/conflict/howto/punchline.\n\n"
    f"{BEATS}"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "b0": {"type": "integer"},
                    "b1": {"type": "integer"},
                    "score": {"type": "integer"},
                    "gist": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["story", "insight", "conflict", "howto", "punchline"],
                    },
                },
                "required": ["b0", "b1", "score", "gist", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


def _keys():
    """Provider -> key, from the environment (.env is loaded by clipping.config)."""
    try:
        from clipping import config  # noqa: F401  (loads .env as a side effect)
    except Exception:
        pass
    return {
        name: os.environ.get(provider.env_key, "").strip()
        for name, provider in PROVIDERS.items()
    }


def bench_link(link, key, *, samples, max_tokens, timeout, verbose):
    """Return a result dict for one link, timing *samples* single requests."""
    label = describe(link)
    row = {
        "link": label,
        "ok": 0,
        "failed": 0,
        "latencies": [],
        "out_tokens": [],
        "structured": None,
        "error": None,
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
        started = time.monotonic()
        try:
            value = client.complete_json(
                system=SYSTEM,
                user=USER,
                schema=SCHEMA,
                schema_name="candidates",
                max_tokens=max_tokens,
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
        row["structured"] = llm.negotiated_level(link)
        n = len(value.get("candidates", [])) if isinstance(value, dict) else 0
        row.setdefault("candidates", []).append(n)

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
    return (
        f"{row['link'][:44]:<44} {status:<8} "
        f"{best:6.1f}s {mean:6.1f}s {tps:>7} {str(row['structured'] or '-'):<12} "
        f"{(row['error'] or '')[:60]}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--chain",
        default=os.environ.get("LLM_CHAIN", "").strip() or DEFAULT_LLM_CHAIN,
        help="Comma-separated <provider>/<model> links to benchmark.",
    )
    parser.add_argument("--samples", type=int, default=3,
                        help="Requests per link (default 3).")
    parser.add_argument("--max-tokens", type=int, default=400,
                        help="Output budget per request (default 400).")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Per-request timeout, 0 = the provider's default.")
    parser.add_argument("--json", metavar="PATH", help="Also write raw results here.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    chain = parse_chain(args.chain)
    keys = _keys()

    print(f"Prompt: ~{pacing.estimate_tokens(SYSTEM, USER)} tokens in, "
          f"{args.max_tokens} max out, {args.samples} sample(s) per link.")
    print("Each row is ONE http request per sample (the SDK's own retries are off).\n")
    print(f"{'link':<44} {'status':<8} {'best':>7} {'mean':>7} {'tok/s':>7} "
          f"{'structured':<12} error")
    print("-" * 120)

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
            link, key,
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
        print("Suggested LLM_CHAIN (fastest first, others as fallback):")
        order = sorted(working, key=lambda r: min(r["latencies"]))
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
