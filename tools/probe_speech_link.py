#!/usr/bin/env python3
"""Buy ONE speaking clip on one video link and measure whether its line was spoken.

Plan 23 stage C2. A native-speech story asks a video model to speak a shot's
line itself, lips and voice one performance. Before a link is trusted with a
whole episode, this probe buys a single clip on it and measures the answer:

* the prompt is the one a story sends (``prompting.speech_clip_prompt``),
  built from the flags below, with the line quoted in it;
* the link runs ALONE through ``generation.run_generation_chain``, journaled
  under ``--cache-dir`` (``gencache.GenCache``): a paid submit is never
  retried, a kept answer is served and a submitted request resumed without
  paying again;
* the clip's own sound is transcribed (the STT chain of Settings,
  ``assets.default_transcriber``) and aligned against the line
  (``native_speech.evaluate_take``): the words matched, what was heard, the
  speech window, and the speaking rate against the planned 2.4 words/s;
* the result is written to ``<out-dir>/<budget day>-<link slug>.json`` (never
  overwritten: ``-2``, ``-3``... when it exists) with the clip beside it.

**Pass rule.** ``matched >= 0.8`` (``passed``) AND accepted by ear: the JSON's
``ear`` stays null for the human to fill after listening. With no STT key
``passed`` is null: the clip was bought, the human listens.

**Gates, in order, before anything is sent.** For a paid link: ``ALLOW_PAID``
on in Settings AND ``--allow-paid`` given (the Settings switch wins);
``--max-usd`` given and the estimate of the one try under it (a paid link
makes one attempt); then the real budget gate (the daily cap, the day's
extra, the per-episode cap) inside the runner. Any refusal exits 2 with
nothing sent and nothing booked.

**Booking.** Only the journal books: ``GenCache(book=...)`` calls
``budget.record`` when the provider accepts the request (once; a resume or a
kept answer books nothing). There is no story, so there is no ledger row: the
JSON records ``booked_usd`` (this run) and the journal's stamp
(``meta.booked``).

The clip is bought at 720p, 9:16 (no flag: 1080p nearly doubles the price,
``fal/ltx-2.5-fast`` $0.09/s at 720p vs $0.16/s at 1080p).

    python tools/probe_speech_link.py --image portrait.png --dry-run
    python tools/probe_speech_link.py --image portrait.png --allow-paid --max-usd 0.60

Exit codes: 0 done (passed, failed or unknown), 2 refused, 1 failed after the
gates (the provider or the transport), 130 interrupted (a submitted request
stays journaled: run the same command again to resume it, unpaid).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clipping.aistory import native_speech, prompt_budgets, prompting  # noqa: E402
from clipping.providers import budget as budget_mod  # noqa: E402
from clipping.providers import gating, gencache  # noqa: E402
from clipping.providers import generation as gen  # noqa: E402
from clipping.providers import video  # noqa: E402,F401 - registers the video adapters
from clipping.providers.registry import ChainError, describe  # noqa: E402

DEFAULT_LINK = "fal/ltx-2.5-fast"
DEFAULT_DURATION_S = 6
# Twelve words: exactly what a 6 s clip speaks (native_speech.capacity(6)).
DEFAULT_LINE = "Tu as vendu mon étal à ma sœur sans me le dire."
DEFAULT_LANGUAGE = "fr"
DEFAULT_SPEAKER = "speaker"
DEFAULT_LOOK = "the person in the reference image"
DEFAULT_ACTION = "leans forward, eyes fixed, jaw tight"
DEFAULT_REACTION = "eyes widening"
DEFAULT_PLACE = "a busy covered market at midday"
DEFAULT_AMBIENCE = "a distant market crowd"
DEFAULT_CAMERA = "hold"
DEFAULT_OUT_DIR = os.path.join(ROOT, "data", "probes")

RESOLUTION = "720p"
ASPECT = "9:16"
MATCHED_THRESHOLD = 0.8
WPS_REFERENCE = native_speech.SPEECH_WPS
# No story, so no style lock: the prompt carries no style motion suffix.
NEUTRAL_STYLE_LOCK = {"motion_rules": {"tier2_prompt_suffix": ""}}
# ``run_probe(transcribe=...)`` left at this: the STT chain of Settings.
FROM_SETTINGS = object()


class ProbeRefused(Exception):
    """A gate refused the probe before anything was sent; the message says why."""


class ProbeFailed(Exception):
    """The probe passed its gates but the link failed; the message says why."""


# ------------------------------------------------------------------ helpers

def _load_settings_env(settings_file=None):
    """The dashboard's saved settings (``tools/bench_llm.py``'s reading):
    ``clipping.config`` loads ``.env`` first, then ``web.api.settings_store``.
    Empty when neither is available."""
    try:
        from clipping import config  # noqa: F401  (loads .env as a side effect)
    except Exception:  # noqa: BLE001 - .env is optional
        pass
    try:
        from web.api import settings_store

        return settings_store.load(settings_file)
    except Exception:  # noqa: BLE001 - a missing store just means env only
        return {}


def link_slug(link) -> str:
    """A file name for *link*: ``fal__ltx-2.5-fast``."""
    text = describe(link).replace("/", "__").replace("@", "-at-")
    return re.sub(r"[^A-Za-z0-9_.-]", "_", text)


def _sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def derive_seed(label, line, image_sha256) -> int:
    """A seed from the link, the line and the image's bytes: the same inputs
    make the same request key, so a second run is served from the journal."""
    digest = hashlib.sha256(f"{label}|{line}|{image_sha256}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _probe_stem(out_dir, day, slug) -> str:
    """``<day>-<slug>``, or ``-2``, ``-3``... when its JSON or clip exists."""
    base = f"{day}-{slug}"
    stem, number = base, 1
    while os.path.exists(os.path.join(out_dir, f"{stem}.json")) or os.path.exists(
            os.path.join(out_dir, f"{stem}.mp4")):
        number += 1
        stem = f"{base}-{number}"
    return stem


def _atomic_json(path, value) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".probe-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _json_safe(meta) -> dict:
    kept = {}
    for name, value in (meta or {}).items():
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        kept[str(name)] = value
    return kept


def _valid_usd(value) -> bool:
    return value is not None and value == value and 0 < value < float("inf")


class _Watched:
    """The link's adapter, counting the calls that can reach the provider
    (``generate``, ``resume``): a chain that ends with none was refused
    before anything was sent."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        value = getattr(self._inner, name)
        if name in ("generate", "resume") and callable(value):
            def called(*args, **kwargs):
                self.calls += 1
                return value(*args, **kwargs)
            return called
        return value


