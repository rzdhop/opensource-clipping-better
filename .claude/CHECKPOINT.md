# CHECKPOINT

## In progress
- **Task:** Re-architect into **rzdhop's clips** — free hosted AI endpoints, any
  device, auto-download of video + subtitles, SRT in/out, every feature kept.
  Plan approved by the human: `/home/ubuntu/.claude/plans/hey-here-is-sleepy-walrus.md`
  (11 stages; read it before resuming — it carries the root causes and the design).
- **Phase:** IMPLEMENT — **Stages 1-9 DONE.** S1 rolling-display cue semantics +
  SRT writer (`5d46c81`); S2 the provider core (`52ac830`); S3 beats, snapping,
  presets and language detection (`cbe10f3`); S4 the three-pass analyzer
  (`e2856b6`, `ed447b6`), proven live; S5 hosted transcription (`f54d508`); S6 the
  truth fixes (`818d748`); S7 auth, the static dashboard and Tailscale
  access (`c53949b`); S8 URL ingestion and the PC helper
  (`20102bd`); S9 SRT export. Next: Stage 10, the rename to rzdhop's clips.
- **⚠️ Production was already broken before this work:** the shipped NVIDIA model
  `deepseek-ai/deepseek-v4-flash-0731` now returns **410 Gone** (it answered a
  real job on 2026-09-19 and was dead by 2026-09-21; the whole DeepSeek v4
  family has left the NIM catalogue). The default is now
  `google/gemma-4-31b-it`, picked by live benchmark — 6.0s and 31.3 tok/s
  against the real workload, 3/3 schema-valid. See DEC-024.
- **Branch:** `feature/rzdhop-clips-rearchitecture`, cut from `main` at `f8ad8b4`.
- **Checkpoint commit:** `f8ad8b4` — clean tree, identical to `origin/main`.
  Roll back here.
- **Tier-1 baseline at `f8ad8b4`:** pytest **369 passed, 0 failed**; `compileall`
  clean. (Needs `PYTHONPYCACHEPREFIX` locally — see the root `__pycache__` note.)
- **Next action:** Stage 10 — rename to **rzdhop's clips**: README ×2,
  `docs/*.html`, `wiki/*.md`, `web/api/app.py`, `main.py`, compose container
  names, `pyproject.toml`, `package.json`, `index.html`, `App.jsx`, plus one
  `__version__` and `docs/api.md`. **Do NOT rename:** the `clipping/` package,
  any `clipping/studio/` symbol, `gemini_response.json`, `transcript.vtt`,
  `highlight_rank_N_ready.mp4`, `OSC_VIDEO_SCALE_ALGO`, `outputs/jobs.json`.
- **The human must now, before the app is reachable from the phone:**
  1. enable HTTPS certificates in the Tailscale admin console, then
     `tailscale serve --bg --https=443 http://127.0.0.1:8000`;
  2. rebuild (`docker compose up -d --build`) and read the API token off the
     startup log, or `docker compose exec backend cat /app/data/api_token`.
  See `docs/deploy-tailscale.md`.
- **Rollback for the new analysis:** `--ai-provider nvidia` (or `gemini`) runs
  the original single-request path, which is still in `engine.py` untouched and
  still covered by `tests/test_nvidia_retry.py`. Stage 11 retires it, and not
  before a full live job has completed on the new path.
- **Inspect before rendering:** `--dry-run-analysis` writes
  `gemini_response.json` + `metadata_preview.json` and stops; re-run with
  `--load-gemini-json` to render from them for free. It needs no render stack,
  so it works on this host, which has no cv2 or mediapipe.
- **The human still owes four keys** (Groq, Gemini, OpenRouter, Mistral). Until
  then the chain runs on NVIDIA alone, which works: keyless links are skipped
  with a printed reason. `python tools/bench_llm.py` validates each key as it
  arrives and prints a suggested `LLM_CHAIN` ordered by measured speed.
- **Tier-1 after Stage 9:** pytest **978 passed, 0 failed**; `compileall` clean.
  Under the simulated pytest-only CI environment: **918 passed, 32 skipped, 0
  failed**. Previously: 900 after S8, 848 after S7, 799 after S6, 782 after S5, (733 after S4, 648 after S3, 523 after S2, 368 after S1, 328 at
  baseline) — every new test runs in CI, none of them skipped.
