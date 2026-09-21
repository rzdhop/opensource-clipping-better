# CHECKPOINT

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
| `a154579` | S8 env samples, compose passthrough, README, DEC-016/017, A-009 |

**S6 and S7 were merged** into one commit: the warnings S6 adds depend on controls
S7 introduces (split-screen and its trigger), so two commits on the same file
could not have been reverted independently — the only reason to split them.

### Verified against a running stack (not just unit tests)
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

Running it for real found two bugs that no test could have caught.

| What | Result |
|---|---|
| **Default model was dead** | `deepseek-v4-flash-0731` hit EOL at 2026-09-21T08:00:00Z — **the same day**. Every default job failed with 410. Replaced with `nvidia/nemotron-3-super-120b-a12b` (DEC-018) |
| Retry classification, live | The 410 was correctly called fatal and **not** retried: one call, not three |
| NVIDIA path after the S3 refactor | Full CLI run: 55 segments, AI picked 15.2–32.8s and 61.0–86.7s with titles and BGM moods, **2 real clips rendered at 720x1280 h264**, first attempt |
| **Custom endpoint, first live run** | Failed all 3 attempts on `JSONDecodeError` — a reasoning model leaked a bare `[` before its own valid array. Fixed by salvaging the first balanced JSON value (DEC-019) |
| Custom endpoint after the fix | Same run succeeds on **attempt 1** and renders at 720x1280 |
| Fast preset, end to end | `--render-height 720` produced genuine 720x1280 output |

### Not verified
- ~~No live call through `openai_compat`~~ — **done**, and it found a real bug
  (DEC-019). Both providers now verified end to end against a live endpoint.
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
- `run_upload.py:17` imports `youtube_uploader.safety`, which does not exist.
  Excluded from the `compileall` CI job for that reason.
- `gdown` is declared in `pyproject.toml` only, so `--hook-source <drive-url>`
  fails in every documented install path.

## Follow-ups deliberately not done
- `hook_manager.py` (`--hook-source`) is now the only network fetch left in the
  CLI pipeline, which is inconsistent with local-first.
- 9 dead `from yt_dlp import YoutubeDL` imports remain in `clipping/studio/`.
