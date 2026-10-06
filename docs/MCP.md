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
uv venv && uv pip install -e ".[mcp]"   # fastmcp + python-dotenv on top of the app (or: pip install .[mcp])
uv run python -m mcp_server             # streamable HTTP on 127.0.0.1:8787 (MCP_HOST/MCP_PORT)
uv run python -m mcp_server --stdio     # for a local client (Claude Code on the same machine)
```

`.env` (see `.env.example`):

| Variable | What |
|---|---|
| `RUNPOD_API_KEY` | the key the app already uses |
| `RUNPOD_COMFY_ENDPOINT_ID` | the **video** endpoint (Wan 2.2 / LTX) |
| `RUNPOD_IMAGE_ENDPOINT_ID` | the **image** endpoint (FLUX.2 klein), same volume; empty = images run on the video endpoint |
| `RUNPOD_IMAGE_API_KEY` | optional: a key of its own for the image endpoint (else `RUNPOD_API_KEY` opens both) |
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
ExecStart=/path/to/opensource-clipping-better/.venv/bin/python -m mcp_server
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

## The story tools (stage 2) — the steps, with Claude as the writer

The app's story steps (`clipping/aistory/steps`) write their documents through
`llm_call.call_json`: build the prompt, send it on the LLM chain, validate the
reply, ask again on a refusal. The chain runner is injectable, and the MCP uses
that: a step started from the chat runs in a thread with a runner that, instead
of calling a provider, **parks the prompt for the conversation** and waits for
its answer. Claude reads the prompt (system, user, JSON schema, token cap),
answers it, and the step carries on exactly as with a hosted model — the same
prompts, validators, documents, approvals, and the web UI shows the result.
The chain the run sees is `chat/claude` (`registry.PROVIDERS["chat"]`), a free
link nothing else can call; the dashboard's saved Settings (`data/settings.json`)
are read underneath it for everything else (image/video chains, keys, budget).

| Tool | Does |
|---|---|
| `story_list`, `story_create(language, seed_text?, style?, episode_format?, generation_profile?)`, `story_options` | the store and its catalogue (styles, episode formats, steps) |
| `story_get(story_id)` | the story at a glance: bible, approvals, style lock, season, knowledge, entity summaries with what each lacks, episodes, recent runs |
| `story_doc`, `story_entities`, `story_entity`, `episode_get`, `episode_doc` | the full documents |
| `story_step_start(story_id, step, ep?, params?)` | runs a step; returns its first event: `waiting` with a `pending` prompt, `done` with the result, `failed` with the error, or `running` |
| `story_step_answer(handle, answer)` | the chat's JSON answer to a pending prompt; returns the next event (the validator's refusal comes back as the same prompt with the reason under it) |
| `story_step_status`, `story_step_cancel`, `story_runs` | follow, stop, list runs (one per story at a time) |
| `story_approve(story_id, doc, approve_anyway?, direction?)`, `story_approve_all(story_id, group)` | the app's approvals, by its rules (`workflow.approve_*`) |
| `story_choose_concept`, `story_patch`, `entity_patch`, `episode_patch` | the app's edits (`workflow.patch_*`) |

Steps that make images or clips (`cast`, `places`, `assets`, `style_preview`)
spend through the generation chains the Settings name — RunPod once stage 3's
image adapter is in — so the director says what a step will buy before it runs.
Do not run the same story from the web UI and from the chat at the same time:
the MCP does not see the web worker's job queue.

## Images and clips on RunPod (stage 3)

`clipping/providers/runpod_images.py` makes the image templates paid links of
the app's own image chains — `runpod/t2i_flux2_klein` (text to image),
`runpod/edit_flux2_klein_multiref` (up to four references), `runpod/edit_qwen_image`
— on the image endpoint, journaled and resumed like a clip, priced per image
(`pricing.PRICES`), the GPU seconds logged. Two ways to use them:

- a **legacy story**: `IMAGE_CHAIN=runpod/t2i_flux2_klein,…` and
  `IMAGE_EDIT_CHAIN=runpod/edit_flux2_klein_multiref,…` in `.env` / Settings;
- a **v2 story** (the quality pipeline): the budget profile **`own_gpu`**
  ("Quality on your own GPU"), whose sheet/plate/prop/keyframe roles name the
  RunPod links first and fal behind them (skipped without `FAL_KEY`), the clips
  on `VIDEO_CHAIN`'s first link, up to $2 an episode. Pick it on the story
  (`generation_profile.budget_profile`, `story_create`'s `generation_profile`,
  or the story page).

Model files on the volume, in the worker's folders (`unet/` for diffusion
models, `clip/` for text encoders): `flux-2-klein-4b.safetensors`,
`qwen_3_4b.safetensors`, `flux2-vae.safetensors` — the templates' `requires`
lists are the source of truth, and `validate_template` names what is missing.

The LTX-2.5 clip templates (native audio) are added from a ComfyUI API export
of its own "LTX-2.5 Image to Video" template once the worker's ComfyUI carries
it; `deploy/runpod/worker-comfyui.Dockerfile` builds a worker on a newer
ComfyUI when the shipped one is too old.

## Tests

`tests/test_mcp_runpod_jobs.py` (the job client, fake transport),
`tests/test_mcp_director.py` (steps with the chat as writer, through the real
`call_json`) and `tests/test_mcp_server.py` (the tools through an in-process MCP
client, the real `concepts` step included; skipped when `fastmcp` is not
installed). Nothing leaves the machine; nothing is spent.
