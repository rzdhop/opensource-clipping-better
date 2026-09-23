## CURRENT TASK — onboarding, job lifecycle, render-layer fixes (IN PROGRESS)
- **Origin:** a fork-vs-upstream analysis (2026-09-23). Upstream has had no
  commits since `3c72b75`, so nothing needs porting. The findings were ranked
  and the human chose three areas: onboarding (A1-A7), job lifecycle (B1-B5)
  and the render layer (D1-D7). Everything else stays on the roadmap.
- **Plan:** `C:\Users\ridap\.claude\plans\on-this-sessions-we-snoopy-flurry.md`
  (approved in chat). It holds the full findings table, the roadmap and the
  21-stage ledger.
- **Host:** Windows 11 (Python 3.11.15, ffmpeg 8.1.1, node 26). cv2,
  mediapipe and fastapi are installed, so real renders can run here.
  Docker and the deploy stay on the human's Ubuntu VPS.
- **Checkpoint commit:** `8fa873d`, a clean tree. Local `main` was
  fast-forwarded from `d5c502a`, which was 68 commits behind. Roll back here.
- **Tier-1 baseline at `8fa873d` (this host):**
  - Local suite: `python -m pytest` = **1353 passed, 8 failed**.
  - CI-equivalent run, in a clean venv with pytest only
    (`python -m venv <scratch>/civenv`, `pip install pytest`, then
    `PYTHONNOUSERSITE=1 <venv>/python -m pytest`) = **1202 passed, 125
    skipped, 6 failed**.
  - `compileall` is clean. `npx vite build --outDir <scratch>` is green.
- **The 8 baseline failures are pre-existing and specific to Windows.** None
  is touched by this task. The rule for every stage is **no new failures;
  these 8 stay put**.
  - 5 permission tests (`test_auth_token`, `test_settings_store` x3,
    `test_clip_srt_export`): POSIX `chmod 0600` and unwritable-directory
    semantics do not exist on Windows.
  - `test_chain_readiness::test_the_cli_gates_after_the_key_gate_and_before_the_probe`:
    the test reads a source file with the default cp1252 codec.
  - `test_clip_serving::test_the_api_is_untouched_by_the_fallback`: the SPA
    fallback answers 200 for `/api/nope` on Windows.
  - `test_transcript_dispatch::test_bypass_does_not_import_ctranslate2`:
    ctranslate2 is installed here, so the transcript path's import of it
    becomes visible. The Ubuntu env does not have the package, so this
    **may be a real bug hidden there** (logged as a follow-up).
- **Phase:** IMPLEMENT. Group 1 merged and pushed (`7d374b2`). Group 2 merged and pushed (`b6202c9`). Next stage: Group 3 live Tier-2 (real backend: cancel mid-render, delete, 429, browser pass), then merge feature/job-lifecycle.
- **Open questions:** none. Two scope calls were made in chat: loudnorm is
  an opt-in flag, default off; cancel uses checkpoints plus a kill of the
  job's ffmpeg children.

### Stage ledger
| S | Item | Branch | State |
|---|---|---|---|
| 0 | sync + baseline | — | **done** |
| 1 | A6 health version | feature/onboarding | **done** |
| 2 | A2 retire .env.sample | feature/onboarding | **done** |
| 3 | A3 pyproject mirrors requirements | feature/onboarding | **done** |
| 4 | A4 README/wiki links | feature/onboarding | **done** |
| 5 | A5 retire docs/studio | feature/onboarding | **done** |
| 6 | A1 notebooks | feature/onboarding | **done** |
| 7 | A7 source_manager docstring | feature/onboarding | **done** |
| 8 | D7 diarization stderr | feature/render-fixes | **done** |
| 9 | D6 hook fetch timeout/cap | feature/render-fixes | **done** |
| 10 | D4 memoise encoder probes | feature/render-fixes | **done** |
| 11 | D1 watermark load hoisted + settings-keyed cache | feature/render-fixes | **done** |
| 12 | D5 hook-v2 temp cleanup | feature/render-fixes | **done** |
| 13 | B5 LLM_CHAIN note (no code) | feature/job-lifecycle | **done** |
| 14 | B1a cancel token + checkpoints | feature/job-lifecycle | **done** |
| 15 | B1b web cancel + child kill (**high risk**) | feature/job-lifecycle | **done** |
| 16 | B2 delete removes files (**high risk**) | feature/job-lifecycle | **done** |
| 17 | B4 queue cap | feature/job-lifecycle | **done** |
| 18 | B3 dashboard Cancel/Delete | feature/job-lifecycle | **done** |
| 19 | D2 studio real package (**RISKIEST**) | feature/studio-package | pending |
| 20 | D3 opt-in loudnorm | feature/loudnorm | pending |
| 21 | docs + DEC-075..085 + close-out | — | pending |

### Regression contract for this task
| ID | Must keep working | Proven by |
|---|---|---|
| RC-7 | The render layer produces the same frames | framemd5 identical before and after S10/S11/S19 on hybrid+watermark, split-screen (face trigger), hook-v2, edge-glow (local render) |
| RC-8 | Split-screen renders | the same local render; camera-switch is **UNVERIFIED** (needs pyannote) |
| RC-10 | The web API runs a job end to end | existing `test_job_stream.py`, `test_clip_serving.py` + a manual pass |
| RC-12 | The suite imports with pytest alone (DEC-012) | the clean-venv run above |
| RC-A* / RC-B* | Chain behaviour, budgets, readiness gate (previous task) | `test_nvidia_retry.py`, `test_preflight.py`, `test_chain_readiness.py`, `test_provider_registry.py` |
| RC-B7 | A render-only rerun needs no key | `test_web_reuse_bypass.py` |
| RC-L1 | Clone & Rerun reuses the saved transcript (DEC-022) | `test_transcript_persistence.py` |

---

## PREVIOUS TASK — chain readiness gate + per-provider probe timeout (COMPLETE, awaiting Tier-2 ack + deploy)
- **Phase:** DOCUMENT done. All 8 stages committed; head `9050839`.
- **Plan:** `~/.claude/plans/still-not-working-groovy-puffin.md` (approved in chat).
- **Checkpoint commit before the work:** `e991ff8` (+ `dafcf0b`, the checkpoint note).
- **Tier-1:** baseline 1299 passed -> now **1361 passed, 0 failed**
  (`python -m pytest -p no:warnings`). Every behaviour test was verified to
  FAIL against its pre-change code; guards that pass both ways are named in the log.
- **Tier-2:** no E2E browser suite exists in this repo. Verified instead:
  live CLI refusal (0.08s), live probe of the real NVIDIA key (39.1s / 57.4s --
  both alive now, the latter dead under the old 45s), live test-chain route, and
  a manual browser pass of New Job + Settings on a throwaway app serving the
  fresh build. **Awaiting the human's acknowledgement, and a container rebuild**
  (dashboard + backend changed): `docker compose rm -sfv backend && docker compose up -d --build backend` (sudo).
- **Dependency audit:** pip-audit is not installed on this box; this task changed
  no dependency file (requirements.txt, pyproject.toml, package*.json untouched).
- **Next action:** the human sets a free GOOGLE_API_KEY (and/or GROQ_API_KEY,
  see A-009), rebuilds, and reruns the job.
- **Open questions:** none.
- **Follow-ups, deliberately not done:** ~~`LLM_CHAIN` is not in
  `settings_store.PERSISTED_KEYS` (a runtime-set chain vanishes on restart)~~
  -- **not a bug** (checked 2026-09-23, DEC-085): nothing sets `LLM_CHAIN` at
  runtime. `SettingsRequest` has no chain field, the settings route only
  *reads* it (`routes/settings.py:65,227`), and a job carries its own
  `llm_chain` (`models.py:202`). It comes from `.env`/compose, which survive a
  restart; persisting it would do nothing. A Settings field for the chain would
  be a feature, not this fix. Adding new free providers needs a benchmark per model; the web path still has
  no `--no-preflight` equivalent; `tests/test_clip_length.py` leaves an empty
  `outputs/jobid` behind (pre-existing).

### Why (measured 2026-09-23)
The failing job had ONLY `NVIDIA_API_KEY`. With that key the NIM ping
succeeded (`ok`) in 48.9 / 57.0 / 49.7s -- all queue wait -- so the 45s probe
cap reported a live provider as dead. Groq/Gemini keys were never set.

