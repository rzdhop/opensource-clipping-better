"""Regenerate the clips of "Faille d'amour" episode 1 with the plan-34 motion
recipe (2026-10-07): the ten shots from ``clips_v2_plan.json`` (prompt, seed,
keyframe taken from the first production's journal rows), one job after the
other on a warm worker, then a continuation clip from the last frame of the
three shots that carry two lines. Resumable: a clip on disk is skipped, an
in-flight job is waited for, a failed one is resubmitted; the run stops at the
dollar cap and before the time budget runs out.

    python tools/regen_faille_ep01_clips.py [budget_seconds=560] [max_usd=2.0]

Kept as the record of how the episode's clips were made; the MCP job client
does the work (``mcp_server.runpod_jobs``), ffmpeg extracts the last frames.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server.config import load_settings
from mcp_server.runpod_jobs import JobClient, normalise_bill
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
c = JobClient(load_settings())
plan = json.load(open("outputs/faille_damour/ep01/clips_v2_plan.json"))
dest = "faille_damour/ep01/clips_v2"
os.makedirs(os.path.join("outputs", dest), exist_ok=True)
log_path = os.path.join("outputs", dest, "regen_log.json")
log = json.load(open(log_path)) if os.path.exists(log_path) else {}
budget_s = float(sys.argv[1]) if len(sys.argv) > 1 else 560
MAX_USD = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
def spent():
    return round(sum(float(e.get("billed_usd") or 0) for e in log.values()), 3)
t0 = time.time()
def trim(p):
    if len(p) < 300: return p
    cut = max(p.rfind(". "), p.rfind(", "))
    return p[:cut + 1] if cut > 150 else p
for row in plan:
    out = os.path.join("outputs", dest, row["name"] + ".mp4")
    if os.path.exists(out):
        continue
    if time.time() - t0 > budget_s - 90:
        print("budget reached before", row["name"], flush=True); break
    entry = log.get(row["name"]) or {}
    if not entry.get("job_id") and spent() >= MAX_USD:
        print(f"CAP: ${spent()} billed >= ${MAX_USD}; stopping before {row['name']} (the human decides)", flush=True); break
    if not entry.get("job_id"):
        rec = c.submit("i2v_wan22_14b_lightning", prompt=trim(row["prompt"]), seed=row["seed"], seconds=5,
                       image_path=row["keyframe"], name=row["name"], dest=dest,
                       note=f"plan 34: ep01 {row['name']} again with the 8-step recipe (seed of {row['from']})")
        entry = {"job_id": rec["job_id"], "submitted": time.strftime("%H:%M:%S")}
        log[row["name"]] = entry; json.dump(log, open(log_path, "w"), indent=1)
        print(time.strftime("%H:%M:%S"), "submitted", row["name"], rec["job_id"], flush=True)
    remaining = budget_s - (time.time() - t0)
    rec = c.wait(entry["job_id"], timeout_s=max(5, remaining), poll_s=10)
    b = normalise_bill(rec)
    entry.update({"state": rec["state"], "gpu_seconds": b.get("gpu_seconds"), "delay_seconds": b.get("delay_seconds"),
                  "billed_usd": b.get("billed_usd"), "error": rec.get("error")})
    log[row["name"]] = entry; json.dump(log, open(log_path, "w"), indent=1)
    print(time.strftime("%H:%M:%S"), row["name"], rec["state"], "gpu", b.get("gpu_seconds"), "delay", b.get("delay_seconds"), "$", b.get("billed_usd"), rec.get("error") or "", flush=True)
    if rec["state"] not in ("COMPLETED",) and rec["state"] in ("FAILED", "CANCELLED", "TIMED_OUT", "GONE"):
        entry["job_id"] = None  # a failed job is resubmitted on the next run
        json.dump(log, open(log_path, "w"), indent=1)
done = sorted(f for f in os.listdir(os.path.join("outputs", dest)) if f.endswith(".mp4"))
print("on disk:", done, "| billed so far $", round(sum(float(e.get("billed_usd") or 0) for e in log.values()), 3), flush=True)

# ---- phase 2: continuation clips for the shots that carry two lines (from the first clip's last frame)
CONT = {
    "clip02": (752, "The coffee splash settles; the kiwi-headed man grins and teases her, the strawberry-headed woman wipes her white jacket and glares back, then checks her watch in a hurry and taps her foot. Pixar-style 3D animation, comedic timing, lively gestures."),
    "clip03": (753, "The strawberry-headed woman tilts her head, intrigued, while the kiwi-headed man answers with a shy half-smile and a small shrug; she raises an eyebrow and smiles despite herself. Pixar-style 3D animation, expressive faces, lively gestures."),
    "clip04": (754, "The strawberry-headed woman hides her phone behind her back and waves the mango-headed woman away, flustered and talking fast; the mango-headed woman smirks, arms crossed, not convinced. Pixar-style 3D animation, comedic timing, lively gestures."),
}
all_base = all(os.path.exists(os.path.join("outputs", dest, s + ".mp4")) for s in ("clip02", "clip03", "clip04"))
if all_base:
    for shot, (seed, prompt) in CONT.items():
        out = os.path.join("outputs", dest, shot + "b.mp4")
        if os.path.exists(out):
            continue
        if time.time() - t0 > budget_s - 90:
            print("budget reached before", shot + "b", flush=True); break
        last = os.path.join("outputs", "faille_damour", "ep01", "kf", f"{shot}_v2_last.png")
        if not os.path.exists(last):
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.07", "-i", os.path.join("outputs", dest, shot + ".mp4"),
                            "-frames:v", "1", "-update", "1", last], check=True)
        name = shot + "b"
        entry = log.get(name) or {}
        if not entry.get("job_id") and spent() >= MAX_USD:
            print(f"CAP: ${spent()} billed >= ${MAX_USD}; stopping before {name} (the human decides)", flush=True); break
        if not entry.get("job_id"):
            rec = c.submit("i2v_wan22_14b_lightning", prompt=prompt, seed=seed, seconds=5,
                           image_path=os.path.relpath(last, "outputs"), name=name, dest=dest,
                           note=f"plan 34: ep01 {shot} continuation from its last frame (two lines on the shot)")
            entry = {"job_id": rec["job_id"], "submitted": time.strftime("%H:%M:%S")}
            log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
            print(time.strftime("%H:%M:%S"), "submitted", name, rec["job_id"], flush=True)
        remaining = budget_s - (time.time() - t0)
        rec = c.wait(entry["job_id"], timeout_s=max(5, remaining), poll_s=10)
        b = normalise_bill(rec)
        entry.update({"state": rec["state"], "gpu_seconds": b.get("gpu_seconds"), "delay_seconds": b.get("delay_seconds"),
                      "billed_usd": b.get("billed_usd"), "error": rec.get("error")})
        if rec["state"] in ("FAILED", "CANCELLED", "TIMED_OUT", "GONE"):
            entry["job_id"] = None
        log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
        print(time.strftime("%H:%M:%S"), name, rec["state"], "gpu", b.get("gpu_seconds"), "$", b.get("billed_usd"), rec.get("error") or "", flush=True)
    done = sorted(f for f in os.listdir(os.path.join("outputs", dest)) if f.endswith(".mp4"))
    print("on disk:", done, "| billed so far $", round(sum(float(e.get("billed_usd") or 0) for e in log.values()), 3), flush=True)
