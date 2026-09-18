# CHECKPOINT

## In progress
- **Task:** Live progress / debug feed in the dashboard — tell the user which
  step is running, which AI provider+model is being called, and how long it has
  been stuck there.
- **Phase:** IMPLEMENT. Plan approved 2026-09-18 (all 5 stages, confined to
  `web/`; `clipping/` is not touched). Containers restarted after the
  fast-forward, so both now run `5bdd31c`.
- **Checkpoint commit:** `5bdd31c` (clean tree, `main`).
- **Tier-1 baseline at `5bdd31c`:** `python -m pytest -q` = **254 passed**,
  exit 0.
- **Session finding:** local `main` was 8 commits BEHIND `origin/main`
  (`242b1f6` vs `5bdd31c`) — the previous session pushed from elsewhere and the
  working tree never caught up. Fast-forwarded (`git merge --ff-only`). The
  docker containers started at 08:26 from the stale tree and must be restarted
  to pick the code up.
- **Docker verification (containers live this session):**
  - `d845413` container uid fix — **VERIFIED.** `osc-backend` runs
    `uid=1001 gid=1001` (host `.env` sets `DOCKER_UID/GID=1001`), `/app/uploads`
    and `/app/outputs` are owned `1001:1001`, a write probe inside the container
    succeeded, `HOME=/tmp`, `/tmp/Ultralytics` is 0777.
  - `9a9adc5` Vite timeout fix — **still unverified at runtime.** The new
    `vite.config.js` is on disk and inside the container, but the running vite
    process loaded the OLD config (started 08:26:48, file rewritten 08:29:32).
  - CUDA branch of the device resolver — **as verified as this host allows.**
    The branch logic is covered by injection/monkeypatch in
    `tests/test_device_resolution.py` (`test_auto_with_cuda_picks_cuda_and_float16`,
    `test_explicit_cuda_is_respected_when_available`,
    `test_detection_uses_ctranslate2_when_it_reports_a_device`). Only
    `whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build
    remains untestable here, and nothing short of a GPU box will close it.
- **Open questions:** none blocking.

### Stages (approved)
1. Capture the pipeline's own stdout/stderr into a per-job structured event
   feed. `web/api/{activity,store,models,worker}.py`. **Riskiest stage.**
2. `JobProgressEvent` gains `detail`, `provider`, `model`, `attempt`,
   `max_attempts`, `clip_index`, `clip_total`, `step_started_at`.
3. SSE carries the new events incrementally, with a heartbeat.
4. `JobDetail.jsx` "Live activity" panel: step + detail, provider/model chip,
   elapsed timers, clip sub-bar, live console.
5. `Dashboard.jsx` row detail + README/CHANGELOG.

### Verified against real services this session
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
- **Docker containers** — image build was still exporting layers at close of
  this stretch. The uid fix (`d845413`) has never run against a live daemon.
- **The CUDA branch** of the device resolver: this host is CPU-only.
- **Vite timeout fix** (`9a9adc5`): a 300MB upload succeeded through the proxy,
  but on fast loopback it never approached the 300s window that actually broke.
- RC-8 diarization / split-screen.

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