### Regression contract for this task
| ID | Must keep working | Proven by |
|---|---|---|
| RC-B1 | A chain with a keyed primary link runs, no new gate in the way | `test_preflight.py::test_a_live_chain_returns_no_complaint`, new readiness tests |
| RC-B2 | A failing link is reported, never removed (DEC-003/023) | `test_preflight.py` report-not-remove tests |
| RC-B3 | A slow-but-healthy call is never cut off (DEC-020) | `::test_a_failed_work_probe_falls_back_to_the_ping` + new nvidia-50s-is-live |
| RC-B4 | `effective_timeout` unchanged in value and role | `test_provider_registry.py`, `test_llm_negotiation.py` |
| RC-B5 | The NIM model id exists in one place | `test_provider_registry.py::test_no_former_copy_grew_a_model_literal_back` |
| RC-B6 | Every Settings field the page sends is declared by the backend | `test_dashboard_payload_contract.py` |
| RC-B7 | A render-only rerun needs no key, no probe, no gate | `test_web_reuse_bypass.py` + new API test |
| RC-B8 | Suite importable with pytest alone (DEC-012) | CI env / importorskip |

### Stage ledger
| S | Stage | State |
|---|---|---|
| 1 | registry: probe_timeout, primary, signup_url | **done** `e8fa95c` |
| 2 | probes resolve their cap per link | **done** `f66328e` |
| 3 | chain_readiness + CLI gate | **done** `d9c5f55` |
| 4 | web plumbing: allow_slow_chain | **done** `fbf41c4` |
| 5 | refusal at POST /api/jobs | **done** `7a28316` |
| 6 | POST /api/settings/test-chain | **done** `e6e73dc` |
| 7 | dashboard | **done** `abd0125` |
| 8 | docs + DECISIONS | **done** `9050839` |

---

## Previous task — analysis round S1-S12 (COMPLETE)
- **Phase:** COMPLETE. All twelve stages are committed. DOCUMENT done;
  awaiting the human's Tier-2 verdict on the deployed app.
- **Checkpoint commit before the work:** `6dbc431`. **Head:** see the ledger.
- **Tier-1:** CI env **1170 passed, 101 skipped, 0 failed** (baseline 1110/101).
  Every behaviour-guarding test was verified to FAIL against its pre-change
  code. Concurrency suites: 20/20 clean reruns.
- **Tier-2:** no E2E browser suite exists. Verified instead by live runs
  against the real NVIDIA endpoint (below) and by a byte-identical `.ass`
  render for S10. **The human is testing S1–S6 on the deployed app and has not
  reported back.**
- **Next action:** deploy and test S7–S12, or act on what the human reports.

### The live measurement that changed a decision
S12's plan said default to 2 workers. The measurement said otherwise — same
transcript, same NVIDIA key, cache off:

| | pass A | for | per window |
|---|---|---|---|
| `workers=1` | 330s | 4 windows | 82s |
| `workers=2` | 346s | 4 windows | 86s |

Two overlapping requests should have finished those four in ~180s. **They did
not overlap at all**: NVIDIA's free tier serialises requests on one key, so a
batch cost the sum of its members and the run was 16s *worse*. The default
shipped as **1**. The flag remains because the chain's first link is Groq
(fast, published 30 rpm) and that is where it should pay — **untested, because
there is still no Groq key on this box.**

### Still the one thing that would help most
**Set a Groq key.** Both live runs above used NVIDIA alone, at ~85s per
request, and both lost windows 5 and 6 to the budget:
`⏱ Window 5/6 skipped: 300s left ... one request to this chain can take 330s`.
Groq is the chain's first link and Settings has had a field for it since
DEC-057. With it the analysis finishes in seconds, none of the budget
machinery binds, and `--analysis-workers 2` finally gets a fair test.

### Stage ledger
| S | Stage | Commit |
|---|---|---|
| 1 | gist/kind survive snapping; reach pass C | `597f06d` |
| 2 | Pass B sees hook lines; topic + variety | `6e6c096` |
| 3 | Pass A prompt rewrite + `--topic` | `08e0c5e` |
| 4 | `hook_beats` + temperature 0.5 for pass C | `2f93f02` |
| 5 | Voice-over prompt into `prompts.py` | `60193e5` |
| 6 | Mid-sentence-start guard | `e8778f5` |
| — | *(pushed to origin as `5a6f687`)* | |
| 7 | Per-window pass-A cache | `7195582` |
| 8 | Preflight does real work | `11276f6` |
| 9 | Delete the legacy monolith | `7d01041` |
| 10 | Karaoke colour into config | `02cddc0` |
| 11 | Analysis trace | `604aba8` |
| 12 | Batched scan windows (default 1) | `06b88b8` |

### Notes a fresh session will want
- `pyproject` sets `addopts = "-q"`; passing `-q` again makes it `-qq` and the
  summary line vanishes. Run pytest with no `-q` of your own.
- The render stack is **not** in `~/.local` — cv2/mediapipe/PIL are at
  `/tmp/claude-1001/scratch/renderdeps`, usable via `PYTHONPATH`.
- `clipping/studio.py` **shadows** the `clipping/studio/` package, so
  `from clipping.studio import subtitles` fails; import `clipping.studio`.
- `npm run build` in `web/dashboard` fails with EACCES — `dist/` is owned by
  root from the container build. `node_modules` is installed locally
  (gitignored); build with `npx vite build --outDir <scratch> --emptyOutDir`.
- `viral_score` prefers `meta["score"]` over `span.score` (`adapter.py:92`), so
  a test asserting which candidate pass B picked needs a metadata fixture with
  no score.

### Deploy
The dashboard changed in S3, S10 and S12, so the container needs a rebuild:
`docker compose rm -sfv backend && docker compose up -d --build backend`
(the `-v` matters; `down -v` would delete the Caddy certificates). Docker
needs sudo on this box.
- **Open questions:** none. Three scope calls were made in chat and are
  recorded in the plan: cover everything ranked, concurrency last, legacy
  single-request path deleted with `openai_compat` kept as a chain alias.

### Stage ledger
| S | Stage | State |
|---|---|---|
| 1 | gist/kind survive snapping; reach pass C | **done** `597f06d` |
| 2 | Pass B sees hook lines; topic + Python variety | **done** `6e6c096` |
| 3 | Pass A prompt rewrite + `--topic` | **done** `08e0c5e` |
| 4 | `hook_beats` + temperature 0.5 for pass C | **done** `2f93f02` |
| 5 | Voice-over prompt into `prompts.py`, English | **done** `60193e5` |
| 6 | Mid-sentence-start guard on b0 | **done** `e8778f5` |
| 7 | Per-window pass-A cache | **done** `7195582` |
| 8 | Preflight does real work when it can | **done** `11276f6` |
| 9 | Delete the legacy monolith; `openai_compat` alias | **done** `7d01041` |
| 10 | Karaoke highlight colour into config | **done** `02cddc0` |
| 11 | Persist the analysis trace | **done** `604aba8` |
| 12 | Concurrent pass-A windows (riskiest) | **done** `06b88b8` |

### Regression contract for this task
Each item names what proves it. Nothing here may break.
- **RC-A1** Three-pass analysis still produces renderable clips —
  `test_analysis_windows.py::test_a_full_run_produces_renderable_clips`.
- **RC-A2** A failed window loses one window, not the run (DEC-027/054) —
  `::test_a_failed_window_does_not_fail_the_run`, `::test_every_window_is_scanned`.
- **RC-A3** The requested clip count is never silently reduced (DEC-021) —
  `::test_a_failed_ranking_falls_back_to_the_scan_scores`, plus the new
  variety-backfill and never-empty-valve tests in S2/S6.
- **RC-A4** A model-invented beat id never becomes a cut (DEC-028) —
  `::test_a_beat_id_outside_the_window_is_discarded`, `::test_a_malformed_candidate_is_skipped`.
- **RC-A5** Provider failure and empty transcript stay distinguishable
  (DEC-055) — the `ScanStats` tests in `test_analysis_windows.py`.
- **RC-A6** The per-window time budget holds (DEC-053/054/059) — the three
  `Clock`/`greedy` tests; S12 must pin them to `analysis_workers=1`.
- **RC-A7** The render layer's clip-dict contract is unchanged (DEC-029, RC-7) —
  `test_slim_schema_adapter.py`, `test_manifest_fields.py`.
- **RC-A8** Transcription and transcript parsing survive the S9 deletion —
  `test_cpu_transcription_warning.py`, `test_json3_parser.py`,
  `test_transcript_dispatch.py`, `test_transcript_persistence.py`.
