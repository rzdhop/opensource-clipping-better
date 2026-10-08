# Plan 36 — Stage 2: the MCP v2 (`showrunner` connector)

Status: **DONE 2026-10-08** — approved, built (2.0–2.3), deployed; then, on Rida's word, the showrunner server
**replaced** rzdhop-story on port 8787 and the Funnel root (DEC-325): the 8443 / 8788 layout below was dropped.
Left: Rida re-adds the connector; the smoke (≈ $0.50) on his go. Rida's answers: a new connector next to
`rzdhop-story` · clips returned inline for now · voice conversion is a tool (DEC-323).
Read first: `docs/plans/36-CHECKPOINT.md` (top), `docs/plans/36-rebuild-from-scratch-plan.md` §4, `showrunner/README.md`.

## 1. Goal

A chat (claude.ai or Claude Code) drives the stage-1 toolbox with no shell: read and write a story folder, send
GPU jobs (images, clips) into it, check a clip, convert its voices, approve a take, assemble the episode and get
it back. Every tool is a thin wrapper over a stage-1 module; no tool calls an LLM or decides anything creative.

## 2. Facts that shape it (mapped 2026-10-08)

- The live server `mcp_server/` (39 tools, unit `rzdhop-story-mcp`, `.venv`, port 8787) owns the Funnel's
  `https://main-network-interface.tail01346d.ts.net/` (443, root path). OAuth discovery is origin-rooted, so the new
  server gets its **own Funnel port: 8443** → `https://main-network-interface.tail01346d.ts.net:8443/mcp`, local
  port **8788**.
- `mcp_server/auth.py` (203 lines: Bearer door `StaticTokenVerifier` + claude.ai door `GatedOAuthProvider`, a login
  page asking the token) imports only stdlib + `mcp`/`fastmcp`: copied, not imported, with its branding, its
  bearer client id and its state file changed. **Same secret** (`MCP_TOKEN`), separate OAuth state
  (`outputs/showrunner-mcp/oauth.json`, mode 600).
- The live server reads `MCP_HOST/MCP_PORT/MCP_PUBLIC_URL` from the shared `.env`: the new one reads
  `SHOWRUNNER_MCP_HOST` (127.0.0.1), `SHOWRUNNER_MCP_PORT` (8788), `SHOWRUNNER_MCP_PUBLIC_URL`.
- Media to the chat: images as `Image(jpeg)` thumbnails; an mp4 inline as a base64 `EmbeddedResource`
  (≤ 25 MiB, the live `comfy_download` rule — a 30-s episode is ≈ 8 MB); a chat tool call must answer within
  ≈ 280 s, so GPU tools are submit-then-fetch, never one blocking call.
- `fastmcp` 4.0.11 is in `.venv` only: the server tests run there (scratch pytest on `PYTHONPATH`, the venv never
  modified — the live unit runs from it); the system-python suite skips them (`importorskip`).

## 3. Stages (one commit each; rollback = revert that commit)

