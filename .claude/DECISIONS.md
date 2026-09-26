# DECISIONS

Append-only. IDs are never renumbered.

## DEC-001 — Purge yt-dlp from ingestion only
**Context.** `yt-dlp` is used by `engine.download_video()` (ingestion), by
`studio/effects.py:86` (glitch asset), `studio/transitions.py:156` (transition
pool) and `youtube_tracker/youtube_fetcher.py` (metadata only, `skip_download=True`).
A full purge would break three working features unrelated to the ingestion problem.
**Decision.** Delete the ingestion layer; keep the dependency declared.
**Consequence.** `yt-dlp` stays in both manifests with a comment naming the two
real call sites. The main pipeline is local-first; asset fetching is not.

## DEC-002 — NVIDIA NIM becomes the default provider; Gemini is kept
**Context.** The brief asks to "gut the current API routing" in favour of NVIDIA.
`analyze_with_nvidia()` already exists and is complete; `voiceover.py` has an
independent Gemini client for TTS commentary.
**Decision.** Flip the `AI_PROVIDER` default to `nvidia`; keep Gemini reachable
via `--ai-provider gemini`.
**Consequence.** Lowest-risk, fully reversible. `google-genai` stays a dependency.

## DEC-003 — No cross-provider fallback
**Context.** `analyze_with_ai` (`engine.py:1115`) catches bare `Exception` and
silently falls back to Gemini — which is how the undeclared `openai` dependency
(A-002) stayed invisible.
**Decision.** Fail fast and loud. Validate the selected provider's key up front;
let provider exceptions propagate.
**Consequence.** Breaking change. A user who chose NVIDIA is no longer silently
billed on Gemini.

## DEC-004 — Keep `deepseek-ai/deepseek-v4-pro` as the NIM default
**SUPERSEDED by DEC-007** — that model reached end of life on 2026-08-07.
**Context.** The brief specifies `meta/llama-3.1-70b-instruct`.
`metadata.py:136-141` already carries a DeepSeek-shaped output fixup, indicating
DeepSeek is the model actually validated against this prompt.
**Decision.** Keep `deepseek-v4-pro`; document llama-3.1-70b as an alternative.
**Consequence.** Diverges from the literal brief; confirmed with the human.

## DEC-005 — `--transcript` is optional, not mandatory
**Context.** The brief implies mandatory local transcripts. The web upload path
and story mode both still legitimately need in-process Whisper, and forcing an
external Whisper run on a user who only has an mp4 is a regression.
**Decision.** `--video` mandatory (outside story mode); `--transcript` optional
with a Whisper fallback; `--no-whisper` turns the fallback into a hard error.
**Consequence.** A loud banner distinguishes the two paths so the slow path is
never taken silently.

## DEC-006 — Adapt story mode and the web API rather than delete them
**Context.** Both call `engine.download_video()`. Story mode already supports
`platform: "local"`; the web API already has an upload path.
**Decision.** Restrict both to their existing local paths.
**Consequence.** `sources.json` schema becomes local-only — a breaking change for
existing URL-based recipes.

## DEC-007 — NIM default moves to `deepseek-ai/deepseek-v4-flash-0731`
**Context.** End-to-end verification against the live API returned
`410 Gone: deepseek-ai/deepseek-v4-pro has reached its end of life on
2026-08-07`. The alternative named in the original brief,
`meta/llama-3.1-70b-instruct`, is retired too (410, EOL 2026-08-2x). Both
candidates considered in DEC-004 are therefore non-functional.
Querying `https://integrate.api.nvidia.com/v1/models` shows
`deepseek-ai/deepseek-v4-flash-0731` live (probe returns 401 auth-required, not
410), and it is the same DeepSeek v4 family, so the DeepSeek-specific output
fixup at `metadata.py:136-141` still applies.
**Decision.** Default to `deepseek-ai/deepseek-v4-flash-0731`.
**Consequence.** Preserves DEC-004's intent (stay on DeepSeek v4) with the
smallest possible deviation. The default is pinned by a test so the next
retirement surfaces as a test failure rather than a production 410. NOTE: the
model has NOT been exercised against the real API -- no valid NVIDIA_API_KEY was
available -- so its conformance to the `guided_json` schema is unverified.

## DEC-008 — Fix the Windows ffmpeg path escaping despite the "don't touch the render layer" rule
**Context.** This refactor promised to leave `clipping/studio/` alone, and its
diff across all 11 stages was empty. But end-to-end verification could not run at
all on Windows: `escape_ffmpeg_filter_value` produced a path ffmpeg rejected, so
every subtitle burn-in failed. The bug is pre-existing and had never surfaced
because the project has only ever been run on Colab/Kaggle.
**Decision.** Fix it, since it blocked verification of the whole refactor. Keep
the change minimal and prove POSIX output is byte-identical so the primary
platform is untouched.
**Consequence.** One function changed in the render layer. The correct escaping
was established empirically by probing ffmpeg with seven candidate forms rather
than inferred from documentation.

## DEC-009 — Mirror argparse `choices=` as enums; invent no numeric bounds
**Context.** Declaring the 21 dropped `JobCreateRequest` fields raised the
question of how much validation to mirror from `clipping/config.py`. An audit of
that file found it enforces **no numeric range** for any of the 21 — the
argparse `type=int/float` cast is the only check — but three flags do carry
`choices=` allow-lists: `--split-trigger`, `--video-scale-algo`, `--yolo-size`.
**Decision.** Add `SplitTrigger`, `VideoScaleAlgo` and `YoloSize` enums so the
API rejects exactly what the CLI rejects. Add no `ge=`/`le=` bounds to the eight
numeric fields. `diarization_speakers` is typed `Union[Literal["auto"], int]` to
mirror `_parse_speakers`.
**Consequence.** The two front ends accept the same value domain. A bound added
on the API side only would have rejected payloads the CLI accepts — a silent
divergence, and out of scope for a "declare the missing fields" task. Constraining
`yolo_size` additionally closes a path-traversal surface: `config_adapter`
interpolates it into both a local filename and a download URL.

## DEC-010 — Translate prose to English; leave the AI prompts in Indonesian
**Context.** The human asked for the codebase in English. An inventory found
~477 translatable items, but also three classes of Indonesian text that are
*data*, not prose: the `_looks_indonesian` stopword list (a live language
detector), dict keys and comparison values (`"khusus"`, `"utama"`, `"chill"`,
`"jedag_jedug"`, `"kata_utama"`, `title_indonesia`/`title_inggris`), and the
~320-line AI prompt, which names ~15 Indonesian JSON keys the pipeline reads
back and explicitly requires three output fields in Indonesian.
**Decision.** Translate strings, comments, docstrings and UI copy. Do not rename
identifiers. Leave `get_analysis_prompt()`, `get_commentary_prompt()`,
`TARGET_ACCOUNTS` and the stopword list untouched.
**Consequence.** Zero behaviour change: verified by 0 diff hunks inside the
prompt and schema regions and by the suite. Translating the prompt was deferred
because its effect on output register cannot be verified without live API runs
(see A-007). Six test assertions on the old Indonesian text were updated to the
new wording at equal specificity.

## DEC-011 — Run the backend container as the host uid
**Context.** Every upload failed with `EACCES` on `/app/uploads`. The compose
bind mounts replace the image's directories, discarding the Dockerfile's
build-time `chown appuser`; the mounted directory carries the host's ownership
(uid 1000) while the process ran as `appuser`, created by `useradd -r` and so
below uid 1000.
**Decision.** Run the backend as `${DOCKER_UID:-1000}:${DOCKER_GID:-1000}`,
make `/tmp/Ultralytics` world-writable, pin `HOME=/tmp`, ship `.env.example`.
**Consequence.** Ownership matches by construction rather than by a `chmod` the
user has to remember. Not verified against a live daemon — none was available
in the authoring environment; verified only that compose parses and honours the
override.

## DEC-012 — The stdlib-only test suite is a hard constraint
**Context.** `tests/test_web_job_fields.py` imported `web.api.models`, pulling in
pydantic. CI installs pytest and nothing else, so collection aborted the entire
run with exit 2. It passed locally because pydantic is installed there.
**Decision.** The regression guard reads declared fields from `models.py` via
`ast` instead of importing it. Round-trip tests use plain dicts, which
`build_config_from_payload` already accepts. Only genuinely model-level tests
use `importorskip`.
**Consequence.** The guard keeps running in CI, which is the one place it
matters. Any future web test must be checked against a pytest-only environment,
not just a local one.

## DEC-013 — Structured output moves to `response_format`, not `nvext.guided_json`
**Context.** The first real analysis call ever made returned
`400 unknown field 'guided_json'` from `deepseek-ai/deepseek-v4-flash-0731`.
The endpoint was probed with six mechanisms: plain prompt, `json_object`,
`json_schema`, `nvext.guided_json`, top-level `guided_json`, and
`chat_template_kwargs`. `nvext.guided_json` was the ONLY one rejected.
`response_format={"type":"json_schema","strict":true}` returned the array
directly; `json_object` returned a `{"items": ...}` wrapper and invented
content, so it is not a substitute.
**Decision.** Send the schema via `response_format`. When a provider answers
400 naming that parameter, drop it for the remaining attempts rather than
burning identical retries, and let the prompt plus `_extract_clip_list` carry
the shape.
**Consequence.** The AI path works for the first time. The fallback keeps other
models usable. Verified live: 2 clips, zero missing keys, normalization passed.

## DEC-014 — Whisper device detection asks CTranslate2, not torch
**Context.** Defaults of `cuda`/`float16` crashed on CPU-only installs, and
`--whisper-device auto` was accepted but never resolved, so it crashed too.
The obvious detector, `torch.cuda.is_available()`, is the wrong signal: Whisper
runs on CTranslate2 and the default PyPI wheel is CPU-only, so torch can report
a GPU that Whisper cannot use. This host additionally has Docker's `nvidia`
runtime registered with no usable GPU, which defeats presence-based heuristics.
**Decision.** New `clipping/device.py` resolves device and compute type from
`ctranslate2.get_cuda_device_count()`, falling back to torch only if CTranslate2
cannot be asked. Defaults become `auto` everywhere; resolution happens once in
`load_whisper_model`.
**Consequence.** A CPU-only machine works with no flags. The CUDA branch is
unit-tested by injection but not exercised on real hardware here.

## DEC-015 — Optional-flag defaults must test `model_fields_set`
**Context.** `POST /api/jobs` auto-enabled `load_gemini_json` for a reused job
via `"load_gemini_json" not in payload`. Once that field was declared on the
model, `model_dump()` always included it, so the branch was dead and a rerun
failed demanding an API key it did not need.
**Decision.** Use `req.model_fields_set` to tell "not sent" from "sent as
false".
**Consequence.** Clone & Rerun works again. Any future "default this on unless
the client said otherwise" logic must use the same mechanism -- the payload
dict cannot express the distinction.

## DEC-014 — Read the pipeline's progress from its stdout, not from a callback
**Context.** The dashboard could only say "Analyzing with AI..." at 36%, for as
long as the provider took — up to a ten-attempt Gemini ladder with 60s-to-505s
backoff plus a fallback model. The pipeline already prints everything the user
needs (provider, model, each retry attempt and its reason, the Whisper device,
every render sub-stage, the model downloads that look like a hang), but
`run_pipeline(cfg)` and `studio.proses_klip(...)` expose no progress hook, so
none of it could reach a caller.
**Decision.** Tee `sys.stdout`/`sys.stderr` in `web/api/activity.py` and
attribute each line to the job whose worker thread produced it, rather than
threading an `on_progress` callback through `runner.py`, `engine.py` and
`studio/core.py`.
**Consequence.** `clipping/` is untouched, so the CLI pipeline's signatures and
the render layer the regression contract protects are unchanged, and the feature
covers every print site at once — including ones nobody enumerated. The tee
always writes the real stream first and records inside a `try`, so it cannot
break a print, and records only for threads inside `activity.capture(...)`, so
uvicorn's logging is unaffected. The cost is the coupling's shape: severity is
inferred from the pipeline's emoji, and `web/api/signals.py` — the one place
that matches on wording — reads the retry counters. Reword those prints and the
counter stops appearing while the line is still shown verbatim. The real limit
is that ffmpeg is a subprocess writing to the real file descriptors, so its
output is not in the feed; that is documented in the README.

## DEC-015 — The activity feed is capped and its persistence throttled
**Context.** `store._persist()` re-serializes every job in the store, under the
lock, on every write. That was affordable when only the 13 coarse worker
messages triggered it. The pipeline's own output arrives orders of magnitude
faster.
**Decision.** The feed is a 500-entry ring buffer with per-job sequence numbers;
event appends call `_persist(force=False)`, which writes at most once per
second. Every status and progress change still writes through immediately. The
store lock became an `RLock`.
**Consequence.** Persistence cost is bounded by wall-clock rather than by how
chatty the pipeline is, and no client-visible transition is delayed — the next
unthrottled write flushes whatever was skipped, within a crash window
best-effort persistence already had. Sequence numbers rather than list indices
because the ring buffer drops from the front and would shift any index a client
was holding. The `RLock` removes a whole class of deadlock: the tee turns any
`print` into a store write, so a plain `Lock` would hang the worker the moment
anything printed while the lock was held.

## DEC-016 — Responsive layout fixes go in the base declarations, not the mobile media query
**Context.** The dashboard scrolled sideways at 375px. The obvious home for the
fix was the existing `@media (max-width: 768px)` block, which is where the only
other responsive rules in the stylesheet live. Measuring first showed that would
have been wrong: at 820px — sidebar on screen, media query not applied — a job
page carrying a real `source_url` gives `scrollWidth` 959 against `clientWidth`
805. The bug is not a phone bug; it is a "content is wider than its column" bug,
and the column is narrowest *relative to its content* in the 769–1100px range,
where the sidebar still takes 260px.
**Decision.** `min-width: 0`, `flex-wrap: wrap` and `overflow-wrap: anywhere`
are base declarations on `.main-content`, `.page-header`, `.progress-steps`,
`.page-header h2/p` and `.job-info h3`. Only the cosmetic
`justify-content: flex-start` for already-wrapped steps is mobile-scoped, because
that one genuinely is about the wrapped state and nothing else.
**Consequence.** The stylesheet has a single breakpoint and no tablet range, so
anything scoped to `max-width: 768px` silently leaves 769–1100px broken. Rules
that express "this element must be allowed to shrink" belong unscoped; only
rules that express "at this size, arrange differently" belong in the query. The
cost is that `.page-header` and `.progress-steps` can now wrap at *any* width,
including desktop — which is the correct fallback (wrapping beats clipping), and
was verified not to trigger at 1280px.

## DEC-017 — `overflow-wrap: anywhere`, never `break-word`, for job-supplied strings
**Context.** The dashboard renders raw job input — `source_url`,
`upload_filename`, `job.id`, model ids — as unbreakable single tokens. After
`min-width: 0` let the content column shrink, those tokens still widened it:
`scrollWidth` 432 on a job page with a YouTube URL, and 552px for a job card
whose title was a long filename.
**Decision.** Use `overflow-wrap: anywhere` on the elements that render raw job
input.
**Consequence.** The two values are not interchangeable here. `break-word` wraps
the visible text but **does not reduce the element's min-content width**, and
min-content is precisely the quantity that propagates back up through
`min-width: auto` on every flex and grid item above it — so `break-word` would
have looked fixed in a screenshot while `scrollWidth` stayed wrong. Note that
`.activity-message` still uses `word-break: break-word` (the legacy alias) and
so still contributes a full-token min-content; it is contained today only
because `.log-viewer` is its own scroll container. If that container ever loses
`overflow`, this is where the overflow will come back.

---

## Index note — duplicate IDs DEC-014 and DEC-015 (recorded 2026-09-18)
Two IDs are used twice in this file, from two different sessions:

| ID | Entry | Subject |
|---|---|---|
| DEC-014 | first | Whisper device detection asks CTranslate2, not torch |
| DEC-014 | second | Read the pipeline's progress from its stdout, not from a callback |
| DEC-015 | first | Optional-flag defaults must test `model_fields_set` |
| DEC-015 | second | The activity feed is capped and its persistence throttled |

Not renumbered: this file is append-only and IDs are never reused or changed,
so rewriting them would invalidate every reference already made to them
elsewhere. Cite these four by **subject as well as ID**. The next free ID after
this note is **DEC-018**.

