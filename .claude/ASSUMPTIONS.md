# ASSUMPTIONS

## Unconfirmed
- (none open)

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
