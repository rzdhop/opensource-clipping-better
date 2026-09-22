# ASSUMPTIONS

## Unconfirmed
- **A-013** — Output language defaults to the transcript's language (reported by
  the hosted STT, else stopword detection); `output_language` overrides it.
  English titles/keywords/hashtags are still produced alongside. UNCONFIRMED.
- **A-014** — The default clip count stays 7 (DEC-021: no silent change). The
  three-pass analyzer removes the reason it mattered. UNCONFIRMED.
- **A-015** — Platform presets: `tiktok` 15–90 s (target 34), `reels` 15–90 (30),
  `shorts` 15–59 (45), `auto` 20–75 (40), `long` 60–179 (90); cuts snap to
  sentence boundaries. UNCONFIRMED.
- **A-016** — The full image (torch, pyannote, ultralytics, faster-whisper) stays
  the default build because every feature must survive; a slim build is opt-in
  via a build arg. UNCONFIRMED.
- **A-010** — NIM model ids in this project have a shelf life measured in weeks:
  **four** defaults have now died in about six weeks. The current pin is
  `deepseek-ai/deepseek-v4.1-flash`, chosen by live benchmark on 2026-09-21
  against the real Pass-A workload (1.3–2.9s, schema-valid) and defined once, in
  `registry.NVIDIA_DEFAULT_MODEL` (DEC-052). Re-pick with
  `tools/bench_llm.py --nim-shortlist`. Two traps are now proven rather than
  suspected: a model listed by `/v1/models` may answer 404 for a given account,
  and the fast candidates are reasoning models that return `content=null` or
  unparseable prose unless thinking is switched off. UNCONFIRMED (the shelf-life
  estimate; the measurements are facts).

- **A-009** — *(being revisited 2026-09-22: the human states they have or can
  obtain a Groq key, and Settings now has a field for it — DEC-057. Confirm
  on the next real run, then move this to Confirmed or Invalidated.)*
  Groq and Mistral do not reliably offer a usable free API key,
  despite their own documentation describing free tiers on 2026-09-21. This rests
  on the human's own attempt, not on a page we can cite, and it is the reason
  neither is recommended anywhere in the UI. It does not affect correctness:
  both are reachable through the generic `openai_compat` provider, which makes no
  claim about their pricing. Recheck before ever promoting either to a
  recommended provider. xAI is a separate and firmer case: its own pricing page
  confirms the free API tier ended in May 2025.

## Confirmed
- **A-012** — The phone-width overflow is fixable in CSS alone; no JSX change is
  needed. *Confirmed by measurement. The exploration flagged several inline
  `style={{ display: 'flex' }}` rows that no stylesheet can reach — chiefly the
  job header's action group at `JobDetail.jsx:255` — as probable blockers. They
  are not: once `.page-header` wraps, that group measures 222px and fits inside
  the 343px content box unchanged. Every route measured `scrollWidth ==
  clientWidth` at 375, 414 and 820px with the diff confined to `index.css`.*
- **A-011** — The pipeline's Python-level `print` output is enough to tell a user
  what is happening. *Confirmed against a live job on the running containers: the
  feed carried the transcript warning (34% of words dropped for backwards
  timestamps), the segment/word summary, the provider and model line, and the
  NVIDIA retry ladder including `attempt 1 failed | ValueError: NVIDIA returned
  an empty clip array`. Known gap: ffmpeg is a subprocess writing to the real
  file descriptors, so its output is not captured — documented in the README.*
- **A-007 — RESOLVED 2026-09-18, against the live API.**
  `deepseek-ai/deepseek-v4-flash-0731` exists and authenticates, but it
  **rejected** the `nvext.guided_json` the code was sending:
  `400 unknown field 'guided_json'`. So the assumption was wrong and every real
  analysis would have failed on the first attempt. Probing six mechanisms showed
  `response_format={"type":"json_schema"}` accepted; after switching to it a live
  call returned 2 clips with zero missing keys and
  `metadata.normalize_and_validate` accepted the result. See DEC-013.
- **A-010** — Whisper's device failure is a CTranslate2 property, not a torch
  one. *Confirmed: this machine has ctranslate2 4.8.2 with
  `get_cuda_device_count() == 0` and no torch at all, and reproduced both
  reported crashes; `torch.cuda.is_available()` would not have detected it.*
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
- **A-003 — CONFIRMED, and RESOLVED 2026-09-18 (`a269a8f`).** Only
  `studio/effects.py:86` and `studio/transitions.py:156` actually call yt-dlp
  inside `clipping/studio/`. *The call-site set was right; the count was not —
  there were **10** dead imports across 12 files carrying one, not 9. Re-verified
  by an AST pass rather than a grep: in each of the ten, `YoutubeDL` occurred
  exactly once, as the import itself. The ten are deleted; afterwards no module
  in `clipping/` uses the name unimported.*
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
- **A-010's test claim** (2026-09-22) — "`tests/test_config_cli.py` pins the
  string so the next retirement surfaces as a test failure rather than a
  production 410." It cannot, and did not. `google/gemma-4-31b-it` was never
  retired: it stayed in `/v1/models`, accepted requests, returned no 410 and no
  error — it simply answered nothing, for 120s, on an 8-token request. Every
  test on that string passed throughout. Replaced by DEC-056's liveness probe,
  which is the only thing that can catch this: a real request.

- (none)

## Notes
A-007 is closed. The container uid fix is verified against a live daemon
(2026-09-18). The CUDA branch of the device resolver is verified by injection —
`resolve_whisper_runtime` takes `cuda_available`, and three tests drive the
CUDA-true path — so only `whisper_cuda_available()` against a real CUDA-enabled
CTranslate2 build remains, which needs a GPU host and nothing less. Diarization /
split-screen (RC-8) is still unexercised.

## Notes on this round (2026-09-21)
A-013 (output language follows the transcript), A-014 (clip default stays 7) and
A-015 (platform presets) are all now **confirmed by a live run**: the analysis
produced French titles with English tags from a French video, and three clips
inside the tiktok window. A-016 (the full image stays the default) is unchanged
and untested — Stage 11 is where it would be.
- **A-017** — A 12-hour media-URL TTL is long enough that expiry-mid-playback is
  a tab left open overnight, and short enough that a URL pasted into a chat stops
  working the same day. Both halves are judgement, not measurement. The
  `onError` handler in JobDetail.jsx re-fetches once, so the overnight case
  recovers rather than stalling; `MEDIA_URL_TTL` moves it. UNCONFIRMED.
- **A-018** — Nobody relies on `/api/outputs/{id}/{file}` answering with
  `Content-Disposition: attachment` by default. It now answers `inline` unless
  `?download=1` is given. Only the dashboard and `docs/studio/` consume it, and
  `docs/studio/` cannot authenticate against this API at all. UNCONFIRMED.
