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
    python tools/bench_llm.py --episode-prompts outputs/stories/7733d759c562 --ep 1

Keys come from the environment (and .env, via clipping.config) and from the web
dashboard's saved settings (``data/settings.json``), which win, exactly as they
do for a job. Prints no key material. Read-only: it writes nothing unless
--json is given.

    python tools/bench_llm.py --episode-ab outputs/stories/<id> --dry-run \\
        --chains "gemini-paid/gemini-3.8-flash,anthropic/claude-sonnet-5-5" --max-usd 2.50

``--episode-ab <story_dir>`` (plan 23 stage D7) writes one episode with the
writing-v3 chain once per link and puts the scripts side by side (it writes
only under ``<story_dir>/bench/<timestamp>/``, plus the ledger rows of a paid
call); it is the one place this tool may spend, and only under
``--allow-paid`` and ``--max-usd``. See the "episode A/B" section below.

``--episode-prompts <story_dir>`` is a different measurement (AI Story phase
3, stage 12, spec's A-entry "JSON validity rate per provider with the
E-prompts"): instead of the pass-A clip-finding request above, it sends the
E1/E2/T1 episode-writing requests built from a real, already-written episode
of *story_dir* -- one to every **keyed free** link of the chain individually
(never through chain fallback; a paid link is skipped, DEC-115, exactly as a
story step itself leaves one out), and scores each reply on whether it
parses as JSON, validates against the prompt's own schema, and passes the
prompt's own post-validator. See :func:`build_episode_prompt_requests` and
:func:`run_episode_prompt_bench`.
"""

from __future__ import annotations

import argparse
import contextlib
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

# One episode-writing prompt to send: its id (matches
# clipping.aistory.prompts.MAX_TOKENS/TEMPERATURE/SCHEMA_NAMES), the built
# system/user/schema, the prompt's own cap and temperature, and the same
# post-validator a story step itself calls the reply against.
EpisodeRequest = namedtuple(
    "EpisodeRequest", "prompt_id system user schema schema_name max_tokens temperature validate"
)


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


def _load_settings_env(settings_file=None):
    """The dashboard's saved settings (env-var-name -> value), read the way
    a job's own config resolution does: ``clipping.config`` loads ``.env``
    first (a side effect only, it is never read from directly here), then
    ``web.api.settings_store`` if the web layer is importable. Empty when
    neither is available -- callers then fall back to the process
    environment alone, exactly as :func:`_keys` and
    ``clipping.aistory.steps.llm_call.resolve_chain``/``resolve_keys`` do.
    """
    try:
        from clipping import config  # noqa: F401  (loads .env as a side effect)
    except Exception:
        pass
    try:
        from web.api import settings_store

        return settings_store.load(settings_file)
    except Exception:  # noqa: BLE001 - a missing store just means env only
        return {}


def _keys(settings_file=None):
    """Provider -> key, resolved the way a web job resolves it.

    The dashboard's saved settings win over the environment, because that is
    the order ``config_adapter.resolve_provider_keys`` applies -- a bench that
    read only ``.env`` would measure keys a job never uses.
    """
    saved = _load_settings_env(settings_file)
    return {
        name: (saved.get(provider.env_key) or os.environ.get(provider.env_key, "")).strip()
        for name, provider in PROVIDERS.items()
    }


# ------------------------------------------------------- episode-prompt bench
#
# AI Story phase 3, stage 12 (spec's A-entry "JSON validity rate per provider
# with the E-prompts"). A different measurement from the pass-A request
# above: not "does it find the clip" but "does the reply parse as JSON,
# validate against the prompt's own schema, and pass the prompt's own
# post-validator" -- for E1 (the beat sheet), E2 (one body scene's dialogue)
# and T1 (one scene's shots), the three episode-writing prompts spec 4.2
# calls out.
#
# The requests are built the same way the step runners build them
# (``clipping.aistory.steps.script.write_beat_sheet``/``write_body_scene``,
# ``clipping.aistory.steps.storyboard.plan_scene``) -- reusing their own
# leading-underscore helpers (``script_step._cast_lines``/``_entity``/
# ``_previous_line``/``_word_budget``, ``storyboard_step.shot_inputs``/
# ``_builder_kwargs``/``_previous_shots``) exactly as ``storyboard.py``
# itself already reuses ``script_step._entity``/``_and``/``_pack`` -- rather
# than a second, drifting copy of that request-building logic. Nothing here
# calls an LLM or writes to the story.


def _e1_request(ec, context_mod, prompts_mod, timing_mod):
    pack = context_mod.build_pack(language=ec.language, story=ec.story, note=None)
    cast = [{"char_id": doc["char_id"], "name": doc["name"]} for doc in ec.cast]
    places = [
        {"place_id": pid, "name": ec.entities["places"][pid]["name"], "time_variants": variants}
        for pid, variants in ec.places.items()
    ]
    props = [{"prop_id": pid, "name": ec.entities["props"][pid]["name"]} for pid in ec.prop_ids]
    slots = timing_mod.episode_slots(ec.template, ec.ep)
    system, user, schema = prompts_mod.build_e1(
        pack, ep=ec.ep, arc_entry=ec.arc_entry, template=ec.template, episode_defaults=ec.episode_defaults,
        cast=cast, places=places, props=props, memory=ec.season, slots=slots,
    )
    cast_ids = list(ec.entities["characters"])
    prop_ids = list(ec.prop_ids)

    def validate(value):
        return prompts_mod.validate_e1(
            value, ep=ec.ep, template=ec.template, episode_defaults=ec.episode_defaults,
            cast_ids=cast_ids, places=ec.places, prop_ids=prop_ids,
        )

    return EpisodeRequest("E1", system, user, schema, prompts_mod.SCHEMA_NAMES["E1"],
                           prompts_mod.MAX_TOKENS["E1"], prompts_mod.TEMPERATURE["E1"], validate)


def _e2_request(ec, script, scene, context_mod, prompts_mod, script_step):
    sid = scene["scene_id"]
    cast = script_step._cast_lines(ec, scene["characters"], sid)
    place_doc = script_step._entity(ec, "places", scene["place_id"], sid)
    props = [{"prop_id": pid, "name": script_step._entity(ec, "props", pid, sid)["name"]} for pid in scene["props"]]
    previous = script_step._previous_line(ec, script, scene)
    word_budget = script_step._word_budget(ec, scene)
    pack = context_mod.build_pack(language=ec.language, story=ec.story, note=None)
    system, user, schema = prompts_mod.build_e2(
        pack, scene=scene, scene_number=script["scenes"].index(scene) + 1, outline=script["scenes"],
        previous=previous, word_budget=word_budget, cast=cast,
        place={"place_id": place_doc["place_id"], "name": place_doc["name"],
               "layout_notes": place_doc["layout_notes"] or ""},
        props=props, sfx_cues=ec.sfx_cues, narrator_enabled=ec.narrator,
        voice_direction=ec.style_lock["audio"]["voice_direction"],
    )

    def validate(value):
        return prompts_mod.validate_e2(value, scene=scene, narrator_enabled=ec.narrator, sfx_cues=ec.sfx_cues)

    return EpisodeRequest("E2", system, user, schema, prompts_mod.SCHEMA_NAMES["E2"],
                           prompts_mod.MAX_TOKENS["E2"], prompts_mod.TEMPERATURE["E2"], validate)


def _t1_request(ec, script, scene, plans, context_mod, prompts_mod, storyboard_step):
    inputs = storyboard_step.shot_inputs(ec, scene)
    pack = context_mod.build_pack(language=ec.language, story=ec.story, note=None)
    system, user, schema = prompts_mod.build_t1(
        pack, scene=scene, previous_shots=storyboard_step._previous_shots(script, plans, scene["scene_id"]),
        **storyboard_step._builder_kwargs(inputs),
    )

    def validate(value):
        return prompts_mod.validate_t1(
            value, scene=scene, shots_per_scene=inputs["shots_per_scene"],
            modifiers_allowed=inputs["modifiers_allowed"], tags_allowed=inputs["tags_allowed"],
            n_lines=len(scene["lines"]), names=inputs["names"],
        )

    return EpisodeRequest("T1", system, user, schema, prompts_mod.SCHEMA_NAMES["T1"],
                           prompts_mod.MAX_TOKENS["T1"], prompts_mod.TEMPERATURE["T1"], validate)


def build_episode_prompt_requests(story_dir, *, ep=1):
    """E1, E2 (one body scene) and T1 (one scene) requests, built the way
    the step runners build them (minus the call) from *story_dir* -- an
    ``outputs/stories/<id>`` folder with a written episode *ep* script (a
    storyboard is used for T1's "previous shots" context when there is one,
    else T1 is planned with none, same as a first-ever storyboard run).

    Read-only: nothing here calls an LLM or writes to *story_dir*. Raises
    ``clipping.aistory.steps.StepFailed``/``KeyError``/
    ``clipping.aistory.schemas.SchemaError`` the way
    ``episode_common.load_context`` does for a story or episode that cannot
    be read, and ``ValueError`` when the episode has no script yet or no
    body scene to build E2/T1 from.
    """
    from clipping.aistory import context as context_mod
    from clipping.aistory import prompts as prompts_mod
    from clipping.aistory import shots as shots_mod
    from clipping.aistory import timing as timing_mod
    from clipping.aistory.steps import episode_common
    from clipping.aistory.steps import script as script_step
    from clipping.aistory.steps import storyboard as storyboard_step
    from clipping.aistory.store import StoryStore

    story_dir = os.path.normpath(story_dir)
    story_id = os.path.basename(story_dir)
    outputs_dir = os.path.dirname(os.path.dirname(story_dir))
    stores = StoryStore(outputs_dir, on_log=lambda line: None)

    ec = episode_common.load_context(stores, story_id, ep)
    script = stores.read_episode_doc(story_id, ep, episode_common.SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise ValueError(f"episode {ep} of {story_id!r} has no script.json yet; write one first.")
    storyboard = stores.read_episode_doc(story_id, ep, episode_common.STORYBOARD_DOC)

    body = script_step.body_scenes(script)
    scene = next((s for s in body if s["lines"]), None) or (body[0] if body else None)
    if scene is None:
        raise ValueError(f"episode {ep} of {story_id!r} has no body scene to build E2/T1 requests from.")

    plans = shots_mod.plans_from_storyboard(storyboard, script) if storyboard is not None else {}

    return [
        _e1_request(ec, context_mod, prompts_mod, timing_mod),
        _e2_request(ec, script, scene, context_mod, prompts_mod, script_step),
        _t1_request(ec, script, scene, plans, context_mod, prompts_mod, storyboard_step),
    ]


def classify_episode_failure(exc) -> str:
    """Why one episode-prompt call never produced a scoreable reply:
    ``"timeout"`` (the provider's own timeout exceptions, or the SDK's,
    matched the same way ``clipping.providers.llm._timed_out`` diagnoses one
    elsewhere), ``"no_json"`` (``LlmClient.complete_json`` raises
    ``ValueError`` when nothing in the reply parses as JSON -- the only
    ``ValueError`` source on its call path), or
    ``"provider_error: <ExceptionClassName>"`` for anything else (a bad key,
    a rate limit, a connection failure, ...).
    """
    from clipping.providers import llm as llm_mod

    if llm_mod._timed_out(exc):
        return "timeout"
    if isinstance(exc, ValueError):
        return "no_json"
    return f"provider_error: {type(exc).__name__}"


def score_episode_reply(value, request) -> str:
    """``"ok"``, ``"schema"`` (fails *request*'s own JSON schema) or
    ``"validator: <first error>"`` (schema-valid, but *request*'s own
    post-validator -- the same one a story step calls the reply against --
    rejects it) for one already-parsed reply."""
    from clipping.aistory import schemas

    schema_errors = schemas.validate(value, request.schema)
    if schema_errors:
        return "schema"
    errors = request.validate(value)
    if errors:
        return f"validator: {errors[0]}"
    return "ok"


def select_episode_links(settings_env):
    """``(usable, skipped)``: the keyed **free** links of the chain
    *settings_env* resolves (``clipping.aistory.steps.llm_call.
    resolve_chain``/``resolve_keys``/``is_free_link``, the same functions a
    story step itself calls), and every other link with why it is left out
    -- a paid link (:data:`llm_call.PAID_SKIP_REASON`, DEC-115: AI Story
    spends only on opt-in) or a free one with no key set. The chain itself
    is never edited, reordered or trimmed (DEC-003/023) -- this only says
    which of its links this bench may call."""
    from clipping.aistory.steps import llm_call as episode_llm_call

    chain = episode_llm_call.resolve_chain(settings_env)
    keys = episode_llm_call.resolve_keys(settings_env)
    usable, skipped = [], []
    for link in chain:
        if not episode_llm_call.is_free_link(link):
            skipped.append((link, episode_llm_call.PAID_SKIP_REASON))
        elif not keys.get(link.provider):
            skipped.append((link, f"no key set ({env_key_for(link)})"))
        else:
            usable.append(link)
    return usable, skipped


def run_episode_prompt_bench(
    story_dir, *, ep=1, requests=None, settings_env=None, samples=3, timeout=None,
    client_factory=None, on_log=None, on_row=None,
):
    """Every keyed free link of the resolved chain, every episode-prompt
    request, *samples* times each, ONE http request per sample
    (``LlmClient.complete_json``, never through chain fallback), sequentially
    -- free-tier RPM survives a burst better than it survives a flood.

    *requests* defaults to :func:`build_episode_prompt_requests`\\ 's own
    (built once, so a caller that already printed them is not charged a
    second read of *story_dir*). *settings_env* defaults to
    :func:`_load_settings_env`\\ 's own (no argument: ``data/settings.json``
    over the process environment, exactly as a job resolves it). *timeout*
    is the per-call timeout in seconds (default
    ``clipping.aistory.steps.llm_call.STORY_CALL_BUDGET_SECONDS``, the same
    300 s budget one story call gets). *client_factory(link, key)* builds
    one link's client (default: a fresh ``llm.LlmClient`` with its own
    ``pacing.Limiter``, mirroring :func:`bench_link`); a test replaces it so
    nothing reaches the network. *on_row(row)* is called after every scored
    call, for a caller that wants to print progress as it happens.

    Returns ``(rows, usable, skipped)``. Each row:
    ``{"link", "prompt", "rep", "class", "latency", "tokens"}`` -- *tokens*
    is the ≈chars/4 estimate of the parsed reply (``None`` when nothing
    parsed). No key, prompt text or reply text is ever in a row.
    """
    from clipping.aistory.steps import llm_call as episode_llm_call
    from clipping.providers import llm as llm_mod

    if requests is None:
        requests = build_episode_prompt_requests(story_dir, ep=ep)
    settings_env = _load_settings_env() if settings_env is None else settings_env
    usable, skipped = select_episode_links(settings_env)
    keys = episode_llm_call.resolve_keys(settings_env)
    call_timeout = timeout or episode_llm_call.STORY_CALL_BUDGET_SECONDS

    def default_factory(link, key):
        limiter = pacing.Limiter(link.provider, rpm=PROVIDERS[link.provider].rpm, tpm=PROVIDERS[link.provider].tpm)
        return llm_mod.LlmClient(
            link, api_key=key, timeout=call_timeout, limiter=limiter,
            on_log=on_log or (lambda *_a: None),
        )

    make_client = client_factory or default_factory

    rows = []
    for link in usable:
        client = make_client(link, keys[link.provider])
        for request in requests:
            for rep in range(1, samples + 1):
                started = time.monotonic()
                try:
                    value = client.complete_json(
                        system=request.system, user=request.user, schema=request.schema,
                        schema_name=request.schema_name, max_tokens=request.max_tokens,
                        temperature=request.temperature,
                    )
                except Exception as exc:  # noqa: BLE001 - scored, never raised
                    row = {"link": describe(link), "prompt": request.prompt_id, "rep": rep,
                           "class": classify_episode_failure(exc), "latency": time.monotonic() - started,
                           "tokens": None}
                else:
                    elapsed = time.monotonic() - started
                    tokens = pacing.estimate_tokens(json.dumps(value, ensure_ascii=False))
                    row = {"link": describe(link), "prompt": request.prompt_id, "rep": rep,
                           "class": score_episode_reply(value, request), "latency": elapsed, "tokens": tokens}
                rows.append(row)
                if on_row is not None:
                    on_row(row)
    return rows, usable, skipped


def _episode_summary(rows):
    """One row per (link, prompt): ok/total, median latency, median
    ≈tokens-out, and the distinct non-``"ok"`` classes seen -- the table the
    A-entry wants."""
    import statistics
    from collections import defaultdict

    groups = defaultdict(list)
    for row in rows:
        groups[(row["link"], row["prompt"])].append(row)

    summary = []
    for (link, prompt), group in groups.items():
        latencies = [r["latency"] for r in group]
        tokens = [r["tokens"] for r in group if r["tokens"] is not None]
        summary.append({
            "link": link, "prompt": prompt,
            "ok": sum(1 for r in group if r["class"] == "ok"), "total": len(group),
            "median_latency": statistics.median(latencies) if latencies else 0.0,
            "median_tokens": statistics.median(tokens) if tokens else None,
            "classes": sorted({r["class"] for r in group if r["class"] != "ok"}),
        })
    return summary


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


def _run_episode_prompt_mode(args) -> int:
    """``--episode-prompts``: never falls through to the pass-A path above."""
    requests = build_episode_prompt_requests(args.episode_prompts, ep=args.ep)
    settings_env = _load_settings_env(args.settings_file)
    usable, skipped = select_episode_links(settings_env)

    print(f"Episode prompts from {args.episode_prompts} (episode {args.ep}): "
          + ", ".join(r.prompt_id for r in requests))
    print("Keyed free link(s): " + (", ".join(describe(link) for link in usable) or "none") + ".")
    for link, reason in skipped:
        print(f"Skipped {describe(link)}: {reason}")

    samples = args.samples
    estimate = len(usable) * len(requests) * samples
    if estimate > 40:
        reduced = 2
        print(f"\n{len(usable)} link(s) x {len(requests)} prompt(s) x {samples} repetition(s) = {estimate} calls "
              f"(> 40): reducing to {reduced} repetitions.")
        samples = reduced
        estimate = len(usable) * len(requests) * samples
    print(f"Estimated calls: {len(usable)} link(s) x {len(requests)} prompt(s) x {samples} repetition(s) "
          f"= {estimate}.\n")

    if not usable:
        print("No keyed free link to bench: set one of the keys named above, or turn a paid link's chain "
              "entry into a free one.")
        return 1

    print(f"{'link':<36} {'prompt':<6} {'rep':<4} {'class':<40} {'latency':>8} {'tokens':>7}")
    print("-" * 110)

    def on_row(row):
        tokens = "-" if row["tokens"] is None else str(row["tokens"])
        print(f"{row['link'][:36]:<36} {row['prompt']:<6} {row['rep']:<4} {row['class'][:40]:<40} "
              f"{row['latency']:7.1f}s {tokens:>7}")

    rows, usable, skipped = run_episode_prompt_bench(
        args.episode_prompts, ep=args.ep, requests=requests, settings_env=settings_env, samples=samples,
        timeout=args.timeout, on_row=on_row, on_log=(print if args.verbose else None),
    )

    print(f"\n{'link':<36} {'prompt':<6} {'ok/total':<10} {'median_s':>9} {'median_tok':>11}  classes seen")
    print("-" * 110)
    for row in _episode_summary(rows):
        ok_total = f"{row['ok']}/{row['total']}"
        median_tok = "-" if row["median_tokens"] is None else str(row["median_tokens"])
        print(f"{row['link'][:36]:<36} {row['prompt']:<6} {ok_total:<10} {row['median_latency']:9.1f} "
              f"{median_tok:>11}  " + (", ".join(row["classes"]) or "-"))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
        print(f"\nRaw results written to {args.json}")

    return 0 if rows else 1


# ------------------------------------------------------- episode A/B (plan 23 stage D7)
#
# ``--episode-ab <story_dir> --chains "<link>,<link>,..."``: the writing-v3
# chain of ONE episode (E1v3 -> E2v3 per body scene -> E3v3 -> J1v3) run once
# per link, each link alone (a one-link chain: no fallback to another link),
# the scripts and the judge's verdicts side by side, so the human can read
# them and rate the models (complete lines, the hook, the validator pass
# rate, retries, real cost, latency).
#
# It reuses the script step's own methods (``steps/script._Run.beat_sheet`` /
# ``body`` / ``framing`` / ``first_watch``: the prompts, the validators, the
# retry-once-then-fail ladder of ``llm_call.call_json``, the meter), and
# nothing else of the step (no fill pass, no E4, no repair: the A/B reads
# what each model wrote, not what a repair loop made of it). The step writes
# its script after every accepted call, so it runs against a throwaway COPY
# of the story's JSON documents in a temp folder (the story's own episode
# files are never read for writing and never written; an existing script of
# the episode is not in the copy: the episode is written afresh, from the
# season and, from episode 2 on, the memory of the episodes before it). The
# copy is stamped ``writing: "v3"`` when the story is not (it is in the
# copy only, and the summary says so).
#
# **An explicit exception to the bench's skip-paid rule (DEC-115).** A paid
# link is called only with ``--allow-paid`` AND ``allow_paid`` on in Settings
# (the Settings switch wins), under a ``--max-usd`` that is a hard cap for the
# whole run: before every request -- a validator retry counts -- the meter's
# own estimate of it is added to what the run has booked, and a request that
# would cross the cap is refused unsent (after a refusal the rest of that
# link's calls are refused too: its chain is broken). The same request is also
# checked against the live caps (``budget.check`` through the meter's gates:
# the daily cap with ``day_state()``, the story's cap). Every answered request
# is booked through the normal meter (``llm_spend.Meter``: ``spend.json`` and
# the story's own ledger, step label ``bench``, the served model on the row),
# as a story-level row (no episode: an experiment is not part of an
# episode's production cost, and the per-episode cap -- already spent by a
# rendered episode -- must not decide an A/B). The bench never spends without
# the flag: without ``--allow-paid`` the paid links are listed and skipped.

AB_STEP = "bench"
AB_PROMPT_IDS = ("E1v3", "E2v3", "E3v3", "J1v3")
AB_RETRY_FACTOR = 2  # the upper bound: every call asked once more
# Folders of a story that hold images, audio, video or caches: never copied.
_AB_SKIP_DIRS = frozenset({"cache", "bench", "refs", "assets", "render", "styles"})
_AB_SKIP_FILES = frozenset({"cost_ledger.json"})
_AB_FIRST_LINES = 3


class AbRefused(Exception):
    """The A/B cannot start; the message says why and what to do."""


def _ab_slug(link) -> str:
    """A file name for *link*: ``anthropic__claude-opus-5-5-at-xhigh``."""
    import re

    text = describe(link).replace("/", "__").replace("@", "-at-")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def _atomic_json(path, value) -> None:
    import tempfile

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".bench-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_text(path, text) -> None:
    import tempfile

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".bench-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def copy_story_documents(story_dir, ep, dest_outputs) -> str:
    """Copy *story_dir*'s JSON documents (never an image, a sound, a video or
    a ledger; never an episode from *ep* on) to ``<dest_outputs>/stories/<id>``
    -- what the script step reads to write episode *ep* -- and return the
    copy's story id. Read-only on *story_dir*. ``AbRefused`` when it holds no
    ``story.json``."""
    import re
    import shutil

    story_dir = os.path.normpath(story_dir)
    story_id = os.path.basename(story_dir)
    if not os.path.isfile(os.path.join(story_dir, "story.json")):
        raise AbRefused(f"{story_dir}: no story.json there; give the story's folder (outputs/stories/<id>).")
    target = os.path.join(dest_outputs, "stories", story_id)
    for root, dirs, files in os.walk(story_dir):
        rel = os.path.relpath(root, story_dir)
        parts = [] if rel == "." else rel.split(os.sep)

        def keep(name, parts=parts):
            if name in _AB_SKIP_DIRS:
                return False
            if parts == ["episodes"]:
                match = re.fullmatch(r"ep(\d+)", name)
                if match and int(match.group(1)) >= ep:
                    return False
            return True

        dirs[:] = sorted(name for name in dirs if keep(name) and not os.path.islink(os.path.join(root, name)))
        for name in sorted(files):
            source = os.path.join(root, name)
            if not name.endswith(".json") or name in _AB_SKIP_FILES or os.path.islink(source):
                continue
            destination = os.path.join(target, *parts, name)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copyfile(source, destination)
    return story_id


def _stamp_writing_v3(outputs_dir, story_id):
    """Stamp the COPY of the story ``writing: "v3"`` (the bench measures the
    writing-v3 chain, whatever the story's own stamp says). Returns the stamp
    the story had (None: none). ``AbRefused`` for a story that is not on the
    v2 pipeline: v3 writes only there."""
    path = os.path.join(outputs_dir, "stories", story_id, "story.json")
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    profile = doc.setdefault("generation_profile", {})
    if profile.get("pipeline") != "v2":
        raise AbRefused(f"story {story_id}: not on the v2 pipeline; the writing-v3 chain is written only for a v2 story.")
    before = profile.get("writing")
    profile["writing"] = "v3"
    _atomic_json(path, doc)
    return before


def _ab_context(outputs_dir, story_id, ep, settings_env, on_log):
    """``(ctx, ec)`` of episode *ep* in the copy at *outputs_dir*, past the
    script step's own preconditions (a ready story, an episode the season
    plans, the memory of the episode before it, a v2 story's approved
    knowledge base). ``AbRefused`` with the step's sentence otherwise."""
    from clipping import cancel as cancel_mod
    from clipping.aistory import steps
    from clipping.aistory.steps import episode_common
    from clipping.aistory.store import StoryStore

    stores = StoryStore(outputs_dir, on_log=lambda line: None)
    ctx = steps.StepContext(
        job_id="bench-ab", story_id=story_id, step="script", ep=ep, params={}, cancel=cancel_mod.CancelToken(),
        settings_env=dict(settings_env), outputs_dir=outputs_dir, on_log=on_log,
    )
    try:
        ec = episode_common.load_context(stores, story_id, ep)
        episode_common.check_episode_preconditions(ctx, ec, require_knowledge=True)
    except steps.StepFailed as exc:
        raise AbRefused(str(exc)) from None
    return ctx, ec


