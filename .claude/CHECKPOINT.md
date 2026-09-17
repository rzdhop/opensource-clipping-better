# CHECKPOINT

## In progress
- **Task:** Local-first refactor — **COMPLETE** (all 11 stages committed)
- **Current phase:** DOCUMENT / close-out
- **Next action:** Manual Tier-2 verification on a machine with the render stack
  installed (see *Deferred verification* below). Nothing is blocked on code.
- **Open questions:** none

## Checkpoint commit
Branch `refactor/local-first-engine`, head `b60fc4b`.
Baseline before the work: `3c72b75` (clean tree, `main`).

Stage commits, oldest first:

| Commit | Stage |
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

## Tier-1 baseline
Now backed by a real suite (there was none at `3c72b75`):

```
python -m pytest -q                                            # 165 tests
python -m compileall -q clipping web main.py run_fb_upload.py youtube_uploader youtube_tracker
PYTHONIOENCODING=utf-8 python main.py --help
```

All green at `b60fc4b`. CI runs the first two on every push.

`PYTHONIOENCODING=utf-8` is required on Windows: the help text contains emoji and
the console defaults to cp1252, so a bare `--help` raises `UnicodeEncodeError`.
Pre-existing and unrelated to this task.

## Regression contract

| # | Behaviour | Proof | Status |
|---|---|---|---|
| RC-1 | `data_segmen` shape (`{start,end,words[{word,start,end}]}`, absolute seconds) | `tests/helpers.assert_valid_data_segmen`, asserted across every parser and fixture | ✅ verified |
| RC-2 | JSON3 parser byte-identical after the shared-helper extraction | `test_json3_parser.py::test_json3_golden` | ✅ verified |
| RC-3 | Whisper still runs when no `--transcript` is given | `test_transcript_dispatch.py::test_falls_back_to_whisper_without_transcript` (call args asserted; real inference not exercised) | ⚠️ partially — see below |
| RC-4 | Karaoke word alignment in the burned-in `.ass` | — | ❌ UNVERIFIED — needs manual A/B |
| RC-5 | Gemini reachable via `--ai-provider gemini` | `test_nvidia_retry.py::test_dispatch_routes_to_gemini` | ✅ verified |
| RC-6 | NVIDIA returns a valid clip list, retries, fails loudly | `test_nvidia_retry.py` (24 cases, fake client) | ✅ verified (no live API call) |
| RC-7 | Render layer untouched — clips still render | — | ❌ UNVERIFIED — needs the render stack |
| RC-8 | Diarization / split-screen on a non-yt-dlp source | `test_config_cli.py::test_derive_audio_path_*` covers the path bug only | ❌ UNVERIFIED end-to-end |
| RC-9 | Story mode assembles | `test_story_loader.py` covers schema only | ❌ UNVERIFIED end-to-end |
| RC-10 | Web API job reaches `completed` | `test_web_config_adapter.py` covers the adapter only | ❌ UNVERIFIED end-to-end |

## Deferred verification (Tier 2) — IMPORTANT

**This machine does not have the render stack installed.** `cv2`, `mediapipe`,
`ultralytics`, `faster-whisper`, `torch`, `pyannote.audio`, `google-genai` and
ffmpeg are all absent, so **no clip was ever rendered during this work**. What
was verified is everything up to and including `run_pipeline`'s lazy import of
`studio`; past that point the process stops with `ModuleNotFoundError: cv2`.

Before merging, run on a machine with the full environment:

```bash
pip install -r requirements.txt

yt-dlp -f "bv*[vcodec!*=av01]+ba/b" --write-auto-subs --sub-format vtt \
       --convert-subs vtt -o "sample.%(ext)s" "<url>"

# 1. The headline path: local files, no network, no Whisper
python main.py --video sample.mp4 --transcript sample.en.vtt --clips 1

# 2. RC-4: the one thing no unit test can prove. Compare the karaoke word
#    alignment of the .ass above against a Whisper run of the same video.
python main.py --video sample.mp4 --clips 1        # RC-3 + RC-4 reference

# 3. RC-8: diarization now extracts audio from an arbitrary container
python main.py --video sample.mkv --transcript sample.vtt --split-screen

# 4. RC-9 / RC-10
python main.py --story-mode --sources-json sources.json
uvicorn web.api.app:app    # upload mp4 + vtt, POST /api/jobs, poll to completed
```

The strongest proof of the bypass is step 1 in a venv with **`faster-whisper`
uninstalled**: it should succeed.

## Unrelated changes present at checkpoint time
None — the tree was clean at `3c72b75` and every commit on this branch belongs
to this task.

## Known pre-existing breakage (not caused by this task)
- `run_upload.py:17` imports `youtube_uploader.safety`, which does not exist, so
  that script raises `ModuleNotFoundError` on import. Predates this work and is
  excluded from the `compileall` CI job for that reason.
- `python main.py --help` raises `UnicodeEncodeError` on a cp1252 Windows
  console. Workaround: `PYTHONIOENCODING=utf-8`.
- `gdown` is declared in `pyproject.toml` only, not `requirements.txt`, so
  `--hook-source <drive-url>` fails in every documented install path.

## Follow-ups deliberately not done
- `hook_manager.py` (`--hook-source`) still fetches over the network via
  `gdown`/`requests`. It is now the only remaining fetch in the CLI pipeline and
  is inconsistent with local-first; restricting it to local paths would also let
  `gdown` be dropped.
- 9 dead `from yt_dlp import YoutubeDL` imports remain in `clipping/studio/`
  (only `effects.py` and `transitions.py` actually use it). Removing them would
  measurably speed startup, but it touches the render layer this refactor
  promised to leave alone.