- **RC-A9** Karaoke word alignment unchanged at the default colour (RC-4/RC-7,
  **UNVERIFIED** — no automated cover; S10 requires a byte-identical `.ass` diff).

### Unstaged at checkpoint time
- `.claude/settings.local.json.tmp.3012965.9704ffe2b8fd` — a stray editor temp
  file, unrelated to this task. Left alone, not staged.

---

## Previous task — COMPLETE
- **Task:** A job run with video only failed after 47 minutes of CPU Whisper:
  the analysis collapsed and blamed the transcript. **All six stages committed
  and verified against the real job.**
- **Checkpoint commit before the work:** `4703679`. **Head:** `d844177`.
- **Tier-1:** CI env **1110 passed, 101 skipped, 0 failed** (baseline was
  1069/101). Local **1236 passed**.
- **Tier-2:** no E2E browser suite exists in this project. Verified instead by
  re-running the real failed job against the live provider (below).
- **Tier-3:** ~30 new tests across `test_provider_registry.py`,
  `test_llm_negotiation.py`, `test_analysis_windows.py`, `test_preflight.py`
  (new) and `test_dashboard_payload_contract.py`. Every one that guards a
  behaviour change was verified to FAIL against the pre-change code.
- **Not pushed.** `git push origin main` when you are ready — remember the
  memory note: `github_osc_better` AND `-F /dev/null`.
- **Plan:** `/home/ubuntu/.claude/plans/i-wanted-to-test-effervescent-hearth.md`
- **Decisions written:** DEC-052 … DEC-059.

### The result
The job that died after 63 minutes claiming the video was "all housekeeping"
now produces **5 clips in 590s** from the same transcript (Whisper not re-run).

### What was wrong — four things, not one
1. **The shipped NIM model answered nothing.** `google/gemma-4-31b-it` returns
   no reply in 120s to an 8-token request. Not 410, not an error, still in
   `/v1/models` — it simply hangs. DEC-021's reading of those 504s ("the request
   asks for too much work") is falsified.
2. **The budget check weighed the backoff, not the request.** DEC-020's
   predictive rule lived only in the legacy `engine.py`; `llm.py` compared a
   4/12s sleep against the deadline while the request ran up to 330s.
3. **One window could spend the whole run's budget**, so windows 2-6 were
   skipped untried — breaking DEC-027's "a failure is local".
4. **The error blamed the content.** `_pass_a` discarded each window's failure
   reason, so "every window answered and found nothing" and "no window ever
   answered" produced the same message.

### The two defects the test suite could not have found
Both surfaced only by running the real job, and both are worth remembering:

- **A model can be fast, schema-valid and useless.** `deepseek-v4.1-flash`
  shipped for one commit and answers `{"candidates": []}` in seven tokens on
  every real transcript, including one that had already yielded seven clips.
  Every guard in the project passes it. Only the real workload catches it
  (DEC-058). The default is now `nvidia/nemotron-3.5-lightning-30b-a3b`, which
  two earlier benchmark rounds had rejected as "reasoning prose" — that was a
  **missing flag**, not the model: `_extra_body` turned thinking off for
  "deepseek" and nothing else. **Check `_NIM_REASONING_FAMILIES` before judging
  any new NIM candidate.**
- **A window granted exactly one request's worth was refused**, because the
  clock moves between granting the deadline and measuring it. The never-tries
  bug, reintroduced by rounding inside the mechanism written to prevent it. The
  fake runners in the tests trusted the allowance instead of re-reading the
  clock, so nothing caught it (DEC-059).

### Where it still hurts, and the one thing left to do
**Set a Groq key.** NVIDIA is the last link and the slowest: ~90s per request,
so the 590s run lost window 5 to a truncated reply it had no budget to retry,
skipped window 6, and gave clip 5 a basic title. All reported honestly, but it
is running at the edge of the 900s budget. Groq is the chain's **first** link
and the fastest free tier, and Settings now has a field for it (it did not
before — that gap is why this job had a single point of failure). With it the
analysis finishes in seconds and none of the budget machinery binds.

### Deploy
The dashboard changed, so the container needs a rebuild:
`docker compose rm -sfv backend && docker compose up -d --build backend`
(the `-v` matters; `down -v` would delete the Caddy certificates). Docker needs
sudo on this box. Then re-run the failed job with **Clone & Rerun**, which
reuses the job id so `config_adapter` adopts the existing `transcript.vtt` and
skips Whisper entirely.

### Still true
- `sudo chown -R "$(id -u):$(id -g)" data` on any older clone.
- CI installs pytest and nothing else. Reproduce it exactly:
  `pip install --target /tmp/cilibs pytest` then
  `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest -q`.
  `PYTHONNOUSERSITE=1` is the load-bearing half.
- `.env` is gitignored; it now declares each key exactly once. Backup of the
  old one: `.env.backup-20260922-080505`.

### Follow-ups, deliberately not done
- `cfg.llm_timeout` is declared, parsed, documented and tested — and reaches
  nothing. Same shape as DEC-051's clip length.
- `analysis_budget_seconds` is read by `analyzer.py` and settable from nowhere.
  It is the binding constraint on a slow provider.
- `MAX_TOKENS_CANDIDATES = 700` truncated one real reply mid-object.
- `.claude/ASSUMPTIONS.md` has **two** entries numbered A-010 (pre-existing).

---

## Previous task (complete, committed)
- **Task:** Two defects the human found while using the deployed app: a
  "blackscreen like bug" a second into every clip, and no way to make clips
  longer. **Both fixed and committed**; the container is rebuilding.
- **Phase:** IMPLEMENT complete → verify on the rebuilt container, then push.
- **Checkpoint commit:** `ee2c1fe` (the media-access task, deployed and working).
- **Tier-1:** pytest **1198 passed, 0 failed**.
- **Next action:** once `docker compose up --build` finishes, confirm the Clip
  Length select is present, render one clip with Hook Glitch ON and check the
  transition frame is static (~126/255) rather than black (~19/255), then
  `git push origin main`.

### The black frame
It was the **Hook Glitch** transition, and it was real. The lavfi fallback — the
only path since the pinned source video went private — built its noise on
`color=c=black`. `noise` adds a *signed* offset, so on pure black every negative
value clamps to 0 and only the positive half survives: **19.2/255 mean luma**, a
black frame with faint speckle. `blackdetect` never fired because it is not
*quite* black, which is why nothing caught it. On mid-grey the identical chain
measures **126.9/255, stddev 25.8** — actual static.

Also flipped the default **off** in all five places it is defined (config
constant, `JobCreateRequest`, `config_adapter`, the CLI, `NewJob.jsx`): a
one-second full-frame effect on every clip is an opt-in. `--hook-glitch` enables
it; `--no-hook` is kept and still wins.

**The font bug's twin, caught before it bit:** `glitch_ready_{w}x{h}.ts` is
cached by filename and returned unconditionally, exactly like
`custom_fonts/Montserrat-Regular.ttf` was (DEC-049). Any machine that had
rendered once would have kept the black `.ts` forever and the fix would have
looked inert. The cache key now carries `GLITCH_RECIPE_VERSION` (now 2), so
changing the filter chain invalidates every cached file automatically.

### Clip length
`platform` picks the window each clip is snapped into — `auto` 20–75s,
`tiktok`/`reels` 15–90s, `shorts` 15–59s, `long` 60–179s. The API has declared
it since the snapper landed and it reaches `cfg`, but `NewJob.jsx` never sent
it, so every job from the UI was `auto` — which is why the reported clips came
out 22–41s. Same class of gap as the provider select that could not reach
`chain`. A Clip Length select now sends it; **default stays `auto`** so nothing
is silently re-cut.

### ⚠️ Run the CI suite, not just the local one, before pushing
CI is `pip install pytest` and nothing else (DEC-012). A test that reaches
`web.api.models` without a guard passes here and fails on every push. **101
tests skip in that environment**, so a green local run proves less than it
looks. Reproduce it exactly:

```
pip install --target /tmp/cilibs pytest
PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest -q
```

`PYTHONNOUSERSITE=1` is the load-bearing half: it hides `~/.local`, where
pydantic, fastapi, httpx and cv2 live on this box. Expect
**1069 passed, 101 skipped, 0 failed**.