def ab_estimate(link, ec) -> dict:
    """What one run of the chain on *link* may cost, before anything is sent:
    one call per prompt of the chain (E1v3, one E2v3 per body scene of the
    template, E3v3, J1v3; a free link: $0), each at the widest input its prompt may be sent
    with (``prompts.input_budget``) and its reply cap -- E1v3's payoff
    variant from episode 2 on -- plus the link's thinking room
    (``registry.output_headroom``), priced as the meter prices a request
    (``pricing.llm_estimate_cost``, rounded up to the ledger's four
    decimals). ``usd`` is one try per call; ``upper_usd`` asks every call
    once more (the validator's one retry). ``PriceUnknown`` for a link with
    no price."""
    from clipping.aistory import prompts, timing
    from clipping.aistory.steps import llm_call, llm_spend
    from clipping.providers import pricing, registry

    body = timing.episode_slots(ec.template, ec.ep).count("body")
    free = llm_call.is_free_link(link)
    plan = (("E1v3", 1), ("E2v3", body), ("E3v3", 1), ("J1v3", 1))
    rows, total = [], 0.0
    for prompt_id, count in plan:
        cap = prompts.MAX_TOKENS[prompt_id]
        if prompt_id == "E1v3" and ec.ep >= 2:
            cap = max(cap, prompts.E1V3_PAYOFF_MAX_TOKENS)
        cap += registry.output_headroom(link, prompts.anthropic_effort(prompt_id))
        tokens_in = prompts.input_budget(prompt_id)
        # A free link costs nothing and has no row in the price table.
        each = 0.0 if free else llm_spend.ledger_usd(pricing.llm_estimate_cost(link, tokens_in, cap))
        rows.append({"prompt": prompt_id, "calls": count, "tokens_in": tokens_in, "tokens_out": cap,
                     "usd_each": each})
        total += each * count
    total = round(total, 4)
    return {"calls": sum(row["calls"] for row in rows), "usd": total,
            "upper_usd": round(total * AB_RETRY_FACTOR, 4), "prompts": rows}


