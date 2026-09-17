# ASSUMPTIONS

## Unconfirmed
- **A-005** — The render layer is unaffected by this refactor. Reasoning: it reads
  `cfg.file_video_asli` (unchanged in name, now absolute and local) and
  `data_segmen` (contract pinned by `tests/helpers.assert_valid_data_segmen`).
  **Not verified end-to-end** — this machine has no cv2/ffmpeg/mediapipe, so no
  clip was rendered. See the deferred Tier-2 list in CHECKPOINT.md.
- **A-006** — VTT-derived karaoke timing is visually acceptable. Inline
  `<00:00:01.234>` tags are used verbatim where present, and even division
  otherwise (the same approximation the pre-existing JSON3 parser used). Needs a
  manual A/B against a Whisper run of the same video.

## Confirmed
- **A-001** — The transcript contract consumed by `studio/subtitles.buat_file_ass`
  is `{"start": float, "end": float, "words": [{"word","start","end"}]}` with no
  `"text"` key, and word-level timestamps are mandatory.
  *Confirmed by reading `clipping/studio/subtitles.py:172` and `:247`, which index
  `seg["words"]` with `[]` rather than `.get()`.*
- **A-002** — `openai` is an undeclared dependency: imported at
  `clipping/engine.py:901`, present in neither `requirements.txt` nor
  `pyproject.toml`. *Confirmed by grep over both manifests.*
- **A-003** — Only `studio/effects.py:86` and `studio/transitions.py:156` actually
  call yt-dlp inside `clipping/studio/`; the other 9 module-scope imports are dead.
  *Confirmed by grep for `YoutubeDL(`.*
- **A-004** — `-v` and `-t` are free as short flags; only `-u`, `-n`, `-r` are
  taken. *Confirmed by grep over `clipping/config.py`.*

## Invalidated
- (none)

## Notes
A-005 and A-006 are the two open risks at close-out. Both are verification gaps
rather than known defects, and both are listed with concrete commands in
CHECKPOINT.md.
