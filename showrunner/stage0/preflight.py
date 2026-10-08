"""Free, read-only checks before stage 0 spends anything (plan 36).

One line per check, ``ok`` / ``MISSING`` with what to do. Nothing here submits a GPU job:

    python -m showrunner.stage0.run_stage0 preflight --video-endpoint <id> --images-endpoint <id>

What it cannot see (the restricted RunPod key has no account access): whether the network volume
holds the weights. The `smoke` clip is the proof of that.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request

from showrunner import runpod_client as rp
from showrunner.stage0 import matrix as M

IMAGE = "rzdhop/showrunner-worker"
IMAGE_TAG = "0.1.0"
# One small file per gated repo: a ranged GET of its first byte proves the license is accepted.
# Only LTX-2.5 is gated; LTX-2.3(-fp8) and the Comfy-Org files download without a token.
GATED = {
    "Lightricks/LTX-2.5": "vae/ltx-2.5-audio-vae-bf16.safetensors",
}


def _http(url: str, headers: dict | None = None, timeout: int = 20) -> tuple:
    """``(status, body text)``; never raises on an HTTP error."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2048).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2048).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def check_hf() -> list:
    try:
        token = rp.api_key(name="HF_TOKEN")
    except rp.RunPodError:
        return [("HF token", False, "HF_TOKEN empty here (needed only for the gated Lightricks/LTX-2.5, on the pod "
                                    "that fills the volume; put it in .env too if you want this check)")]
    rows = []
    for repo, path in GATED.items():
        code, _ = _http(f"https://huggingface.co/{repo}/resolve/main/{path}",
                        {"Authorization": f"Bearer {token}", "Range": "bytes=0-0"})
        ok = code in (200, 206, 302)
        rows.append((f"HF license {repo}", ok,
                     "accepted" if ok else f"HTTP {code}: accept it on https://huggingface.co/{repo} "
                                           "with the account of this HF_TOKEN"))
    return rows


def check_image() -> list:
    code, body = _http(f"https://ghcr.io/token?scope=repository:{IMAGE}:pull")
    token = json.loads(body).get("token", "") if code == 200 else ""
    code, _ = _http(f"https://ghcr.io/v2/{IMAGE}/manifests/{IMAGE_TAG}", {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, "
                  "application/vnd.docker.distribution.manifest.list.v2+json"})
    ok = code == 200
    return [(f"image ghcr.io/{IMAGE}:{IMAGE_TAG}", ok,
             "public, pullable" if ok else "not pullable anonymously: wait for the 'showrunner-worker image' "
                                           "build, then GitHub > Packages > showrunner-worker > Public")]


def check_endpoint(label: str, endpoint_id: str | None, key_name: str) -> list:
    if not endpoint_id:
        return [(f"{label} endpoint", False, "no id given")]
    try:
        key = rp.api_key(name=key_name)
    except rp.RunPodError:
        key = rp.api_key()
    try:
        health = rp.Endpoint(endpoint_id, key=key, timeout=20).health()
    except rp.RunPodError as exc:
        hint = " (the key does not cover this endpoint: add it to the key's scope)" if "HTTP 40" in str(exc) else ""
        return [(f"{label} endpoint {endpoint_id}", False, f"{str(exc)[:120]}{hint}")]
    w = health.get("workers", {})
    detail = (f"reachable; workers idle {w.get('idle', 0)} ready {w.get('ready', 0)} running {w.get('running', 0)} "
              f"initializing {w.get('initializing', 0)} throttled {w.get('throttled', 0)}")
    if w.get("throttled") and not (w.get("idle") or w.get("ready") or w.get("running")):
        detail += " — throttled only: that datacenter is short of this GPU right now"
    return [(f"{label} endpoint {endpoint_id}", True, detail)]


def check_local(seeds: list) -> list:
    rows = [("ffmpeg", shutil.which("ffmpeg") is not None, "needed by `voice` and `review`")]
    for cid, c in M.CHARACTERS.items():
        has_kf = os.path.exists(c["keyframe"])
        rows.append((f"keyframe {cid}", has_kf,
                     "present" if has_kf else f"run `keyframe --character {cid}`, then `--pick <seed>`"))
    kf3 = os.path.join(M.STAGE0_DIR, "kf_three.png")
    rows.append(("keyframe three-speaker", os.path.exists(kf3), "present" if os.path.exists(kf3) else "run `keyframe3`"))
    voices = os.path.join(M.STAGE0_DIR, "voices")
    locked = [cid for cid in M.CHARACTERS if os.path.exists(os.path.join(voices, f"{cid}.wav"))]
    rows.append(("locked voices (paths b, c)", len(locked) == len(M.CHARACTERS),
                 f"{len(locked)}/{len(M.CHARACTERS)} {locked}; lock with `voice --character <id> --from <clip or wav>`"))
    return rows


def batch_sizes(seeds: list) -> dict:
    """Jobs each paid batch would submit with the files present now (same rules as run_stage0)."""
    ready = [c for c, info in M.CHARACTERS.items() if os.path.exists(info["keyframe"])]
    kf3 = os.path.join(M.STAGE0_DIR, "kf_three.png")
    exchanges = [k for k, ex in M.EXCHANGES.items() if os.path.exists(ex["keyframe"] or kf3)]
    return {
        "smoke": 1,
        "a": len(ready) * len(seeds) + 2 * len(exchanges),
        "b": len(ready) * len(seeds) + 2 * len(exchanges),  # + one TTS job per line, cents
        "c": len(ready) * len(seeds) + len(exchanges),
    }


def format_report(rows: list, sizes: dict) -> str:
    out = []
    for name, ok, detail in rows:
        out.append(f"{'ok     ' if ok else 'MISSING'}  {name}: {detail}")
    out.append("")
    out.append("clips per batch with today's files: " + ", ".join(f"{k} {v}" for k, v in sizes.items()))
    out.append("not checkable from here: the weights on the network volume (the `smoke` clip proves them)")
    missing = sum(1 for _, ok, _ in rows if not ok)
    out.append(f"{missing} item(s) missing" if missing else "all checks pass: ready for `smoke`")
    return "\n".join(out)


def run(args) -> int:
    rows = check_hf() + check_image()
    rows += check_endpoint("video", args.video_endpoint, "RUNPOD_API_KEY")
    rows += check_endpoint("images", args.images_endpoint, "RUNPOD_IMAGE_API_KEY")
    rows += check_local(args.seeds)
    print(format_report(rows, batch_sizes(args.seeds)))
    return sum(1 for _, ok, _ in rows if not ok)
