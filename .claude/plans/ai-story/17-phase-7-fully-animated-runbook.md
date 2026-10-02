# Fully animated paid test — runbook (phase 7, 2026-10-02)

The human's ask (2026-10-02): "only fully animated episodes, no diaporama, even if it costs money; ask the
provider for video and check the API key, then start with the desired setting". The human's choices:
phase-7 code with a v2 story, fal Seedance 1 pro fast at 720p, refuse unless every shot moves, caps 2 / 6 / 20.

## Why the dashboard story could not animate (root causes)

| # | Gate | What a dashboard story had | Fix |
|---|---|---|---|
| 1 | Tier | The form sent tier 1 (stills with motion) | FA1: the form starts from `GET /api/stories/new-profile` (v2, tier 2, api, quality when FAL_KEY is set) and sends no profile unless one is changed |
| 2 | Budget profile | The form offered `free` (`animate: none`) and `one_dollar` (key shots); `quality` (`animate: all_shots`) was not offered on `main` | FA1: "Quality (billed APIs) — every shot animated" is offered and is the default with FAL_KEY |
| 3 | Pipeline | The form never sent `pipeline: v2`, so even phase-7 code made v1 stories | FA1: the form sends the server's v2 profile; an existing story moves to v2 with **Animate every shot** while no episode has a script (template and narrator follow) |
| 4 | Paid switch and caps | `allow_paid` off; Settings saved 1 / 3 / 10 | You: Settings → allow paid on, caps 2 / 6 / 20 |
| 5 | Still fallback | A shot without a clip rendered as a still with a zoom | FA2: on a fully animated story the assets approval and the render refuse until every shot has a current clip (only a shot you pin `keep_still` is exempt; `fill_failed_with_motion` is refused) |
| 6 | Key check | The video chain test never asks a video provider (RC-V8): a bad FAL_KEY showed on the first clip bought | FA3: Settings → Video → **Ask the providers (free)** (`POST /api/settings/check-video-keys`): fal pricing / Gemini `models.get`, nothing generated |

## 1. Deploy the branch (at 0 jobs)

The main checkout stays on `main` (the `rzc-backend` container bind-mounts it). Phase 7 is not closed; this
deploys its current head for the test.

```bash
cd /home/ubuntu/Documents/tools/opensource-clipping-better
curl -s http://127.0.0.1:8000/api/jobs | python3 -c "import json,sys; d=json.load(sys.stdin); j=d.get('jobs', d); print(sum(1 for x in j if x.get('status') in ('queued','running')), 'jobs running')"
git fetch origin claude/phase-7-capability-upgrade-b0huym
git merge --ff-only origin/claude/phase-7-capability-upgrade-b0huym      # main fast-forwards; nothing is rewritten
sudo docker compose rm -sfv backend && sudo docker compose up -d --build backend   # Python + dashboard changed
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/health   # 200
```

To go back to phase 6 later: `git merge --ff-only` cannot undo; note the hash you came from
(`git rev-parse HEAD` before the merge, `30604dd` today) and ask before resetting `main` to it.

## 2. Settings (dashboard → Settings)

1. Keys: `FAL_KEY` set (fal pays for every quality image and every clip, DEC-235).
2. **Allow paid: on.** Without it no image or clip is ever bought, whatever the story says.
3. **Caps: episode 2.00 / day 6.00 / story 20.00.** Saved Settings override the new defaults, so 1 / 3 / 10
   saved earlier still apply until changed here; an every-shot episode (≈ $1.73) is refused whole at $1.00.
