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
    args = parser.parse_args(argv)

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
