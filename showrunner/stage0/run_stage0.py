"""Stage 0 of plan 36: the voice-path spike on the RunPod `video` endpoint.

Runs the matrix of :mod:`showrunner.stage0.matrix` and writes every clip, its contact sheet and a
review table under ``stories/_stage0/`` so Rida can watch, listen and pick the primary path.

Order of operations (each command is one GPU batch, submitted in parallel, waited, saved):

    python -m showrunner.stage0.run_stage0 smoke     --video-endpoint <id>
    python -m showrunner.stage0.run_stage0 a         --video-endpoint <id> [--lang fr|en] [--seeds 11 22 33]
        path (a) prompt-only dialogue: 3 characters x seeds + the two-speaker exchange (+ three if kf_three.png exists)
    python -m showrunner.stage0.run_stage0 voice     --character paloma --from stories/_stage0/a/paloma_fr_s22.mp4
        locks a voice reference (the audio of the take you liked) -> stories/_stage0/voices/paloma.wav
    python -m showrunner.stage0.run_stage0 b         --video-endpoint <id>
        path (b) TTS (Chatterbox, the locked voice) -> LTX-2.5 A2V, same matrix; exchanges from concatenated lines
    python -m showrunner.stage0.run_stage0 c         --video-endpoint <id>
        path (c) LTX-2.3 ID-LoRA (keyframe + locked voice -> one pass), same matrix
    python -m showrunner.stage0.run_stage0 keyframe3 --images-endpoint <id>
        the three-character keyframe for the 3-speaker exchange (showrunner's edit_flux2_klein_multiref)
    python -m showrunner.stage0.run_stage0 keyframe  --character camille --images-endpoint <id> [--seeds 1 2]
        start-image candidates for a character with no keyframe (t2i_flux2_klein); then, free:
    python -m showrunner.stage0.run_stage0 keyframe  --character camille --pick 2
    python -m showrunner.stage0.run_stage0 preflight --video-endpoint <id>
        free, read-only: HF licenses, the image on GHCR, both endpoints and their keys, local files, batch sizes
    python -m showrunner.stage0.run_stage0 vc        --video-endpoint <id> [--take 33] [--dry-run]
        stage 1.0 (A-221): one path (a) take per character re-voiced with its locked voice (FL_ChatterboxVC),
        timing kept, remuxed on the original picture -> stories/_stage0/vc/<char>_s<take>.mp4
    python -m showrunner.stage0.run_stage0 vc        --exchanges [--exchange-take 22] [--dry-run]   (run with .venv/bin/python)
        the 2- and 3-speaker clips: cut at the clip check's line timings, each line converted to its speaker's
        locked voice, rejoined at the same times -> stories/_stage0/vc/ex_<two|three>_fr_s22.mp4
    python -m showrunner.stage0.run_stage0 verify    --clip stories/_stage0/a/paloma_fr_s33.mp4 --line "paloma: Tu souris ..."
        free: the clip check (speech-to-text on this CPU, aligned to the lines); run it with .venv/bin/python
    python -m showrunner.stage0.run_stage0 review
        probes every clip (duration, audio, loudness), writes contact sheets and stories/_stage0/review.md

``RUNPOD_API_KEY`` from the environment or ``.env``. Nothing here calls an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from showrunner import comfy_templates  # noqa: E402
from showrunner import runpod_client as rp  # noqa: E402
from showrunner import verify  # noqa: E402
from showrunner import voice  # noqa: E402
from showrunner.stage0 import matrix as M  # noqa: E402

OUT = os.path.join(M.REPO_ROOT, "stories", "_stage0")
# RunPod Serverless list prices, flex workers, per second (check against the invoice).
L40S_USD_PER_S = 0.00053
IMAGES_USD_PER_S = 1.58 / 3600  # the images endpoint's rate in .env (RUNPOD_IMAGE_GPU_USD_PER_HOUR)
VOICES = os.path.join(OUT, "voices")
KF_THREE = os.path.join(OUT, "kf_three.png")


# ------------------------------------------------------------------ batch helper

def run_batch(endpoint: rp.Endpoint, jobs: list, out_dir: str, *, poll_s: float = 8.0) -> list:
    """*jobs*: ``[(stem, template, values, files), ...]``. Submits all, waits all, saves all.
    Returns ``[(stem, paths, billed_s, status)]`` and appends to ``<out_dir>/jobs.jsonl``."""
    os.makedirs(out_dir, exist_ok=True)
    submitted = []
    for stem, template, values, files in jobs:
        values = dict(values, name=stem)
        job_id = rp.submit_template(endpoint, template, values, files)
        print(f"submitted {stem} -> {job_id}")
        submitted.append((stem, job_id, template, values))
        # Written at once: a crashed or killed run still leaves every job id to collect or cancel.
        with open(os.path.join(out_dir, "submitted.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stem": stem, "job": job_id, "endpoint": endpoint.id,
                                 "at": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
    results = []
    total = 0.0
    for stem, job_id, template, values in submitted:
        try:
            status = endpoint.wait(job_id, poll_s=poll_s)
        except rp.RunPodError as exc:  # one stuck job never sinks the batch
            print(f"FAILED {stem}: {exc}")
            status = {"status": "LOST", "error": str(exc)}
        try:
            paths = rp.save_outputs(status, out_dir, stem=stem) if status.get("status") != "LOST" else []
        except rp.RunPodError as exc:
            print(f"FAILED {stem}: {exc}")
            paths = []
        billed, waited = rp.billed_seconds(status), rp.delay_seconds(status)
        total += billed
        results.append((stem, paths, billed, status.get("status")))
        with open(os.path.join(out_dir, "jobs.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stem": stem, "job": job_id, "template": template, "status": status.get("status"),
                                 "billed_s": billed, "delay_s": waited, "paths": paths, "values": {k: v for k, v in values.items()
                                                                                 if k not in ("prompt",)},
                                 "prompt": values.get("prompt"), "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                ensure_ascii=False) + "\n")
        print(f"done {stem}: {status.get('status')} ran {billed:.0f} s (waited {waited:.0f} s, not billed) "
              f"-> {[os.path.basename(p) for p in paths]}")
    print(f"batch ran {total:.0f} GPU-seconds (≈ ${total * L40S_USD_PER_S:.2f} at the L40S flex list price)")
    return results


def _voice_path(char_id: str) -> str:
    return os.path.join(VOICES, f"{char_id}.wav")


def _need_voices(chars: list) -> None:
    missing = [c for c in chars if not os.path.exists(_voice_path(c))]
    if missing:
        sys.exit(f"no locked voice for {', '.join(missing)}: run `voice --character <id> --from <clip.mp4>` "
                 f"on a path (a) take first (stories/_stage0/a/)")


def _ready_characters() -> list:
    """The characters whose start image exists; the others are skipped with the command that makes it."""
    ready = []
    for c, info in M.CHARACTERS.items():
        if os.path.exists(info["keyframe"]):
            ready.append(c)
        else:
            print(f"skip {c}: no keyframe yet (run `keyframe --character {c}`, then `--pick`)")
    return ready


def _video_endpoint(args) -> rp.Endpoint:
    # The showrunner-video endpoint has its own key (RUNPOD_SHOWRUNNER_VIDEO_KEY); fall back to the main one.
    try:
        key = rp.api_key(name="RUNPOD_SHOWRUNNER_VIDEO_KEY")
    except rp.RunPodError:
        key = None
    return rp.Endpoint(args.video_endpoint, key=key)


def _images_endpoint(args) -> rp.Endpoint:
    # The images endpoint has its own key in the app (RUNPOD_IMAGE_API_KEY); fall back to the main one.
    try:
        key = rp.api_key(name="RUNPOD_IMAGE_API_KEY")
    except rp.RunPodError:
        key = None
    return rp.Endpoint(args.images_endpoint, key=key)


# ------------------------------------------------------------------ commands

def cmd_smoke(args) -> None:
    ep = _video_endpoint(args)
    c = "paloma"
    jobs = [(f"smoke_{c}", "ltx25_i2v_speech",
             {"prompt": M.prompt_path_a(c, args.lang), "negative": M.negative(c), "seed": 11, "width": M.WIDTH,
              "height": M.HEIGHT, "seconds": M.SINGLE_SECONDS, "fps": M.FPS},
             {"image": M.CHARACTERS[c]["keyframe"]})]
    run_batch(ep, jobs, os.path.join(OUT, "smoke"))


def _exchange_jobs_a(lang: str, seeds: list) -> list:
    jobs = []
    for key, ex in M.EXCHANGES.items():
        kf = ex["keyframe"] or KF_THREE
        if not os.path.exists(kf):
            print(f"skip exchange '{key}': no keyframe ({kf}); run keyframe3 first")
            continue
        for seed in seeds[:2]:
            jobs.append((f"ex_{key}_{lang}_s{seed}", "ltx25_i2v_speech",
                         {"prompt": M.prompt_exchange_a(key, lang), "negative": M.exchange_negative(key), "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": M.EXCHANGE_SECONDS, "fps": M.FPS},
                         {"image": kf}))
    return jobs


def cmd_a(args) -> None:
    ep = _video_endpoint(args)
    jobs = []
    for c in _ready_characters():
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx25_i2v_speech",
                         {"prompt": M.prompt_path_a(c, args.lang), "negative": M.negative(c), "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": M.SINGLE_SECONDS, "fps": M.FPS},
                         {"image": M.CHARACTERS[c]["keyframe"]}))
    jobs += _exchange_jobs_a(args.lang, args.seeds)
    run_batch(ep, jobs, os.path.join(OUT, "a"))
    print("\nNext: listen, pick one take per character, lock its voice:\n"
          "  python -m showrunner.stage0.run_stage0 voice --character paloma --from stories/_stage0/a/paloma_fr_s22.mp4")


def cmd_voice(args) -> None:
    os.makedirs(VOICES, exist_ok=True)
    dest = _voice_path(args.character)
    verify.extract_audio(args.source, dest, start_s=args.start, max_s=args.max_s)
    info = verify.report(dest)
    print(f"locked {dest}: {info['duration_s']} s, mean {info['mean_db']} dB")
    if info["silent"]:
        print("WARNING: that audio is silent; pick another take")


def _guard_line(raw: str, text: str, dest: str) -> str:
    """Cut a Chatterbox line where the line ends (verify.line_cut_s); the raw file is kept."""
    duration = verify.probe(raw)["duration_s"]
    cut = verify.line_cut_s(text, verify.silences(raw), duration)
    verify.extract_audio(raw, dest, max_s=cut)
    print(f"line guard {os.path.basename(dest)}: {duration:.1f} s -> {cut:.2f} s")
    return dest


def _tts_jobs(ep: rp.Endpoint, lines: list, lang: str, out_dir: str, *, reuse: bool = False) -> dict:
    """*lines*: ``[(stem, char_id, text)]`` -> ``{stem: wav path}`` (Chatterbox, the locked voices),
    each cut by the line guard. *reuse*: guard the lines already in *out_dir* and send only the
    missing ones (same voices, no new TTS spend)."""
    os.makedirs(out_dir, exist_ok=True)
    text_of = {stem: text for stem, _, text in lines}
    raws = {}
    for stem, _, _ in lines:
        raw, old = os.path.join(out_dir, f"{stem}_raw.wav"), os.path.join(out_dir, f"{stem}.wav")
        if reuse and not os.path.exists(raw) and os.path.exists(old):
            os.replace(old, raw)  # a line made before the guard existed
        if reuse and os.path.exists(raw):
            raws[stem] = raw
    jobs = [(stem, "tts_chatterbox_line",
             {"text": text, "language": M.CHATTERBOX_LANGUAGE[lang], "exaggeration": 0.5, "seed": 7},
             {"voice_ref": _voice_path(char_id)}) for stem, char_id, text in lines if stem not in raws]
    for stem, paths, _, _ in (run_batch(ep, jobs, out_dir) if jobs else []):
        src = next((p for p in paths if p.endswith((".flac", ".wav", ".mp3", ".mp4"))), None)
        if src:  # flac from SaveAudio (patched handler); an mp4 wrapper would demux the same way
            raws[stem] = verify.extract_audio(src, os.path.join(out_dir, f"{stem}_raw.wav"))
    return {stem: _guard_line(raw, text_of[stem], os.path.join(out_dir, f"{stem}.wav")) for stem, raw in raws.items()}


def _seconds_for(wav: str) -> float:
    d = verify.probe(wav)["duration_s"] + 0.7
    return float(min(max(round(d * 2) / 2, 3.0), 10.0))


def cmd_b(args) -> None:
    ep = _video_endpoint(args)
    chars = _ready_characters()
    _need_voices(chars)
    out_dir = os.path.join(OUT, "b")
    # 1. the lines, one TTS job each
    lines = [(f"tts_{c}_{args.lang}", c, M.LINES[c][args.lang]) for c in chars]
    for key, ex in M.EXCHANGES.items():
        for k, (who, text) in enumerate(ex["lines"][args.lang]):
            lines.append((f"tts_ex_{key}_{args.lang}_{k}_{who}", who, text))
    wavs = _tts_jobs(ep, lines, args.lang, os.path.join(out_dir, "tts"), reuse=args.reuse_tts)
    # 2. the clips
    jobs = []
    for c in chars:
        wav = wavs.get(f"tts_{c}_{args.lang}")
        if not wav:
            continue
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx25_a2v_speech",
                         {"prompt": M.prompt_path_b(c, args.lang), "negative": M.negative(c), "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": _seconds_for(wav), "fps": M.FPS,
                          "sampler": args.sampler},
                         {"image": M.CHARACTERS[c]["keyframe"], "audio": wav}))
    for key, ex in M.EXCHANGES.items():
        kf = ex["keyframe"] or KF_THREE
        parts = [wavs.get(f"tts_ex_{key}_{args.lang}_{k}_{who}") for k, (who, _) in enumerate(ex["lines"][args.lang])]
        if not os.path.exists(kf) or not all(parts):
            print(f"skip exchange '{key}' (keyframe or a line missing)")
            continue
        joined = verify.concat_audio(parts, os.path.join(out_dir, "tts", f"ex_{key}_{args.lang}.wav"))
        for seed in args.seeds[:2]:
            jobs.append((f"ex_{key}_{args.lang}_s{seed}", "ltx25_a2v_speech",
                         {"prompt": M.prompt_exchange_b(key, args.lang), "negative": M.exchange_negative(key), "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": _seconds_for(joined), "fps": M.FPS,
                          "sampler": args.sampler},
                         {"image": kf, "audio": joined}))
    run_batch(ep, jobs, out_dir)


def cmd_c(args) -> None:
    ep = _video_endpoint(args)
    chars = _ready_characters()
    _need_voices(chars)
    jobs = []
    for c in chars:
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx23_idlora_speech",
                         {"prompt": M.prompt_path_c(c, args.lang), "negative": M.negative(c), "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": M.SINGLE_SECONDS, "fps": M.FPS,
                          "identity_guidance": args.identity_guidance},
                         {"image": M.CHARACTERS[c]["keyframe"], "voice_ref": _voice_path(c)}))
    # ID-LoRA is a single-speaker method: the exchanges run with the first speaker's voice only, as a
    # documented failure/limit test (D6), not as a candidate.
    for key, ex in M.EXCHANGES.items():
        kf = ex["keyframe"] or KF_THREE
        if not os.path.exists(kf):
            continue
        first = ex["speakers"][0]
        speech = " ".join(text for _, text in ex["lines"][args.lang])
        prompt = M.prompt_path_c(first, args.lang, line=speech).replace(
            M.CHARACTERS[first]["setting"], ex["setting"])
        jobs.append((f"ex_{key}_{args.lang}_s{args.seeds[0]}", "ltx23_idlora_speech",
                     {"prompt": prompt, "negative": M.exchange_negative(key), "seed": args.seeds[0], "width": M.WIDTH,
                      "height": M.HEIGHT, "seconds": M.EXCHANGE_SECONDS, "fps": M.FPS,
                      "identity_guidance": args.identity_guidance},
                     {"image": kf, "voice_ref": _voice_path(first)}))
    run_batch(ep, jobs, os.path.join(OUT, "c"))


def _keyframe3_job() -> tuple:
    """``(stem, template, values, files)`` of the three-character keyframe (multi-reference edit)."""
    ex = M.EXCHANGES["three"]
    heads = " ".join(M.CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    prompt = (f"{M.exchange_medium('three')} {heads} The three of them stand close together by a desk in {ex['setting']}, "
              f"facing each other mid-conversation, medium three-shot, only these three characters, nobody else. "
              f"Same fruit heads, same outfits as the reference images.")
    values = {"prompt": prompt, "negative": M.exchange_negative("three") + ", people, crowd, fourth character",
              "seed": 5, "width": M.WIDTH, "height": M.HEIGHT}
    files = comfy_templates.multiref_files([M.CHARACTERS[s]["ref"] for s in ex["speakers"]])
    return "kf_three", "edit_flux2_klein_multiref", values, files


def cmd_keyframe3(args) -> None:
    """The three-character keyframe (showrunner's Flux 2 Klein multi-reference template)."""
    ep = _images_endpoint(args)
    stem, template, values, files = _keyframe3_job()
    job = rp.submit_template(ep, template, dict(values, name=stem), files, execution_timeout_s=600)
    print("submitted keyframe3 ->", job)
    status = ep.wait(job)
    paths = rp.save_outputs(status, OUT, stem="kf_three")
    png = next((p for p in paths if p.endswith(".png")), None)
    if png and png != KF_THREE:
        os.replace(png, KF_THREE)
    ran = rp.billed_seconds(status)
    print(f"keyframe: {KF_THREE} ran {ran:.0f} s (≈ ${ran * IMAGES_USD_PER_S:.3f}; waited {rp.delay_seconds(status):.0f} s, "
          f"not billed)")


def cmd_keyframe(args) -> None:
    """Start-image candidates for a character with none (t2i_flux2_klein), or ``--pick`` one (free)."""
    char = M.CHARACTERS[args.character]
    dest = char["keyframe"]
    cand = lambda seed: os.path.join(OUT, f"kf_{args.character}_s{seed}.png")  # noqa: E731
    if args.pick is not None:
        src = cand(args.pick)
        if not os.path.exists(src):
            sys.exit(f"no candidate {src}: run `keyframe --character {args.character}` first")
        shutil.copyfile(src, dest)
        print(f"locked {dest} (from seed {args.pick})")
        return
    ep = _images_endpoint(args)
    submitted = []
    for seed in args.seeds:
        values = {"prompt": M.prompt_keyframe(args.character), "negative": M.negative(args.character), "seed": seed,
                  "width": M.WIDTH, "height": M.HEIGHT, "name": f"kf_{args.character}_s{seed}"}
        job = rp.submit_template(ep, "t2i_flux2_klein", values, {}, execution_timeout_s=600)
        print(f"submitted kf_{args.character}_s{seed} -> {job}")
        submitted.append((seed, job))
    total = 0.0
    for seed, job in submitted:
        status = ep.wait(job)
        paths = rp.save_outputs(status, OUT, stem=f"kf_{args.character}_s{seed}")
        png = next((p for p in paths if p.endswith(".png")), None)
        if png and png != cand(seed):
            os.replace(png, cand(seed))
        total += rp.billed_seconds(status)
        print(f"candidate {cand(seed)}")
    print(f"ran {total:.0f} GPU-seconds (≈ ${total * IMAGES_USD_PER_S:.3f}). Look at them, then: "
          f"keyframe --character {args.character} --pick <seed>")


# A voice locked FROM a take cannot test the conversion of that same take: use another seed.
VC_SOURCE_SEED = {"camille": 22}  # camille.wav was cut from camille_fr_s33 (2026-10-08)
VC_DIR = os.path.join(OUT, "vc")


def _vc_jobs(take: int, lang: str) -> list:
    """``[(stem, template, values, files, clip)]`` for the voice-conversion test: per character with a
    locked voice, the audio of its path (a) take as ``input``, the locked voice as ``target_voice``."""
    jobs = []
    for c in M.CHARACTERS:
        seed = VC_SOURCE_SEED.get(c, take)
        clip = os.path.join(OUT, "a", f"{c}_{lang}_s{seed}.mp4")
        if not os.path.exists(clip) or not os.path.exists(_voice_path(c)):
            print(f"skip {c}: needs {os.path.relpath(clip, OUT)} and voices/{c}.wav")
            continue
        stem = f"{c}_{lang}_s{seed}_vc"
        jobs.append((stem, "vc_chatterbox", {"seed": 0},
                     {"input": os.path.join(VC_DIR, f"{c}_{lang}_s{seed}_in.wav"), "target_voice": _voice_path(c)},
                     clip))
    return jobs


remux = voice.remux  # moved to showrunner/voice.py (stage 2.3); the name stays for this runner


def _vc_exchange_jobs(take: int, lang: str, *, verdicts: dict | None = None) -> list:
    """Per-speaker voice conversion of the exchanges: ``[(clip, stem, parts)]`` where *parts* is
    ``[(stem, template, values, files, seconds)]``, one per line, cut by the clip check's timings
    (:func:`verify.speaker_parts`). *verdicts* ``{clip stem: verify_take verdict}`` skips the check (tests)."""
    out = []
    for key, ex in M.EXCHANGES.items():
        stem = f"ex_{key}_{lang}_s{take}"
        clip = os.path.join(OUT, "a", f"{stem}.mp4")
        speakers = [who for who, _ in ex["lines"][lang]]
        missing = [c for c in dict.fromkeys(speakers) if not os.path.exists(_voice_path(c))]
        if not os.path.exists(clip) or missing:
            print(f"skip {stem}: needs a/{stem}.mp4 and the locked voices of {', '.join(missing) or '-'}")
            continue
        verdict = (verdicts or {}).get(stem) or verify.verify_take(clip, ex["lines"][lang], lang)
        try:
            cuts = verify.speaker_parts(verdict, verdict.get("duration_s") or verify.probe(clip)["duration_s"])
        except ValueError as exc:
            print(f"skip {stem}: {exc}")
            continue
        parts = []
        for k, (who, start, end) in enumerate(cuts):
            pstem = f"{stem}_l{k}_{who}_vc"
            parts.append((pstem, "vc_chatterbox", {"seed": 0},
                          {"input": os.path.join(VC_DIR, f"{stem}_l{k}_{who}_in.wav"), "target_voice": _voice_path(who)},
                          (start, end)))
        out.append((clip, stem, parts))
    return out


def cmd_vc(args) -> None:
    if args.exchanges:
        return _cmd_vc_exchanges(args)
    jobs = _vc_jobs(args.take, args.lang)
    if not jobs:
        sys.exit("nothing to convert")
    print(f"{len(jobs)} voice-conversion jobs on {args.video_endpoint}: ≈ $0.05-0.10 warm, up to ≈ $0.20 cold "
          f"(model load); the queue is not billed")
    if args.dry_run:
        for stem, _, _, files, clip in jobs:
            print(f"  {stem}: {os.path.relpath(clip, OUT)} -> voice of {os.path.relpath(files['target_voice'], OUT)}")
        return
    os.makedirs(VC_DIR, exist_ok=True)
    for _, _, _, files, clip in jobs:
        verify.extract_audio(clip, files["input"])
    results = run_batch(_video_endpoint(args), [j[:4] for j in jobs], VC_DIR)
    clips = {j[0]: j[4] for j in jobs}
    for stem, paths, _, status in results:
        src = next((p for p in paths if p.endswith((".flac", ".wav", ".mp3"))), None)
        if not src:
            print(f"no audio back for {stem} ({status})")
            continue
        dest = remux(clips[stem], src, os.path.join(VC_DIR, f"{stem[:-3]}.mp4"))
        print(f"listen: {dest}  (original: {clips[stem]})")


def _cmd_vc_exchanges(args) -> None:
    plans = _vc_exchange_jobs(args.exchange_take, args.lang)
    count = sum(len(p) for _, _, p in plans)
    if not count:
        sys.exit("nothing to convert")
    print(f"{count} voice-conversion jobs (one per line) for {len(plans)} exchanges on {args.video_endpoint}: "
          f"≈ $0.01 warm, up to ≈ $0.05 cold; the queue is not billed")
    for clip, stem, parts in plans:
        print(f"  {stem}: " + " | ".join(f"{p[0].split('_l')[1][:-3]} {p[4][0]:.2f}-{p[4][1]:.2f} s" for p in parts))
    if args.dry_run:
        return
    os.makedirs(VC_DIR, exist_ok=True)
    for clip, _, parts in plans:
        for _, _, _, files, (start, end) in parts:
            verify.extract_audio(clip, files["input"], start_s=start, max_s=round(end - start, 3))
    jobs = [p[:4] for _, _, parts in plans for p in parts]
    results = {stem: paths for stem, paths, _, _ in run_batch(_video_endpoint(args), jobs, VC_DIR)}
    for clip, stem, parts in plans:
        back = []
        for pstem, _, _, _, (start, end) in parts:
            src = next((p for p in results.get(pstem, []) if p.endswith((".flac", ".wav", ".mp3"))), None)
            if not src:
                print(f"no audio back for {pstem}: {stem} not rebuilt")
                break
            back.append((src, end - start))
        else:
            joined = verify.join_parts(back, os.path.join(VC_DIR, f"{stem}_vc.wav"))
            dest = remux(clip, joined, os.path.join(VC_DIR, f"{stem}.mp4"))
            print(f"listen: {dest}  (original: {clip})")

def cmd_verify(args) -> None:
    """The clip check on one clip (free, this host's CPU): ``--line "who: text"`` once per scripted line."""
    lines = []
    for raw in args.line or []:
        who, _, text = raw.partition(":")
        if not text.strip():
            sys.exit(f"--line needs 'who: text', got {raw!r}")
        lines.append((who.strip(), text.strip()))
    if not lines:
        sys.exit("verify needs at least one --line")
    started = time.time()
    v = verify.verify_take(args.clip, lines, args.lang)
    print(f"{os.path.basename(args.clip)}: {v['state']}  matched {v['matched']:.0%}  in order {v['in_order']}  "
          f"speech {v['start_s']}-{v['end_s']} s of {v['duration_s']} s  extra after {v['extra_after_s']} s  "
          f"({time.time() - started:.0f} s)")
    for row in v["lines"]:
        print(f"  {row['speaker']}: {row['heard']}/{row['words']} words, {row['start_s']}-{row['end_s']} s  {row['text']}")
    print(f"  heard: {v['heard_text']}")


def cmd_review(args) -> None:
    rows = []
    for sub in ("smoke", "a", "b", "c"):
        d = os.path.join(OUT, sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".mp4"):
                continue
            path = os.path.join(d, f)
            info = verify.report(path)
            sheet = verify.contact_sheet(path, os.path.join(d, f[:-4] + "_sheet.jpg"))
            rows.append((sub, f, info, os.path.relpath(sheet, OUT)))
    lines = ["# Stage 0 review", "", f"{len(rows)} clips. Fill the last column after watching: ok / voice / lips / identity / motion / other.", "",
             "| path | clip | s | audio | mean dB | size MB | sheet | verdict |", "|---|---|---|---|---|---|---|---|"]
    for sub, f, info, sheet in rows:
        lines.append(f"| {sub} | {f} | {info['duration_s']} | {'yes' if info['has_audio'] and not info['silent'] else 'SILENT'} "
                     f"| {info['mean_db']} | {info['size_mb']} | {sheet} |  |")
    dest = os.path.join(OUT, "review.md")
    os.makedirs(OUT, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("\nwrote", dest)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["preflight", "smoke", "a", "voice", "b", "c", "keyframe3", "keyframe", "review", "vc", "verify"])
    ap.add_argument("--video-endpoint", help="RunPod endpoint id of the showrunner video worker; default "
                                             "RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID (never the live app's "
                                             "RUNPOD_COMFY_ENDPOINT_ID)")
    ap.add_argument("--images-endpoint", help="RunPod endpoint id of the images worker (Flux 2 Klein); "
                                              "default RUNPOD_IMAGE_ENDPOINT_ID from the environment or .env")
    ap.add_argument("--lang", choices=["fr", "en"], default="fr")
    ap.add_argument("--seeds", type=int, nargs="+", default=M.SEEDS)
    ap.add_argument("--reuse-tts", action="store_true",
                    help="path b: guard the TTS lines already made instead of making them again")
    ap.add_argument("--sampler", default="euler", help="path b sampler (euler recommended for A2V lip-sync)")
    ap.add_argument("--identity-guidance", type=float, default=3.0, help="path c LTXVReferenceAudio scale")
    ap.add_argument("--character", choices=sorted(M.CHARACTERS), help="voice / keyframe: character id")
    ap.add_argument("--pick", type=int, help="keyframe: lock the candidate of this seed as the start image (free)")
    ap.add_argument("--from", dest="source", help="voice: the mp4 (or wav) whose audio becomes the reference")
    ap.add_argument("--start", type=float, default=0.0, help="voice: skip this many seconds")
    ap.add_argument("--max-s", type=float, default=10.0, help="voice: keep at most this many seconds")
    ap.add_argument("--take", type=int, default=33, help="vc: the seed of the path (a) take to convert")
    ap.add_argument("--dry-run", action="store_true", help="vc: list the jobs, submit nothing")
    ap.add_argument("--exchanges", action="store_true",
                    help="vc: the multi-speaker clips, each line converted to its own speaker's locked voice")
    ap.add_argument("--exchange-take", type=int, default=22, help="vc --exchanges: the seed of the exchange take")
    ap.add_argument("--clip", help="verify: the clip to check")
    ap.add_argument("--line", action="append", help="verify: 'who: text', once per scripted line, in order")
    args = ap.parse_args()
    if not args.video_endpoint:
        try:
            args.video_endpoint = rp.api_key(name="RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID")
        except rp.RunPodError:
            pass
    if not args.images_endpoint:
        try:
            args.images_endpoint = rp.api_key(name="RUNPOD_IMAGE_ENDPOINT_ID")
        except rp.RunPodError:
            pass
    if args.command == "preflight":
        from showrunner.stage0 import preflight
        sys.exit(1 if preflight.run(args) else 0)
    if args.command in ("smoke", "a", "b", "c", "vc") and not args.video_endpoint:
        sys.exit("--video-endpoint is required")
    if args.command == "keyframe3" and not args.images_endpoint:
        sys.exit("--images-endpoint is required")
    if args.command == "keyframe" and not args.character:
        sys.exit("keyframe needs --character")
    if args.command == "keyframe" and args.pick is None and not args.images_endpoint:
        sys.exit("--images-endpoint is required (or --pick <seed> to lock a candidate)")
    if args.command == "verify" and not args.clip:
        sys.exit("verify needs --clip")
    if args.command == "voice" and not (args.character and args.source):
        sys.exit("voice needs --character and --from")
    {"smoke": cmd_smoke, "a": cmd_a, "voice": cmd_voice, "b": cmd_b, "c": cmd_c,
     "keyframe3": cmd_keyframe3, "keyframe": cmd_keyframe, "review": cmd_review, "vc": cmd_vc, "verify": cmd_verify}[args.command](args)


if __name__ == "__main__":
    main()
