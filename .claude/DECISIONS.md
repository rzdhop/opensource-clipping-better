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

*Superseded in part by DEC-173 (2026-09-29): a token is required only when `API_TOKEN` is set; nothing is generated
or stored in `data/api_token` any more. The router-level dependency, `/api/health` and the 401-not-422 rule stay.*

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

*Superseded by DEC-173 (2026-09-29): the shipped default is now open, so this machine needs no override; the file
was moved to `/home/ubuntu/backups/auth-opt-in/`.*

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

*Superseded by DEC-173 (2026-09-29): the tailnet exposure stays, now as the default rather than an override; a
public `DOMAIN` refuses to start without `API_TOKEN`, which answers the "revisit" above.*

## DEC-106 — A paid link gets exactly one attempt; the runner never retries it
**Context.** Found on 2026-09-26 while mapping the paid path, before the first
paid call phase 0 ever made. `generation._attempt` retried a RETRY or
RATE_LIMITED failure by calling `adapter.generate()` again. `FalAdapter.generate`
submits the job to fal's queue first and only then polls and downloads, and fal
bills a job once the submit is accepted. So a timeout, dropped connection, 5xx
or 429 on a *poll* or on the image download re-submitted a second job. That job
was billed and never recorded: the ledger and `spend.json` carry the one
estimate `_run_candidates` stamps on the result. That broke DEC-103 (at most one
paid call per click) and DEC-097 (no paid call without `budget.check`), since
the second submit passed no check.
**Decision.** `_attempt(paid=True)` runs one attempt. A retryable failure on a
paid link ends that link, and its reason says "paid link: not retried, a second
request could be billed again". Free and local links keep `MAX_ATTEMPTS = 2`.
Moving on to the *next* link of a chain is unchanged: that is a new call with its
own estimate and its own `budget.check`.
**Consequence.** One click submits at most one paid job, whatever the network
does. The cost is that a transient hiccup on a paid link fails the link rather
than recovering. For a chain test that is the right trade. For phase 4's real
asset generation it means a flaky paid provider falls through to the next link
sooner. **Still open, deliberately not done here:** a paid attempt that fails
*after* the provider accepted it may still be billed, and nothing records it,
so the ledger can under-report by the estimate of a failed paid attempt. A
charge-on-attempt rule changes DEC-098's accounting. It belongs to phase 4,
when paid generation runs for real, and it's recorded as a follow-up in
CHECKPOINT. Rejected: making fal's submit idempotent (its queue API offers no
idempotency key we rely on); retrying only the poll inside the adapter (still
needs a rule for a lost submit, and it doesn't cover the other paid adapters).
Test: `tests/test_generation_chain.py::test_a_paid_link_is_never_retried_so_one_click_cannot_bill_twice`
(timeout, connection, 5xx, 429). Commit `32f8346`.

## DEC-107 — Story writing is one small artifact per request; a concept is one call
**Context.** Spec §4.1 extends DEC-027 to story writing: one request, one artifact,
≤ ~400 output tokens, so the free and slow providers stay usable and every piece
can be regenerated alone. The spec disagreed with itself on C1 (2 or 5 concepts per
call); the human chose 2 per call × 5. The live Tier-2 run (2026-09-26) then showed
two French concept cards overrunning the 500-token cap on every call.
**Decision.** C1 writes **one** concept per call; "Generate 10 more" is ten calls.
Caps are sized so the largest French reply a prompt allows fits (a test builds it at
every stated word limit, × 1.3 for French, and checks it against the cap): C1 700,
B1 400, B2 520, B3 300. The bible is B1 → B2 → B3, each writing its own fields; a
field is regenerated with an optional note by rerunning only its prompt and applying
only its keys. A reply that fails validation is asked for once more with the same
cap, then reported; a request is never shrunk and a reply never trimmed. A failure is
local: the concepts and bible parts that answered are kept and the failure names what
to regenerate. Every accepted call prints its link and `≈N tokens out (cap …)`.
**Consequence.** Ten small calls for ten concepts (≈ 25 s on Gemini's free tier),
each failure losing one concept. The caps are ceilings, not targets: live French
replies measured ≈ 290–335 (C1), 151/266/122 (B1/B2/B3). The 1.3 factor is not
measured (A-046). The human's decisions: 2026-09-26 in chat.

## DEC-108 — A story step ends awaiting approval: finished for the worker, not for the user
**Context.** Spec §9.1: a story step is a job on the existing worker and slot; it must
free the slot when done but wait for the user, and survive a restart.
**Decision.** Two job statuses: `running` (a step at work; failed at restart like any
interrupted job) and `awaiting_approval`. `awaiting_approval` joins the store's
terminal set (a cancel answers 409), the SSE close set and the dashboard's finished
set; `fail_stale_jobs` leaves it untouched (a third bucket). Approving a document
flips its awaiting jobs to the existing `completed` with `approved_at`; a newer job
for the same document supersedes the older one (`completed` + `superseded_by`).
There is no `done` status. A job has a `kind` (`clip` by default; a record without
one reads as a clip); a rerun cannot reuse a step job's id.
**Consequence.** Verified live: a restart with a bible job awaiting left it awaiting
and approvable. Every hard-coded status set had to learn the new states — the
dashboard's stream hook and the wizard both missed a step finishing until two fixes
(the hook reacts to a terminal progress frame; the wizard polls its story while a
step is in flight).

*Amended by DEC-161 (2026-09-29): render, metadata, fast-track and a metadata regenerate end `completed` instead
of `awaiting_approval`, because they have nothing for the user to approve.*

## DEC-109 — A step with no external call runs in the request; approval lives on the document
**Context.** One worker slot is shared with clip jobs, so a pure step (choosing a
concept, building or locking a style) would wait behind a 50-minute clip render.
**Decision.** (Human, 2026-09-26.) Only steps that call an LLM or an image API are
jobs. Concept choice, the style draft and the lock run inside the request. Approval
state is `story.approvals {concept, bible, style}`; `status` is derived from the
contiguous prefix of approvals on every save and never set directly. Editing an
approved bible field clears `approvals.bible`; the style approval is kept (the lock
reads no bible text). One step job per story at a time (409).
**Consequence.** Locking a style never queues. An approval cannot be left stale by an
edit, and status cannot drift from what was approved.

*Amended by DEC-161 (2026-09-29): render is also a job, though it calls no LLM or image API, because it needs
minutes of CPU on the shared worker slot.*

## DEC-110 — `stories.json` is written atomically and can be rebuilt from the folders
**Context.** The spec asked for "the same atomic-write discipline as jobs.json"; the
job store writes `jobs.json` with a plain `open(..., "w")` — no temp file, no rename.
**Decision.** Story documents and the index are written through a temp file in the
same directory, `fsync` and `os.replace`, mode 0644 so the host can read what the
container writes. The index is a cache: missing, torn or of the wrong schema, it is
rebuilt from the story folders and the rebuild is printed; an invalid folder is
skipped and reported, never deleted. One RLock per resolved root, shared by every
instance in the process. `jobs.json`'s non-atomic write is recorded, not fixed here.
**Consequence.** A crash mid-write cannot hide stories. The CLI and the server do not
coordinate across processes (A-044).

## DEC-111 — No job may own `outputs/stories`; deleting a story removes only its folder
**Context.** Found while placing stories under `outputs/`: `POST /api/jobs` takes
`reuse_job_id` from the client and makes it the job id, and deleting a job removes
`outputs/<id>/`. A job named `stories` would render into, and on delete wipe, every
story; phase 0's `_chain_test` had the same exposure.
**Decision.** `cleanup.RESERVED_OUTPUT_NAMES = {stories, _chain_test, stories.json,
jobs.json}` (compared case-folded, trailing dots and spaces ignored): refused as a
`reuse_job_id` (400) before anything is created, never removed by a job delete, and
`outputs/stories` is not listed by the outputs route. A story delete answers 409
while one of its steps is queued or running; otherwise it removes the story's step
jobs, its index entry and its folder — only as a real directory directly inside
`outputs/stories`, a symlink is kept and never followed. 12-hex validation of
`reuse_job_id` was rejected (tests use ids like `job123`).
**Consequence.** The only client-controlled path to a destructive delete of story data
is closed; phase 0's chain-test samples are protected by the same rule.

## DEC-112 — The generation route is chosen per story in phase 1
**Context.** Phase 0 deferred the per-task route selector (`auto|local|api`) to
phase 1; in phase 1 only the three-image style preview generates images.
**Decision.** (Human, 2026-09-26.) `story.generation_profile.route` is editable and
applied to the preview. The Settings per-task selector moves to phase 2, where cast
and place images need it.
**Consequence.** No Settings change in phase 1; phase 2 owns the selector and its
precedence over the story's route.

## DEC-113 — Story media is token-gated and loaded as blobs in phase 1
**Context.** Signed media URLs (DEC-048) require routes whose parameters are named
`job_id` and `filename` under `/api/outputs/`. Phase 1 serves three preview images.
**Decision.** `GET /api/stories/{id}/files/{name}` serves `preview_[1-9].(png|jpg|jpeg|webp)`
from the story's `styles/preview/`, behind the router's token, `Cache-Control:
no-store`; the dashboard fetches it with the header and shows an object URL. The
signed-URL code is not touched.
**Consequence.** No auth change for three PNGs. Audio and video (phase 4) need range
requests from `<audio>`/`<video>`, so that phase designs signed story media once.

## DEC-114 — `--ai-story` has its own parser, and a story's language has no default
**Context.** The clip CLI's defaults are pinned by the five-place tests; spec §9.3
wants story commands with the API's defaults. A silent default language would be the
silent fallback §0 forbids.
**Decision.** `main.py --ai-story new|step|list` is dispatched before the clip parser
to `clipping/aistory/cli.py`; the clip `--help` gains one pointer line and no
argument. The story rules the API and the CLI share live in
`clipping/aistory/workflow.py` (the route keeps only what needs the job store).
Language is required in the request model, the CLI and the wizard; the four
generation-profile defaults (tier 1, route auto, consistency references, budget
profile free) agree across `defaults.py`, the request model, the CLI and the React
form (`tests/test_story_defaults.py`). The CLI reads keys from the environment, not
from the dashboard's Settings.
**Consequence.** One set of rules, two front ends; a story is always in a language
someone chose.

## DEC-115 — A story step never calls a paid LLM link unless `allow_paid` is on
**Context.** Live Tier-2 (2026-09-26): truncated C1 replies exhausted Gemini and the
LLM chain fell through to the paid OpenRouter link (DEC-088), about $0.0001 and not
tracked by the budget. AI Story's rule is free by default, paid only by opt-in (§8.5).
**Decision.** (Human, 2026-09-26.) For story steps, a link whose provider is not free
tier (an OpenRouter `:free` model counts as free) is skipped while `allow_paid` is
off, with a printed `⏭ Skipping <link>: paid link, allow_paid is off (AI Story spends
only on opt-in).` before the chain runs. A chain whose only keyed links are paid is
refused up front (API 400, CLI exit 1) naming `allow_paid` and the free keys to set;
the estimate marks skipped links. Clip jobs are unchanged.
**Consequence.** Verified live: fifteen story LLM calls, the paid link skipped and
printed each time, no spend. With `allow_paid` on, LLM spend is still not estimated or
booked in phase 1 (the budget governs generation spend) — a follow-up.

## DEC-116 — Tier-2 of phase 1 was run by me, at the human's word
**Context.** The approved plan had the human walk the wizard on their phone. The
human answered "if it's good, push merge, we'll start phase 2" without walking it.
**Decision.** The script ran in the built-in browser at 375 px against the live
container on the free chain. It failed at "Generate 10 more" (DEC-107's cap) and then
exposed two dashboard defects (DEC-108's consequence); all three were fixed and the
full script passed: FR story, ten concepts, `tentafruit_island`, bible, two
regenerate-with-note, approve, `fruit_drama` with one accent, preview 3/3 free, lock.
**Consequence.** The push rests on that substitute, as the human asked. A walk on the
human's own phone is still welcome and was not done.

## DEC-117 — Prompt-only consistency is a labelled mode the user chooses; the step stops and asks first
**Context.** Spec §8.1: when no reference-capable editor can run, the sheets step "stops and asks"; prompt-only
(text-to-image with the locked prompt block and the portrait's seed) is an explicit per-story choice, never
automatic. This deployment has no free editor: ComfyUI is unreachable, the hosted editors are paid.
**Decision.** Turnaround, expressions and place variants in `references` mode first run a no-call readiness
check of IMAGE_EDIT_CHAIN (keys, route, allow_paid, caps, free allowance; local editors probed with
`GET /system_stats`, ≤ 2 s, cached 60 s for the story page). Not ready → `NeedsEditor`: zero generation calls,
nothing booked, nothing written; the story page shows why (every link's reason, paid editors with their
estimate) and offers "Switch this story to prompt-only consistency" behind a confirmation. In `prompt_only`
the images go through IMAGE_CHAIN with the portrait's (or plate's) seed and are labelled `prompt_only` in their
JSON and in the UI; portraits, master plates and prop images are `base`, edited ones `references`.
**Consequence.** Verified live: the cast stopped before any call, the switch showed with the reasons, six
prompt-only sheets were made from each portrait's seed and labelled. The first version counted an unreachable
local editor as ready, so the choice never appeared — the probe fixed that dead end.

## DEC-118 — No face-identity adapters; uploads are design references for stylised characters
**Context.** Spec §1.2: no real-person likeness; the cast is often fruit.
**Decision.** No InstantID/PuLID/InfiniteYou. A character upload is a design reference, stated on the page
("Design references for stylised characters. Imitating real people is not supported."); U1 describes only
clothing and colours for a photo of a real person.
**Consequence.** A reference steers K1's text (DEC-121) and, with a real editor, the edits — never an identity.

## DEC-119 — The cast is picked from the concept's sketch, plus your own characters
**Context.** A concept sketches 3–5 characters; the human wanted to choose (2026-09-26).
**Decision.** The cast step creates the ticked sketch characters (all by default in the UI) and custom ones
(name, role, one line), ≤ 8 in total (the number of distinct French Edge voices). The step fills what is missing
for every character and can be re-run; the CLI creates the whole sketch only when the story has no character
yet, so a re-run never resurrects a deleted one.
**Consequence.** A cast of 3 from a 5-character sketch, as in the Tier-2 walk.

## DEC-120 — Places and props are proposed, then edited, then described one by one
**Context.** The spec has P1/R1 per item but no step deciding which places and props exist; the human chose a
proposal the user edits (2026-09-26).
**Decision.** P0 (one small call) proposes 2–3 places from the bible and props from its motifs and the cast's
signature objects → `places_proposal.json`; the page edits it (1–6 places, 0–6 props, owners) and the places step
runs P1/R1 per item, a master plate per place and an image per prop; time variants are made on demand.
**Consequence.** Live: P0 proposed 2 places + 3 props, edited to 2 + 1 on the page.

## DEC-121 — An upload is validated, re-encoded and uuid-named before anything reads it, and described for K1
**Context.** User images are untrusted; the clip upload path trusts extensions and reuses names (the recorded
"second talk.mp4 overwrites the first" follow-up). The human chose a vision description folded into K1 (2026-09-26).
**Decision.** The route takes the raw request so the token is checked before any body is read, and streams the
multipart under a cap; `accept_upload` refuses a 5th upload before reading, > 10 MiB, > 40 MP (two independent
bomb guards), and anything but PNG/JPEG/WEBP/GIF decoders; re-encodes to a metadata-free PNG (≤ 1536 px long side)
named `<uuid4hex>.png`. Before any K1 run, undescribed uploads are described by U1 on VISION_CHAIN (free Gemini
here), and K1 receives the notes.
**Consequence.** Live: a JPEG became a clean PNG; U1 described it in 2.6 s for $0. K1 follows a reference's colours
but bends a human-looking one toward the style (A-054). The pre-existing clip upload routes still spool before the
token check — its own task.

## DEC-122 — A pinned voice is a one-link chain; voice briefs are kept; age is a distance
**Context.** Spec §8.1/§11: a character's voice is pinned and must never fall back silently; leads never share.
**Decision.** Voices are proposed from `voices.json` in TTS_CHAIN order for the story language (leads → support →
recurring → guest; leads and supports never share; guests reuse only when exhausted); the sample is synthesised
through a chain of the pinned voice alone — a failure offers alternates and tries nothing else. K1's brief is kept
as `voice_hints` so a voice can be re-proposed later. Age is scored by distance (child < young < adult < elder;
catalogue "senior" = elder). Edge rate/pitch are optional keyword arguments; clip voiceovers are unchanged.
**Consequence.** Live, before the age fix, an elder matriarch got a young voice; after it, the top alternate was
an adult one.

## DEC-123 — Approval is per entity; groups fold; any rewrite clears the approval of what it rewrote
**Context.** Spec §3 approves characters, places and props one by one and the season as a whole.
**Decision.** `approvals` gains `cast`, `places`, `season`; `cast` holds when ≥ 1 lead/support exists and every
lead and support is approved (recurring/guests never block); `places` when ≥ 1 place exists and every place and
prop is approved; a folder that cannot be read blocks its group. Approving an entity needs its parts (a
character: text, three sheets, a pinned voice, a sample). Every regenerate, PATCH or step write clears that
entity's approval (and `season:<ep>` the season's); groups re-fold, so the status can fall back. Deleting an entity
removes its id from every document that pointed at it. Phase-1 story files gain the three keys on read.
**Consequence.** The status chain runs `style_approved → cast_approved → places_approved → ready`, verified live.

## DEC-124 — Steps fill what is missing; regenerations use a fresh seed
**Context.** A "needs an editor" stop leaves a cast half made; re-running must not redo or re-pay what exists.
**Decision.** `cast` and `places` fill only what is missing (text, images, voices, samples); a complete re-run
makes zero calls. A regenerate uses a fresh seed (so a seed-honouring provider does not return the same picture);
a new portrait remakes the sheets derived from it.
**Consequence.** Live: after the prompt-only switch, Continue cast made exactly the six missing sheets.

## DEC-125 — Phase 2's Tier-2 was walked by me; the human listens to the voices
**Context.** The human chose (2026-09-26) that I walk the script at 375 px and they only judge the voices.
**Decision.** Walked live on the Tentafruit story: cast of 3 with an upload, stop-and-ask, prompt-only, voices,
two places with a night variant, a prop, an 8-episode arc → `ready`, $0.00, no paid call. The voice listening is
the human's.
**Consequence.** One defect (voice age) and four polish issues were found and fixed during the walk.

## DEC-126 — Timing is computed in Python, never by the model
**Context.** Spec §4.1 forbids the model from writing a duration, timestamp or path; nothing before phase 3
checked that this rule actually held.
**Decision.** `clipping/aistory/timing.py` is pure and stdlib-only: a line's duration comes from measured audio
when it exists and from the template's per-language rate otherwise. Every LLM prompt for the script and the
storyboard receives a word or shot-count budget, never a duration to state.
**Consequence.** A prompt's schema has no duration field to omit by accident; every timing bug is a Python bug,
testable with no model call at all.

## DEC-127 — The deterministic estimate is chars × the language's rate; French measured at 0.070 s/char
**Context.** A line needs a duration before any audio exists, and again after it, until it is next spoken by its
actual pinned voice.
**Decision.** `estimate_line` = `len(text) × RATE_PER_CHAR[language]`; French is measured (0.070 s/char on three
Edge samples, phase-3 stage 0; corroborated live at +0.6 % against 15 real lines in stage 13, A-055), English is
authored and unmeasured (A-056). The timing source (`estimated` / `tts_word_timestamps` / `audio_duration_only`)
is stored per line, so a later measurement or a text edit knows what it can still trust.
**Consequence.** A script can always be timed, with or without real audio; the duration bar labels which kind it
is showing.

## DEC-128 — `serial_60s_v1` (55–80, target 60, tighten above 75) and `serial_90s_v1` (75–100, target 85) both ship
**Context.** Spec §6.2/§2.7 disagreed with the brief on the window (55–80 vs 55–75); the human's answer 2
(2026-09-27) settled it.
**Decision.** Both templates ship as data (`templates/episodes/serial_60s_v1.json`, `serial_90s_v1.json`),
validated (`episode_template_v1`); `story_bible_v1.episode_template_id` is an enum of the shipped ids, not a free
string. The template is chosen per story and, once any episode has a script, fixed for the whole story.
**Consequence.** Every episode of a story shares one pacing target; switching template mid-season is not offered.

## DEC-129 — Episode approvals live on the episode documents, never on the story
**Context.** The story's `approvals` is a closed six-key schema and `status` a contiguous prefix (`store.py`); an
episode is edited far more often than a season is re-planned.
**Decision.** `script.json` and `storyboard.json` each carry their own `approved_at` (the script also
`approved_anyway`). A script approves with a complete document and a consistency report that is fresh (checked
against the current revision) and passed, or a recorded approve-anyway; a storyboard approves with an approved
script, every scene planned and current against it, and no outdated prompts. Any edit or regenerate that changes
a script clears both approvals and stales the report; a storyboard edit clears only its own.
**Consequence.** Editing episode 1 never touches `story.status`, which stays `ready` throughout (RC-E2); each
episode approves independently of every other.

*Amended by DEC-185 (2026-09-30): a text-only line edit (words, delivery, or a scene's `pays_off`, with the same
line ids, speakers and emotions) clears only the script approval and keeps the storyboard approval; only a
structural edit clears both, as this entry originally described.*

## DEC-130 — Phase 3 writes episode 1 only; episode N ≥ 2 waits for episode N−1's recap in series memory
**Context.** Spec's series-memory step (phase 5) is what would give E3 something true to recap; without it a
"recap" scene would be invented.
**Decision.** `episode_context` refuses ep ≥ 2 (script, storyboard and their estimates) until
`series_memory.recaps["ep{N-1:02d}"]` exists, naming "approve episode N−1 and run the memory step (it arrives in
phase 5) first."
**Consequence.** Only episode 1 can be produced this phase; the E3 recap prompt itself is built and
golden-tested now, ready for phase 5 to call.

*Amended by DEC-179 (2026-09-30): the gate now needs episode N-1's memory entry approved and fresh, not merely a
`recaps` string — a hand-written recap with no entry behind it no longer passes.*

## DEC-131 — A script is one resumable job (E1 → E2×N → E3 → E4), saved after every call, under a predictive
30-minute step budget
**Context.** The season precedent (one job, no time cap) suits eight independent calls; a script is up to ~14
calls with the free tier's 1–100 s latency swings (DEC-091), and it holds the app's one worker slot.
**Decision.** `EPISODE_STEP_BUDGET_SECONDS = 1800`, checked predictively before every call (a call starts only if
it can still finish inside the budget from the elapsed time so far); reaching it ends the job failed, naming
what is left, and "Continue writing" resumes exactly there. Every scene stays independently regenerable
afterwards.
**Consequence.** A slow free-tier run degrades to "finish it in a second click" rather than blocking the render
queue indefinitely.

## DEC-132 — Shots name entities by tags; a per-character handle replaces the tag in the English action; no
entity's real name ever reaches an image prompt
**Context.** "She"/"him" would be ambiguous with more than one character in a shot; the prompt still has to read
as natural English.
**Decision.** T1 and the fast path both write `@char_x` / `#place_y:variant` / `%prop_z` tags inside the action;
`shots.py` resolves them to a handle (the descriptor's own leading noun phrase, disambiguated with a signature
item on collision) for reading, and strips every name before the image prompt is built (the same logic
`refimages` already used, lifted to `names.py`). The reference list is always resolved regardless of consistency
mode; whether phase 4 sends it is a later decision.
**Consequence.** An edited action keeps the same tag grammar, checked by the same rules T1's own reply is
checked against; a name typed into a manual edit is refused, not silently sent to an image model.

## DEC-133 — The fast storyboard is deterministic and inline; the same rule pass runs on both paths; T1 writes
2–4 shots per scene
**Context.** Spec §4.2 said "1–2 shots"; the brief and spec §2.8 said 2–4; the human's answer (chat, 2026-09-27)
took the brief.
**Decision.** `build_fast` derives shots from a scene's own lines, characters and function with no external call
(DEC-109); `rule_pass` (no back-to-back repeated framing, a reaction close-up roughly every three scenes, a
push-in on peaks) runs identically whether the shots came from the fast path or from T1. "Fast (no calls)" and
"Plan shots" sit in the same button row; "Plan remaining with T1" re-plans only the scenes still missing, stale,
or built fast.
**Consequence.** A storyboard is never left half-conforming to the visual rules just because one scene was
planned differently from its neighbours.

## DEC-134 — E4 (the consistency check) has its own input ceiling
**Context.** Every other episode prompt fits the story's ordinary 1,200-token pack; E4 compares the whole script
against the bible, cast, places and series memory at once.
**Decision.** `E4_INPUT_BUDGET` is sized on a 12-scene French worst-case fixture (measured ~1,009 of a first-cut
1,200 budget, then rebudgeted to 3,900 once every episode prompt was remeasured on live-sized data, DEC-138)
rather than reusing `context.check_budget`'s general cap.
**Consequence.** A long episode's consistency check still runs in one call; every other prompt stays inside the
shared 1,200-token pack.

## DEC-135 — Real-voice measurement is opt-in, uses only the pinned voice, and its audio is kept as phase 4's
line audio
**Context.** Measuring every line by default would spend TTS quota (and, on a paid voice, money) before the
script is even settled; a wrong voice must never be tried as a silent fallback (DEC-023).
**Decision.** `params.measure_voices` on the script step measures only a line whose timing is still estimated, or
whose text or pinned voice changed, or whose audio is missing, through that character's pinned one-link chain
alone; a failing voice fails only its own lines, naming the character, and nothing else is tried. The audio and
its sidecar are written under `episodes/epNN/assets/voice/` and read again by phase 4's renderer.
**Consequence.** A script can be approved and re-approved on estimated timing alone, at $0, with measurement
added only when the human asks for it.

## DEC-136 — Phase 3's Tier-2 was walked by me at 375 px on the live server; the human acknowledges it
**Context.** The human's answer 4 (2026-09-27), the same arrangement as phases 1 and 2.
**Decision.** I ran the plan §4 script against the live story after the merge and the rebuild, recorded every
finding in `CHECKPOINT.md`, fixed the majors and the UI issues the human chose (two rounds for the length fix),
and re-walked the affected steps after each fix.
**Consequence.** Live episode 1 stands as it was left after the human's "Acknowledged, go to 14" (2026-09-27):
the pre-fix script and a storyboard whose stored shot durations predate the F7 timing fix, both approved, until
the episode is next re-timed, re-planned or rewritten.

## DEC-137 — A script's line ids are fixed blocks of four per scene, never resequenced
**Context.** Regenerating one scene must not be able to make another scene's line ids drift, because measured
audio is filed under a line's id (`episode_asset_path`) — a drifted id would silently attach the wrong audio to
the wrong text.
**Decision.** `schemas.line_id_for` gives every scene a stable four-id block (`l00`–`l51`) regardless of how many
lines it actually uses; `episode_script_errors` checks the assignment.
**Consequence.** A scene can be regenerated with a different line count without disturbing any other scene's
measured audio.

## DEC-138 — Every episode prompt's cap is measured on live-sized data, at worst case + 15 %
**Context.** The spec's starting caps (E1 600, E2 300, E3 350, E4 350, T1 250) are what DEC-107's method starts
from, not what it ends at; E3 alone measured ~1,009 of its first 1,200-token budget on an 8-scene copy of the
live story.
**Decision.** Every episode prompt (E1, E2, E3, E4, T1, T1r) was remeasured on a 12-scene French worst-case
fixture and its cap set to that measurement plus 15 %: E1 1,270 input / 1,450 output, E2 1,660 / 600, E3 2,360 /
720, E4 3,900 / 800 (kept from 3,523), T1 1,270 / 580, T1r 1,410 / 150.
**Consequence.** A worst-case French episode has headroom under every cap without the model's replies being able
to grow unbounded.

## DEC-139 — E1 asks for an exact, numbered scene list; `validate_e1` accepts any count legal for the episode
**Context.** The stage-12 bench found both free links under-generating against a stated range ("8–12 scenes"): a
model given a range does not reliably hit it.
**Decision.** `timing.episode_slots` computes the exact legal scene count for the episode (60 s ep 1: hook + 8
body + cliffhanger = 10; 90 s ep 1: 12; ep ≥ 2 adds the recap slot); `build_e1` asks for exactly that many,
numbered; `validate_e1` still accepts any count the spec's range and slot positions allow, so a reply that
legally differs is not refused over a formality.
**Consequence.** Live re-bench after the change: Gemini went from 0/3 to 3/3 on E1; NVIDIA 1/3 (still short —
recorded as a hazard, not chased this phase).

## DEC-140 — `shot` stays in `workflow.LATER_TARGETS`, except its own `:plan` target
**Context.** Spec §9.2 keeps `shot:<ep>:<shid>` (a single shot's future asset target) and
`shot:<ep>:<shid>:video` for phases 4 and 6; only re-planning a shot's framing exists yet.
**Decision.** `regenerate.parse_target` folds `shot:<ep>:<shid>:plan` into the real grammar now; every other
`shot:` form still answers `later_phase`, unchanged.
**Consequence.** Phase 4/6 add their own shot targets without this phase's grammar having claimed the whole
namespace.

## DEC-141 — A camera motion the style's rules fix for a scene's function is refused on edit, not overridden
**Context.** `motion_rules.tier1` already picks a motion by function/framing (push-in on peaks, and so on) for
both T1 and the fast path; a manual PATCH could otherwise contradict a rule the style itself states as fixed.
**Decision.** Editing a shot's `camera_motion` where the style's rules pin one for that function returns 400,
naming the fixed value, rather than accepting the edit and quietly drifting from the style.
**Consequence.** A style's camera language stays consistent across every shot of that function, even under
manual editing.

## DEC-142 — `timing.episode_pass` is the single timing source for script and storyboard; a scene with shots is
never shorter than its shot count's floor
**Context.** Tier-2 found `shots.py`'s own scene-level timing ignoring the episode-level window pass (the hold
extension when an episode runs under, the tightening when it runs over): s01/s04/s10's shots came out 1.0 s
short of the script's own scene durations (F7, major).
**Decision.** `episode_pass(script, storyboard=...)` becomes the one function both script and storyboard timing
read; a scene whose shot count needs more than `n × min_shot_s` gets the shortfall as a held tail, counted
inside the existing 1.0 s hold-extension cap; a tightened episode never tightens a scene below that floor.
**Consequence.** Script timing can now depend on the storyboard's own shot count — a scene planned with more
shots may hold slightly longer than it would on script alone; verified on the copy at 0.000 s difference between
script and shot durations on every scene, fast and T1 alike.

*Amended by DEC-183 (2026-09-30): timing is quantized to whole frames at 30 fps; the single-source rule this entry
established is unchanged, only what it rounds to.*

## DEC-143 — E1 aims for the upper half of each slot; E2 asks a two-sided word range with one retry, then accepts
**Context.** Episode 1 first came out at 38.6 s, under the 55–80 s window: E1 was picking low targets inside its
slots, and E2's word budget was a ceiling only, so scenes ran short with nothing pushing them up.
**Decision.** E1's ask targets the upper half of each range; `script._normalize_episode_targets` then raises
every scene's target toward its slot's high end until the episode sums to the template's target (never
lowering, never exceeding a slot). E2 asks for "not fewer than lo, not more than hi" words (lo ≈ 0.7 × the
budget); a reply outside `[⌈budget/2⌉, ⌊1.5×budget⌋]` is retried once, then accepted regardless, logged — a word
count never fails a scene.
**Consequence.** Two rounds were needed: round 1 fixed the length but let E2 overshoot 1.5–2.8×; round 2's
ceiling and softer ask brought live samples to 55.0–71.2 s with 0–4 flags, all inside or at the edge of the window.

## DEC-144 — French elisions are repaired deterministically after every model reply, never by retrying the call
**Context.** The free model drops an elision's apostrophe often enough to be visible in a live walk (F1); a
retry costs a call and is not guaranteed to fix it either.
**Decision.** `prompts.repair_fr_elisions` applies a fixed rule (l/d/j/c/n/m/s/qu + space + a vowel or h → the
elided form) to every model-written field of E1–E3 before validation; a text that already holds an apostrophe is
left alone, and "t il" is never touched. Phase-1/2 prompts and the shared system template are untouched.
**Consequence.** Zero dropped elisions across every later live sample; a separate, unrelated diacritic garbling
(F2, "trâne" → "tr¤ne") has no equivalent fix and is carried forward as a follow-up.

## DEC-145 — The script's estimate chip shows the number of calls E1's own exact ask will make; its message's
range is the worst case
**Context.** The chip first showed the range's top end (12, for a body-high-9 episode) against an exact ask of
11 — technically honest but needlessly alarming next to a button that would make exactly 11 calls.
**Decision.** The chip's `llm_calls` is `workflow.script_units`'s exact count for the episode's own slots; the
estimate message still names the low–high range so the tooltip explains why the number could differ for a
different-length episode, and lists any paid link skipped rather than reached.
**Consequence.** Matches DEC-139's exact-ask design: what the chip promises is what "Write" actually spends.

## DEC-146 — The Ready card's "story is ready" state follows the server's own derived status
**Context.** A voice regeneration on an already-`ready` story clears that character's cast approval (DEC-123)
and so the derived status, without ever touching `approvals.season` directly; the card had been keyed on
`approvals.season` and kept claiming "ready" after that (F9).
**Decision.** The dashboard reads `story.status === "ready"` (`store.derive_status`'s own answer) for the Ready
card and the Open-episode link, instead of inspecting `approvals.season` on its own.
**Consequence.** The card falls back honestly the moment any group approval it depends on clears, exactly
mirroring what the episode steps themselves would refuse.

## DEC-147 — "Measure with real voices" stays disabled whenever only the consistency check is missing or stale
**Context.** The runner fills whatever a script is missing before it does anything else, including a stale E4 —
so pressing Measure on a script whose only gap is the check would trigger a consistency call the Measure
button's own estimate never showed (found during the fix round).
**Decision.** The button (and its estimate fetch) stays disabled while `episode.state.report` is `"stale"` or
`"none"`, with the hint "Check the consistency first.", exactly like Approve above it.
**Consequence.** Every call a user's click can trigger is one the click's own chip already named — never a
hidden extra one.

## DEC-148 — The fix round's changes were verified on a copy of the live story, not on the story itself
**Context.** The human's choice (2026-09-27): don't risk the one real fixture mid-fix, and the free-tier evidence
needed was already about Gemini alone.
**Decision.** Every round's live check ran on a scratchpad copy (`run_cli.py`, `GOOGLE_API_KEY` only,
`usage.json`/`spend.json` redirected so the real files never moved) rather than on `b1104ec66b05` itself.
**Consequence.** Live episode 1 still carries the pre-fix 39.7 s script and a storyboard whose stored shot
durations predate the F7 fix, both left approved as the human chose; re-timing, re-planning or rewriting it will
pick up every fix at once.
## DEC-150 — A finished job's Live activity total stops where the job stopped; no step clock
**Context.** `/clips/job/b37b36a9b34e` (completed, 54 min) read "33h 56m on this step · 34h 50m total" ~34 h
later: `LiveActivity` measured both clocks to the reader's `Date.now()`; only the 1 s ticker stopped. The job has
no `finished_at`; `updated_at` moves again when a story step is approved or superseded (hours later).
**Decision.** `jobClocks(job, events, running, now)` in `web/dashboard/src/time.js`: running jobs unchanged; a
finished job (TERMINAL, `awaiting_approval` included) shows no "on this step" and a total from `created_at` to the
earlier of `updated_at` and the last feed line's `ts` (either may be missing; neither → no total). Chosen by the
human over `updated_at` alone and a backend `finished_at` stamp (model/store/persistence change, restart to ship).
Id DEC-150 leaves DEC-126+ to the phase-3 session. Tested by running `time.js` with the system Node from pytest,
skipped without Node or `node_modules` (DEC-012 holds: CI still needs pytest only), plus a text guard.
**Consequence.** Every LiveActivity caller (Clips job page, story step panels, EpisodeStudio, the wizard's step-job
history) shows a fixed duration once a job ends. A finishing path that ever goes silent before its end would
understate the total by that silence (A-070).

## DEC-151 — Never lose a paid generation: a generation cache and submit journal, opt-in and story-scoped
**Context.** A paid generation that failed after its submit was billed, unrecorded and lost (DEC-106's open item);
nothing cached an answer, so a rerun re-bought it. `clipping/providers/generation.py` had no seam for a durable
record between a submit and its outcome.
**Decision.** New stdlib module `clipping/providers/gencache.py`: a canonical sha256 key over kind, link, prompt,
negative, reference bytes, seed, size, text, voice, rate, pitch, template and take; a `gen_journal_v1` entry per
key (`sending|submitted|done|failed|lost`) written atomically beside its outputs. `run_generation_chain` takes an
optional `cache=`; when given, `cache.lookup` runs before the paid, budget and limiter gates on every candidate —
a `done` entry is served from disk at $0, a `submitted` entry is resumed, a `sending` entry left by a crash is
booked conservatively first. Booking itself is done by the journal, through the caller's own `book(entry)`.
Without a cache, nothing changes: phase-2 sheets, the Settings chain test and clip jobs pass none (RC-A2).
**Consequence.** Only phase 4's assets and regenerate paths pass a cache, so every other generation call keeps its
exact pre-phase-4 behaviour, proven by the unedited pinned tests (`test_story_measure.py`, `test_story_refimages.py`,
`test_generation_chain_api.py`, `test_generation_chain.py`, `test_image_adapters.py`). `FalAdapter` now exposes
`_submit/_poll/_fetch` plus `resume(...)`, so a queued request's `request_id` survives a crash between polls.

## DEC-152 — Resuming a journaled request is not a re-submit
**Context.** DEC-106 allows a paid link exactly one attempt; a journal that simply retried a submitted request on
every rerun would silently buy a second generation for a request that was merely slow or interrupted.
**Decision.** A `submitted` journal entry is resumed by polling and fetching the same `request_id`, retried up to
`MAX_ATTEMPTS` and never passed through the budget gate again, across runs. A fal FAILED/CANCELLED answer moves the
entry to `failed`; a 404/410 on the status or response URL moves it to `lost`. Both stay booked and the link then
ends for that run — no second submit on the same link, though the chain still tries its next link through its own
gates, and a later explicit run may submit again. A poll-budget timeout or a cancel simply leaves the entry
`submitted` for the next run.
**Consequence.** DEC-106's open item is closed: a paid request can no longer vanish between a submit and a crash.
If the journal or its booking cannot be written right after a submit, the whole chain stops rather than risk an
unrecorded charge, printing the `request_id` and URLs to the activity feed.

## DEC-153 — Conservative booking: booked unless proven unbilled
**Context.** Sync paid adapters (Gemini, OpenAI images) and ambiguous transport failures had no rule for whether a
possibly-billed call should be recorded before its outcome is known.
**Decision.** A paid request is booked unless an HTTP 4xx answer (400/401/403/404/409/413/422/429) or a failure
before the transport was ever called proves it unbilled. Everything else — a 5xx, a timeout or reset after
sending, a 200 with no output, or an unknown outcome after a crash — is booked, carrying a `note` explaining why.
**Consequence.** A crash between two writes can over-report the ledger by at most one estimate; it can never
under-report. `CostLedger` rows gained an optional `note`, absent unless given, so every exact-row pinned test still
holds unedited.

## DEC-154 — Python fixes an image's seed before the call; a regenerate's fresh seed is persisted as `pending`
**Context.** The generation cache's key includes the seed, so a `None` seed (today's default on most calls) would
make every image request key-less and bypass the cache entirely — exactly today's behaviour, but never cacheable.
**Decision.** Before an image call, the assets step fixes the seed itself: `pending.seed` if a regenerate left one,
else in `prompt_only` mode the first `@char` portrait's recorded seed (else the place plate's), else in
`references` mode `derive(story_id, ep, shot_id)`. A regenerate picks a fresh random seed and writes it as
`pending` *before* the call, so a retry of the same note hits the same cache key. A request with no seed still
bypasses the cache, exactly as before.
**Consequence.** Extends DEC-124 (fill missing, persist a fresh seed as pending): identical shot requests now share
one cached image and are booked once, proven live (24 shots → 20 calls on the phase-8 fixture).