## DEC-018 — Finish the abandoned revert in `run_upload.py`, do not restore the feature
**Context.** `run_upload.py` could not be imported: it referenced
`youtube_uploader.safety`, deleted long ago. Investigating showed the module was
added in `5bf93d5` and removed deliberately in `ec3010d`
(`revert: remove safety checklist and manual approval from youtube_uploader`),
which also stripped `safety_config` and `skip_approval` from
`upload_manifest_to_youtube` — but never updated the CLI. So the file carried
**two** breakages, and the intuitive fix (restore `safety.py`) would only have
turned the `ImportError` into a `TypeError`.
**Decision.** Finish the revert: delete the import, the `--safety-config` and
`--no-approval` flags, the approval warning, and the two dead kwargs. Do not
reinstate the guardrails. Keep `youtube_uploader/Safety.md` and
`upload_safety.json`.
**Consequence.** `run_upload.py` now matches `run_fb_upload.py` — written
*after* the revert with no safety surface — and both READMEs, which document no
safety flags. Nothing that functions was removed: the enforcement died in
`ec3010d`, only references to it survived. The open **product** question is
untouched and deliberately so: the guardrails (daily caps, minimum interval,
manual approval) were originally written to fight YouTube bans, and were removed
for automation, not because the risk went away. If they are ever wanted back,
restore `git show 5bf93d5:youtube_uploader/safety.py` — **not** Safety.md's
snippet, whose defaults (3/day, 2/run, 2h, queue 15) contradict the shipped
`upload_safety.json` (2/day, 1/run, 24h, queue 7) and which pulls in an
undeclared `pytz` and writes the config file back to disk.

## DEC-019 — The SDK's own retry policy is disabled; the ladder in `engine.py` is the only one
**Context.** Job `756c7ee8a2c3` reported three NVIDIA attempts and had made
nine. `_make_nvidia_client` passed neither `max_retries` nor `timeout`, and the
`openai` SDK (2.24.0) defaults to `max_retries=2` with `_should_retry` returning
True for any status >= 500 — so every attempt in the visible ladder was silently
1 + 2 HTTP requests. Three deterministic 504s at ~302s each cost 45 minutes
instead of 15, and the log misreported what had happened.
**Decision.** `max_retries=0` and an explicit
`timeout=NVIDIA_REQUEST_TIMEOUT_SECONDS` (330s) on the client.
**Consequence.** Two retry policies stacked multiplicatively, not additively,
which is why the arithmetic was so far off. Any future client construction in
this project must set `max_retries` explicitly — the SDK's default is not a safe
one when the caller has its own ladder, and the failure is invisible because the
SDK's retries produce no output. 330s is deliberately *above* the measured ~300s
gateway limit so the server's 504 is received rather than raced to a local
timeout: a 504 says the gateway gave up, a client timeout says nothing.
`tests/test_nvidia_retry.py::test_client_disables_the_sdks_own_retries` pins it.

## DEC-020 — The analysis has an overall time budget, checked predictively
**Context.** Bounding each request is not the same as bounding the wait. With a
330s per-request timeout the three-attempt ladder still runs to ~17 minutes, and
against a provider failing deterministically every minute of that re-proves the
same result.
**Decision.** `NVIDIA_TOTAL_BUDGET_SECONDS = 900` caps the whole ladder. The
check before each attempt asks whether the attempt *could* outlast the budget
(`elapsed + REQUEST_TIMEOUT > BUDGET`), not whether the budget is already spent.
**Consequence.** A retrospective check would be nearly useless here: a third
attempt starting at 610s has not exceeded a 900s budget but ends at ~940s. The
budget is deliberately >= two full-length requests, so a slow-but-healthy call is
never cut off — cutting one off would be a regression dressed as a fix. For the
observed failure the ladder now stops after 2 attempts and ~604s. The reason is
printed, so it reaches the activity feed instead of the job simply ending
sooner with no explanation.

## DEC-021 — An oversized request proposes a smaller one; it does not silently shrink it
**Context.** The 504s were not transient. Probing the live endpoint measured
generation at **~12–13 tokens/s** and **~1200 tokens per clip** (23 required
fields), so the gateway's **~300s** window fits about **three** clips. The
shipped default asks for **seven**, which needs ~660s and can never complete.
All three attempts were re-sending an arithmetically impossible request. The
probe also eliminated the two obvious alternative causes: `max_tokens` (4096 and
16384 behave identically) and the strict `response_format` schema (removing it
still 504s at 302.1s).
**Decision.** When a run fails and any attempt failed with a "too much work"
signature — a **504**, an **`APITimeoutError`**, or a schema-conformant **empty
clip array** — the error carries a **proposal** naming a concrete smaller clip
count and how to apply it (`--clips N`, or Clips + Clone & Rerun). The request
is **never** silently shrunk, and an ordinary malformed sample proposes nothing.
**Consequence.** An earlier version of this change degraded automatically —
7 → 3 on the next attempt — and was rejected in review: silently returning three
clips to someone who asked for seven trades one surprise for another, and the
user cannot tell whether they got what they asked for. Proposing keeps the
decision with the person who set the number.

Three details are load-bearing:
- **The proposal fires at the end of the ladder, not on the first 504.** A single
  504 can be a gateway blip rather than a capacity limit, so the retry is still
  spent; only a run that actually fails proposes. Tested, both ways.
- **The suggestion is capped at measured capacity, not halved.** Half of a
  30-clip request is 15 — still five times what the provider can do, and an
  unusable suggestion is worse than none. `_suggested_clip_count` is
  `min(current - 1, NVIDIA_CLIPS_WITHIN_BUDGET)`, so it is always strictly
  fewer than what failed and never above what was measured to work.
- **It is printed as well as raised.** `web/api` tees stdout into the activity
  feed, so a proposal that only lived in the exception would reach a different
  surface from the one the user is watching.

**This still treats the symptom.** The underlying mismatch is that `clips`
defaults to **7** and the API permits up to **30** (`web/api/models.py:108`,
`Field(7, ge=1, le=30)`), while this provider delivers ~3; 30 would need ~3000s
against a 300s ceiling. Lowering the default and the bound was offered and
deliberately **not** taken — it silently gives every user fewer clips, which is
a product decision. If the provider stays this slow, the default is the thing to
revisit.

## DEC-022 — A Whisper transcript is persisted, and a re-run detects it without a new flag
**Context.** The transcript existed only in memory. Any failure at or after AI
analysis destroyed it, and so did success — a completed job's directory holds
`gemini_response.json`, the video, clips, thumbnails and the manifest, and no
transcript. On the measured CPU job that was 94 minutes thrown away, and it made
the "re-run with fewer clips" proposal of DEC-021 cost ~94 minutes to act on.
**Decision.** `resolve_transcript` writes `transcript.vtt` into `cfg.outputs_dir`
when Whisper actually ran, and `build_config_from_payload` falls back to that
file when no transcript was uploaded. **No new request field and no flag.**
**Consequence.** `resolve_transcript` already treats a non-null
`transcript_path` as "skip Whisper", so detection alone is enough — and adding a
flag would have landed back in DEC-015 territory, where `model_dump()` always
contains declared fields and "default this on for a reuse" logic must test
`model_fields_set`. That is the bug that broke Clone & Rerun once already, so
the design deliberately avoids being able to repeat it. Because `outputs_dir` is
a pure function of `job_id` and `reuse_job_id` reuses the id, Clone & Rerun
lands in the same directory with no dashboard change.

Four constraints the round-trip imposed, each a silent-corruption risk if missed:
- **`dedupe` must be off for a file we wrote.** `parse_vtt_subs` drops a cue
  whose text repeats the previous cue's — correct for scraped captions with
  rolling repetition, wrong for speech: `you know / you know` came back as one
  `you know`. Plumbed as `cfg.transcript_dedupe`, defaulting True.
- **`transcript_offset` must not be reapplied.** It is a manual sync correction
  for a *supplied* file. A saved one was generated from this very video, so an
  old offset would desync every subtitle.
- **The write is atomic** (temp + `os.replace`). The reader raises on a
  malformed transcript and no endpoint can delete a file from an output
  directory, so a half-written file would hard-fail every later re-run with no
  recovery from the UI.
- **Saving is best-effort.** A disk error must not fail a run whose expensive
  work has already succeeded.

Two accepted, tested losses: non-final word *ends* snap to the next word's start
(harmless — `studio/subtitles.buat_file_ass` recomputes them identically, so the
renderer never sees the originals), and segments are **re-chunked** by
`max_words_per_subtitle` rather than preserved, because the reader flattens words
while Whisper also breaks at its own segment ends. No word, order or start time
is lost. Note the CLI's `outputs_dir` is shared rather than per-job, so
consecutive CLI runs overwrite the file; only the web path is per-job.

## DEC-023 — An explicit, configured provider chain is not the fallback DEC-003 forbade
**Context.** Every heavy AI call now targets a free hosted endpoint, and free
endpoints fail for reasons that have nothing to do with the request: a daily
quota resets at midnight Pacific, a model is retired without notice, a gateway
has a bad minute. Pinning the whole pipeline to one provider makes each of those
a job failure. But DEC-003 removed cross-provider fallback for good reasons, and
those reasons have not changed.
**Decision.** Introduce `LLM_CHAIN`: an ordered list of `<provider>/<model>`
links, tried in order, defined in `clipping/providers/registry.py` and
overridable per run with `--llm-chain`.
**Consequence.** What DEC-003 actually forbade was a *silent* fallback — bare
`except Exception` that billed a user on Gemini when they had chosen NVIDIA,
reducing the real error to one warning line, and which is how an undeclared
`openai` dependency stayed invisible. A chain differs on every count that
mattered:
- it is a list the user wrote down, not a hidden second choice;
- every hop is printed, including *why* the previous link was abandoned;
- a provider absent from the chain is never contacted — enforced by
  `test_a_provider_not_in_the_chain_is_never_contacted`, not merely intended;
- a link with no key is skipped with a line saying which env var is missing,
  rather than raising, so a partly-configured chain degrades to what is set up.

The `analyze_with_ai` dispatcher keeps its no-silent-fallback behaviour for the
single-provider modes. DEC-003 is scoped by this entry, not overturned.

## DEC-024 — The NIM default is `google/gemma-4-31b-it`, chosen by measurement
**Context.** `deepseek-ai/deepseek-v4-flash-0731` answered a real job on
2026-09-19 and returned **410 Gone** on 2026-09-21. The entire DeepSeek v4
family has left the NIM catalogue; only `deepseek-coder-6.7b-instruct` remains,
which is a coding model. This is the **third** NIM default this project has had
(`deepseek-v4-pro` died 2026-08-07, DEC-004 → DEC-007), so the shipped default
was broken in production at the moment this was written.
**Decision.** Benchmark the live catalogue against the real workload and pick on
evidence. Measured 2026-09-21 with `tools/bench_llm.py`, ~1700 tokens in / 500
out, one http request per sample:

| model | best | tok/s | result |
|---|---|---|---|
| `google/gemma-4-31b-it` | **6.0s** | 31.3 | 3/3 schema-valid |
| `openai/gpt-oss-20b` | 25.3s | 12.4 | 3/3 schema-valid |
| `nvidia/nemotron-3.5-lightning-30b-a3b` | 73.0s | — | returned prose, not JSON |
| `nvidia/nemotron-3-super-120b-a12b` | 4.5s | — | returned malformed JSON |

**Consequence.** `google/gemma-4-31b-it` becomes the `--nvidia-model` default and
the NVIDIA link of the default chain. Supersedes DEC-004 and DEC-007.

Two corrections this forced, both worth more than the model choice itself:
- **DEC-007's stated safeguard does not work.** It claimed pinning the model
  string in a test would make "the next retirement surface as a test failure
  rather than a production 410". It cannot: the string is still the string after
  NVIDIA retires the model. It caught neither retirement. The assertion is kept
  — a deliberate change to the default should still be a visible decision — but
  its docstring now says plainly what it can and cannot do.
- **What actually survives a retirement is the chain.** A dead link answers 410,
  which `errors.classify` calls fatal, so it is abandoned without burning
  retries and the next provider answers. That is a structural fix; a pinned
  string is not.

## DEC-025 — The SDK's retry policy is disabled for every provider, not just NVIDIA
**Context.** DEC-019 set `max_retries=0` on the NIM client after discovering the
SDK's default of 2 had turned a reported "attempt 3/3" into nine silent HTTP
requests. The new provider layer builds a client for five more providers.
**Decision.** `build_client` sets `max_retries=0` and an explicit per-provider
timeout unconditionally, pinned by
`test_the_sdks_own_retries_are_disabled`.
**Consequence.** The ladder in `llm.py` is the only retry policy in force
anywhere. The failure mode this prevents is invisible — the SDK's retries print
nothing — so it has to be asserted rather than remembered.

## DEC-026 — Structured output is negotiated per model and remembered per process
**Context.** DEC-013 established the `response_format` ladder for one model.
With five providers and any model the user names, the question "does this model
accept a JSON schema?" has no fixed answer, and getting it wrong costs a whole
attempt.
**Decision.** `complete_json` walks `json_schema` → `json_object` → prompt-only,
caching the level that worked for each `(provider, model)` pair. A schema
rejection is not counted as a retry, because the request was never really made
at that level.
**Consequence.** Generalizes DEC-013 rather than replacing it, including its
400-detection predicate. Two details are load-bearing and tested: the level is
recorded only *after* the reply parses — a provider that accepts `json_schema`
and then ignores it is worse than one that refuses it — and an ordinary 400
(a bad temperature, an oversized `max_tokens`) must not be mistaken for a schema
refusal, or the client would silently strip the schema and accept whatever prose
came back.

## DEC-027 — Analysis becomes three small passes over sentence beats
**Context.** The single-request design never produced a clip set. It asked for
`clips` items each carrying 22 required fields — measured at ~1200 output tokens
per clip — from a provider generating 12-13 tokens/s behind a gateway that gives
up at ~300s. Seven clips needs ~660s and cannot finish; it also exceeds Groq's
8000 tokens/minute in a single call. Every AI job in `outputs/jobs.json` failed,
and the only "completed" job was a hand-written render probe.
**Decision.** Split it:

| pass | requests | largest generation |
|---|---|---|
| A candidate scan, one per ~45-beat window | ~4 for 20 minutes | ~320 tokens |
| *(snap + dedupe — Python, no model)* | 0 | — |
| B global re-rank | 1 | ~200 tokens |
| C per-clip metadata | one per clip | ~280 tokens |

**Consequence.** ~12 requests and ~19k tokens for a 20-minute video, with no
generation over ~320 tokens — small enough for every free tier measured. Three
properties come out of the split that the monolith did not have:

- **The re-rank sees the whole video at once.** The monolith claimed to, and
  never achieved it, because it could not finish.
- **A failure is local.** A failed window loses one window's candidates; a
  failed metadata request loses one clip's title, and that clip still renders.
  Previously any failure lost the entire job.
- **Hallucinated timings became impossible.** The model answers with beat ids,
  never timestamps, and `beats.span_of` raises on an id that does not exist.

## DEC-028 — The model chooses moments; Python chooses cuts
**Context.** Asking a language model for a timestamp invites one that is
plausible and wrong. A clip starting half a syllable early is glaring to a
viewer and invisible in a JSON diff.
**Decision.** `clipping/analysis/snap.py` owns every timing decision: sentence
boundaries, the platform duration window, lead-in and tail clamped to
neighbouring beats, and overlap rejection.
**Consequence.** Two asymmetries are deliberate and tested. Growth prefers
**forward**, because a candidate that is too short usually stops before its own
payoff. Trimming removes beats from the **start**, never the end, because the
payoff of a short-form clip is its last line — trimming from the end to fit a
duration window produces a clip that stops before the reason it was chosen.

A candidate that cannot be made to fit is dropped **with a printed reason**
rather than silently, because a moment the model liked and the snapper refused
is exactly what someone reading the log needs to see.

## DEC-029 — The legacy key names live at one boundary, not in the model's prompt
**Context.** The render layer, both uploaders and `metadata.normalize_and_validate`
read a fixed set of clip keys — several Indonesian, one misspelled (`hastag`).
Renaming them means editing a render layer with zero automated coverage (RC-7)
that is verified only by live renders.
**Decision.** `clipping/analysis/adapter.py` translates the slim output into
those names. The model is asked for `title_native`; the adapter puts it in
`title_indonesia`.
**Consequence.** `clipping/studio/` is untouched, and the model never sees an
Indonesian key name — which is also what stops it defaulting to Indonesian, the
thing DEC-010 could not fix without live verification. A French video now gets a
French title in a key called `title_indonesia`, which is ugly and correct.