class _AbRun:
    """What the whole run has booked and been refused: *max_usd* is its hard
    cap (None: no paid link runs, nothing to cap)."""

    def __init__(self, max_usd):
        self.max_usd = max_usd
        self.booked_usd = 0.0
        self.refusals = []

    def book(self, usd) -> None:
        self.booked_usd = round(self.booked_usd + float(usd), 6)

    def check(self, trace, usd, link) -> None:
        """Raise ``BudgetRefused`` for a request of *usd* that would take the
        run past ``--max-usd`` -- and for every request of a link a refusal
        already stopped."""
        from clipping.providers import budget as budget_mod

        if trace.refused:
            raise budget_mod.BudgetRefused(trace.refused, cap="bench", usd=usd, spent=self.booked_usd,
                                           cap_usd=self.max_usd or 0.0)
        if self.max_usd is not None and round(self.booked_usd + usd, 6) > self.max_usd + 1e-9:
            reason = (f"refused: est ${usd:.4f} on {describe(link)} would bring this bench run to "
                      f"${self.booked_usd + usd:.4f} of its --max-usd ${self.max_usd:.2f} "
                      f"(${self.booked_usd:.4f} booked so far)")
            trace.refused = reason
            trace.refused_by = "max-usd"
            self.refusals.append({"link": describe(link), "by": "max-usd", "reason": reason})
            raise budget_mod.BudgetRefused(reason, cap="bench", usd=usd, spent=self.booked_usd,
                                           cap_usd=self.max_usd)