## DEC-155 — Shot images live in `storyboard.shots[].assets`; grid approval and audio resolution live in
`assets.json`, guarded by a fingerprint
**Context.** The shot `assets` object is closed with five required keys (the phase-3 shape); phase 4 needed a
place for the generated image, its provider/model/route/seed, and a place for line word-source, SFX/BGM
resolution and the episode's own approval — without a second source of truth for the image.
**Decision.** The shot's `assets` object gains optional keys (`model`, `consistency`, `route`, `prompt_hash`,
`locked`, `note`, `generated_at`, `est_usd`, `cache_key`, `pending`); it stays closed with the original five still
required, so every phase-3 board still validates unchanged. `assets.json` (`episode_assets_v1`) holds per-line
word source, SFX/BGM resolution and one `approved{at, fingerprint}` — the fingerprint is a sha256 over every
shot's `(prompt_hash, image sha256, locked)`, every line's `(text_hash, voice, audio sha256)` and the SFX/BGM
files, so approval is derived stale the moment the fingerprint changes; no phase-3 writer had to learn to clear
it. Measuring and re-timing the board clear no approval (extends DEC-135).
**Consequence.** Rejected: mirroring image records into `assets.json` as well — that would be a second source of
truth beside `shot.assets`. RC-A8 held: a copy of live ep01 validated unchanged under the new schema.

## DEC-156 — The AI-Story renderer is pure FFmpeg, separate from the clip studio, with its own golden-render
parity rule keyed by ffmpeg version × architecture
**Context.** `clipping/studio` renders clips and must stay untouched (RC-A1); the AI-Story renderer needed its own
home, and one framemd5 cannot hold across the host's ffmpeg 6.1.1/aarch64, the container's 7.1.5/aarch64 and CI's
x86_64.
**Decision.** New stdlib package `clipping/aistory/render/`; `clipping/studio/ffmpeg_utils.detect_video_encoder`
is imported lazily and only for the opt-in `encoder=auto`. `tests/test_aistory_render_golden.py` never skips and
keys its framemd5 digest by `"<ffmpeg version>/<machine>"`; an unknown key fails loudly, printing the digest and
how to record it (`tools/render_golden.py --record`) rather than silently passing. CI is pinned to
`runs-on: ubuntu-24.04` with `apt-get install ffmpeg`, still `pip install pytest` only (DEC-012 holds). A
stdlib test over a committed `tests/fixtures/render_layer_sha256.json` guards `clipping/studio/**` and
`clipping/story/**` byte-for-byte, because CI's shallow checkout cannot run `git diff` against an old commit.
**Consequence.** Keys recorded for host `6.1.1-3ubuntu5/aarch64`, the container's `7.1.5-0+deb13u1/aarch64`, and
CI's `x86_64` (pushed once at stage 7 per the human's Q5 answer). At close, `git diff --stat 1367d75 --
clipping/studio clipping/story` is empty and the guard test is green — RC-A1 held through all 17 stages.

## DEC-157 — Audio-mix constants; the loudness target amended mid-phase to TP −2.5 with AAC PNS disabled
**Context.** The plan set weights (dialogue 1.0, BGM 0.30, SFX 0.8), `sidechaincompress 0.03/8/20/300`,
`amix normalize=0`, a 0.5 s (`cut_to_black`) / 50 ms (`hard_stop`) bed fade-out, a 48 kHz mix, and a two-pass
`loudnorm` target of `I=-14:TP=-1:LRA=11`. Tier-2's live FR render measured **+0.57 dBTP** after the AAC encode
against that −1 target: the mix itself peaked +1.30 dBFS, so `loudnorm`'s linear pass could never fit under a −1
ceiling and silently fell back to its dynamic mode; on top of that, AAC's PNS (perceptual noise substitution)
resynthesised a TTS noise burst (13.19–13.31 s) with build-dependent peaks (−0.7 dBTP on the host, +0.6 on the
container).
**Decision.** `render/profiles.py`'s `LOUDNORM_TARGET` moves to `I=-14:TP=-2.5:LRA=11`, and the final AAC encode
now passes `-aac_pns 0`. Warning thresholds are unchanged (−1.0 dBTP, ±1 LU either side of −14 LUFS), and
`clipping/loudness.py`'s own `TARGET` for clips is untouched — `loudness.py` only gained an optional `target=`
parameter, so clip output is byte-identical. Measured after the fix, on the real mix on both host and container:
FR I −14.2 / TP −2.3 / LRA 4.9; and later, on the finished episodes: FR I −14.2 / TP −2.3, EN I −14.1 / TP −2.1.
Fix commit `2d26371`.
**Consequence.** A-066 (the −1 dBTP target would survive the AAC encode) is invalidated by this measurement. The
golden render's video framemd5 was unaffected (audio-only change) on both host and container.

## DEC-158 — Tier-1 audio is one absolute timeline; `acrossfade` is deferred to phase 6
**Context.** Rejected alternative: per-shot audio segments joined with `acrossfade`. Every dissolve would then
risk desync and cost an extra encode, and "no line inside a transition window" would no longer be directly
checkable against a single timeline.
**Decision.** Every dialogue line, SFX cue and the BGM bed are placed on one absolute output timeline (`adelay`
per line and per SFX anchor; the bed `-stream_loop`ed and trimmed to the total). `acrossfade` is not built until a
shot carries its own audio, in phase 6.
**Consequence.** The "no line falls inside a transition window" invariant is a plain arithmetic check on the
timeline, proven by golden tests in stage 6 (line onsets within 0.02 ms of target on the fixture).

## DEC-159 — Every burned text is libass from ASS built in Python; no PIL in the render path
**Context.** Rejected alternative: PIL for the end card and cover. CI has no PIL, so the golden render would have
to skip there, and the project would carry two text engines with two typographies.
**Decision.** Subtitles (`word_pop`, `two_line`, `none`), the hook overlay, the `ai_label`, the end card and the
cover are all ASS documents built in Python (`render/subtitles.py`) and burned with libass (`ass=` in the
filtergraph). Fonts resolve in order: the template's family file in `custom_fonts/` (checked with
`clipping.fonts.font_file_declares`), else the committed Montserrat Black; the chosen font is staged per render
into `render/fonts/`, and the manifest records the family, file, sha256 and why it was chosen.
**Consequence.** One text-rendering path end to end, provable by the golden render test, which never skips.
Stage 5's live check also found and fixed a `two_line` accent too close to its highlight colour to read — folded
into DEC-169 below.

## DEC-160 — SFX are self-made by a committed generator; BGM is the shipped Clips tracks through `bgm_index.json`
**Context.** Human's CLARIFY answer 1 (2026-09-28): self-made SFX from a committed generator for every cue in the
seven templates, plus the existing 15 `assets/bgm/` tracks, with the licence recorded as "shipped with Clips,
source unrecorded" pending a CC0 replacement.
**Decision.** `tools/make_sfx.py` (stdlib `wave`/`math`/seeded `random`) synthesises 33 distinct cue names across
the seven packs, 22.05 kHz mono 16-bit WAV, each ≤ 3 s, listed in `sfx_index.json` with sha256 and licence
"self-made". `bgm_index.json` maps the 15 existing tracks to every mood any style names. The mood is the emotion
with the largest summed scene duration (ties go to the earliest scene), mapped through `emotion_to_mood`; the
track is picked deterministically by `sha256(story_id:ep) mod n` among tracks carrying that mood.
**Consequence.** A-068 (the BGM licence) stays UNCONFIRMED by design — it is a licence fact, not something code
can settle. CC0 replacements for the 15 tracks are a follow-up, not this phase's job.

## DEC-161 — Render, metadata, fast-track and a metadata regenerate end `completed`, amending DEC-108 and DEC-109
**Context.** DEC-108 ("a step ends awaiting approval") does not fit render, metadata or fast-track, which have
nothing for the user to approve. DEC-109 ("only steps that call an API are jobs") does not fit render, which
calls no API but needs minutes of CPU on the shared worker slot.
**Decision.** `steps.ends_completed(step, params)` makes render, metadata, fast-track and a `metadata:` regenerate
end `completed` instead of `awaiting_approval`; every other story step is unchanged. Render is still a job — it
occupies the one worker slot for its CPU time — even though it calls no LLM or image API.
**Consequence.** Both conflicts are resolved inline rather than reopened as new rules; DEC-108 and DEC-109 each
carry a one-line amendment pointer to this entry.

## DEC-162 — Fast track is one resumable job under a predictive 60-minute budget, with its own auto-approval rule
and a stop before any paid spending
**Context.** Rejected alternative: chained jobs, one per step. Their state would have to survive restarts, and
auto-approvals would have to cross job boundaries; the DEC-131 precedent (one resumable job with no cross-restart
orchestration state) fits fast track directly.
**Decision.** `steps/fast_track.py` runs script → storyboard → assets → render → metadata in-process under one
`Budget(3600)`. It auto-approves the script only when it is complete, E4 passed and is fresh, and its timing is
not `over` or `under` — approve-anyway is never used by fast track. Storyboard and assets have their own,
narrower auto-approval rules. Any paid part of the assets estimate needs `allow_paid` on and every cap (episode,
day, story) satisfied; otherwise fast track stops before making any generation call, showing the numbers. Every
stop ends the job `failed`, naming the sub-step; "Continue" re-runs and makes 0 repeated calls.
**Consequence.** Measured live (A-076): the EN Midnight Fridge fast track needed 8 presses across a 48-minute wall
time (including a mid-run bug fix and restart), but any single job press ran ≤ 9.4 minutes, well inside the
60-minute budget; summed job time was about 26 minutes.

## DEC-163 — Signed story media uses its own HMAC context and a closed file list; DEC-048 is untouched
**Context.** A `<video>`/`<img>` tag cannot send the bearer header DEC-048's clip signatures rely on, and blob
video breaks seeking — both already rejected for clips (DEC-048, DEC-113).
**Decision.** `web/api/auth.py` gains a second signing context, `b"rzc-story-media-v1"`, over a length-prefixed
`("episode-media", story_id, ep, name, exp)` payload, checked against an allow-list of exactly two names
(`episode_final.mp4`, `cover.jpg`). It reuses `media_expiry` bucketing so the signed URL stays byte-identical
across `EpisodeStudio`'s 4-second poll. `require_token` gains one new branch, checked after the existing clip
check; a tokenless deployment (DEC-092/105) still opens everything.
**Consequence.** RC-A5 held: a story signature opens exactly the one named file and nothing else — verified
against every other story route, every clip route, six path-traversal attempts and both signature kinds tried
against each other's routes.

## DEC-164 — The subtitles toggle is a per-episode render parameter; switching it re-runs only the audio-to-mux
stages, never the shots
**Context.** A soft WebVTT track cannot draw `word_pop`'s per-word pop animation, so subtitles must be burned
in per render rather than served as a separate track.
**Decision.** `subtitles: style|word_pop|two_line|none` is a render parameter. Every per-shot clip (stage S) and
the end card (stage E) stay cached across a subtitles change; only the audio mix, loudness passes and final mux
(A…M) re-run.
**Consequence.** Measured live: a subtitles switch on the 21-shot FR episode ran in 89–90 s against a full
render's 160 s, all 21 shots reported `cached`.

## DEC-165 — Forced alignment is opt-in per assets run; the word source is stored per line
**Context.** Spec 6.4's timing order is provider words, then optionally forced alignment, then an even split as
the last resort; alignment costs an extra STT call per line and should not run unasked.
**Decision.** `params.align_words` on the assets step opts a run into forced alignment: `stt.transcribe` runs on
each line's audio, its words are matched to the script's own words with `difflib`, and any gap is interpolated
(`wordtiming.py`, pure). Without the flag, provider word cues are used, falling back to an even split labelled
"approximate timing". `words_source` (`provider|alignment|even_split`) is stored per line, plus `aligned_by` when
alignment ran.
**Consequence.** The STT fake in stage 8's tests is called only when `align_words` is set, proven by a dedicated
test; the grid can show an "approximate timing" flag per line without guessing.

