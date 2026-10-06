#!/usr/bin/env python3
"""Voice the 18 lines of "Accès refusé" episode 1 on the RunPod ComfyUI worker (plan 31).

COSTS MONEY (GPU seconds), and only with --go: without it the script prints the
plan and the estimate and stops. It reads the lines from tools/render_ep01.py
(spoken_lines()), submits one tts_chatterbox job per line through the MCP's job
client (the same journal, pricing and folder rules as the chat's comfy_submit),
with the speaker's frozen reference voice (outputs/acces_refuse/voices/
ref_<name>.wav, from tools/make_voice_refs.py) and a fixed seed per speaker,
waits for each, converts the FLAC the worker returns to
outputs/acces_refuse/ep01/voices/lNN.wav (the FLAC stays next to it) and keeps
the voices' manifest. Then: tools/render_ep01.py --use-existing-voices.

Runs on the host that holds RUNPOD_API_KEY (.env). The endpoint that serves
audio (RUNPOD_AUDIO_ENDPOINT_ID, else the image one) must run the TTS worker
image (docker/worker-comfyui-tts). Lines go one after the other by default:
one warm worker says a line in ~8 GPU-s, while every parallel worker pays its
own ~90 s cold start (--parallel N when the endpoint has N idle workers).

    python tools/voice_ep01_comfy.py                 # the plan and the estimate, nothing sent
    python tools/voice_ep01_comfy.py --go            # voice every line not yet done
    python tools/voice_ep01_comfy.py --go --only l03,l04 --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                           # mcp_server, clipping
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tools/ is not a package
import make_voice_refs  # noqa: E402
import render_ep01  # noqa: E402
from mcp_server import media  # noqa: E402
from mcp_server.config import load_settings  # noqa: E402
from mcp_server.runpod_jobs import JobClient, JobError  # noqa: E402

TEMPLATE = "tts_chatterbox"
DEST = "acces_refuse/ep01/voices"          # under outputs/: render_ep01's VOICE_DIR
# One fixed seed per speaker: the same voice every episode, every re-run.
SEEDS = {"RIDA": 1101, "MARIE-JEANNE": 2202, "ANANAS": 3303, "INÈS": 4404}
# Starting points for the Chatterbox knobs (exaggeration 0-2: intensity;
# cfg_weight 0-1: lower = slower, closer to the reference's pace).
KNOBS = {"RIDA": (0.35, 0.5), "MARIE-JEANNE": (0.5, 0.5), "ANANAS": (0.3, 0.3), "INÈS": (0.45, 0.6)}
COLD_START_S = 90.0     # the 3.2 GB weights' load on an idle worker
WARM_LINE_S = 8.0       # one short line on a warm worker
DEFAULT_RATE = 1.58     # USD per GPU hour, the RTX 5090 flex price (≈ $0.00044/s)
SAMPLE_RATE = 24000


def load_env_file(path: Path = ROOT / ".env") -> None:
    """The repo's .env into the environment (names not already set), for a
    python without python-dotenv; inline '# comments' dropped like dotenv does."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = re.split(r"\s+#", value, 1)[0].strip().strip('"').strip("'")
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value


def plan_lines(refs_dir: Path, voices_dir: Path, *, only=None, force: bool = False) -> list:
    """The 18 lines with their job: speaker, text, seed, knobs, reference path,
    and whether the WAV already exists (skipped unless *force*)."""
    wanted = {s.strip() for s in (only or "").split(",") if s.strip()}
    plan = []
    for line in render_ep01.spoken_lines():
        if wanted and line["id"] not in wanted:
            continue
        ref = make_voice_refs.ref_path(refs_dir, line["speaker"])
        if not ref.exists():
            sys.exit(f"missing reference voice {ref} for {line['speaker']}: run tools/make_voice_refs.py first")
        exaggeration, cfg_weight = KNOBS[line["speaker"]]
        wav = Path(voices_dir) / f"{line['id']}.wav"
        plan.append({**line, "ref": str(ref), "seed": SEEDS[line["speaker"]], "exaggeration": exaggeration,
                     "cfg_weight": cfg_weight, "wav": str(wav), "done": wav.exists() and not force})
    return plan


def estimate_usd(n_lines: int, rate: float, parallel: int = 1) -> tuple:
    """(GPU seconds, USD) for *n_lines*: one cold start per parallel worker, then warm lines."""
    seconds = min(max(parallel, 1), n_lines) * COLD_START_S + n_lines * WARM_LINE_S
    return seconds, round(seconds * rate / 3600.0, 3)


def flac_to_wav(src: str, dst: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ar", str(SAMPLE_RATE), "-ac", "1",
                    "-sample_fmt", "s16", dst], check=True)