class _AbTrace:
    """One link's run: every ``call_json`` it made, in order."""

    def __init__(self, link):
        self.link = link
        self.calls = []
        self.current = None
        self.refused = None
        self.refused_by = None
        self.error = None
        self.interrupted = False
        self.last_usage = None


def _usage_of(response) -> dict:
    usage = getattr(response, "usage", None)

    def first(*names):
        for name in names:
            value = getattr(usage, name, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    return {"tokens_in": first("prompt_tokens", "input_tokens"),
            "tokens_out": first("completion_tokens", "output_tokens")}


def _hook_meter(meter, run, trace) -> None:
    """Put the run's cap in front of *meter*'s gates (every request, and the
    meter's own pre-check), and have every booking counted against the run,
    kept on the call's record and tagged on its ledger row. The meter itself
    -- estimates, the live caps, the booking at the served model -- is
    ``llm_spend.Meter``'s, untouched."""
    from clipping.providers import budget as budget_mod

    gates = meter.gates
    real_check = gates.check

    def check(usd, link):
        run.check(trace, usd, link)
        try:
            real_check(usd, link)
        except budget_mod.BudgetRefused as exc:
            trace.refused = str(exc)
            trace.refused_by = f"live cap ({exc.cap})" if exc.cap else "live cap"
            run.refusals.append({"link": describe(link), "by": trace.refused_by, "reason": str(exc)})
            raise

    gates.check = check

    real_reply = meter.book_reply

    def book_reply(link, response, estimate_usd, note=None):
        trace.last_usage = _usage_of(response)
        real_reply(link, response, estimate_usd, note=note)

    meter.book_reply = book_reply

    real_book = meter.book

    def book(link, *, qty, usd, note=None):
        record = trace.current
        label = f"bench A/B {meter.prompt_id}" + (f" #{record['index']}" if record else "")
        real_book(link, qty=qty, usd=usd, note=f"{label}; {note}" if note else label)
        run.book(usd)
        usage, trace.last_usage = trace.last_usage or {}, None
        if record is not None:
            record["books"].append({"model": link.model, "qty": qty, "usd": usd, **usage})

    meter.book = book


def _finish_call(record, started, trace, link) -> dict:
    """Close one call's record: latency, requests (one per booking; the
    answered replies for a link that is not metered), retries, cost, tokens
    and the model that served it."""
    from clipping.providers import registry

    books = record.pop("books")
    record["latency_s"] = round(time.monotonic() - started, 3)
    record["requests"] = len(books) or record["validator_replies"]
    record["retries"] = max(0, record["requests"] - 1)
    record["usd"] = round(sum(book["usd"] for book in books), 4)
    tokens_in = [book.get("tokens_in") for book in books if book.get("tokens_in") is not None]
    tokens_out = [book.get("tokens_out") for book in books if book.get("tokens_out") is not None]
    record["tokens_in"] = sum(tokens_in) if tokens_in else None
    record["tokens_out"] = sum(tokens_out) if tokens_out else None
    served = list(dict.fromkeys(book["model"] for book in books))
    requested = registry.split_effort(link)[0]
    record["served_models"] = served
    record["fallback"] = any(registry.split_effort(link._replace(model=model))[0] != requested for model in served)
    return record


@contextlib.contextmanager
def _ab_metered(run, trace, *, ledger_outputs_dir):
    """Run the script step's calls with the A/B's meter hooks in place: the
    meter a paid call opens books to the REAL story's ledger
    (*ledger_outputs_dir*, step ``bench``, no episode) while the step writes
    its script into the throwaway copy, and ``call_json`` is wrapped to
    record each call. Both are module attributes looked up at call time and
    put back on exit; nothing of the production code is edited."""
    import dataclasses
    from unittest import mock

    from clipping.aistory.steps import llm_call, llm_spend

    real_open = llm_spend.open_meter
    real_call = llm_call.call_json

    def open_meter(ctx, prompt_id, *, system, user, cap):
        bench_ctx = dataclasses.replace(ctx, step=AB_STEP, ep=None, outputs_dir=ledger_outputs_dir)
        meter = real_open(bench_ctx, prompt_id, system=system, user=user, cap=cap)
        _hook_meter(meter, run, trace)
        return meter

    def call_json(ctx, prompt_id, system, user, schema, *, validator, **kwargs):
        record = {"index": len(trace.calls) + 1, "prompt": prompt_id, "validator_replies": 0,
                  "validator_passes": 0, "rejections": [], "accepted": False, "error": None, "books": []}
        trace.calls.append(record)
        trace.current = record
        started = time.monotonic()

        def counted(value):
            errors = list(validator(value))
            record["validator_replies"] += 1
            if errors:
                record["rejections"].append([str(error) for error in errors[:2]])
            else:
                record["validator_passes"] += 1
            return errors

        try:
            value = real_call(ctx, prompt_id, system, user, schema, validator=counted, **kwargs)
        except BaseException as exc:
            record["error"] = (getattr(exc, "reason", None) or str(exc) or type(exc).__name__)[:400]
            raise
        else:
            record["accepted"] = True
            return value
        finally:
            _finish_call(record, started, trace, trace.link)
            trace.current = None

    with mock.patch.object(llm_spend, "open_meter", open_meter), mock.patch.object(llm_call, "call_json", call_json):
        yield


def _speaker_name(ec, speaker) -> str:
    if speaker in (None, "narrator"):
        return "Narrator"
    return ec.names.get(speaker, str(speaker))


def _script_excerpt(ec, script) -> dict:
    """The hook line and the first spoken lines of *script*, as the human
    reads them: ``{"hook": "Name: text" | None, "on_screen_text": ..., "first_lines": [...]}``."""
    hook = next((scene for scene in script["scenes"] if scene["function"] == "hook"), None)
    hook_line = None
    if hook is not None and hook["lines"]:
        first = hook["lines"][0]
        hook_line = f"{_speaker_name(ec, first['speaker'])}: {first['text']}"
    lines = []
    for scene in script["scenes"]:
        if scene["function"] == "hook":
            continue
        for line in scene["lines"]:
            if len(lines) < _AB_FIRST_LINES:
                lines.append(f"{_speaker_name(ec, line['speaker'])}: {line['text']}")
    return {"hook": hook_line, "on_screen_text": (script.get("hook") or {}).get("on_screen_text"),
            "first_lines": lines}


def _verdict(script) -> str:
    from clipping.aistory.steps import judge

    report = (script or {}).get(judge.FIRST_WATCH)
    if report is None:
        return "not judged"
    if report.get("passed"):
        return "pass"
    count = len(judge.blocking_issues(report))
    return f"fail ({count} blocking)"


def _run_ab_link(link, estimate, *, snapshot, ep, story_id, settings_env, run, ledger_outputs_dir, work_dir,
                 runner, on_log, time_fn) -> tuple:
    """One link's chain, alone. Returns ``(row, document)``: the summary row
    and the full record the link's JSON file holds."""
    import shutil

    from clipping.aistory.steps import episode_common, llm_call
    from clipping.aistory.steps import script as script_step
    from clipping.aistory.steps import StepFailed

    label = describe(link)
    outputs_dir = os.path.join(work_dir, _ab_slug(link))
    shutil.copytree(snapshot, outputs_dir)
    # One link, the same one for every prompt: no other link to fall back to.
    link_env = dict(settings_env, STORY_LLM_CHAIN=label, STORY_LLM_PREMIUM_CHAIN=label)
    ctx, ec = _ab_context(outputs_dir, story_id, ep, link_env, on_log)
    trace = _AbTrace(link)
    step = script_step._Run(ctx, ec, runner=runner, time_fn=time_fn)
    step.script = script_step.skeleton(ec, now=llm_call.utc_now())
    started = time.monotonic()
    with _ab_metered(run, trace, ledger_outputs_dir=ledger_outputs_dir):
        try:
            for phase in (step.beat_sheet, step.body, step.framing, step.first_watch):
                phase()
                if trace.refused:
                    break
        except StepFailed as exc:
            trace.error = exc.reason or str(exc)
        except KeyboardInterrupt:
            trace.interrupted = True
    wall = round(time.monotonic() - started, 3)
    script = step.script
    complete = (script_step.is_complete(script, ec.ep) and not step.failed
                and (script.get("first_watch") is not None))
    if trace.interrupted:
        status = "interrupted"
    elif trace.refused:
        status = "refused"
    elif complete:
        status = "complete"
    elif trace.error or step.failed:
        status = "failed"
    else:
        status = "incomplete"
    reason = trace.refused or trace.error or step.failures() or None
    replies = sum(call["validator_replies"] for call in trace.calls)
    passes = sum(call["validator_passes"] for call in trace.calls)
    served = list(dict.fromkeys(model for call in trace.calls for model in call["served_models"]))
    excerpt = _script_excerpt(ec, script)
    real = round(sum(call["usd"] for call in trace.calls), 4)
    latency = round(sum(call["latency_s"] for call in trace.calls), 3)
    totals = {
        "calls": len(trace.calls), "requests": sum(call["requests"] for call in trace.calls),
        "retries": sum(call["retries"] for call in trace.calls), "validator_replies": replies,
        "validator_passes": passes, "validator_pass_rate": round(passes / replies, 3) if replies else None,
        "real_usd": real, "latency_s": latency, "wall_s": wall, "served_models": served,
        "fallback": any(call["fallback"] for call in trace.calls),
    }
    row = {
        "link": label, "status": status, "reason": reason, "est_usd": estimate["usd"],
        "upper_usd": estimate["upper_usd"], "real_usd": real, "latency_s": latency,
        "validator_pass_rate": totals["validator_pass_rate"], "validator_replies": replies,
        "validator_passes": passes, "retries": totals["retries"], "calls": len(trace.calls),
        "verdict": _verdict(script), "served_models": served, **excerpt, "file": f"{_ab_slug(link)}.json",
    }
    document = {
        "link": label, "status": status, "reason": reason, "story_id": story_id, "episode": ep,
        "estimate": estimate, "totals": totals, "calls": trace.calls,
        "failed": [{"what": what, "target": target, "reason": why} for what, target, why in step.failed],
        "first_watch": script.get("first_watch"), "excerpt": excerpt, "script": script,
    }
    return row, document


def _pct(row) -> str:
    rate = row.get("validator_pass_rate")
    if rate is None:
        return "-"
    return f"{round(rate * 100)}% ({row['validator_passes']}/{row['validator_replies']})"


def _ab_table(rows) -> list:
    """The console table: one line per link."""
    width = max([len("link")] + [len(row["link"]) for row in rows])
    head = (f"{'link':<{width}} {'status':<12} {'est $':>8} {'real $':>8} {'latency':>9} {'validator':>14} "
            f"{'retries':>7}  J1")
    lines = [head, "-" * len(head)]
    for row in rows:
        skipped = row["status"] == "skipped"
        lines.append(
            f"{row['link']:<{width}} {row['status']:<12} {row['est_usd']:>8.4f} "
            f"{'-' if skipped else format(row['real_usd'], '.4f'):>8} "
            f"{'-' if skipped else format(row['latency_s'], '.1f') + 's':>9} "
            f"{'-' if skipped else _pct(row):>14} {'-' if skipped else row['retries']!s:>7}  "
            f"{row['reason'] if skipped else row['verdict']}")
    return lines


def _ab_markdown(summary) -> str:
    out = [f"# Episode A/B: story {summary['story_id']}, episode {summary['episode']}", ""]
    out.append(f"Started {summary['started']}; "
               + ("INTERRUPTED, this is a partial summary. " if summary["interrupted"] else "")
               + f"booked ${summary['booked_usd']:.4f} of --max-usd "
               + (f"${summary['max_usd']:.2f}" if summary["max_usd"] is not None else "(none: no paid link ran)")
               + ".")
    if summary.get("writing_stamp_was") != "v3":
        out.append(f"The story's own writing stamp is {summary.get('writing_stamp_was')!r}: the run used the "
                   "writing-v3 prompts on a throwaway copy; the story itself is unchanged.")
    out += ["", "| link | status | est $ | real $ | latency | validator pass | retries | J1 verdict |",
            "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in summary["rows"]:
        skipped = row["status"] == "skipped"
        out.append(f"| {row['link']} | {row['status']} | {row['est_usd']:.4f} | "
                   f"{'-' if skipped else format(row['real_usd'], '.4f')} | "
                   f"{'-' if skipped else format(row['latency_s'], '.1f') + ' s'} | "
                   f"{'-' if skipped else _pct(row)} | {'-' if skipped else row['retries']} | "
                   f"{row['reason'] if skipped else row['verdict']} |")
    if summary["refusals"]:
        out += ["", "## Refused", ""]
        out += [f"- {item['link']} ({item['by']}): {item['reason']}" for item in summary["refusals"]]
    for row in summary["rows"]:
        if row["status"] == "skipped":
            continue
        out += ["", f"## {row['link']}", "", f"Status: {row['status']}"
                + (f" ({row['reason']})" if row.get("reason") else "")
                + (f"; served by {', '.join(row['served_models'])}" if row.get("served_models") else "")
                + f"; full record: {row['file']}", ""]
        out.append(f"- Hook: {row['hook'] or '(none)'}"
                   + (f"  [on screen: {row['on_screen_text']}]" if row.get("on_screen_text") else ""))
        out.append("- First spoken lines:")
        out += [f"  {number}. {line}" for number, line in enumerate(row["first_lines"], 1)] or ["  (none)"]
    out.append("")
    return "\n".join(out)


def _write_ab_outputs(out_dir, summary, documents) -> None:
    """Write whatever exists now: each finished link's JSON, ``summary.json``,
    ``summary.md`` -- called after every link and once more at the end, so a
    run cut short leaves its partial summary."""
    for name, document in documents.items():
        _atomic_json(os.path.join(out_dir, name), document)
    _atomic_json(os.path.join(out_dir, "summary.json"), summary)
    _atomic_text(os.path.join(out_dir, "summary.md"), _ab_markdown(summary))


def _print_today(out, label) -> None:
    from clipping.providers import budget as budget_mod

    state = budget_mod.day_state()
    out(f"{label} ({state.zone} {state.day}): paid spend today ${state.spent:.4f}"
        + (f", +${state.extra:.2f} allowed on top of the daily cap" if state.extra else ""))


def run_episode_ab(
    story_dir, chains, *, ep=1, allow_paid=False, max_usd=None, dry_run=False, settings_env=None,
    runner=None, out=print, on_log=None, now=None, time_fn=time.monotonic,
) -> dict:
    """The A/B (module section above). *chains*: ``"<link>,<link>"`` (or a
    list of ``Link``). Returns the summary dict (``summary.json``'s content;
    ``rows`` has one row per link, ``summary_dir`` where the files are, None
    for a dry run). *runner* is ``llm.run_chain`` unless a test hands in a
    stand-in (a stand-in answers by itself: nothing is metered, capped or
    booked); *out* prints the console lines, *on_log* the chain's own.

    ``AbRefused`` before any request: ``--allow-paid`` given while Settings
    has ``allow_paid`` off (the Settings switch wins), a paid link to run
    without ``--max-usd``, an unreadable story or episode, or a chain that
    cannot be parsed."""
    import tempfile
    from datetime import datetime, timezone

    from clipping.aistory.steps import llm_call
    from clipping.providers import budget as budget_mod
    from clipping.providers import gating, pricing, registry

    settings_env = _load_settings_env() if settings_env is None else settings_env
    try:
        links = list(dict.fromkeys(
            registry.parse_chain(chains) if isinstance(chains, str) else list(chains)))
    except registry.ChainError as exc:
        raise AbRefused(f"--chains: {exc}") from None
    if max_usd is not None and not (max_usd > 0 and max_usd == max_usd and max_usd != float("inf")):
        raise AbRefused(f"--max-usd is an amount above zero, not {max_usd!r}.")
    try:
        budget = gating.budget_of(gating.merged_env(settings_env))
    except ValueError as exc:
        raise AbRefused(f"The budget settings cannot be used: {exc}") from None
    keys = llm_call.resolve_keys(settings_env)

    paid_links = [link for link in links if not llm_call.is_free_link(link)]
    if paid_links and allow_paid and not budget.allow_paid:
        raise AbRefused("--allow-paid was given but allow_paid is off in Settings (ALLOW_PAID): the Settings "
                        "switch wins. Turn it on in Settings first, or run without --allow-paid to bench the "
                        "free links only.")
    if paid_links and allow_paid and not dry_run and max_usd is None:
        raise AbRefused("A paid link runs only under a hard cap: give --max-usd (e.g. --max-usd 2.50).")

    story_dir = os.path.normpath(story_dir)
    stamp = datetime.now(timezone.utc) if now is None else now
    out_dir = os.path.join(story_dir, "bench", stamp.strftime("%Y%m%dT%H%M%SZ"))

    with tempfile.TemporaryDirectory(prefix="bench-ab-") as work_dir:
        snapshot = os.path.join(work_dir, "snapshot")
        story_id = copy_story_documents(story_dir, ep, snapshot)
        stamp_was = _stamp_writing_v3(snapshot, story_id)
        _ctx, ec = _ab_context(snapshot, story_id, ep, settings_env, on_log or (lambda line: None))
        out_dir_ledger = os.path.dirname(os.path.dirname(story_dir))

        plan, skipped = [], []
        for link in links:
            label = describe(link)
            estimate = None
            reason = None
            try:
                estimate = ab_estimate(link, ec)
            except pricing.PriceUnknown as exc:
                reason = f"no price: {exc}"
            if reason is None and not llm_call.is_free_link(link) and not allow_paid:
                reason = "paid link: --allow-paid not given (DEC-115)"
            elif reason is None and not keys.get(link.provider):
                reason = f"no key set ({registry.env_key_for(link)})"
            if reason is None:
                plan.append((link, estimate))
            else:
                skipped.append({"link": label, "status": "skipped", "reason": reason,
                                "est_usd": (estimate or {}).get("usd", 0.0),
                                "upper_usd": (estimate or {}).get("upper_usd", 0.0), "real_usd": 0.0,
                                "latency_s": 0.0, "validator_pass_rate": None, "validator_replies": 0,
                                "validator_passes": 0, "retries": 0, "calls": 0, "verdict": "-",
                                "served_models": [], "hook": None, "on_screen_text": None, "first_lines": [],
                                "file": None})

        out(f"Episode A/B: story {story_id} ({(ec.story.get('title') or '')!r}), episode {ep}, "
            f"template {ec.template['template_id']}, writing v3 (the story's own stamp: {stamp_was!r}).")
        _print_today(out, "Today")
        cap_text = f"--max-usd ${max_usd:.2f}" if max_usd is not None else "no --max-usd"
        out(f"Daily cap ${budget.daily_cap_usd:.2f}, story cap ${budget.per_story_cap_usd:.2f}; {cap_text}; "
            f"allow_paid in Settings: {'on' if budget.allow_paid else 'off'}; --allow-paid: "
            f"{'given' if allow_paid else 'not given'}.")

        estimates = {describe(link): estimate for link, estimate in plan}
        listing = []
        for link, estimate in plan:
            listing.append({"link": describe(link), "status": "to run", "reason": "", "est_usd": estimate["usd"],
                            "upper_usd": estimate["upper_usd"], "real_usd": 0.0, "latency_s": 0.0,
                            "validator_pass_rate": None, "verdict": "-", "retries": 0})
        listing += [dict(row, status="skipped") for row in skipped]
        out("")
        width = max([len("run total (keyless links counted)")] + [len(row["link"]) for row in listing])
        out(f"{'link':<{width}} {'status':<9} {'calls':>5} {'est $ (one try)':>16} "
            f"{'upper $ (each retried once)':>28}  note")
        for row in listing:
            calls = (estimates.get(row["link"]) or {}).get("calls")
            if calls is None:
                calls = "-"
            out(f"{row['link']:<{width}} {row['status']:<9} {calls!s:>5} {row['est_usd']:>16.4f} "
                f"{row['upper_usd']:>28.4f}  {row['reason']}")
        counted = [item["usd"] for item in estimates.values()]
        counted_upper = [item["upper_usd"] for item in estimates.values()]
        if dry_run:
            # A dry run also counts the links left out only for want of a key: what the run costs once it is set.
            keyless = [row for row in skipped if row["reason"].startswith("no key set")]
            counted += [row["est_usd"] for row in keyless]
            counted_upper += [row["upper_usd"] for row in keyless]
        run_total = round(sum(counted), 4)
        run_upper = round(sum(counted_upper), 4)
        total_label = "run total" + (" (keyless links counted)" if dry_run and len(counted) > len(estimates) else "")
        out(f"{total_label:<{width}} {'':<9} {'':>5} {run_total:>16.4f} {run_upper:>28.4f}")
        if max_usd is not None and run_upper > max_usd:
            out(f"Note: the upper bound ${run_upper:.2f} is above --max-usd ${max_usd:.2f}: a call that would "
                "cross the cap is refused unsent, and the rest of that link is not run.")

        summary = {
            "story_id": story_id, "episode": ep, "started": stamp.isoformat(), "interrupted": False,
            "max_usd": max_usd, "allow_paid": bool(allow_paid), "dry_run": bool(dry_run),
            "writing_stamp_was": stamp_was, "estimate_usd": run_total, "estimate_upper_usd": run_upper,
            "booked_usd": 0.0, "rows": list(skipped), "refusals": [], "summary_dir": None,
        }
        if dry_run:
            out("\nDry run: nothing was sent, booked or written.")
            summary["rows"] = [dict(row) for row in listing]
            return summary
        if not plan:
            out("\nNo link to run: " + ("every link was skipped (see the table)." if skipped else "none given."))
            return summary

        summary["summary_dir"] = out_dir
        out(f"\nOutputs: {out_dir}\n")
        _print_today(out, "Before the first call")
        run = _AbRun(max_usd)
        documents = {}
        rows = []

        def snapshot_summary():
            summary["rows"] = rows + list(skipped)
            summary["booked_usd"] = round(run.booked_usd, 4)
            summary["refusals"] = list(run.refusals)
            _write_ab_outputs(out_dir, summary, documents)

        log = on_log or (lambda line: out(f"    {line}"))
        try:
            for link, estimate in plan:
                out(f"== {describe(link)} (est ${estimate['usd']:.4f}, upper ${estimate['upper_usd']:.4f})")
                row, document = _run_ab_link(
                    link, estimate, snapshot=snapshot, ep=ep, story_id=story_id, settings_env=settings_env,
                    run=run, ledger_outputs_dir=out_dir_ledger, work_dir=work_dir, runner=runner, on_log=log,
                    time_fn=time_fn)
                rows.append(row)
                documents[row["file"]] = document
                snapshot_summary()
                if row["status"] == "interrupted":
                    summary["interrupted"] = True
                    break
        except KeyboardInterrupt:
            summary["interrupted"] = True
        finally:
            snapshot_summary()

    out("")
    for line in _ab_table(summary["rows"]):
        out(line)
    for row in summary["rows"]:
        if row["status"] == "skipped":
            continue
        out(f"\n{row['link']}:")
        out(f"  hook: {row['hook'] or '(none)'}")
        for number, line in enumerate(row["first_lines"], 1):
            out(f"  {number}. {line}")
        if row.get("reason") and row["status"] != "complete":
            out(f"  {row['status']}: {row['reason']}")
    out("")
    _print_today(out, "After")
    out(f"Booked this run: ${summary['booked_usd']:.4f}"
        + (f" of --max-usd ${max_usd:.2f}" if max_usd is not None else "")
        + f". Summary: {os.path.join(out_dir, 'summary.md')}"
        + ("  (INTERRUPTED: partial)" if summary["interrupted"] else ""))
    return summary


def _run_episode_ab_mode(args) -> int:
    """``--episode-ab``: never falls through to the pass-A path."""
    from clipping.providers import budget as budget_mod

    if not args.chains:
        print("--episode-ab needs --chains \"<provider>/<model>,...\": the links to compare (nothing is chosen "
              "for you: a default could spend).")
        return 2
    if args.episode_prompts:
        print("--episode-ab and --episode-prompts are different measurements; give one.")
        return 2
    settings_env = _load_settings_env(args.settings_file)
    budget_mod.set_settings_reader(lambda: settings_env)
    try:
        summary = run_episode_ab(
            args.episode_ab, args.chains, ep=args.episode, allow_paid=args.allow_paid, max_usd=args.max_usd,
            dry_run=args.dry_run, settings_env=settings_env, on_log=(lambda line: print(f"    {line}"))
            if args.verbose else (lambda line: None))
    except AbRefused as exc:
        print(f"Refused: {exc}")
        return 2
    if args.dry_run:
        return 0
    if summary["interrupted"]:
        return 130
    ran = [row for row in summary["rows"] if row["status"] != "skipped"]
    return 0 if ran and all(row["status"] == "complete" for row in ran) else 1



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
    parser.add_argument(
        "--episode-prompts", metavar="STORY_DIR",
        help="Send the E1/E2/T1 episode-writing requests built from this story folder's written "
             "episode instead of the pass-A request; scores each reply's JSON validity against the "
             "prompt's own schema and post-validator, on every keyed free link (paid links skipped, "
             "DEC-115). See the module docstring. Overrides --chain/--transcript/--nim-shortlist.",
    )
    parser.add_argument("--ep", type=int, default=1,
                        help="With --episode-prompts: which episode to build requests from (default 1).")
    parser.add_argument(
        "--episode-ab", metavar="STORY_DIR",
        help="Plan 23 stage D7: write one episode of this story folder with the writing-v3 chain (E1v3, E2v3, "
             "E3v3, J1v3) once per link of --chains, each link alone (no fallback), and put the scripts and "
             "the judge's verdicts side by side under STORY_DIR/bench/<timestamp>/. Reads the story, writes "
             "nothing into its episode files. A paid link runs only with --allow-paid under --max-usd "
             "(DEC-115's explicit exception); see the module section.",
    )
    parser.add_argument("--chains", metavar="LINKS",
                        help="With --episode-ab: the comma-separated <provider>/<model> links to compare "
                             "(an Anthropic link may end in @effort).")
    parser.add_argument("--episode", type=int, default=1,
                        help="With --episode-ab: which episode to write (default 1).")
    parser.add_argument("--allow-paid", action="store_true",
                        help="With --episode-ab: let the paid links run (Settings' allow_paid must be on too); "
                             "without it they are listed and skipped.")
    parser.add_argument("--max-usd", type=float, default=None, metavar="X",
                        help="With --episode-ab: a hard cap on what the whole run may book; a request whose "
                             "estimate would cross it is refused unsent. Required with a paid link.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --episode-ab: print the per-link estimate and exit without calling anything.")
    args = parser.parse_args(argv)

    if args.episode_ab:
        return _run_episode_ab_mode(args)
    if args.episode_prompts:
        return _run_episode_prompt_mode(args)

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
