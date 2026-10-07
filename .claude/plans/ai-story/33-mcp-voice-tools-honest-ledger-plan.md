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

## Stage 4 as shipped

Branch `worktree-agent-a58dc6082717c13a8` (worktree of ba145e7). The rule: at tier >= 2 every shot is a
video clip. Only a shot the user pins `keep_still` (DEC-236's one exemption, kept) is cut from its picture.

**Entry points guarded.**
- `assets.animate_param` raises `StepFailed(ANIMATE_OFF_REFUSAL)` for `animate: false`. It is called
  first in `assets.run()`, before the context loads, which covers the MCP director (it passes raw
  params). It is also called in `workflow.phase4_request`, so the API answers 400 before a job exists.
  The sentence points to the keyframe hold (`keyframes:<ep>`) and the fast track's `stop_at_keyframes`.
  `animate: true` is still accepted (`ASSETS_PARAMS` unchanged; the dashboard always sends `animate: true`).
- `render.shot_clips`: for every tier-2+ story, a shot not kept still that has no clip record now
  refuses the same way as a failed or stale one (`clip_refusal(ec, blocked, unmade)`). The sentence
  names each shot and its `shot:<ep>:<shot_id>:video` target and offers no fill. A fully animated
  story keeps its own sentence (`fully_animated_refusal`), which now also names the targets of shots
  with no clip. Untouched: the tier-1 path (`stock_shot_clips`), `render/plan.py`, `filtergraph` and
  the goldens.
- MCP (`mcp_server/story_tools.py`): `story_create` without a preset uses `animated_profile()` (the
  quality profile on `own_gpu`: v2, tier 3), with the caller's keys on top. `still_refusal` refuses
  tier < 2 or a budget profile that is not `all_shots`, with or without a preset, at `story_create`
  and `story_patch` (`story_patch` checks only the keys sent). The refusal names the animated
  profiles. `story_options` lists only `all_shots` profiles. The docstrings no longer say "no clips",
  `animate (default true)` or `fill_failed_with_motion`. The render line says a missing or failed clip
  stops the render and names `shot:<ep>:<shot_id>:video`.

**Removed.**
- `fill_failed_with_motion` is gone from:
  - `render.PARAMS` and `workflow.RENDER_PARAMS` (the API now answers 400 "unknown parameter");
  - `RenderStepParams`;
  - the estimate route's query param;
  - the CLI (`--fill-failed-with-motion`, and `--no-animate` with it);
  - the dashboard (both checkboxes, and `api.js`).
- The fast track's paid stop no longer offers "keep their shots still / animate off" as a way out,
  for any story.
- `fill_failed_with_motion: true` sent straight to the step (the MCP path) is refused
  (`render.fill_refusal`). On a re-render, an old manifest's `true` is dropped (`rerender_params`).
- `schemas.RENDER_FILL_PARAM` and the `motion_fill` mode stay, so old manifests still validate.
- `workflow.assets_gate` and `assets_estimate` lost their `animate` kwarg.
- `docs/AI_STORY.md` updated (four passages).

**Tests migrated.**
- `tvp._keyframes` now makes the images and voices at tier 1, then moves the story to tier 2. That is
  the state `animate` off used to leave.
- The v2 keyframe tests (keyframe_gate ×9, keyframe_hard_gate ×4, handoff_gate ×1) drop
  `animate: False`: the keyframe hold already keeps the clips out.
- New helpers `tvp.planned_ids` and `tvp.keep_unplanned_still`, plus
  `trc._animated(..., still_unplanned=True)`. They pin `keep_still` on the shots that the one_dollar
  key-shots plan leaves out, in the tests that must reach a render: one_click ×4, fast_track_v2,
  api_clips ×3, clip_controls, render_native_audio, render_ambience and stock_render tier 3.
- Rewritten to assert the refusal:
  - render_clips ×2;
  - fully_animated_render (the unplanned shots of a story that is not fully animated, and the fill
    param refused);
  - clip_controls ×2;
  - the fast_track_v2 legacy sentence;
  - the video_phase animate-off test;
  - api_clips ×2;
  - api_phase4 (`RENDER_PARAMS`, the closed-list text, a new 400 for `animate: false`);
  - payload_contract, defaults and cli_clips.
- New `tests/test_mcp_no_still.py` (5 tests).
- The selection (256 files) passes in both environments:
  - local: 7339 passed, 12 skipped, exit 0;
  - CI-like: 6370 passed, 950 skipped, exit 0.

**Left.**
1. `tests/test_mcp_server.py` (not edited here) pins the old MCP behaviour and fails 2 tests:
   - line ~315 expects `plain["generation_profile"]["budget_profile"] == "free"`; it is now `own_gpu`
     at tier 3;
   - line ~327 reads `caps["free"] == 0.0`, but free is no longer listed.
2. `workflow._require_every_clip` (the assets approval guard) still applies only to fully animated
   stories. For other stories the render is the gate. Extending it is a separate change.
3. The app's own new-story paths (the wizard, the API's create, the CLI's `--tier 1`) can still make a
   tier-1 or one_dollar story. At tier 2+ such a story now stops at the render until every shot has a
   clip or is kept still. Removing tier 1 or the key-shots profile is the human's decision.