The guard is an **AST test**, not a convention: every key `clipping/studio/*`
and `runner.py` read off a clip dict must appear in `LEGACY_KEYS`, in
`PIPELINE_WRITTEN_KEYS` (things the pipeline writes itself, like `voiceover`),
or in `NORMALIZER_KEYS`. It reads the render layer with `ast` rather than
importing it, because those modules import cv2 and mediapipe at file scope and
CI installs pytest and nothing else. Verified non-vacuous: removing
`typography_plan` from the list fails the test.

## DEC-030 — Five fields are no longer asked for, and five more are derived
**Context.** An audit of the 22-field schema against every consumer found that
much of it was never read.
**Decision.** Not asked for at all: `recommended_visual_broll_hook` (read by
nothing in the repo), `klasifikasi_akun` with its eight nested keys and the
`TARGET_ACCOUNTS` personas (printed to the console, never used for routing), and
the three `tiktok_*_id` variants (never reached `render_manifest.json`).
Derived in Python: `typography_plan`, `broll_list`, `keep_segments`, `hook_v2`,
`bgm_mood`.
**Consequence.** Roughly 1200 output tokens per clip become ~280. Deriving also
made two of them *correct* for the first time: the old prompt asked the model to
emphasise words from the transcript and to keep b-roll out of the hook window,
and nothing verified either. An emphasis word that is not actually spoken cannot
be matched by `buat_file_ass` and would render unstyled — an invisible failure —
so it is now dropped, and a b-roll placement that would cover the hook is moved.

`keep_segments` and `hook_v2` are **omitted** rather than emitted empty:
`studio/core.py:262` and `:456` have their own fallbacks, and an empty list
suppresses them.

## DEC-031 — `--dry-run-analysis`, and why `studio` is imported late
**Context.** Analysis is the expensive, uncertain half; rendering is the slow,
reliable half. Paying for both to inspect the first is wasteful, and on this
ARM host the render is the longer part.
**Decision.** `--dry-run-analysis` writes `gemini_response.json` and
`metadata_preview.json` and stops. Re-running with `--load-gemini-json` renders
from them for free.
**Consequence.** `run_pipeline` no longer imports `clipping.studio` at the top.
Every studio module imports cv2, mediapipe, PIL and numpy at file scope, so the
old placement made an analysis-only run impossible on a machine with no render
stack — which is exactly the machine such a run is for. Found by trying it: the
first `--dry-run-analysis` on this host died on `ModuleNotFoundError: cv2`
before reaching the analysis it was meant to run.

## DEC-032 — Transcription moves to hosted providers; local Whisper stays as a fallback
**Context.** In-process Whisper is unusable on the hardware this runs on. On
this ARM host, `faster-whisper` with `large-v3` on CPU measured **4.6x
realtime** — 94 minutes for a 20-minute video — and there is no GPU to move it
to. That single fact is what the local-first refactor was working around, and
supplying a `.vtt` by hand was the workaround.
**Decision.** `STT_CHAIN`, spelled like `LLM_CHAIN`: an ordered list tried in
order, defaulting to
`groq/whisper-large-v3-turbo,mistral/voxtral-mini-latest`. `--no-whisper`
becomes an alias for `--stt-chain none`, and `local/faster-whisper` is a link
like any other.
**Consequence.** A video with no transcript now works in minutes instead of
hours, for free. Three details are load-bearing:

- **The output goes through `transcript._chunk_into_segments`**, the same
  function the VTT and JSON3 parsers use. RC-1 then holds by construction
  rather than by a second implementation happening to agree with the first.
- **A hosted failure only falls back to local Whisper when the chain says so.**
  Silently spending 94 minutes on CPU because a hosted call failed is exactly
  the surprise this pipeline exists to prevent, so a chain with no `local/`
  link raises instead.
- **Voxtral's link omits `language`.** Mistral rejects
  `timestamp_granularities` and `language` together, and word timings matter
  more than a language hint — without them there is no karaoke.

The provider also reports the language it heard, which is better evidence than
the stopword detector guessing afterwards, so it is fed forward as
`cfg.detected_language`.

## DEC-033 — Audio is chunked on measured bytes, not on duration
**Context.** Groq's free tier caps an upload at 25 MB. The obvious
implementation assumes a bitrate and splits by the clock.
**Decision.** Extract to 16 kHz mono FLAC — what every Whisper-family model
resamples to anyway — then derive bytes-per-second from the **actual file** and
split only when it does not fit, moving each boundary to the nearest silence
within 15 seconds.
**Consequence.** Measured on the real 20-minute video: **39.3 MB**, half again
over the cap. An earlier draft of this note estimated 19-23 MB from the format
alone and was simply wrong — FLAC is variable-rate, and this video has music
under most of it. A quiet interview of the same length would fit in one
request, and the derived rate gets both cases right where a fixed assumption
gets one of them wrong.

Two refinements came from running it rather than reasoning about it:
- **Boundaries snap to silence**, so a cut never lands mid-word and the seam
  has something for the overlap-dedupe to match on.
- **A stub final chunk is absorbed.** The first real plan ended with a
  10.8-second tail: its own upload, its own round trip, its own seam, and Groq
  bills a 10-second minimum per request regardless. It is folded into the
  previous chunk when the result still fits, and kept when it would not,
  because a short chunk beats a rejected one.

## DEC-034 — Settings are persisted to a 0600 file, not held in a dict
**Context.** API keys entered on the Settings page lived only in
`worker._settings_env`. A restart emptied them silently, and the next job failed
for a key the dashboard still showed as set.
**Decision.** `web/api/settings_store.py` writes `data/settings.json`
atomically with mode 0600, loaded at import and written through on every PUT.
`data/` is gitignored and bind-mounted.
**Consequence.** The mode is applied to the temp file *before* the rename, so
the real path is never briefly world-readable. `load()` never raises: a corrupt
file returns `{}` rather than stopping the server, because the user can re-enter
values but cannot re-enter them into a server that will not boot.

## DEC-035 — An interrupted job is failed at startup, but its error is not rewritten
**Context.** A job whose worker thread died with the process stayed in a
non-terminal status forever. `outputs/jobs.json` has held one in `analyzing`
since 2026-09-18.
**Decision.** `store.fail_stale_jobs()` runs in the app's lifespan startup.
**Consequence.** It deliberately does **not** overwrite an existing `error`.
Replacing a real diagnosis with "interrupted by a restart" would destroy the
only record of why a job actually failed, which is worse than the stuck record
it was written to fix.

## DEC-036 — `output_file` is deleted rather than excused
**Context.** A new guard asserting that every manifest key `worker.py` reads is
actually written failed on `output_file`.
**Decision.** Check the whole git history, find that no version of
`studio/core.py` has ever written it, and delete the dead fallback.
**Consequence.** Zero behaviour change — `video_path` is and always was the real
key — and the manifest/worker agreement becomes exact and testable rather than
permanently excusing one dead name. The alternative, an allow-list entry, would
have made the guard weaker every time something like this was found.

## DEC-037 — One API token on every route; `/api/health` is the only exception
**Context.** There was no authentication of any kind, and the backend bound
`0.0.0.0:8000` on a machine with a public IP. Anyone who found the port could
read every job, upload a 2 GB file, read back which API keys were configured, or
call `POST /api/shutdown` — an unauthenticated kill switch.
**Decision.** A bearer token checked with `hmac.compare_digest`, applied as a
router-level dependency so a new route file cannot be added unprotected by
accident. Generated on first start and stored 0600 in `data/api_token`; pinned
with `API_TOKEN`. The port moves to `127.0.0.1`.
**Consequence.** Generating rather than refusing to start is deliberate: a
server that will not boot without a hand-written token is one people work around
by disabling auth. `/api/health` stays open because a container healthcheck and
a reverse proxy need it, and it reports only booleans and counts.

The escape hatch `DISABLE_AUTH=1` exists for a developer's terminal and is
asserted absent from both compose files by a test.

**The bug worth remembering:** the dependency was first written
`async def require_token(request)` with no annotation. FastAPI then treats
`request` as a request-body field, so **every route answered 422** and the token
was never examined — including the health check. Every unit test passed, because
every function was individually correct. Only a live request showed it, which is
why `tests/test_auth_token.py` now drives a real `TestClient` and asserts 401
rather than 422.

## DEC-038 — The dashboard is served by the API, and SSE moves off EventSource
**Context.** The production image ran the **Vite dev server**. The dashboard
also hardcodes `API_BASE = '/api'`, so it only ever worked same-origin.
**Decision.** Build the dashboard into the image (`node:20-alpine` stage,
`npm ci`) and mount `dist/` at `/` **after** the API routers. One origin, no
CORS, `api.js` unchanged in that respect.
**Consequence.** The `.:/app` bind mount in compose would have hidden the built
dashboard behind the host's (gitignored, absent) directory, so an anonymous
volume keeps the image's copy visible — the same trick `__pycache__` already
uses two lines above. Without it the API would have silently fallen back to its
"dashboard not built" JSON.

`EventSource` cannot send headers, so the job stream is read with `fetch` and a
`ReadableStream` instead. The easy alternative was `?token=...`, which puts the
credential in access logs, browser history and every `Referer` the page sends.
A test asserts `token=` never appears in `api.js`.

`npm ci` needs a lockfile, and the project shipped none — recorded as a
follow-up conflicting with the "pin and verify" rule. It is committed now: a
production image that resolves its own dependency tree at build time is exactly
what that rule exists to prevent.

## DEC-039 — A refused download is a job state, not a failure
**Context.** Server-side downloading was asked for, and it mostly will not
work from here. Sites score a request by the IP's reputation before they look
at cookies or proof-of-origin tokens, and this machine is in a datacenter
range. The bgutil project says plainly that a PO token "may help your traffic
seem more legitimate" — it is not a bypass.
**Decision.** Try anyway, behind `ENABLE_SERVER_FETCH` (on by default), and
when refused move the job to a new status, `needs_upload`, carrying a message
that names both remedies. `POST /api/jobs/{id}/source` attaches a file and
resumes **the same job**.
**Consequence.** The job keeps its id, its settings and its output directory,
so anything it already produced — notably a saved transcript (DEC-022) — is
still there. Creating a fresh job instead would discard all of it to work
around a download the user has already handled.

Two distinctions are load-bearing and tested:
- **A refusal is not a missing video.** A private or deleted video also fails
  to "extract a player response", so the fatal markers are checked *first*;
  otherwise the user is sent off to run a helper script for a video that no
  longer exists.
- **A missing yt-dlp parks the job too.** The remedy is identical to a refusal,
  so an `ImportError` must not reach the user as a failed job with a Python
  traceback they cannot act on. Found by running it: yt-dlp is not installed on
  this host.

## DEC-040 — The PC helper is the supported path, and it is stdlib-only
**Context.** Downloads succeed from a home connection and fail from a VPS. The
human has a Windows machine on the same tailnet.
**Decision.** `tools/rzclips-fetch.py`: downloads with yt-dlp locally, uploads
the video and its subtitles, creates the job, prints the URL. One command.
**Consequence.** It imports nothing outside the standard library, asserted by a
test that walks its AST — it has to run on a bare Windows Python where a
dependency is something the user must install before the tool works at all. A
second test checks every key it sends is a declared `JobCreateRequest` field,
because an undeclared key is silently dropped by Pydantic, which is exactly how
the "Bypass AI" toggle once did nothing.

It prefers `json3` subtitles for the same reason the server does: that format
carries YouTube's own per-word timings, and word timings are what the karaoke
subtitles are built from.

## DEC-041 — `clipping/ingest/fetch.py` is named `downloader.py`
**Context.** The package exported a function `fetch` from a module `fetch`, so
`from clipping.ingest import fetch` returned the function to some callers and
the module to others. Thirty-five tests failed on it at once.
**Decision.** Rename the module.
**Consequence.** Renaming the module is better than renaming the function: the
function's name is what callers read, and `ingest.fetch(url)` says what it
does. Recorded because the failure mode is confusing out of proportion to the
cause — an `AttributeError` on a module that plainly has the attribute.

> **Renumbered on merge (2026-09-21).** DEC-042 to DEC-045 below arrived from
> `origin/main` as DEC-023 to DEC-026. This branch had already published its own
> DEC-023 to DEC-041 across 16 commits, so main's four moved rather than ours.
> A commit message on `main` may still cite the old number:
> DEC-023→042, 024→043, 025→044, 026→045. Main itself had already renumbered
> these once (from DEC-016..019); that earlier mapping is superseded by this one.

## DEC-042 — One generic OpenAI-compatible provider, not a provider per vendor
**Context.** The human was previously pointed at Groq, xAI (Grok) and Mistral as
free analysis providers and found none of them usable, and the Settings page had
nowhere to put such a key in any case. Checking the providers on 2026-09-21:
xAI ended its free API tier in May 2025 and offers only conditional promo
credits; Groq's and Mistral's docs still advertise a free tier, but the human's
own attempt says otherwise. NVIDIA NIM and Google Gemini both still issue a key
with no credit card. Meanwhile `analyze_with_nvidia` turned out to be ordinary
OpenAI-SDK code with four NVIDIA-specific details in it.
**Decision.** Keep NVIDIA and Gemini as the two recommended providers, and add a
single `openai_compat` provider taking a base URL, a key and a model, with
base-URL presets in the UI. No named Groq/Mistral/xAI providers.
**Consequence.** One dispatcher branch and one `PROVIDER_KEYS` entry covers
OpenRouter, Groq, Mistral, xAI, vLLM and Ollama alike, and anything else that
appears later, without the enum, the argparse choices, the gate and the settings
form growing per vendor. Three sub-decisions:
- The id is `openai_compat`, not `openai`: a test already pins `"openai"` as an
  *unknown* provider, and `OPENAI_API_KEY`/`OPENAI_BASE_URL` are read implicitly
  by the `openai` SDK, so reusing those names would cross-talk with a real
  OpenAI account. The env vars carry the same `_COMPAT` infix for that reason.
- The API key stays required even for a local Ollama, which ignores it. Making
  the gate conditional on the URL looking like localhost would put URL parsing
  inside a security-adjacent check to save the user typing one word; the UI says
  to enter any value instead.
- `PROVIDER_REQUIRED_EXTRA` extends the fail-fast gate to the base URL and model.
  A half-configured endpoint fails as surely as a missing key and should fail as
  early — before ingestion and transcription have run.

## DEC-043 — Settings persist to `.local/settings.json`, and an empty value clears
**Context.** Everything entered on the Settings page lived in a module-level dict
in `worker.py`, so a restart discarded every API key with no warning.
**Decision.** Persist an allow-listed subset to `.local/settings.json`
(overridable with `WEB_SETTINGS_FILE`), written atomically and owner-only, loaded
from the app lifespan. An empty value removes an override rather than storing an
empty string.
**Consequence.** Three things follow, and each was the reason for a rejected
alternative:
- **Not `outputs/settings.json`.** `routes/files.py` serves that directory to
  the browser. Its `".."` check happens to make the current route shape safe, but
  a secrets file does not belong inside a served tree on principle.
- **Not loaded at import**, the way `store.py` loads jobs. An import-time read of
  a secrets file means any test importing the worker picks up the developer's
  real keys.
- **Empty means clear.** `config_adapter` resolves every key as
  `env.get(NAME, os.environ.get(NAME, ""))`, so a persisted empty string would
  shadow a working `.env` key permanently, with no way to undo it from the UI.
  The bug was latent before persistence; storing values would have made it stick.

## DEC-044 — The NIM default leaves the DeepSeek family for NVIDIA's own model
**Context.** `deepseek-ai/deepseek-v4-flash-0731` reached end of life at
2026-09-21T08:00:00Z and returns 410, so every default job failed. This is the
third death in this slot (DEC-004 `deepseek-v4-pro`, DEC-007 this one), and the
DeepSeek chat family is now absent from the platform entirely — only
`deepseek-coder-6.7b-instruct` remains, which is a code model.
Four candidates were probed through the real production path:
`nvidia/llama-3.1-nemotron-70b-instruct` and `mistralai/mistral-large-2-instruct`
are listed in `/v1/models` but answer **404 for this account** — being listed is
not the same as being available. `openai/gpt-oss-20b` worked but took 587s for a
single clip. `nvidia/nemotron-3-super-120b-a12b` returned a complete
schema-valid result in 62s.
**Decision.** Default to `nvidia/nemotron-3-super-120b-a12b`.
**Consequence.** The pinned default is NVIDIA's own current generation on
NVIDIA's own endpoint, which is the least likely thing to be retired from under
us. `metadata.py`'s so-called DeepSeek fixup is generic alias handling, so
leaving the family costs nothing. **Verified live**, end to end: two real clips
rendered at 720x1280 from a local mp4 + vtt. Note the retirements are not
predictable — the test pinning this string is what turns the next one into a
test failure instead of a production 410.

