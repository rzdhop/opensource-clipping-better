# Plan 33 — MCP voice tools, file upload, an honest ledger, the "no still" guard (2026-10-07)

**Source.** A brief pasted by the human on 2026-10-07, written for this server by the remote
Claude that produced "Faille d'amour" ep01 through the MCP tools and hit gaps only this host
can close. The brief is treated as the approved plan (A-215); the human is away.

**Goal.** Five tasks: (1) `tts_line` / `tts_batch` / `voice_ref_make` on the MCP server;
(2) `file_upload` under `outputs/` only; (3) `billed_usd` from RunPod's `executionTime`, the
wall time kept beside it; (4) no still with motion can leave the pipeline from any client;
(5) the 13 Gemini voice lines of ep01 on disk with their durations.

## Context (from the three exploration maps)

- Tools are `@mcp.tool` closures in `mcp_server/server.py` or `register(mcp, ...)` modules;
  `tests/test_mcp_server.py::test_the_tools_are_listed` asserts membership, never the exact
  set. Domain errors must be re-raised as `ToolError` to keep their message.
- The journal (`outputs/mcp/jobs.json`) already stores `execution_ms` and `delay_ms` per job;
  `_billed` sums them (`runpod_comfyui.gpu_seconds`). The MCP seam is `JobClient._billed`,
  `ledger()` and `server._public`; the app adapters keep the shared function (follow-up).
- `GeminiTtsAdapter.generate` writes a mono 16-bit WAV at the answer's rate (24 kHz) after the
  tail guard; no style is applied (a spoken direction is read aloud). Edge gives MP3; ffmpeg
  makes the WAV. Chatterbox is `comfy_submit(template="tts_chatterbox", audio_path=<ref>)`,
  answered as FLAC; no runaway detector exists. `timing.RATE_PER_CHAR["fr"] = 0.070` s/char.
- `comfy_download.resolve_server_path` is the containment pattern to mirror for the upload.
- The still paths: `fill_failed_with_motion` (DEC-209), `animate: false` on the assets step
  (DEC-202), a missing clip record silently rendered as `motion` outside `fully_animated`
  stories (DEC-236), tier 1 and the `free`/`one_dollar` profiles (stills by construction).

## Stages

- **Stage 1 — honest ledger.** `mcp_server/runpod_jobs.py`: `_billed` bills `executionTime`
  only; `wall_seconds` and `delay_seconds` kept; `normalise_bill()` derives the honest fields
  from old rows (`execution_ms` present, `wall_seconds` absent). `ledger()` sums both and
  shows `wall_usd_if_delay_were_billed`. `server._public` shows the new fields. Tests:
  the three billing assertions updated, one test on an old row. Risk: low. Rollback: revert.
- **Stage 2 — `file_upload`.** New `mcp_server/file_upload.py`: outputs-only realpath
  containment (`os.sep`), extension allowlist, 25 MiB cap on the decoded bytes, atomic
  write, no overwrite unless asked, sha256 + size (+ duration for audio) back. Tests: traversal,
  extension, size, round trip. Risk: low.
- **Stage 3 — voice tools.** New `mcp_server/voice_tools.py`: `tts_line`, `tts_batch`,
  `voice_ref_make`; a `voice_ledger.json` under `outputs/mcp/` summed by `cost_ledger`.
  Gemini through `GeminiTtsAdapter` (retry on 429/5xx), edge through `edge_tts` + ffmpeg,
  Chatterbox through `JobClient.submit/wait` + ffmpeg (FLAC → WAV), the runaway guard at
  3 × `0.070 s/char` (+ 1 s), the silence-only check on the PCM. Tests with fakes (no network,
  ffmpeg-gated). Risk: medium (three providers). **The riskiest stage.**
- **Stage 4 — the "no still" guard** (an Opus agent, worktree, in parallel with 2–3):
  `animate: false` refused at every entry (the step, `phase4_request`, the MCP director);
  `fill_failed_with_motion` removed from the render params, API model, CLI, dashboard,
  MCP docstring; a shot without a current clip stops the render for every tier-2+ story,
  naming `regenerate shot:<ep>:<shot_id>:video`; the MCP's `story_create` without a preset
  makes a fully animated story and refuses tier 1 / non-`all_shots` profiles. Tier-1 goldens
  untouched (tier 1 stays in the app, unreachable from the MCP — open question for the human).
  Tests migrated per the map. Risk: high blast radius.
- **Stage 5 — restart + the 13 lines.** MCP unit restarted; `tts_batch` on the brief's
  lines with Gemini voices (≈ $0, free tier per the price table; the brief says ≈ $0.01);
  each WAV checked (0.8–6 s, no trailing noise, not silence-only); `durations.json`.
- **Stage 6 — docs.** DEC-316 (ledger), DEC-317 (no still), A-215…, CHANGELOG, checkpoint, log.

**Rejected alternative.** Fixing `runpod_comfyui.gpu_seconds` for the app and the MCP at once:
it changes the app's spend ledger and three more test files for a task scoped to the MCP's
ledger; left as a follow-up. **DECISIONS check:** DEC-209, DEC-202, DEC-236 amended by
DEC-317; DEC-012 (stdlib-only providers) respected — the new modules live in `mcp_server/`.