# ------------------------------------------------------------------ the probe

def build_prompt(*, link, speaker, look, action, listener, language, line, reaction, camera, place, ambience):
    """The speaking clip's prompt as a story builds it, to *link*'s speech budget."""
    if camera not in prompting.CAMERA_PHRASES:
        raise ProbeRefused(f"--camera is one of {', '.join(prompting.CAMERA_PHRASES)}, not {camera!r}.")
    return prompting.speech_clip_prompt(
        NEUTRAL_STYLE_LOCK, speaker=speaker, look=look, action=action, listener=listener, language=language,
        voice=prompting.voice_line(None), line=line, reaction=reaction,
        camera_phrase=prompting.CAMERA_PHRASES[camera], place=place, ambience=ambience,
        budget=prompt_budgets.speech_clip_words(link))


def _measure(clip_path, *, line, language, duration_s, transcribe, stt_reason, on_log):
    """The take of *clip_path*: ``(clip_real_s, has_audio, stt, take, reason)``,
    the primitives recombined as the assets step's ``_take`` does."""
    from clipping.aistory.steps import clips
    from clipping.aistory.steps import native_take as take_mod

    try:
        real = take_mod.clip_seconds(clip_path)
    except take_mod.TakeError as exc:
        return None, clips.clip_has_audio(clip_path), None, None, f"the clip cannot be read ({exc})"
    has_audio = clips.clip_has_audio(clip_path)
    reason, stt = None, None
    words = aligned_by = None
    with tempfile.TemporaryDirectory(prefix="probe-take-") as work:
        full = None
        if has_audio:
            try:
                full = take_mod.extract_audio(clip_path, os.path.join(work, "clip.wav"))
            except take_mod.TakeError as exc:
                reason = str(exc)
        if full is None:
            words = []
            reason = reason or "the clip has no sound track"
        elif transcribe is None:
            reason = stt_reason or "no STT link has a key"
            stt = f"unavailable: {reason}"
        else:
            try:
                words, aligned_by = transcribe(full, language=language, on_log=on_log, cancel=None)
                stt = aligned_by
            except Exception as exc:  # noqa: BLE001 - a failed STT leaves the take unknown
                words, reason = None, f"the transcription failed ({type(exc).__name__}: {exc})"
                stt = f"unavailable: {reason}"
    take = native_speech.evaluate_take(line, words, clip_real_s=real, clip_s=duration_s, aligned_by=aligned_by)
    if take["state"] == native_speech.TAKE_NO_SPEECH and reason is None:
        reason = "nothing was heard in the clip's sound"
    return real, has_audio, stt, take, reason


