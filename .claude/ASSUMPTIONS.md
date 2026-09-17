# ASSUMPTIONS

## Unconfirmed
- **A-007** — `deepseek-ai/deepseek-v4-flash-0731` honours the `nvext.guided_json`
  schema as well as the retired `deepseek-v4-pro` did. The model is confirmed
  live (probe returns 401 auth-required, not 410) and is the same v4 family, but
  it has **not** been exercised against the real API — no valid `NVIDIA_API_KEY`
  was available. This is the one substantive open risk.

## Confirmed
- **A-008** — The 21 undeclared `JobCreateRequest` fields are API-only: no part
  of the dashboard sends them. *Confirmed by grep for all 21 names over the whole
  of `web/dashboard/src` — zero matches. The task brief stated the dashboard sent
  "several of them"; it does not.*
- **A-009** — `reuse_job_id` is live, not dead. *Confirmed at
  `web/api/routes/jobs.py:67` (validation) and `:83` (popped from the payload and
  passed to `store.create_job(job_id=...)` to reuse a prior job directory). It is
  absent from `config_adapter`/`worker` by design, because it is popped before the
  payload reaches them.*
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
- **A-005** — The render layer is unaffected by this refactor. *Confirmed: a real
  1080x1920 h264+aac clip plus thumbnail rendered end-to-end from a local mp4 +
  vtt, after fixing a pre-existing Windows path-escaping bug that blocked all
  subtitle burn-in (see DEC-008).*
- **A-006** — VTT-derived karaoke timing is correct. *Confirmed: regenerated the
  burned-in ASS and compared every Dialogue timing back to the source VTT — 0
  word mismatches across 44 words, every delta <=0.010s (ASS centisecond
  resolution). The single 0.833s outlier is a word starting before the clip cut,
  correctly clamped to the clip start.*

## Invalidated
- (none)

## Notes
A-007 is the only substantive open risk at close-out: the replacement NIM model
is confirmed to exist but has not been called with a real key.