Two ways to handle a test that needs a heavy import, and the choice matters:
`pytest.importorskip(...)` when the test genuinely exercises the object, but
**read the source as text** when the test is a guard against drift — an
importorskip on a guard means it never runs in the one place that checks every
push.

### Still true from the previous task
- `sudo chown -R "$(id -u):$(id -g)" data` on any older clone, or the API token
  changes every restart and settings silently fail to save. Fixed at the source
  by tracking `data/.gitkeep`.
- Deploy with `docker compose rm -sfv backend && docker compose up -d --build
  backend`. The `-v` matters; `down -v` would delete the Caddy certificates.
- No E2E browser suite exists in this project.

---

## In progress
- **Task:** The clips rendered but the app could not show them; then land
  everything on `main`. **COMPLETE — all stages committed and verified in a
  real browser.**
  Plan: `/home/ubuntu/.claude/plans/i-want-the-code-breezy-starlight.md`
- **Phase:** DONE, pending the human's acknowledgement on Tier 2 (below).
- **Open questions:** none.
- **Branch:** `feature/rzdhop-clips-rearchitecture`.
- **Checkpoint commit:** `233b860` (pre-task). Tier-1 there: **1008 passed**.
- **Tier-1 now:** pytest **1187 passed, 0 failed**. +179: 46 came from
  `origin/main` in the merge, 133 are new here.
- **Tier-2:** this project has **no E2E browser suite** — no playwright, no
  cypress, no `e2e/` — so there is nothing to run. Done instead: the real app
  was started with the dashboard built in a scratch dir, and job
  `2773bd83c7b6` was opened and driven in a browser (results below).
  **The human still owes an explicit acknowledgement that no E2E suite exists
  and that the browser check stands in its place.**

### What was wrong, in one paragraph
`c53949b` put `Depends(require_token)` on the whole files router and the token
is header-only by design, but `<video src>` and `<a href download>` are requests
the BROWSER makes and cannot carry a header. All three reported symptoms were
one `401 {"detail": ...}`: the player got it instead of video, the `download`
attribute saved that JSON body and the browser renamed it `.json`, and a pasted
URL got it too. The clips were never the problem. Fixed by signed, expiring,
per-file URLs (DEC-048).

### Stages, all committed
| # | Commit | What |
|---|---|---|
| 0 | `0b4ff43` | checkpoint + regression contract |
| 1 | `5ac08b9` | merge `origin/main`, both provider paths kept (DEC-046/047) |
| 2 | `114cef3` | HMAC sign/verify/mint helpers in `web/api/auth.py` |
| 3 | `87477f4` | `require_token` accepts a media signature — **riskiest**, mutation-tested |
| 4 | `17750e7` | inline vs `?download=1`; Range asserted live |
| 5 | `516305b` | `thumbnail_url` + `srt_url`, derived on read |
| 6 | `b19672f` | URLs signed at serialization — **the fix** |
| 7 | `2fb25b1` | dashboard: poster, `.srt` button, expiry recovery |
| 8 | `a383fa4` | the subtitle font was DejaVuSans on every clip (DEC-049) |
| 9 | `9bfa0ff` | stop fetching the private glitch video |
| 10 | `65d0e52` | docs, DEC-048/049, CHECKPOINT reordered |
| 10b | `5207985` | SPA deep links 404'd — found in the browser, not by a test |

### Verified in a real browser, against the human's real job `2773bd83c7b6`
All 7 cards render with their thumbnail as the `poster`. All 7 videos fetched
**206 Partial Content carrying no credential**; `readyState` 4, 1080x1920,
27.3 s. A seek to 0:20 succeeded and stayed at `readyState` 4, so Range seeking
works. The Download link is
`/api/outputs/…/highlight_rank_1_ready.mp4?exp=…&sig=…&download=1` with
`download="highlight_rank_1_ready.mp4"`, answering **200 `video/mp4`,
`Content-Disposition: attachment`** — an `.mp4`, not the `.json` that started
this. The `.srt` button is present and signed. Every client-side route serves
the app, missing assets still 404, `/api/jobs` still 401.

### And at the HTTP level
All 7 clips: `.mp4` + `.jpg` + `.srt` fetched over HTTP with **no headers at
all**, 206 on a Range request, `?download=1` giving `attachment` with the right
filename, two consecutive reads returning byte-identical URLs, the token
appearing nowhere in the response, the same URL stripped of its signature still
`401 application/json` (the exact response that was being saved as `.json`),
that signature on `/api/jobs` and `/api/shutdown` still 401, and
`outputs/jobs.json` never written. The font fix was proved with a real libass
burn: `fontselect: (Montserrat, 400, 0) -> Montserrat-Regular`, where the
human's run said `-> DejaVuSans.ttf`.

### Deployed and verified on the real container (2026-09-21 20:36 UTC)
The human rebuilt and started `rzc-backend`. Confirmed against it: every SPA
route serves the app and `/assets/nope.js` still 404s; the served bundle is
`index-tVovS33m.js`, the build carrying the JobDetail changes; all three clip
URLs come back signed; the clip plays with **no headers** (200 `video/mp4`), a
range request answers **206**, and `?download=1` returns a real ISO Media MP4 as
`attachment; filename="highlight_rank_1_ready.mp4"`. The unsigned URL is still
401, and a clip signature on `/api/jobs` is still 401.

**One silent deployment bug was found in that log and fixed** (`d55271c`):
`./data` is a bind mount, gitignored, so it does not exist in a fresh clone —
and Docker creates a missing bind-mount source as **root**, after which the
container (host uid, 1001 here) cannot write it. The API token could not be
persisted, so it **changed on every restart**; and settings persistence answered
200 while failing. Now `data/.gitkeep` is tracked so a clone owns the directory.
On an older clone the one-time fix is `sudo chown -R "$(id -u):$(id -g)" data`.

### ⚠️ Blocker the human must clear once, before building the dashboard LOCALLY
`web/dashboard/node_modules` and `web/dashboard/dist` are both **empty
root-owned directories** — mount points docker created — so `npm ci` dies with
EACCES. Unrelated to this task, but it blocks the documented build step:

```
sudo rm -rf web/dashboard/node_modules web/dashboard/dist
```

The Docker build is unaffected (`.dockerignore` excludes both). The JSX in this
task was verified by copying `web/dashboard/` to a scratch dir and building
there: vite 6.4.3, 48 modules, clean.

### Deploying this
```
docker compose rm -sfv backend && docker compose up -d --build backend
```
`rm -sfv`, **not** `docker compose down -v`: the latter also deletes the
`caddy_data`/`caddy_config` volumes and any issued TLS certificates. The `-v`
matters — the anonymous volume at `/app/web/dashboard/dist` survives
`up --build`, so a rebuilt image otherwise keeps serving the old bundle and the
fix looks like it did nothing.

### Regression contract for this task — all green
| # | Must keep working | Proven by |
|---|---|---|
| MC-1 | Every non-media route still refuses an unauthenticated request | `tests/test_auth_token.py`, green unchanged |
| MC-2 | A media signature is not a general credential | a valid clip signature on `/api/jobs`, `/api/settings`, `/api/upload`, `/api/shutdown` → 401 |
| MC-3 | The outputs listing stays private | `GET /api/outputs/{job}` has no `filename` param, so no signature can reach it |
| MC-4 | The traversal guard still refuses | `tests/test_auth_token.py:309-360` plus a signed-escape test |
| MC-5 | The manifest/worker contract stays exact | `tests/test_manifest_fields.py` — `srt_path` added to the literal, not worked around |
| MC-6 | The dashboard mount stays last | `test_the_dashboard_mount_is_the_last_route_registered` |
| MC-7 | The token never travels in a URL | `test_the_token_never_travels_in_a_query_string`, plus `test_the_token_is_not_in_the_url` |
| MC-8 | `outputs/jobs.json` is never rewritten | derived on read; mtime unchanged after a live read |

### Follow-ups deliberately not done
- **`docs/studio/`** — the stale GitHub Pages client. Its `api.js` has no token
  support and uses `EventSource`, which cannot send headers, so it is already
  wholly non-functional against a token-gated API. Retire it, or give it a token
  field and a `fetch`-based stream. Not a media-playback fix.
- **`web/api/settings_store.PERSISTED_KEYS` has no UI for `GROQ_API_KEY`,
  `OPENROUTER_API_KEY`, `MISTRAL_API_KEY` or `LLM_CUSTOM_*`.** The Settings page
  covers Google, NVIDIA, Pexels, HF and the `openai_compat` trio only, so four of
  the chain's providers can be configured by `.env` alone.
