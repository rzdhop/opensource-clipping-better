# The story MCP server — Claude as the director, this backend as its hands

`mcp_server/` turns the backend into [MCP](https://modelcontextprotocol.io) tools
that Claude calls from a chat: RunPod Serverless ComfyUI for images and clips
(stage 1, this page), the story store (stage 2), TTS, the renderer and the
metadata pack (stage 4). The idea: no wizard, no pages — you talk, Claude writes
the bible, the cast, the script and the storyboard in the conversation, asks you
to approve at each gate, and spends GPU seconds only through a tool call that
says so. The web UI keeps working as a viewer of the same `outputs/` folder.

## Install and run

```bash
pip install .[mcp]                      # fastmcp + python-dotenv on top of the app
python -m mcp_server                    # streamable HTTP on 127.0.0.1:8787 (MCP_HOST/MCP_PORT)
python -m mcp_server --stdio            # for a local client (Claude Code on the same machine)
```

`.env` (see `.env.example`):

| Variable | What |
|---|---|
| `RUNPOD_API_KEY` | the key the app already uses |
| `RUNPOD_COMFY_ENDPOINT_ID` | the **video** endpoint (Wan 2.2 / LTX) |
| `RUNPOD_IMAGE_ENDPOINT_ID` | the **image** endpoint (FLUX.2 klein), same volume; empty = images run on the video endpoint |
| `RUNPOD_GPU_USD_PER_HOUR`, `RUNPOD_IMAGE_GPU_USD_PER_HOUR` | optional flex prices, so the ledger shows dollars |
| `MCP_TOKEN` | the bearer token every client must send; empty = no auth, keep it on localhost |
| `RZDHOP_OUTPUTS_DIR` | where files go (default `<repo>/outputs`) |

Two endpoints because a worker that holds the 28 GB of Wan weights has to unload
them to run FLUX and reload them for the next clip: one endpoint per kind, both
on the `comfy-models` volume, and a warm worker stays warm for its kind.

### Expose it to claude.ai (Tailscale Funnel)

On the server, once:

```bash
sudo tailscale funnel --bg 8787
```

gives `https://<node>.<tailnet>.ts.net/mcp` with a valid certificate; nothing else
on the machine is exposed. Set `MCP_TOKEN` to a long random string first
(`python -c "import secrets; print(secrets.token_urlsafe(32))"`).

Clients:

- **Claude Code** (laptop or the server): `claude mcp add --transport http rzdhop https://<node>.<tailnet>.ts.net/mcp --header "Authorization: Bearer <MCP_TOKEN>"`.
- **claude.ai web / desktop / mobile** (custom connector): needs OAuth rather than a static header — stage 5 adds a provider; until then, use Claude Code.

A systemd unit for the server:

```ini
[Unit]
Description=rzdhop story MCP
After=network-online.target

[Service]
WorkingDirectory=/path/to/opensource-clipping-better
ExecStart=/path/to/venv/bin/python -m mcp_server
Restart=on-failure
EnvironmentFile=/path/to/opensource-clipping-better/.env

[Install]
WantedBy=multi-user.target
```

## The tools (stage 1)

| Tool | Cost | Does |
|---|---|---|
| `runpod_health(kind?)` | free | workers ready/idle/running/throttled and queued jobs per endpoint |
| `templates_list()` | free | the `templates/workflows/*.json` this server can submit: task, endpoint kind, values, clip lengths, model files |
| `comfy_submit(template, prompt, …)` | **GPU seconds** | one `POST /run`; returns the `job_id` at once. Several submits run in parallel on the endpoint's workers. Arguments: `negative`, `seed`, `width`/`height`, `seconds` (video), `image_path` (i2v keyframe), `ref_paths` (edit references, up to 4), `name` (file stem), `dest` (folder under `outputs/`), `note` |
| `comfy_status(job_id)` | free | one `GET /status`; a job that ended is settled: its files are written to `dest`, its GPU seconds and dollars kept, or its error |
| `comfy_fetch(job_id, wait_s=240)` | free | waits (bounded) and shows the result: an image as a thumbnail, a clip as a contact sheet of 8 frames plus duration, size and whether it has sound |
| `comfy_cancel(job_id)` | free | cancels a queued or running job |
| `comfy_jobs(limit, refresh)` | free | the journal, newest first; `refresh` asks RunPod about the unfinished ones |
| `cost_ledger(since?)` | free | GPU seconds and dollars of the finished jobs, per kind |
| `view_file(path)` | free | look at any image or clip under `outputs/` (or the repo) |
| `list_files(folder)` | free | the files under a folder of `outputs/`, newest first |

Jobs are journaled in `outputs/mcp/jobs.json` the moment RunPod answers the
submit, so a job survives a server restart and is never submitted twice; the
files land in `outputs/mcp/<date>/` unless `dest` says otherwise (a story's
folder, from stage 2 on). What RunPod bills (`executionTime + delayTime`, cold
start included) is kept per job and summed by `cost_ledger`.

## Tests

`tests/test_mcp_runpod_jobs.py` (the job client, fake transport) and
`tests/test_mcp_server.py` (the tools through an in-process MCP client; skipped
when `fastmcp` is not installed). Nothing leaves the machine; nothing is spent.