## DEC-045 — Salvage the first well-formed JSON value when a direct parse fails
**Context.** The first live run through the new `openai_compat` provider failed
all three attempts with `JSONDecodeError`. The model had returned a valid
`json_schema` array preceded by a bare `[` on its own line — a fragment of its
reasoning scratchpad in the content. `finish_reason` was `stop`; nothing was
truncated. NVIDIA's own path never hits this because it sends
`extra_body={"chat_template_kwargs": {"thinking": False}}`, which suppresses the
reasoning pass. An arbitrary OpenAI-compatible endpoint has no equivalent
switch, and sending that NIM-only field to one would risk a 400.
**Decision.** Keep the direct `json.loads` as the fast path. On failure only,
scan for the first balanced JSON array or object, tracking string state and
escapes so a bracket inside a title cannot truncate the span.
**Consequence.** Reasoning models are usable through the generic provider
without a vendor-specific flag. Salvaged content still passes through every
existing shape check, so this cannot smuggle a malformed clip through, and
unsalvageable content still raises retryably. Verified live: the same run that
failed three times now succeeds on the first attempt and renders.

## DEC-046 — Two custom-endpoint paths, kept side by side rather than collapsed
**Context.** Merging `origin/main` into the rearchitecture branch brought two
independent answers to the same need. This branch had built
`clipping/providers/` — a six-provider registry (groq, gemini, nvidia,
openrouter, mistral, **custom**), a chain runner, negotiated structured output
and tolerant JSON extraction — reached with `--ai-provider chain` and configured
by `LLM_CHAIN` + `LLM_CUSTOM_BASE_URL`/`LLM_CUSTOM_API_KEY`. `origin/main` had
separately added `openai_compat`: a third *legacy single-request* provider
alongside `nvidia` and `gemini`, configured by `OPENAI_COMPAT_*`, with a
Settings-page card, a New Job selector, a fail-fast gate for its base URL and
model (`PROVIDER_REQUIRED_EXTRA`), and 46 passing tests. The human's instruction
was to keep both sides' work, with this branch winning genuine conflicts.
**Decision.** Keep both. `PROVIDER_KEYS` holds all seven providers;
`--ai-provider` accepts `chain, gemini, nvidia, openai_compat`; the chain's
`custom` link and the legacy `openai_compat` provider coexist with separate env
vars. Nothing from either side was deleted.
**Consequence.** Three things follow.
- **One redundant path, and it is the cheap option.** Collapsing them would have
  silently changed the meaning of an existing `OPENAI_COMPAT_*` setup, deleted a
  working feature and its 46 tests, and rewired a dashboard that merged in
  cleanly. The redundancy mirrors one the branch already tolerates: `nvidia` and
  `gemini` are legacy single-request paths that the chain also covers.
- **`set(PROVIDER_KEYS) == set(registry.PROVIDERS)` stopped being true**, because
  `openai_compat` is not a chain link. `test_every_registry_provider_has_a_key_mapping`
  now asserts containment in the direction that matters (every chain provider has
  a key mapping) instead of equality, and `PROVIDER_CASES` is split from
  `CHAIN_ONLY_PROVIDERS` because the four chain-only providers are not valid
  `--ai-provider` values and cannot be gated the same way.
- **Which conflicts this branch actually won:** `AI_PROVIDER = "chain"` (not
  `nvidia`), `NVIDIA_MODEL = "google/gemma-4-31b-it"` (not
  `nvidia/nemotron-3-super-120b-a12b` — gemma is what the human's 7-clip run on
  2026-09-21 succeeded with), the chain-aware `missing_provider_key`, and the
  banner that prints a chain rather than one model name. Main's
  `PROVIDER_REQUIRED_EXTRA` loop was folded *into* the chain-aware gate rather
  than replacing it.

## DEC-047 — Settings persist to data/, with main's two better behaviours
**Context.** Both sides of the merge had independently built settings
persistence, because both had hit the same bug: everything typed into the
Settings page lived in a module-level dict in `worker.py`, so a restart threw
every API key away silently. This branch wrote `data/settings.json`, loaded at
worker import, chmod 0600 set on the temp file *before* the rename.
`origin/main` wrote `.local/settings.json`, loaded from the app lifespan,
with an explicit `PERSISTED_KEYS` allow-list and "an empty value clears".
**Decision.** Keep this branch's file location and write mechanics; adopt main's
loading point and its empty-clears semantics; adopt its allow-list.
**Consequence.** Each half of that was chosen against a specific failure.
- **`data/`, not `.local/`.** It is gitignored, bind-mounted by compose so a
  container restart keeps the values, and excluded by `.dockerignore` (`f162ace`)
  so the keys cannot bake into an image layer. It already holds `api_token`.
  `test_default_path_is_not_under_outputs` moved from pinning `/.local/` to
  pinning `/data/`; the invariant it exists for — not inside the tree
  `routes/files.py` serves — is unchanged.
- **Not loaded at import.** An import-time read of a secrets file means any test
  that imports the worker picks up the developer's real keys. `load_settings_env()`
  is called from the app lifespan instead.
- **An empty value clears the override, it is not stored as `""`.**
  `config_adapter` resolves every key as `env.get(NAME, os.environ.get(NAME, ""))`,
  so a persisted `""` would shadow a working `.env` key forever with no way to
  undo it from the UI. `test_values_are_coerced_to_strings` used to pin the
  opposite and now pins this.
- **Chmod before rename** is kept from this branch: setting the mode on the temp
  file means the real path is never briefly world-readable.

## DEC-048 — Signed expiring media URLs, not a session cookie
**Context.** After a clean 7-clip render the dashboard's player showed nothing,
its Download button saved a `.json`, and a pasted clip URL said the file was not
available. One cause: `c53949b` put `Depends(require_token)` on the whole files
router, and the token is header-only by design — but `<video src>` and
`<a href download>` are requests the *browser* makes and cannot carry a header.
All three symptoms were the same `401 {"detail": ...}`; the `download`
attribute saved that body and the browser renamed it to match its
`application/json` type. Nothing tested it: `tests/test_auth_token.py:271` pinned
`/api/outputs/...` → 401 as a *desired* invariant.
**Decision.** `GET /api/outputs/{job}/{file}` also accepts `?exp=&sig=`, an HMAC
over that one `(job_id, filename, exp)` triple keyed by `HMAC(token, context)`,
minted when a job is serialized. An `HttpOnly` session cookie was rejected.
**Consequence.** Five things, and the first is why the choice was forced.
- **The deployment is plain HTTP on a tailnet IP, from several devices.** A
  `Secure` cookie is *silently dropped* there: login appears to succeed and every
  later request 401s with no error anywhere. Dropping `Secure` puts a
  whole-API credential in cleartext across the tailnet, attached automatically to
  every request the browser can be induced to make. A signature needs no
  per-device setup — any device holding the token gets working URLs from its
  first `GET /api/jobs/{id}`.
- **The rule that the credential never travels in a URL still holds.** A
  signature is not the credential: it opens one file, expires, and cannot be
  reversed into the token. A leaked media URL reads one mp4 for a few hours; a
  leaked cookie is the whole API, `POST /api/shutdown` included.
- **Scoped by what the signature attests, not by route**, because
  `test_every_router_requires_a_token` is an AST guard that every router keeps
  its `dependencies=`. Three conditions must hold: the path is under
  `/api/outputs/`, the route has **both** a `job_id` and a `filename` path
  parameter, and the HMAC verifies with `exp` in the future. That second
  condition is what keeps `GET /api/outputs/{job_id}` — the directory listing —
  private, so its existing 401 assertion needed no change at all.
- **`exp` is quantised into buckets of TTL/2.** With `now + ttl` every response
  would mint a different URL for the same file, and handing a `<video>` a new
  `src` tears down playback and discards the cached bytes — and the dashboard
  re-fetches the job on every re-render. Bucketing makes the URL byte-identical
  inside the window, and makes the tests deterministic without freezing a clock.
- **Signed at serialization, never persisted.** `outputs/jobs.json` keeps
  unsigned URLs, so no stored record carries an expiry that outlives it, and
  holding a valid token is exactly what mints a playable URL. The new
  `thumbnail_url`/`srt_url` are likewise *derived on read* from the manifest blob,
  so the seven clips already on disk work with zero writes and no migration.
- **Rejected:** fetching the mp4 in JS with the header and feeding `<video>` a
  blob URL. It needs no backend change, and that is its only virtue: the whole
  file must be in memory before the first frame, `Range`/seeking is destroyed,
  and a 7-clip grid would buffer every clip on page load.

## DEC-049 — A font is validated by the family it declares, not by its file size
**Context.** Every clip rendered on 2026-09-21 burned its subtitles in
DejaVuSans under a log line saying `✅ All fonts prepared successfully`. Not a
`fontsdir` problem — all four burn sites pass it and it works. The configured URL
(`cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-400-normal.ttf`)
serves a 48832-byte face whose name table says **Montserrat Thin**, so libass
found no family `Montserrat` and fontconfig substituted. Every *other* fontsource
URL in the table is fine, so nothing about it invited suspicion.
**Decision.** Point Montserrat at the upstream JulietaUla project (the source the
DEFAULT style already used), and check the declared family — not just the file
size — both when accepting a cached file and after a download.
**Consequence.**
- **The URL fix alone would have changed nothing.** The gate was
  `getsize(path) > 1000`, so a wrong-but-large font is valid forever, and
  `custom_fonts/` is bind-mounted — "already cached" is the normal case on every
  machine. Checking the family is what makes the stale file get replaced.
- **PIL made this nearly inert, and a failing test caught it.**
  `ImageFont.truetype(PATH, size)` falls back to searching the *system* font
  directories for a file of the same basename when the path will not load — and
  `register_fonts_for_libass` copies these very fonts into
  `~/.local/share/fonts`. A file of pure garbage therefore reported family
  "Montserrat Thin", read from the stale installed copy. `font_family_name` opens
  the file and passes PIL the handle. A validator that can silently inspect a
  different file than the one asked about is worse than none.
- **The damage was wider than the glyphs.** PIL loads the same file *by path* in
  `clipping/studio/subtitles.py` to measure line wrapping and `\pos` centering,
  where family names never apply and nothing substitutes. Every `.ass` carried
  hairline-Thin metrics while libass drew DejaVu.
- **It fails loudly now.** `siapkan_font_tipografi` raises and names both the
  declared and the wanted family, rather than printing success.

## DEC-050 — The glitch transition is opt-in, and its cache key carries a recipe version
**Context.** A user reported a "blackscreen like bug" about a second into every
clip. It was the Hook Glitch transition. The lavfi fallback — the only path
since the pinned source video went private (`URL_GLITCH_VIDEO`) — built its noise
on `color=c=black`. `noise` adds a **signed** offset, so on pure black every
negative value clamps to 0 and only the positive half survives: 19.2/255 mean
luma, a black frame with faint speckle. `ffmpeg`'s own `blackdetect` never fired
because it is not *quite* black.
**Decision.** Base the noise on mid-grey (126.9/255, stddev 25.8 — actual
static); default the effect **off** in all five places it is defined; and put a
recipe version in the cached filename.
**Consequence.**
- **The default moves from opt-out to opt-in.** It is a one-second full-frame
  effect inserted into *every* clip. `--hook-glitch` enables it from the CLI;
  `--no-hook` is kept and still wins, so a script that disables it explicitly
  does not silently start enabling it when the default flips.
- **Five definitions, one value.** `clipping/config.USE_HOOK_GLITCH`,
  `JobCreateRequest`, `config_adapter`, the CLI and `NewJob.jsx` each carry the
  default; a test asserts they agree, because a mismatch means the dashboard and
  the CLI disagree about what a job with no explicit setting does.
- **`glitch_ready_{w}x{h}.ts` was the font bug's twin.** It is keyed by filename
  and returned unconditionally when present — exactly like the stale
  `custom_fonts/Montserrat-Regular.ttf` in DEC-049. Any machine that had
  rendered once would have kept serving the black `.ts` forever, and this fix
  would have looked inert. The key is now
  `glitch_ready_{w}x{h}_v{GLITCH_RECIPE_VERSION}.ts`, so changing the filter
  chain invalidates every cached file automatically and permanently. **This is
  the second time a filename-keyed cache silently preserved a defect through its
  own fix; treat any `if os.path.exists(x): return x` in this codebase as
  suspect.**

## DEC-051 — Clip length is a UI choice, defaulting to auto
**Context.** `platform` selects the duration window every clip is snapped into
(`clipping/analysis/presets.py` → `snap.py`): `auto` 20–75 s, `tiktok`/`reels`
15–90 s, `shorts` 15–59 s, `long` 60–179 s. `JobCreateRequest` has declared the
field since the snapper landed and it reaches `cfg`, but `NewJob.jsx` never sent
it — so every job created from the dashboard was `auto` whatever the user
intended. The reported clips came out 22–41 s, consistent with it.
**Decision.** Add a Clip Length select offering all five presets, wired into the
`jobFields` literal and the Clone & Rerun restore. The default stays `auto`.
**Consequence.**
- **Nothing is silently re-cut.** Widening `auto` instead would have changed the
  output of every existing workflow to fix a missing control.
- **Third instance of the same shape.** A field the backend declares and the
  pipeline honours, with no control in the UI that is actually deployed — after
  the AI provider select that could not reach `chain`, and the four chain
  provider keys that still have no Settings field. Worth a sweep: the backend
  contract and the dashboard drift apart silently, and only
  `tests/test_dashboard_payload_contract.py` catches the reverse direction (a
  key the page sends that the model does not declare).

## DEC-052 — The NIM default is defined once, in the registry
*(The model this entry originally named, `deepseek-ai/deepseek-v4.1-flash`,
was overturned within the hour by DEC-058. The one-definition half stands;
read the model choice below as the mistake DEC-058 is about.)*
**Context.** A job run with video only failed after 47 minutes of CPU Whisper.
Probing NVIDIA NIM with the account's own key showed why: `google/gemma-4-31b-it`
— the shipped default, benchmarked at 6.0s / 31.3 tok/s on 2026-09-21 (DEC-024)
— **returns nothing in 120s for an 8-token "reply ok" request**. It hangs on
*any* request, so every attempt rode the 330s socket timeout into the gateway's
504. The model id also had **four** definitions (`registry.py`,
`config.NVIDIA_MODEL`, `web/api/models.py`, `web/api/config_adapter.py`) with
nothing making them agree.
**Decision.** `registry.NVIDIA_DEFAULT_MODEL = "deepseek-ai/deepseek-v4.1-flash"`,
the single definition the other three reference, with an agreement test that also
asserts the negative half.
**Consequence.**
- **Measured, against the real Pass-A workload** (45 beats, strict
  `CANDIDATES_SCHEMA`, `max_tokens=700`), one request each:

  | model | result |
  |---|---|
  | `deepseek-ai/deepseek-v4.1-flash` | **1.3–2.9s, schema-valid, 296–331 tokens** |
  | `z-ai/glm-5.3-flash` | 12.6s, schema-valid — **only** with thinking off |
  | `nvidia/nemotron-3.5-lightning-30b-a3b` | 83s, reasoning prose, unparseable |
  | `z-ai/glm-5.3` | timed out at 90s |
  | `google/gemma-4-31b-it` | **hangs**, nothing in 120s |
  | `openai/gpt-oss-20b` | **hangs**, nothing in 45s |

- **DEC-021's diagnosis is falsified for the current state.** It read the 504s as
  "the request asks for too much work" and built a proposal machinery around a
  smaller clip count. An 8-token request hangs too. The proposal stays — it is
  still right when a request genuinely is too large — but a 504 from this
  provider must no longer be read as evidence about request size.
- **Listed is not callable.** `google/gemma-3-12b-it`,
  `nvidia/nemotron-nano-3-30b-a3b` and `moonshotai/kimi-k2.6` are all in
  `GET /v1/models` and all answer `404 Function <uuid>: Not found for account
  <id>`. Reading the catalogue proves nothing; only a real request does. The
  READMEs said "list current ones at /v1/models" and now say otherwise.