- **Stage 5 is NOT live-verified.** Audio extraction and chunking are (see
  below), but the hosted transcription call itself needs `GROQ_API_KEY`, which
  does not exist yet. Until then transcription still falls through to local
  Whisper, which on this host is 4.6x realtime.
- **Tier-1 after Stage 1:** pytest **409 passed, 0 failed**; `compileall` clean.
  Under the simulated pytest-only CI environment: **368 passed, 20 skipped, 0
  failed**, against a measured baseline of **328 passed, 20 skipped** — exactly
  the 40 new tests, no new skips. The simulator must allow `pygments`,
  `exceptiongroup` and `tomli`: pytest 8.4+ depends on pygments, so CI has it.
- **Tier-2 owed to the human:** a live render on the running containers. This
  host has no `cv2`/`mediapipe` and the docker socket is permission-denied, and
  the running backend imported `clipping.transcript` before the fix, so it needs
  `docker compose restart backend` before any job exercises the new parser.
- **Open questions:** none blocking. The human must create the four API keys
  (Groq, Gemini, OpenRouter, Mistral), enable HTTPS certificates in the Tailscale
  admin console, and install Python + yt-dlp on `pops-1` before Stages 2/5/7/8
  can be verified live.

### Why this task exists — the two faults, both measured
1. **The transcript loses ~35% of its words before the model sees it.** The
   human's subtitle exports (DownloadYoutubeSubtitles.com, VTT and SRT alike)
   carry single-line cues with **no inline word tags**, **unique text** and
   **overlapping windows** (0.560→2.280, 1.439→3.800, 2.280→6.000 …).
   `_expand_run_to_words` spreads each cue's words evenly across its own span,
   so cue N+1's first words start before cue N's last words, and
   `_enforce_monotonic` drops them. Dedupe cannot help — the text never repeats.
   **Measured:** `uploads/subtitles.vtt` → 718 of 2095 words dropped today;
   simulating the rolling-display clamp keeps **2095 of 2095**. That file has
   371 cues and **zero** inline tags, and no existing fixture contains a single
   overlapping cue pair, so the fix cannot move an existing test.
2. **The analysis asks for more output than any free provider can produce.**
   One request demands 22 required fields per clip (~1200 output tokens each)
   from a model measured at ~12 tok/s behind a ~300s gateway: 7 clips needs
   ~660s and can never finish, and it also exceeds Groq's 8k TPM. Five of those
   fields are read by nothing. The replacement is three small passes with a
   largest single generation of ~320 tokens. See the plan for the budget table.