## DEC-166 — M1 is one LLM call per platform; the teaser, pinned comment and cover are built by Python
**Context.** DEC-027/107/138: every LLM call belongs on a click's own estimate chip and should be sized against
measured limits, not guessed ones.
**Decision.** `steps/metadata.py` makes exactly one `llm_call.call_json` per platform (tiktok, shorts, reels) on
free links only (DEC-115), against a schema of `{title, description, hashtags[3-6], hook_text}` in the story's
language, plus `title_en`/`hashtags_en` for French stories. The `next_episode_teaser` is appended to `description`
and the pinned comment built by Python, not asked of the model; the cover is the hook scene's first shot plus
`hook.on_screen_text` (else M1's `hook_text`), rendered as an ASS overlay. The prompt's token cap is 330 (DEC-138),
sized from the measured French-Reels worst case (216 tokens × 1.3 headroom × 1.15).
**Consequence.** Proven live: FR metadata (3 platforms, 6 s, 3 free calls) produced `title_en`/`hashtags_en`,
teaser-ended descriptions, "PARTIE 2 →" and a cover with the hook text; EN metadata produced "PART 2 →" with no
EN-duplicate fields.

## DEC-167 — Phase 4's Tier-2 was walked by me; the human watches both finished episodes
**Context.** CLARIFY answer 3 (2026-09-28): the plan's live walk (steps 3–15, both episodes) is done by the
session at 375 px, measuring everything the spec's numeric gates ask for; only the final phone watch is the
human's own check.
**Decision.** Every Tier-2 step through resilience (steps 1–15) ran under this session's own judgement —
approvals, approve-anyway calls, operator edits (the EN script's summary and one line) and fix decisions were all
mine, logged as they happened. Step 16 — watching both finished episodes on the phone over the tailnet and
acknowledging them — is reserved for the human alone.
**Consequence.** Every numeric gate in the plan's §4 (length, loudness, ducking, no overflow, resilience) is
already measured and recorded before the human ever opens a phone; their watch is a taste and framing check, not
a re-verification of the numbers.

## DEC-168 — The assets step paces itself against free-tier rate limits instead of failing an item outright
**Context.** Tier-2 finding T2-F1: the live FR assets run (`3a415855191c`) ended with only 4/21 images and 15/18
lines — pollinations answered HTTP 402 except for about one image a minute, and Gemini TTS answered 429 after
about four calls a minute, both of which the step had been treating as a hard per-item failure.
**Decision.** After its first pass, `steps/assets.py` retries the items a free tier held back — an HTTP 429 from
a free non-local link, or an HTTP 402 specifically from pollinations, and never an item whose chain already sent
a paid request — in further rounds, each opening with one cancel-aware 60 s pause (`RATE_LIMIT_PAUSE_S`) that
serves every held-back provider at once. A pause only starts if the predictive budget has room for it plus the
next call; a provider that makes no progress across a whole round is given up on for that run (the existing
pinned-voice advice still applies). Every retry reuses the same seed/voice/cache key, so nothing is bought twice.
**Consequence.** Fix commit `d9a2e92`. Measured live after the fix: the FR run finished 21/21 images and 18/18
lines in 17 minutes over 14 paced rounds, roughly one image a minute, still $0. The known gap, T2-F11: a single
pollinations HTTP 500 inside a paced round (whose own retry then answers 402) still gives that provider up for
the round exactly as a 402 does — "Continue" resumes it on the next press; recorded as a follow-up, not fixed
this phase.

*Amended by DEC-190 (2026-09-30): the cast step now paces itself against free-tier rate limits the same way,
sharing the predicates from the new `steps/pacing.py`.*

## DEC-169 — Subtitles show the script's own tokens, timed by the provider's word cues; `two_line` speaker
accents must clear WCAG contrast against the outline
**Context.** Tier-2 findings T2-F6 and T2-F7 on the live FR `two_line` render: Broccolia's speaker accent
(`#1E1E24`) sat unreadable against the subtitle's 3 px black outline because the existing distance rule skipped
an accent that equalled the style's own highlight colour; separately, Edge's provider word cues carry bare words,
so the burned subtitles were dropping the script's own punctuation (reading "sécurité Ils" instead of
"sécurité. Ils").
**Decision.** `render/subtitles.py` now times the script's own word tokens by aligning them to the provider's
cues with `wordtiming.align` (difflib on normalised words, interpolating any unmatched token), falling back to
the provider's own tokens only when nothing matches. Every `two_line` speaker accent must clear
`TWO_LINE_MIN_CONTRAST_RATIO` (WCAG 4.5:1) against the outline, on top of the existing distance-from-highlight
and distance-from-each-other rules; the neutral fallback ramp is now light greys that pass.
**Consequence.** Fix commit `207f8c7`. Live re-render: Kiwilo `#E4572E`, Mangella `#FFFFFF`, Broccolia `#C5C5C5`,
all readable on the outline; punctuation restored ("sécurité. Ils se trompent.").

## DEC-170 — French spaced punctuation joins its preceding (or following) word in subtitles
**Context.** Tier-2 finding T2-F5: the live FR render popped a lone "?" as its own subtitle word at 45 s, because
French writes a space before `? ! : ;` and inside `« »`, and a plain whitespace split (plus a provider's separate
punctuation cue) left punctuation-only tokens.
**Decision.** `render/subtitles.py._join_spaced_punctuation` runs on both the provider-word and even-split paths
of `_line_word_spans`: a token with no letter or digit joins the word before it (keeping the space, extending the
end time); an opening mark (`«`) joins the word after it; a line made of punctuation alone still keeps one span
rather than vanishing.
**Consequence.** Fix commit `d360b66`. The golden render's framemd5 was unaffected (a text-only change); every
subsequent live render carried correctly-joined punctuation.

## DEC-171 — E1 on a story with no props asks for an always-empty list and repairs a stray reply before validation
**Context.** Tier-2 finding T2-F9: the EN fast track stopped at the script twice — on a story with no props,
`prompts.e1_schema` had been enumerating prop ids only when some exist, so with none `props` items fell back to a
bare string, and `_E1_ASK_TEMPLATE` still asked for "0 to 4 of the existing props" with no roster to choose from.
The French story had passed only because its non-empty prop enum happened to constrain the model.
**Decision.** `prompts.py` gains an `_E1_NO_PROPS_LINE` ("props: always [] — this story has no props") slotted
into the ask whenever a story has none, plus a schema description saying the same; the model-facing schema stays
in the strict-mode subset (no `maxItems`, matching `schemas.py:670-677`). `steps/script.py._repair_e1_reply`
empties every scene's `props` before validation when the story has no props, defensively, in case a reply still
strays. A story that does have props is asked byte-identically to before, and an unknown prop id is still
refused and retried.
**Consequence.** Fix commit `2fbd9f0`. Proven live: the EN fast track's next script attempt wrote clean on the
first try (67 s, E1 + 8×E2 + E3 + E4).

## DEC-172 — A cancelled render waits 1.0 s before killing ffmpeg; a stalled stage still waits the full 3.0 s
**Context.** Tier-2 finding T2-F12: cancelling an EN render mid-final-pass answered the API in 0.17 s, but the
worker's own "cancelled during F" line landed 3.38 s later — ffmpeg's SIGTERM handler flushes x264's lookahead
buffer (about 3 s at 1080×1920) before it can exit, so the cancel was riding the same `KILL_GRACE_S = 3.0 s`
meant for a genuinely stuck stage.
**Decision.** `render/runner.py` gains `CANCEL_GRACE_S = 1.0`, passed by `_wait` to `_stop` only on a cancel; a
plain stage timeout still waits the full `KILL_GRACE_S = 3.0` before SIGKILL, so a slow-but-alive stage is not cut
short.
**Consequence.** Fix commit `7083c39`. Live repeat: the same cancel-during-F now completes in 1.43 s, down from
3.38 s, with the previous `episode_final.mp4` untouched and the partial file kept.

## DEC-173 — Auth is opt-in: a token is required only when `API_TOKEN` is set
**Context.** The human, 2026-09-29: "remove all access restrictions to the app, it's only local or via tailscale",
answered as "auth off by default with an opt-in token", done as its own task before AI Story phase 5. Until then a
token always existed (DEC-037: `$API_TOKEN`, else `data/api_token`, else a generated one stored 0600), and this VPS ran
tokenless only through the gitignored `DISABLE_AUTH=1` override (DEC-092/105). Two paths reach the public internet —
the Caddy `domain` profile and the Kaggle notebook's ngrok tunnel — and a naive flip would open both, including
`POST /api/shutdown` and `PUT /api/settings` (provider keys, paid spending). Two traps in the old code:
`token_is_valid` never accepts an empty value (so "no token" must short-circuit before it, or every route turns
401), and the signing keys used `str(token or current_token())` (so no token would sign with a public key).
**Decision.**
- `auth_enabled()` is the single source: on iff `API_TOKEN` is non-empty after trimming and `DISABLE_AUTH` is not
  set. `data/api_token` is neither read nor written; a leftover file is named in the startup banner ("set API_TOKEN
  to its value to keep token auth"). The token is read live from the environment, with no cache.
- Open: `require_token` returns before any header or signature check; `media_url` / `story_media_url` return the
  plain path; `_signing_secret` raises rather than sign with no token, and verification with no token is `False`. The
  banner says the API is OPEN and never prints a token.
- Public paths never start open: compose passes `DOMAIN` to the backend, which refuses to start when `DOMAIN` is set
  without a token (`open_public_exposure`); the Kaggle notebook generates and prints a token when no `API_TOKEN`
  secret is set; the Funnel section of `docs/deploy-tailscale.md` says to set `API_TOKEN` first.
- Open mode keeps one restriction: `CrossSiteWriteGuard` (pure ASGI) answers 403 to a POST/PUT/PATCH/DELETE carrying
  `Sec-Fetch-Site: cross-site` unless its `Origin` is in `ALLOWED_ORIGINS`. It blocks other websites open in the
  same browser, never the user: same-origin pages, same-site dev servers and non-browser clients pass. It steps
  aside when a token is set (the gate already refuses a cross-site request, which cannot carry the header). The
  human was offered its removal mid-Tier-2 and answered "Keep as is".
- Token-on deployments behave exactly as before: header or `X-API-Key`, clip and story signatures (DEC-048, DEC-113,
  DEC-163), 401 not 422.
- `tools/rzclips-fetch.py` runs without a token and sends `Authorization` only when it has one (A-081).
**Consequence.** Supersedes DEC-037's "a token always exists", DEC-092's override and DEC-105's "revisit" note; the
VPS's override moved to `/home/ubuntu/backups/auth-opt-in/`. Tests: `tests/test_auth_opt_in.py` (47, fail-first
42/47 on `b60938e`), the SPA-fallback test now proves both modes, notebook and fetch-tool tests; the four
token-generator tests are gone. **Accepted risk:** a third-party install that relied on the generated token and is
reachable some other way (its own proxy, an open port) becomes open on upgrade, with only the banner and the
CHANGELOG Security entry to say so (A-080). Verified live 2026-09-29 (deploy `b76f0a9`): no sign-in on the tailnet,
clips and the FR episode play and seek from plain URLs, cross-site POST 403, a scratch token-on server 401/200, and
`DOMAIN` without a token exits 3 from source and from compose. The human acknowledged on the phone.

## DEC-174 — The paid-path test runs right after the auth task, on fal.ai, with a $3 hard ceiling
**Context.** Phase 5's plan put the paid live test at its end (stage 14b), after the human funds providers. On
2026-09-29 the human put $10 on fal.ai ("Do not use all 10$") and, asked when and how much, answered "Right after
auth, $3 cap".
**Decision.** Stage 14b's walk (a)–(d) — paid links refused without `allow_paid`, a cap below the estimate refused
with both numbers, a real paid assets run with one submit per request and the journal booked at submit, a forced
poll failure resumed without re-buying, a same-input regenerate served from the gencache, ledger vs fal's
dashboard — runs before phase 5 on fal only, never above $3 in total. Nothing paid runs until the human has seen the
per-step estimate and said go; `allow_paid` goes back off afterwards. 14b(e) (re-edit and partial re-render on paid
assets) waits for phase-5 stages 7–8.
**Consequence.** Phase 5's stage 14b shrinks to (e). Phase 5 takes ids from DEC-175 / A-082.

## DEC-175 — A per-megapixel price bills whole megapixels of 1024x1024, rounded up (fal's rule)
**Context.** The paid test (DEC-174) booked $0.6934 for 35 fal requests, and fal's dashboard showed $0.70. fal's
FLUX.1 [schnell] page says "$0.003 per megapixel. Images are billed by rounding up to the nearest megapixel", and fal
prices a 1024x1024 image as one megapixel. `pricing.estimate` multiplied by the exact size in decimal megapixels, so
a 720x1280 shot was booked $0.0028 against fal's $0.003, and a 576x1024 preview $0.0018 against $0.003.
**Decision.** `per_megapixel` prices count whole megapixels of `MEGAPIXEL = 1024 * 1024` pixels, rounded up with an
integer ceiling. flux-schnell is the only per-megapixel price today. Its note now reads "$0.003 at 720x1280, $0.006
at 1080x1920".
**Consequence.** Estimates and bookings for flux-schnell match fal's bill: 13 × $0.003 + 22 × $0.03 = $0.699, which
fal shows as $0.70. Every estimate is at least as high as before, so a cap never lets more through. Deliberate test
re-pins (named in the log): `test_pricing.py` (1080x1920 → $0.006, 1024x1024 → $0.003), `test_image_adapters.py`,
`test_generation_chain_api.py` and `test_style_preview.py` (3 previews → $0.009). New test: five sizes across the
rounding edge. Stored ledgers keep what they booked.

## DEC-176 — Tier-1 runs once per stage, in parallel; implementation agents run only the targeted tests
**Context.** The human, 2026-09-30: "The test are running for littéral hours and make me loose days of work, find a
workaround to take less time on tests and still keep the best quality of produced code". Measured on phase 5's
stage-9 tree: each stage ran the full local suite (~7.8 min) and the CI-env suite (~6.5 min) once or more inside the
implementation agent and again by the orchestrator, single-core on a 4-core VPS — 25–40 minutes of test time per
stage. One test (`test_a_paid_link_is_never_called_and_a_keyless_one_never_built`, since phase 3) waited 60 s of real
time on gemini's rate limiter.
**Decision.**
- Tier-1 runs with pytest-xdist, 4 workers, in both environments: local `PYTHONPATH=~/.cache/rzc-xdist python -m
  pytest -p no:warnings -n 4`; CI env `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs:~/.cache/rzc-xdist python3 -m
  pytest -p no:warnings -n 4`. pytest-xdist 3.8.0 + execnet 2.1.2 live in `~/.cache/rzc-xdist` (installed with
  `pip install --no-deps --target`; PEP 668 refuses a user-site install and nothing is forced): a local dev tool, not
  a project dependency; `/tmp/cilibs` and `.github/workflows/ci.yml` are unchanged, so the CI-env run still mirrors
  CI's packages.
- The full suites run **once per stage, by the orchestrator**. Implementation agents run only the new and touched
  test files and their neighbours (and show fail-first there); they never run the full suites.
- Unchanged: fail-first for every behaviour test, both environments every stage, compileall, the real-ffmpeg golden
  and partial == full tests inside the suite, and the CI-env run before any push.
- A test never waits on wall-clock time it does not assert on: the 60 s test now paces its limiter on a fake clock
  (0.21 s; a new `clock > 0` assertion proves the limiter still paced).
**Consequence.** Stage 9's tree: identical counts serial and parallel (local 6278 / 1, CI env 5497 / 751, 0 failed),
14 min 21 s → 6 min 19 s for both suites; with no duplicate runs, test time per stage drops from 25–40 min to about
6 min. Amends the global agreement's Section 9 practice only in who runs Tier-1 and how (the human's chat instruction
takes precedence). A-083 records the xdist-safety assumption.

## DEC-177 — Series memory is the only carrier of continuity between episodes
**Context.** Episode 2 needs to open on a true recap and pay off a real hook. The season arc's own `open_hooks_out`
are only suggestions, and nothing else in the story documents records what an episode actually did once it aired.
**Decision.** Episode N's approved script is read once, by one S3 call, into that episode's own series-memory entry
(a recap, the hooks it opened and closed, relationship deltas). Every continuity-dependent prompt — E1's recap and
open-hooks block, E3's recap scene, E4's payoff check, N1's proposals — reads only the folded series memory, never a
prior episode's script directly.
**Consequence.** Continuity is exactly as good as the entries are: an episode approved with no memory entry does not
propagate (the gate, DEC-179), and a hand-written recap with no entry behind it no longer passes. The model's
continuity input stays small and auditable instead of growing with every prior episode's full text.

## DEC-178 — Memory is a fold over per-episode entries, not incrementally merged state; re-running an episode's
memory is idempotent
**Context.** The rejected alternative (incrementally merging each S3 reply into one running state) would
double-apply a re-run's deltas — a memory step re-run after a script edit would count its own hooks twice.
**Decision.** `series_memory.entries[epNN]` holds each episode's own entry (recap, hooks_opened, hooks_closed,
relationship_deltas, script_rev). `recaps`, `open_hooks` and `relationship_state` are *derived* from a pure fold
over `entries` (`fold_memory`), stored so existing readers (`memory_section`, E4, the spec-shape test) see them
unchanged. `open_hooks_before(season, N)` folds only the entries before N — the function every prompt for episode N
must call, never the stored `open_hooks`, which folds later entries too. Re-running episode N's memory replaces its
one entry and re-folds from scratch, so a re-run is idempotent. A later episode's memory that closes a hook the new
entry no longer opens raises `FoldError`, naming the later episode; nothing is written.
**Consequence.** An edited-and-re-approved script re-runs memory safely. The fold is deterministic and
order-independent by episode number; a season with hand-written recaps but no entries gets no hooks and no payoff
demand, so old data degrades gracefully rather than breaking. Proven by golden fold cases and by all four live
seasons validating unchanged (stage 1).

## DEC-179 — Memory runs on an approved script, goes stale on a script change, and gates episode N+1's script,
storyboard and fast track (amends DEC-130)
**Context.** DEC-130 gated episode N+1 on `series_memory.recaps["ep{N-1}"]` existing at all — any string would do,
including a hand-written one with nothing behind it. Phase 5 needs the gate to mean something: a real, current entry
from the script that was actually approved.
**Decision.** The `memory` step needs episode N's script **approved** and records the script's `rev` in the entry.
`entry_is_stale` compares that `rev` against the script's current one: a later edit (even one that re-approves)
makes the entry stale. Episode N+1's script, storyboard and the fast track — only while any of them would actually
write — refuse until `entries[epN]` is both approved and fresh, each refusal naming the exact missing piece
(missing, not approved, or stale).
**Consequence.** A hand-written recap with no entry no longer passes (no live season had one, so nothing broke).
The gate does not touch assets, render, metadata or their regenerates — only writing new script/storyboard content
for N+1.

## DEC-180 — Hooks are closed by an enumerated value, never fuzzy matching; E1 marks `pays_off`, E4 checks it
**Context.** The rejected alternative, fuzzy-matching hook text, is non-deterministic — the same reply could close a
hook on one run and miss it on the next.
**Decision.** `hooks_closed` in an S3 reply is an enum of the hooks open before that episode, verbatim — never free
text. E1 for episode ≥ 2 with open hooks is shown at most `PAYOFF_HOOKS_MAX` (4) of them, oldest first, and a
per-scene `pays_off` field (one hook per scene) that at least one body scene must fill; a framing scene (recap,
hook, cliffhanger) that pays one off has it cleared before validation. A deterministic pre-check (`payoff_issues`)
runs before E4: no body scene paying off anything, or a scene naming a hook that isn't open. E4 gains the
`hook_payoff` issue kind and judges whether the lines actually deliver the payoff, shown only earlier episodes' own
recaps (never a later one).
**Consequence.** A hook can only close on a value the model was actually offered, so a closed hook is always
traceable to an open one. Live free-chain bench: E1 put `pays_off` on the hook scene itself in every run
(T2-P5-F7) — fixed by restricting the field to body scenes only (see DEC-188).

## DEC-181 — Audience feedback is pasted, capped and never trimmed; one item per episode; the latest decided
direction steers only the next E1
**Context.** A paste of unbounded length would blow past F1's input budget silently, or get silently cut and digest
the wrong half of the comments.
**Decision.** `POST /episodes/{ep}/feedback` stores `{text, stats?}`, each capped at 6,000 characters (code points)
at the API, refused whole over the cap, never trimmed. One item per episode — a second paste replaces the first. F1
digests it (≤ 60 words) into exactly three directions (≤ 25 words each); approving `feedback:<ep>` with
`{direction: 0|1|2|null}` records `chosen_direction`. Only the latest *decided* feedback item's direction steers the
next episode's E1 (and N1) — never the story overall, never more than one episode ahead.
**Consequence.** The pasted text is fenced in the prompt as untrusted data, not instructions. Re-running F1 clears
any direction already chosen, since it would point at superseded text.

## DEC-182 — N1 proposals: fixed ids, recurring/guest by default, decisions are final; accepting a twist rewrites
the arc entry with the old text kept in `history`; approving the proposals document writes nothing new
**Context.** Each proposal needs a stable handle for the dashboard's accept/reject UI, and a twist accepted for a
future episode has to land somewhere the season arc already reads from.
**Decision.** `episodes/ep{N+1}/proposals.json` holds up to two characters (`char_1`, `char_2`) and up to two twists
(`twist_1`, `twist_2`), each targeting an arc episode after N. A character's role defaults to `recurring` or `guest`
so the story stays `ready`; `lead`/`support` is allowed but folds the cast approval (DEC-123, unchanged) and the
dashboard shows that warning before it's sent. Accepting a character queues the existing cast path (K1, sheets,
voice) with `introduced_in = N+1`. Accepting a twist replaces the target arc entry's summary and `open_hooks_out`,
pushing the old text into that entry's `history`; the acceptance *is* the approval, so `approvals.season` is not
cleared. Every decision (`accepted`/`rejected`) is final. `approve proposals:<ep>` is only reachable once every item
is decided, and it writes nothing — it completes the propose-next job that is already `awaiting_approval`
(`stories.py`'s `_complete_awaiting`), which is what the dashboard's "Approved" reads (stage 13b F5).
**Consequence.** A re-run of propose-next replaces the whole document and its decisions start over, but anything an
earlier acceptance already did (a character queued, an arc entry amended) stays done — re-running is not a
rollback.

## DEC-183 — Shot timing moves to whole frames at the source (amends DEC-142); a storyboard written before this
converts once, on its next full re-time
**Context.** Stage 6's key finding: cumulative frame rounding over a script's scene durations meant a single line
edit could flip the frame count — and so the render cache key — of roughly a third of the shots after it (measured
22.6% live), which made "rebuild only the changed shots" impossible. DEC-142 named `episode_pass` the single timing
source but never required whole frames.
**Decision.** `timing.episode_pass` (and `scene_timing`/`allocate_shots` beneath it) now default to
`whole_frames=True`: every scene and shot duration is the nearest frame at 30 fps, window states
(tightened/under/over) decided on frame counts rather than the rounded seconds figure. A new storyboard carries
`whole_frames: true`; an old one converts on its first full re-time (every scene re-timed, not skipped), which
re-renders most of its shots once. Golden and every stored document already rendered continue to render
byte-identical, because the timeline and `audio_entries` re-run `episode_pass` on every render and compare against
the board's own flag.
**Consequence.** Later-shot frame drift from an edit measured 22.6% before the fix, 0% after (spike, 110-episode
sample). One latent old-timing bug survives, unfixed and now unreachable by a whole-frame board: an unflagged board
whose total lands on an exact half frame can be refused as unplannable at plan time (~1 in 32 random episodes on the
old code). Both live stories converted once on stage 13's walk (FR 7/21 shots, EN 2/20).

## DEC-184 — The partial re-render's baseline is the last good manifest; a clip is reused only when its recorded
sha256 matches; partial and full renders must agree exactly
**Context.** The runner already reused a cached clip by key, but only checked that the cached file existed and was
non-empty — never that its bytes were the ones actually rendered for that key. The manifest was overwritten on
every run, including a failed one, so nothing recorded what had been reused relative to a *good* render, and a
failed re-render could replace the manifest while the previous `episode_final.mp4` stayed on disk untouched.
**Decision.** `render_manifest.last_good.json` is written only by a render that completes, and only after the final
file is read back — never by a failed or cancelled one. `render/partial.cache_state` is the one hit predicate
shared by the runner and the dry run: a cached clip is reused only when its sha256 is one the last good render (or
the current run) actually recorded for that exact key. `render/partial.select` returns `{rebuild, reuse, reasons}`
with one reason per shot (image, overlay, modifiers, frames, motion, settings, new, missing, corrupt). A real-ffmpeg
CI test proves partial and full renders of the same edited documents produce an identical framemd5.
**Consequence.** A stale reuse — the one failure mode that would ship an old frame silently — is structurally
impossible: reuse requires a recorded, hash-verified match, not merely a file being present. This was named the
riskiest stage of the phase-5 plan; proven by `tests/test_aistory_render_partial.py` (55 tests) and the golden
test, both unedited elsewhere and green.

## DEC-185 — Re-edit rules (amends DEC-129): a text-only line edit keeps the storyboard approval and re-times in
place; a framing swap outdates the image and a stale image is never rendered; a rule violation is refused; a voice
regenerate persists its take and its note
**Context.** Before phase 5, any script edit — even fixing a typo — cleared both the script and storyboard
approvals and forced a full storyboard re-plan, which dropped every shot's locks, notes and images (DEC-129 as
originally written). Separately, `render.py` never checked a shot's own `shot_state`, so a re-approved storyboard
could render an image that had gone stale underneath it.
**Decision.**
- A **text-only edit** — a line's words or delivery, or a scene's `pays_off`, with the same line ids, speakers and
  emotions — clears only the script approval and stales E4; the **storyboard approval and every shot are kept**,
  the scene is marked `retime_only` and re-timed in place once its lines are re-voiced. Speaker or emotion changes,
  or added/removed lines, stay **structural**: exactly today's behaviour (both approvals cleared, a full re-plan).
- **Re-voicing** one line (`line:<ep>:<lid>`) now applies its note to the TTS request instead of ignoring it, and
  persists the resulting take (audio sha256, the note) on the line entry, closing a phase-4 loss.
- A **framing, action or prompt edit** marks the shot's image stale (`shot_state`); `render.require_renderable` and
  `current_render` now refuse a render with a stale or failed unlocked shot image, naming exactly which shot to
  regenerate. A framing edit that would break a cross-scene rule (no back-to-back repeated framing, etc.) is refused
  outright rather than silently moving a neighbouring shot.
- A **motion swap** or **transition change** keeps the image and only changes that shot's own render key (DEC-141
  kept); a transition edit can still shift whole frames of the scene it leaves, but never another scene's shots.
- Live fix T2-P5-F9 (`10acc3d`): Gemini's free TTS spoke a French re-voice note aloud instead of treating it as
  style direction (Google's "Say `<note>`: `<line>`" example form is read as text on `flash-lite`). Gemini now
  sends the line alone, like Edge and the local engines; the note is recorded with the take, never sent to any TTS
  provider.
**Consequence.** One line edit's cost drops from "E4 → script approval → a full re-plan (every image lost) →
storyboard approval → assets → assets approval" to "E4 → script approval → re-voice", for the common case. Proven
by `tests/test_story_reedit.py` (36 tests) and the phase-3 re-time tests passing unedited once their inputs were
changed to stay structural (named in the log).

## DEC-186 — The per-style fonts ship (Bangers, Bebas Neue, Patrick Hand under OFL-1.1; Luckiest Guy, Chewy under
Apache-2.0 — the human chose to ship them knowing that), with one licence file each, sizes and git blob SHAs
recorded in `fonts_index.json`; a template change bumps its `version` and names the re-pinned test; a style lock
never changes when its template does
**Context.** Only Montserrat Black had ever shipped; three of the five new styles specify `two_line`, which neither
MVP style renders, and `pan_pct` had been a hardcoded constant (4) regardless of what a template asked for.
**Decision.** The five named faces (confirmed by the human, "All 5 as named", 2026-09-30) are committed to
`assets/fonts/`, each verified against its approved byte size and its git blob sha from `google/fonts` `main`, with
its own licence file (`<Name>-OFL.txt` or, for the two Apache-2.0 faces, `<Name>-LICENSE.txt`) and an entry in
`fonts_index.json` (licence, sha256, licence file, source URL, git blob sha). `render.fonts.resolve_font` looks in
`assets/fonts/` before `custom_fonts/`, then the Montserrat fallback. `render.plan`/`filtergraph`/`motion` now read
the style lock's own `motion_rules.tier1.pan_pct` instead of the hardcoded constant; the two MVP styles' value
already equals it, so their render argv is unchanged (asserted). `tests/test_style_lock.py` proves a style lock
never changes when its template does (a deliberate mutation, `lock = template`, fails 12 tests).
**Consequence.** All six typefaces carry French glyph coverage (checked with `fc-query` against the accented
character set the language needs); the two_line speaker accents of the three new styles were measured against the
WCAG contrast rule (DEC-169) and three of them fail it, so the renderer substitutes its own readable grey rather
than the template's colour — recorded as a stage-14 A-entry, no template edited.