- **The two fastest candidates are reasoning models and are unusable with
  thinking on** — deepseek returns `content=null`, GLM spends all 700 tokens on
  the preamble and truncates the JSON mid-object. `llm._extra_body` already
  switches it off for models whose name contains `deepseek`, which is the only
  reason this default works, and a test pins that the shipped default is covered
  by that predicate. **A future default that is also a reasoning model but is not
  called "deepseek" would silently lose the switch.**
- **Amends DEC-024's correction.** DEC-024 said a pinned string cannot detect a
  retirement and that what survives one is the chain. Both still hold, and this
  job sharpened them: gemma was *not* retired, *not* 410 Gone, and still in the
  catalogue — it simply stopped answering, which no test on a string and no
  reading of a model list can ever catch. Only a real request can, which is what
  DEC-056 is for. And the corollary DEC-024 did not state: **a chain with one key
  is a chain of one.**

## DEC-053 — The chain runner's budget check is predictive, against the client's own timeout
**Context.** DEC-020 chose a predictive check — *could this attempt outlast the
budget* (`elapsed + REQUEST_TIMEOUT > BUDGET`) rather than *is the budget already
spent*. It was implemented in the legacy monolith (`engine.py:1164-1168`) and
**never carried into the chain runner**. `llm.py`'s only in-ladder guard weighed
the 4s/12s **backoff sleep** against the deadline while the request itself ran
for up to 330s, so a third attempt started at 614s against a 900s deadline and
ended at ~925s — 25 seconds past a budget whose entire purpose was to stop that.
**Decision.** `registry.effective_timeout(link, override)` is the single source
of the per-request timeout; `LlmClient` reads it too. `run_chain` refuses a link
whose request cannot fit, and `_run_link` refuses an attempt, **before** the
`attempt N/M` line is printed.
**Consequence.**
- **The check and the socket cannot drift apart.** That they could is what made
  the old guard decoration rather than policy, and a test asserts the equality
  for every provider in the registry.
- **The refusal is per link, not a blanket abort.** With 200s left, a 330s NVIDIA
  request is refused while a 120s Groq one is still tried. Aborting the chain
  there threw away a provider that was ready to answer.
- **A refused attempt is never announced.** `web/api/signals.py` turns every
  `attempt N/M` line into a retry the dashboard shows, so counting an attempt
  that was never made would report a retry that never happened — the same class
  of misreporting DEC-019 was written about.
- **The provider's own error still propagates.** A 504 says the gateway gave up;
  "out of budget" says only that we stopped asking, and the caller needs the
  first. Stopping early must not replace the diagnosis with a stopwatch reading.
- **It is deliberately pessimistic.** The check uses the *timeout*, not the
  observed latency, so a provider that normally answers in 2s is refused when
  fewer than 330s remain. DEC-020 chose that conservative form on purpose; the
  reason is printed with its arithmetic rather than left to be inferred.

## DEC-054 — The time budget is hierarchical: run → pass → window
**Context.** DEC-027 states the property the three-pass split was supposed to
buy: *"A failure is local. A failed window loses one window's candidates."* It
was not true. `analyze()` computed **one** absolute deadline for the whole run
and handed it unchanged to every request, and `_pass_a` had no per-window
allocation at all. When window 1's provider hung for three attempts it spent all
900s, and windows 2–6 were skipped without ever being contacted.
**Decision.** Pass A allocates per window, recomputed at the top of each
iteration: `share = remaining / windows_left`, `window_deadline = min(pool,
now + max(share, floor))`. `PASS_A_BUDGET_SHARE = 0.7` holds time back for the
later passes. `run_chain`'s signature is unchanged.
**Consequence.**
- **Unused time returns to the pool by construction.** Because `remaining` and
  `windows_left` are recomputed each iteration, a window answering in two
  seconds simply leaves a larger `remaining` behind it. A ledger of banked
  seconds was the alternative: more state, same result, one more thing that can
  go stale.
- **The floor is load-bearing and is one full request against the slowest
  *keyed* link.** Without it, 900s over six windows is 150s each — below NVIDIA's
  330s timeout — so DEC-053's check would refuse **every** window and the run
  would issue no requests at all. That trades a starvation bug for a never-tries
  bug, and DEC-020 already forbade it: *"a slow-but-healthy call is never cut off
  — cutting one off would be a regression dressed as a fix."* Keyless links are
  excluded, since a provider that is never contacted costs no time and counting
  its timeout would shrink every window's allowance for nothing.
- **Pass A does not get the whole pool.** A pass A that consumes everything
  leaves the re-rank falling back to the scan's own scores and every clip
  rendering with a basic title — a silent quality loss of exactly the kind
  DEC-021 was written about. This is the change's one heuristic; its rollback is
  `PASS_A_BUDGET_SHARE = 1.0`.
- **Measured on the recorded failure, through the real chain runner:** 1 HTTP
  request instead of 3, 302s instead of 925s. With a healthy provider all five
  windows scan in 78s of the 900s budget — unchanged.
- **Amends DEC-027.** Locality has to hold for the shared *time budget*, not only
  for exception handling. Catching a window's exception is worthless if that
  window has already spent everyone else's time.
- **Known imprecision, stated rather than papered over:** `pacing.Limiter.acquire`
  sleeps inside a window's share without the deadline knowing. Because the floor
  is a *request* timeout and the skip is predictive, that sleep eats borrowed
  time rather than a guaranteed minimum and can never cause an overrun.

## DEC-055 — A provider failure and an empty transcript are different errors
**Context.** When every scan window failed, the run died with *"No clippable
moment was found anywhere in this transcript. That can mean the video is all
housekeeping, or that the transcript does not match the video."* Nothing had been
analysed. `_pass_a` caught each window's exception, logged it, and **discarded
the reason**, returning an empty list that meant both "every window answered and
found nothing" and "no window ever answered".
**Decision.** `_pass_a` returns a `ScanStats(total, answered, failed, skipped,
last_error)` alongside the candidates, and `analyze()` branches on whether
anything was actually read.
**Consequence.**
- **`answered == 0` gets its own message**, naming the provider error and the
  counts, with no sentence about the transcript at all. The old text is kept for
  the case it was actually written about, with a note appended when some windows
  were lost.
- **`_report_shortfall` gets the same treatment.** Its docstring already argued
  DEC-021's *"a supply limit is not a provider limit"*; it simply had no way to
  tell which one it was looking at, and so blamed the transcript for both.
- **It landed before DEC-054 deliberately.** The per-window split *creates*
  skipped windows; shipping it first would have reproduced this bug in a new
  disguise.

## DEC-056 — The chain is asked whether it answers, before anything expensive runs
**Context.** `missing_provider_key` refuses a chain where *no* link has a key —
correctly, since a partly-configured chain should degrade rather than fail
(DEC-023). It cannot refuse the case that happened: one link had a key, the gate
passed, Whisper ran for 47 minutes on CPU, and only then did the analysis
discover that the one keyed provider answered nothing at all. **A key proves a
provider was configured, not that it is alive.**
**Decision.** `llm.probe_chain` asks each keyed link, in order, an 8-token
question with a 45s timeout, stopping at the first that replies. The job fails
before ingestion only when **nothing** answers. `--no-preflight` opts out, and a
render-only rerun skips it as it already skips the key gate.
**Consequence.**
- **Measured against the real endpoints:** the dead model is caught in **21s**
  instead of 62 minutes; the live one answers and the job proceeds.
- **45s is measured, not chosen by taste.** Five probes of the shipped default
  ran 1.3 / 1.6 / 2.2 / 2.7 / 11.4s — a healthy free tier is usually instant and
  occasionally slow to wake — while the dead model does not answer in 120s, so
  the gap is not close. The cost of being wrong is asymmetric: too short fails a
  job whose provider was merely cold; too long still catches a dead provider
  sixty times faster than the failure it replaces.
- **It proves liveness, deliberately not suitability.** `z-ai/glm-5.3-flash`
  answered this same ping in 0.67s and still failed the real Pass-A request,
  spending its whole token budget on a reasoning preamble. Suitability is what
  `tools/bench_llm.py` is for, and the limit is written in the docstring so the
  check is not mistaken for more than it is.
- **A failing link is reported, never removed.** DEC-003 and DEC-023 make the
  chain a list the user wrote down; a runtime edit to it is the silent fallback
  both forbid.
- **No schema, no negotiation.** Involving the structured-output ladder would let
  a provider's `json_schema` support decide a liveness question, and would cost
  several requests where one is the point.
- **Only the chain path is gated.** The legacy single-provider paths are an
  escape hatch, not somewhere to add a new gate.

## DEC-057 — Every secret the backend accepts has a field on the Settings page
**Context.** Groq is the **first and fastest** link in the default chain.
`web/api` has accepted `groq_api_key` since the chain landed, `settings_store`
persists `GROQ_API_KEY`, and `SettingsResponse` reports `groq_api_key_set` —
there was simply no box to type it into, and the same for OpenRouter and
Mistral. That is why the failed job had a single point of failure: of three
links, exactly one had a key, and that one was the slowest and, on the day, dead.
**Decision.** Add the three fields, Groq first because that is its position in
the chain; and add the guard that ends the pattern.
**Consequence.**
- **This is the fourth instance of one shape** — a field the backend declares
  with no control in the deployed UI — after the AI provider select that could
  not reach `chain`, `platform` (DEC-051) and `nvidia_model`. DEC-051 called for
  a sweep; this is it, made permanent as a test.
- **The guard checks both halves.** A secret that `SettingsRequest` declares and
  `settings_store` persists must have an input that **sends** it *and* a badge
  that reads its `_set` flag, because a control that cannot tell the user whether
  a key is already saved is barely a control. The reverse direction is guarded
  too: a key the page sends that the model does not declare is dropped in
  `model_dump()` and does nothing.
- **It reads the page as text.** Importing `web/api` needs pydantic, which CI
  does not install; an `importorskip` on a drift guard means it never runs in the
  one place that checks every push (DEC-012, and the DEC-050 precedent).
- **`.env` carried the same class of trap.** It declared `NVIDIA_API_KEY` twice —
  empty at line 15, real at line 29 — and both dotenv and docker-compose resolve
  that to the **last** one. Someone fixing the "empty" key at the top would have
  changed nothing, and a parser that took the first would have blanked it
  silently. It now declares every key exactly once.


## DEC-058 — A NIM model is picked on whether it finds clips, not on whether it replies
**Context.** DEC-052 replaced a model that answered nothing with
`deepseek-ai/deepseek-v4.1-flash`, chosen on two measurements: latency (1.3–2.9s)
and schema-validity. Running the **actual failed job** showed it answers
`{"candidates": []}` in **seven tokens** on every real transcript — including
`outputs/c135b9d76f99`, which had already yielded seven clips — at every
structured-output level (`json_schema`, `json_object`, prompt-only), with
thinking on or off, at max_tokens 700, 3000 and 6000. The synthetic probe that
had validated it used fabricated repetitive beats, which it happily labelled.
**Decision.** The default is `nvidia/nemotron-3.5-lightning-30b-a3b`.
`tools/bench_llm.py --nim-shortlist` measures **candidates returned**, not only
latency and validity.
**Consequence.**
- **Fast, schema-valid and useless is still useless.** Every guard this project
  had — the strict schema, the negotiation ladder, the retry classification, the
  new liveness probe — passed a model that could not do the work. Each of them
  answers "did a well-formed reply arrive", and none answers "was it any good".
  Only the real workload does.
- **This is the exact limitation DEC-056 documents, arriving one commit later.**
  The probe's docstring says it proves liveness and deliberately not suitability.
  That warning was written and then not heeded in the very next decision. A
  documented limitation is not a mitigation.
- **The replacement had already been rejected, by a missing flag rather than by
  its own behaviour.** `nvidia/nemotron-3.5-lightning-30b-a3b` was benchmarked as
  "73.0s → prose, not JSON" (DEC-024) and again as "83s, reasoning prose,
  unparseable" (DEC-052). `llm._extra_body` turned thinking off for models whose
  name contained `deepseek` **and nothing else**. With the flag it answers the
  same request in ~20s with usable candidates. `_NIM_REASONING_FAMILIES` now
  lists the three families measured to need it — deepseek returns
  `content=null`, nemotron-3.5-lightning returns prose, glm truncates the JSON
  mid-object — and a test asserts each is covered. **Check that list before
  judging any new NIM candidate; two benchmark rounds disqualified a working
  model over a one-word predicate.**
- **NIM access is far narrower than the catalogue suggests.** Of 18 models
  probed for this decision, ten answered `404 Function <uuid>: Not found for
  account`, four hung or timed out, one returned malformed JSON, and one
  returned nothing useful. One worked.
- **Verified end to end**, against the job that started this: 5 clips in 590s
  from the transcript whose run previously died after 63 minutes claiming the
  video was "all housekeeping".

## DEC-059 — A granted time slice must survive the clock moving before it is measured
**Context.** DEC-054's floor exists so a window is never handed less than one
full request's worth of time, because a window that cannot fit a request is
refused and the run makes none. Granting **exactly** `floor` did precisely that:
`run_chain` reads the clock again — after the beats are rendered, the prompt is
built and the chain's keyless links are walked — so `left` is a few microseconds
under `floor` and `need > left` is true. On the real job every window but the
last was skipped with *"a 330s request does not fit the 330s left in the time
budget"*.
**Decision.** `GRANT_MARGIN_SECONDS = 1.0`; a window is granted, and required to
have, `floor + margin`.
**Consequence.**
- **The never-tries bug was reintroduced by rounding**, inside the very
  mechanism written to prevent it, and no unit test caught it because the fake
  runners trusted the allowance they were handed instead of reading the clock
  themselves. The regression test now mimics `run_chain` faithfully: it re-reads
  the clock and refuses on its own arithmetic.
- **It was found by running the real job, not by the suite.** Both defects in
  this round were. A green suite and a live run answer different questions.

## DEC-060 — The candidate's description travels with the span
**Context.** `snap()` rewrites `b0`/`b1` while growing a candidate toward
`preset.target` and while trimming one back under `preset.max`. Growing fires on
nearly every candidate. `_pass_b` looked each candidate's `gist` and `kind` up in
`{(c["b0"], c["b1"]): c}` — keyed on the ids it had sent *in* — so the lookup
missed whenever a boundary had moved, and the whole-video re-rank was reading
`#3 [34s] score=88 clip` with an empty gist for most of the field. Live since
the three-pass split shipped (DEC-027); invisible because no test asserted
prompt content.
**Decision.** `Span` carries `gist` and `kind`, appended last with defaults so
the five-positional-field construction in three call sites keeps working.
`snap_all` accepts candidate dicts as well as bare triples. `snap` passes both
through and reads neither.
**Consequence.**
- **The timing-only charter (DEC-028) is intact.** Snapping still decides
  nothing but boundaries; the two new fields are an opaque payload.
- **Pass C is fed the same two values**, which `clip_meta_prompt` had accepted
  since it was written and nothing had ever passed.
- **A key derived from mutable state is the bug**, not the missing passthrough.
  Anything that must survive `snap` travels with the span from now on.

## DEC-061 — The re-rank sees the hook line, and variety is enforced in Python
**Context.** Pass B chose which moments ship from a duration, a score and a
twelve-word gist written by a different request. The words the clip *opens on* —
what actually decides whether a stranger keeps watching — were never shown to
it. Separately, "prefer variety" was an instruction, and models agree with it
and still return five versions of the strongest point.
**Decision.** Each candidate line carries the first fifteen words of its `b0`
beat, truncated not summarised. `RANKED_SCHEMA` gains a required one-word
`topic`, and `_enforce_variety` demotes a pick repeating an earlier pick's
topic.
**Consequence.**
- **Demotion, not deletion.** A repeat loses its slot only to something
  different, and is backfilled when there is nothing different to promote. That
  is what keeps DEC-021 true: a video genuinely about one subject still yields
  the count that was asked for, and the log says so.
- **A blank topic is an unknown, not a match.** Treating two blanks as the same
  subject would silently cost a clip every time a weaker model omitted the field.
- **`topic` is not editorial output.** Nothing renders it; it exists so a rule
  can be enforced rather than requested. `MAX_TOKENS_RANKED` 400 → 600.

## DEC-062 — Prompts put the data first, and the scan is told what the video is
**Context.** Thirty lines of instructions arrived before any transcript, so every
rule was a rule about nothing until the model reached the bottom. The 1-100 score
had no scale, so each window scored from its own private sense of the range and
pass B then compared those numbers across windows as if they agreed. And every
window judged "would this stand alone to someone who has not seen the rest"
without knowing what the rest *was*.
**Decision.** Beats before rules, in both pass A and pass B. Numbered rules
rather than prose. A four-band `SCORING` rubric ending "below 50, do not return
it at all". One good and one bad worked example, marked invented. A
`video_context` preface: length, language, and an optional `--topic`.
**Consequence.**
- **Nothing here costs a request.** Length and language are already in hand and
  the topic is typed by the uploader. An auto-summarised topic was rejected: one
  more request and one more failure point per run for a line a human can type.
