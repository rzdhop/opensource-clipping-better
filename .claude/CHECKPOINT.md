# CHECKPOINT

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

## In progress
- **Task:** The clips render but the app cannot show them — the media viewer
  plays nothing, Download saves a `.json`, direct URLs say "file not available".
  Then land everything on `main`.
  Plan approved by the human:
  `/home/ubuntu/.claude/plans/i-want-the-code-breezy-starlight.md` (11 stages;
  read it before resuming — it carries the root cause and the design).
- **Phase:** CHECKPOINT done → next is Stage 1 (merge `origin/main`).
- **Current stage:** Stage 0 complete. **Next action:** `git merge origin/main`,
  resolve 11 conflicts with this branch winning every genuine clash.
- **Open questions:** none. The human answered all three: merge and keep both
  (this branch wins conflicts); access is **tailnet, plain HTTP, several
  devices**; and all three adjacent log defects are in scope (font, thumbnails
  + `.srt`, dead glitch URL).
- **Root cause of the reported bug:** `c53949b` put `Depends(require_token)` on
  the whole files router (`web/api/routes/files.py:15`) and the token is
  deliberately header-only (`web/api/auth.py:88-105`). But
  `JobDetail.jsx:312` (`<video src>`) and `:321` (`<a href download>`) are
  browser requests, which cannot carry a header. Both get
  `401 {"detail":"Missing or invalid API token."}`; because that body is
  `application/json`, the `download` attribute saves it and the browser
  rewrites the extension. **That JSON file IS the 401.** The clips on disk are
  correct — job `2773bd83c7b6` has 7 `.mp4` + 7 `.srt` + 7 `.jpg`.
- **Fix chosen:** HMAC-signed, expiring, per-file media URLs
  (`?exp=&sig=`), accepted without a header. A session cookie was rejected
  because the deployment is plain HTTP on a tailnet IP, where a `Secure`
  cookie is **silently dropped** — login would appear to work and then 401
  everything with no error anywhere. See DEC-025.
- **Branch:** `feature/rzdhop-clips-rearchitecture`.
- **Checkpoint commit:** `233b860` — clean tree. Roll back here.
- **Tier-1 baseline at `233b860`:** pytest **1008 passed, 0 failed** (5.3s).
  Run with `PYTHONPYCACHEPREFIX` set — see the root `__pycache__` note below.
- **Where main stands:** local `main` is stale at `f8ad8b4` (the merge-base).
  `origin/main` is `d5c502a`, 13 commits past it (persistent Settings, the
  `openai_compat` provider, the reasoning-model JSON rescue); this branch is 16
  past it. They conflict in 11 files.
- **One merge hunk checked because it looked like a trap and is not:**
  `NVIDIA_MODEL`. `origin/main` moved it to `nvidia/nemotron-3-super-120b-a12b`
  after two DeepSeek models died; this branch has `google/gemma-4-31b-it`,
  which is what the human's 18:12 run on 2026-09-21 actually succeeded with
  (7 clips). Branch wins, and it is the better value, not a regression.

### Regression contract for this task
| # | Must keep working | Proven by |
|---|---|---|
| MC-1 | Every non-media route still refuses an unauthenticated request | `tests/test_auth_token.py` green **unchanged**, incl. `POST /api/shutdown`, `/api/jobs`, `/api/settings`, `/api/upload` |
| MC-2 | A media signature is not a general credential | new negative battery: a valid clip signature pasted onto `/api/jobs`, `/api/settings`, `/api/upload`, `/api/shutdown` still 401s |
| MC-3 | The outputs directory listing stays private | `tests/test_auth_token.py:271` (`GET /api/outputs/somejob` → 401) stays valid **by construction**: it has no `filename` path param, so it cannot satisfy the signature path |
| MC-4 | The traversal guard still refuses | `tests/test_auth_token.py:309-360`; plus a new test that a valid signature does not let a traversal attempt through |
| MC-5 | The manifest/worker contract stays exact | `tests/test_manifest_fields.py` |
| MC-6 | The dashboard mount stays the last route | `test_the_dashboard_mount_is_the_last_route_registered` |
| MC-7 | The token never travels in a URL | `test_the_token_never_travels_in_a_query_string`, plus a new sibling: a minted media URL never contains the token as a substring |
| MC-8 | `outputs/jobs.json` is never rewritten by this change | new fields are derived on read; verified by mtime |

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