- **Stage 11 of the previous task** (retiring the legacy analysis path) is still
  deliberately undone, and is now *more* entangled: `openai_compat` joined that
  path in the merge.

---

## History below this line

Everything that follows is closed work, including the banner and sections
that arrived from `origin/main` in the 2026-09-21 merge. Read it for
context, not for what to do next.

> **Two streams of work were merged on 2026-09-21.** Both are complete. One made
> the analysis provider pluggable and reworked the job-creation UI; the other
> fixed the NVIDIA retry behaviour, capped the time budget and persisted the
> Whisper transcript. They touched the same file, `clipping/engine.py`, and the
> merge kept both: the retry ladder, the SDK-retry fix and the time budget now
> apply to **every** provider, because they live in the shared core that the
> NVIDIA and custom-endpoint wrappers both call.
>
> **Tier-1 on the merged tree: 419 passed, 0 failed.** `compileall` clean.
> Decisions DEC-023 to DEC-026 were renumbered from DEC-016 to DEC-019 during
> this merge; see the note in DECISIONS.md.


## Last task — provider settings, job guidance, render options (COMPLETE, VERIFIED)
- **Task:** Add a generic OpenAI-compatible analysis provider, persist the Settings
  page values, add provider guidance + warnings to New Job, expose the render
  quality options. Plan approved 2026-09-21.
- **Phase:** closed out. All 8 planned stages committed (S6 and S7 merged — see below).
- **Open questions:** none.
- **Baseline before the work:** `5bdd31c`, 254 tests passing.
- **Tier-1 now:** 304 passed locally; **282 passed / 16 skipped in a clean
  pytest-only venv**, which is what CI runs (DEC-012); compileall green;
  `main.py --help` green.

| Commit | What |
|---|---|
| `1d65fd7` | S1 stale settings defaults (`cuda`→`auto`, `gemini`→`nvidia`) |
| `6a755cf` | S2 settings persist to `.local/settings.json` |
| `96a92c2` | S3 generic `openai_compat` provider (engine + CLI) |
| `140b752` | S4 web API surface for it |
| `a0f7769` | S5 Settings page custom-endpoint card |
| `a8505a7` | S6+S7 New Job banner, warnings, quality preset, Advanced, dead toggle removed |
| `43a6f1a` | Fix: the new settings test aborted CI collection |
| `a154579` | S8 env samples, compose passthrough, README, DEC-023/024, A-009 |

**S6 and S7 were merged** into one commit: the warnings S6 adds depend on controls
S7 introduces (split-screen and its trigger), so two commits on the same file
could not have been reverted independently — the only reason to split them.

### Verified against a running stack (not just unit tests)

## Previous task (closed — rearchitecture stages 1-10)
- **Task:** Job `756c7ee8a2c3` burned 2h22m and produced nothing. **COMPLETE** — 5 stages, last `c68eb3d`, plus `e5f473d`/`f8ad8b4` CI fixes.
- **Phase:** closed out. Merged to `main` and pushed; `origin/main` is `f8ad8b4`.
- Its findings (the NVIDIA 504 arithmetic, the live probes, the transcript
  persistence design) are preserved below and in DEC-019..022.

### The 45 minutes were 9 requests, not 3 — measured, not inferred
`_make_nvidia_client` (`clipping/engine.py:317`) builds `OpenAI(...)` with
**neither `timeout` nor `max_retries`**. The installed SDK (2.24.0) defaults to
`DEFAULT_MAX_RETRIES = 2` and a 600s read timeout, and
`BaseClient._should_retry` returns `True` for any status >= 500 — so **504 is
retried twice inside the SDK, invisibly**. Each `attempt N/3` line in the log is
1 + 2 = **3 HTTP requests**; the ladder is 9 requests, reported as 3.

Proof from the job's own timestamps, before any probe was run:
- Gaps *between* attempts are **5s** and **15s** — exactly
  `NVIDIA_BACKOFF_SECONDS = (5, 15)`. So all ~15 minutes elapsed *inside* one
  `create()` call.
- A single 900s request is impossible: it would have exceeded the SDK's 600s
  read timeout and raised `APITimeoutError`, not `InternalServerError: 504`.

### Live probe results (2026-09-18, real endpoint, `max_retries=0`)
Each row is ONE http request. Model `deepseek-ai/deepseek-v4-flash-0731`.

| Variant | Elapsed | Outcome |
|---|---|---|
| 60s transcript, 1024 tok, 1 clip | **144.1s** | OK — but `completion_tokens=2`, an empty clip array |
| 1211s transcript, 16384 tok, 7 clips (what the job sent) | **302.1s** | **`InternalServerError: 504`** |

| 1211s, 4096 tok, 7 clips | 124.0s | OK — but `completion_tokens=2`, empty array again |
| 1211s, 16384 tok, **3 clips** | **291.8s** | **OK — 3911 tokens, 3 real clips** |
| 1211s, 16384 tok, 7 clips, **no schema** | 302.1s | **504** |
| 1211s, 4096 tok, **3 clips** | **278.8s** | **OK — 3371 tokens, 3 real clips** |

So the gateway cuts a request off at **~300s**, and 3 × 302s ≈ the 15:09 / 15:08
/ 15:07 seen per attempt. The failure is generation time, not input size — the
1211s transcript is only ~27 KB.

### What the probe ruled out, and what it implicates
Two plausible causes are **eliminated**: `max_tokens` is not the driver (4096 vs
16384 changes nothing about success), and neither is the strict `response_format`
schema — dropping it entirely still 504s at 302.1s, so this is not
constrained-decoding overhead.

**Clip count is the driver.** The arithmetic closes:
- Generation rate measured at **~12–13 tokens/s** (3911 tok / 291.8s; 3371 tok /
  278.8s).
- Cost per clip **~1200 tokens** (23 required fields each).
- A 300s gateway window therefore allows **~3800 tokens ≈ 3 clips**.
- 7 clips needs ~8400 tokens ≈ **~660s**, i.e. more than twice the limit. It can
  never complete, which is why all three attempts failed identically.

**The shipped default is `clips = 7`** (`web/api/models.py:108`,
`Field(7, ge=1, le=30)`). So the default request is ~2.3x what this provider can
deliver, and the documented maximum of 30 is ~10x unreachable — it would need
roughly 3000s against a 300s limit. Even 3 clips lands at 291.8s against ~302s,
about 3% of headroom.

Unresolved and possibly separate: every 7-clip request that did *not* 504
returned an **empty array** with `completion_tokens=2` — the same
`ValueError: NVIDIA returned an empty clip array` the artifacts record from an
earlier job. 3-clip requests never did this.

### Verification of this round
| # | Claim | Evidence |
|---|---|---|
| V-1 | Each visible attempt was 3 http requests | SDK source (`DEFAULT_MAX_RETRIES=2`, `_should_retry` true for >=500) + the job's own 5s/15s inter-attempt gaps + the impossibility of a 900s request under a 600s read timeout |
| V-2 | The gateway cuts off at ~300s | Live probe: the job's exact payload returned 504 at **302.1s** in a single request |
| V-3 | `max_tokens` is not the cause | 4096 and 16384 behave identically (124.0s empty array vs 302.1s 504) |
| V-4 | The strict schema is not the cause | Dropping `response_format` entirely still 504s at **302.1s** |
| V-5 | Clip count is the cause | 3 clips → **291.8s, 3911 tokens, 3 real clips**; 7 clips → 504. ~12–13 tok/s × ~1200 tok/clip ⇒ ~3 clips per 300s window |
| V-6 | The live 504 will trigger the proposal | The probe read `status_code` off the real exception and printed `InternalServerError(504)`, exactly what `_nvidia_request_too_large` keys on |
| V-7 | Unit behaviour | 352 tests; every new test checked against the pre-fix code — `KeyError: 'max_retries'`, the unbudgeted loop running all 3 attempts, the 3 proposal tests, and the duplicate CUDA warning reporting "appeared 2 times" |

### Live end-to-end against the real API (2026-09-18)
Ran the real `analyze_with_nvidia` against the live endpoint with `clips=7`,
on the *auto-degrade* build that preceded `46d341c`:

```
🔁 attempt 1/3 -> ⚠️ InternalServerError: 504
✂️ 7 clips is more than this model can generate — asking for 3
🔁 attempt 2/3 (3 clips) -> RESULT: 3 clips in 581.0s, ranks [1, 2, 3]
```

