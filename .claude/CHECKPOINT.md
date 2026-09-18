# CHECKPOINT

## In progress
- **Task:** Live progress / debug feed in the dashboard. **COMPLETE.**
- **Phase:** closed out.
- **Checkpoint commit:** `5bdd31c` was the baseline. Stages:
  `58c07a5` activity feed, `e83c364` progress fields, `19fd3d7` SSE,
  `06fb8bc` the panel, `6c325df` list view + docs.
- **Tier-1:** `python -m pytest -q` = **311 passed** locally;
  **273 passed, 20 skipped** in a pytest-only venv matching CI (DEC-012);
  `compileall` clean.
- **Tier-2:** no E2E suite exists in this project (no playwright/cypress, no
  test script in `web/dashboard/package.json`), so Tier 2 is browser
  verification against the running containers — done, see below.
- **Session finding:** local `main` was 8 commits BEHIND `origin/main`
  (`242b1f6` vs `5bdd31c`) — the previous session pushed from elsewhere and this
  checkout never caught up. Fast-forwarded. The repo-local git identity was
  unset; set to the one the existing history uses.

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
- The dashboard scrolls sideways at 375px (`main.main-content` renders 427px).
  Pre-existing; recorded as a follow-up, deliberately not in this diff.

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
- `run_upload.py:17` imports `youtube_uploader.safety`, which does not exist.
  Excluded from the `compileall` CI job for that reason.
- `gdown` is declared in `pyproject.toml` only, so `--hook-source <drive-url>`
  fails in every documented install path.

## Follow-ups deliberately not done
- `hook_manager.py` (`--hook-source`) is now the only network fetch left in the
  CLI pipeline, which is inconsistent with local-first.
- 9 dead `from yt_dlp import YoutubeDL` imports remain in `clipping/studio/`.