4. Video card → **Ask the providers (free)**. Expected: `✅ fal/seedance-1-pro-fast: fal accepted FAL_KEY;
   fal-ai/bytedance/seedance/v1/pro/fast/image-to-video is live at 0.022 USD per second` (the price is fal's
   own answer). `✖ … refused FAL_KEY (HTTP 401)` means the key is wrong; `⚠️ … not found` means the model id
   moved. The same from a shell:

   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/settings/check-video-keys | python3 -m json.tool
   curl -s http://127.0.0.1:8000/api/stories/new-profile | python3 -m json.tool   # quality: true, allow_paid: true
   ```

## 3. The story

- **"Cœur Firewall et Larmes de Citron" before any episode has a script:** open it → Visual tier card → **Animate
  every shot**. It becomes v2, tier 2, api, quality, `serial_60s_v2`, narrator on. If its cast already exists,
  run the Cast step again afterwards: on v2 it writes each character's dossier and look and redraws the sheets
  (its estimate shows the cost). Same by curl:

  ```bash
  curl -s http://127.0.0.1:8000/api/stories | python3 -c "import json,sys; [print(s['story_id'], s['title']) for s in json.load(sys.stdin)['stories']]"
  curl -s -X PATCH http://127.0.0.1:8000/api/stories/<STORY_ID> -H 'Content-Type: application/json' \
       -d '{"generation_profile": {"pipeline": "v2", "tier": 2, "route": "api", "consistency_mode": "references", "budget_profile": "quality"}}'
  ```

- **If an episode already has a script:** the switch to v2 is refused (the script was written for the legacy
  shot layout). Create a new story: the form now says "Fully animated: every shot is a video clip" when FAL_KEY
  is set; leave the profile as it is.

## 4. The walk (each paid step shows its estimate first)

| # | Step | Gate (what can stop it) | Expected $ |
|---|---|---|---|
| 1 | Concept, bible, style (writing on NIM, else free Gemini; the style preview is a free draft) | — | ≈ 0 |
| 2 | Cast: K1, D1 dossier, D2 look per character; 3 sheets each on fal Seedream 4.5 ($0.04) | approve each character (dossier and look editable) | ≈ 0.12 per character |
| 3 | Places and props: plates and prop images ($0.04 each) | approve each | ≈ 0.10–0.25 |
| 4 | Season, then the **Knowledge base** step | approve the knowledge base (edits make it "Approve again") | ≈ 0.04–0.12 (new props) |
| 5 | Script ep 1: E1–E4, the fill pass if short, the **first-watch check (J1)** | approve: refused outside 55–75 s (no anyway) or with J1/E4 issues (anyway allowed) | 0 (free chain) |
| 6 | Storyboard: T1 v2, 6–10 shots | approve: refused outside 55–75 s | 0 |
| 7 | Assets with **animate off**: keyframes (6–10 × $0.04), voices, the **keyframe check (J2)** per shot | storyboard → **Keyframes** card: Approve keyframes (or anyway) — no clip is bought before | ≈ 0.32 |
| 8 | Assets with **animate on**: one seedance clip per shot, ≈ 60 s × $0.022 + rounding | caps (refused whole if over) | ≈ 1.41 |
| 9 | Approve the assets, then render | refused while a shot has no current clip, or the measured length is outside the window; the render also refuses a short music bed or missing frames ("render again") | 0 |

Episode ≈ $1.73 under the $2 cap; story one-off ≈ $0.56 for 3 characters, 2 places, 3 props.

**What to judge on the phone (the acceptance verdict):** the story is clear on one watch; the looks hold from
shot to shot; **every shot moves**; the hook has on-screen text; no line is said twice; the music runs to the
end; no lettering or badges drawn on clothes or signs.

**The fast track** does steps 5–9 in one job but stops where a human decides: a script it cannot approve, and
on a v2 episode the keyframes (approve them, then Continue).

**From the CLI instead of the dashboard** (one process per paid step, the DEC-215 pattern; `--settings` reads
the keys, caps and `allow_paid` the dashboard stored, so no scratch driver is needed):

```bash
python main.py --ai-story step STORY_ID assets --ep 1 --no-animate --settings --estimate   # the price first
python main.py --ai-story step STORY_ID assets --ep 1 --no-animate --settings
python main.py --ai-story approve STORY_ID keyframes:1            # add --anyway to go over J2's findings
python main.py --ai-story step STORY_ID assets --ep 1 --settings --estimate
python main.py --ai-story step STORY_ID assets --ep 1 --settings --auto-approve
python main.py --ai-story render STORY_ID --ep 1 --settings
```

## Guarded since this runbook was first written

- A music bed cut short, or a final missing frames, now fails the render before anything is published
  (DEC-238); render again (cached shots are reused).
- Crowded keyframes fit their 220 words; a prop's own name is no longer garbled in its description (DEC-237).
- Groq transcription no longer answers 403 "error code 1010" (the app now names itself in its User-Agent).