That is the evidence behind the number the proposal hands the user: **3 clips
really does succeed on this endpoint immediately after a 7-clip 504**, and the
581.0s total matches the probe (302s + 279s).

### The transcript is discarded — found while checking the proposal's cost
`resolve_transcript(cfg)` (`web/api/worker.py:221`) returns the transcript in
memory and `analyze_with_ai` (`:244`) consumes it. **Nothing ever writes it to
disk** — verified on a *successful* job too: `outputs/275d7caf2436/` holds
`gemini_response.json`, the source mp4, clips, thumbnails and
`render_manifest.json`, and no transcript. The failed job's directory is empty.

Consequence, and why it matters here: the proposal added in `46d341c` tells the
user to re-run with fewer clips, and on a CPU job doing so **re-runs the
94-minute transcription**. Measured cost of the two designs:

| | Time | Outcome |
|---|---|---|
| Auto-degrade (rejected in review) | 581s | 3 clips delivered |
| Propose (chosen) + re-run on CPU | ~604s + **~94 min** | 3 clips, after a full re-transcribe |

It is also a standalone bug: a 94-minute artifact is destroyed by any failure at
or after analysis, in a project whose premise is local-first and which already
accepts `--transcript file.vtt`. Stage 5 fixes it.

### Regression contract for this task
| # | Must keep working | Proven by |
|---|---|---|
| N-1 | The retry ladder still retries genuine transient failures | `tests/test_nvidia_retry.py` (37 tests) stays green |
| N-2 | Fatal errors (bad key, 4xx) still fail fast, not after 3 attempts | same suite |
| N-3 | `response_format` 400-fallback to prompt-only still works | same suite |
| N-4 | The activity feed still shows each attempt and its reason | it reads stdout; print sites unchanged |
| N-5 | Gemini path untouched | `REQUEST_TIMEOUT_MS` at `:1120` is Gemini's and is not in this diff |

## Previous task (closed)
- **Task:** Close the four remaining follow-ups, then merge to `main` and push.
  **COMPLETE** — stages `eb1feca`, `d4d5c78`, `a269a8f`, `f296eb3`.
  Scope approved by the human: (1) the remaining layout items, (2) the `gdown`
  packaging bug, (3) `run_upload.py`'s broken `youtube_uploader.safety` import,
  (4) the dead yt-dlp imports in `clipping/studio/`.
- **Phase:** closed out. Pushed: `origin/main` moved `105cddc` → `d03fd41`,
  and the main checkout was fast-forwarded to match.

## Previous task (closed)
- **Task:** The dashboard scrolled sideways at phone width. **COMPLETE.**
- **Phase:** closed out.
- **Checkpoint commit:** `105cddc` was the baseline. Stages: `ac622fb` the
  content column, `cbc389b` unbreakable tokens. Artifacts: `36aa45f` (before),
  and this commit.
- **Tier-1:** `python -m pytest -q` = **327 passed, 0 failed** before and after
  (needs `PYTHONPYCACHEPREFIX` locally — see the root `__pycache__` note below).
  `npm run build` green; the stylesheet went 13.19 kB -> 13.38 kB.
- **Tier-2:** no E2E suite exists in this project, so Tier 2 is browser
  measurement — done, see below.
- **Tier-3:** no test added. There is no frontend test infrastructure at all
  (no vitest, no playwright, no `test` script in `web/dashboard/package.json`);
  a real guard needs a headless browser asserting `scrollWidth == clientWidth`
  per route, which is a new dependency and harness and so its own task. Listed
  under follow-ups.
- **Open questions:** none.

### Root cause, measured (do not re-derive)
`.main-content` is a flex item of `.app-layout` (`display:flex`) with `flex: 1`
and **no authored `min-width`**, so it kept the flex default `min-width: auto`,
which resolves to its **min-content width** and overrides `flex-shrink: 1`
entirely. Measured in a 375px viewport: `main` = 427.234px, its `min-content` =
427px, and `min-width: 0` brings it to exactly 375px.

All three suspects in the original brief were absent — `grep` finds **zero**
`min-width` and **zero** `calc()` in the whole 897-line stylesheet, and the
`@media (max-width: 768px)` block *does* correctly override `margin-left` and
`padding`. The minimum was implicit, which is why it was not greppable.

**It was never a phone-only bug.** At 820px — sidebar on screen, media query not
applied — a job page with a real `source_url` gave `scrollWidth` 959 against
`clientWidth` 805. See DEC-016 for why the rules are base declarations.

### Contract for the layout fix (do not undo)
- `.main-content { min-width: 0 }` is load-bearing, not defensive. Remove it and
  the column goes back to refusing to shrink below its widest child.
- The wrap and `overflow-wrap` rules are **base declarations on purpose**
  (DEC-016). Moving them into `@media (max-width: 768px)` re-breaks 769–1100px
  while looking correct on every phone.
- `overflow-wrap: anywhere`, **not** `break-word` (DEC-017). Only `anywhere`
  reduces min-content width, and min-content is what travels back up the tree.
  A `break-word` swap looks identical in a screenshot and leaves `scrollWidth`
  wrong.
- `.log-viewer` and every `.activity-*` rule are **outside this diff**. The feed
  is contained because `.log-viewer` is its own scroll container; that is what
  keeps `.activity-message`'s `word-break: break-word` from mattering.

### Verification of the layout fix (2026-09-18)
Measured against a Vite dev server running **this worktree** on `:5174` against
the live backend on `:8000`. The `:5173` server serves the main checkout and
would not have shown the edit.

| # | Contract item | Result |
|---|---|---|
| R-1 | No horizontal scroll at 375px | **PASS** — `scrollWidth == clientWidth == 375` on `/`, `/new`, `/settings` and three job pages; zero elements extend past the viewport |
| R-2 | Same at 414px and 820px | **PASS** — 414/414 and 820/820 (805 where a scrollbar is present) |
| R-3 | Desktop unchanged | **PASS** — at 1280px the sidebar is still 260px, `main` 1020px, and all six step dots sit on one row |
| R-4 | Live activity panel intact | **PASS** — headline, `🤖 NVIDIA` + `deepseek-ai/deepseek-v4-flash-0731` chip, both clocks (`34m 41s on this step · 35m 55s total`), console header, 90 feed lines across three severity classes. `.chip-sub` measures 205px against its 204.697px `max-width` |
| R-5 | Feed follows the tail only when not scrolled up | **PASS** — console scrolled to top stayed at `scrollTop 0` across a poll cycle. `.log-viewer` is not in the diff |
| R-6 | Job cards keep their fields | **PASS** — cards 343px wide, still showing `36% · Analyzing with AI...` |
| R-7 | Python suite unaffected | **PASS** — 327 passed, 0 failed |

Two **stressed** cases, both real overflows found by injecting realistic content
rather than by reading code, both fixed and re-measured at 375px:

| Case | Before | After |
|---|---|---|
| Job page with a real YouTube `source_url` | `scrollWidth` 432 | 375 |
| Job card with a long uploaded filename | `scrollWidth` 568, card 552px | 375 |

### Verification of the follow-up round (2026-09-18)
| Stage | Result |
|---|---|
| `eb1feca` layout remainder | `scrollWidth == clientWidth` on all five routes at **320**, 375 and 1280px. `.config-grid` renders the same three 303px columns at 1280px as the inline style did |
| `d4d5c78` gdown | Declaration only; `pytest` 327 passed |
| `a269a8f` dead imports | AST pass: `YoutubeDL` occurred exactly once in each of the ten (the import). Diff is 10 files / 10 deletions / 0 insertions. `compileall` clean, 327 tests green. **RC-7 not re-verified by a live render** — no cv2, no mediapipe, no docker access on this host |
| `f296eb3` run_upload | With only the absent google-auth chain stubbed: `import run_upload` OK, `--help` builds, every kwarg it passes is accepted by `upload_manifest_to_youtube` |

### Status of the previous task
Live progress / debug feed — **COMPLETE**, stages `58c07a5`, `e83c364`,
`19fd3d7`, `06fb8bc`, `6c325df`, `96d22ec`, docs `105cddc`. Its contract is
below and still binding.

### The feature, in one line
Everything the pipeline prints now reaches the job that printed it, and the job
page says which provider and model is being asked, which retry attempt it is on,
which clip of how many is rendering, and how long it has been on this step.

