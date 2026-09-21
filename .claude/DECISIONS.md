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