## DEC-187 — Live fix T2-P5-F3: N1 gets its own measured input budget and a bounded open-hooks list
**Context.** "Propose next episode" on the live FR story failed before any LLM call — "prompt is 1536 estimated
tokens, over the 1200-token budget" — because N1 had shipped on the default budget measured from a small fixture,
and its open-hooks list was unbounded (a season's fold can hold dozens of entries).
**Decision.** `INPUT_BUDGET['N1']` is remeasured at 3,740 tokens from the live-sized worst case (DEC-138's method:
chars/4, +15%, rounded up to ten) — an 8-character cast, a 12-episode arc, a full recap and direction, and at most
4 open hooks. `_n1_memory_block` now shows only the oldest `PAYOFF_HOOKS_MAX` (4) open hooks — the same window E1
offers — instead of the whole stored list.
**Consequence.** Fix `64ed69e`. The live story's proposals ran on the next attempt. `tests/test_story_prompts_series.py`
gained a worst-case-input-fits-its-budget test and a hooks-window test (10 hooks shown as 4; 30 hooks equal to 4 in
token count).

## DEC-188 — Live fix T2-P5-F7: only a body scene pays off a hook; E1's reply is repaired before validation
**Context.** Live ep-2 E1 and a 4-of-4 free-chain bench both put `pays_off` on the hook scene itself (s01) as well
as a body scene; E4 then refused the payoff on the framing scene (part of T2-P5-F6).
**Decision.** `prompts._E1_PAYOFF_LINE` now says explicitly: body scenes (setup, rising, peak, turn) only, always
`[]` on the recap, hook and cliffhanger. `steps/script._repair_e1_reply` empties a framing scene's `pays_off` before
validation, defensively, the same pattern already used for stray `props` (T2-F9) — no retry spent on the free tier
for something Python can fix in place. Episode 1 is unaffected (no payoff line is ever sent to it).
**Consequence.** Fix `7b9cc92`. `tests/test_story_episode_steps.py` gained a test for the repair; the ep-2 E1
goldens and the measured input budgets moved by the longer ask line (named re-pins in the log).

## DEC-189 — The dashboard never offers "Sign out" without a stored token, and signing out re-checks the server
**Context.** Found in the stage-10 browser check: the sidebar's "🔒 Sign out" control had rendered on every server
since an earlier commit and, clicked, set the app to signed-out, which renders the Login screen. On the live open
app at desktop widths (the sidebar is desktop-only) one click showed the sign-in screen the human had explicitly
ruled out (memory: no auth on the app, ever).
**Decision.** "Sign out" renders only while a token is actually stored client-side; signing out re-checks the
server rather than trusting the client's own state, so an open server always answers "in".
**Consequence.** Complements DEC-173 (auth stays opt-in) and is itself part of RC-M9, the no-auth regression item.
`tests/test_dashboard_no_sign_in.py` added.

## DEC-190 — The cast step paces itself against free-tier rate limits, the same way the assets step does (amends
DEC-168)
**Context.** T2-P5-F4: an accepted proposed character needed six separate cast presses, roughly a minute apart, to
finish — `steps/cast.py` had no pacing at all, so a Pollinations 402 or a Gemini 429 failed the step outright
("Cast incomplete…") instead of being retried. The predicates DEC-168 built for the assets step (`is_rate_limit`,
`_paid_sent`, `rate_limited_by`, `RATE_LIMIT_PAUSE_S`) lived only in `assets.py`.
**Decision.** The predicates move, byte-for-byte, to a new `steps/pacing.py`; `assets.py` re-imports them under its
own names, so nothing that already patches `assets.RATE_LIMIT_PAUSE_S` etc. changes. `cast.run` paces every
held-back sheet and voice sample in DEC-168-shaped rounds: one cancel-aware 60-second pause per round, checked
against the step's own 1800-second budget before each pause and each call, a provider making no progress across a
whole round given up on for that run, a paid-sent item never retried, and a `NeedsEditor` stop never paced (it
never will succeed on its own).
**Consequence.** Proven live 2026-09-30 on the stage-14 anime cast: a character's portrait, turnaround and
expressions sheet all completed in one press, through one paced round ("⏳ pollinations rate-limited: waiting 60 s
before retrying 1 image"). The places step does not yet pace the same way — a known gap, not fixed this phase (see
`docs/AI_STORY.md`).

## DEC-191 — Dashboard series polish (stage 13b): the Season step stays open after a series action, the feedback
counter counts code points, and the episode page shows the server's own refusal sentence for a blocked regenerate
**Context.** Stage 13's browser check and the human's own use surfaced four small dashboard gaps: (F1) any series
action (approving memory, digesting feedback, deciding a proposal) collapsed the wizard back to the Cast step; (F2)
the pasted-feedback counter counted UTF-16 code units (295) against the server's 6,000-code-point cap (294 for the
same text), so it could read under the cap while the server was already at it; (F5) "Approve proposals" stayed a
clickable button after the approval, which writes nothing (DEC-182), so nothing visibly changed; (F8) "Re-voice
this line", a shot-image regenerate and a metadata regenerate all stayed enabled while the server would refuse them
with a 409, wasting a press to learn why.
**Decision.** The Season step's wizard state now only refreshes on a series action instead of navigating away. The
feedback counter uses `[...s].length` (code points), matching the server exactly. The episode and story pages carry
`proposals_approved` (true once the latest propose-next job for that episode is `completed`) and
`assets_regenerate_blocked` / `metadata_regenerate_blocked` (the server's own refusal sentence, `None` when the
action is allowed); the dashboard disables each control with that sentence shown as visible text, not a
hover-only `title`.
**Consequence.** All four verified in a 375 px browser check on a scratch copy of the live story. F6 (E4 sometimes
still judging a present payoff as missing on `flash-lite`) is recorded as A-084 rather than fixed — no code change
is justified by one model's occasional variance.

## DEC-192 — Testing scope: one fail-first test per fix plus the working-path guard; live checks walk the main
path only (amends the agreement's Section 9 practice, alongside DEC-176)
**Context.** The human, 2026-09-30, mid-close: "Some test will happen via the usage of the app, do not test every
possible outcome, only the essential one."
**Decision.** From this point in the phase: each fix gets one fail-first test proving the bug, plus a guard that
the already-working path still passes — not an exhaustive combinatorial suite. Live browser and CLI walks exercise
the main path only (the plan's own acceptance walk), not every edge case a step could hit. Tier-1 (full suites,
both environments, every stage) and the fail-first requirement for new behaviour are unchanged; this narrows only
how much *new* coverage a single fix earns.
**Consequence.** Stage 14's five per-style episodes keep the plan's own per-style acceptance (a manifest, a contact
sheet) rather than a full regression pass per style; 14b(e) runs exactly its stated path. Recorded as a durable
override so a later session does not over-test by default.

## DEC-193 — The free-tier daily counter gives a slot back when the provider refused the credentials; stage 14's
live fixes to the free links
**Context.** Stage 14 ran the first Cloudflare Workers AI calls ever (the human added keys mid-stage). A mistyped
token made every call answer 401 while `limits.acquire` took a slot before each call, so the app declared the day's
170 calls spent although Cloudflare served none (T2-P5-F12); with the right token every call then answered 400
because the adapter sent a `seed` the model's schema refuses (T2-P5-F13). The same stage found the K1 own-name check
matching substrings ("Rin" in "earrings", T2-P5-F10) and an Edge voice Microsoft retired (`en-US-DavisNeural`,
T2-P5-F11).
**Decision.** A free link's final 401 or 403 releases the slot it took (`DailyUsage.release`, `limits.release`,
`FreeTierLimiter.release`, called by the chain runner); every other failure still counts, since the provider may
have spent the allowance; paid links stay uncounted. Cloudflare's request carries only `prompt` and `steps`, and
its result says `seed_honoured: False` (the Gemini/OpenAI convention). K1 matches the name as a whole word or phrase,
case-insensitive and Unicode-aware. The voice catalogue swaps DavisNeural for AndrewNeural.
**Consequence.** Commits `279b572`, `d08c92d`, `d2ba496`, `3cfce9a`, each with one fail-first test (DEC-192).
Today's Cloudflare counter was corrected twice by hand to the images actually served (0), with backups
(`/home/ubuntu/backups/ai-story-phase-5/usage.before-cloudflare-reset*.json`). A 400 still counts; the catalogue has
no live check (A-093).

## DEC-194 — A capped paid test on a story that already spent: the daily cap is the hard limit
**Context.** Stage 14b(e)'s go was "hard cap $0.10" on T2 `ab8fc500173e`, which already held $0.671 ($0.60 in episode
1). The caps are cumulative: `budget.check` adds the episode's and the story's earlier spend, so caps of
0.10/0.10/0.10 refused the $0.03 regenerate before any submit ("would bring this episode to $0.63 of its $0.10
cap"). The human, asked, answered "Decide for me".
**Decision.** For such a test the **daily** cap carries the hard limit (0.10, on a day with $0.00 paid so far) and the
episode and story caps are set to what they already hold plus the same headroom (0.70 and 0.78); `allow_paid` is on
only for the one paid call, inside a shell `trap` that restores off and 1/3/10 whatever happens, with `LLM_CHAIN`
pinned without OpenRouter and every LLM call made while `allow_paid` is still off.
**Consequence.** 14b(e) spent exactly $0.030 (one fal seedream-4-edit, journaled at submit), `allow_paid` was on for
31 s, and the re-render's manifest matched its dry run (2 of 20). A-094 records the numbers.

## DEC-195 — A redirect carries the credential headers only while it stays on their origin
**Context.** `transport.urllib_transport` opened every request through the stdlib's default opener. On Python
3.12.3, `HTTPRedirectHandler.redirect_request` copies every header except `Content-Length`/`Content-Type` onto the
redirected request, whatever its host. A 30x from a provider to a CDN or a signed-URL host would therefore have
carried fal's `Authorization: Key …`, Gemini's `x-goog-api-key`, or the Cloudflare or pollinations bearer token
there. No adapter can see that redirect:
- fal's `status_url`/`response_url` come from fal's answer;
- phase 6's Veo `_download` checks the host once, before it sends.

The human asked for the fix on 2026-09-30.
**Decision.**
- `urllib_transport` opens through a module-level `_OPENER`, built with `_CredentialSafeRedirectHandler`. After the
  stdlib's `redirect_request`, the handler removes every header named in `CREDENTIAL_HEADERS` when the hop's
  (scheme, host, port) differs from the previous hop's. The names are `authorization`, `proxy-authorization`,
  `x-goog-api-key` and `x-api-key`, matched case-insensitively.
  - The default port is filled in.
  - A malformed port counts as another origin.
  - A same-origin hop keeps the credentials. Once dropped, they never come back later in the chain.
  - A proxy's own `Proxy-Authorization` is re-added by `ProxyHandler` on each hop.
- Signature, `Response` and the whole exception mapping are unchanged, and no exception class is added (DEC-012).
- Rejected:
  - `add_unredirected_header` drops the credentials on same-origin redirects too, and no code shows that no
    provider relies on those.
  - `install_opener` is a process-wide side effect on every `urlopen` user.
  - Per-adapter host checks cannot see a redirect inside `urlopen`.

**Consequence.**
- `tests/test_transport_redirects.py` runs two local servers.
  - The cross-origin test failed first: server B received all four credentials. It now proves B gets none of them,
    while `X-Trace` still arrives.
  - The same-origin guard passed both before and after the fix. An "always drop" mutation fails it.
- `test_provider_http.py`'s error-mapping test now patches `transport._OPENER.open`. Its fakes and four assertions
  are unchanged (A-098).
- Not covered, same class, follow-ups: `stt.py` `_post_multipart` (Groq/Mistral bearer) and `studio/broll.py` (the
  Pexels key) call `urlopen` directly.
- A-097 records the provider-side assumption.

## DEC-196 — stt's upload and the Pexels search open through the transport's credential-safe opener (extends DEC-195)
**Context.**
- DEC-195 gave `transport.urllib_transport` an opener that drops the credential headers when a redirect leaves
  their origin. It named two callers that bypass the transport as follow-ups:
  - `stt._post_multipart`: Groq and Mistral, `Authorization: Bearer`.
  - `studio/broll.py`'s Pexels search: the key is the `Authorization` value.
- Both handed their own `Request` to `urllib.request.urlopen`, whose default redirect handler copies the header to
  any host. A 302 turns stt's POST into a GET, and the bearer key still goes with it.
- The human asked for the fix on 2026-09-30 and approved the plan in chat
  (`.claude/plans/stt-broll-redirect-credentials.md`).

**Decision.**
- Both calls are now `transport._OPENER.open(request, ...)`, with no shared helper: `_OPENER.open` is already the
  seam `test_provider_http.py` patches.
  - stt keeps `timeout=REQUEST_TIMEOUT`.
  - broll keeps no explicit timeout (the socket default, as before).
- Return values, the `HTTPError` → `SttError` mapping, and broll's `except Exception` → `False` branches are
  unchanged: `_OPENER` is `build_opener` with only the redirect handler replaced, so it raises the same exceptions.
- broll gets `PEXELS_VIDEO_SEARCH_URL`, the same URL as a module constant. It is the only seam a local test can
  point at.
- broll's CDN download stays on `urlopen`: it sends only `User-Agent`.
- `broll.py` imports `from ..providers import transport`. It is the studio package's first import from
  `clipping.providers`, which imports nothing from studio, so there is no cycle.
- Rejected:
  - Routing through `urllib_transport`: it returns a `Response` for an `HTTPError` and maps connection errors, so
    both functions' error handling would change.
  - A public forwarding helper: one more name for two callers.
  - Wrapping `_OPENER.open` in the test to rewrite the URL: the fail-first run would have hit api.pexels.com.

**Consequence.**
- `tests/test_stt_broll_redirects.py` runs two local servers per test; A answers 302 to B.
  - stt: before the fix, B received `Authorization: Bearer test-groq`. Now B gets one keyless `GET /moved`, and the
    result is B's JSON.
  - broll: loaded through `conftest.render_stack_stubbed`, since cv2, mediapipe, numpy and yt_dlp are missing in
    both environments. Before the fix, B received `test-pexels`. Now B gets the search and the clip with no key,
    and `download_pexels_broll` returns `True`.
  - Both tests failed first in both environments, on the leak assertion only.
- Tier 2 was a keyless live probe through the new code:
  - Pexels answered 401, and the function returned `False` with no file.
  - Groq answered **403 "error code: 1010"**. The old `urlopen` gets the same answer: Cloudflare refuses urllib's
    default `Python-urllib/3.12` User-Agent from this host, before any auth. This is pre-existing and recorded as
    a follow-up, not fixed here.
- A-099 records the provider-side assumption.

## DEC-200 — Keyframe-first I2V
**Context.** Phase 6 turns a shot's existing image into a short clip at tier ≥ 2. Spec 8 requires a video model
never invent a character from text: the phase-4 shot image is the character's only proven look.
**Decision.** Every video adapter, hosted and local alike, takes the shot's own current image as its one keyframe
input; no `VIDEO_CHAIN` link runs text-to-video, and none is given a text-only fallback.
**Consequence.** A shot's video can only be made once its image is current, which is why the video phase runs last
in `assets.run()`, after images, voices, SFX and BGM (DEC-202). `clip_seconds()` refuses before any call a request
with no keyframe.

## DEC-201 — Native model audio is discarded at Tier 2
**Context.** Several hosted video models can speak dialogue in their own voice. That voice would not match the
character's pinned TTS voice from Cast, breaking the voice consistency every earlier phase built.
**Decision.** Native model audio is discarded at Tier 2 — dialogue comes from our TTS for voice consistency — and
kept only under the Tier-3 opt-in.
**Consequence.** Every Tier-2 clip is generated and rendered with `-an` (`tier2_clip_argv`); the shot's own TTS
line is placed exactly as it was before phase 6. Only a Tier-3 shot with `keep_native_audio` set adds the clip's
own audio as a further stem (DEC-210), still timed and subtitled against our own line text.

## DEC-202 — Video is the last phase of the assets step
**Context.** A separate "animate" step or fast-track sub-step was considered. It would add a step, a gate, an
estimate and a CLI path, and re-pin `SUB_STEPS` — for no parallelism gained, since the busy rule allows one job
per story, and an `animate` param already lets keyframes be reviewed before any clip is bought.
**Decision.** Video generation is the last phase inside `assets.run()`, after `write_assets_doc`, gated by an
`animate` assets param (default on, mirrored in `AssetsStepParams`; the dashboard's `StoryboardPane` sends
`animate: true`).
**Consequence.** Turning `animate` off runs images, voices, SFX and BGM only — nothing video-related is attempted
or spent. See "animate off first" in `docs/AI_STORY.md`.

## DEC-203 — The planner's selection is derived, never stored
**Context.** Storing the planner's shot selection was considered and rejected: it would go stale the moment a
price, the day's spend, or a cap moved, letting the shown estimate and the actual run disagree.
**Decision.** `plan_animation`'s pick of which shots to animate is recomputed every time from the current cap,
committed spend and what already exists — never written to a document. A shot is pinned in or out of the plan
only through `patch_assets` (`animate` / `keep_still` per shot). The `free` budget profile animates only shots a
ready local route can make, since that is the only way to animate at $0.
**Consequence.** The estimate and the run always agree on what will be made (RC-V6); an episode's cap or spend can
move without ever leaving a stale plan on disk.

## DEC-204 — Sticky image and video links per episode (amends DEC-168's fall-through)
**Context.** Phase 5 found that mixing two image providers inside one episode breaks the look (A-087): a
character drawn flat-cartoon by one link and photoreal by another no longer reads as the same character. The
human's answer for phase 6: one image provider and one video provider per episode, sticky, with stop-and-ask on a
switch — closing A-087 and extending the same rule to video.
**Decision.** `assets.json` gains an optional `links {image?, video?: {link, since, switched_from?}}`, written
once the first asset of that kind is served (a cache restore counts). While a link is recorded, the runner gets a
one-link chain: `FALLBACK_LINKS` retired-model swaps still apply, and a 402/429 waits inside DEC-168's paced
rounds rather than falling through to the next link. A link that is gone — no key, the day's allowance spent, an
`allow_paid`/cap refusal, a 401/403, or an unreachable local server — stops before any call with a DEC-117-shaped
offer: the link, why, the next runnable link, the shots to redo, and the estimate. Switching is only through
`patch_assets {"links": {"image"|"video": link}}`, which stales exactly the assets made on the old link and
leaves the storyboard approval untouched. A legacy episode with no record, mixed under the old fall-through rule,
prints one note and behaves as it did before.
**Consequence.** RC-V5: no episode can silently mix two image or two video providers again. A rate-limited link in
force still paces instead of hopping to a different provider mid-episode.

## DEC-205 — `GEMINI_PAID_API_KEY`
**Context.** Gemini's existing credential (`GOOGLE_API_KEY`) serves the free image and TTS chains. Reusing it for
Veo would bill the free chain's own project the moment a paid video call ran. Veo needs a key from a separate,
billing-enabled project.
**Decision.** `generation.LINK_ENV_KEYS` adds `"gemini/veo-3.1-lite": ("GEMINI_PAID_API_KEY",)`, read through a new
`env_keys_for(link)` and used by `missing_keys`, `credentials_for` and `gating.link_summary`. The key is added to
`PERSISTED_KEYS` and `SECRET_KEYS`, saved and masked exactly like `FAL_KEY`, and reported as
`gemini_paid_api_key_set`.
**Consequence.** RC-V4: Veo never reads `GOOGLE_API_KEY`, and no other Gemini link ever reads
`GEMINI_PAID_API_KEY`. Nano-banana stays on `GOOGLE_API_KEY` for now — moving it to its own paid key is a
follow-up, safe while that project's billing stays off.

## DEC-206 — Paid LLM metered and booked (amends DEC-115)
**Context.** DEC-115's follow-up: paid LLM calls were neither estimated, capped nor booked, so OpenRouter — the
one paid LLM link — could never be funded without bypassing every other paid link's discipline.
**Decision.** New `steps/llm_spend.py` meters every call through `run_chain`'s existing `client_factory` seam, so
`llm.py` needs no edit (`git diff 25abdd1 -- clipping/providers/llm.py` stays empty, keeping RC-S4). With
`allow_paid` on and a keyed paid link in the chain: each paid link is estimated (tokens of system + user + the
reply cap, times `pricing.LLM_PRICES`) and checked against the assets step's `LineGates` for the episode, the day
and the story; a refused or unpriced link is dropped with a printed line, and free links still run. Each paid
reply is booked once: unit `token`, cost from `usage.cost` where the provider gives it, else usage × price, else
the estimate with a DEC-153 note.
**Consequence.** OpenRouter can now be funded and booked like any other paid link, the same budget discipline as
images and voices. `pricing.LLM_PRICES` holds the dearest OpenRouter host's verified prices for `DEFAULT_LLM_CHAIN`
(mistral-small-3.2 $0.10 / $0.30 per M tokens; llama-3.3-70b, the DEC-089 fallback, $1.04 / $1.04). Tested only
this phase — OpenRouter itself stays unfunded, the human's standing choice.

## DEC-207 — The video cache key
**Context.** The generation cache (`gencache`) had no `"video"` kind, so a clip request had no key shape and could
never be served from cache or protected against a double-buy.
**Decision.** `"video"` joins `CACHED_KINDS` and `SEEDED_KINDS`. Only when `kind == "video"` the cache payload adds
`clip_s`, `fps` and `native_audio` (whole floats become ints); the keyframe's sha comes through the existing
`refs`; `KEY_VERSION` stays 1. A request with no `duration_s` or no seed gets no key at all, so it is never
journaled.
**Consequence.** A same-input video request (same keyframe, duration, fps, native_audio and seed) is served from
cache at $0, exactly like an image. The stage-3/8 adapters refuse an un-keyable request before it is ever sent.

## DEC-208 — The clip-length table; trim or hold at render
**Context.** Every hosted and local link offers only a handful of fixed clip lengths, never a shot's exact
`duration_s`.
**Decision.** `CLIP_LENGTHS` (one source in `clipping/providers/video.py`, re-exported by `video_plan`) lists each
link's supported seconds: seedance 2–12 s continuous, `ltx-2.3-fast` 6/8/10 s, kling 5/10 s, veo 4/6/8 s, and each
local template's own frame rule. `requested_seconds` picks the smallest supported length that is at least the
shot's `duration_s`, or the longest offered length if none reaches it. The render then trims a clip that ran long,
or holds its last frame (`tpad stop_mode=clone` before `trim`) when it ran short.
**Consequence.** A shot is never asked for a length no link offers, and a render is never left short of frames —
the pre-fix bug this closes: a 1.0 s clip on a 1.5 s shot made only 30 of 45 frames and the render still reported
"completed".

## DEC-209 — `fill_failed_with_motion` is a render param, default off
**Context.** Without an escape hatch, a single failed, stale or missing clip would block the whole episode's
render.
**Decision.** A render param `fill_failed_with_motion` (default off, recorded in the manifest only when true) lets
such a shot render with Tier-1 motion instead of refusing. Without it, the render refuses and names the shots,
each with its regenerate target or, for a request still open, a Continue offer.
**Consequence.** RC-V7: a failed or stale clip never silently renders. Ticking the box (dashboard) or passing
`--fill-failed-with-motion` (CLI) is a deliberate choice to accept the substitute, and the manifest's
`shot_modes` records which shots were filled (`motion_fill`) versus actually animated.

## DEC-210 — Tier-3 audio stem (amends DEC-158)
**Context.** DEC-158 kept Tier-1's audio as one absolute timeline and deferred per-shot audio mixing
(`acrossfade`-style) to phase 6. Tier 3 needed a way to keep a model's own clip audio without breaking that
timeline or the existing ducking.
**Decision.** At tier 3, for a shot with `keep_native_audio` set whose current clip's mp4 actually carries a
sound track (`clips.clip_has_audio` reads the mp4 boxes directly — no subprocess), the clip's own audio becomes
one more stem in the A-stage mix: `amovie`, `atrim` to the shot's own samples, 10 ms edge fades, `adelay` to the
shot's start. That shot's own TTS line is left out of the mix, though subtitles still come from our line text and
TTS timing; ducking is unchanged; the video stage itself keeps `-an`. A clip with no sound track renders as
Tier 2, with one printed note.
**Consequence.** Tier 3 is proven by tests only this phase, never run live (budget; see A-105). A Tier-2 clip's
audio stays discarded by a guard test. A shot on a link that carries no audio at all (seedance — A-108) always
falls back to the Tier-2 path, silently correct but never native.

## DEC-211 — `gen_timings.json`
**Context.** An ETA for local generation needs a measured history, but `data/usage.json` resets daily
(`limits.py:95-101`) and cannot hold one.
**Decision.** New `providers/gen_timings.py` and `data/gen_timings.json` (schema `gen_timings_v1`) hold the last
20 `{wall_s, clip_s}` rows per link, template and profile. The shown ETA is the median rate times the planned
seconds; with no history yet it is null, shown as "no measured history".
**Consequence.** Local ComfyUI's ETA on the dashboard improves as more clips are made on a given template and
profile, and nothing is lost across a day boundary the way a `usage.json`-based history would be.

## DEC-212 — The video "Test chain" never generates (amends DEC-103)
**Context.** DEC-103 lets Settings' "Test chain" spend once on a pressed paid link, to prove it reachable. For
video, that would buy a whole clip just to check a key.
**Decision.** Pressing Test chain on `VIDEO_CHAIN` never generates. A local link is checked with `/system_stats`
and `/object_info` only. A hosted link shows its key status and a priced estimate for a default clip length, and
is never called even when pressed.
**Consequence.** RC-V8. The stage-4 estimates recorded at test time: seedance $0.11, `ltx-2.3-fast` $0.36, kling
$0.21, veo $0.30 — each the price of the clip length nearest 5 s that the link actually offers.

## DEC-213 — CLI `--tier`/`--route` patch the story
**Context.** A run-level `--tier` override on the CLI was considered and rejected: it would let the dashboard and
the estimate disagree with what actually ran, and there is no per-episode tier field, only a per-story one.
**Decision.** `step ID assets --ep N --tier N --route R` patches the story's own `generation_profile` (not a
run-only override) and prints the resulting profile before the step runs.
**Consequence.** The CLI, the dashboard and the API always agree on one story-level tier and route; a change made
from the CLI is visible to the next dashboard session too, not only to the CLI process that set it.

## DEC-214 — `PRICES_AS_OF`
**Context.** Stage 2 re-read all four video model prices on their own provider pages on 2026-09-30. None had
moved against the figures already in `pricing.py`, but the re-read itself needed recording somewhere.
**Decision.** `PRICES_AS_OF` keeps its existing date — it stamps the whole price table, not one row. The four
video price rows instead carry their own per-row note of the 2026-09-30 re-read, backed by A-100 through A-103.
**Consequence.** A future full price-table refresh still only needs to bump the one shared date; a reader
checking just the video rows has their own re-read date sitting beside them instead.

## DEC-215 — Paid walks use per-process caps
**Context.** Settings' `allow_paid` must stay off in the live app (standing rule), yet the Tier-2 live walk still
needed to spend real money on specific, bounded shots.
**Decision.** Every paid run of the walk is a single CLI process inside the container, given `ALLOW_PAID` and its
own episode/day/story caps as that process's own environment only (DEC-114's CLI environment isolation) — nothing
persisted to Settings. This reuses DEC-194's capped-test pattern for phase 6.
**Consequence.** Settings' `allow_paid` measured false throughout the whole walk. Each paid shot needed a shown
estimate and the human's explicit go before its process ran. The walk's total ceiling was $0.55 ($0.30 fal,
including the A-071 probe, plus $0.25 reserved for Veo); actual spend $0.32, after the Veo shot was replaced by
Kling (DEC-218).

