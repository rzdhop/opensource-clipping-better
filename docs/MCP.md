# The showrunner MCP server — Claude writes and directs, these tools are its hands

`showrunner/mcp_server.py` turns the plan-36 toolbox (`showrunner/`) into [MCP](https://modelcontextprotocol.io)
tools that Claude calls from a chat: the story folder (`stories/<slug>/`, in git), GPU jobs on RunPod (images and
clips), the clip check, the locked voices, the assembly. No tool calls an LLM and no tool decides anything creative;
every tool that spends money says so, and Claude asks Rida's go before calling it.

It replaced the rzdhop-story server (`mcp_server/`, 39 tools, deleted 2026-10-08, DEC-325) on the same port and the
same public URL. That server's guide is in git history before that date.

## Install and run

```bash
uv venv && uv pip install -e ".[mcp]"          # fastmcp 4 + python-dotenv (the repo's .venv has them)
.venv/bin/python -m showrunner.mcp_server       # streamable HTTP on 127.0.0.1:8787, path /mcp
```

Settings, from the environment or `.env` (never committed):

| Name | What |
|---|---|
| `SHOWRUNNER_MCP_HOST` / `SHOWRUNNER_MCP_PORT` | where it listens (127.0.0.1 / 8787) |
| `SHOWRUNNER_MCP_PUBLIC_URL` | the address clients reach it at (the Funnel URL, no `/mcp`); set, the server is also the OAuth server of the claude.ai connector |
| `MCP_TOKEN` | the secret of both doors: the bearer token, and what the login page asks |
| `RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID` + `RUNPOD_SHOWRUNNER_VIDEO_KEY` | the video endpoint (LTX-2.5 clips, Chatterbox voice conversion) |
| `RUNPOD_IMAGE_ENDPOINT_ID` + `RUNPOD_IMAGE_API_KEY` | the images endpoint (FLUX.2 klein) |
| `SHOWRUNNER_STORIES_DIR` | the stories (`stories/`) |

The live app's `RUNPOD_COMFY_ENDPOINT_ID` is read only to be refused. The OAuth clients and tokens are kept in
`outputs/showrunner-mcp/oauth.json` (mode 600), so a restart does not log the connector out.

## Expose it (Tailscale Funnel) and connect

```bash
sudo tailscale funnel --bg 8787        # https://<node>.<tailnet>.ts.net -> 127.0.0.1:8787
sudo cp deploy/showrunner-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now showrunner-mcp
journalctl -u showrunner-mcp -f
```

- **claude.ai** (web, desktop, phone): Settings → Connectors → Add custom connector, URL
  `https://<node>.<tailnet>.ts.net/mcp`, no client id; the login page asks `MCP_TOKEN`.
- **Claude Code**: `claude mcp add --transport http showrunner https://<node>.<tailnet>.ts.net/mcp --header "Authorization: Bearer <MCP_TOKEN>"`.

## The tools (24)

| Group | Tools |
|---|---|
| Endpoints | `runpod_health` · `templates_list` |
| Story files | `story_list` · `story_create` · `store_read` · `store_write` · `store_copy` · `store_lock` · `store_unlock` (a reason is required) |
| Prompts (stage 3) | `prompt_keyframe` · `prompt_clip` · `prompt_cast`: built from the story's own files through `showrunner/prompts.py` (the batch-a golden), never by hand |
| Look and hand over | `view_file` (an image as a picture, a clip as an 8-frame sheet + its numbers) · `file_download` (the bytes, ≤ 25 MiB) |
| GPU jobs | `comfy_submit` (**costs money**) · `comfy_fetch` · `comfy_jobs` · `cost_ledger` |
| Gates | `verify_take` (the clip check on the CPU) · `voice_ref_from_take` (a character's locked voice, from a take Rida approved) · `vc_clip` (**costs money**, locked voices) · `vc_fetch` · `approve_take` (a note quoting Rida) · `assemble_episode` |

`comfy_submit` sends the prompt the server builds: `prompt_from` is `keyframe:<ep>:<shot>`, `clip:<ep>:<shot>` or
`cast:<char>:<full_body|turnaround|emotions>` (what the prompt tools show). A hand-written `values.prompt` is refused
unless `hand_prompt_reason` says why; the reason is kept in the job journal. A character's voice is cast the stage-0
way: a take of that character (usually an `ep00` casting shot) that Rida approved, its line cut by the clip check's
timings (2–10 s), locked as `02-cast/<char>/voice_ref.wav` with `voice_ref.json` saying where it came from.

GPU tools are submit-then-fetch: a chat tool call must answer within about 280 s, and GPU queues of 20–35 minutes are
normal (and free). A job is written to the story's `jobs.jsonl` the moment it is sent and settled once; its cost goes
to `costs.jsonl` (execution time only, DEC-316).

## Tests

The server's tests need fastmcp, so they run in `.venv` with a pytest kept outside it (the live unit runs from `.venv`):

```bash
PYTHONPATH=<a folder with pytest> .venv/bin/python -m pytest showrunner/tests -o addopts="" -q
```

The system python runs the rest of `showrunner/tests` (the server's tests skip there).
