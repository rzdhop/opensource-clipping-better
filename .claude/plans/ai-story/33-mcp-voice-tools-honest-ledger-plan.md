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
# Plan 35 as shipped — one talking clip per line, never slowed (input for DEC-318)

Branch: the agent worktree branch (see the report for the name and commit). Base: main at 4f2d989.

## What the pipeline now does

### 1. Per-line talking parts (own_gpu / `talking_clips: runpod_s2v` only)
- `clipping/aistory/steps/talking.py`
  - `verdict()` (plan 32 stage 8, one talking clip) is **unchanged**.
  - New `shot_verdict()` wraps it: when the single verdict is refused for `speakers`, `framing`,
    `not_alone` or `too_long` (`SPLIT_REASONS`) **and the shot has 2+ lines**, it tries `split()`.
    A one-line shot is `verdict()` alone (a one-line two-shot stays i2v, as before).
  - `split()` → a verdict that talks with `parts: [{line_id, speaker, start_s, duration_s, spec}]`
    and `single` (why one clip could not). Each part = one line; `spec` is a one-line dialogue
    track (`lipsync.build_track` format, the line at its place in the part, 5 s, tempo 1.0).
    The combined `spec.hash` covers every part's track hash and span → a re-voiced / re-timed line
    makes the clip stale through the existing `track_current` / `clip_state` path.
  - `_cuts()`: the parts cover the shot end to end (the shot keeps its storyboard length; the
    timeline, `timing.py` and the line placement are untouched). First part starts at 0; each cut
    falls in the pause between two lines (its middle, moved inside the pause if a part would run
    past `MAX_PART_S` = 4.875 + 0.5 s hold); each line must end within one chunk (`CHUNK_S` 4.8)
    of its part's start. Infeasible → `too_long` (the shot stays i2v → refused if longer than its
    clip, see 3).
  - A speaker must be a character of the story (`NO_FACE` otherwise: the narrator has no face).
  - `verdicts()` / `verdict_now()` now return `shot_verdict()`.
  - Helpers: `is_split`, `part_name` (`shot_NN.lNN.mp4`), `closeup_name` (`shot_NN.lNN.<ext>`),
    `closeup_reference` (the speaker's portrait sheet), `closeup_prompt`, `record_parts`.
  - Constants: `TALK_KEPT_S` 4.875, `PART_HOLD_S` 0.5, `MAX_PART_S`, `MAX_HOLD_S` 1.25 (the hold a
    single talking clip may take after its speech — what stage 8 allowed through MAX_STRETCH).
- `clipping/aistory/steps/assets.py`
  - `make_clip()` uses `talking.shot_verdict()`; a split verdict goes to the new
    `make_talk_parts()`: per part, `make_closeup()` then one S2V request
    (`clip_request(..., keyframe=<close-up>, name="shot_NN.lNN")`, the part's track as
    `GenRequest.audio`, the clip's seed for every part), through the generation cache and the paid
    gates; the part's mp4 is kept as `assets/clips/shot_NN.lNN.mp4`. A part that fails fails the
    clip (the parts made are in the journal: a retry collects them at $0).
  - `make_closeup()`: a multi-reference edit (`IMAGE_EDIT`) — references (speaker's sheet, shot
    keyframe) — on the episode's recorded keyframe link (own_gpu: `runpod/edit_flux2_klein_multiref`),
    else the keyframe chain; seed = the part's recorded `closeup_seed`, else
    `derive_seed(story, ep, "shNN.lNN")`; cropped to 9:16 like a keyframe; stored as
    `assets/shots/shot_NN.lNN.<ext>`.
  - The clip record carries `parts: [{line_id, speaker, start_s, duration_s, closeup,
    closeup_sha256, closeup_seed, closeup_cache_key, closeup_note, video, video_sha256, cache_key,
    track_hash, audio_sha256, est_usd, seed}]` and `talk` (`record_parts`); `assets.video` names
    the first part (the schema accepts it only for a clip with parts).
  - `clip_key()` returns None for a split shot (never "booked" in the estimate; the journal still
    serves each part).
- `clipping/aistory/steps/clips.py`: `clip_state()` → `stale` when a part's clip or close-up is
  gone or its sha256 moved (`parts_intact`); `part_clip_path`, `closeup_path`.
- `clipping/aistory/schemas.py`: `SHOT_IMAGE_NAME_PATTERN` / `SHOT_CLIP_NAME_PATTERN` accept the
  `.lNN` part names (the shot's own `assets.image` still may not have one); `parts` on
  `_STORYBOARD_CLIP_SCHEMA`.

### 2. The render cuts the parts back to back
- `steps/render.shot_clips()` hands `video_parts: {shot_id: [file record, ...]}` for a current clip
  with parts; `render_inputs()` passes it as `inputs["video_parts"]`.
- `render/plan.py`: a shot whose clip has `parts` and whose part files were given →
  `filtergraph.tier2_parts_argv()`: one input per part (manifest input id `shNN_lNN`, role `shot`),
  each cover-filled, 30 fps, held if short, `trim=end_frame=<its frames>`, then
  `concat=n=N`; `-frames:v` = the shot's exact frames. Frames per part: `filtergraph.part_frames()`
  (cumulative rounding, the last part takes the rest). No `setpts` — never slowed.
- An old clip recorded `cover: stretch` (made before plan 35) still renders as it did
  (tier2_clip_argv unchanged; tier-2 goldens untouched).

### 3. No stretch, for every story (DEC-250 amended)
- `clips.MAX_STRETCH = 1.0` → `stretch_of()` never returns a factor; no new row/record says
  `cover: stretch`.
- A fully animated shot longer than its clip by more than `HOLD_TOLERANCE_S` (0.5 s) is refused
  (`too_long`): the sentence now says "a clip is never slowed", names each shot with its length and
  its regenerate target `shot:<ep>:<shot_id>:plan`, and on a talking-clips story adds that a
  speaking shot of 2+ lines is cut per line when each line fits one clip.
- A single talking clip (stage 8) may still be held after its speech up to `talking.MAX_HOLD_S`
  (1.25 s) — `_talk_held` no longer reads MAX_STRETCH.
- `clips.cover_sentence()`: a split row → "shNN is cut from N talking clips back to back, one per
  line (never slowed)"; otherwise the held sentence (the stretch branch only for an old record).

### 4. Estimates and pricing
- `clips._talk_rows()`: a split shot is one row with `link` S2V, `talks`, `parts` N, `closeups` N,
  `clip_s` = 5·N, `est_usd` = 5·N·$0.02 + N·(close-up price); no `held_s`. The talking part counts
  `count` (clips), `seconds`, `est_usd`, `split`, `split_lines`, `closeups`, `closeup_usd`; its
  sentence adds "K of them cut into one talking clip per line (N clips, each from a close-up of its
  speaker)".
- `clips.closeup_price()`: one image on the episode's planned keyframe link at its table price
  (own_gpu: $0.015); 0.0 when unreadable.
- The rows' est feeds `plan.video_usd` → the episode cap (own_gpu $2) sees parts and close-ups;
  `story_estimate(episode)` (MCP) reads the same units.

### 5. Regenerate target
- `shot:<ep>:<shot_id>:closeup:<line_id>` (kind `shot_closeup`, `regenerate.parse_target`,
  `EPISODE_TARGETS`): a fresh close-up seed + the note recorded on that part, then the shot's clip
  again with the clip's own seed kept (`regenerate_shot_clip(keep_seed=True)`): only that part's
  close-up and S2V clip are bought; the other parts are served by the journal at $0.
  Refused when the clip is not cut per line or has no part for the line
  (`assets.closeup_target_refusal`). Wired in `episode_regenerate`, `workflow` (target docs,
  checks, units = one edit; the HTTP estimate goes through `regenerate_clip_estimate`, which prices
  the whole shot — an over-estimate) and `web/api/routes/stories.py`; listed in the MCP
  `story_step_start` docstring.

## Untouched (as asked)
`prompting.py` and every hashed prompt core (the close-up prompt is a new send-layer prompt), the
goldens, DEC-317 (no still path added), the i2v recipe of plan 34, the `.claude/*.md` artifacts,
CHANGELOG, the action log, `mcp_server/voice_tools.py`, `mcp_server/file_upload.py`,
`tools/edit_faille_ep01.py`, `tools/regen_faille_ep01_clips.py`.

## Tests
- New `tests/test_story_talking_parts.py` (6): the two-speaker two-line split (speakers, spans,
  each part's own WAV at its offset, the hash moving on a re-voice); one-line / two-shot / silent
  shots unchanged; `_cuts` rules; `part_frames` + `tier2_parts_argv` (argv + real ffmpeg: exact
  frame count); the one click end to end (close-up requests with (sheet, keyframe), S2V requests
  with the close-up, per-part tracks, records, manifest inputs, `S:` argv `concat=n=N`, no `*PTS`,
  Continue buys nothing); regenerate one close-up (one image + one clip bought, the other part kept).
- Moved to the new behaviour (DEC-250 amended, justified, nothing loosened):
  `test_story_long_shots.py` (refusal instead of stretch, regenerate targets named, MAX_STRETCH 1.0,
  the one click buys nothing for a too-long shot), `test_story_ambience.py` (the 9.4 s shot is
  named), `test_story_lipsync.py` (an 11 s shot on a 10 s cap is refused, not slowed),
  `test_story_talking_clips.py` (the estimate counts parts and close-ups; the one click names the
  part requests).
- Runs: see the report (both envs + the MCP venv).

## What is left (follow-ups)
1. The part's S2V prompt is still the shot's video prompt (it may name both characters); a
   per-part prompt ("<speaker> speaks to the camera …") is a send-layer follow-up.
2. A part that fails drops the `parts` record (and a close-up regenerate's seed/note) from the
   failed clip record; the retry re-derives the seeds (the journal still serves what was made).
3. The Handoff / episode page / brief show `assets.video` = the first part only; a page view of the
   parts and their close-ups is UI work.
4. The HTTP regenerate estimate for `:closeup:` prices the whole shot (every part), not the one part
   it will actually buy.
5. Shots whose cuts are infeasible (one line > 4.8 s, or a long silent stretch inside a 2+ line
   shot) stay i2v and, past the hold, refuse the plan — the storyboard should size exchanges so
   every line fits one chunk (a storyboard-side rule, not done here).
6. A one-line two-shot keeps the i2v link (literal reading of the brief); splitting it into one
   close-up part would make it talk too — a one-line change in `shot_verdict` if wanted.
7. Live check on RunPod: the close-up edit on the real FLUX.2 klein multiref template and the S2V
   parts on a real two-speaker shot (nothing here reached RunPod or Gemini).
