"""One S2V talking clip per line of ep01 (plan 35): keyframe = the speaker's close-up, audio = the line's WAV."""
import json, os, sys, time
sys.path.insert(0, "/home/ubuntu/Documents/tools/opensource-clipping-better")
os.chdir("/home/ubuntu/Documents/tools/opensource-clipping-better")
from mcp_server.config import load_settings
from mcp_server.runpod_jobs import JobClient, normalise_bill
c = JobClient(load_settings())
plan = json.load(open("outputs/faille_damour/ep01/talk_plan.json"))
voices_dir, dest = "faille_damour/ep01/voices_v2", "faille_damour/ep01/talk"
os.makedirs(os.path.join("outputs", dest), exist_ok=True)
log_path = os.path.join("outputs", dest, "talk_log.json")
log = json.load(open(log_path)) if os.path.exists(log_path) else {}
budget = float(sys.argv[1]) if len(sys.argv) > 1 else 560
cap = float(sys.argv[2]) if len(sys.argv) > 2 else 1.5
NEG = "slow motion, static, frozen, blurry, distorted face, extra mouth, text, watermark"
spent = lambda: round(sum(float(e.get("billed_usd") or 0) for e in log.values()), 3)
t0 = time.time()
for row in plan:
    name = f"{row['id']}_{row['who']}"
    out = os.path.join("outputs", dest, name + ".mp4")
    if os.path.exists(out):
        continue
    if time.time() - t0 > budget - 90:
        print("budget reached before", name, flush=True); break
    entry = log.get(name) or {}
    if not entry.get("job_id") and spent() >= cap:
        print(f"CAP ${spent()} >= ${cap}; stopping before {name}", flush=True); break
    if not entry.get("job_id"):
        wav = os.path.join(voices_dir, name + ".wav")
        if not os.path.exists(os.path.join("outputs", wav)) or not os.path.exists(os.path.join("outputs", row["keyframe"])):
            print("missing input for", name, flush=True); continue
        rec = c.submit("s2v_wan22", prompt=row["prompt"], negative=NEG, seed=2000 + int(row["id"][1:]), seconds=5,
                       image_path=row["keyframe"], audio_path=wav, name=name, dest=dest,
                       note=f"plan 35: talking clip {name} (S2V from the speaker's close-up)")
        entry = {"job_id": rec["job_id"]}; log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
        print(time.strftime("%H:%M:%S"), "submitted", name, rec["job_id"], flush=True)
    rec = c.wait(entry["job_id"], timeout_s=max(5, budget - (time.time() - t0)), poll_s=10)
    b = normalise_bill(rec)
    entry.update({"state": rec["state"], "gpu_seconds": b.get("gpu_seconds"), "delay_seconds": b.get("delay_seconds"),
                  "billed_usd": b.get("billed_usd"), "error": rec.get("error")})
    if rec["state"] in ("FAILED", "CANCELLED", "TIMED_OUT", "GONE"): entry["job_id"] = None
    log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
    print(time.strftime("%H:%M:%S"), name, rec["state"], "gpu", b.get("gpu_seconds"), "$", b.get("billed_usd"), rec.get("error") or "", flush=True)
print("on disk:", sorted(f for f in os.listdir(os.path.join("outputs", dest)) if f.endswith(".mp4")), "| $", spent(), flush=True)