## DEC-216 — T2-P6-F1: a Tier ≥ 2 clip that is not 9:16 is covered and centre-cropped
**Context.** Walk step 9 found story B's Kling clip playing as a square boxed in black bars: Kling
(`fal/kling-2.5-turbo-std`) keeps the **input keyframe's own aspect ratio** rather than a requested one (A-102,
measured live) — a square 1024×1024 Cloudflare keyframe gave a 960×960 clip — while `tier2_clip_argv` padded such
a clip down and letterboxed it, the opposite of the still path's own cover-crop rule for a non-9:16 image.
**Decision.** `tier2_clip_argv` now covers and centre-crops every Tier ≥ 2 clip with the renderer's own cover rule
(`filtergraph._cover_fill`, the same string shared with the still path's `cover_argv`), plus `setsar=1` to
normalize the sample aspect ratio. A clip is never letterboxed or padded again. Fixed in commit `ea19ab2`.
**Consequence.** Measured before → after: a 960×960 and a 96×96 clip had 420 black rows top and bottom → 0; a
120×208 clip 24 black rows → 0; the 704×1248 seedance clip had 2+4 black rows and SAR 4213:4212 → 3 columns
cropped, SAR 1:1; a 720×1280 clip was frame-identical either way. The tier-2 golden was re-recorded (host and
image keys; the x86_64 key followed from CI). The source fix — sending the model a 9:16 crop of the keyframe in
the first place, so its own output is already vertical — is a follow-up, keyed on the source sha plus a crop-rule
token.

## DEC-217 — Stage 13b: a still more than 2% off 9:16 is centre-cropped before the scale, never stretched
**Context.** The still path (`shot_argv`: `scale=<W×upscale>:-2` then `zoompan … s=1080x1920`) had been
**stretching** any non-9:16 still vertically since phase 5 — a pre-existing bug, not a phase-6 regression. A
centred 32×32 square came out 364×648. (A-095 first misread this as an existing crop; corrected once measured.)
Asked mid-walk, the human chose "fix now, in phase 6."
**Decision.** A still more than 2% (relative) off the 9:16 ratio is centre-cropped to an exact 9:16 before the
scale — `crop=<9k>:<16k>` at the largest even multiple of 9×16 that fits the source, which keeps SAR 1:1 (e.g.
1024×1024 → 576×1024, measured) — never stretched. A 9:16 still, a near-9:16 still inside the 2% tolerance, and a
still whose size cannot be read all keep their argv byte for byte (RC-M2, RC-M3). Sizes are read by a new
stdlib-only header reader, `clipping/aistory/render/imagesize.py` (PNG/JPEG/WebP), with no PIL (A-096).
`partial.shot_facts` now counts only a crop applied **after** `zoompan` as the handheld modifier, so this still's
own cover crop (and a clip's cover crop, DEC-216) reads framing reason `settings`, not `modifiers`. Fixed in
commit `3841a1d`.
**Consequence.**
- A rounded-crop alternative (crop to the nearest even dimensions without landing exactly on 9:16) was rejected:
  it leaves SAR 26281:26280, which breaks the final pass's concat (measured: exit code 234).
- 9:16, near-9:16 and unreadable sizes keep today's argv byte for byte (RC-M2, RC-M3).
- The live $0 re-renders (walk step 5): `560e901c1b3d` 3/20 rebuilt, `979c8376e43e` 14/20 (13 stills plus sh01's
  clip, DEC-216), `04feb539840f` 18/18, `14ff154d3bff` 21/21; the fully-9:16 rendered episodes read current with
  0 shots to rebuild on the deployed code (RC-M3 live; not re-rendered).
- Follow-ups left open: a near-9:16 size inside the 2% tolerance whose upscale is not exact breaks the final
  pass's concat the same way (832×1472, 736×1312 — no live still has such a size yet); EXIF orientation is not
  read; the centre crop can lose an off-centre subject.

## DEC-218 — The walk's Veo shot was replaced by fal Kling 2.5 turbo std
**Context.** Walk step 9 (story B `04feb539840f`, FR) was planned as the one live Veo shot (4 s, about $0.20,
cap $0.25). The human's call: "Skip Veo, use fal.ai in its place."
**Decision.** The shot ran on `fal/kling-2.5-turbo-std` instead — 5 s, about $0.21, inside the same $0.25 cap, a
second fal adapter path beside story A's seedance shot. `ltx-2.3-fast` was considered and rejected: its 6 s
minimum would cost $0.36, over the $0.25 cap.
**Consequence.** Veo stays proven only by its recorded API-documentation replies, never by a live call; A-103
stays UNCONFIRMED. The T2-P6-F1 finding (DEC-216) came out of this substitution — Kling's aspect-keeping
behaviour, not a Veo behaviour. A live Veo shot remains a follow-up.

## DEC-219 — Quality before $0: every shot animated, no cheap image AI for the cast, places and props, billed APIs urged on weak hardware, richer prompts (the human's verdict after the phase-6 walk; amends the planning bundle's "free tiers by default" and the `one_dollar` "key shots animated" target)
**Context.** Walk step 12, 2026-10-01: the human watched story A `979c8376e43e` and story B `04feb539840f` ep 1 on
the phone. Their verdict:
- only one shot was animated, and they want the whole video animated, every shot;
- the story is not understandable;
- the pollinations images are "awfully ugly": do not use cheap AI to generate characters, decors and props;
- if the machine cannot run good image, text or video generation, the app should strongly recommend billed APIs;
- the prompts are too short and give too little context about proportions, traits and the specific image a shot
  needs, so they must be upgraded.

The walk animated one shot per story on purpose, under its $0.55 ceiling. So the first point is about the
product's target, not the walk.
**Decision.** This is a durable direction for the next phase (phase 7, which replaces reference-video import):
- At tier ≥ 2 a story aims to animate **every** shot; "key shots within $1" is no longer the target. The
  per-episode budget that makes this possible is a question for the next phase's CLARIFY.
- Characters, places (decors) and props are never generated on cheap or free image links (pollinations and the
  like). They use a quality image model. When the host cannot run a good model locally (this VPS has no GPU), the
  app strongly recommends billed APIs instead of falling through to free links without a word.
- Prompts carry enough context: proportions, traits, wardrobe, the place, the props, and the specific image each
  shot needs.
- An episode must be understandable on a first watch. The next phase diagnoses why it is not before choosing
  fixes.
- Unchanged: `allow_paid`, the caps, a shown estimate and the human's go still gate every paid call (DEC-194/215).
  No auth, ever.

**Consequence.**
- `.claude/plans/ai-story/15-phase-7-quality-overhaul.md` frames the next session (EXPLORE → CLARIFY → PLAN).
- Reference-video import (`08-phase-7-reference-import.md`) is taken off the schedule. Its file is kept.
- The verdict's four points are findings T2-P6-F2…F5 (action log, 2026-10-01). Phase 6's code is not changed by
  this decision.

## DEC-220 — Phase 7 stage 1 (W0): the clip prompt's action is resolved and stored; names are stripped from the action only; no run-on sheet or plate prompts
**Context.** The phase-7 audit (T2-P6-F6/F7, E1, E5) found three defects under every later improvement:
- The I2V clip prompt was built from the storyboard shot's raw `action`, with `@char_…`/`#place_…:variant`/`%prop_…`
  tags (`steps/clips.py:220` → `video_plan.build_video_prompt`). Only the image path resolved them, and never stored
  the result. Story A's paid seedance clip was prompted "@char_captain_obvious and @char_miss_overthink stand in
  #place_city_square:day …".
- `names.without_names` ran over the whole assembled image prompt (`shots.py:373`), so a place named "City square"
  turned its own descriptor into "A bustling urban the place featuring…" in all 20 of story A's shot prompts.
- The sheet and plate builders spliced a descriptor that ends with a period ("mouth., wearing", "bench., day"), and
  the plate ran `environment_rules` into the rendering text with no period.

**Decision.**
- `shots.resolve_shot` sweeps entity names from the resolved action only and returns it as `video_action`.
  `build_storyboard`, `refresh_prompts` and the storyboard edit path (`workflow.py`, an action or subject edit) store
  it as an optional storyboard-shot key. The image prompt's action sentence is the same text.
- `video_plan.build_video_prompt` uses `shot.get("video_action") or shot["action"]`.
- `portrait_prompt`, `turnaround_prompt`, `expressions_prompt`, `character_prompt_block` and `master_plate_prompt`
  strip the descriptor's trailing period; `environment_rules` gets a terminal period when it has none.
- The budget profile's dead `images`/`tts` keys (E5 D4) are not touched here: stage 2a makes `images` real.

**Consequence.**
- Stored storyboards have no `video_action`, so their clip prompts stay byte-identical: story A's sh01 hash
  `48955e43…` equals the recorded `prompt_hash`, and both live clips stay `current`. A new storyboard, a
  `refresh_prompts` or an action edit writes the field and stales only the shots whose prompt changed.
- Sheet and plate prompts are built at generation time with no stored hash, so no stored sheet or plate goes stale.
- A character descriptor that contains its own name is no longer stripped from the image prompt (K1 writes
  "appearance only", so this is unlikely; the user note and the prompt override are still stripped).
- Pins moved on purpose: the two fruit_drama plate goldens (the added period) and RC-V1's `TIER1_BOARD_SHA` (the new
  key only: the board with `video_action` removed from all 24 shots still hashes to `859441d7…4cf4`).
- Commit `9850fa5`.

## DEC-221 — Phase 7 stage 2a: image links per role, the "Quality (billed APIs)" profile, quality-only images on v2 stories (amends DEC-117 for v2 stories; makes the budget profiles' `images` policy real)
**Context.** DEC-219: characters, places and props are never made on cheap image AI. EXPLORE found 87 % of character
sheets and 56 % of shot images on pollinations, and 8 of 9 stories on prompt-only because the edit chain never ran
while `allow_paid` was off (A-123). The budget profiles' `images` key was validated and never read (E5 D4).
**Decision.**
- A story created from phase 7 on may carry `generation_profile.pipeline: "v2"` (optional schema key; own commit
  `7425fd3`). It is the one switch for every phase-7 behaviour (plan A1).
- New `clipping/aistory/media_policy.py`: `role_chain(role, kind, merged, story)` for the roles sheet, plate, prop and
  keyframe. A legacy story gets `gen.chain_from_env(kind, merged)` unchanged. A v2 story gets its budget profile's
  `roles[role]` when the profile's `images` policy is `quality_roles`, else its env chain; either way with
  `LOW_QUALITY_LINKS` (cloudflare/flux-1-schnell, pollinations/flux, fal/flux-schnell, openai/gpt-image-2-low) removed.
  The style preview is not a role and keeps the free chain, labelled a draft.
- Every chain site that serves these roles asks it: `imaging.resolve`/`estimate`, the refimages plans, and in
  `steps/assets.py` the quote, the chain rows, the sticky slot validation, the switch offer and `make_image` (before
  the DEC-204 pin). The estimate and the run use one chain (RC-V6).
- The shipped `quality` profile becomes "Quality (billed APIs)": `cap_usd 2.0`, `images quality_roles`, roles sheet /
  plate / prop `gemini/nano-banana-2`, keyframe `fal/seedream-4.5-edit` then `gemini/nano-banana-2-lite`,
  `video_resolution 720p`. `roles` and `video_resolution` are optional profile keys, validated when present.
- `fal/seedream-4.5-edit`: `fal-ai/bytedance/seedream/v4.5/edit`, up to 10 references, a custom size scaled up to the
  model's smallest area (720×1280 → 1440×2560, an exact 9:16), no negative field; $0.04 an image (A-111).
- A v2 story must use `references`: prompt-only is refused at create, patch, the style step and the CLI
  `--prompt-only`. Its "needs an editor" message names each link's reason and the keys to add, with no prompt-only
  offer. Legacy messages are byte-identical.
- A v2 keyframe that is not an exact, even 9:16 is centre-cropped at the source by one ffmpeg frame
  (`media_policy.keyframe_crop`; ffmpeg missing or failing is a `ShotFailed`, never a silent keep).
- New stories: the API create route and CLI `new` give the quality profile (`defaults.quality_generation_profile`:
  tier 2, route api, references, quality, v2) when no profile is sent and both FAL_KEY and GEMINI_PAID_API_KEY are
  set; otherwise today's default. An explicit profile is honoured as sent.
**Consequence.** RC-Q2 (no v2 sheet/plate/prop/keyframe on a draft link) is tested. Legacy stories keep every chain,
message and estimate (the revert experiment: with caps and keys put back, all affected files pass). Commit `e00a97a`.

## DEC-222 — Nano-banana reads only GEMINI_PAID_API_KEY (amends DEC-205; RC-V4 re-pinned)
**Context.** The human funds a paid Gemini key for images (CLARIFY answer 2). Nano-banana had stayed on
`GOOGLE_API_KEY`, the free chains' project, as a phase-6 follow-up.
**Decision.** `LINK_ENV_KEYS` adds `gemini/nano-banana-2` and `gemini/nano-banana-2-lite` on `GEMINI_PAID_API_KEY`;
`GeminiImageAdapter` reads the link's own variable and sends `imageConfig.imageSize "1K"`. The free Gemini LLM, vision
and TTS links never read the paid key; Veo and nano-banana never read `GOOGLE_API_KEY`.
**Consequence.** Until the human adds `GEMINI_PAID_API_KEY` in Settings, nano-banana is skipped as "no API key" on
every story (legacy chains included; none used it, since `allow_paid` is off). The RC-V4 test is re-pinned on purpose.

## DEC-223 — Cap defaults 2.00 / 6.00 / 20.00
**Context.** The human set the per-episode budget for an every-shot episode at about $1.50 with caps $2 / $6 / $20
(CLARIFY answer 1).
**Decision.** `PER_EPISODE_CAP_USD 2.00`, `DAILY_CAP_USD 6.00`, `PER_STORY_CAP_USD 20.00` in all five places (budget,
config/CLI, API models, config adapter, the dashboard Settings form). `allow_paid` stays off.
**Consequence.** Caps already saved in Settings still win (this VPS saved 1/3/10: the human changes them in Settings
when ready). The five-place agreement test is re-pinned on purpose; tests that pinned 1/3/10 moved to the new numbers;
three estimate-guard hashes re-pinned, each reproduced with the caps forced to 1/3/10.

## DEC-224 — AI Story's own writing chain: NIM nemotron-3 ultra, then super, then OpenRouter mistral-medium-3.1, then free Gemini (amends DEC-206's chain, RC-S4 exception in `llm.py`)
**Context.** E4 found 100 % of the writing on `gemini/gemini-3.5-flash-lite`, with a climax line spoken twice and a beat
sheet with no causality. The human: "use the Nvidia free endpoint if possible, else OpenRouter". A free bench on
2026-10-01 (story B FR and story A EN; E1/E2/T1; 3 samples; `tools/bench_llm.py --episode-prompts` with replies kept):
- the NIM default `nemotron-3.5-lightning` timed out at 300 s on all three prompts;
- `nemotron-3-ultra-550b-a55b` and `nemotron-3-super-120b-a12b` return no JSON unless thinking is off; with it off,
  super answers in 1–23 s (about 65 % valid; failures are word-cap overruns) and ultra in 11–70 s with the best beat
  sheets, but often answers HTTP 500 in under a second;
- `glm-5.3` writes well but takes 110–300 s; `deepseek-v4.1-flash` and `kimi-k3` 125–300 s; `kimi-k2.6` and
  `mistral-large` are not served;
- every model fails E2's `sfx_cues[].at` in the same way: a prompt problem, left to stage 5c.
**Decision.**
- `registry.DEFAULT_STORY_LLM_CHAIN` = `nvidia/nvidia/nemotron-3-ultra-550b-a55b`,
  `nvidia/nvidia/nemotron-3-super-120b-a12b`, `openrouter/mistralai/mistral-medium-3.1`, `gemini/gemini-3.5-flash-lite`.
- `llm_call.resolve_chain`: `STORY_LLM_CHAIN` (Settings, then env), then `LLM_CHAIN` (Settings, then env), then the new
  default. Clip jobs keep `DEFAULT_LLM_CHAIN`. `STORY_LLM_CHAIN` is a persisted, validated Settings key.
- `llm._NIM_REASONING_FAMILIES` gains `"nemotron-3-"` (thinking off). It does not match `nemotron-3.5-…`. This is the
  stated RC-S4 exception.
- `pricing.LLM_PRICES` gains `openrouter/mistralai/mistral-medium-3.1` at $0.44 / $2.20 per M (A-115). DEC-115 keeps it
  skipped while `allow_paid` is off; DEC-206 books it when on.
**Consequence.**
- On this VPS (no `LLM_CHAIN` set) AI Story writing now tries NIM first and falls back to Gemini.
- The NIM links are still the DEC-073 floor (`primary=False`): a host keyed only on NVIDIA is refused unless the slow
  chain is allowed; here Gemini is keyed.
- Seven tests that pinned `DEFAULT_LLM_CHAIN` as the story chain now pin the new default. The story/clip refusal test
  keeps its one-rule check, but the two texts now name different keys.
- Commit `c02ec6b`.

## DEC-226 — Structured looks and v2 sheet, plate and prop prompts (part 1 of 2; part 2 = props as entities, stage 3c)
**Context.** E1/E3: a character's look is a ≤ 45-word free-text descriptor; the portrait is chest-up, so no image
ever carries full-body proportions or a height against the rest of the cast; the plate omits the layout notes;
props have no scale. DEC-219 asks for proportions, traits and wardrobe in the prompts.
**Decision.**
- Optional schema blocks (own commit `6caed8f`): `character.look` {build, silhouette, face, hair, skin_material,
  height_cm 5–500, palette ≤ 4, wardrobe_sets 1–3, season_change}; `character.dossier` (its writer D1 is stage 5a);
  `place.look` {layout_map, scale_note, lighting per time variant, props_here}; `prop.look` {scale_cm, material,
  colour, scale_phrase, where_when}.
- New calls on v2 stories: D2 character look (380 tokens out, input budget 2290), D3 place look (300 / 1940), R1v2
  prop look (220 / 1170). D2 runs in cast order and sees every height already written, so the cast shares one scale.
  No entity name may appear in a visual field.
- v2 order: K1 → D2 → sheets; P1 → D3 → plate; R1 → R1v2 → prop image. Calls are idempotent and saved. A
  `<kind>:<id>:text` regenerate rewrites the look too (no new target; the estimate counts 2 calls). The cast and
  places estimates count the new calls on v2 only.
- Renderers `shots.render_look/render_place/render_prop` (≤ 45 words; relative height against the others in the
  frame, by handle). New builders `portrait/turnaround/expressions/plate/prop_prompt_v2`: a full-body reference
  (≤ 130 words), the turnaround and expressions as edits of it with role text, the plate with its layout map,
  lighting and resident props (≤ 150), the prop at real scale (≤ 80); each ends with the positive constraints clause
  (DEC-225's negative-prompt rule, A7). Legacy builders and their goldens are untouched.
**Consequence.**
- Legacy stories: every prompt, estimate and golden unchanged (Tier-1: local 6680/1, CI env 5885/765).
- Re-pins on purpose: the closed-object guard's optional-key table, the MAX_TOKENS / SCHEMA_NAMES / INPUT_BUDGET
  registries (three new ids), and the v2 cast-estimate test (3 D2 calls).
- D3's 300-token cap fits up to P1's 3 time variants; a place with more variants or a larger props registry (stage
  5b's D6 allows 8) can overflow it: re-measure in stage 5b.
- A signature item keeps its stored capital inside the prose ("with Comically oversized …"): cosmetic, for stage 3b.
- Commits `6caed8f` (schema) and `ffcfa4d`.

## DEC-234 — Tests are chosen by what a change touches; the full suite runs on CI and once before main (amends DEC-176)
**Context.** The human, 2026-10-01, during phase 7: "Think when running tests which are useful and which can be skipped
because the edit did not touch the part. Keep this reasoning at all times." DEC-176 ran both full suites (≈ 3.5 + 3 min)
after every stage, even when a stage touched a few modules. CI already runs the full suite on every push (CI
environment, x86).
**Decision.** For each stage, before running anything, write down the selection and why:
1. The stage's new and edited tests.
2. Every test file that imports or names a changed module or file: `grep -rlE "<module import name>|<path>" tests/`
   for each changed file (for a changed template, schema or JSON, the tests that load it).
3. The guard tests of the regression-contract items whose area the diff touches, and only those:
   - `clipping/aistory/render/**`, timing or the subtitle builder → `tests/test_aistory_render_golden*.py` (RC-M2) and
     the partial-render test (RC-M8);
   - `web/api/**` or the dashboard → the no-auth tests (RC-M9): `tests/test_auth_opt_in.py`,
     `tests/test_auth_token.py`, `tests/test_dashboard_no_sign_in.py`;
   - `clipping/studio/**` → `tests/test_render_layer_guard.py` (RC-A1);
   - prompts, prompting or shots → the prompt goldens of the touched builders (RC-M1, RC-Q1);
   - budget, config or Settings defaults → the five-place agreement tests.
4. Run that selection in both environments (`-o addopts="" -n 4`), since the CI environment skips the local-only API
   tests.
5. The full suites run: on CI at every push (the full CI environment); one full local run before a merge to `main`,
   at the phase close, or when a selection grows past about half the suite. Docs- or `.claude/`-only changes run only
   the tests that read those files (unchanged from the memory rule of 2026-10-01).
**Consequence.** A stage's test time follows its blast radius. The action-log line of every stage names the
selection rule it used and its counts; a skipped area is a stated choice, never a silent one. CI on the push is the
backstop for anything the selection missed, so a push is due after every stage.

## DEC-225 — Layered keyframe and clip prompts with reference roles; positive constraints instead of an unsent negative (v2 stories)
**Context.** E1: the shot's own content was 7.6 % of a 190–280-word prompt, after 34–72 words of descriptors; about 70 %
was episode-wide boilerplate; the reference images were sent with no role; no hosted image link sends a negative
prompt; the clip prompt was the raw action. E2: nano-banana and seedream read long natural prose and weigh references
by instruction; seedance's prompt limit is unpublished (A-110).
**Decision.**
- `prompting.layered_shot_prompt` (130–220 words), in order: reference roles ("Image 1 is <handle>'s reference (keep
  identity, proportions and outfit exactly) …"), the beat (`video_action` and the spoken line's emotion and delivery),
  staging (each subject by handle with `render_look` and its relative height, left/right, facing), the framing
  coerced to the subject count (a two-shot for two characters on `medium_single`; `over_shoulder` names whose
  shoulder; no "skin detail" or forced shallow focus on a style with no depth of field), the place slice (full layout
  for wide/medium, light and one element for tight framings), a style tail (rendering and palette) and the clause
  "Clean frame: no captions, logos or watermarks; each character appears once."
- `prompting.layered_clip_prompt` (≤ 80 words): what moves (the shot's `motion` when stage 4 provides it, else
  `video_action`), one secondary motion, the camera phrase, "The set, the lighting and every character's look stay
  exactly as in the first frame.", the style's motion suffix. Stored in `shot.video_prompt`; `build_video_prompt`
  sends it when present.
- References on v2: identity sheets (the expressions sheet for close-ups), the set, the turnarounds, the props, up to
  10; per-link limits (seedream-4.5-edit 10, nano-banana 14) apply only to shots with `prompt_layout: "layered_v1"`;
  every other shot keeps 4. The role text is written from the list actually sent.
- The negative prompt keeps being computed and hashed (no hash moves); links with a negative field (kling) still send
  it.
- `resolve_shot(v2=)` from build_storyboard, refresh_prompts and the storyboard edit path; on v2 a camera or
  modifier edit rewrites only the clip prompt.
- The v2 shot card shows the clip prompt, the reference roles and word counts against 220 / 80 (read-only).
**Consequence.**
- RC-Q1: story A sh01 resolves byte-identically on the legacy path (image sha `71348b92…`, clip hash `48955e43…`).
- Dry check on a v2 copy of story A with sample looks: sh01's image prompt is 217 words and its clip prompt 59;
  across the 20 shots, images 159–220 words (one place-only close-up 91) and clips 45–70; no names, no "..", no ".,".
- A v2 shot on a link outside `REFERENCE_LIMITS` defaults to 10 references: a 4-slot local ComfyUI would refuse it
  visibly. Not reachable on the quality profile.
- Not built: the plan's optional `plan` key on the shot (unneeded so far).
- Commits `2c6c786` (schema) and `5eef509`.

## DEC-226 (part 2) — The plot's objects become props (amends DEC-171 for v2 stories)
**Context.** E1 was told "props: always [] -- this story has no props" (DEC-171), so story A's toaster and story B's
key were never entities: no look, no scale, no reference; the toaster was on screen about 6 % of story A's runtime.
**Decision.** On a v2 story from episode 2, E1 may name up to 2 `new_objects` {name ≤ 4 words, one_line ≤ 15,
owner}; scenes tag them `%prop_<slug>`. The script step creates idempotent prop stubs (no descriptor), which the next
places run completes with R1 and R1v2 and an image (already estimated there). The storyboard refuses on v2, before
any call, while a scene's prop has no image (named, with its estimate). `validate_t1` on v2 refuses `insert_prop`
without a prop subject. Episode 1's objects come from the knowledge step's props registry (stage 5b).
**Consequence.** v1 prompts and schemas unchanged (the field is absent unless offered). A new unapproved prop folds
the story's places approval back (DEC-123), so the episode gate fires first; the storyboard refusal is the second
line. Commit recorded in the action log.

## DEC-235 — fal only for every quality image: no Google billing (amends DEC-221's roles; DEC-222 stays as code)
**Context.** The human, 2026-10-01: "I have a Gemini subscription, I already put a Gemini API key but no money on it."
A Gemini app subscription does not fund the Gemini API, and nano-banana is priced per image there, so the plan's
sheet, plate and prop links (nano-banana-2) could not run. Asked to choose, the human picked "fal only".
**Decision.** The Quality profile's roles move to fal Seedream 4.5: the first portrait, the plates and the props on
its text-to-image endpoint, the turnaround, expressions and other variants on `fal/seedream-4.5-edit` with the
portrait or plate as reference; keyframes stay on `fal/seedream-4.5-edit`. Nano-banana stays in the code on
`GEMINI_PAID_API_KEY` (DEC-222), unused by default; `nano-banana-2-lite` remains the keyframe role's second link and is
skipped as "no key" while no paid Gemini key exists. The free `GOOGLE_API_KEY` keeps serving the free LLM, vision and
TTS calls. Built as stage 2c after stage 4.
**Consequence.** One funded key (fal). One-off images per story (3 characters × 3 sheets, 2 plates, 3 props) ≈ $0.56
instead of ≈ $0.94; keyframes $0.04 each as planned. Nano-banana's 4 character + 3 style reference slots are not used;
Seedream 4.5 takes up to 10 references (A-111).

## DEC-227 — serial_60s_v2: 6–10 beat shots of 5–12 s, T1 v2 with motion and staging, every shot animated, a per-story 1080p switch (v2 stories)
**Context.** The human (CLARIFY 3 and 5): seedance at 720p on every shot, 1080p a per-story switch; drop the shot count
("fal can generate up to 15 s videos") to 6–10 shots of 5–12 s in the 55–75 s window; the 3–6 s hook was accepted.
**Decision.**
- New template `serial_60s_v2` (window 55–75, target 62, `tighten_above_s` 72 because the schema needs it below the
  window's end; scenes and shots 6–10; body 5–11 s, count 4–7, default 6; hook 3–6 s; cliffhanger 4–10 s; recap 3–4 s;
  `min_shot_s` 3.0; optional `shots_per_scene [1, 2]` and `max_shot_s 12`). A v2 story is created on it;
  `episode_defaults` takes the template's `shots_per_scene`. v1 templates are untouched.
- T1 v2 / T1rv2 (new ids; T1/T1r untouched): exactly 1 shot per scene, 2 only past 12 s; action ≤ 45 words, `motion`
  ≤ 25 (stored as the shot's `clip_motion`, since `motion` is the Tier-1 camera dict), `staging` ≤ 4 {subject,
  position, facing, expression}. Inputs: the place descriptor, each line's delivery, on-screen text and sfx, the props
  with their look, the previous shot's action and staging, the beat's purpose. A close-up ask stops the rule pass
  cascading with one shot per scene. Caps measured (DEC-138): T1v2 1040 out / 2020 in, T1rv2 520 / 2060.
- Every shot animated (`animate: all_shots`): `spending_caps`/`plan_refusal` already refused an over-cap run before
  any clip; the refusal now carries the numbers sentence. A shot over 12 s gets 12 s of seedance and a held last frame,
  noted in the estimate.
- 1080p: optional `generation_profile.video_resolution`; `media_policy.video_resolution` = story, else profile, else
  720p; it reaches seedance through `request.extra`; priced from `fal/seedance-1-pro-fast@1080p` (0.0486/s) through
  `pricing.price_key`; the cache key and the clip request hash add the resolution only when it is not 720p, so every
  stored key and clip stays current and a switch re-buys.
**Consequence.**
- Dry check on story B made v2: 8 shots (4.5–6.5 s), 42.1 s, state "under": its lines were written for v1's shorter
  slots, so the writer side must fill v2 body scenes (stage 5c/6a's fill pass and the hard length gate).
- Re-pins on purpose: the template count (3), the MAX_TOKENS/SCHEMA_NAMES/INPUT_BUDGET registries, the dashboard's
  template list and its payload-contract count.
- Commits `e78b887` (schema) and `a2881ae`.

**DEC-224 amendment (2026-10-01, the walk's finding).** As built, every story call skipped both NIM links: their 330 s
provider timeout did not fit the 300 s per-call budget, so all writing still ran on gemini. Fix: per-model timeouts
(`registry.MODEL_TIMEOUTS`: ultra 60 s, super 40 s; others unchanged, so the Clips mode keeps 330 s) and a separate
per-call deadline `STORY_CALL_DEADLINE_SECONDS = 480` (both NIM retry ladders, 3 × 60 + 3 × 40, plus one 180 s gemini
request). A step's predictive planning keeps `STORY_CALL_BUDGET_SECONDS = 300`, so step budgets and their tests are
unchanged; a call can run past 300 s only when every NIM attempt times out.

**DEC-234 addendum (the human, 2026-10-01: "Parallelize what can be done in parallel of the tests").** The two
environments' runs start together (`-n 4` each) instead of one after the other, and work that does not depend on a
test result goes on while they run. Measured on the 63-file selection: 3 min 15 s together vs 3 min 48 s in a row.
A wait loop must never `pgrep -f` a pattern its own command line contains: wait on the PIDs or on `wait`.

## DEC-228 — The story knowledge base (part 1: the document and the dossiers)
**Context.** The human (CLARIFY 8): a full knowledge base, dashboard-approved before episode 1. E3: backstories,
goals, secrets, relationship history, voice patterns and a continuity state were stored nowhere.
**Decision (stage 5a).**
- `knowledge.json` (`story_knowledge_v1`): `rev`, `approved_at`, `world` {geography, period details, visual motifs},
  `timeline` [per episode: ≤ 8 beats {what, place, who, objects, knows_after}], `props_registry` (≤ 8),
  `ledger_seed` {per character: location, wardrobe set, possessions, injuries, relationship notes}; an approved
  document needs all four sections. The same per-character ledger is optional on a series-memory entry (stage 5d
  writes it). Store helpers are atomic and check every id against the story.
- D1 (character dossier) runs on v2 after K1 and before D2. Its caps are measured: 1290 tokens out (the French worst
  case with 3 relationships; the plan's "≤ 420 per call" cannot hold the dossier's own caps), 3890 in. The season arc
  is left out of D1's input (it does not exist when the cast runs, and it would push the worst case past 4000 tokens);
  a character's place in the season comes from the stage-5b timeline.
- A failed D1 does not hold back D2 or the sheets: it is recorded, the step ends failed, and a rerun fills it.
**Consequence.** Stored stories are unchanged. A v2 character costs 3 writing calls (K1, D1, D2). Follow-ups: deleting
an entity leaves its id in `knowledge.json` (the next write is refused until fixed: stage 5b); `drop_character` does
not drop a ledger entry (stage 5d). Commits recorded in the action log.

**DEC-228 part 2 (stage 5b, 2026-10-01).** The `knowledge` step (v2 only, after the season; ends awaiting approval):
D4 world, D5 one timeline call per planned episode, D6 props registry (≤ 3 new props, ≤ 8 registered; new props made
through `places.new_prop` and drawn by the next places run), a deterministic ledger seed; saved per call, resumable,
stopped cleanly by the 30-minute step budget. Measured caps: D4 430 out / 2270 in, D5 3330 / 3930 (the plan's 420 held
only the beats' "what"), D6 540 / 3560; D5's input leaves out the bible and world to stay under 4000 tokens.
`approve_knowledge` requires all four sections, every planned episode and every new object registered, and records
`approved_rev`; any write bumps `rev`, so the base reads stale. The gate is on the script step (and fast-track) only:
storyboards, assets and renders are not gated. Deleting an entity removes its id from the base and makes it stale. The
season's approval is the step's precondition (D6's new props lower the story status until the places run draws them).
Dashboard: a read-and-approve Knowledge step for v2 stories; editing is stage 7.

**DEC-228 part 3 (stage 5c).** Every v2 writing call gets a slice of the knowledge base: per scene (present characters'
goal, need, the relevant secret by word overlap, catchphrases, relationship history among those present, knows-so-far
from the timeline up to the mapped beat and the series memory, the ledger state, "ep N, beat k of m", the place's light
and layout; ≤ 352 words), per episode for E1v2 (≤ 457), per shot for T1 v2 (wardrobe and holders). A scene maps to the
timeline beat that shares the most with it (place 2 points, each character and object 1). New ids E1v2/E2v2/E3v2 with
the no-repeat, first-appearance and shown-reveal instructions, and E2v2's `sfx_cues[].at` as a closed list — the fix for
the field every model filled wrong. Budgets 2870 / 2420 / 3370 in, caps 1750 / 600 / 720 out.

## DEC-229 — The continuity ledger (extends DEC-177/178)
**Context.** E3: location, wardrobe, possessions and injuries were tracked nowhere, so a character could change
clothes or lose an object between episodes without anyone deciding it.
**Decision.** On a v2 story the memory step runs L1 after S3: per present character, location, wardrobe set (checked
against that character's own sets by the step, since a strict schema cannot vary the enum per item), possessions,
injuries and relationship notes. It is written in the same atomic write as the memory entry, so it goes stale with it.
L1 sees the episode's own places and props plus what present characters already held (not the whole roster). Caps
690 out / 3920 in. `series_memory.fold_ledger` gives the state before an episode from the knowledge base's seed;
`context.ledger_before` uses it. Dropping a character drops it from every ledger.
**Consequence.** Writers (5c) and shots see the state as of the episode being made. Legacy stories have no ledger.
Commit `88e4b3f`.

## DEC-231 (part 1) — Narrator on, subtitles floor, prosody and the fr-FR locale (v2 stories; stage 6c)
**Context.** The human (CLARIFY 7 and 11): narrator on by default; two_line subtitles with a 150 ms floor per word_pop
card; edge TTS with rate and pitch per character and per line. E1: TTS got text and a voice id only; story B's mayor
had an fr-CA voice in an fr-FR story; E4: 31 % of story A's word_pop cards lasted under 150 ms.
**Decision.**
- A v2 story is created with the narrator on; the cast step pins a narrator voice distinct from the cast's.
- v2 voice proposals keep to the story's default locale (fr → fr-FR, en → en-US).
- A v2 style lock starts on `two_line` (a user's choice is kept on later edits) and always carries
  `typography.word_min_card_ms: 150` (optional schema key, own commit). A word_pop card shorter than the floor merges
  forward with the next card(s) rather than shifting them, so no card overlaps or drifts; without the key the render
  is byte-identical (RC-M2: goldens and partial render green).
- `voices.base_prosody` reads the dossier's voice patterns (fast, slow, deep, high; word-boundary matches) for the
  pinned base rate and pitch; `voices.prosody_for` adds a per-emotion delta (angry +6 %/+2 Hz, sad −8 %/−3 Hz, fear
  +8 %/+3 Hz, shocked +5 %/+4 Hz, tender −5 %/−1 Hz), clamped to ±20 % / ±8 Hz, applied only on v2 lines.
**Consequence.** Built in a parallel worktree and cherry-picked: commits `8fd7f4b` (schema) and `39871dd`.

## DEC-236 — Fully animated stories: a dashboard story starts on v2 Quality, no shot is ever a still, video keys asked for free
**Context.** The human (2026-10-02), after creating "Cœur Firewall et Larmes de Citron" in the dashboard for a paid
test: "impossible to animate shots; I want only fully animated episodes, no diaporama, even if it costs money; ask
the provider for video and check the API key, then start with the desired setting". Choices given: phase-7 code with
a v2 story, fal Seedance 1 pro fast 720p, refuse unless every shot moves (keep_still the one exemption), caps
2 / 6 / 20. Three gates were closed for every dashboard story: the form always sent tier 1 + `free` (stills with
motion, `animate: none`) without `pipeline`, so `media_policy.new_story_profile` never applied and `quality` was not
offered; `allow_paid` off and saved caps 1 / 3 / 10 (the human's Settings); and nothing refused a shot rendered as a
still when its clip was missing. The video chain test never asks a hosted video provider (RC-V8), so a wrong key
showed only when the first clip was bought.
**Decision.**
- The new-story form starts from `GET /api/stories/new-profile` (`media_policy.new_story_offer`: the profile a story
  made now gets, `quality`, `missing_keys`, `allow_paid`; never a key) and sends no profile until the user changes
  one. It offers the v2 pipeline and "Quality (billed APIs) — every shot animated", and says whether the story will
  be fully animated and why not.
- A `generation_profile.pipeline` patch brings what `store.create` gives that pipeline (episode template unless sent,
  `narrator.enabled` unless sent) and is refused once an episode has a script (`workflow._follow_pipeline_switch`). A
  cast made before the switch stays; the cast step run again on v2 writes dossiers and looks and redraws the sheets.
  The Visual tier card gains "Animate every shot".
- `media_policy.fully_animated(story)`: v2, tier ≥ 2, a budget profile with `animate: all_shots`. On such a story
  `approve_assets` and the render refuse while a shot not pinned `keep_still` has no current clip (never made,
  failed, stale, still generating), naming each with what to do; `fill_failed_with_motion` is not offered. Any other
  story keeps phase 6's behaviour (RC-M3).
- `video.check_key`: one free GET per keyed hosted video link — fal's Platform API
  `GET /v1/models/pricing?endpoint_id=` (`Authorization: Key`; the live price comes back), Gemini's
  `GET /v1beta/models/{model}` (`x-goog-api-key`; proves the key and the model, not the billing). `POST
  /api/settings/check-video-keys` and Settings' "Ask the providers (free)". The chain test stays call-free (RC-V8).
**Consequence.** Commit `2aa7c00`. The human's runbook: `.claude/plans/ai-story/17-phase-7-fully-animated-runbook.md`
(deploy the branch at 0 jobs, allow paid on, caps 2 / 6 / 20, ask the providers, switch or create the story, the
walk with its estimates). Not proven live from this cloud session: fal's Platform API answer shape (read leniently:
`prices[].unit_price/unit/currency`; A-125), and the fal/Gemini hosts are blocked by this container's network policy.

## DEC-237 — Crowded v2 keyframes fit 220 words; a descriptive name is kept; no lettering drawn (the W-mid leftovers)
**Context.** The W-mid walk (2026-10-01) left keyframe prompts of 234 and 243 words against
`KEYFRAME_V2_MAX_WORDS` (220) and a 'P' badge on a vest despite "no logos". Measuring a crowded shot on
2026-10-02 (three characters with their sheets, the set, two props, a 41-word tagged action) gave 308 words and a
new defect: "lifts the golden *the object* on a thin chain" — names were swept from the action after its tags
became descriptor handles, and a prop's name is usually the noun of its own descriptor.
**Decision (v2 stories only; legacy byte-identical, RC-Q1).**
- Past the budget ladder, `shots._LAYERED_LAST_RUNGS`: the reference roles in one compact sentence
  (`prompting.role_text(compact=True)`), props in `render_prop`'s short form, looks/place/rendering cut further, and
  last the layout and the prop sentences left out (the set and prop images are sent; props stay named in the
  roles and the beat). Looks keep 4 words so the presentation leads (stage 3d). A prompt that fits earlier is
  unchanged.
- `shots._v2_name_map` leaves out a descriptive name (every word, articles aside, in the entity's own descriptor or
  look); every proper name is still stripped (spec 2.3).
- Every v2 clean-frame clause says "no captions, lettering, logos or watermarks" (re-pins on purpose:
  `test_aistory_prompting` and `test_story_shots`' pinned strings). The same-character clause is 13 words (A7's
  12-word target was a guideline).
**Consequence.** Commit `8cf3f2c`. Stored v2 prompts (only the walk story) re-resolve on refresh. Whether Seedream
honours "lettering" is checked in the acceptance walk.

## DEC-238 — A render is complete only when its audio and frames are whole
**Context.** The action log's open items: the bgm stem cut short on ffmpeg 7.1.5 (phase 6 stage 10: 10/12 with an
mp4 input of the audio stage, moved to `amovie`; once in ~76 plain runs, never reproduced), and the final's frame
count never compared with the timeline (the 30/45-frame bug class).
**Decision.** After stage A the runner measures the mix and its three stems from their WAV headers
(`runner.wav_seconds`: RIFF `fmt `/`data`, any sample format — the renderer writes 32-bit float, which the stdlib
`wave` refuses); one shorter than `timeline.total_s` by more than a frame fails the render at A ("render again"),
nothing after it runs, nothing is published. `_output_record` counts M's framemd5 against `total_frames`; a mismatch
fails before publishing (the last good final and its baseline stay). Files that are not a WAV / a framemd5 are not
measured (the runner tests' stand-in bytes). No plan stage was added, so no plan hash moved.
**Consequence.** Commit `ff7d3ee`. Proven on real ffmpeg 6.1.1 in the cloud container: the tier-1 golden measures
4.75 s for every WAV and 142 frames and completes. A retry of stage A was not built (a rewrite of the runner's stage
loop for an unreproduced fault); the user re-renders, and the partial render reuses every cached shot.

## DEC-232 — Phase 7 stage 7: weak hosts are told the billed preset and its price; looks, dossiers and the knowledge base are editable (A18, A19)
**Context.** DEC-219 asked the app to strongly recommend billed APIs when the host cannot run good models, but the hardware card listed only local models and free hosted chains. Phase 7's writers (D1 dossier, D2/D3/R1v2 looks, the knowledge step) produced documents the dashboard could only read: the Knowledge step was read-and-approve, and Cast and Places showed no look or dossier. The walk found the story page had no budget-profile control (F7), and a route planning 0 clips gave its reason only in a tooltip (F6). The cast and places estimates still said "no LLM price table" after DEC-224 added one.
**Decision.**
- `media_policy.preset_estimate(merged=None)` is pure: `pricing.py`, the `quality` profile and `serial_60s_v2`, nothing else.
  - **An episode:** episode 1's shots (hook + `default_body_count` + cliffhanger, one each) cover `target_s` in equal shots. Each is a clip on the profile's first-in-chain hosted video link at its size, rounded per shot by `video_plan.requested_seconds` (DEC-208), plus one keyframe on the keyframe role's first link.
  - **Once per story:** 3 characters × (portrait + 2 sheet edits), 2 plates, 3 props on the roles' links.
  - **Output:** the assumptions are stated in words.
  - **Today:** 8 shots, 64 s billed × $0.022 + 8 × $0.04 = $1.73 an episode; 14 × $0.04 = $0.56 once per story.
- `hardware.recommendations_for` puts a billed-preset row first on cpu_only, container_no_gpu and low: the preset, its two numbers, FAL_KEY. It is built when asked, so it never states an old price. `new_story_offer` carries the same `estimate`. Settings' hardware card and the new-story form show them.
- **Entity edits:** `PATCH` of a character takes `look` and `dossier`; a place or prop takes `look`. They are merged as `personality` is and checked by the schema and against the story's ids. A look may not drop the wardrobe set the ledger seed uses. The edit clears the entity's approval and outdates its prompts like any other edit. A legacy story is refused (409, RC-M3).
- **Knowledge edits:** `PATCH /api/stories/{id}/knowledge` edits:
  - the world (merged);
  - a beat, named by its episode and 1-based position;
  - the props registry (the whole list);
  - a ledger-seed entry (merged).

  Each write is checked as the knowledge step's writes are and moves `rev`, so the base must be approved again (DEC-228 part 2). Nothing changed: nothing written. 409 for a legacy story, before the knowledge step, and while a step runs.
- **Dashboard:**
  - Cast and Places edit the dossier and the looks (v2 only).
  - Knowledge edits inline and says "Approve again" when stale.
  - The Visual tier card gains the budget-profile select (F7) and shows the reason for a 0-clip plan (F6).
- **Estimate sentences:** they price a billed LLM link a step can fall through to, "up to $Z if the free links fail", from `pricing.LLM_PRICES` (`llm_spend.worst_call_usd`). `est_usd` is unchanged.
**Consequence.**
- Legacy stories are unchanged: no field was added to their documents or prompts, and their estimate sentences differ only in the LLM wording.
- No test was re-pinned. 29 new tests, each fail-first.
- Full suites: local 6830 passed (plus the container's 2 root-only failures); CI-like 6132 passed / 680 skipped (plus the same 2 and the 14 fastapi-fixture errors).
- Tier-2 at 375 px passed, 20/20 checks (stage-7 report).
- Images drawn from an old look are kept until regenerated, as after a descriptor edit.
- Built in worktree `work/phase7-s7` (`385c98c`, `01ffdcf`, `297cdd4`, `067dafd`) and cherry-picked: `97bcc73`, `aa0331b`, `c24ea85`, `14147e8`. Combined full suites on `14147e8` (this container): local 6843 passed / 1 skipped, CI-like 6145 / 680 skipped, plus the container-only 2 root failures and 14 fastapi-fixture errors.
- Follow-up: a look edit does not mark the sheets drawn from the old look stale; the dashboard cannot add/remove a relationship or a wardrobe set, nor edit `props_here`/`where_when` (the API can).

## DEC-230 — The judges: J1 reads the script as a first-time viewer would, J2 checks each keyframe; a keyframe approval before any v2 clip (v2 stories; stages 6a and 6b)
**Context.** E4's diagnosis: story A's climax line was spoken twice verbatim, a character was never introduced, and the episode's object was never shown. Story B shipped its hook with no on-screen text. E4 checks a script against the bible; nothing judged what a viewer takes away, or whether a keyframe shows its beat. The plan (A16) asked for a judge that can block approval, and a vision check of the keyframes before any clip is bought.
**Decision.**
- **J1** (new `steps/judge.py`, prompt `J1`, schema `first_watch_check`): one call on the story writing chain at the analytic temperature, after E4 in the script step. It reads only what a first-time viewer has: the script digest E4 reads, the objects and the scenes that show them, the hook text, the reveal, and the previous recap. It returns who wants what, what happens, why it matters, `passed`, and issues from `unclear_goal, unmotivated, unintroduced, object_unseen, repeated_line, no_hook_text`. Caps measured: 920 out, 3,680 in.
- **Deterministic checks lead the same report** and fail it whatever J1 says: any two lines whose normalised-token Jaccard is 0.7 or more (3 words or more; accents, case and punctuation folded; at most 6 issues), and a hook with no on-screen text. The report is stored as `script.first_watch` (optional key, own commit `afce483`) and goes stale like the consistency report.
- **`approve_script` (v2):** a missing or stale report is never approvable; a failed one is refused unless "approve anyway", which `approved_anyway` records.
- **E2v2/E3v2 replies** that repeat a line of the episode are refused, so the retry and the chain's next links ask again. E3v2 also needs the hook's on-screen text whatever the hook style, and its ask says so (E3v2's budget 3,370 → 3,380).
- **J2** (prompt `J2`, schema `keyframe_check`, 110 tokens out): in the assets step, once the keyframes exist, one vision call per shot on VISION_CHAIN (free Gemini first, `describe_upload`'s gates and booking). It sends the keyframe, the previous shot's keyframe and the shot's brief (action with names, framing, place, each character's look and staging, the props; capped, 831 tokens at worst), and gets back `{shows_beat, missing, continuity_issue}`. Stored in `assets.keyframe_verdicts` with both images' sha256 (own commit `94a10a8`), written after each call. A verdict current for the same images is never asked again. A vision chain that cannot run skips J2 with a line in the feed.
- **`approve_keyframes`** (`keyframes:<ep>`: API route, no auth; CLI `approve ID keyframes:N [--anyway]`): every shot must have a current keyframe; "anyway" covers a failed or missing verdict, never a missing keyframe. It writes `assets.keyframes_approved {at, anyway, fingerprint}`, the fingerprint of the keyframe images. The approval is stale once one changes: derived, never cleared (DEC-155).
- **RC-Q3:** while the approval is missing or stale, or a keyframe is still to make, a v2 episode's clip plan is held: shown and priced, out of the run's total, caps and readiness, like animate off. The assets step makes the keyframes and stops before the video phase, and a clip regenerate is refused, each saying "approve the keyframes first".
**Consequence.**
- Legacy stories are never judged, held or keyframe-approved: same prompts, estimates, runs, routes and pages.
- A v2 episode now needs two human looks before money is spent on clips: the script (with J1) and the keyframes (with J2). DEC-202's "video last" becomes two assets runs with the keyframe approval between them.
- Re-pins on purpose: the MAX_TOKENS / SCHEMA_NAMES / INPUT_BUDGET registries, E3v2's measured budget, and the v2 script-step test fixture (lines of each scene's own, a J1 answer).
- J2's recall and false positives are unknown (A-117): "anyway" stays.
- Follow-ups: the fast track on v2 should stop at the keyframe approval; a shot-image regenerate leaves J2 to the next assets run.
- Built in worktree `work/phase7-s6` (`afce483`, `954ef6e`, `94a10a8`, `748841a`, `e227568`) and cherry-picked: `254f2c9`, `32dbd47` (one conflict in `approve_assets`: DEC-236's `_require_every_clip` kept before `_refuse_length`), `b46360c`, `03781f6`, `3088eb7`. Combined full suites on `3088eb7` (this container): local 6880 passed / 1 skipped, CI-like 6181 / 681 skipped, plus the container-only 2 root failures and 14 fastapi-fixture errors.

## DEC-231 (part 2) — The hard length gate and the fill pass (v2 stories; stage 6a)
**Context.** Story B shipped 9.9 s under its window's floor, with only a warning at render. The stage-4 dry check of story B made v2 measured 42.1 s, "under": v1-sized lines leave v2 body scenes short. The human (CLARIFY 7): a hard length gate.
**Decision.**
- `steps/gates.length_refusal` checks a v2 episode against its template's `window_s` (55–75 s for `serial_60s_v2`), using the timing the render cuts it to (`timing.episode_pass` with the storyboard, whole frames when the board is). There is no "anyway".
- One-line calls: `approve_script` and `approve_storyboard` (estimated length), `approve_assets` and the render's last precondition (measured; the render's pre-job 409 says the same).
- Each refusal states the length, how it was measured, the gap, the window and what to do: lengthen or shorten which scenes, then which approvals and steps to redo.
- A legacy story keeps the render's warning.
- **Fill pass** (script step, v2): once the script is complete and not approved, while its estimate is under the window, E2v2 runs again on the shortest body scenes. Each scene is first raised to its slot's top, so its word budget grows, and is asked to write fuller. At most 2 calls a run, stopping once inside.
- The fill pass runs before E4 and J1, so they judge the filled script. On a script checked already, a rewritten scene goes through `mark_changed` like a regenerate.
- It is logged with before and after values and recorded in the step result (`fill: {before_s, after_s, window_s, scenes, failed}`). A failed call keeps the scene as it was.
**Consequence.**
- A v2 episode outside its window can be neither approved nor rendered. Lengthening it means the fill pass (run the script step again), a regenerate with a note, or an edit.
- Each re-run of an under-window, unapproved v2 script spends up to 2 E2v2 calls, plus E4 and J1 again when it was already checked.
- The fill pass cannot add a scene: an episode whose scenes are all short after two fills still needs a writer's hand.
- Commit `954ef6e` in the worktree, `32dbd47` on the branch.

## DEC-239 — Phase 7 close-out fixes: the v2 fast track, the keyframe card, the CLI's --settings, two render-path edges
**Context.** Merging stages 6 and 7 left the agents' open issues and the rest of the log sweep (2026-10-02): the fast
track auto-approved v2 assets whose clips were held and told the user to "approve it yourself" outside a window v2
never allows; the keyframe approval had an API and a CLI but no screen; the CLI read keys from the process
environment only, while the VPS keeps them in Settings; a tier-3 story on a silent link fell back to tier 2 without
a word before buying; the preflight did not check two filters every render uses.
**Decision.**
- **Fast track on v2** (`9287ce4`): after the assets run that makes and checks the keyframes, a v2 episode at
  tier ≥ 2 whose clips are held stops at the assets with the hold's sentence (approve the keyframes, then Continue);
  the script refusal names a missing, stale or failed J1 report and never offers "approve it yourself" outside the
  window; on a fully animated story the paid stop never offers keep-still or animate-off as a way out
  (`paid_verdict(fully_animated=)`).
- **The Keyframes card** (`edb719c`): the storyboard pane lists each shot's J2 verdict, Approve keyframes, and
  after a refusal the server's sentence and Approve anyway; between the keyframes and the video phase.
- **CLI `--settings`** (`33b740d`): every step, estimate and gate of the run reads the stored Settings over the
  environment, never printing a value. The CLI reads the file itself (it never imports `web`); a test pins the same
  path and values as `settings_store.load`.
- **Tier-3 on a silent link** (`9f1203a`): the clip estimate says the shots render as at tier 2 (A-108).
- **Preflight** (`2cb7568`): `movie` (the paper texture, every render) and `amovie` (tier-3 native audio) are
  required filters.
**Consequence.** Each change fail-first; the selections of the touched areas green in both environments; the final
full suites on the branch head are recorded in the action log. Not done (follow-ups): a look edit does not mark the
sheets drawn from the old look stale; regenerating one shot image does not run J2 (the next assets run does); each
re-run of an unapproved, under-window v2 script spends up to 2 more fill calls; the dashboard cannot add or remove a
relationship or a wardrobe set (the API can).

## DEC-240 — Every generation link's prompt size limit is known, listed and enforced before a call (phase 7 follow-up, F1)
**Context.** The human (2026-10-02): "some providers have limited prompt size so check that also". No adapter knew or
enforced one; the v2 builders use one fixed word budget for every link (keyframe 220, clip 80), the keyframe ladder
sends its last rung over budget anyway (DEC-237), and a vendor's HTTP 400 or a silent truncation was the only signal.
**Decision** (`25b460a`, `d8f6633`, `c42223a`, `38b924f`).
- `clipping/providers/prompt_limits.py`: one `Limit` per link label (or per provider: `edge/*`, `pollinations/*`): a
  cap the API accepts in characters (URL-encoded for Pollinations, whose prompt rides in the URL path), tokens or
  words, its source, and `verified` (the vendor publishes it) or ours (A-126). A text encoder's window (FLUX's T5, 512
  tokens) is a separate `window_tokens`: it bounds the word budget but never refuses — the v1 fixture shot prompts
  reach ~2200 characters and were always sent.
- `budget_words(link, default)` = min(`max_words`, `max_chars / 6.5` (`/ 10` URL-encoded), `(max_tokens or
  window_tokens) × 4 / 6.5`) for the prompt builders (stage F2 makes them fit each link); today's fixed budgets fit
  every default link. Tokens are counted as `pacing.estimate_tokens` counts them everywhere (~4 characters).
- One check point: `generation._run_candidates`, after the journal lookup (a kept answer or a resumed request was
  accepted when sent) and before every gate. `PromptTooLong` (a `ProviderError`) names the link, the measured size
  and the limit ("fal/kling-2.5-turbo-std accepts 2500 characters; this prompt is 3120; not sent"); the chain moves
  on at no cost (no estimate, no budget check, no free-tier slot, no journal entry; `steps/pacing._UNSENT` and
  `sticky_link` read it as "may still serve"). Never a silent truncation.
- Live values: the free key check (DEC-236) also reads fal's published OpenAPI schema
  (`https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=<id>`, no key sent) for each fal video link and keeps
  `prompt.maxLength` in `data/provider_limits.json` (`provider_limits_v1`, next to the Settings file; the CLI reads
  it by path, never importing `web`); a published value replaces the table's, "not published" leaves the table in
  charge. `--ai-story prompt-limits` lists every link's limit and source.
**Consequence.** Seedance's and Seedream's numbers are ours until fal publishes them (A-126).
`test_story_sticky_link`'s fake Cloudflare accepted 2164–2176-character prompts the real Workers AI (2048) refuses:
its cap is lifted in that file alone. Not covered: Kling's `negative_prompt` cap (negatives are ~40 words); the
pre-call estimate (`gating.link_summary`) can still call a link runnable when the prompt would be refused — moot once
F2's builders fit each link, pinned there by a test that every built v2 prompt `fits()` its planned link.

## DEC-241 — A written episode moves to v2 by being regenerated, not by a new story (phase 7 follow-up, D)
**Context.** The human (2026-10-02) pressed "Animate every shot" on a story whose episode 1 had a script and got
"Create a new story on the pipeline you want instead" — a dead end: nothing could reset an episode, the card kept
showing the values the server refused, and its hint promised redrawn sheets that `_images` never redraws.
**Decision** (`7fc9b2c`, `f1b99fb`, `28a6b39`).
- **Discard, never delete** (`store.discard_episode`): the episode folder moves to
  `episodes/_discarded/epNN-<UTC stamp>[-k]/` (recoverable by hand; `list_episodes` lists `ep<NN>` folders only, so
  the archive is never an episode); its `series_memory` entry and feedback leave `season.json`; the proposals it
  wrote for N+1 go into its archive while its own `proposals.json` (written from N-1) is kept in a fresh `epNN/`.
  Episodes are discarded latest first so no remaining memory entry closes a hook a removed one opened; a discard
  that would leave that situation is refused before anything moves.
- **Spend**: the ledger rows of a discarded episode are marked `discarded`; `totals(ep)`, `episode_entries(ep)`,
  the bundle view and `workflow.episode_ledger` skip them (the new episode starts at $0 against its cap) while the
  story total keeps them (the money was spent); the daily file is untouched. Every per-episode reader goes through
  these helpers, never a hand-written `row["ep"] == ep`.
- **`POST /api/stories/{id}/switch-pipeline`** `{generation_profile, regenerate_episodes: true}`
  (`workflow.switch_pipeline`): refused 409 while a job of the story runs; discards every episode with a script,
  applies the same patch as `patch_story` (template and narrator follow), completes the jobs left awaiting approval
  on an archived document with a `discarded: <archive>` stamp (never `approved_at`; `JobResponse.discarded`), then
  queues the next needed step itself — cast, else places, else knowledge; a refused gate leaves the switch standing
  and says why in `next_step.refused`. The plain PATCH keeps its 409 but it is structured
  (`{message, code: "pipeline_switch_has_scripts", episodes}`) and its sentence names the button.
- **Redraw from the look, flag-free**: when a v2 look is written for an entity that had none (a legacy cast or place
  moved to v2), its image refs are cleared (portrait and sheets; every plate; the prop image) and the same step run
  draws them again from the look; the cast and places estimates count them. The card's hint says so and is true now.
- **The card**: on the structured 409 it offers "Regenerate episode N on v2" with a confirm that lists what is
  archived (script, storyboard, images, clips, render), kept (cast, places, props, season, music) and what runs next;
  a refused save shows the server's values again (the optimistic tier/route/profile are reverted and the story
  refreshed).
**Consequence.** The auto-queued cast step runs without showing its estimate first (the caps still hold; the confirm
says it). Places & props keep their approvals after a switch, so the page can show that step done while its looks
are still to be written — the confirm and the hint say to run it again; it is not queued. Regenerating the text of a
v2 character that has images but no look drops those images until the cast step redraws them. No CLI command for
the switch yet. `ledger.mark_discarded` holds only the instance lock and relies on the route's in-flight refusal.

## DEC-242 — Every clip brings its own ambience and effects (Veo 3.1 lite); the dialogue stays on the pinned voices (phase 7 follow-up, E)
**Context.** The human (2026-10-02): "a lot of video providers provide audio with the videos directly, which is
nice, only pre-prompted also". Their choices: the clip's sound is AMBIENCE + SFX only (each character keeps its
pinned Gemini voice in every shot; a model's invented voice would change from shot to shot); the link is Veo 3.1
lite on `GEMINI_PAID_API_KEY` ($0.05/s at 720p, sound always on, 4/6/8 s clips); the caps follow. Tier 3 so far
(DEC-201) replaced a shot's TTS lines with the clip's sound, shot by shot on an opt-in flag, and the default link
(seedance) makes no sound at all.
**Decision** (`10ee32b`, `a8ccd4d`, `a93192b`, `98f57dd`, `414ae29`).
- **The mode** `tier3_native_audio: "ambience"` (the quality profile; `"opt_in"` keeps DEC-201's replace mode; v1
  and tiers 1–2 untouched) and `video_link_policy: "first_with_audio"`: the first keyed hosted link whose clips
  always have sound (Veo); none keyed → the first keyed link (seedance, silent) with "No ambience: … add
  GEMINI_PAID_API_KEY" in the estimate, never a silent switch; LTX is never picked on its own (its sound is unproven,
  1080p-only at $0.06/s blows the cap) but an episode already recorded on it asks for sound. The preset is tier 3
  now; on an ambience story the per-shot `keep_native_audio` is not read: every line stays TTS.
- **The audio brief** inside the clip prompt's budget (140 words in all; the closing "no music, no voices, nobody
  speaks or sings, no narration" and the "speaks silently" sentence are never cut): the place's sound from its
  look/variant, the shot's SFX cues (`CLIP_SFX_EXCLUDED`: no stingers, whooshes or crowd gasps, which would
  contradict "no voices"); speech in the visual text is made silent (quotes dropped, speech verbs "silently").
- **The mix**: each ambience clip is a stem on the SFX bus (`_ambience_stems` / `_ambience_bus`), not a fourth stem,
  so the three stem files and the Tier-2 ducking check stand; the dialogue stem and the ducked bed are byte-identical
  to tier 2's. `AMBIENCE_GAIN 0.5` (about 8 dB under the lines with the bus's 0.8), its own gentler duck (threshold
  0.06, ratio 3, attack 50 ms, release 600 ms: no pumping on syllables, no bounce in the 0.25 s between lines),
  80 ms fades capped at half the shot. Measured on the test episode: half level 40 ms in, −9.6 dB under a line. A
  clip without a sound track adds nothing and the render log says so.
- **Shot length**: on a fully animated story the beat-shot limit is min(template max, the planned link's longest
  clip) — 8 s on Veo; scenes planned longer are re-planned by the storyboard step (its estimate counts them); over
  the limit a shot gets up to 0.5 s of held last frame, beyond that the estimate refuses the plan (`too_long`) with
  the fix, and the assets step refuses before any keyframe is made. Other stories keep DEC-208's held frame.
- **Caps** 4 / 12 / 40 (the 1:3:10 ratio of DEC-236's 2/6/20; saved Settings still override), the quality profile's
  `cap_usd` 4.0; `preset_estimate` / `new_story_offer` price the preset on Veo when its key is set ($3.52 an episode
  at 8 × 8 s; keyframes on fal) and name both keys.
**Consequence.** Not heard on a live Veo clip yet (A-127): the brief's obedience and the mix constants wait for the
human's walk. An episode near the cap (scenes split into two shots round up more; up to $0.40 of keyframe redraws,
DEC-243) is refused whole when over. A shipped SFX cue still plays at its anchor, so a sound the clip also makes can
be heard twice (follow-up: drop shipped cues on shots whose clip has sound); a scene-start cue goes to the shot that
holds the scene's first line, so a silent establishing shot before it misses it.

## DEC-243 — v2 keyframes consistent by construction; the ones the keyframe check flags are redrawn by the step (phase 7 follow-up, B)
**Context.** The human (2026-10-02): "make it possible to avoid regenerating the shots that have consistency issues,
make it so that it doesn't happen". Four causes in our own code: the character sheets are drawn in the FIRST
wardrobe set while the keyframe text dresses the character in the ledger's set and the role text said "keep the
outfit exactly" (the prompt contradicted its references); the previous keyframe of the scene was never a reference;
J2 compared a shot only to the storyboard's previous shot — across scene changes, never to the sheets, and knew the
characters by their descriptor, not their look; nothing redrew a flagged shot, and a manual regenerate did not re-run
J2 (DEC-239's follow-up).
**Decision** (`ad17e81`, `39bee68`, `cac23f6`).
- **Roles follow the shot's outfit**: when the shot's wardrobe set differs from the set the sheets were drawn in, the
  role text keeps face, hair, build and proportions exactly and names the set worn now; when they match, "outfit
  exactly" stays.
- **A continuity reference with a fixed slot**: the storyboard keeps a placeholder (`continuity/previous_shot`) in the
  reference list, after the sheets and the plate, before the turnarounds and props (dropped first at the cap of 10);
  at request time the previous same-scene keyframe fills it; the prompt hash counts the slot as one constant token
  whatever image fills it, so redrawing a keyframe never makes the next shot stale (no cascade of paid redraws); what
  was sent is recorded as `assets.continuity {shot_id, image_sha256}`, which plays no part in "current"; a previous
  keyframe not yet on disk re-resolves the shot without the slot, its hash unchanged. v1 shots never get the slot.
- **J2 prompt version 2** (`prompts.J2_PROMPT_VERSION`, stored per verdict): up to 4 identity sheets with the two
  keyframes (6 images at most, A-128); each character described by its look and THIS shot's set; told whether the
  previous shot is the same scene — across a change it compares only who the characters are, never set or light.
  Older verdicts still validate but are not current: the next assets run re-checks them (free Gemini vision first)
  and `approve_keyframes` refuses them until it does.
- **Auto-fix** (the human's caps: 2 redraws a shot, $0.40 an episode; `budget_profiles.json` quality
  `keyframe_fix`; profiles without it never redraw): after `judge_keyframes`, every failed current verdict is redrawn
  with a fresh seed and a correction note built from the verdict (J2's names mapped to the prompt's own handles, a
  place to "the set"; `with_note` uses the v2 name list so a descriptive name is not garbled), then re-judged with
  the shot after it; until it passes or its redraws are spent; only while the keyframes are not approved (an
  approval, "anyway" included, is never redrawn over); locked shots and shots drawn from a person's note are skipped;
  the paid gates, the cancel and the time budget stop it. The redraws and the spend live in the episode's own
  `assets.json` (`keyframe_fixes`, `keyframe_fix_budget`); per-episode spend is read through `ledger.totals(ep)`
  (DEC-241). The estimate counts the ceiling (what is left of the $0.40, at most every shot twice; 0 once approved)
  in the plan total and the cap check and takes it off before the clips are planned.
- **A manual shot-image regenerate runs J2** on that shot and the one after it; it never auto-fixes (the person's
  note is the correction).
- **The contract for the review screen**: per shot `keyframe_verdict {passed, current, shows_beat, missing,
  continuity_issue, checked_at}` and `keyframe_fix`; the episode's `keyframes.fix_budget`; the step result's
  `keyframes.fix` and the log line "🛠 Keyframe auto-fix: …".
**Consequence.** Shots resolved before this change have no continuity slot until their prompts are refreshed, which
stales every shot after the first of its scene once. A tight episode cap can refuse a plan whose images alone would
fit (the fix ceiling is reserved). Known and left: the framing-edit re-resolve still drops T1 v2 `staging` and
`clip_motion`; `render_look` appends signature items that are not worn, so a clothing signature item can still
contradict another set.

## DEC-244 — Gemini voice lines lose the burst of static after their last word; every line fades at its edges (phase 7 follow-up, A)
**Context.** The human (2026-10-02): "the sounds from Gemini are the best for now, but at the end we hear a big
'crshhhh' at the end of each vocal". A known Gemini TTS fault (Google's forum: "static noise/artifact at the end of
TTS generation", 2.5 flash/pro and 3.1 flash): a burst of broadband noise after the last word. The adapter wrote the
PCM as it came (`tts.py`) and the mix placed each line's whole file with `adelay`, no trim, no fade. No real sample
reached this session: the detector was designed from the description and proven on synthetic signals (A-129).
**Decision** (`8a6a87e`, `c935906`, `46f01bc`, `78380c9`).
- **`clipping/providers/tts_tail.py`** (stdlib: `array`, `math`, `wave`; `TAIL_GUARD_VERSION = 1`): 10 ms frames,
  per-frame RMS and zero-crossing rate; the floor is the 10th percentile of frame levels, the speech level the 95th
  percentile of the voiced-like (low-ZCR) frames; a frame is audible above floor + 10 dB (held between 40 and 30 dB
  under the speech level, the 30 dB bound catching a burst 25 dB under speech in a line with no real silence),
  noise-like at ZCR ≥ 0.25 (white noise ~0.5, voiced speech < 0.1), voiced only in runs of ≥ 3 frames. Cut rules:
  noise glued to the speech for ≥ 250 ms is cut 120 ms after it starts (a final "s"/"ch" survives); noise after a
  gap ≥ 60 ms is cut at most 50 ms into the gap, only when the noise runs ≥ 250 ms or the gap is ≥ 150 ms ("fax",
  "texts" stay whole); never more than 1.5 s, never under 50 % of the line kept — else "suspect", nothing cut; pure
  trailing silence is never cut (pacing unchanged); 5 ms in / 25 ms out fades in the file. An aligned last word's end
  is used only where the detector found nothing (cut at word end + 150 ms when something audible follows): STT ends
  stretch into trailing noise, so they never move a cut the detector made.
- **The Gemini adapter** cleans the PCM before writing; the duration follows; the `line_timing_v1` sidecar and the
  `GenResult.meta` carry `tail_guard` (the report). Edge and the local engines are not trimmed.
- **Lines recorded before the fix** are cleaned in place, for free, on the next assets run (a Gemini sidecar without
  `tail_guard` or of an older version): the file is rewritten atomically, the sidecar and the measured timing follow
  as after any measurement (the storyboard re-timed, no approval cleared by the step itself); the generation cache
  keeps Gemini's answers as sent and every copy is cleaned as it lands in `assets/voice/`, so a cache hit cannot
  bring the noise back; a voice-regenerate take follows the cleaned file's sha256. The assets summary gets a `tails`
  entry only when a Gemini line was seen (Edge-only runs keep their pinned summary hash); the log says "N Gemini line
  endings cleaned (x s of static cut)" and names suspect lines.
- **The mix** (every story, v1 included — a bug fix): each dialogue line fades 5 ms in and 10 ms out at its own
  file's edges (`afade`, `areverse` pair) before its `adelay`, tier-3 path included, so no line can click; the
  tier-1 RC-M3 render guard's plan and manifest hashes were re-pinned after proving that removing the fades restores
  the old hashes exactly.
- **`python main.py --ai-story voice-tails STORY_ID --ep N`** (read only): per line the length, the file, what was
  cut or would be cut and why, and the summary "N Gemini lines: n cleaned, m to clean (x s of static)".
**Consequence.** The thresholds wait for the human's real lines (A-129): `voice-tails` before and after an assets
run is the check; darker, pinkish noise (ZCR under ~0.23) would not be detected. After an in-place clean the assets
approval is stale (the audio hashes changed), as after any measurement: approve again. Every story's mix render cache
key changes once (the fades), so each re-render re-mixes once.

## DEC-245 — The script step repairs what the first-watch check finds; the v2 writers hear the rules first (phase 7 follow-up, G)
**Context.** The human (2026-10-03) generated an episode and got J1's six issues ("s01 (unclear goal): …; s04
(unintroduced): Présenter Kevin avant qu'il n'entre…; s05 (object unseen): Montrer les boutons de manchette… ;
s06 (repeated line) …") with "Fix them (edit the script, or regenerate the scenes they name) … or approve anyway" —
"the purpose is that the generated content is always perfect". The step knew the scene and the fix of each issue
and applied none; two of them (a character introduced too late, an object shown too late) need an EARLIER scene.
**Decision** (`6997fd7`, `47197a0`, `f64b3ff`).
- **The repair pass** (`script.repair_plan`, pure; `_Run.repair` after J1; v2 only; never on an approved script):
  `repeated_line` → the later line's scene with a note for a different line that keeps the beat; `no_hook_text` →
  `write_framing("hook")`; `unclear_goal`, `unmotivated` → the named scene; `unintroduced` / `object_unseen` → the
  named scene AND the nearest earlier body scene where the character / prop the fix names (whole-word, the scene's
  own cast/props first, then the story's) is present, with "X is in this scene, before sNN: say their name and who
  they are here / show it on screen here"; a fix that names nothing matchable → the named scene alone (a wrong guess
  would cost a call); a framing scene → its partial E3; a scene nobody can speak in, an unknown id or a null
  `scene_id` → left in the report. Grouped per scene (one call per scene per pass, all its notes), in scene order,
  `REPAIR_CALLS_MAX = 4` a pass, `REPAIR_PASSES_MAX = 2` a run (the free writing chain: the cost is time, checked by
  `before_call`); after a pass `fill()` (its own rule), E4 and J1 again; the loop ends on a pass, nothing repaired,
  the passes spent or the budget. Notes read "First-watch check -- Unclear goal: <fix verbatim>" (J1 writes the fix
  in the story language) through the pack's 60-word note cap; the regenerate's own path is reused, so
  `target_duration_s` and the slot rules do not move.
- **The record**: `script["repairs"]` (optional schema key; replaced by the next run that repairs, kept as history
  otherwise), `summary["repairs"]`, "🩹 Repair pass 1: N scenes rewritten for M issues (…)", "👀 First watch after
  repair: passed | N issues remain"; the approve-script and fast-track refusals say "after N repair passes, N issues
  remain" (`judge.issues_sentence`, every pass that ran counted) and end each fix once (the "urgent.." double period
  is gone).
- **Prevention**: E1v2 and E2v2 open with the first-watch rules (`prompts.FIRST_WATCH_RULES`: state what each main
  character wants in their first scene; give every action a reason the viewer saw; name a character before they act
  or speak; show an object before the story turns on it; never repeat or paraphrase a line; the hook's on-screen text
  states the premise); v1 prompts byte-identical (their sha pins hold); E1v2/E2v2 input budgets re-pinned
  2870 → 2970 / 2420 → 2520 on measured 2576 / 2187.
**Consequence.** A fast-track or one-click episode gets the repairs for free (same step). Not shown on the episode
page yet (`script.repairs` is available to it). A second `fill()` inside the loop overwrites `summary["fill"]` with
the latest record. The repair pass runs even when E4 failed earlier in the run and then re-asks E4 (the fill pass's
exposure). Three `test_story_judge` fixtures now queue the repair's replies, since the step repairs before refusing.

## DEC-246 — One click makes the whole episode up to the finished render; one Review tab to check and approve it (phase 7 follow-up, C)
**Context.** The human (2026-10-02): "I generate directly the whole thing at once, ready to read and approve"; their
choice: the one click goes up to the finished render, no stop at the keyframes (stage B makes them consistent and
redraws the flagged ones). DEC-239's fast track stopped at the keyframes on a v2 story and ended the job *failed*;
the Keyframes card was a text list with no thumbnail and no regenerate.
**Decision** (`96dbcb1`, `fea1e7c`).
- **The fast track goes to the render** on a v2 story at tier ≥ 2: after the assets run that makes, checks (J2) and
  auto-fixes the keyframes it records the keyframe approval itself (`keyframes_approved.by: "fast_track"`, `anyway`
  only when shots are still flagged, named in `flagged`) — the human's "Generate episode" click is the consent and
  the confirm says so — then runs the assets step again for the held clips, approves the assets (`by: "fast_track"`),
  renders and writes the metadata; the last feed line says "ready for review". `stop_at_keyframes` (API; CLI
  `--stop-at-keyframes`) keeps DEC-230's stop. Approval provenance (`by`) is written for keyframes and assets whoever
  approves (v1 documents gain only the optional key), so the review can say what was auto-approved.
- **The whole episode is checked against the caps before any call** (`whole_episode_units`: the keyframe hold is
  lifted for the check so clips + the fix ceiling + voices + images are refused whole, never half-bought).
- **The time budget follows the plan** (`budget_seconds`: 3600 + clips × 600 + J2 120 a shot + redraws × 540, ceiling
  4 h — A-130), derived at the start and again exactly after the paid check, announced in the feed; legacy stays at
  3600; the predictive checks still stop early and Continue resumes.
- **`GET /episodes/{ep}` gains `review`** (`workflow.episode_review`, pure over the page already computed): per shot
  the keyframe, the clip, the verdict state, the fix history, the lines; per episode the status and headline, what
  was auto-approved, what is pending (in order), flagged / unchecked / fixed, the spend by kind, the render state,
  `script_repairs` (DEC-245).
- **The dashboard**: "Generate episode" (the Fast track button) with an explicit confirm (what runs, the caps, "no
  stop for keyframe review — you review the finished episode") and a "stop at the keyframes" checkbox (off); the
  running job shows its sub-step and a progress bar and the page switches to Review when it ends. The **Review tab**
  (v2 episodes; added last so the first three tabs keep their contract): a 3-column tile grid at 375 px — keyframe,
  verdict chip (passed / fixed after N / still flagged: issue / not current), the shot's line — an overlay with the
  clip and both regenerate controls, the episode's status and spend above, and one "Approve keyframes and assets"
  action ("Approve anyway" after a refusal). The Keyframes card is slimmed and points to Review.
**Consequence.** A story that opts for the stop keeps the old flow. The spend split of the review relies on the
ledger's `unit` values (image / char / second), the fixes taken off the images by `keyframe_fix_budget.spent_usd`.
Playwright at 375 and 1280 px: no horizontal scroll, no page error (`scratchpad/p8c/c-*.png`).

## DEC-247 — Each v2 prompt fills its own link's budget with the story's richest context, ordered by value (phase 7 follow-up, F2)
**Context.** The human (2026-10-02): "More prompt context is more accuracy and details that make the story good"
and "some providers have limited prompt size so check that also". The v2 builders used one fixed budget for every
link (keyframe 220 words, clip 80; sheets/plates/props 130/150/80) and the keyframe ladder sent its last rung over
budget anyway; the keyframe never carried the beat's mood, the character's bearing, what changed since the previous
shot or the time of day, and the clip never the emotion, micro-actions or the camera's intent.
**Decision** (`40d101d`, `07bde58`, `1dfe8ed`, `b98a760`).
- **Per-link budgets** (`clipping/aistory/prompt_budgets.py`): `<kind>_words(link)` = `prompt_limits.budget_words`
  (DEC-240) bounded by a quality ceiling — keyframe 320 (≈ FLUX's 512-token T5 window, the widest text window known
  whole; A-131), clip 160, ambience clip 160 + the brief's 60-word share (`CLIP_AUDIO_SHARE_WORDS`, DEC-242's brief
  takes its share instead of a fixed 140), sheet 200, plate 220, prop 120; no link → today's numbers
  (220/80/140/130/150/80). Every quality-preset link accepts more than its ceiling, so there the ceiling is the
  budget; a live fal limit under it wins.
- **The link is known at build time**: the storyboard step builds every shot to the episode's planned links
  (`clips.episode_budgets`: the recorded sticky image link, else the keyframe role chain's head; `clips.planned_link`
  for the clip) and fails, writing nothing, when a shot cannot fit even the last rung (`shots.PromptOverBudget`,
  naming the shot and the link — no more "sent anyway"); workflow edits and `refresh_prompts` do the same. At request
  time `assets.request_parts` / `clips.clip_request_parts(link=)` return `over` (over the link's limit by
  `prompt_limits.check`, or built for another link's budget) and `make_image` / `make_clip` refuse that shot with
  "refresh the prompts"; the prompt hash never moves with it; F1's dispatch check is the backstop. Check-and-refuse
  rather than an automatic re-fit: the storyboard and assets steps derive the same link, so this fires only after
  a link switch or a live-limit change.
- **Keyframe layers** (`shots._layered`; dropped least valuable first — between, when, bearing, since, mood — after
  the rendering cut and before any look or place shortening, so today's 220-word fixtures stay byte-identical):
  **mood** (the scene's function and emotion; the first framed speaker "mid-sentence, asking a question /
  exclaiming / speaking" — never the words, which could be drawn as lettering; the listeners; the ledger's
  injuries); **between** (the dossier relationship's `now`); **since** (positions moved, who or what came into
  frame, the previous action's gist — `resolve_shot(previous_plan=)` threaded through build, refresh, the workflow
  and the assets re-resolution); **bearing** (`look.bearing`); **when** (the variant as time of day; light
  direction is stored nowhere, so not said). Knowledge facts beyond the relationship's `now` are not drawable and
  stay out.
- **Clip layers**: the emotion, micro-actions (breathing, the staged glance, hands), the camera's intent; dropped
  intent → micro → emotion before the motion is cut.
- **Sheets, plates, props**: `shots.visual_cues` = the descriptor's clauses holding a mark word not already said by
  the look or the items, plus the bearing (order: head / look / tail > cues > rendering ≥ 8 words > rules); plates
  gain the style's `environment_rules` as one sentence when room allows. **`look.bearing`** (optional, ≤ 10 words;
  D2 asks for it as it asks for `presentation`, A3's pattern): the dossier is story-language prose for the writers
  with no visual field, the look is the English the image prompts render from — so the bearing lives there. D2
  re-measured 2096 tokens, `INPUT_BUDGET["D2"]` 2370 → 2420. The Cast step's look editor gains the field.
**Measured** (the 9-shot v2 fixture and the crowded 3-character shot): keyframes on seedream-4.5-edit /
nano-banana / gpt-image at 320: max 312 words (crowded), mean 221.5, max 1875 characters, all fit (mood and since
kept on the fixture; a two-character shot with 5–6 references runs ~300 words of core, so ~20 words of context fit
at 320 — all five layers would need ~590); clips at 160: max 124 words, mean 94.7, 797 characters; ambience at 220:
max 170 words, 1095 characters; micro-actions in 9 clips of 10, intent in 10; sheets at 200: max 196 words (cues on
3 of 3), plate 181, prop 84.
**Consequence.** An ambience story's stored clip prompts now hash with the link's budget (220 on Veo vs 140): a
long-visual clip already bought at 140 reads stale and is re-bought; short visuals (the norm) are unchanged;
existing keyframes and clips are untouched until their prompts are refreshed. Follow-ups: drop a look's palette or
hair (shown by the sheets) before the mood rather than after, since the core eats most of the 320; an automatic
re-fit after a link switch (today the refusal names the remedy); the dashboard's prompt-target chips still say
220/80 (a per-link target needs the API to expose the budgets); workflow edits compute budgets from the process
env alone (harmless while every hosted link's budget is its ceiling).

## DEC-248 — J1 marks each issue blocking or minor and passes when nothing blocks; the re-check after a repair converges; the fast track approves over minor issues (after the phase 7 follow-up wave)
**Context.** The human (2026-10-03), after deploying the wave: "the fast track does not work" — "Fast track stopped at
the script (step 1 of 6): Episode 1's first-watch check (J1): after 2 repair passes, 6 issues remain: s01
(unclear_goal): Montrer ou dire ce qu'est le CŒUR…; s02 (unmotivated) …; s04 (unintroduced): Présenter Kevin…; s05
(object_unseen): Montrer les boutons de manchette…; s06 (unclear_goal) …; s08 (unintroduced): Introduire Madame
Pamplemousse … avant le cliffhanger". Root causes found in the code: (1) J1 v1 failed on ANY issue and, asked for
"at most 6", returned exactly 6 in both of the human's runs (DEC-245's and this one) — no severity, so a nitpick
failed the script like a broken plot; (2) J1 did not know the format: s01 is the 3–6 s hook (5–10 words) asked to
explain the premise, s08 is the cliffhanger whose reveal of a new character was called "unintroduced"; (3)
`REPAIR_CALLS_MAX = 4` in scene order never reached s05, s06 and s08 (the plan was 6–7 scenes) — exactly the scenes
still named, s05's issue already in DEC-245's run; (4) J1 reads each scene's summary and an objects list built from
the scenes' `props`, which a line rewrite never changes; (5) the J1 after a pass was a fresh critique with no memory
of what it had asked, so the loop had no reason to converge; (6) a note over the pack's 60 words was cut at its end,
losing the earlier-scene ask. The human's answers: approve over minor issues and record them; never add a minor or
temporary object to the story's prop library; a new branch.
**Decision** (branch `claude/fix-fast-track-j1`).
- **J1 version 2** (`prompts.J1_PROMPT_VERSION = 2`): the prompt opens with the format (the episode's estimated
  seconds and spoken words; a scene's first line is what is on screen; the hook's tease, the cliffhanger's reveal —
  someone first seen there is its point — and a secret kept for later are never issues; a detail the format has no
  room for is minor at most); each issue has `severity` `blocking` (a first-time viewer cannot follow who the main
  character is, what they want, what happens or why it matters) or `minor`; "none is a fine answer"; the issues come
  before `passed` in the schema; `validate_j1` requires `passed` == no blocking issue. The deterministic checks'
  issues (repeated line, no hook text) are blocking. The report gains `version` and each issue `severity` (optional
  schema keys: an issue without one reads as blocking, a version-1 report keeps its meaning). **The report passes
  when no issue is blocking** (`judge.blocking_issues`).
- **Older reports**: `needs_first_watch` is true for a report of an older J1 on a script **not approved yet** — the
  human's stuck episode is judged again by "Generate episode"/Continue with no edit; an approved script keeps its
  report (RC-M3).
- **The re-check**: after a repair pass, J1 is shown the blocking issues the pass tried (J1's own kinds, at most 6,
  each fix cut to `J1_RECHECK_FIX_MAX_WORDS = 5` words) and asked to keep blocking only those still there; the judge
  enforces it — a blocking issue of the re-check that is not one of them (same scene and kind) is kept as **minor**.
  The blocking set can only shrink, so the loop converges; the deterministic checks still run on every call.
- **The repair pass**: only the blocking issues are repaired (the minor ones never are); `REPAIR_CALLS_MAX` 4 → 8;
  each note is fitted to the pack's 60-word cap by shortening J1's fixes evenly (a short fix whole), never the app's
  asks (`script._fitted_note`); an `object_unseen` whose fix names one of the story's props that the scene does not
  list gets it listed on the scene before the rewrite (within the schema's 4 props; unlisted again when the call
  fails), recorded as `props_added` (optional key) and logged "🩹 Scene sNN now shows …" — an object that is not one
  of the story's props is never added to the library (the human's answer): the rewrite shows it in the lines. The
  repair record's `issues_before`/`issues_after` count the blocking issues; the logs say "N blocking issues".
- **The approvals**: `approve_script` refuses only blocking issues (minor alone is no "anyway"); the refusals list
  the blocking issues and count the minor ones ("(1 minor issue kept for review)"); a version-1 report still says
  "issues". The fast track approves a script whose J1 found only minor issues, its feed line naming each
  (`fast_track.script_detail`); "The fast track never approves over blocking issues".
- **The Review tab**: `episode_review` gains `script_minor_issues`, shown under the repairs line.
- **Budgets** (re-measured on the French worst case, the re-check at its bound): J1 input 3195 → 3463,
  `INPUT_BUDGET["J1"]` 3680 → 3990 (under the spec's 4000 — the wording was tightened to fit); reply 795.6 → 842.4,
  `MAX_TOKENS["J1"]` 920 → 970.
- **`tools/j1_calibrate.py`**: J1 version 2 on a copy of a written episode N times (the step's own call, keys and
  gates, `--settings` for the stored Settings), printing each verdict and a summary — read-only but for what a paid
  link books in the ledger. For the human to run where the keys and the story live (A-132).
**Consequence.** A v2 script can now be approved — by the fast track too — over issues the judge calls minor; they
stay on the report and the Review tab, never repaired. A new blocking issue a repair introduces is kept as minor by
the re-check (a repeated line or a missing hook text is still caught by the deterministic checks, and E4 runs again).
Every v2 script not approved yet is judged once more on its next script run (one free J1 call). Follow-ups: the
episode page's Script tab does not list the minor issues yet (the Review tab does); `script.repairs` from an earlier
run is kept when a re-judged report passes without a pass (history, as DEC-245 says).

## DEC-249 — A prompt over its link's budget at request time is resolved again to the room left, the note kept whole; refused only when no rung can make room (after DEC-248)
**Context.** The human (2026-10-03), on the fresh v2 episode d0ee5ebd745d/ep01 right after DEC-248: "Fast track stopped
at the assets (step 4 of 6): … shot sh03's keyframe prompt (326 words) is over fal/seedream-4.5-edit's budget of 320
words -- built for another link, or fal/seedream-4.5-edit's limit moved since". Verified on disk: sh03's stored prompt
is 308 words, built to the right link; the keyframe auto-fix (DEC-243) redrew it with an 18-word correction note
that `assets.with_note` appends at send time, after the prompt was fitted to its budget and counted against none
(up to `REGENERATE_NOTE_MAX` 300 characters, ~46 words); `prompt_budgets.over_sentence` then refused the shot and
the fast track stopped, naming two causes that were both wrong. `video_plan.build_video_prompt` appends a
re-animate's note to a stored clip prompt the same way (the ambience prompt alone fits its note itself, DEC-242).
DEC-247 chose check-and-refuse over an automatic re-fit for the link-switch case and left the re-fit as a follow-up.
**Decision.**
- **`shots.resolve_stored`**: a stored v2 shot resolved again as `refresh_prompts` resolves it (its scene from the
  script, its plan from itself, the plan before it from the storyboard's order, the continuity slot as stored unless
  told), to given budgets; `PromptOverBudget` named with the shot and the link.
- **At request time** (`assets.request_parts` → `_fitted`/`fit_to_budget`; `clips.clip_request_parts` →
  `_fitted_clip`): a layered prompt that is over its link's budget — with its note, or built to another link's
  budget, or over the link's character limit — is resolved again to the budget minus the note's words, so the
  ladder's context layers (DEC-247's order) make room and the note is sent whole; the fitted prompt is what is sent,
  `refit` says from and to how many words, and `make_image`/`make_clip` write a feed line ("ℹ️ Shot sh03's keyframe
  prompt: its note (18 words) takes it to 326 words, over fal/seedream-4.5-edit's budget of 320; resolved again to
  3xx words -- its context shortened, nothing of the note cut."). A user's `prompt_override` is sent as written.
- **The hash never moves with it**: `prompt_hash` stays over the stored prompt with its note (as the continuity slot's
  rule already had it: what filled the request never moves the hash), so a keyframe or clip made before reads
  current and `shot_state`/`clip_state` agree with the step without reading the script.
- **Refused only when even the ladder's last rung is over with the note**: `prompt_budgets.note_over_sentence`
  names the note's words, how long a note fits this shot on this link, and what to do (shorten the note or ask
  without one); a prompt over without any note keeps DEC-247's sentence. Nothing is sent either way.
- **Tests**: `tests/test_story_prompt_note_fit.py` (the auto-fix end to end at a budget with no word to spare, the
  request parts for keyframes and clips with and without room, the alone path, the refusal sentence, the legacy
  guard, `resolve_stored` byte-identical on the storyboard's budgets); DEC-247's pin "a stored prompt over the
  budget is refused" re-pinned on purpose to "resolved again to this link's budget, hash unchanged".
**Rejected.** Cutting the note to the room left (the correction is the point of the redraw; the lowest context
layer is worth less than it); storing a re-fitted `image_prompt` on the storyboard (the note is per attempt, the
storyboard is the script's); falling through to the chain's next link (A-087: the episode's link alone).
**Consequence.** The fast track no longer stops at the assets over a correction note; a stored prompt built to a
roomier link is sent fitted rather than refused (DEC-247's follow-up closed). The fitted prompt depends on the
entities, ledger and script at send time while the hash does not — the same standing as the continuity slot's;
`require_approved` already refuses outdated entities. The refit reads the script and storyboard once per over-budget
request only. Follow-up: the Keyframes card could show the fitted prompt a redraw was sent with (today the feed line
says the numbers).

## DEC-250 — A shot longer than the longest clip its link sells is covered: planned on the voices' real length, else its clip slowed to the shot (at most 1.25x); refused only past that (after DEC-249)
**Context.** The human (2026-10-03), the same episode d0ee5ebd745d/ep01, next stop: "Episode 1's clips cannot be made:
shots sh04 (12.767 s) and sh03 (14.133 s) run longer than the 12 s clip fal/seedance-1-pro-fast sells, and every shot
of this story is one clip: plan the storyboard again …". Verified on disk: the storyboard planned one beat shot a
scene from the script's estimated timing (`timing.estimate_line`, a French rate measured on Edge voices); the assets
step then made the Gemini voices and re-timed the shots from them (`voice_lines.sync_storyboard` →
`shots.retime_storyboard`, which redistributes within the planned shots and never splits one); the measured speech ran
1.16–1.80x the estimate (mean 1.35 over 18 lines), three scenes went `over`, and the fully animated plan refused past
DEC-208's 0.5 s held frame. The human's only remedy was to plan the storyboard again by hand (new T1 calls, the scene's
keyframes re-bought) and run the assets step again — after every one-click run.
**Decision.**
- **Plan time** (`storyboard.expected_scene_seconds`): a scene's length as its voices will measure it — the
  script's timing plus, for each line not measured yet, its estimate times the speaker's TTS provider's overrun
  (`voices.SPEECH_OVERRUN`: gemini 1.35, A-134; a provider not named 1.0; a measured line adds nothing).
  `beat_shot_count` reads it, so T1 v2 is asked two beat shots for a scene the estimate puts under the clip's length
  but the voices past it; `short_of_beats` and the next storyboard run follow the same number.
- **Run time** (`clips.stretch_of`, `MAX_STRETCH` 1.25): on a fully animated story, a shot longer than the link's
  longest clip by more than `HOLD_TOLERANCE_S` is covered by that clip slowed to the shot's length when the factor
  is at most 1.25 — the plan row says `cover: stretch` and the factor, the estimate's message and the step's feed say
  "shX runs 14.133 s: its 12 s clip is slowed to cover it (0.85x speed)", `make_clip` records `cover` on the clip
  (`assets.clip.cover`, an optional key: every stored story validates unchanged, RC-M3), and the render
  (`render/plan.py` → `filtergraph.tier2_clip_argv(clip_s=)`) adds `setpts=<duration/clip_s>*PTS` before the fps
  resample; the hold and the trim after it still pin the exact frames. A clip at least as long as its shot, a record
  without `cover`, a legacy story: the argv it always was (the tier-2 goldens hold). The factor is computed from the
  shot's current duration at render, so a later re-time needs no new clip.
- **Refused only past 1.25x** (`_too_long`): the sentence names the shot, the most a slowed clip covers ("even slowed
  (at most 15 s)"), and both remedies (plan the scene as two shots; shorten its lines).
- **Tests**: `tests/test_story_long_shots.py` (the live numbers on seedance, the refusal past 15 s, the key-shots
  guard, `stretch_of`, the expected length with Gemini voices and measured lines, the storyboard step asking two
  shots, the argv golden with and without a stretch, a real-ffmpeg check that a slowed clip keeps moving to the last
  frame where a held one freezes, the clip schema, and the one click end to end: stop at the keyframes, the voices
  re-time the shots, the sold lengths narrowed under them, Continue buys the slowed clips, records and renders them,
  a second Continue repeats nothing). `test_story_ambience`'s refusal pin re-pinned on purpose: 9.4 s on Veo (8 s)
  is now covered (1.175x), the refusal needs 10.5 s.
**Rejected.** Holding the frozen last frame longer (a fully animated story shows no still, DEC-236); cutting or
speeding the voices (quality); buying a second clip for the remainder from the clip's last frame (a seam, a second
request, continuity risk — left as the follow-up for shots past 1.25x); re-planning and re-buying the scene's
keyframes inside the assets step (bypasses the storyboard approval, costs images); recalibrating
`timing.estimate_line` itself for Gemini voices (it drives the 55–75 s length gate and every window pass — a
cross-cutting change with its own task; the overrun table is read by the beat-shot rule alone until then).
**Consequence.** The one click no longer stops at the clips over a voice overrun: a scene the voices will carry past
the clip is two beat shots from the start, and a shot still over is covered by its clip at 0.80–0.99x speed (A-135:
whether that reads well on the phone is the human's verdict). Storyboards planned before this change keep their
shots until planned again. Follow-ups: the speech-rate estimate itself (`RATE_PER_CHAR`) should learn from a
story's measured lines (the length gate writes to an estimate the voices beat by a third); a shot past 1.25x could
take a second clip from the first's last frame; `test_story_timing`'s "a v2 scene plus hold never exceeds 12 s"
carve-out for `over` scenes now rests on the stretch, said in its docstring only.

## DEC-251 — The Gemini tail guard, version 2: the real burst is louder than the speech, after a gap, to the file's end (after DEC-250)
**Context.** The human (2026-10-03), on the deployed episode: "À la fin de chaque réplique il y a encore le crshhhh des
voix" — though the feed had said "18 Gemini line endings checked: no static to cut". DEC-244's guard was written with
no real sample ("built on what that noise is: louder than the floor, noise-like, at the end"). Read from the 18 live
lines (all identical): 60–250 ms of near-silence (−58 to −100 dBFS) after the last word, then ~120 ms at −6 dBFS RMS,
clipping on its first sample, 2–8 dB LOUDER than the speech itself, zero-crossing rate 0.09–0.35 — a buzz, not white
noise — running to the very last sample. Version 1 measured the speech level over all low-ZCR frames, so the burst set
the level it was judged against, read as a loud voiced syllable, became "the end of speech", and nothing followed it.
**Decision.** `tts_tail.analyse` first looks for an end burst (`_end_burst`): the final active stretch reaches the
last frame, lasts at most `BURST_MAX_S` 0.20 s, follows a near-silent gap of at least `GAP_MIN_S`, and its median level
is within `BURST_UNDER_SPEECH_DB` 3 dB of (or above) the speech level measured on the frames BEFORE the last 0.20 s —
then reason `burst_at_end`, cut in the gap (`GAP_KEEP_S` after it starts), the old limits (`MAX_CUT_S`,
`MIN_KEEP_RATIO`) kept. Speech never ends that way (every TTS leaves silence after the last word; a shouted last word
is followed by it too), and a last word at the line's own level is not louder than its syllables. `TAIL_GUARD_VERSION`
1 → 2, so `tts.tail_guard_due` re-cleans every line cleaned by version 1 on its next assets run, for free (the
line's duration shrinks 0.26–0.47 s, the script and storyboard re-time, the render is no longer current and the fast
track renders again). Tests: `tests/test_tts_tail.py` (the live pattern as a buzz and as short static after 80–250 ms
gaps, why version 1 missed it, a shouted last word followed by silence kept, a last word at the speech level cut at
the file's end kept, a long static left to the gap rule); the version pins moved to 2.
**Rejected.** Cutting a fixed 150 ms off every Gemini line (the gap varies 60–250 ms and a future fix on Google's side
would then eat a word's end); raising `NOISE_ZCR` (the burst's ZCR overlaps a vowel's at 0.09–0.17).
**Consequence.** All 18 lines of d0ee5ebd745d/ep01 read `burst_at_end` under version 2 (checked offline on the files).
The threshold rests on one episode's sample (A-136); `voice-tails` shows what version 2 cuts before anything is made
again.

## DEC-252 — v2 clips ask for a performance, never stillness; T1 v2 varies the camera; a body scene is two beat shots (after DEC-251)
**Context.** The human (2026-10-03), on the first fully animated v2 episode (one seedance clip per shot, the TTS voices
over it): "no lipsync, the video does not say things or do movements, boring, no rhythm". The clip prompt asked for
idleness, and the model obeyed: `shots._layered` wrote "X reacts with a small natural movement" (or "small natural
idle movements in between") and "Micro-actions: X breathes visibly, a glance toward Y, hands shift slightly" for every
framed character; `prompting.layered_clip_prompt` closed on `STAYS_STILL` ("... stay exactly as in the first frame")
and the style's `tier2_prompt_suffix` (fruit_drama: "subtle natural head and shoulder movement ... camera slowly pushes
in"). T1 v2 asked a 25-word `motion` and a free camera, refused only a repeated framing, and `rule_pass` then made every
hook, peak and cliffhanger of fruit_drama a push-in (`by_function`). `beat_shot_count` gave a scene one beat shot unless
it ran past the clip (DEC-227, DEC-250), so every scene was one long clip.
**Decision.** (v2 stories only; every legacy prompt and its clip hash byte-identical, RC-Q1.)
- **The clip says a performance** (`shots._performance`): whoever speaks one of the shot's lines in frame "speaks with
  the mouth moving on the words, face and brows carrying the emotion" (two speakers: "speak in turn"); the others
  "react visibly, <reaction>" from `_REACTIONS` by the line's emotion, else the scene's, else `_FUNCTION_REACTIONS` by
  its function (shocked: stepping back, eyes widening; tension: leaning in, jaw set; scheming: narrowing the eyes, a
  slow smile; ...); with no line, everyone in frame "acts the moment out with clear gestures". The words are never in
  it (DEC-201: the TTS is the voice; quoted words would be drawn). `_gestures` replaces the micro-actions: a staged
  character "turns toward <facing>, face <expression>", a held prop is in its holder's hands; nothing staged, nothing
  invented. `_CLIP_DROP_ORDER` is intent, gestures, emotion, performance -- the performance outlives the rest; only
  then is the motion cut.
- **Identity, not stillness**: `prompting.IDENTITY_KEEPS` ("Keep every character's look, the set and the light as in
  the first frame; the characters move freely within it.") closes a v2 clip; `STAYS_STILL` and `MOTION_NEGATIVE` stay.
- **A v2 suffix per style**: `motion_rules.tier2_prompt_suffix_v2` in all seven styles (no "subtle", no "gentle", no
  camera: the camera sentence says it) and optional in both schemas; `prompting.clip_motion_suffix` reads it, else
  the old key. `tier2_prompt_suffix` is untouched (v1 reads it). A story locked before the key existed takes its
  shipped template's in memory (`episode_common.with_clip_suffix`, `style_lock.json` never written), so a re-plan of
  an existing story ends on the lively suffix too. Template versions are not bumped (a bump would refuse every
  draft's style overrides, `stylelock.apply_overrides`).
- **T1 v2**: `motion` asks one clear physical action per framed character and the speaker's mouth and face, never
  "subtle"/"small"/"slight" (asked, not validated); `camera_motion` never the previous shot's. `validate_t1_v2` refuses
  a repeat (inside the scene, and the first shot against `previous_camera`, the episode's shot before it), but
  `storyboard._repair_t1_v2_reply` first moves a repeat to the next motion of `_CAMERA_ROTATION` that neither neighbour
  has and logs it -- a repair costs nothing, a retry a call. T1r v2 (an author's re-plan of one shot) is asked, not
  refused: the note may want that very motion. On v2, `shots.motion_for(v2=True)` puts the shot's own camera before the
  scene function's rule (a framing's own rule, the wide shot's pan, still wins), in `rule_pass`, `build_storyboard` and
  the shot edit. Budgets re-measured: T1v2 1,865 -> 2,150, T1rv2 1,852 -> 2,130.
- **Rhythm** (`storyboard._two_beats`): a body scene with two lines or two characters and at least 2 x `min_shot_s` is
  two beat shots; the recap, the hook and the cliffhanger keep the clip-length rule. `short_of_beats` reads
  `beat_shot_count(rhythm=False)`, so an existing storyboard is never planned again for the new default: only a fresh
  plan, or a scene planned again (stale, fast, re-planned), takes it. `serial_60s_v2.shots` 6-10 -> 6-18 (7 body scenes
  x 2, the recap, the hook, a cliffhanger past Veo's 8 s; the brief's 16 undercounted) and its notes.
**Rejected.** Editing `tier2_prompt_suffix` (moves every v1 prompt and stored clip hash); keeping the performance as a
fixed, never-dropped sentence (at 80 words, no link known, it pushes the motion out); refusing a repeated camera
without a repair (a paid retry for a one-word fix); re-planning every one-beat scene of existing storyboards (re-buys
keyframes the human approved); quoting the lines for lipsync (DEC-201; lipsync is another route).
**Consequence.** A v2 clip names who talks and who reacts and lets them move; an episode runs up to 17-18 shorter shots instead of at most 10.
Any v2 shot resolved again (a re-plan, an entity refresh) gets the new wording, so its stored clip goes stale and is
made again on the next run. Tests: `tests/test_story_clip_performance.py` (13, each failing on the parent); re-pinned on
purpose: `test_story_prompt_layers` (two clip tests), `test_story_shots` and `test_story_video_plan` (the closing
clause and suffix), `test_story_prompts_episode` (the largest reply's second camera), `test_story_episode_prompt_budgets`
(the measured budgets), `test_story_timing` (the shots range), `test_story_storyboard_props`, `test_story_ambience`,
`test_story_long_shots` (the beat counts, via `test_story_storyboard_props.two_beats`; the short-of-beats test plans its
first storyboard with the rhythm off). No v1 pin moved. A-137.

## DEC-253 — The dashboard gets a UI kit, design tokens and lucide icons; every story confirm is the kit's dialog (dashboard overhaul stage 1, after DEC-252)
**Context.** The human (2026-10-03): "upgrade the whole UI/UX of the AI Story pages", in stages, a refined dark studio
(violet on slate kept), small dependencies allowed (lucide-react only). The dashboard had no primitives: emoji for
icons, a `fmtUsd` pasted into six story files (a seventh copy, `formatUsd`, in EstimateChip), twelve `window.confirm()`
calls whose native box drops every line break the messages carry, no toast, no empty state, no focus ring, and a
`--font-mono` naming fonts that are never loaded. Plan: `.claude/plans/dashboard/01-ai-story-ui-overhaul-plan.md`.
**Decision.**
- **Dependency**: `lucide-react` pinned exactly at 1.51.0 (latest stable, React 19 peer); nothing else is added. Pages
  take icons from `src/ui/icons.js` only (familiar names kept where lucide renamed one: Trash2, AlertTriangle,
  Loader2, Wand2).
- **Tokens** in `index.css :root`, every existing name kept: `--space-1..8` (4-48 px), `--text-xs..2xl` (12-28 px) with
  `--leading-*`, `--focus-ring` (2 px violet, offset 2 px), `--z-sidebar/dropdown/dialog/toast`, `--surface-raised`,
  `--backdrop`; `--font-mono` now names the system mono faces. No new font.
- **Kit** `src/ui/` (+ `ui.css`, imported once from `main.jsx`): Button (the existing `.btn` classes, so a swap is
  pixel-neutral, plus loading/icon/`as`), IconButton (aria-label required), Card slots, Badge, Chip (on `.chip`),
  Dialog (role dialog/alertdialog, aria-modal, labelled and described, focus trap and return, Escape closes, the
  backdrop closes unless `danger`), `DialogProvider` + `useConfirm()` + module-level `confirmDialog()` (both fall back
  to `window.confirm` with no provider: a confirmation is never dropped), ToastProvider/`useToast()` (one
  aria-live=polite region), EmptyState, Skeleton, Field (htmlFor, hint/error via aria-describedby), Money, and
  `lib/format.js formatUsd` (the seven identical copies, deleted; same output for every value).
- **Adopted**: the 12 story confirms (StoriesList, Cast x2, Places x3, Season x2, Storyboard x2, EpisodeStudio,
  the wizard's pipeline switch) are `await confirm({title, message, confirmLabel, tone})` with their wording split into
  title and message; deletes and the season re-plan are `danger` (focus starts on Cancel). Emoji become icons in the
  sidebar, the phone top bar, ModeSwitch, RouteChip, the stepper's done marks and the stories list; labels unchanged.
**Rejected.** Radix or another component kit, and a TypeScript rewrite (the source-text contracts, DEC-012, would all
move at once); a confirm that silently resolves false without a provider; restyling the pages now (stages 2-5).
**Consequence.** Clips mode pages are untouched (their two `window.confirm` stay, pinned by test_dashboard_job_controls).
Contracts moved (literal only, intent kept): `test_dashboard_clip_controls`, `test_dashboard_story_shared`,
`test_dashboard_switch_pipeline` and `test_story_payload_contract_series` now read `await confirm(` where they read
`window.confirm(`. The `✕` remove buttons and `↻ Regenerate` keep their glyphs (pinned by test_story_payload_contract).
Bundle: JS 549.45 -> 568.92 kB (gzip 153.1 -> 158.7), CSS 41.4 -> 51.0 kB. A-138.

## DEC-254 — The stories list shows cover cards: the list payload gains a cover, progress, episodes and a style label (dashboard overhaul stage 2, after DEC-253)
**Context.** Stage 2 of `.claude/plans/dashboard/01-ai-story-ui-overhaul-plan.md`. The list card showed a title, a status
chip, the raw style and a red Delete; `GET /api/stories` answered the seven index fields only, so a cover, the progress
or the episodes would have cost one `GET /api/stories/{id}` per card.
**Decision.**
- **Payload** (`workflow.list_card(s)`, called by the route; `StoryStore.list()` and its seven fields unchanged, byte for
  byte): each entry adds `cover` (the path under `/api` of the first character's portrait on disk, in `cast_order` --
  leads first -- else null), `progress` `{steps_done, steps_total, next}` (the contiguous prefix of concept, bible, style,
  cast, places, season, as `derive_status` counts; a v2 story adds `knowledge`, done while approved and current; `next`
  a NewStoryWizard step id or null), `episodes` `{count, latest: {ep, state} | null}` (state `draft|written|planned|
  assets|rendered`: an approved script, storyboard, assets, a render manifest with an output; four document reads at
  most, never `episode_outputs`' hashing), `style_label` (the shipped name, English) and `pipeline`. Read through a
  quiet store (a skipped entity folder is not printed on every visit); calls no provider; 12 live stories in ~100 ms
  cold. A story whose story.json cannot be read keeps a null cover and zeros (`steps_total: 0`); each part fails alone.
- **UI** `StoriesList.jsx`: a card grid (3 columns from 1100 px, 2 from 700 px, 1 below) of 4:5 covers (the portrait as
  a blob through `fetchStoryCoverUrl`, which only accepts a `/stories/<id>/media/` path; else the style's palette as a
  gradient with the title's initial), chips (language, style label, "Animated" on v2), a progress ribbon ("2 of 7
  steps · Next: Style", or "Ready" with the latest episode's state), "Updated 2 h ago" (`lib/format.js
  formatRelativeTime`), and an overflow menu (Open, Delete… through `useConfirm`, danger, today's wording). The title's
  link stretches over the card; the menu sits above it (no button inside a link). A skeleton grid while loading; the
  empty state and its text unchanged; the header counts the stories. The kit gains `ui/Menu.jsx` (menu button,
  arrow keys, Escape, focus back on the trigger before the action runs).
**Rejected.** One `GET /api/stories/{id}` per card (N requests, the whole story page's payload each); a signed cover URL
(DEC-113: story media stays behind the token, fetched as a blob); "the first approved portrait" (the plan's wording) --
a draft story's portrait is still the best cover; a menu local to the page (stages 3-4 need the same control).
**Consequence.** No dashboard contract moved. New: `tests/test_stories_api_list.py` (route + rules) and
`tests/test_dashboard_stories_list.py`. The status chip is gone from the card (the ribbon says it). Bundle: JS 576.75 kB
(gzip 161.0), CSS 55.0 kB.

