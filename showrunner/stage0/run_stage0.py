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
    python -m showrunner.stage0.run_stage0 review
        probes every clip (duration, audio, loudness), writes contact sheets and stories/_stage0/review.md

``RUNPOD_API_KEY`` from the environment or ``.env``. Nothing here calls an LLM.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from showrunner import runpod_client as rp  # noqa: E402
from showrunner import verify  # noqa: E402
from showrunner.stage0 import matrix as M  # noqa: E402

OUT = os.path.join(M.REPO_ROOT, "stories", "_stage0")
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
        billed = rp.billed_seconds(status)
        total += billed
        results.append((stem, paths, billed, status.get("status")))
        with open(os.path.join(out_dir, "jobs.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"stem": stem, "job": job_id, "template": template, "status": status.get("status"),
                                 "billed_s": billed, "paths": paths, "values": {k: v for k, v in values.items()
                                                                                 if k not in ("prompt",)},
                                 "prompt": values.get("prompt"), "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                ensure_ascii=False) + "\n")
        print(f"done {stem}: {status.get('status')} billed {billed:.0f} s -> {[os.path.basename(p) for p in paths]}")
    print(f"batch billed {total:.0f} GPU-seconds (≈ ${total * 0.00049:.2f} on an L40S flex worker)")
    return results


def _voice_path(char_id: str) -> str:
    return os.path.join(VOICES, f"{char_id}.wav")


def _need_voices(chars: list) -> None:
    missing = [c for c in chars if not os.path.exists(_voice_path(c))]
    if missing:
        sys.exit(f"no locked voice for {', '.join(missing)}: run `voice --character <id> --from <clip.mp4>` "
                 f"on a path (a) take first (stories/_stage0/a/)")


# ------------------------------------------------------------------ commands

def cmd_smoke(args) -> None:
    ep = rp.Endpoint(args.video_endpoint)
    c = "paloma"
    jobs = [(f"smoke_{c}", "ltx25_i2v_speech",
             {"prompt": M.prompt_path_a(c, args.lang), "negative": M.NEGATIVE, "seed": 11, "width": M.WIDTH,
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
                         {"prompt": M.prompt_exchange_a(key, lang), "negative": M.NEGATIVE, "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": M.EXCHANGE_SECONDS, "fps": M.FPS},
                         {"image": kf}))
    return jobs


def cmd_a(args) -> None:
    ep = rp.Endpoint(args.video_endpoint)
    jobs = []
    for c in M.CHARACTERS:
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx25_i2v_speech",
                         {"prompt": M.prompt_path_a(c, args.lang), "negative": M.NEGATIVE, "seed": seed,
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
    ep = rp.Endpoint(args.video_endpoint)
    _need_voices(list(M.CHARACTERS))
    out_dir = os.path.join(OUT, "b")
    # 1. the lines, one TTS job each
    lines = [(f"tts_{c}_{args.lang}", c, M.LINES[c][args.lang]) for c in M.CHARACTERS]
    for key, ex in M.EXCHANGES.items():
        for k, (who, text) in enumerate(ex["lines"][args.lang]):
            lines.append((f"tts_ex_{key}_{args.lang}_{k}_{who}", who, text))
    wavs = _tts_jobs(ep, lines, args.lang, os.path.join(out_dir, "tts"))
    # 2. the clips
    jobs = []
    for c in M.CHARACTERS:
        wav = wavs.get(f"tts_{c}_{args.lang}")
        if not wav:
            continue
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx25_a2v_speech",
                         {"prompt": M.prompt_path_b(c, args.lang), "negative": M.NEGATIVE, "seed": seed,
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
                         {"prompt": M.prompt_exchange_b(key, args.lang), "negative": M.NEGATIVE, "seed": seed,
                          "width": M.WIDTH, "height": M.HEIGHT, "seconds": _seconds_for(joined), "fps": M.FPS,
                          "sampler": args.sampler},
                         {"image": kf, "audio": joined}))
    run_batch(ep, jobs, out_dir)


def cmd_c(args) -> None:
    ep = rp.Endpoint(args.video_endpoint)
    _need_voices(list(M.CHARACTERS))
    jobs = []
    for c in M.CHARACTERS:
        for seed in args.seeds:
            jobs.append((f"{c}_{args.lang}_s{seed}", "ltx23_idlora_speech",
                         {"prompt": M.prompt_path_c(c, args.lang), "negative": M.NEGATIVE, "seed": seed,
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
                     {"prompt": prompt, "negative": M.NEGATIVE, "seed": args.seeds[0], "width": M.WIDTH,
                      "height": M.HEIGHT, "seconds": M.EXCHANGE_SECONDS, "fps": M.FPS,
                      "identity_guidance": args.identity_guidance},
                     {"image": kf, "voice_ref": _voice_path(first)}))
    run_batch(ep, jobs, os.path.join(OUT, "c"))


def cmd_keyframe3(args) -> None:
    """The three-character keyframe with the repo's existing Flux 2 Klein multi-reference template."""
    from clipping.providers.local_comfyui import load_template, render_template  # the images endpoint's templates

    # The images endpoint has its own key in the app (RUNPOD_IMAGE_API_KEY); fall back to the main one.
    try:
        key = rp.api_key(name="RUNPOD_IMAGE_API_KEY")
    except rp.RunPodError:
        key = None
    ep = rp.Endpoint(args.images_endpoint, key=key)
    ex = M.EXCHANGES["three"]
    refs = [M.CHARACTERS[s]["ref"] for s in ex["speakers"]]
    names = [f"kf3_ref{k}.png" for k in range(len(refs))]
    heads = " ".join(M.CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    prompt = (f"{M.MEDIUM} {heads} The three of them stand close together by a desk in {ex['setting']}, "
              f"facing each other mid-conversation, medium three-shot, only these three characters, nobody else. "
              f"Same fruit heads, same outfits as the reference images.")
    graph = render_template(load_template("edit_flux2_klein_multiref"), {
        "prompt": prompt, "negative": M.NEGATIVE + ", people, crowd, fourth character", "seed": 5,
        "width": M.WIDTH, "height": M.HEIGHT, "ref_paths": names})
    payload = {"workflow": graph, "images": [rp._file_entry(n, p) for n, p in zip(names, refs)]}
    job = ep.run(payload, execution_timeout_s=600)
    print("submitted keyframe3 ->", job)
    status = ep.wait(job)
    paths = rp.save_outputs(status, OUT, stem="kf_three")
    png = next((p for p in paths if p.endswith(".png")), None)
    if png and png != KF_THREE:
        os.replace(png, KF_THREE)
    print("keyframe:", KF_THREE, "billed", rp.billed_seconds(status), "s")


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
    ap.add_argument("command", choices=["smoke", "a", "voice", "b", "c", "keyframe3", "review"])
    ap.add_argument("--video-endpoint", help="RunPod endpoint id of the showrunner video worker")
    ap.add_argument("--images-endpoint", help="RunPod endpoint id of the images worker (Flux 2 Klein)")
    ap.add_argument("--lang", choices=["fr", "en"], default="fr")
    ap.add_argument("--seeds", type=int, nargs="+", default=M.SEEDS)
    ap.add_argument("--sampler", default="euler", help="path b sampler (euler recommended for A2V lip-sync)")
    ap.add_argument("--identity-guidance", type=float, default=3.0, help="path c LTXVReferenceAudio scale")
    ap.add_argument("--character", help="voice: character id")
    ap.add_argument("--from", dest="source", help="voice: the mp4 (or wav) whose audio becomes the reference")
    ap.add_argument("--start", type=float, default=0.0, help="voice: skip this many seconds")
    ap.add_argument("--max-s", type=float, default=10.0, help="voice: keep at most this many seconds")
    args = ap.parse_args()
    if args.command in ("smoke", "a", "b", "c") and not args.video_endpoint:
        sys.exit("--video-endpoint is required")
    if args.command == "keyframe3" and not args.images_endpoint:
        sys.exit("--images-endpoint is required")
    if args.command == "voice" and not (args.character and args.source):
        sys.exit("voice needs --character and --from")
    {"smoke": cmd_smoke, "a": cmd_a, "voice": cmd_voice, "b": cmd_b, "c": cmd_c,
     "keyframe3": cmd_keyframe3, "review": cmd_review}[args.command](args)


if __name__ == "__main__":
    main()