### Contract for the activity feed (do not undo)
- `clipping/` is **untouched**. Progress is read from the pipeline's stdout, not
  from a callback (DEC-014). Do not thread `on_progress` through `runner.py` /
  `engine.py` / `studio/core.py` without revisiting that decision.
- The tee writes the real stream **first** and records inside a `try`. Recording
  must never be able to break a `print`.
- `store._lock` is an **RLock** on purpose: the tee turns any `print` into a
  store write, so a plain `Lock` deadlocks the worker if anything prints while
  the lock is held (DEC-015).
- `web/api/signals.py` is the **only** place that matches on the pipeline's
  wording. Keep it that way; everything else is generic.
- Event appends use `_persist(force=False)`. Anything a client waits on
  (status, progress, completion) must keep forcing.
- ffmpeg output is **not** in the feed and cannot be — it is a subprocess on the
  real file descriptors. Documented in the README; do not claim otherwise.
- **Progress-bar redraws must not enter the feed.** They go to `progress.detail`
  only. Without this, two 12-second clips fill the 500-entry buffer (90% bars)
  and evict everything worth reading. A bar's identity is the label *before* the
  percentage — a digit-blind normalization folds Rank 2's bar into Rank 1's —
  and the percent-sign requirement is what keeps the retry counters out of the
  coalescing entirely.