| # | Step | Files | Risk | Verification | Regression items |
|---|---|---|---|---|---|
| 2.0 | **Skeleton + doors:** `python -m showrunner.mcp_server` (streamable HTTP `/mcp`), `mcp_auth.py` (the copied doors), settings from `SHOWRUNNER_MCP_*` + `MCP_TOKEN`, tools `runpod_health`, `templates_list` | `showrunner/mcp_server.py`, `showrunner/mcp_auth.py`, `showrunner/tests/test_mcp_server.py` | Low | in-process `fastmcp.Client` tests; the auth dance over ASGI (401 without token, Bearer ok, register → login → 303 → token → tool call) as the live `test_mcp_auth.py` does | the live unit untouched; no `clipping`/`mcp_server` import (extended test) |
| 2.1 | **Story tools:** `story_list`, `story_create`, `store_read`, `store_write` (text only), `store_lock`, `store_unlock` (reason required), `store_copy` (a file inside the story), `view_file` (image → thumbnail; mp4 → contact sheet + probe), `file_download` (mp4/png inline, ≤ 25 MiB) | `showrunner/mcp_server.py` + tests | Low | temp stories through the tools; a locked file refused; a path leaving the story refused | `store.py` contracts (test_store) |
| 2.2 | **GPU jobs:** `comfy_submit(story, template, values, files, dest)` → job id + ≈ cost (files are story paths; video templates on `showrunner-video`, image templates on the images endpoint with `RUNPOD_IMAGE_API_KEY`; never `RUNPOD_COMFY_ENDPOINT_ID`), `comfy_status`, `comfy_fetch(job, wait_s ≤ 240)` (saves the outputs at `dest` in the story, a clip recorded as the shot's next take, a row in `costs.jsonl`, a thumbnail/contact sheet back), `cost_ledger(story)`. Job journal `<story>/jobs.jsonl` (submit written at once, as `run_batch`) | `showrunner/jobs.py` (submit/settle into a story, from `run_stage0.run_batch`), `mcp_server.py` + tests | **Riskiest: money through a tool** | fake endpoint: routing per template, billed = execution time (DEC-316), the live endpoint id refused, a LOST job never sinks the journal | `runpod_client` (test_stage0_offline) |
| 2.3 | **Gate tools:** `verify_take(story, ep, shot, take)` (CPU, ≈ 35 s, verdict into `takes.json`), `vc_clip(story, ep, shot, take)` (submit one VC job per speaker part; `vc_fetch` joins, remuxes, records the next take — DEC-323), `approve_take(story, ep, shot, take, note)` (the note quotes Rida; the docstring forbids approving without Rida's word in the chat), `assemble_episode(story, ep)` (mp4 inline + contact sheet + report) | `showrunner/voice.py` (clip VC from `run_stage0`'s exchange code, shared by both), `mcp_server.py` + tests | Medium | the stage-1 synthetic story end to end through the tools with a fake endpoint (submit → fetch → verify on injected words → vc → approve → assemble) | `verify`, `assemble`, `run_stage0 vc` (its tests keep their intent against `voice.py`) |
| 2.4 | **Deploy + smoke:** `deploy/showrunner-mcp.service` (`.venv`, `ReadWritePaths` = `stories/` + `outputs/showrunner-mcp/`); Rida runs the two sudo commands (unit install, `tailscale funnel --bg --https=8443 http://127.0.0.1:8788`) and adds the connector; then **on Rida's go (≈ $0.50)** a scratch story from the chat: one keyframe, one 5-s clip, its check, its VC, one assembly | `deploy/showrunner-mcp.service`, `docs/MCP.md` (a showrunner section), `showrunner/README.md` | Medium | Rida sees the clip and the mini-episode in the chat | the live connector keeps working (Rida checks one call) |

Each stage: tests green (system python + venv) → commit → action-log line → next.

## 4. Checks

- **Rejected alternatives:** the new tools inside `rzdhop-story` (couples the rebuild to the old app's imports and its
  live unit; Rida chose a new connector); one blocking tool per GPU job (the chat's ≈ 280 s limit, 20–35 min queues);
  a storage bucket now (Rida: inline for now; a 10-s 704×1280 clip is ≈ 2 MB).
- **DECISIONS.md:** DEC-316 (ledger = execution time), DEC-317 (assembly refuses an unapproved shot), DEC-319 (any
  universe), DEC-320 (D7), DEC-321 (CPU clip check), DEC-322 (no media in git), DEC-323 (locked voices). The memory
  rule "no auth on the app" concerns the web app; the MCP keeps the existing token door (public via Funnel).
  Checked, no other conflict. New at close: DEC-324 (the connector's layout: own port, Funnel 8443, same secret).
- **Riskiest stage:** 2.2 (spend through a tool).
- **Regression contract:** the live `rzdhop-story` unit, its 39 tools, its Funnel route and the app's endpoints
  untouched; `RUNPOD_COMFY_ENDPOINT_ID` never used (test); the 76 showrunner tests stay green; nothing in
  `showrunner/` imports `clipping` or `mcp_server` (test).
- **Cost:** stages 2.0–2.3 $0; 2.4 smoke ≈ $0.50 on Rida's go.
- **What Rida does:** approve this plan; at 2.4 run two sudo commands, add the connector (URL + the token on the login
  page), give the smoke's go.
