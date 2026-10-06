"""One clip through the RunPod Serverless ComfyUI adapter, from the command line (DEC-310).

Sends the repo's template (``runpod/<template>``) with one keyframe to the
endpoint named in the environment or ``.env``, exactly as the app would
(``clipping.providers.runpod_comfyui``: one ``POST /run``, ``/status``
polled, the base64 ``.mp4`` written), prints RunPod's job states as they
change and, at the end, the GPU seconds billed and their price when
``RUNPOD_GPU_USD_PER_HOUR`` is set. PAID: a clip costs a few cents, a cold
worker a few more.

    python tools/runpod_smoke.py --image assets/images/rzdhop-clips-icon-1024.png
    python tools/runpod_smoke.py --template i2v_wan22_5b --seconds 3 --seed 9

The key is read from the environment or from ``.env`` in the repo root and
never printed. Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from clipping.providers import runpod_comfyui  # noqa: E402
from clipping.providers.generation import GenRequest  # noqa: E402
from clipping.providers.registry import Link  # noqa: E402

KEYS = ("RUNPOD_API_KEY", "RUNPOD_COMFY_ENDPOINT_ID", "RUNPOD_GPU_USD_PER_HOUR")


def credentials() -> dict:
    """The three RUNPOD_* values: the environment first, then ``.env``."""
    found = {name: os.environ.get(name, "").strip() for name in KEYS}
    env_file = os.path.join(ROOT, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            name, sep, value = line.strip().partition("=")
            if sep and name in KEYS and not found[name]:
                found[name] = value.strip().strip('"').strip("'")
    missing = [name for name in KEYS[:2] if not found[name]]
    if missing:
        sys.exit(f"not set (environment or .env): {', '.join(missing)}")
    return {name: value for name, value in found.items() if value}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", required=True, help="the keyframe (png/jpg)")
    ap.add_argument("--template", default="i2v_wan22_14b_lightning", choices=runpod_comfyui.TEMPLATES)
    ap.add_argument("--prompt", default="the character slowly turns toward the camera and smiles, gentle push-in")
    ap.add_argument("--negative", default="blurry, distorted, text, watermark, extra limbs")
    ap.add_argument("--seconds", type=int, default=5, help="the clip length the template sells")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "outputs"))
    args = ap.parse_args()

    creds = credentials()
    link = Link("runpod", args.template)
    ok, note = runpod_comfyui.RUNPOD_COMFY.probe(link, credentials=creds)
    print(("health: " if ok else "health FAILED: ") + note)
    if not ok:
        sys.exit(1)
    request = GenRequest(kind="video", prompt=args.prompt, negative=args.negative, seed=args.seed,
                         references=(args.image,), duration_s=args.seconds, out_dir=args.out_dir,
                         extra={"name": f"runpod_smoke_{args.template}_{args.seed}"})
    started = time.monotonic()
    result = runpod_comfyui.RUNPOD_COMFY.generate(link, request, credentials=creds, on_log=print)
    meta = result.meta
    print(f"wrote {result.paths[0]} ({os.path.getsize(result.paths[0]) / 1e6:.1f} MB) in {time.monotonic() - started:.0f}s "
          f"wall; {meta['width']}x{meta['height']}, {meta['frames']} frames at {meta['fps']} fps")
    billed = f"${meta['billed_usd']:.3f}" if meta.get("billed_usd") is not None else "set RUNPOD_GPU_USD_PER_HOUR to price it"
    print(f"billed: execution {float(meta.get('execution_ms') or 0) / 1000:.0f}s + delay "
          f"{float(meta.get('delay_ms') or 0) / 1000:.0f}s = {meta['gpu_seconds']:.0f} GPU-s ({billed})")


if __name__ == "__main__":
    main()
