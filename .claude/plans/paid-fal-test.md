# Paid-path live test on fal.ai (DEC-174)

Status: APPROVED 2026-09-29 — "Go: shots + reference edits (Recommended)". Hard ceiling **$3 total** (the human
funded $10: "Do not use all 10$"). Expected spend about **$0.80**. This is phase-5 plan stage 14b (a)–(d), run ahead of
phase 5; 14b(e) (re-edit / partial re-render on paid assets) waits for phase-5 stages 7–8.

Source: a read-only Opus mapping of the code (file:line citations are in the session log). Its key facts:
- **Chains can only be changed through the process environment** (`generation.py:222-226`); compose passes
  `IMAGE_CHAIN` / `IMAGE_EDIT_CHAIN` / `LLM_CHAIN` from `.env`. There is no Settings field and no per-task selector.
- The default image chain puts fal 4th (after cloudflare, pollinations, comfyui). The default edit chain puts a
  keyed paid Gemini link before fal.
- **A paid LLM link is usable with `allow_paid` on and is never booked or capped** (`steps/llm_call.py:150-153`,
  "caps are not checked here"). The only paid LLM link with a key is `openrouter/...`, so the test pins
  `LLM_CHAIN` without it.
- Shots are 720x1280: flux-schnell costs **$0.0028** each, a seedream edit **$0.03**.
- Paid shot requests go through the journal (booked at submit, resumed by poll). Cast, places and the style preview
  are not journaled: only answered calls are booked.
- There is no fault-injection hook. The forced poll failure is a **job cancel during the 2 s before the first
  poll**. Continue re-polls the same request id.
- A true $0 cache hit comes from deleting a shot image the assets step made and running assets again. That sends the
  same seed, and the cache serves the kept answer. A regenerate uses a fresh seed, so it is a new paid call.

## Guards (all phases)
- The FR `b1104ec66b05` and EN `0a9572a6a8be` stories are never touched. Their shot and episode files are
  sha256-snapshotted first and checked last.
- 0 jobs running before any container recreate.
- No LLM-only step while a paid LLM link exists: `LLM_CHAIN` has no openrouter link for the whole paid window.
- Read every estimate before each paid run: any paid link other than fal (a TTS line, an LLM) means **stop**.
- A test cap is always set below the remaining ceiling. Stop at once if `spend.json` or the ledger passes $1.50.

## Phases
- **A — free build of story T1** (`allow_paid` off, default chains):
  - EN, family_3d, **prompt_only**, 1 character, 1 place (day), 3-episode season;
  - ep1 script, fast storyboard, everything approved;
  - **stop before assets**.
- **B — switch to fal-only.** Put these in `.env`, then `docker compose up -d backend` at 0 jobs:
  - `IMAGE_CHAIN=fal/flux-schnell`
  - `IMAGE_EDIT_CHAIN=fal/seedream-4-edit`
  - `LLM_CHAIN=groq/openai/gpt-oss-120b,gemini/gemini-3.5-flash-lite,mistral/mistral-small-latest,nvidia/nvidia/nemotron-3.5-lightning-30b-a3b`

  Check that `GET /api/settings` shows the chains' `source` as `env`.
- **(a)** With `allow_paid` off, `POST steps/assets` on T1 → 409 "allow_paid is off …". No ledger row and no
  `spend.json`.
- **(b)** `allow_paid` on and each cap at $0.01 in turn (episode, then day, then story). Each gives a 409 naming both
  numbers.
- **(c) T1 paid run.** Caps: episode $0.20, day $0.50, story $0.50.
  - c1: assets, then cancel right after the first "journaled, booked" line. Expect one booked row and one journal
    entry `submitted`.
  - c2: Continue. Expect "resuming request <same id>" with no new booking, then N−1 bookings. Ledger total ≈
    N × $0.0028.
  - c3: move one shot image aside and run assets again. Expect "kept answer, no call made" and $0 added.
- **C — T2, the references pass.** Caps: episode $0.80, day $1.20, story $1.00.
  - Build a second story in **references** mode, 1 character, 1 place (day), with `allow_paid` on and the fal-only
    chains. Its sheets are seedream edits (about $0.06) and the portrait/plate are flux-schnell (about $0.006).
  - Script and storyboard run on the free LLM chain.
  - Assets: N × $0.03 on seedream, journaled.
- **(d) Totals.** Sum the ledger per story, `data/spend.json` and the journal states, then compare with the human's
  fal dashboard (request ids from the journal).
- **Revert.** In order:
  1. `allow_paid` off, and caps back to 1 / 3 / 10;
  2. remove the three `.env` lines and recreate the backend;
  3. check the chains' `source` is back to `default`;
  4. check the EN/FR sha256 snapshot passes.

  T1 and T2 are kept for the audit trail: deleting them is the human's call.