### Docker / device verification (2026-09-18, live daemon)
| Item | Status |
|---|---|
| `d845413` container uid | **VERIFIED.** `osc-backend` runs `uid=1001 gid=1001`; `/app/uploads` and `/app/outputs` owned `1001:1001`; in-container write probe OK; `HOME=/tmp`; `/tmp/Ultralytics` 0777. The host `.env` correctly sets `DOCKER_UID/GID=1001` (this host's uid is 1001, not 1000) |
| CUDA branch of the device resolver | **As verified as a CPU-only host allows.** `resolve_whisper_runtime` takes an injectable `cuda_available`; `test_auto_with_cuda_picks_cuda_and_float16`, `test_explicit_cuda_is_respected_when_available` and `test_detection_uses_ctranslate2_when_it_reports_a_device` drive the CUDA-true path. Only `whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build is left, and only a GPU host closes it |
| `9a9adc5` Vite timeout | **VERIFIED at runtime.** After restarting the frontend so the new config was actually loaded: a 20MB upload rate-limited to 50KB/s through the dev proxy returned `HTTP 200 in 390.56s`. That is 90s past Node's default 300s `requestTimeout`, which is the timeout that used to kill the request with nothing in the backend log. The earlier 300MB test never reached the window because loopback was too fast |

### Tier-2 — verified in a browser against the running containers
A real job (`video.mp4` + `subtitles.vtt`, 2 clips) run end to end on the live
stack. The panel rendered:
- the provider/model chip `NVIDIA · deepseek-ai/deepseek-v4-flash-0731`
- `attempt 2 of 3`, amber once past the first attempt
- time-on-step and total, both ticking
- the quiet notice after 45s of silence
- the feed, severity-coloured, carrying things that were previously invisible:
  `⚠️ 718 of 2095 words (34%) in subtitles.vtt had backwards timestamps and were
  dropped` and `⚠️ NVIDIA attempt 1 failed | ValueError: NVIDIA returned an
  empty clip array.`
- the job list showing `36% · Asking nvidia... ⏱ 6m 26s`

### Known state of the running stack
- Job `d4a133c4e9cd` is stuck in `analyzing` forever: its worker thread died when
  the containers were recreated at 08:26. It is a dead record, not a running job;
  delete it when convenient. It predates this work.
- The repo-root `__pycache__/` is owned by root (from a docker run predating the
  uid fix), so a local `compileall` needs `PYTHONPYCACHEPREFIX`. CI is unaffected.
- ~~The dashboard scrolls sideways at 375px~~ — **FIXED** 2026-09-18
  (`ac622fb`, `cbc389b`). See the layout-fix contract above.

### Verified against real services (previous task, 2026-09-17/18)
| What | Evidence |
|---|---|
| S1 fix is live | `GET /api/settings` returns `default_whisper_device: "auto"`, `default_ai_provider: "nvidia"` |
| Settings survive a restart | PUT a custom endpoint → stop the backend → start it → `🔐 Restored 3 saved setting(s)` and all three values come back |
| Empty value clears an override | PUT `""` for the base URL → the key is **absent** from the file, not stored empty |
| Settings page | Rendered in a browser: NVIDIA first with its free-key link, custom-endpoint card with presets, "✅ Configured" in System Info |
| New Job page | Banner, three-provider select, Fast/Balanced/Best chips, Advanced section all render |
| Missing-key warning | Selecting Gemini (key unset) shows the analysis-step warning |
| Diarization warning | Split screen + diarization with no HF token shows the switch-to-face warning |
| Split trigger default | `face` in the UI, so the unverified pyannote path is not the default one a user hits |
| Dead toggle | "YouTube Subs" absent from the rendered page |
| **Payload round-trip** | Captured the real POST the browser sends (fetch stubbed, no live API call), fed it through `JobCreateRequest` + the adapter: **0 fields dropped**, and every Fast-preset value reached cfg (`render_output_height` 720, cq 30, crf 26, bilinear) |
| Custom endpoint gate | With settings loaded from disk the worker gate passes; clearing the model returns `('openai_compat_model', 'OPENAI_COMPAT_MODEL')` |

### Verified against the LIVE free NVIDIA endpoint (the user authorised it)

### Still unverified
- **RC-8 diarization / split-screen.** Needs `pyannote.audio` + `torch` + an
  accepted HuggingFace model agreement. Unchanged.
- **`whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build.**
  Needs a GPU host; the branch logic around it is covered by injection.
- Everything else previously carried here — the container uid fix, the Vite
  timeout fix, the CUDA branch — is resolved above.

Running it for real found two bugs that no test could have caught.

| What | Result |
|---|---|
| **Default model was dead** | `deepseek-v4-flash-0731` hit EOL at 2026-09-21T08:00:00Z — **the same day**. Every default job failed with 410. Replaced with `nvidia/nemotron-3-super-120b-a12b` (DEC-025) |
| Retry classification, live | The 410 was correctly called fatal and **not** retried: one call, not three |
| NVIDIA path after the S3 refactor | Full CLI run: 55 segments, AI picked 15.2–32.8s and 61.0–86.7s with titles and BGM moods, **2 real clips rendered at 720x1280 h264**, first attempt |
| **Custom endpoint, first live run** | Failed all 3 attempts on `JSONDecodeError` — a reasoning model leaked a bare `[` before its own valid array. Fixed by salvaging the first balanced JSON value (DEC-026) |
| Custom endpoint after the fix | Same run succeeds on **attempt 1** and renders at 720x1280 |
| Fast preset, end to end | `--render-height 720` produced genuine 720x1280 output |

### Not verified
- ~~No live call through `openai_compat`~~ — **done**, and it found a real bug
  (DEC-026). Both providers now verified end to end against a live endpoint.
  Still untested: a *non-NVIDIA* host (OpenRouter, Groq, Ollama). The protocol is
  the same, but each provider's quirks are its own.
- RC-8 (diarization / split-screen render) remains unexercised, as before. S7
  makes split-screen reachable from the dashboard for the first time, which is
  exactly why its trigger defaults to `face`.
- Docker: the compose passthroughs were added but not run against a daemon.

### Follow-ups deliberately not done
- `docs/studio/*.html`, the static GitHub Pages UI: still has the dead
  `use_dlp_subs` toggle, a two-provider select, no custom-endpoint field, and
  still posts `url`/`source` fields the backend purged. It is now further behind
  the React dashboard than it was.
- `notebooks/Quick_Start.ipynb` and `kaggle-studio-server.ipynb` never collect
  `NVIDIA_API_KEY`, so a default-provider run from either fails on a missing key.
- `wiki/2-Getting-Started.md` still calls `NVIDIA_API_KEY` optional.
- `use_camera_switch` is still unexposed in the dashboard; it always needs
  diarization, with no `face` escape.
- **`--clips N` is only a prompt hint.** Nothing truncates the model's list, so a
  run with `--clips 1` rendered 3 clips when the model returned 3. Pre-existing;
  clamping it would change output for existing users, so it was logged not fixed.

## Status of the previous task
- **Task:** Local-first refactor — **COMPLETE and VERIFIED END-TO-END**
- **Phase:** closed out
- **Open questions:** none blocking. One open risk: see A-007.

## Checkpoint commit
Branch `refactor/local-first-engine`. Baseline before the work: `3c72b75`
(clean tree, `main`).

| Commit | What |
|---|---|
| `8947641` | S1–S3 lazy Whisper import, test scaffolding, `clipping/transcript.py` |
| `0825828` | S4 `--video` / `--transcript` CLI surface |
| `1da2c65` | S5 runner wiring + Whisper bypass |
| `6d8bfd8` | S6 NVIDIA hardening (retry, fail-fast, `openai` declared) |
| `9a5db01` | S7 NVIDIA becomes the default provider |
| `253e90d` | S8 story mode local sources |
| `abb0286` | S9 web API local-first jobs |
| `fa7dbad` | S10 the purge (deletions only) |
| `b60fc4b` | S11 docs, notebooks, CI, 2.0.0 |
| `a2cacc9` | Fixes found by end-to-end verification (voiceover import, Windows ffmpeg escaping, worker key gate) |
| `bf797c0` | The shipped NIM model default was retired — replaced |
| `47ca1af` | Web job fields Pydantic was dropping |

## Tier-1
```
python -m pytest -q                                            # 166 tests
python -m compileall -q clipping web main.py youtube_uploader youtube_tracker
PYTHONIOENCODING=utf-8 python main.py --help
```
All green. CI runs the first two on every push.

`PYTHONIOENCODING=utf-8` is needed on Windows only: the help text contains emoji
and the console defaults to cp1252. Pre-existing, unrelated.

## Tier-2 — verified on real media

Everything below was run against a genuine 70s 1280x720 h264 video with a
realistic YouTube-style auto-caption VTT (inline `<00:00:01.234>` word tags,
rolling repetition, 10ms bridge cues).

| # | Behaviour | Result |
|---|---|---|
| RC-1 | `data_segmen` contract | ✅ `assert_valid_data_segmen` across every parser, fixture and producer |
| RC-2 | JSON3 byte-identical after helper extraction | ✅ golden test |
| RC-3 | Whisper fallback | ✅ real inference (`tiny`/CPU) on SAPI-generated speech → contract-valid segments |
| RC-4 | **Karaoke word alignment** | ✅ regenerated the burned-in ASS and compared every Dialogue timing to the source VTT: **0 word mismatches / 44 words, every delta ≤0.010s** (ASS centisecond resolution). The one 0.833s outlier is a word starting before the cut, correctly clamped |
| RC-5 | Gemini still reachable | ✅ dispatch test |
| RC-6 | NVIDIA retry / fail-fast | ✅ 25 unit cases, **plus a live 410 correctly classified fatal and not retried** |
| RC-7 | **Render layer intact** | ✅ real 1080x1920 h264+aac clip (34.1s) + thumbnail from local mp4+vtt |
| RC-8 | Diarization / split-screen | ❌ **STILL UNVERIFIED** — needs `pyannote.audio` + `torch` + an accepted HF model agreement. The audio-path bug it depended on is unit-tested, but the split-screen render was never exercised |
| RC-9 | Story mode | ✅ assembled `hook_1.mp4` + `highlight_1.mp4` from two local sources; one used its VTT (Whisper bypassed), the other fell back to Whisper |
| RC-12 | Stdlib-only CI suite | ✅ verified in a clean venv with pytest as the only dependency (180 passed, 5 skipped). Any new web test must be checked there, not just locally |
| RC-11 | Job settings reach the pipeline | ✅ `tests/test_web_job_fields.py` — audit guard + round-trip for `video_cq`, `split_trigger`, `yolo_size`, `diarization_speakers`. Proven non-vacuous (8/11 fail against the pre-fix model) |
| RC-10 | Web API | ✅ upload mp4 → upload vtt → POST job → **`completed`** with a real 1080x1920 render; provenance persisted, `url` is `None` |

Dedupe effectiveness, measured on the realistic fixture: **86 words with dedupe
vs 244 without** — the LLM would otherwise have seen every sentence ~3x.

## What verification found (none caught by unit tests)

1. **`--voiceover` made `google-genai` mandatory for every run.** Optional
   feature, unconditional import.
2. **Subtitle burn-in failed on every Windows path** — pre-existing;
   `clipping/studio/` had an empty diff across all 11 stages. Correct escaping
   established by probing ffmpeg with 7 candidate forms.
3. **The shipped `--nvidia-model` default was dead** — `deepseek-v4-pro` returns
   `410 Gone` (EOL 2026-08-07). The brief's alternative
   `meta/llama-3.1-70b-instruct` is retired too.
4. **The web worker demanded a key for a render-only rerun.**
5. **`load_gemini_json` / `nvidia_model` never reached the backend** — undeclared
   on `JobCreateRequest`, so Pydantic dropped them.

## Remaining risks

- **A-007 (substantive):** `deepseek-ai/deepseek-v4-flash-0731` is confirmed to
  exist (probe returns 401, not 410) but has **not been called with a real key**,
  so its `guided_json` conformance is unverified. First real run will confirm.
- **RC-8:** split-screen / diarization not exercised.
- ~~21 more `JobCreateRequest` fields are still dropped by Pydantic~~ — **DONE**
  (`91b7712`, `832300c`); guarded by `tests/test_web_job_fields.py`.

## Known pre-existing breakage (not from this task)
- ~~`run_upload.py:17` imports `youtube_uploader.safety`~~ — **FIXED**
  (`f296eb3`). **Two corrections to what this entry used to say:** it was *not*
  excluded from the `compileall` CI job — `.github/workflows/ci.yml:29` has
  always included `run_upload.py`, and `compileall` byte-compiles without
  executing imports, so it structurally cannot catch a missing module. And the
  file had **two** breakages, not one: the dead import plus two keyword
  arguments (`safety_config`, `skip_approval`) that `upload_manifest_to_youtube`
  no longer accepts, so restoring `safety.py` alone would only have turned the
  `ImportError` into a `TypeError`. See DEC-018.
- `gdown` is declared in `pyproject.toml` only, so `--hook-source <drive-url>`
  fails in every documented install path.

## Follow-ups deliberately not done
- **No frontend test infrastructure.** A `scrollWidth == clientWidth` guard per
  route needs a headless browser (playwright/vitest + a harness), which is a
  dependency decision of its own. Until then the layout fix has Tier-2 evidence
  only.
- **`web/dashboard` ships no lockfile.** Only `node_modules/` is gitignored, so
  `npm install` produces an untracked `package-lock.json` that nothing pins.
  That conflicts with the "pin and verify" rule; adding one is a dependency task.
- ~~`.config-grid` missing from the media query~~ and ~~inline
  `minmax(300px, 1fr)` at `NewJob.jsx:384`~~ — **both FIXED** (`eb1feca`), and
  both were mis-described. `.config-grid` was *dead CSS* that no JSX referenced,
  not a rule missing a breakpoint; the grid now uses it. The NewJob grid does
  not overflow at 375px or 320px — it breaks below ~316px — and the real
  overflow at 320px was the provider chip, which nothing had listed.
- **CI cannot catch an orphaned import.** `.github/workflows/ci.yml:28` claims
  the `compileall` step "catches orphaned references in modules the test suite
  does not import". It cannot — `compileall` byte-compiles without executing
  imports, which is why `run_upload.py` stayed broken. A real guard is one line:
  `python -c "import run_upload"`. Not added here because CI's installed deps
  were not verified against it.
- **The two dependency manifests diverge in both directions.**
  `requirements.txt` carries 9 packages `pyproject.toml` lacks (`numpy<2.0.0`,
  `pyannote.audio`, `torch`, `torchaudio`, `fastapi`, `uvicorn`,
  `python-multipart`, `pydantic`, `edge-tts`). Only the `gdown` case was fixed;
  reconciling them is a dependency task.
- **Product question, not a bug: do YouTube uploads want guardrails again?**
  `upload_safety.json` is tracked but orphaned, and `Safety.md` exists to
  re-enable the feature. See DEC-018 — restore from `5bf93d5`, never from
  Safety.md.
- `hook_manager.py` (`--hook-source`) is now the only network fetch left in the
  CLI pipeline, which is inconsistent with local-first.
- ~~9 dead `from yt_dlp import YoutubeDL` imports remain in `clipping/studio/`~~
  — **FIXED** (`a269a8f`). The count was wrong: there were **10**, across 12
  files carrying the import. `studio/effects.py` and `studio/transitions.py`
  keep theirs (real call sites at `:86` and `:156`, per DEC-001).
