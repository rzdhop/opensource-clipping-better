import json, os, sys, time  # record script: paths are the production host's
sys.path.insert(0, "/home/ubuntu/Documents/tools/opensource-clipping-better")
os.chdir("/home/ubuntu/Documents/tools/opensource-clipping-better")
from mcp_server.config import load_settings
from mcp_server.runpod_jobs import JobClient, normalise_bill
c = JobClient(load_settings())
PRE = "Stylised high-end 3D Pixar-style CGI animation still, vertical cinematic frame. "
POST = (" Exactly the same character design as the first reference image, the same place and lighting as the second "
        "reference image. Head and shoulders, the face centred, fully visible and sharp, looking at the camera, the mouth "
        "relaxed and slightly open, about to speak. No text, no other character, no mask.")
NEG = "photorealistic, human face, human head, mask, text, watermark, extra characters, blurry, cropped face"
RIDA = ("the kiwi-headed hacker (a fuzzy brown kiwi head with a bright green kiwi-flesh face and tiny black seeds, "
        "dark jacket over a green T-shirt, headphones around the neck)")
MJ = ("the strawberry-headed woman (a glossy red strawberry head with golden seeds and a green leaf crown, cream blazer, "
      "red blouse)")
PALOMA = "the mango-headed woman (a yellow-red mango head with a leaf, gold hoop earrings, mustard cardigan over a teal dress)"
CU = [
 ("cu_l01_rida", "char_rida.png", "kf01_loft.png", 901, f"Dramatic close-up of {RIDA}, lit by the red glow of his monitor in the same dark loft, city lights behind; a calm, cheeky half-smile."),
 ("cu_l02_marie_jeanne", "char_marie_jeanne.png", "kf02_collision.png", 902, f"Close-up of {MJ} in the same warm café, a splash of coffee frozen in the air beside her, startled and indignant, eyebrows up."),
 ("cu_l03_rida", "char_rida.png", "kf02_collision.png", 903, f"Close-up of {RIDA} in the same warm café, an amused teasing look, one eyebrow raised."),
 ("cu_l04_marie_jeanne", "char_marie_jeanne.png", "kf03_spark.png", 904, f"Close-up of {MJ} in the same warm café, chin lifted, a proud confident smile."),
 ("cu_l05_rida", "char_rida.png", "kf03_spark.png", 905, f"Close-up of {RIDA} in the same warm café, a shy half-smile, soft eyes."),
 ("cu_l06_paloma", "char_paloma.png", "kf04_paloma.png", 906, f"Close-up of {PALOMA} in the same bright open-plan office, a conspiratorial grin, leaning in."),
 ("cu_l07_marie_jeanne", "char_marie_jeanne.png", "kf04_paloma.png", 907, f"Close-up of {MJ} in the same bright open-plan office, flustered, hiding her phone against her chest, cheeks flushed."),
 ("cu_l09_marie_jeanne", "char_marie_jeanne.png", "kf06_red_screen_v2.png", 909, f"Close-up of {MJ} in the same boardroom flooded with alarm-red light from the big screen behind her, wide-eyed panic, hands near her face."),
]
dest = "faille_damour/ep01/kf_cu"
os.makedirs(os.path.join("outputs", dest), exist_ok=True)
log_path = os.path.join("outputs", dest, "closeups_log.json")
log = json.load(open(log_path)) if os.path.exists(log_path) else {}
t0 = time.time(); budget = float(sys.argv[1]) if len(sys.argv) > 1 else 540
for name, char, kf, seed, text in CU:
    out = os.path.join("outputs", dest, name + ".png")
    if os.path.exists(out) or time.time() - t0 > budget - 60:
        continue
    entry = log.get(name) or {}
    if not entry.get("job_id"):
        rec = c.submit("edit_flux2_klein_multiref", prompt=PRE + text + POST, negative=NEG, seed=seed, width=704, height=1216,
                       ref_paths=[f"faille_damour/chars/{char}", f"faille_damour/ep01/kf/{kf}"], name=name, dest=dest,
                       note=f"plan 35: per-speaker close-up for the talking clip of {name[3:6]}")
        entry = {"job_id": rec["job_id"]}; log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
        print(time.strftime("%H:%M:%S"), "submitted", name, rec["job_id"], flush=True)
    rec = c.wait(entry["job_id"], timeout_s=max(5, budget - (time.time() - t0)), poll_s=8)
    b = normalise_bill(rec)
    entry.update({"state": rec["state"], "gpu_seconds": b.get("gpu_seconds"), "billed_usd": b.get("billed_usd"), "error": rec.get("error")})
    if rec["state"] in ("FAILED", "CANCELLED", "TIMED_OUT", "GONE"): entry["job_id"] = None
    log[name] = entry; json.dump(log, open(log_path, "w"), indent=1)
    print(time.strftime("%H:%M:%S"), name, rec["state"], "gpu", b.get("gpu_seconds"), "$", b.get("billed_usd"), rec.get("error") or "", flush=True)
print("on disk:", sorted(f for f in os.listdir(os.path.join("outputs", dest)) if f.endswith(".png")), "| $", round(sum(float(e.get("billed_usd") or 0) for e in log.values()), 3))
