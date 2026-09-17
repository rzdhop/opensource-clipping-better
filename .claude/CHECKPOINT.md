# CHECKPOINT

## In progress
- **Task:** Local-first refactor (plan: `~/.claude/plans/system-blueprint-mighty-sedgewick.md`)
- **Current phase:** IMPLEMENT
- **Current stage:** Stage 1 — lazy faster-whisper import
- **Next action:** Move `from faster_whisper import WhisperModel` into `transcribe_video()`
- **Open questions:** none

## Checkpoint commit
`3c72b75c684b9f5bc2469af7c983eb279fd14160` — clean tree, pre-refactor baseline.

## Tier-1 baseline
There is **no test suite and no CI in this repo** (`.github/` holds only
`FUNDING.yml`; no `tests/`, no `conftest.py`, no pytest config). There is no type
check and no lint configured. The Tier-1 baseline is therefore:

```
python -m compileall clipping web main.py                      # syntax
PYTHONIOENCODING=utf-8 python main.py --help                   # argparse builds, imports resolve
```

`PYTHONIOENCODING=utf-8` is required on Windows: the help text contains emoji and
the console defaults to cp1252, so a bare `--help` raises `UnicodeEncodeError`.
This is pre-existing and unrelated to this task.

Stage 2 of the plan establishes pytest; from that point Tier 1 becomes
`python -m pytest -q` plus the two commands above.

## Regression contract
Nothing on this list may break. Items marked UNVERIFIED have no covering test
and must be checked manually before the task closes.

| # | Behaviour | Proof |
|---|---|---|
| RC-1 | `data_segmen` shape consumed by `buat_file_ass` (`{start,end,words[{word,start,end}]}`, absolute seconds) | `tests/helpers.assert_valid_data_segmen` (from Stage 2) |
| RC-2 | `parse_youtube_json3_subs` output is byte-identical after helper extraction | `tests/test_json3_parser.py::test_json3_golden` (Stage 2) |
| RC-3 | Whisper transcription still works when no `--transcript` is given | UNVERIFIED — manual smoke |
| RC-4 | Karaoke word alignment in the burned-in `.ass` | UNVERIFIED — manual A/B vs a Whisper run |
| RC-5 | Gemini provider still reachable via `--ai-provider gemini` | `tests/test_analyze_dispatch.py` (Stage 6) |
| RC-6 | NVIDIA provider returns a valid clip list | `tests/test_nvidia_retry.py` (Stage 6) |
| RC-7 | Render layer (FFmpeg/OpenCV/YOLO) untouched — clips still render | UNVERIFIED — manual smoke |
| RC-8 | Diarization / split-screen still works on a non-yt-dlp source | UNVERIFIED — manual smoke |
| RC-9 | Story mode assembles | UNVERIFIED — manual smoke |
| RC-10 | Web API job reaches `completed` | `tests/test_web_config_adapter.py` (Stage 9) + manual |

## Unrelated changes present at checkpoint time
None — tree was clean at `3c72b75`.

## Known pre-existing breakage (not caused by this task)
- `run_upload.py:17` imports `youtube_uploader.safety`, which does not exist —
  that script raises `ModuleNotFoundError` on import today.
- `python main.py --help` raises `UnicodeEncodeError` on a cp1252 Windows console
  (emoji in help text). Workaround: `PYTHONIOENCODING=utf-8`.
- `gdown` is declared in `pyproject.toml` only, not `requirements.txt`, so
  `--hook-source <drive-url>` fails in every documented install path.
