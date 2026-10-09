# DECISIONS — rules in force

One line per rule: `DEC-NNN | area | rule`. A changed rule is rewritten in place under a new number; a dead one is
deleted. The full history with context (DEC-001…DEC-332) is in git: `git show 9f0ce43:.claude/DECISIONS.md`.
Next free id: **DEC-336**.

## AI stories (showrunner + skill)
DEC-319 | showrunner | The engine is universe-agnostic (`showrunner/`, image `ghcr.io/rzdhop/showrunner-worker`). A story's universe lives only in its `01-universe.md`, agreed in chat before any picture; nothing hard-wires fruit.
DEC-320 | showrunner | LTX-2.5 I2V makes picture and voice together from the prompt (`ltx25_i2v_speech`). A clip holds 1–3 speakers, multi-speaker preferred; a shot still failing after three rounds is split one shot per speaker. No TTS→A2V or ID-LoRA path.
DEC-321 | showrunner | `verify_take` checks every clip on the host CPU (ffmpeg + faster-whisper large-v3 int8, no script hint): ok when ≥ 75 % of each line is heard, in order, the last word ending ≥ 0.1 s before the end. Its timings drive trims and subtitles.
DEC-322 | showrunner | `stories/` in git holds markdown, JSON and images; clips, finals and audio (locked voices included) are gitignored and stay on the host — back them up.
DEC-323 | showrunner | Each character has one locked `voice_ref.wav`. A checked clip is converted to its speakers' locked voices (`vc_clip`, Chatterbox VC): multi-speaker clips are cut at the pauses, each part converted, rejoined at exact length on the untouched picture.
DEC-324 | showrunner | The MCP server (FastMCP, streamable HTTP `/mcp`) has its own `SHOWRUNNER_MCP_*` settings and OAuth state, secret `MCP_TOKEN`. GPU tools are submit-then-fetch, journaled at submit. `RUNPOD_COMFY_ENDPOINT_ID` (the old app endpoint) is refused.
DEC-325 | deploy | Unit `showrunner-mcp` runs from `.venv` on port 8787 at the Tailscale Funnel root, no EnvironmentFile (the server reads `.env`). Never install packages into `.venv`.
DEC-316 | showrunner | The cost ledger (`costs.jsonl`) bills RunPod execution time only; queue and cold-start delay are recorded beside it, never priced.
DEC-327 | showrunner | A character's voice comes from an approved, checked take (usually an ep00 casting shot): `voice_ref_from_take` cuts the speaker's line (2–10 s, mono 24 kHz) at the pauses and locks `voice_ref.wav` + `voice_ref.json` naming its source.
DEC-328 | showrunner | Claude writes every prompt and sends it as `values.prompt` of `comfy_submit`; the server writes none. Prompts are kept in `shots.json` and the job journal; `PROMPTS.md` holds the patterns that worked.
DEC-317 | skill | Every shot is a real video clip: never a still, a Ken Burns or a slowed clip. A failed take is redone with a new seed and a note, never filled.
DEC-330 | skill | Claude proposes complete choices and Rida corrects. His six gates: concept, universe, cast sheets, finished cast (pictures + locked voices), script, whole episode; then "next episode?". Between gates Claude produces and locks ("Claude's pick: <why>"), says the cost once, stops only past 2× the estimate.
DEC-307 | skill | On pictures Rida approves what he wants; Claude states the risk once in plain words, then does what he chose.
DEC-331 | skill | One skill `.claude/skills/rzdhop-story/` (SKILL.md, steps/*.md read before each step, PROMPTS.md); `build_skills.py` checks and zips it for claude.ai.
DEC-334 | skill | The look that holds attention, every story and universe (Rida, 2026-10-09): busy sets at three depths, named background people of the same universe (silent, in the back; `## Extras` in the universe and each plate), strong but believable emotion with one physical action per shot, ≈ 7 clips in 10 with 2–3 characters, short readable text allowed, placement left to right matching `characters`.
DEC-335 | showrunner | The camera holds still inside clips; `assemble_episode` adds the energy by itself: each next line punches in (1.25× crop toward the speaker, cut mid-silence, top kept), a one-character clip ≥ 3 s once at 45 % (1.15×); pieces < 0.8 s merged; timings unchanged. shots.json `positions` overrides sides, `punch_in: false` turns it off (per shot or episode) (2026-10-09).
DEC-332 | clips-engine | The old in-app AI Story is deleted; the app is Clips only (plus the CLI Story Clip recipe). Stories are made only through the chat and the showrunner.
D4 | skill | Each story is French or English (in `00-brief.md`); spoken lines use it, story files and prompts stay in English.
D7 | showrunner | Lines are quoted in the prompt and voices described; at cfg 1.0 the negative prompt is ignored, so prompts never name what is unwanted. Keyframes are drawn in the shot's framing; the camera holds still on the speaker.
D2 | deploy | The video endpoint is an L40S 48 GB, one GPU per worker, LTX-2.5 int8 distilled stack (+ Gemma 4 int8) on a network volume ≥ 150 GB (`showrunner/worker/ENDPOINT.md`).
DEC-314 | deploy | Chatterbox runs in our worker image `docker/worker-comfyui-tts` (pinned Fill-ChatterBox node pack, weights on the volume through a symlink, handler patched to return audio); the showrunner worker image extends it.

## Clips — engine
DEC-001 | clips-engine | yt-dlp stays a declared dependency: studio asset fetching, youtube_tracker metadata, server-side fetch (`clipping/ingest/downloader.py`).
DEC-005 | clips-engine | `--video` is required unless `--story-mode`; `--transcript` is optional (else the STT chain transcribes); `--no-whisper` requires `--transcript`.
DEC-006 | clips-engine | The CLI Story Clip recipe accepts local sources only (`sources.json` "platform": "local").
DEC-095 | clips-engine | `--story-mode` is labelled "Story Clip (assembly)" everywhere; its end-to-end path is unverified.
DEC-008 | clips-engine | `escape_ffmpeg_filter_value` (studio/helpers.py) carries the Windows path fix; POSIX output stays byte-identical.
DEC-014 | clips-engine | `clipping/device.py` resolves the Whisper device from `ctranslate2.get_cuda_device_count()` (torch only as fallback); default "auto".
DEC-022 | clips-engine | A Whisper/STT transcript is saved atomically as `transcript.vtt` in the job's outputs and reused by reruns (dedupe off, no offset; best-effort).
DEC-031 | clips-engine | `--dry-run-analysis` writes `gemini_response.json` + `metadata_preview.json` and stops; `--load-gemini-json` renders from them. `clipping.studio` is imported lazily.
DEC-041 | clips-engine | The ingest module is `clipping/ingest/downloader.py`.
DEC-049 | clips-engine | Fonts are validated by the family name they declare, not by file size; a mismatch raises.
DEC-050 | clips-engine | The hook glitch is off by default (`--hook-glitch`; `--no-hook` wins), built on mid-grey noise, cached under `GLITCH_RECIPE_VERSION`.
DEC-068 | clips-engine | The single-request analysis path is gone; `--ai-provider openai_compat` becomes a one-link `custom/<model>` chain (`apply_openai_compat_alias`, shared by CLI and web).
DEC-069 | clips-engine | The karaoke highlight colour is configurable (`KARAOKE_HIGHLIGHT_COLOR`, `--karaoke-color`), validated as `&HBBGGRR&`; the base colour is fixed.
DEC-075 | clips-engine | Cancelling is cooperative (`cfg.cancel_token` checked before every spending step; `Cancelled` derives from BaseException); `web/api/children.py` kills a job's subprocesses.
DEC-079 | clips-engine | `clipping/studio` is a real package imported lazily; tests import it under the `render_stack_stubbed` fixture.
DEC-080 | clips-engine | The encoder listing is read once per process; encoder probe answers are cached per argument tuple for 600 s.
DEC-081 | clips-engine | The watermark renderer is cached by its settings + the image's mtime and size, capped at 32 entries.
DEC-082 | clips-engine | `--loudnorm` is opt-in: two-pass EBU R128 (I −14, TP −1.5, LRA 11) as each clip's last write, Story Clip outputs too; video copied, best-effort.
DEC-288 | clips-engine | B-roll comes from `clipping/stock` (stdlib): `BROLL_SOURCES` order (default local,pexels,pixabay), keyless skipped, local folder under `/app/broll`, Pixabay cached 24 h, credits in the manifest (`broll_credits`).

## Clips — analysis
DEC-027 | clips-analysis | Three passes over sentence beats: windowed candidate scan (A), global re-rank (B), per-clip metadata (C). Models answer with beat ids, never timestamps.
DEC-028 | clips-analysis | `snap.py` owns every timing decision (sentence bounds, duration window, lead-in/tail, overlap); it grows forward, trims from the start only, drops unfit candidates with a reason.
DEC-029 | clips-analysis | The model gets English keys; `analysis/adapter.py` maps them to the legacy render keys. An AST test asserts every key the render layer reads is produced.
DEC-030 | clips-analysis | Unused fields are not requested; typography_plan, broll_list, keep_segments and bgm_mood are derived in Python; hook_v2 is model-informed with a heuristic fallback.
DEC-020 | clips-analysis | Analysis has an overall time budget (900 s default); a request is refused beforehand only if it could outlast it.
DEC-054 | clips-analysis | The budget splits run → pass → window: pass A gets 0.7, each window remaining/windows_left with a one-request floor against the slowest keyed link (+1 s margin, DEC-059); unused time returns to the pool.
DEC-021 | clips-analysis | The requested clip count is never silently shrunk; the log says why fewer clips came out.
DEC-055 | clips-analysis | When no window answered, the error names the provider failure, never the transcript.
DEC-060 | clips-analysis | A `Span` carries the candidate's `gist` and `kind` through `snap()`; nothing survives snapping through a lookup keyed on `b0`/`b1`.
DEC-061 | clips-analysis | Pass B sees the first 15 words of each candidate; `RANKED_SCHEMA` requires a one-word `topic`; `_enforce_variety` demotes repeats, backfilling.
DEC-062 | clips-analysis | Prompts put the beats before the numbered rules, use a four-band `SCORING` rubric and a `video_context` preface; `PROMPT_VERSION` is bumped on any wording change.
DEC-063 | clips-analysis | Pass C returns 1–3 `hook_beats` (heuristic fallback). Passes A/B at temperature 0.2, C at 0.5.
DEC-064 | clips-analysis | Every prompt lives in stdlib-only `prompts.py`, in English, the output language a parameter.
DEC-065 | clips-analysis | A span starting mid-sentence is dropped (lower case and previous beat unpunctuated); off below 20 % punctuated beats, never removes every span.
DEC-066 | clips-analysis | `analysis/cache.py` caches pass-A results per window (sha256 of text + salt of prompt version, chain, preset, max candidates); bounded, atomic, never a gate.
DEC-070 | clips-analysis | Each run writes `outputs/<job>/analysis_trace.json` (versioned, atomic, best-effort), failures included.
DEC-071 | clips-analysis | `--analysis-workers` runs pass-A windows in batches (default 1, cap 3); ordering and logs decided on the main thread.

## Clips — LLM / STT providers
DEC-003 | providers | Fail fast and loud: keys validated up front, errors propagate, never a silent fallback to a provider not in the chain.
DEC-023 | providers | `LLM_CHAIN` / `--llm-chain` is an ordered list of `provider/model` links; every hop printed, keyless links skipped with a message, providers absent from the chain never contacted.
DEC-088 | providers | Default chain groq → gemini → openrouter (paid) → mistral → nvidia, built only from the named default-model constants (DEC-087); `Provider.free_tier` marks billed providers "(paid)".
DEC-025 | providers | Every client has `max_retries=0` and an explicit per-provider timeout; the `llm.py` ladder is the only retry policy. `registry.effective_timeout` is the single timeout source (DEC-053).
DEC-026 | providers | `complete_json` walks json_schema → json_object → prompt-only, caching the working level per (provider, model) after a reply parses.
DEC-045 | providers | When `json.loads` fails, `jsonx` salvages the first balanced JSON value; it must still pass every shape check.
DEC-089 | providers | A model-unavailable error retries the provider's next fallback model on the same key; the working model is remembered per key.
DEC-032 | providers | Transcription uses `STT_CHAIN` (default groq whisper-large-v3-turbo, then mistral voxtral); local faster-whisper only when named. Audio is 16 kHz mono FLAC, split at silences only over the upload cap (DEC-033).
DEC-042 | providers | A generic OpenAI-compatible endpoint uses `OPENAI_COMPAT_*` (never `OPENAI_API_KEY`), base URL, key and model required up front.
DEC-056 | providers | The chain preflight probes liveness before anything expensive (per-provider probe timeouts, DEC-072; a real pass-A work probe when a transcript exists, DEC-067); the job fails only if nothing answers.
DEC-073 | providers | `chain_readiness` refuses a chain whose free primaries are all keyless while a slow link has a key (CLI, POST /api/jobs, worker); override `allow_slow_chain`.
DEC-058 | providers | The NIM default is `registry.NVIDIA_DEFAULT_MODEL` (nemotron-3.5-lightning), chosen by real candidates (`bench_llm --nim-shortlist`); thinking off for `_NIM_REASONING_FAMILIES` and nemotron-3 (DEC-224).
DEC-222 | providers | `gemini` reads only `GOOGLE_API_KEY`; `gemini-paid` (DEC-273, never in a default chain) reads only `GEMINI_PAID_API_KEY`.
DEC-284 | providers | Claude is an optional link (`anthropic/claude-sonnet-5-5`, Opus by name, `@effort` suffix) through `anthropic_llm.py`: no SDK retries, refusal fatal, free `models.retrieve` probe, never in a default chain.
DEC-195 | providers | `transport._OPENER` drops credential headers on any redirect leaving the original scheme/host/port; STT uploads and Pexels search go through it too (DEC-196).

## Clips — web app
DEC-173 | web-api | Auth is on only when `API_TOKEN` is set (and `DISABLE_AUTH` is not); open mode still refuses cross-site writes (403); `DOMAIN` without a token refuses to start. Every router carries `require_token`; `/api/health` is open (DEC-037).
DEC-048 | web-api | With auth on, `/api/outputs/{job}/{file}` also accepts expiring HMAC `?exp=&sig=` URLs; in open mode URLs are plain paths.
DEC-009 | web-api | `JobCreateRequest` mirrors the CLI's choices as enums and adds no bounds the CLI lacks. "Default on unless the client said so" tests `req.model_fields_set` (DEC-015).
DEC-014b | web-api | Job progress comes from teeing stdout/stderr per worker thread (`activity.py`); the feed is a 500-entry ring buffer, persisted at most once a second.
DEC-035 | web-api | `fail_stale_jobs()` marks non-terminal jobs failed at startup; retired `story_step` records are skipped on load.
DEC-036 | web-api | Manifest keys the worker reads must match what the render layer writes (tested); dead keys are deleted.
DEC-039 | web-api | Server-side download is best-effort (`ENABLE_SERVER_FETCH`, default on); a refusal parks the job in `needs_upload`, resumed by POST `/api/jobs/{id}/source`. `tools/rzclips-fetch.py` (stdlib) is the reliable path (DEC-040).
DEC-047 | web-api | Settings persist to `data/settings.json` (allow-list `PERSISTED_KEYS`, atomic, chmod 0600, loaded in lifespan); LLM_CHAIN is never set by Settings (DEC-085).
DEC-074 | web-api | POST `/api/settings/test-chain` runs off the worker pool, one at a time (409), ≤ 300 s (504); it sends every keyed link the real pass-A request and returns ready/floor_only/blocked/dead (DEC-090).
DEC-076 | web-api | CANCELLED is terminal; cancelling a finished job or rerunning a running one answers 409.
DEC-077 | web-api | Deleting a job removes only its own plain files in outputs/ and uploads no other job uses; a running job is cancelled first (202).
DEC-078 | web-api | `MAX_QUEUED_JOBS` (default 20, 0 = unlimited) caps the queue: 429.
DEC-111 | web-api | `RESERVED_OUTPUT_NAMES` (stories, _chain_test, showrunner-mcp, mcp, stories.json, jobs.json) are refused as `reuse_job_id` and never removed by a job delete.

## Clips — dashboard
DEC-084 | dashboard | The API-served React dashboard is the only UI (`/clips/*`, `/settings`; old paths redirect, DEC-094); built `dist/` served at `/` after the routers (DEC-038).
DEC-253 | dashboard | Own kit in `src/ui`, tokens in `index.css`, lucide-react pinned 1.51.0 imported only through `src/ui/icons.js`; no other component library, no TypeScript.
DEC-016 | dashboard | Shrink rules (min-width 0, flex-wrap, overflow-wrap) live in base declarations; raw job strings use `overflow-wrap: anywhere` (DEC-017).
DEC-057 | dashboard | Every persisted secret has a Settings input and a `_set` badge (guarded by text tests).
DEC-189 | dashboard | "Sign out" shows only while a token is stored; an open server never shows the Login screen.

## Tests, CI, deploy
DEC-012 | ci-tests | CI installs only pytest + apt ffmpeg (ubuntu-24.04); tests pass without pydantic or the render stack (ast/text reads, importorskip only for model-level tests).
DEC-156 | ci-tests | `test_render_layer_guard.py` checks `clipping/studio/**` and `clipping/story/**` byte-for-byte against `render_layer_sha256.json`; an intended change re-records it.
DEC-278 | ci-tests | Locally, run what a change touches (new/edited tests, tests naming changed modules, the area's guards) with `-n 4`; tests never wait on real wall-clock time (DEC-176). CI runs the full suite.
DEC-192 | ci-tests | Each fix gets one fail-first test plus a guard that the working path still passes.
DEC-086 | ci-tests | The branding guard forbids `opensource-clipping` outside upstream attribution (the repo slug `opensource-clipping-better` is allowed).
DEC-083 | deploy | `.env.example` is the only env template; `pyproject.toml` mirrors `requirements.txt` pins verbatim (tested, DEC-297).
DEC-093 | deploy | Product "rzdhop AI" (distribution `rzdhop-ai`); the package stays `clipping`; scripts `rzclips` and `clipping`.
DEC-011 | deploy | The backend container runs as `${DOCKER_UID:-1000}:${DOCKER_GID:-1000}` with HOME=/tmp.
DEC-263 | deploy | The repo is bind-mounted into the app container: merge to main and rebuild only when no job is running.
DEC-018 | uploaders | The YouTube upload safety guardrails stay removed (restore from `git show 5bf93d5:youtube_uploader/safety.py`).

## Records
DEC-333 | records | `CLAUDE.md` is the shared context; `.claude/` files hold only what is true now (rules in force, open questions, current state). History lives in git (2026-10-09).
