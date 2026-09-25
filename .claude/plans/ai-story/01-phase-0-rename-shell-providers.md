# Phase 0 prompt — rzdhop AI: rename, two-mode shell, generation providers, hardware, budget

Paste this into Claude Code from the repository root.

---

You are continuing work on this repository under its existing cross-session protocol.
Before anything else, read in full: `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` (the master specification of the new
"AI Story" mode). Section 0 of the spec restates the protocol; follow it exactly:
plan approved in chat → checkpoint commit on a clean tree → numbered stages, one commit
each → Tier-1 (`python -m pytest -p no:warnings`, compileall, `npm run build`, and the
CI-equivalent stdlib+pytest env) → Tier-2 live run with my explicit acknowledgement →
last stage "docs + decisions". New decisions start at DEC-092, assumptions at A-030.
Every behaviour test must be shown to fail on the pre-change code. All code, comments,
prompts and docs in English.

## Goal of this phase

Deliver the foundation the AI Story mode stands on, with **no story feature yet**:

1. **Rename** the product to **rzdhop AI** (spec 1.3): dashboard title/login/README/
   `pyproject.toml` name; new logo SVG derived from the existing one; update
   `tests/test_branding.py` (allow the repo slug, forbid `opensource-clipping`, require
   `rzdhop AI` in `web/dashboard/index.html` and `README.md`). Record a DEC that the
   Python package import name stays `clipping`.
2. **Two-mode shell** (spec 1.4): top-bar mode switch `Clips | AI Story` persisted in
   `localStorage`; routes `/clips/*` (all existing pages, unchanged behaviour — add a
   redirect from the old paths) and `/story/*` (a placeholder page "AI Story — coming in
   phase 1" for now); `/` redirects to the last mode. Legacy `--story-mode` is labelled
   **"Story Clip (assembly)"** wherever it appears in the UI/docs. Check layouts at 375,
   820 and 1280 px.
3. **Generation provider chains** (spec 8.1) with the same semantics as `LLM_CHAIN`:
   `IMAGE_CHAIN`, `IMAGE_EDIT_CHAIN`, `VIDEO_CHAIN` (parsing + settings only in this
   phase; adapters come in phase 6), `TTS_CHAIN`, `VISION_CHAIN`, `LOCAL_COMFYUI_URL`,
   `LOCAL_OLLAMA_URL`. Implement adapters now for: `cloudflare/flux-1-schnell`,
   `pollinations/*`, `gemini/nano-banana-2-lite` and `gemini/nano-banana-2` (image + edit
   with reference images), `fal/flux-schnell`, `fal/seedream-4-edit`,
   `fal/flux-kontext-pro`, `openai/gpt-image-2-low`; TTS: `edge/*` (reuse the existing
   edge_tts path — do not fork it), `gemini/flash-lite-tts`, `local/piper`,
   `local/kokoro`, `local/chatterbox` (optional extras, probed, never imported at
   startup); vision: `gemini/flash-lite`, `openrouter/<free vision model>`,
   `local/ollama-vision`; `local/comfyui` and `local/ollama` clients (spec 8.3) with
   API-format workflow templating (`POST /prompt`, `/ws` progress, `/history`, `/view`,
   `/system_stats`, `object_info` validation) **and the three image workflow templates**
   `t2i_flux2_klein.json`, `edit_flux2_klein_multiref.json`, `edit_qwen_image.json`
   (video workflows come in phase 6). `VIDEO_CHAIN` gets parsing, settings and a "Test
   chain" that reports "no adapter yet" for every link until phase 6.
   `LOCAL_COMFYUI_URL` defaults per spec 8.1 (host vs Docker, `host.docker.internal`).
   One small common interface (`generate(request) -> Result` with
   `est_cost, provider, model, seed, paths`; `probe()`), errors through
   `clipping/providers/errors.py` so DEC-089's retired-model swap applies. **No silent
   fallback**; every hop printed; unlisted providers never called.
4. **Pricing table** `clipping/providers/pricing.py` with `PRICES_AS_OF = "2026-09-25"`
   and the unit prices from spec 8.1/8.5 (image, second, char). Test: every paid link in a
   default chain has a price.
5. **Free-tier limiters** `clipping/providers/limits.py` (spec 8.4): RPM/RPD per
   provider, daily counters persisted in `data/usage.json`, "budget left today" readout.
6. **Hardware profiler** `clipping/aistory/hardware.py` (spec 8.2): stdlib probes in the
   given order, fixture-tested parsers, `HardwareProfile` → profile
   `cpu_only|low|mid|high|pro|apple_mps|container_no_gpu`, recommendations table,
   `GET /api/hardware`.
7. **Budget** (spec 8.5): settings `allow_paid` (default off), `per_episode_cap_usd`
   (**1.00**), `daily_cap_usd` (3.00), `per_story_cap_usd` (10.00), default budget profile
   `free` (`one_dollar` once paid is on) — **five-place defaults with the agreement test
   in this phase**; `budget_profiles.json` (free / one_dollar / quality) loaded and
   validated; a `budget.check(est)` used by every adapter before a paid call; refusal
   message carries the numbers; a story-level `CostLedger` writer (spec 2.11) that
   phases 1+ will call (here only exercised by the test and by the Tier-2 paid image).
8. **Settings UI** (spec 8.6): tabs *Providers*, *Generation*, *Local hardware*,
   *Budget*; each new secret persisted via `PERSISTED_KEYS` with a `_set` badge (DEC-057);
   "Test chain" per chain reusing the `diagnose_chain` UX; hardware panel shows the
   detected profile and recommended local models with install hints.
9. **Docs**: README "Two modes" section; `docs/AI_STORY.md` created with the vision and
   "what exists after phase 0"; `.env.example` updated; VISION.md rewritten for the
   two-mode product with the phase map from spec section 15 as the "Next" list.

## Out of scope (do not start)

Story store, wizard, templates, LLM story prompts, renderer, jobs of kind `story_step`.

## Before you plan

Ask me only what you cannot decide from the spec and the code. Then propose the plan
with stages (label the riskiest), the regression contract table (existing clip flow,
existing Settings, auth, legacy story-clip assembly must keep working — name the tests
that prove it), the DEC/A entries you expect to write, and the Tier-2 script (real
dashboard on the phone: switch modes, save a paid key, see the estimate refusal, run
"Test chain" on IMAGE_CHAIN and TTS_CHAIN, hear one Edge TTS sample).

## Acceptance

- All Tier-1 green in both envs; new tests fail on pre-change code (show it).
- Clip mode behaves identically (existing tests + one real clip job).
- `GET /api/hardware` returns a profile on this machine and on the CPU-only VPS.
- A paid image call with `allow_paid=false` is refused with the estimate; with it on and
  a key present, one 9:16 image is produced, appended to a test cost ledger, and the
  free-tier counters in `data/usage.json` are untouched by it (they count free calls).
- CHECKPOINT.md, DECISIONS.md, ASSUMPTIONS.md, VISION.md, docs updated in the last stage.