### Stage 4 proved out live — the first real clip set this project has made
Ran the real three-pass analysis against the live NVIDIA endpoint on the
human's own 20-minute French video, `--clips 3 --platform tiktok
--dry-run-analysis`. It succeeded in **~13 minutes on the slowest provider in
the chain** — NVIDIA alone, because Groq and Gemini have no keys yet and were
skipped with a printed reason, exactly as designed.

| rank | span | duration | title (native / en) |
|---|---|---|---|
| 1 | 750.5 → 799.7 | 49s | Sa femme l'étrangle au lieu de l'appeler / His wife strangles him instead of calling him |
| 2 | 0.4 → 35.4 | 35s | L'île des puceaux / The Virgin Island |
| 3 | 1005.6 → 1052.9 | 47s | Un médecin qui fait ça avec son client ? / A doctor doing that with his client? |

Every contract property was then checked against the transcript, not assumed:
all three inside the 15-90s tiktok window, **zero overlap** between them, each
starting on a beat boundary, each hook inside its clip, every b-roll starting
after its hook ends, and **every emphasis word genuinely spoken** (`puceaux`,
`viergent`, `bomboclat`, `virginité`). Output is in
`outputs/{gemini_response,metadata_preview}.json`.

The clips are also well spread (0s, 750s, 1005s), which is the re-rank pass
doing what the monolith claimed to do and never could.

### Stage 5, verified as far as a machine with no Groq key allows
Audio extraction and chunk planning were run against the real video:

| measurement | result |
|---|---|
| extraction time | 6.7s for a 20-minute video |
| audio size | **39.3 MB** at 16 kHz mono FLAC, confirmed by `ffprobe` |
| Groq free-tier cap | 25 MB — so this file genuinely needs splitting |
| plan | 2 chunks (0→600s, 596→1211.3s), boundaries snapped to silence |

The 39.3 MB is worth remembering: an earlier note here estimated 19-23 MB from
the format alone and was wrong, because FLAC is variable-rate and this video has
music under most of it. The planner derives bytes-per-second from the actual
file, so a quiet interview of the same length stays one request.

### Regression contract for this task
| # | Must keep working | Proven by |
|---|---|---|
| RC-1 | The `data_segmen` contract | `tests/helpers.py::assert_valid_data_segmen` across every parser and producer |
| RC-4 | Karaoke word alignment | **VERIFIED for Stage 1 without a render**: loaded the real `clipping/studio/subtitles.py` with `cv2`/`mediapipe`/`numpy`/`requests` stubbed (`buat_file_ass` touches none of them), regenerated the ASS and compared every highlighted word back to the source — **0 mismatches** across 4 fixtures plus a 60s window of the real French file, at 10ms tolerance (ASS centisecond resolution). A full ffmpeg render is still owed |
| RC-7 | The render layer is intact | `clipping/studio/` is **unmodified so far** (Stage 6 adds one additive line). `tests/test_slim_schema_adapter.py` reads `studio/*.py` and `runner.py` by AST and asserts every clip key they access is produced by the adapter — proven non-vacuous by removing `typography_plan` and watching it fail |
| RC-10 | Web API job → `completed` | live job on the running stack |
| RC-11 | Job settings reach the pipeline | `tests/test_web_job_fields.py` |
| RC-12 | Stdlib-only CI suite | every new test checked under the simulated pytest-only environment below |
| N-1..N-5 | NVIDIA ladder semantics | `tests/test_nvidia_retry.py` stays green until Stage 11 rewrites it against the provider layer |

## Previous task (closed)
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
| Whisper `cuda` + `float16` crashes | Real runs on ctranslate2 4.8.2, 0 CUDA devices, no torch |
| `--whisper-device auto` | Resolves to cpu/int8; 5 segments, contract-valid |
| **A-007** | **CLOSED.** Live call exposed `400 unknown field guided_json`; fixed via `response_format`, re-verified: 2 clips, zero missing keys |
| Full CLI pipeline | Real 720x1280 clips rendered from a 72.9s source |
| Live AI selection | Picked 7.6-30.5s and 54.5-70.8s with titles, hashtags, account classification |
| Pexels B-roll | `download_pexels_broll` returned a 7.1MB clip |
| Web API job | upload -> job -> transcribe -> render -> `completed` |
| 21 job fields | `video_cq: 30`, `split_trigger: face` land over HTTP |
| Reuse bypass | Same request that failed now auto-enables and completes |
| Upload 250MB direct | HTTP 200 in 3.6s |
| Upload 300MB via Vite proxy | Browser: `205 MB / 300 MB`, 68%, 48 MB/s, ETA |
| Dashboard in English | Rendered in a browser |

### Still unverified
- **RC-8 diarization / split-screen.** Needs `pyannote.audio` + `torch` + an
  accepted HuggingFace model agreement. Unchanged.
- **`whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build.**
  Needs a GPU host; the branch logic around it is covered by injection.
- Everything else previously carried here — the container uid fix, the Vite
  timeout fix, the CUDA branch — is resolved above.

### Findings that changed the brief
- `reuse_job_id` is **LIVE**, not dead: `web/api/routes/jobs.py:67` validates it
  and `:83` pops it from the payload before the payload reaches
  `config_adapter`/`worker`, feeding `store.create_job(job_id=...)` to reuse a
  prior job directory. Correctly absent from those two modules.
- The dashboard sends **none** of the 21 (grep over all of
  `web/dashboard/src`). They are API-only fields today.
- `clipping/config.py` enforces **no numeric range** for any of the 21 — the
  `type=int/float` cast is the only check — so no `ge=`/`le=` bounds were
  mirrored. Three flags do carry `choices=` allow-lists; those became enums.

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
