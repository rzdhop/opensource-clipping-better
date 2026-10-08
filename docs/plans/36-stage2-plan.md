# Plan 36 — Stage 2: the MCP v2 (proposed 2026-10-08, after stage 1)

Status: **PROPOSED, questions answered 2026-10-08** (1: a new connector · 2: inline clips for now · 3: VC as a tool); the
final plan awaits Rida's approval.
Read first: `docs/plans/36-CHECKPOINT.md` (top), `docs/plans/36-rebuild-from-scratch-plan.md` §4 (the tool list),
`showrunner/README.md`.

## 1. Goal

The chat drives the stage-1 toolbox: Claude (in a claude.ai chat or a Claude Code session) reads and writes a story
folder, sends GPU jobs (images, clips) on the showrunner endpoints, checks a clip, and assembles an episode, with no
shell. Every tool is a thin wrapper over a stage-1 module; no tool calls an LLM or decides anything creative.
Smoke ≈ $0.50 (one image, one clip, one check, one assembly from the chat).

## 2. Where it lives (recommended)

A **new server** `showrunner/mcp_server.py` (FastMCP, the same library as the live one), its own port and its own
systemd unit (`deploy/showrunner-mcp.service`), next to the live `rzdhop-story` server, which is not touched until
stage 5. Reasons: `showrunner/` must not import `clipping` (the live server does, through `clipping.providers`); the
live server's 39 tools serve the old app and a live unit; a crash or a restart of one never takes the other down.
The token door (`MCP_TOKEN`, Bearer for Claude Code + the claude.ai connector form) is copied from
`mcp_server/auth.py`, not imported. The process runs from `.venv` (faster-whisper is there; the venv is not
modified: `fastmcp` is already in it).

Rejected: adding the tools to the live server (couples the rebuild to the old app's imports and its unit; the
`story_step_*` tools would sit next to the new ones and confuse the writer).

## 3. Stages

| # | Step | Files | Risk | Verification |
|---|---|---|---|---|
| 2.0 | Server skeleton: `runpod_health`, `templates_list`, the token door, the unit file | `showrunner/mcp_server.py`, `showrunner/mcp_auth.py`, `deploy/showrunner-mcp.service`, tests | Low | FastMCP in-process client test; `systemctl --user`-free unit reviewed by Rida before install |
| 2.1 | Story tools: `story_list`, `story_create`, `store_read`, `store_write`, `store_lock`/`store_unlock` (reason required), `view_file` (images back as images) | same + tests | Low | tests on temp stories; a locked file refused through the tool |
| 2.2 | GPU tools: `comfy_submit(template, values, files, story, dest)` (async, journal in the story's `costs.jsonl` + `jobs.jsonl`), `comfy_status`, `comfy_fetch` (saves into the story, contact sheet back), `cost_ledger` | same + tests (fake endpoint) | Medium: money | every submit returns the estimate first; the showrunner endpoints only (`RUNPOD_SHOWRUNNER_VIDEO_*`, images key), never `RUNPOD_COMFY_ENDPOINT_ID` (test) |
| 2.3 | Gate tools: `verify_take(story, ep, shot, take)` (writes the verdict into `takes.json`), `vc_clip(story, ep, shot, take)` (every line to its speaker's locked voice → the next take, DEC-323), `contact_sheet`, `approve_take` (only after Rida's word in the chat), `assemble_episode(story, ep)` (mp4 + sheet back) | same + tests | Medium | the stage-1 tests' synthetic story through the tools |
| 2.4 | Smoke from the chat (Rida's go, ≈ $0.50): one keyframe, one clip, its check, one assembly on a scratch story | — | Medium | Rida watches the clip and the mini-episode |

Each stage: tests green → commit → action-log line. The riskiest: 2.2 (spend through a tool).

## 4. Questions for Rida (blocking)

1. **A new connector** (`showrunner`, its own URL and token) next to `rzdhop-story`, or the new tools inside the
   existing connector? Recommended: a new one (§2).
2. **Clip transfer:** base64 in the job answer (works today up to ≈ 10 s at 704×1280) or an R2/S3 bucket on the
   showrunner endpoint (needed for longer or bigger outputs)? Recommended: base64 now, bucket when a clip outgrows it.
3. ~~Voice conversion in the toolbox~~ — **answered 2026-10-08:** Rida approved the locked voice (singles and
   exchanges); `vc_clip` is in stage 2.3 and the cast step makes a `voice_ref.wav` per character (DEC-323).

## 5. Checks

- DECISIONS: DEC-316 (the ledger bills execution time), DEC-317 (assembly refuses an unapproved shot), DEC-319/320,
  DEC-321 (clip check on the CPU), DEC-322 (no media in git); the memory rule "no auth on the app" concerns the web
  app; the MCP keeps its existing token door (it is reachable from claude.ai). Checked, no other conflict.
- Regression contract: the live `rzdhop-story` unit, its 39 tools and the app's endpoints untouched; the 73
  showrunner tests stay green; nothing in `showrunner/` imports `clipping` or `mcp_server`.
