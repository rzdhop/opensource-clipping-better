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