- **An unset topic prints no heading**, so the prompt never carries a label with
  nothing under it.
- **`PROMPT_VERSION` now exists** and is bumped whenever wording changes, so
  anything caching a reply can tell the question changed.

## DEC-063 — The pass that read the clip names its hook lines; C runs warmer
**Context.** `hook_v2_items` chose its cards with a heuristic — the shortest
beats with the most words, a proxy for density rather than for the line worth
putting on screen — immediately after a request in which a model had read the
whole clip and could simply be asked. Separately, every pass ran at
`run_chain`'s analytic 0.2, including the only pass that writes anything a
person reads.
**Decision.** `hook_beat` (one id) becomes `hook_beats` (1-3, strongest first).
The first drives the teaser window; all of them feed `hook_v2_items`, whose
heuristic remains the fallback. `ANALYTIC_TEMPERATURE = 0.2` for A and B,
`WRITING_TEMPERATURE = 0.5` for C.
**Consequence.**
- **Amends DEC-030.** `hook_v2` moves from purely derived to model-informed
  with a deterministic fallback; ids, never timestamps, so DEC-028 holds.
- **The render contract is untouched.** `studio/core.py` reads only
  `start_time`/`end_time` per item, and cards are sorted chronologically
  whatever the source, because a sequence jumping backwards reads as a mistake.
- **The temperatures are named for what they are for**, not spelled as bare
  numbers at the call sites.

## DEC-064 — Every prompt lives in `prompts.py`, in English
**Context.** `voiceover.get_commentary_prompt` was written *in* Indonesian, with
its style and length tables duplicated in English behind `if language == "en"`.
That is the shape the analysis prompts were rewritten out of: instructions in
the output language pull the answer toward that language whatever the video, and
two parallel tables drift. It also sat in a module importing `google.genai` and
`edge_tts`, so it could not be imported in the pytest-only CI environment
(DEC-012) and had no coverage at all.
**Decision.** `prompts.commentary_prompt` — English instructions, one style
table, one length table, output language as a parameter.
`voiceover.generate_commentary_script` imports it at function level.
**Consequence.**
- **The dependency is one-way.** `prompts.py` stays stdlib-only, which is
  precisely what made twelve tests possible where there had been none.
- **The guard that the old copy is gone reads `voiceover.py` as text.** An
  `importorskip` guard never runs in the one environment that checks every push.
- **`--voiceover-lang` still offers only `id` and `en`.** The prompt is now more
  capable than the flag; widening it is a separate change.

## DEC-065 — A cut may not open on the back half of a sentence, where that is knowable
**Context.** The most recognisable sign of an auto-generated clip is one that
starts mid-thought, and `snap()` *creates* that as often as it inherits it: it
grows backwards into the previous beat when a candidate is short.
**Decision.** `beats.starts_mid_sentence` requires **both** signals — the beat
opens lower case, and the previous beat did not end in sentence punctuation —
and is applied to the snapped `b0`, after `snap_all` and before `dedupe`.
**Consequence.**
- **Either signal alone is wrong.** A sentence can legitimately open lower case
  (`iPhone sales fell by a third.`), and a clean stop before it is the stronger
  evidence.
- **Two valves, both load-bearing.** `has_punctuation` gates the guard off below
  20% of beats ending in punctuation, because auto-captions carry none and every
  candidate would look like a fragment; and if the guard would remove *every*
  span it removes none and prints the override. A heuristic may not quietly
  conclude a video has nothing in it (DEC-021).
- **An uncased script answers neither signal.** `"这"` and `"ه"` are neither
  upper nor lower, so CJK and Arabic return False rather than having every
  candidate rejected. No fixture in this suite had either shape before.
- **The summary line gained a step rather than absorbing one.** Counting these
  rejections under "that fit" would report duration failures that never happened.

## DEC-066 — Pass A remembers each window, keyed on everything that could change the answer
**Context.** Pass A is where the time goes: one request per ~45-beat window,
~90s each against the last link in the shipped chain. Every rerun re-paid for
all of it — and reruns are the common operation (Clone & Rerun, a changed
render flag, a different clip count). Only the finished clip list was
cacheable, whole-or-nothing, behind `--load-gemini-json`.
**Decision.** `clipping/analysis/cache.py` stores each window's cleaned
candidates under `sha256(window text + salt)`, where the salt carries
`PROMPT_VERSION`, the chain spec, the preset and `MAX_CANDIDATES_PER_WINDOW`.
**Consequence.**
- **A stale entry is not something that can be served.** It is a different
  key, so a reworded prompt or a swapped model invalidates the store without
  anyone remembering to clear it. That is why `PROMPT_VERSION` exists.
- **The lookup precedes the budget check.** A hit costs no request and no
  time; refusing one for want of time would refuse something free. It counts
  as `answered`, because the window *was* read, just not today (DEC-055).
- **Bounded and oldest-first.** The CLI writes every run into one shared
  `outputs/`, unlike the per-job web path, so without a cap the file grows for
  the life of the install.
- **Never a gate.** A cache that cannot be read is a cold cache; one that
  cannot be written is a run no slower than it would have been. The save is
  atomic, and its own cleanup cannot raise — `os.unlink` rejects a path with an
  embedded NUL before touching the filesystem, so catching only `OSError` let
  the error handler throw out of a function whose contract is that it cannot.

## DEC-067 — Preflight does real work when there is real work to ask about
**Context.** DEC-056 chose an 8-token ping and said in its own docstring that
liveness is not suitability. The gap is measured, not theoretical: `glm-5.3-flash`
answered that ping in 0.67s and then spent its whole token budget on a reasoning
preamble, and `deepseek-v4.1-flash` answered every real transcript with
`{"candidates": []}` in seven tokens. Both passed preflight.
**Decision.** When a transcript is already on disk — `--transcript`, or
DEC-022's saved `transcript.vtt` — the probe sends window 1's actual pass-A
request, and the answer seeds the DEC-066 cache.
**Consequence.**
- **The Whisper path is untouched.** There is no transcript yet when preflight
  runs, which is the entire reason it runs there; the ping stays, and so does
  the 47-minutes-of-CPU-Whisper guarantee.
- **A failed work probe falls back to the ping before the link is called
  dead.** A provider too slow for a 90s budget but alive on a ping is alive,
  and DEC-020 forbids cutting off a slow-but-healthy call.
- **Zero candidates is LIVE.** One window's empty answer is an opinion about
  45 beats, not evidence about the endpoint.
- **Amends DEC-056 on the negotiation ladder.** That decision kept the ladder
  out so a provider's `json_schema` support could not decide a liveness
  question. It no longer can: the ladder's bottom rung is prompt-only, so only
  a link that answers at *no* rung has failed.
- **A failing link is still reported, never removed** (DEC-003, DEC-023).

## DEC-068 — The single-request analysis path is deleted; `openai_compat` is re-expressed
**Context.** `engine.get_analysis_prompt` asked one model for 22 required
fields per clip. At ~1200 output tokens each against a measured 12-13 tokens/s
and a ~300s gateway it could not finish, and it exceeded Groq's
tokens-per-minute limit outright; every job that ever ran it failed. It had been
unreachable behind `--ai-provider nvidia|gemini` since the chain landed, and its
330-line Indonesian prompt was the first thing anyone found when they went
looking for "the prompt".
**Decision.** Delete it — `engine.py` 1588 lines to 253 — along with
`tests/test_nvidia_retry.py`. Keep transcription, the CPU-Whisper warning and
the transcript re-exports. `--ai-provider openai_compat` becomes
`apply_openai_compat_alias`, which rewrites the setting into a one-link
`custom/<model>` chain at config time.
**Consequence.**
- **Amends DEC-046.** That decision kept two custom-endpoint paths so
  collapsing them could not silently change an existing `OPENAI_COMPAT_*`
  setup. The path it protected is gone; the *surface* it protected is not —
  three env vars, three Settings fields (DEC-057) and the fail-fast gate all
  still work.
- **Not the runtime chain-editing DEC-003/023 forbid.** The rewrite happens at
  config time, over values the user wrote down, and prints what it built.
- **One helper, both callers.** The CLI and `config_adapter` call the same
  function, because this file has form for drifting from its API — and did
  again here: the alias was wired into `build_config` alone and a web test
  caught it.
- **`nvidia` and `gemini` stay in `PROVIDER_KEYS`.** They are ordinary chain
  links and the shipped default names both; only their `--ai-provider` meaning
  went. argparse now *rejects* the removed values, so an old command line fails
  loudly rather than quietly running something else.

## DEC-069 — The karaoke highlight colour is a setting; the colour it reverts to is not
**Context.** `&H00FFFF&` was hardcoded in two branches of `subtitles.py`, each
with its own copy of the highlight/reset pair. It was the one visual choice
people ask about and the one they could not change.
**Decision.** `KARAOKE_HIGHLIGHT_COLOR`, `--karaoke-color` and a
`karaoke_color` job field, validated in both places.
**Consequence.**
- **`KARAOKE_BASE_COLOR` is named but not exposed.** Changing it would render
  every word in the highlight's off-state.
- **It is a different knob from `warna_kata_khusus`**, which styles the
  AI-chosen emphasis words when karaoke is *off*. They now sit together with a
  comment saying which is which; I confused them myself earlier in this work.
- **Validated because libass fails silently.** An override it cannot parse is
  ignored, so an unvalidated typo would give subtitles with no highlight, no
  error, and nothing in the log.
- **Verified by render, not only by the text scan.** `studio/` has no automated
  coverage and cannot be imported in CI (DEC-012), so the evidence is a `.ass`
  built from the same transcript against this tree and the previous commit:
  byte-identical, md5 `e1cce3845a1d7495d4ae2972ae998bbc`. With
  `--karaoke-color &H0000FF&` the same render swaps all 56 occurrences, so the
  setting is not inert either.
- **The scan matches `&H00FFFF&` with its trailing ampersand.** `subtitles.py`
  also holds `&H00FFFFFF`, the eight-digit base colour in the `[V4+ Styles]`
  line; a looser pattern would have demanded a change that breaks every
  subtitle.

## DEC-070 — What the analysis decided is kept, including when it failed
**Context.** Every window had a status and every snap rejection had a reason.
Both went to the console and were lost, and the console truncates the rejection
list on purpose — five and a count.
**Decision.** `outputs/<job>/analysis_trace.json`: per-window status, error and
candidates; every rejection; what was selected with its gist, kind and beat
range; and the run's `ScanStats`. Versioned, best-effort, atomic.
**Consequence.**
- **Written on the failure paths too**, which was not the original plan and is
  the better half of the change. A run that raises "none fit the duration
  window" or "the video was never analysed" is exactly the run someone comes
  looking for reasons about, and it used to leave nothing behind at all.
- **A record of what happened may not change what happened.** An unwritable
  directory logs a line; the clips are returned regardless.
- No route and no UI. This is the data a "why this clip, and why not that one"
  view would need.

## DEC-071 — Scan windows run in batches, and the share is computed per batch
**Context.** Pass A is ~70% of analysis wall time and its windows are
independent, but they ran strictly one after another.
**Decision.** Up to `--analysis-workers` windows in flight, **default 1**,
capped 3. Per batch, `remaining` and `windows_left` are computed **once** and
every member is handed the same deadline — the allowance it would have had as
the first window of a sequential iteration.
**Consequence.**
- **The default is 1 because the measurement said so.** Same transcript, same
  NVIDIA key, cache off: `workers=1` used 330s of pass A's budget for four
  windows, `workers=2` used **346s** for the same four. Two overlapping
  requests should have taken ~180s. They did not overlap at all — NVIDIA's
  free tier serialises requests on one key, so a batch cost the sum of its
  members and the run was 16s *worse* for the scheduling. Shipping 2 as the
  default would have been shipping an unmeasured change, which is the mistake
  DEC-058 is about.
- **The flag still earns its place.** The shipped chain's *first* link is Groq
  — fast, 30 requests/minute published — and that is where this should pay.
  There is no Groq key on this box, so it is untested and therefore opt-in.
- **A batch cannot overspend the pool**, because N ≤ windows_left. With
  `workers = 1` the arithmetic is exactly DEC-054's, no threads are created,
  and logs stream live — so the rollback is a real rollback, asserted by a test
  that compares the schedules.
- **Order is decided on the main thread.** ScanStats is counted there,
  candidates are merged by window index, and each worker's log lines are
  buffered and replayed in order — so pass B's numbering, the DEC-070 trace and
  the activity feed do not depend on which provider answered first.
- **`_NEGOTIATED` is now locked.** The race was never corruption — a dict write
  is atomic in CPython — it was two threads meeting the same unknown model and
  both paying to walk the ladder.
- **DEC-054's stated imprecision gets worse by a factor of N.** `Limiter.acquire`
  sleeps inside a window's share without the deadline knowing. That is why the
  cap is 3 and the default is 2: the ceiling here is the provider's rate limit,
  not this machine.
- **The three DEC-054 proofs are pinned to one worker rather than rewritten.**
  They are the evidence for that decision; new batch tests sit beside them.
  `scripted()` replays answers by call order, which is a race under threads, so
  the concurrency tests use a runner that answers from what it is asked.

## DEC-072 — The liveness probe's timeout is per provider; NVIDIA's is 120s
**Context.** DEC-056 set one 45s probe cap for every provider, from five probes
of a *different* NIM model that ran 1.3–11.4s. Measured 2026-09-23 against the
shipped NIM default, with a working key: `GET /v1/models` answered 200 in
0.07s and listed the model, and the probe's own 2-token "reply with ok" request
answered **ok** in **48.9 / 57.0 / 49.7s**, then 39.1s and 57.4s on later
runs. Nearly all of that was time to first byte, meaning queue wait. So the
45s cap sat below the provider's *healthy* latency and failed a job whose only
keyed link was alive. `PROBE_WORK_TIMEOUT_SECONDS = 90` could not be met
either, at ~12 tokens/s plus a ~50s queue.
**Decision.** `Provider.probe_timeout` in the registry: nvidia 120, custom 60,
every hosted fast tier 45 (the default). It is resolved by
`registry.probe_timeout(link)`. The work probe is capped at
`work_probe_timeout(link) = min(effective_timeout(link), 2 × probe_timeout(link))`.
That is exactly the old 90s for every provider but NVIDIA, which gets 240s.
`probe_chain(timeout=None)` resolves each link's allowance; an explicit number
still overrides every link.
**Consequence.**
- **The margin is thinner than DEC-056's, on purpose.** 45s over 11.4s was 4×;
  120s over 57.0s is 2.1×, from n=5 on one day. Being too short is the total
  failure this fixes; being too long costs 120s.
- **A dead NIM model now costs 120s, not 45s,** and "no reply in 120s" is
  exactly how `registry.py` describes the dead-model case. This is bounded:
  primary links are probed first at 45s each, and DEC-073 means NVIDIA is
  rarely the only keyed link.
- **The 0.07s catalogue check was rejected on evidence.** Ten NIM models appear
  in `GET /v1/models` and answer `404 Function … Not found for account` on a
  real call. A catalogue probe would pass every one of them.
- **This supersedes only the 45s half of DEC-056.** Liveness is still not
  suitability, the probe still sends no schema, a failing link is still
  reported and never removed, and the check still runs only on the chain path.
- The test that asserted one global timeout on an nvidia link *was* the bug.
  It now asserts the invariant it was protecting: a probe never waits as long
  as a request. A new client fake honours its timeout, because the old one did
  not, so no test could tell 45 from 120.

## DEC-073 — A chain whose named primary links are keyless is refused before it starts, with an explicit override
**Context.** DEC-023 says a partly configured chain degrades instead of
failing. What "degraded" means here is now measured. With only
`NVIDIA_API_KEY` set, the default three-link chain is a chain of one at ~12
tokens/s behind a queue. DEC-071 measured pass A at 330s for four windows,
with windows 5–6 skipped by the time budget. That is not a slower job; it is a
job that cannot finish. The user found out 93s into preflight.
**Decision.** `clipping.config.chain_readiness(chain, keys, …)` is a pure
function with no cfg, filesystem or network. It refuses exactly one case: the
chain **names** a `primary` provider, **none** of the named primaries has a
key, and a non-primary link does. `primary` is a registry field: groq, gemini,
openrouter, mistral and custom are primary, and nvidia is not. Three places
enforce it:
the CLI (after the key gate, before the probe), `POST /api/jobs` (before
`store.create_job`), and the worker (as a backstop). The override is
`--allow-slow-chain`, `ALLOW_SLOW_CHAIN=1`, or the Settings toggle, all of
which become `cfg.allow_slow_chain`.
**Consequence.**
- **DEC-023 is amended for one named case, not overturned.** The chain is never
  reordered, edited or trimmed. The job is refused at the start instead; what
  would run is unchanged.