def _wps(line, take):
    """The line's words over its spoken window, or None without one."""
    if not take or take.get("start_s") is None or take.get("end_s") is None:
        return None
    window = float(take["end_s"]) - float(take["start_s"])
    if window <= 0:
        return None
    return round(native_speech.words_of(line) / window, 3)


def run_probe(*, image, link=DEFAULT_LINK, duration_s=DEFAULT_DURATION_S, line=DEFAULT_LINE,
              language=DEFAULT_LANGUAGE, speaker=DEFAULT_SPEAKER, listener="", look=DEFAULT_LOOK,
              action=DEFAULT_ACTION, reaction=DEFAULT_REACTION, place=DEFAULT_PLACE, ambience=DEFAULT_AMBIENCE,
              camera=DEFAULT_CAMERA, seed=None, allow_paid=False, max_usd=None, dry_run=False,
              out_dir=DEFAULT_OUT_DIR, cache_dir=None, settings_env=None, adapters=None, transport=None,
              transcribe=FROM_SETTINGS, time_fn=time.monotonic, sleep_fn=time.sleep, out=print, on_log=None):
    """The probe (module docstring). Returns the record written (the dry
    run's: what would be sent, ``json_path`` None). ``ProbeRefused`` before
    anything is sent, ``ProbeFailed`` when the link failed after its gates.
    *adapters*, *transport*, *transcribe* (None: no STT link), *settings_env*,
    *time_fn* and *sleep_fn* are seams for tests."""
    settings_env = _load_settings_env() if settings_env is None else settings_env
    budget_mod.set_settings_reader(lambda: settings_env)
    on_log = on_log or (lambda message: out(f"    {message}"))

    # -- what is asked, checked before anything else
    try:
        chain = gen.parse_generation_chain(gen.VIDEO, link)
    except ChainError as exc:
        raise ProbeRefused(f"--link: {exc}") from None
    if len(chain) != 1:
        raise ProbeRefused(f"--link names one video link, not {len(chain)}.")
    the_link = chain[0]
    label = describe(the_link)
    adapter = gen.adapter_for(gen.VIDEO, the_link.provider, adapters)
    if adapter is None or gen.is_manual(the_link):
        raise ProbeRefused(f"{label}: no video adapter can call it.")
    lengths = video.CLIP_LENGTHS.get(label)
    if not lengths:
        raise ProbeRefused(f"{label}: no clip lengths are known for it (video.CLIP_LENGTHS).")
    if duration_s not in lengths:
        raise ProbeRefused(f"{label} sells clips of {', '.join(str(n) for n in lengths)} s, not {duration_s!r}.")
    if video.AUDIO.get(label, "never") == "never":
        raise ProbeRefused(f"{label} makes silent clips: it cannot speak a line.")
    image = os.path.abspath(image)
    try:
        image_sha = _sha256_file(image)
    except OSError as exc:
        raise ProbeRefused(f"--image cannot be read ({type(exc).__name__}: {exc}).") from None
    line = " ".join(str(line).split())
    if not line:
        raise ProbeRefused("--line is empty.")
    seed = derive_seed(label, line, image_sha) if seed is None else int(seed)
    prompt = build_prompt(link=the_link, speaker=speaker, look=look, action=action, listener=listener,
                          language=language, line=line, reaction=reaction, camera=camera, place=place,
                          ambience=ambience)
    word_count = native_speech.words_of(line)
    capacity = native_speech.capacity(duration_s)
    out_dir = os.path.abspath(out_dir)
    cache_dir = os.path.abspath(cache_dir or os.path.join(out_dir, "gen"))
    day = budget_mod.today()
    stem = _probe_stem(out_dir, day, link_slug(the_link))
    request = gen.GenRequest(kind=gen.VIDEO, prompt=prompt, seed=seed, references=(image,),
                             duration_s=int(duration_s), native_audio=True, out_dir=out_dir,
                             extra={"name": stem})
    paid = gen.is_paid(the_link)
    estimate = adapter.estimate(the_link, request) if paid else None
    est_usd = round(float(getattr(estimate, "est_usd", estimate) or 0.0), 4)
    merged = gating.merged_env(settings_env)
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        raise ProbeRefused(f"The budget settings cannot be used: {exc}") from None

    def gates():
        """The refusal of the first gate that refuses, or None (the order of the module docstring)."""
        if paid and not budget_obj.allow_paid:
            return ("allow_paid is off in Settings (ALLOW_PAID): the Settings switch wins. Turn it on in "
                    f"Settings first; {label} is a paid link.")
        if paid and not allow_paid:
            return f"{label} is a paid link (est ${est_usd:.2f}): give --allow-paid to buy the clip."
        if paid and max_usd is None:
            return f"A paid link runs only under a hard cap: give --max-usd (e.g. --max-usd {est_usd:.2f})."
        if paid and not _valid_usd(max_usd):
            return f"--max-usd is an amount above zero, not {max_usd!r}."
        if paid and est_usd > float(max_usd) + 1e-9:
            return f"est ${est_usd:.2f} on {label} is over --max-usd ${float(max_usd):.2f}; nothing was sent."
        return None

    record = {
        "link": label, "model": the_link.model, "duration_s": int(duration_s), "resolution": RESOLUTION,
        "aspect": ASPECT, "seed": seed, "image": {"path": image, "sha256": image_sha}, "line": line,
        "language": language, "prompt": prompt, "prompt_sentences": prompting.speech_prompt_sentences(prompt),
        "prompt_words": len(prompt.split()), "word_count": word_count, "capacity": capacity, "est_usd": est_usd,
    }
    if word_count > capacity:
        out(f"⚠️ The line has {word_count} words; a {duration_s} s clip speaks {capacity} "
            "(native_speech.capacity): it may be cut or rushed.")

    if dry_run:
        state = budget_mod.day_state()
        refusal = gates()
        out("Dry run: nothing is sent, nothing is booked, nothing is written.")
        out(f"Link: {label} ({'paid' if paid else 'free'}), {duration_s} s, {RESOLUTION}, {ASPECT}, seed {seed}")
        out(f"Image: {image} (sha256 {image_sha[:12]})")
        out(f"Line ({language}): \"{line}\" -- {word_count} words, capacity {capacity} at {duration_s} s")
        out(f"Prompt ({len(prompt.split())} words, budget {prompt_budgets.speech_clip_words(the_link)}):")
        out(f"  {prompt}")
        out(f"Estimate: ${est_usd:.2f} (one try; a paid link makes one attempt)")
        out(f"Today ({state.zone} {state.day}): paid spend ${state.spent:.4f} of the "
            f"${budget_obj.daily_cap_usd:.2f} daily cap"
            + (f" (+${state.extra:.2f} allowed today)" if state.extra else ""))
        if refusal:
            out(f"Gates: would refuse: {refusal}")
        else:
            out("Gates: the switches and --max-usd would pass; the daily cap is checked again at the call.")
        out(f"Would write: {os.path.join(out_dir, stem + '.json')} (journal {cache_dir})")
        return dict(record, json_path=None, dry_run=True)

    refusal = gates()
    if refusal:
        raise ProbeRefused(refusal)

    # -- the call: the only booking point is the journal's ``book``
    booked, released = [], []

    def book(entry):
        est = float(entry.get("est_usd") or 0.0)
        if entry.get("paid") and est > 0:
            budget_mod.record(est)
            booked.append(est)

    def release(entry):
        est = float(entry.get("est_usd") or 0.0)
        if entry.get("paid") and est > 0:
            when = (entry.get("booked") or {}).get("at")
            budget_mod.release(est, day=budget_mod.day_key_at(when))
            released.append(est)

    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(cache_dir, exist_ok=True)
    cache = gencache.GenCache(cache_dir, book=book, release=release)
    watched = _Watched(adapter)
    out(f"Probe: {label}, {duration_s} s at {RESOLUTION}, est ${est_usd:.2f}; the line: \"{line}\"")
    try:
        result, _used = gen.run_generation_chain(
            gen.VIDEO, chain, request, env=merged, allow_paid=bool(allow_paid and budget_obj.allow_paid),
            on_log=on_log, budget_check=gating.budget_check(budget_obj),
            adapters={(gen.VIDEO, the_link.provider): watched}, transport=transport, sleep_fn=sleep_fn,
            time_fn=time_fn, cache=cache)
    except gen.NoRunnableLink as exc:
        reasons = "; ".join(reason for _label, reason in exc.failures) or str(exc)
        if watched.calls == 0 and not booked:
            raise ProbeRefused(f"{label}: {reasons}") from None
        raise ProbeFailed(f"{label}: {reasons}"
                          + (f" (booked ${sum(booked):.2f} this run)" if booked else "")) from None
    except gencache.JournalError as exc:
        raise ProbeFailed(f"{label}: {exc}") from None

    meta = dict(result.meta or {})
    journal = "kept" if meta.get("cached") else "resumed" if meta.get("resumed") else "new"
    request_id = meta.get("request_id")
    if request_id is None and meta.get("cache_key"):
        try:
            entry = cache.lookup(meta["cache_key"]) or {}
        except gencache.JournalError:
            entry = {}
        request_id = (entry.get("request") or {}).get("request_id")
    clip_path = result.paths[0] if result.paths else None

    # -- the measure: free, local but for the STT call
    stt_reason = None
    if transcribe is FROM_SETTINGS:
        from clipping.aistory.steps import assets

        transcribe, stt_reason = assets.default_transcriber(settings_env)
    if clip_path:
        real, has_audio, stt, take, reason = _measure(
            clip_path, line=line, language=language, duration_s=int(duration_s), transcribe=transcribe,
            stt_reason=stt_reason, on_log=on_log)
    else:
        real, has_audio, stt, take, reason = None, False, None, None, "the link answered no clip"
    matched = (take or {}).get("matched")
    if take is None or take["state"] == native_speech.TAKE_STT_UNAVAILABLE or matched is None:
        passed = None
    else:
        passed = bool(matched >= MATCHED_THRESHOLD)

    json_path = os.path.join(out_dir, f"{stem}.json")
    record.update({
        "booked_usd": round(sum(booked) - sum(released), 4), "journal": journal, "request_id": request_id,
        "meta": _json_safe(meta), "clip_path": clip_path, "clip_real_s": real, "has_audio": has_audio,
        "adapter_has_audio": meta.get("has_audio"), "stt": stt, "take": take, "reason": reason,
        "wps": _wps(line, take), "wps_reference": WPS_REFERENCE, "matched_threshold": MATCHED_THRESHOLD,
        "passed": passed, "ear": None, "spend_today_after": round(budget_mod.day_spent(), 4),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    _atomic_json(json_path, record)
    record["json_path"] = json_path

    verdict = {True: "PASSED", False: "FAILED", None: "UNKNOWN"}[passed]
    out("")
    out(f"{verdict} (machine half; the ear decides the rest): matched "
        + (f"{matched:.2f}" if matched is not None else "-") + f" of {MATCHED_THRESHOLD}, "
        + f"take {take['state'] if take else '-'}")
    if take and take.get("heard") is not None:
        out(f"Heard: \"{take['heard']}\"")
    wps = record["wps"]
    out(f"Rate: {wps if wps is not None else '-'} words/s (planned {WPS_REFERENCE}); "
        f"sound track: {'yes' if has_audio else 'no'}")
    if reason:
        out(f"Note: {reason}")
    out(f"Cost: ${record['booked_usd']:.2f} booked this run (journal: {journal}); "
        f"paid spend today ${record['spend_today_after']:.2f}")
    out(f"Clip: {clip_path}")
    out(f"Probe: {json_path}  (set \"ear\" after listening)")
    return record


# ------------------------------------------------------------------ the CLI

def _parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--link", default=DEFAULT_LINK, help=f"The video link to probe (default {DEFAULT_LINK}).")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION_S,
                        help="The clip's length in seconds; one the link sells (default 6).")
    parser.add_argument("--image", required=True, metavar="PATH", help="The keyframe or portrait the clip starts on.")
    parser.add_argument("--line", default=DEFAULT_LINE, help="The line to speak (default: a 12-word French line).")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="The line's language code (default fr).")
    parser.add_argument("--speaker", default=DEFAULT_SPEAKER, help="The speaker's handle in the prompt.")
    parser.add_argument("--listener", default="", help="Who is spoken to (default: nobody, straight ahead).")
    parser.add_argument("--look", default=DEFAULT_LOOK, help="The speaker's look, a few words.")
    parser.add_argument("--action", default=DEFAULT_ACTION, help="What the speaker does while speaking.")
    parser.add_argument("--reaction", default=DEFAULT_REACTION, help="The listener's reaction (with --listener).")
    parser.add_argument("--place", default=DEFAULT_PLACE, help="Where it happens, a few words.")
    parser.add_argument("--ambience", default=DEFAULT_AMBIENCE, help="The low background sound.")
    parser.add_argument("--camera", default=DEFAULT_CAMERA, choices=sorted(prompting.CAMERA_PHRASES),
                        help="The camera move (default hold).")
    parser.add_argument("--seed", type=int, default=None,
                        help="Default: derived from the link, the line and the image, so a rerun hits the journal.")
    parser.add_argument("--settings-file", metavar="PATH",
                        help="The dashboard's settings file (default: data/settings.json).")
    parser.add_argument("--allow-paid", action="store_true",
                        help="Buy the clip on a paid link (ALLOW_PAID must be on in Settings too).")
    parser.add_argument("--max-usd", type=float, default=None,
                        help="The hard cap of this run; required for a paid link.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the prompt, the estimate and today's spend; send and write nothing.")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="Where the probe JSON and clip go (data/probes).")
    parser.add_argument("--cache-dir", default=None, help="The generation journal (default <out-dir>/gen).")
    return parser


def main(argv=None, *, settings_env=None, adapters=None, transport=None, transcribe=FROM_SETTINGS,
         time_fn=time.monotonic, sleep_fn=time.sleep, out=print) -> int:
    args = _parser().parse_args(argv)
    if settings_env is None:
        settings_env = _load_settings_env(args.settings_file)
    try:
        run_probe(
            image=args.image, link=args.link, duration_s=args.duration, line=args.line, language=args.language,
            speaker=args.speaker, listener=args.listener, look=args.look, action=args.action,
            reaction=args.reaction, place=args.place, ambience=args.ambience, camera=args.camera, seed=args.seed,
            allow_paid=args.allow_paid, max_usd=args.max_usd, dry_run=args.dry_run, out_dir=args.out_dir,
            cache_dir=args.cache_dir, settings_env=settings_env, adapters=adapters, transport=transport,
            transcribe=transcribe, time_fn=time_fn, sleep_fn=sleep_fn, out=out)
    except ProbeRefused as exc:
        out(f"Refused: {exc}")
        return 2
    except ProbeFailed as exc:
        out(f"Failed: {exc}")
        return 1
    except KeyboardInterrupt:
        out("Interrupted. A request the provider accepted stays journaled: run the same command again to "
            "resume it without paying twice.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