def voice_lines(client: JobClient, plan: list, *, dest: str = DEST, timeout_s: float = 600.0, parallel: int = 1,
                convert=flac_to_wav, log=print) -> list:
    """Submit and settle the lines, *parallel* at a time; each done line gets
    its WAV and a manifest entry. Returns the records (failed ones included)."""
    records = []
    manifest_path = Path(client.dest_dir(dest)) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for start in range(0, len(plan), max(parallel, 1)):
        batch = plan[start:start + max(parallel, 1)]
        submitted = []
        for line in batch:
            record = client.submit(TEMPLATE, prompt=line["text"], seed=line["seed"], audio_path=line["ref"],
                                   exaggeration=line["exaggeration"], cfg_weight=line["cfg_weight"],
                                   name=line["id"], dest=dest, note=f"acces_refuse ep01 {line['speaker']}")
            log(f"sent  {line['id']} {line['speaker']}: {line['text']}  (job {record['job_id']})")
            submitted.append((line, record["job_id"]))
        for line, job_id in submitted:
            record = client.wait(job_id, timeout_s=timeout_s)
            records.append(record)
            if record["state"] != "COMPLETED" or not record.get("outputs"):
                log(f"FAILED {line['id']}: {record['state']} {record.get('error') or ''}")
                continue
            convert(record["outputs"][0], line["wav"])
            probe = {}
            try:
                probe = media.probe_audio(line["wav"])
            except media.MediaError:
                pass
            slug = make_voice_refs.BY_NAME[line["speaker"]]["slug"]
            manifest[line["id"]] = {"stamp": {"text": line["text"], "voice": f"chatterbox:{slug}"},
                                    "model": f"runpod/{TEMPLATE}", "speaker": line["speaker"], "seed": line["seed"],
                                    "job_id": job_id, "gpu_seconds": record.get("gpu_seconds"),
                                    "billed_usd": record.get("billed_usd"), "duration_s": probe.get("duration_s")}
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"done  {line['id']}: {probe.get('duration_s', '?')} s, {record.get('gpu_seconds')} GPU-s, "
                f"${record.get('billed_usd')}")
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--go", action="store_true", help="spend: submit the jobs (without it: plan + estimate)")
    parser.add_argument("--only", help="line ids, comma-separated (l03,l04)")
    parser.add_argument("--force", action="store_true", help="re-voice lines whose WAV exists")
    parser.add_argument("--parallel", type=int, default=1, help="lines in flight at once (default 1)")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait per line")
    parser.add_argument("--refs-dir", type=Path, default=make_voice_refs.REFS_DIR)
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not installed or not on PATH.")
    load_env_file()
    settings = load_settings()
    client = JobClient(settings)
    voices_dir = Path(client.dest_dir(DEST))
    plan = plan_lines(args.refs_dir, voices_dir, only=args.only, force=args.force)
    todo = [line for line in plan if not line["done"]]
    for line in plan:
        mark = "have " if line["done"] else "todo "
        print(f"{mark}{line['id']} {line['speaker']:<12} seed {line['seed']}  {line['text']}")
    rate = settings.rate("audio") or DEFAULT_RATE
    seconds, usd = estimate_usd(len(todo), rate, args.parallel)
    try:
        endpoint = settings.endpoint("audio")
    except RuntimeError as exc:
        sys.exit(str(exc))
    print(f"\n{len(todo)} line(s) to voice on endpoint {endpoint} (audio -> {settings.serving_kind('audio')}): "
          f"estimate ≈ {seconds:.0f} GPU-s ≈ ${usd} at ${rate}/h "
          f"({'one' if args.parallel <= 1 else args.parallel} cold start(s) + {WARM_LINE_S:.0f} s a line)")
    if not todo:
        print("nothing to do (every WAV exists; --force to re-voice)")
        return 0
    if not args.go:
        print("Nothing sent. Add --go to spend that.")
        return 0
    try:
        records = voice_lines(client, todo, timeout_s=args.timeout, parallel=args.parallel)
    except JobError as exc:
        sys.exit(f"RunPod refused: {exc}")
    failed = [r for r in records if r["state"] != "COMPLETED"]
    spent = sum(r.get("billed_usd") or 0 for r in records)
    gpu = sum(r.get("gpu_seconds") or 0 for r in records)
    print(f"\n{len(records) - len(failed)}/{len(records)} lines done, {gpu:.0f} GPU-s, ${spent:.3f} billed"
          f"{'; FAILED: ' + ', '.join(r['name'] for r in failed) if failed else ''}")
    print(f"Next: python tools/render_ep01.py --use-existing-voices")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