- **The rule is scoped to links the user already listed.** `LLM_CHAIN=nvidia/…`
  names no primary. The user wrote that list, so it runs.
- **"Primary" is a registry flag, not a brand list in `config.py`.** The
  registry already tracks per-provider facts and their churn. Since June 2026
  Cerebras, GitHub Models, Together and SambaNova all dropped or gated their
  free tiers. A hardcoded `("groq", "gemini")` would have been one more copy
  to forget. OpenRouter and Mistral count as primary on their published free
  tiers. They have **not** been benchmarked against the real pass-A request
  (see ASSUMPTIONS).
- **It is pure because the creation route must answer before a job exists.**
  `build_config_from_payload` creates the job's output directory, and a
  refused job must not leave one.
- **"No key at all" is deliberately left to `missing_provider_key`,** which
  gives the better message. A test checks both functions on the same cfg, so
  the handoff cannot open a gap where a keyless job passes both gates.
- **Render-only reruns are exempt** everywhere, as with the key gate and the
  probe.
- **The message carries the free signup URLs,** read from the registry
  (`Provider.signup_url`). A test asserts each one appears verbatim in
  `.env.example`, which serves as its oracle rather than as another copy.
- **The dashboard shows the server's verdict**
  (`GET /api/settings → chain_blocked_reason`) instead of re-deriving the rule
  in JavaScript. A JavaScript copy would have blocked a saved
  `LLM_CHAIN=nvidia/…` that the server allows.
- `ALLOW_SLOW_CHAIN` is persisted but is not a secret. Turning it off stores
  `""` and never `"0"`: a stored "0" would read as off but shadow a `.env`
  value forever (DEC-043).

## DEC-074 — `POST /api/settings/test-chain` pings every link, off the event loop
**Context.** The only way to learn a link was dead was to start a job and wait.
**Decision.** A route reuses `llm.probe_chain`, the preflight's own primitive.
It does not use `preflight_chain`, which honours `--no-preflight`, defers to
the key gate and seeds the scan cache. `probe_chain` gains
`stop_at_first=True`; the diagnostic passes `False`.
**Consequence.**
- **A diagnostic that stops at the first live link is not a diagnostic.**
  Reporting "Groq ✅" says nothing about the NVIDIA link. `stop_at_first` is
  ignored for a work probe, whose answer is used and must not be paid for twice.
- **`asyncio.to_thread`, not `worker._executor`.** That pool is sized to
  `MAX_CONCURRENT_JOBS` (default 1), so reusing it would let a click stall a
  queued job.
- **`wait_for` cancels the await, not the thread.** On timeout the probe runs
  to completion and its result is discarded. Each SDK client has its own
  timeout, so the thread always ends. The budget is
  `sum(probe_timeout(link)) + 10`, capped at 300s, so it derives from DEC-072
  and cannot drift from it.
- **A lock refuses a concurrent test (409)** rather than spending the free-tier
  quota twice.
- **The request has no base URL field** (pinned by a test).
  `custom/<model>` resolves `LLM_CUSTOM_BASE_URL` from the environment, so the
  route cannot be aimed at an arbitrary host.
- **`ready` = something answered AND a job may start.** A live NVIDIA link
  alone reports `ready: false`, with the DEC-073 message.
- The worst case holds a request for about 210s. The Vite dev proxy already
  disables its timeouts for uploads. If a reverse proxy cuts it off, the
  upgrade path is SSE through `probe_chain`'s existing `on_log` seam.

