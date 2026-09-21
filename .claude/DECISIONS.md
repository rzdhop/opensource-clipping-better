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


## DEC-016 — One generic OpenAI-compatible provider, not a provider per vendor
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

## DEC-017 — Settings persist to `.local/settings.json`, and an empty value clears
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

## DEC-018 — The NIM default leaves the DeepSeek family for NVIDIA's own model
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

## DEC-019 — Salvage the first well-formed JSON value when a direct parse fails
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
