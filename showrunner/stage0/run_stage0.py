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
        the three-character keyframe for the 3-speaker exchange (existing edit_flux2_klein_multiref template)
    python -m showrunner.stage0.run_stage0 keyframe  --character camille --images-endpoint <id> [--seeds 1 2]
        start-image candidates for a character with no keyframe (t2i_flux2_klein); then, free:
    python -m showrunner.stage0.run_stage0 keyframe  --character camille --pick 2
    python -m showrunner.stage0.run_stage0 preflight --video-endpoint <id>
        free, read-only: HF licenses, the image on GHCR, both endpoints and their keys, local files, batch sizes
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

from showrunner import runpod_client as rp  # noqa: E402
from showrunner import verify  # noqa: E402
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
    results = []
    total = 0.0
    for stem, job_id, template, values in submitted:
        status = endpoint.wait(job_id, poll_s=poll_s)
        try:
            paths = rp.save_outputs(status, out_dir, stem=stem)
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


def _tts_jobs(ep: rp.Endpoint, lines: list, lang: str, out_dir: str) -> dict:
    """*lines*: ``[(stem, char_id, text)]`` -> ``{stem: wav path}`` (Chatterbox, the locked voices)."""
    jobs = [(stem, "tts_chatterbox_line",
             {"text": text, "language": M.CHATTERBOX_LANGUAGE[lang], "exaggeration": 0.5, "seed": 7},
             {"voice_ref": _voice_path(char_id)}) for stem, char_id, text in lines]
    results = run_batch(ep, jobs, out_dir)
    wavs = {}
    for stem, paths, _, _ in results:
        src = next((p for p in paths if p.endswith((".flac", ".wav", ".mp3", ".mp4"))), None)
        if src:  # flac from SaveAudio (patched handler); an mp4 wrapper would demux the same way
            wavs[stem] = verify.extract_audio(src, os.path.join(out_dir, f"{stem}.wav"))
    return wavs


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
    wavs = _tts_jobs(ep, lines, args.lang, os.path.join(out_dir, "tts"))
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


def cmd_keyframe3(args) -> None:
    """The three-character keyframe with the repo's existing Flux 2 Klein multi-reference template."""
    from clipping.providers.local_comfyui import load_template, render_template  # the images endpoint's templates

    ep = _images_endpoint(args)
    ex = M.EXCHANGES["three"]
    refs = [M.CHARACTERS[s]["ref"] for s in ex["speakers"]]
    names = [f"kf3_ref{k}.png" for k in range(len(refs))]
    heads = " ".join(M.CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    prompt = (f"{M.exchange_medium('three')} {heads} The three of them stand close together by a desk in {ex['setting']}, "
              f"facing each other mid-conversation, medium three-shot, only these three characters, nobody else. "
              f"Same fruit heads, same outfits as the reference images.")
    graph = render_template(load_template("edit_flux2_klein_multiref"), {
        "prompt": prompt, "negative": M.exchange_negative("three") + ", people, crowd, fourth character", "seed": 5,
        "width": M.WIDTH, "height": M.HEIGHT, "ref_paths": names})
    payload = {"workflow": graph, "images": [rp._file_entry(n, p) for n, p in zip(names, refs)]}
    job = ep.run(payload, execution_timeout_s=600)
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
    from clipping.providers.local_comfyui import load_template, render_template  # the images endpoint's templates

    ep = _images_endpoint(args)
    template = load_template("t2i_flux2_klein")
    submitted = []
    for seed in args.seeds:
        graph = render_template(template, {"prompt": M.prompt_keyframe(args.character),
                                           "negative": M.negative(args.character), "seed": seed,
                                           "width": M.WIDTH, "height": M.HEIGHT})
        job = ep.run({"workflow": graph}, execution_timeout_s=600)
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
    ap.add_argument("command", choices=["preflight", "smoke", "a", "voice", "b", "c", "keyframe3", "keyframe", "review"])
    ap.add_argument("--video-endpoint", help="RunPod endpoint id of the showrunner video worker; default "
                                             "RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID (never the live app's "
                                             "RUNPOD_COMFY_ENDPOINT_ID)")
    ap.add_argument("--images-endpoint", help="RunPod endpoint id of the images worker (Flux 2 Klein); "
                                              "default RUNPOD_IMAGE_ENDPOINT_ID from the environment or .env")
    ap.add_argument("--lang", choices=["fr", "en"], default="fr")
    ap.add_argument("--seeds", type=int, nargs="+", default=M.SEEDS)
    ap.add_argument("--sampler", default="euler", help="path b sampler (euler recommended for A2V lip-sync)")
    ap.add_argument("--identity-guidance", type=float, default=3.0, help="path c LTXVReferenceAudio scale")
    ap.add_argument("--character", choices=sorted(M.CHARACTERS), help="voice / keyframe: character id")
    ap.add_argument("--pick", type=int, help="keyframe: lock the candidate of this seed as the start image (free)")
    ap.add_argument("--from", dest="source", help="voice: the mp4 (or wav) whose audio becomes the reference")
    ap.add_argument("--start", type=float, default=0.0, help="voice: skip this many seconds")
    ap.add_argument("--max-s", type=float, default=10.0, help="voice: keep at most this many seconds")
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
    if args.command in ("smoke", "a", "b", "c") and not args.video_endpoint:
        sys.exit("--video-endpoint is required")
    if args.command == "keyframe3" and not args.images_endpoint:
        sys.exit("--images-endpoint is required")
    if args.command == "keyframe" and not args.character:
        sys.exit("keyframe needs --character")
    if args.command == "keyframe" and args.pick is None and not args.images_endpoint:
        sys.exit("--images-endpoint is required (or --pick <seed> to lock a candidate)")
    if args.command == "voice" and not (args.character and args.source):
        sys.exit("voice needs --character and --from")
    {"smoke": cmd_smoke, "a": cmd_a, "voice": cmd_voice, "b": cmd_b, "c": cmd_c,
     "keyframe3": cmd_keyframe3, "keyframe": cmd_keyframe, "review": cmd_review}[args.command](args)


if __name__ == "__main__":
    main()