## DEC-075 — A job is cancelled cooperatively, and its child processes are killed
**Context.** DELETE flipped a job's status while its worker thread carried on,
spending free-tier quota and CPU. A thread cannot be stopped from outside, and a
clip spends minutes inside one ffmpeg call. The stdout tee must never raise
(DEC-014, second entry), and `clipping/` takes no web callbacks.
**Decision.** `clipping/cancel.py` gives the pipeline a token (`cfg.cancel_token`)
checked before each step that spends: chain link, attempt (before it is
announced, DEC-053), schema re-ask, backoff and rate-limit sleeps, probe, STT
chunk, Whisper segment, and, in the worker, each stage and clip. `Cancelled`
derives from BaseException so the pipeline's `except Exception` blocks cannot
record it as a provider failure and retry. `web/api/children.py` installs a
process-wide `Popen` subclass that attributes each child to the job whose thread
spawned it (the tee's pattern), refuses a spawn on a cancelled token before exec,
and re-checks after registration to close the race with `kill`.
**Consequence.** After Cancel no new request, chunk or subprocess starts, and
ffmpeg dies at once (measured 0.2s live). What is already in flight finishes or
times out: an LLM request (120s Groq, 180s Gemini and others, 330s NVIDIA, up to
3 at once with analysis workers), an STT chunk (300s), a Whisper decode window.
pyannote diarization and the server-side yt-dlp download are not cancellable. A
run without a token -- the CLI -- makes exactly the calls it made before
(`kwargs_for` passes no keyword). Rejected: checkpoints alone (the rendering clip
runs on for minutes of ARM CPU); tracking children at ~12 render call sites (a
large diff in an untested layer); a subprocess per job (breaks the tee, the
in-memory store and per-process pacing); raising from the tee (DEC-014).

## DEC-076 — CANCELLED is terminal; a cancelled job's late writes are dropped
**Context.** A cancelled worker unwinds: a killed ffmpeg surfaces as an error,
and a completion can race the cancel.
**Decision.** `store.request_cancel` decides under the RLock and refuses a job
already completed, failed or cancelled (the route answers 409). Once CANCELLED,
`update_job` drops `status`/`error`/`clips` and `update_progress`/
`refine_progress` are dropped; events and other fields (`delete_requested`) still
apply. The worker treats `Cancelled`, and any exception raised after its token
was set, as a cancellation.
**Consequence.** A job ends COMPLETED or CANCELLED, never FAILED because it was
cancelled. A rerun (`reuse_job_id`) of a job that is still running gets 409, so
two workers never share `outputs/{id}`.

## DEC-077 — Delete removes a job's files, under narrow rules
**Context.** Deleted jobs left `outputs/{id}/` and their uploads forever. Uploads
keep their original file name, so two jobs can share -- or overwrite -- one
file, and `reuse_job_id` / upload names are client-controlled.
**Decision.** `web/api/cleanup.py`: a path is only ever a single plain name
directly inside its root (no separator, `.`/`..`, drive, absolute path, symlink,
or the root itself; a directory in outputs/, a file in uploads/). An upload is
removed only if no other job references the name and its mtime is not newer
than when this job took it (`created_at`, or `source_attached_at` now recorded by
POST /source); with no usable timestamp it is kept. A running job is flagged
`delete_requested`, cancelled, answered 202, and removed by its worker's
event-loop cleanup; startup finishes deletes a crash interrupted. Cancel alone
keeps the files (DEC-022). No age/size retention sweep (a follow-up).
**Consequence.** A leaked file is recoverable; a wrongly deleted one is not, so
every doubt resolves to keeping it. Verified live: a shared upload survived the
first job's delete and went with the second's. A revert cannot restore deleted
files.

## DEC-078 — The queue is capped by MAX_QUEUED_JOBS, answered with 429
**Context.** Every job was accepted and queued with no ceiling.
**Decision.** `MAX_QUEUED_JOBS` (default 20, 0 = no limit, malformed = default),
read per request, checked on POST /api/jobs and POST /{id}/source -- the latter
before the upload is stored.
**Consequence.** 429, not 503: "come back later" is the truth, and a 5xx reads as
a broken server to proxy health logic. The dashboard already renders `detail`.

## DEC-079 — `clipping/studio` is a real package
**Context.** `clipping/studio.py` shadowed the `clipping/studio/` directory, so
its 12 modules loaded their siblings by path: 54 loads, a dozen private copies
of helpers, nothing in sys.modules, no shared module state (DEC-081's bug was one
consequence).
**Decision.** `studio.py` became `studio/__init__.py` with the same 32 public
names and `FIREFOX_UA`; an AST codemod turned every by-path load into
`from . import x [as NAME]`. Callers still import it lazily (DEC-031).
**Consequence.** Module state is shared: one face detector (IMAGE mode,
stateless), one encoder-probe memo, one watermark cache. A studio module can no
longer be executed by path outside its package; a test that needs one imports
`clipping.studio.<x>` under the `render_stack_stubbed` fixture. Verified
frame-identical on four render variants. The package `__init__` is eager, so
importing any submodule pulls in cv2 (a PEP 562 lazy `__init__` is a follow-up).

## DEC-080 — Encoder probes are memoised, not passed down
**Context.** Every render path probed the hardware encoders per clip (up to 7
ffmpeg processes), though the runner probes once. The render paths probe at the
default 1080 while the runner probes at the real height, which changes the
bitrate, so passing the runner's result down would change output.
**Decision.** The encoder listing is read once per process; runtime probe
answers are cached per exact argument tuple for 600s, both outcomes.
**Consequence.** Same encoder, same arguments; 1.4s saved per clip here. The TTL
bounds a stale answer if a GPU appears, vanishes or runs out of NVENC sessions.

## DEC-081 — The watermark renderer is cached by its settings, not by id(cfg)
**Context.** Loading `watermark.py` inside the frame loop re-created its
`_renderer_cache` every frame. Loading it once makes the cache live for the
process, and its `id(cfg)` key then becomes unsafe: ids are reused after an
object is freed.
**Decision.** Key by the nine settings the renderer reads plus the image file's
mtime and size; cap at 32 entries.
**Consequence.** ~4s saved on a 14s watermarked clip (27.3s -> 23.1s against a
23.3s no-watermark control), frame-identical output, and a replaced logo file is
picked up.

## DEC-082 — Loudness levelling is opt-in and runs as the last write
**Context.** Nothing levelled the audio. The human chose opt-in, the stance of
DEC-050 (glitch) and DEC-051 (clip length).
**Decision.** `--loudnorm` / "Level loudness", default off in five places: a
two-pass EBU R128 loudnorm (I=-14, TP=-1.5, LRA=11) over each finished clip,
after the concat and edge glow, and over story mode's assembled files; video
copied, audio AAC at the source rate; best-effort.
**Consequence.** Default output is byte-identical (verified). With the flag a
-21.8 LUFS clip measured -14.0 LUFS, video frames unchanged. It adds one AAC
generation and a few seconds per clip, which is why it is not the default.

## DEC-083 — One env template; pyproject mirrors requirements
**Context.** `.env.sample` called NVIDIA required and the default, which the
chain gate (DEC-073) refuses; `pyproject.toml` lacked nine packages while the
README offered `uv sync`.
**Decision.** Delete `.env.sample` (its four Facebook-uploader keys moved to
`.env.example`, the two with code defaults commented out -- `NAME=` would
override a default with ""). Copy the nine specifiers into pyproject verbatim; a
stdlib test compares the two manifests.
**Consequence.** Docker still builds from `requirements.txt`; nothing was
upgraded. Splitting heavy dependencies into extras remains its own task.

## DEC-084 — The static Studio is retired; the API-served dashboard is the Studio
**Context.** `docs/studio/*.html` posted fields the backend deleted and sent no
API token, so every request it made was refused. The human chose to retire it.
**Decision.** Delete it; the README describes the dashboard the API serves
(DEC-038) and `docs/index.html` links to that section.
**Consequence.** The GitHub Pages `/studio/` path now 404s (Pages settings live
outside the repo). One UI to maintain.

## DEC-085 — LLM_CHAIN is not persisted, because nothing sets it at runtime
**Context.** A follow-up said a runtime-set chain vanishes on restart.
**Decision.** No code. `SettingsRequest` has no chain field, the settings route
only reads `LLM_CHAIN`, and a job carries its own `llm_chain`; the value comes
from `.env`/compose, which survive a restart.
**Consequence.** A Settings field for the chain would be a feature, not a fix.

## DEC-086 — The repository slug is an address, not the old product name
**Context.** The branding guard (`tests/test_branding.py`) forbade the substring
`opensource-clipping` outside upstream attribution, so it rejected this fork's
own GitHub URL, `rzdhop/opensource-clipping-better`. The rename that introduced
it had turned upstream's `your-username/opensource-clipping` placeholder into a
`your-username/rzdhop-clips` repository that does not exist.
**Decision.** One allow-list entry for the slug `opensource-clipping-better`,
the test's own mechanism; docs link and clone the real repository.
**Consequence.** A doc that says `opensource-clipping` alone still fails the
guard (mutation-checked). If the repository is renamed, GitHub redirects the old
URL and this entry can go.

## DEC-087 — Gemini's default is gemini-3.5-flash-lite, and every default model is one constant
**Context.** On 2026-09-24 the human added a new Google key and pressed Test
provider chain. `gemini/gemini-2.5-flash-lite` answered `404 This model
models/gemini-2.5-flash-lite is no longer available to new users`. Old keys
kept working, so no existing setup noticed. The string was an inline literal
inside `DEFAULT_LLM_CHAIN`, with none of the one-place guarding DEC-052 gave the
NIM model.
**Decision.** `GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"` and
`GROQ_DEFAULT_MODEL`, beside `NVIDIA_DEFAULT_MODEL`; `DEFAULT_LLM_CHAIN` is built
only from the constants. Chosen by `tools/bench_llm.py` on the real pass-A
request (see DEC-090's fixture), 2026-09-24:

| model | test transcript (found the clip) | real windows (3 x 2) |
|---|---|---|
| gemini-3.5-flash-lite | 3/3, 0.9-4.4s | 1.1-2.0s, 1-3 candidates |
| gemini-flash-lite-latest | 3/3, 1.0-1.7s | 1.0-1.3s, 1-3 candidates |
| gemini-3.5-flash | 503 "high demand" x3 | - |

**Consequence.**
- **The alias is not the default** although it measured as well: its model
  changes under us without a benchmark (DEC-058). It is the *fallback*
  (DEC-089), which is exactly what a moving alias is good for.
- Tests: each default model equals its constant; the chain block holds no
  retyped literal; no default link is in a table of models measured dead for a
  new account (each entry says when and how); `.env.example` carries the
  shipped chain verbatim. The model-literal guard regex gains `flash-lite`.
- **Groq's default is still unmeasured on this project** (no key). It is named,
  not vouched for.

## DEC-088 — OpenRouter and Mistral join the default chain; a paid link is never called free
**Context.** The human's funded OpenRouter key was never used or tested:
OpenRouter was a registered, `primary` provider but not a link in the default
chain, and a provider absent from the chain is never contacted (DEC-023). The
dashboard's own hint already promised OpenRouter/Mistral coverage.
**Decision.** Default chain `groq -> gemini -> openrouter -> mistral -> nvidia`.
- **OpenRouter: `mistralai/mistral-small-3.2-24b-instruct`, paid.** Test
  transcript 3/3 at 2.5-2.7s; real windows 5.1-13.5s with 2-6 candidates;
  $0.094/$0.25 per M tokens, well under a cent per job. `llama-3.3-70b-instruct`
  also found the clip 3/3 but took 2.3-30.0s on real windows and returned the
  maximum of six candidates every time, ignoring "two strong moments beat six
  weak ones". The `:free` nemotron returned malformed JSON after 79-100s and
  `gpt-oss-20b` returned no content.
- **After both free tiers**, so credits are spent only when they failed.
- **Mistral: `mistral-small-latest`, unmeasured** (no key here). Decided by the
  human in chat; the planning review argued for leaving it out under DEC-058.
  An unkeyed link is skipped at no cost, and the new diagnostic (DEC-090)
  measures it the moment a key is set. Recorded in ASSUMPTIONS.
- `Provider.free_tier` (trailing, default True; OpenRouter False). The DEC-073
  refusal tags a billed row "(paid)" and counts the free ones instead of
  claiming "all are free".
**Consequence.**
- The DEC-073 refusal now lists four missing primaries for an NVIDIA-only
  setup; the order pins in three test files were updated as this decision.
- A key with no OpenRouter credits answers 402, which is FATAL for that link
  and moves on, printed.

## DEC-089 — A retired model is swapped for the same provider's next model, on the same key
**Context.** DEC-087's failure had nothing wrong with the key or the provider,
only the model. `classify` calls a 404 FATAL, so the whole link was abandoned
and the job fell to the NVIDIA floor. Models on free tiers now retire every few
weeks (A-010), for new accounts first.
**Decision.** `errors.is_model_unavailable(exc)` asks a narrower question after
`classify`: 404/410, or a 400/422 naming the model with a gone-marker
("no longer available", "decommissioned", "invalid model", ...). Never for
401/403/429, a refused schema, or OpenRouter's "parameters"/"data policy" 404s.
A provider that names `fallback_models` answers that error, and only that, by
running its next model on the same key; `llm._run_link` wraps the unchanged
ladder (`_run_model`), and the probes share the path.
**Consequence.**
- **DEC-023 is amended for one named case, not overturned.** Before this, the
  same 404 already moved the job to a *different* provider. A same-provider
  swap stays closer to the list the user wrote: the provider and key they
  chose, a model the registry names, and a printed `↪` line every time.
- **No extra attempts, no new budget rule.** A dead model fails on its first
  attempt; the next gets the link's ladder under the same predictive deadline
  check (DEC-053/059). Tested: `[404] + [503]*5` makes `1 + MAX_ATTEMPTS` calls.
- **Remembered per key, never blacklisted.** Availability is per account, so
  the working model is remembered per `(provider, model, sha256(key)[:12])`.
  Nothing is marked dead: a transient 404 cannot exclude a model for the life
  of the server. The key itself is never stored.
- **Fallbacks are benchmarked models only** (DEC-058): Gemini
  `gemini-flash-lite-latest`, OpenRouter `llama-3.3-70b-instruct` (same price
  tier as the default; the plan's "never dearer" rule was relaxed for the only
  other model that found clips, and is stated here instead). NVIDIA, Groq,
  Mistral and custom list none and behave exactly as before, pinned by a guard.
- `classify` is unchanged, so every existing FATAL pin still holds.

## DEC-090 — The chain test sends every keyed link the real request, and reports a verdict
**Context.** On 2026-09-24 `POST /api/settings/test-chain` pinged each link
with "reply with ok" and reported **"✅ Jobs can start"** for a chain whose
Gemini link answered 404 and whose only working link was the NVIDIA floor.
DEC-056 already said a ping proves liveness and not suitability; DEC-058 is
what ignoring that cost. The route's `ready` also came from `chain_readiness`,
which is key-based by design (DEC-073), so a keyed-but-dead primary read ready.
**Decision.** The route runs `llm.diagnose_chain`: every keyed link is sent the
scan's own pass-A request (`diagnostic.pass_a_work`, shared with the preflight
and the bench) on a 14-beat test transcript with exactly one clip in it
(beats 4-9), and `diagnostic.judge` says whether the answer found it.
- **Providers at once, links on one provider one after another** (NVIDIA
  serialises per key; rate limits are per provider). The route waits
  `registry.diagnostic_budget` = the slowest provider's sum of
  `diagnostic_timeout` (= the work probe's cap: 90s fast tiers, 240s NVIDIA)
  + 10s. Default chain: 250s, under the unchanged 300s ceiling.
- **A ping follows only a request that failed with time to spare**, turning
  "failed" into `alive` (key works, model cannot do the job). A timed-out
  request is reported as it is: there is no time left to ask, and the budget is
  the sum of allowances.
- `verdict`: `ready` (a primary completed it, or allow-slow, or the chain names
  no primary), `floor_only` (only the floor did: the reported case), `blocked`
  (the key gate would refuse the job), `dead` (nothing did). `ready` =
  `verdict == "ready"`.
- A key for a provider the chain does not name gets an `unused` row with the
  link to add. It is **never contacted** (DEC-023).
**Consequence.**
- **Amends DEC-074's `ready`, not DEC-073.** `POST /api/jobs` stays pure and
  key-based: it must answer before a job exists and cannot spend a network
  round trip. The page shows both: `chain_blocked_reason` before Start, the
  verdict after a test.
- **NVIDIA's measured time drove the allowance.** The plan said 120s; the
  floor took 93s and ~193s for the fixture request on 2026-09-24, so the work
  probe's 240s is used. Two NVIDIA links in one custom chain exceed the
  ceiling and get the existing 504.
- **A test now costs real tokens**: about 1.1k in and 100-300 out per keyed
  link, well under a cent on OpenRouter and free elsewhere.
- It warms the process's memory: a model swap or negotiated level found here
  is what jobs on the same key reuse. Same facts about the same key.
- `probe_chain` and the preflight are unchanged (RC-C9: `tests/test_preflight.py`
  passes unedited).

## DEC-091 — The chain test waits as long as a job would
**Context.** DEC-090 gave each link the preflight's work-probe cap (90s on the
fast tiers). The same day, the job's own five windows sent straight to
`gemini-3.5-flash-lite` took 100.8 / 46.2 / 1.1 / 4.6 / 1.9s. Output was 76-195
tokens with no reasoning tokens, so this is free-tier queueing, not thinking,
and turning thinking off would not help. The job itself saw the same spread
(1-2s and 44-55s requests) and finished its analysis in 298s.
**Decision.** `registry.diagnostic_timeout(link) = min(effective_timeout(link),
280)`: the job request's own timeout (groq 120, gemini/openrouter/mistral 180),
capped so NVIDIA (330) fits the 300s route ceiling with its 10s slack.
**Consequence.**
- **A timeout in the test now means what it means in a job.** Under DEC-090's
  cap, the test would have reported Gemini dead on a day a job used it.
- The default chain's worst case is 290s; a healthy chain answers in seconds.
- **The preflight is unchanged** (DEC-072, RC-C3). A 100s Gemini answer fails
  the preflight's 90s work probe, falls back to the ping, and the job starts
  (DEC-067); it only loses the cache seed.

## DEC-092 — One machine may run without the API token, through a gitignored compose override
**Context.** On 2026-09-24 the human asked, in chat, to remove the API token
needed to open the app. The escape hatch `DISABLE_AUTH=1` exists but is kept out
of both committed compose files by a test ("for a developer's terminal, never
for a running deployment"), and the dashboard showed its sign-in form whenever
localStorage held no token, even against a server that needed none.
**Decision.** The human's instruction wins for THIS machine, and the shipped
default does not change. `docker-compose.override.yml` (loaded by compose
automatically, gitignored, guarded by a test) sets `DISABLE_AUTH=1`. The
dashboard now asks the server before deciding the user is signed out.
**Consequence.**
- The committed compose files still carry no `DISABLE_AUTH`, and that test is
  unchanged. A fresh clone is authenticated.
- **Safe only while the port is private.** `docker-compose.yml` binds
  127.0.0.1:8000, and on 2026-09-24 neither `tailscale serve` nor the Caddy
  profile ran. Exposing the app without deleting the override exposes every
  route, including job creation (which spends the OpenRouter credit) and
  shutdown. The override file says so in its header.
- With auth on, the dashboard makes one extra request (a 401) before showing
  the sign-in form. Nothing else changes.

<!-- ===================== AI Story phase 0 (stage 14) ===================== -->

## DEC-093 — The product is "rzdhop AI"; the Python package and the CLI names do not move
**Context.** Phase 0 turns a clip tool into a two-mode product (Clips + Story).
The name had to follow, but `clipping` is imported by every module, `rzclips` is
on people's PATH, and `clipping` is still a console script for older installs.
**Decision.** Rename the *product* only: served title, logo, docs. The Python
package stays `clipping`; `rzclips` and `clipping` keep working. The distribution
is `rzdhop-ai`.
**Consequence.** A rename that touches no import graph is a rename that cannot
break a render. The cost is a permanent mismatch between the product name and the
package name — acceptable, and cheaper than a migration nobody asked for.

## DEC-094 — Two modes behind one shell, with the last mode remembered
**Context.** Clips and Story are different jobs with different pages, but one
deployment, one auth surface and one Settings page.
**Decision.** `/clips/*` and `/story/*` under a shared shell; the last mode is
kept in `localStorage`; every old path (`/new`, `/job/<id>`, `/settings`)
redirects rather than 404s; an unknown path opens the remembered mode.
**Consequence.** Bookmarks and the links inside already-rendered job pages keep
working — RC-P1 exists to prove exactly that. Settings deliberately stays at the
shared `/settings` rather than moving under `/clips` (spec §1.4: it is shared),
and the mobile top bar carries its own ⚙️ link because the sidebar is hidden
under 768 px and the Tier-2 phone script needs to reach it.

## DEC-095 — The legacy story-clip assembly keeps its flag and gains a label
**Context.** `--story-mode` predates AI Story and does something entirely
different: it assembles a clip from a recipe. Two things called "story" in one
product is a support question waiting to happen.
**Decision.** The flag is unchanged; it is labelled "Story Clip (assembly)"
wherever it appears.
**Consequence.** No existing script breaks. The end-to-end path stays
**UNVERIFIED** (RC-P4) because the sample sources carry no media — the flag
parses and the loader works, which is all the tests can honestly claim.

## DEC-096 — Generation chains reuse the LLM chain's grammar, parser and hop rules
**Context.** Five new kinds (image, image_edit, video, tts, vision) each need a
provider chain with fallbacks. `LLM_CHAIN` already had a grammar, a
split-on-the-first-slash parser (model ids contain slashes) and skip/swap rules
proven in production.
**Decision.** One grammar, one parser, one runner. `registry.parse_spec/parse_chain`
gained a `providers=` argument (defaulting to the LLM table, so existing
behaviour is byte-identical) and each kind brings its own provider table.
**Consequence.** A user who has configured `LLM_CHAIN` already knows how to
configure `IMAGE_CHAIN`. The gate order is one thing to learn, not six: route →
adapter → keys → local probe → paid/budget (💸) or limiter (⏳) → attempt.

## DEC-097 — No paid call happens without `budget.check`, and paid is off by default
**Context.** Phase 0 adds providers that charge per image, per second of video
and per token. A bug in a retry loop is a bill.
**Decision.** `ALLOW_PAID` defaults False. Gating lives in the **runner**, never
in an adapter (an adapter that policed its own budget would be one forgotten
`if` away from spending). Caps 1.00 / 3.00 / 10.00 USD per episode / day / story.
Profile `free` → `one_dollar` when paid is switched on. A refusal always carries
the numbers.
**Consequence.** Every adapter can be written as a dumb transport, and the audit
surface is a single function. The refusal text currently prints sub-cent
estimates as `$0.000` (gemini/flash vision) — cosmetic, recorded as a follow-up.

## DEC-098 — Free counters and paid spend are two different files
**Context.** Free tiers are rate-limited per day; paid usage is money. Mixing
them makes both unreadable.
**Decision.** `data/usage.json` counts **free** calls only and resets at the UTC
day boundary. `data/spend.json` holds paid spend per day. Both atomic.
**Consequence.** The Tier-2 script can assert `usage.json` is *byte-identical*
across a paid click — a one-line proof that a paid call did not silently consume
a free allowance. It also means the file is a moving target: re-snapshot it
immediately before the click, never reuse yesterday's hash.

## DEC-099 — The pricing table is dated, and every paid default link must have a price
**Context.** An estimate is what the budget gate refuses on. A missing price
means either a crash or an unpriced call.
**Decision.** `pricing.PRICES` with `PRICES_AS_OF`; `price_for(link)` raises
`PriceUnknown` rather than guessing; a test asserts every paid link in the
default chains resolves.
**Consequence.** Prices drift and the table says when it was read (A-037). The
failure mode on a stale table is a wrong estimate, not a wrong charge — the
provider still bills what it bills; the cap is the real protection.

## DEC-100 — Local clients are stdlib HTTP, and optional engines are probed, never imported at startup
**Context.** ComfyUI and Ollama are optional local daemons; piper/kokoro/
chatterbox are heavy optional TTS packages. Importing any of them at startup
would make the web app refuse to boot on a machine that does not have them.
**Decision.** Stdlib `urllib` transports (including a hand-written RFC 6455
websocket reader for ComfyUI progress); optional engines imported *inside* the
synthesize call, behind an `_installed()` probe that reports an install hint.
`LOCAL_*_URL` defaults are Docker-aware (`host.docker.internal` in a container).
**Consequence.** No new runtime dependency for a feature most users will not
enable, and the failure is a readable "unreachable, try `pip install …`" row
rather than an ImportError at boot.

## DEC-101 — The hardware profile comes from stdlib probes, and `container_no_gpu` is its own answer
**Context.** Recommendations ("can this machine run Flux locally?") need to know
the GPU, and this deployment runs in Docker without device passthrough.
**Decision.** `hardware.probe()` shells out to `nvidia-smi` / `system_profiler` /
`rocm-smi` / `wmic` and reads `/proc/meminfo`, with every parser fixture-tested;
ComfyUI's `/system_stats` is authoritative when reachable. `classify()` returns
`container_no_gpu` distinctly from `cpu_only`.
**Consequence.** The distinction is the whole point: `cpu_only` means buy a GPU,
`container_no_gpu` means *you have one, the container cannot see it* — and the
recommendation carries the `host.docker.internal` hint instead of useless advice.
Probe errors are recorded rather than hidden ("pynvml: NVML Shared Library Not
Found" is shown, not swallowed).

## DEC-102 — VIDEO_CHAIN parses and tests with no adapter behind it
**Context.** Video generation is phase 6. Leaving the kind out entirely would
mean re-opening the parser, the settings model and the UI later.
**Decision.** `video` is a first-class kind: it parses, it appears in Settings,
and a chain test renders `no_adapter` rows.
**Consequence.** Phase 6 registers adapters and nothing else changes. The cost
is five rows in the UI that do nothing yet — which is honest, and better than a
kind that silently does not exist.

## DEC-103 — A chain test spends at most one paid call, per link, on an explicit click
**Context.** "Test chain" must be safe to press. Walking a chain that ends in a
paid link would charge for curiosity.
**Decision.** A chain test runs the free and local links; a paid link is
*reported*, not called. Calling one requires naming it (`link=`), an explicit
click on a button that shows the estimate, `allow_paid` on, and a passing
`budget.check`. It is booked in `data/chain_test_ledger.json` and recorded in
spend. Serialized by `_CHAIN_TEST_LOCK` under a wall-clock ceiling.
**Consequence.** The button is safe by construction, and the one paid path is
auditable to a single ledger line. DEVIATION from the plan text: no separate
`chain-test-file` route — samples go under a reserved `_chain_test` id served by
the existing signed outputs route, which keeps the auth surface smaller.

## DEC-104 — The Gemini generation adapters call REST, and no SDK is added
**Context.** `google-genai` is declared in `pyproject.toml` but absent on the
Tier-1 host, so an adapter importing it could not be tested where the tests run.
**Decision.** `generateContent` over the stdlib transport, for both image
(`responseModalities: [IMAGE]`) and TTS (`[AUDIO]`, PCM → WAV).
**Consequence.** The adapters are testable with an injected transport and add no
dependency. The cost is hand-maintained request shapes — which is why the live
model ids were confirmed against the real endpoint (`gemini-3.8-flash-lite-tts`)
rather than trusted from a doc page.

## DEC-105 — The no-token override now spans the tailnet, which supersedes DEC-092's condition
**Context.** DEC-092 allowed this one machine to run without the API token
through a gitignored compose override, justified by the port being private
(127.0.0.1 only). On 2026-09-26 the human asked in chat for the app to be
reachable from the tailnet with no token ("No token nothing only expose to
tailscale adr") — the phone steps in the Tier-2 script had never reached the
server, because nothing outside the VM could connect.
**Decision.** `sudo tailscale serve --bg --tcp 8000 tcp://127.0.0.1:8000`
(persistent, tailnet-scoped). `DISABLE_AUTH=1` stays.
**Consequence.** This **supersedes DEC-092's "safe only while the port is
private" condition**, and that is the point of writing it down: every device on
the tailnet (2 peers today) now reaches every route — jobs, settings, outputs,
`POST /api/shutdown` — with no credential. The blast radius is exactly the
tailnet ACL, so the tailnet ACL is now the only thing protecting this
deployment. Verified: `100.112.96.111:8000` and the MagicDNS name answer 200;
the public interface `10.0.0.113` does not. The gitignored override's header was
rewritten to say so. Undo with `sudo tailscale serve --tcp=8000 off`. A first
attempt with `--http 80` answered only by hostname (404 on the bare IP) and was
turned off. **Revisit before this machine ever leaves a trusted tailnet.**
