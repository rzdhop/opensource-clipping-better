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
| `RUNPOD_AUDIO_ENDPOINT_ID`, `RUNPOD_AUDIO_API_KEY`, `RUNPOD_AUDIO_GPU_USD_PER_HOUR` | the **voice** endpoint (the `tts_chatterbox` template, see "Voice lines"); empty = the image endpoint says the lines (then the video one), once it runs the TTS worker image |
| `MCP_TOKEN` | the secret: the bearer token Claude Code sends, and what the connector's login page asks for; empty = no auth, keep it on localhost |
| `MCP_PUBLIC_URL` | the address clients reach the server at (the Funnel URL); set, the server is also the OAuth authorization server the claude.ai connector needs |
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
- **claude.ai web / desktop / mobile** (Settings → Connectors → Add custom connector, URL
  `https://<node>.<tailnet>.ts.net/mcp`, no client id/secret): the connector speaks
  OAuth, so set `MCP_PUBLIC_URL=https://<node>.<tailnet>.ts.net` and the server is
  its own authorization server (`mcp_server/auth.py`): dynamic client
  registration and PKCE as the connector expects, and an **authorize step that
  shows a login page asking for `MCP_TOKEN`** — nobody who finds the URL gets a
  token without it. Clients and tokens are kept in `outputs/mcp/oauth.json`
  (mode 600), so a restart does not log the connector out.

A systemd unit for the server is in `deploy/rzdhop-story-mcp.service` (the
repo's `.venv`, `python -m mcp_server` from the repo, `Restart=always`, logs in
the journal under `rzdhop-story-mcp`):

```bash
sudo cp deploy/rzdhop-story-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rzdhop-story-mcp
journalctl -u rzdhop-story-mcp -f
```

The unit carries no `EnvironmentFile=`: the server loads the repo's `.env`
itself (python-dotenv), and that file has inline `# comments` after some
values, which dotenv strips and systemd would keep as part of the value. The
address stays `MCP_HOST`/`MCP_PORT` from `.env`, else `127.0.0.1:8787`, so the
Funnel mapping above keeps working unchanged.

## The tools (stage 1)

| Tool | Cost | Does |
|---|---|---|
| `runpod_health(kind?)` | free | workers ready/idle/running/throttled and queued jobs per endpoint |
| `templates_list()` | free | the `templates/workflows/*.json` this server can submit: task (t2i, edit, i2v, tts), endpoint kind (image, video, audio), values, clip lengths, model files |
| `comfy_submit(template, prompt, …)` | **GPU seconds** | one `POST /run`; returns the `job_id` at once. Several submits run in parallel on the endpoint's workers. Arguments: `negative`, `seed`, `width`/`height`, `seconds` (video), `image_path` (i2v keyframe), `ref_paths` (edit references, up to 4), `audio_path` (tts: the reference voice, a 6-30 s WAV), `exaggeration`/`cfg_weight` (tts knobs, 0.5 each by default), `name` (file stem), `dest` (folder under `outputs/`), `note` |
| `comfy_status(job_id)` | free | one `GET /status`; a job that ended is settled: its files are written to `dest`, its GPU seconds and dollars kept, or its error |
| `comfy_fetch(job_id, wait_s=240)` | free | waits (bounded) and shows the result: an image as a thumbnail, a clip as a contact sheet of 8 frames plus duration, size and whether it has sound, a voice line as its duration, sample rate and size (`comfy_download` hands the file over) |
| `comfy_cancel(job_id)` | free | cancels a queued or running job |
| `comfy_jobs(limit, refresh)` | free | the journal, newest first; `refresh` asks RunPod about the unfinished ones |
| `cost_ledger(since?)` | free | GPU seconds and dollars of the finished jobs, per kind |
| `view_file(path)` | free | look at any image or clip under `outputs/` (or the repo) |
| `comfy_download(path, max_mib=25)` | free | the file itself (base64 embedded resource) so the client can save it and hand it to the user; `view_file` and `comfy_fetch` only show previews. Refused outside `outputs/` and the repo, and over `max_mib` (ceiling 50): bigger files travel by scp |
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
| `story_list`, `story_create(language, seed_text?, style?, episode_format?, generation_profile?, preset?)`, `story_options` | the store and its catalogue (presets, styles, episode formats, budget profiles, steps); `preset="fruit_drama"` is the plan 32 shortcut below |
| `story_get(story_id)` | the story at a glance: bible, approvals, style lock, season, knowledge, entity summaries with what each lacks, episodes, recent runs |
| `story_doc`, `story_entities`, `story_entity`, `episode_get`, `episode_doc` | the full documents |
| `story_step_start(story_id, step, ep?, params?)` | runs a step; returns its first event: `waiting` with a `pending` prompt, `done` with the result, `failed` with the error, or `running` |
| `story_step_answer(handle, answer)` | the chat's JSON answer to a pending prompt; returns the next event (the validator's refusal comes back as the same prompt with the reason under it) |
| `story_step_status`, `story_step_cancel`, `story_runs` | follow, stop, list runs (one per story at a time) |
| `story_make_episode(story_id, episode, stop_at_keyframes?, wait_s?)`, `story_estimate(story_id, what, episode?)` | one episode in one run; what a step would cost, in dollars, before it starts (plan 32, below) |
| `episode_sheet(story_id, episode, columns?, max_px?)`, `episode_export(story_id, episode, max_mib?)` | look at the whole episode on one picture; a copy of the video small enough to download (plan 32, below) |
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

## Voice lines on RunPod (plan 31)

`tts_chatterbox` (task `tts`, kind `audio`) says one line in French with
Chatterbox Multilingual (ResembleAI, MIT): core `LoadAudio` reads the reference
voice, the `FL_ChatterboxMultilingualTTS` node of `ComfyUI_Fill-ChatterBox`
speaks, core `SaveAudio` writes a FLAC. It needs the worker image of
[`docker/worker-comfyui-tts/`](../docker/worker-comfyui-tts/README.md): the base
image has neither the node nor a handler that returns audio (the stock one
collects only `images`; the patched one returns `audio` the same way, under its
own key, and this server reads both). The six weight files live on the network
volume under `models/chatterbox/chatterbox_multilingual/` (the template's
`requires` entries, with an empty `field`: the node loads them itself).

- `comfy_submit(template="tts_chatterbox", prompt="<the line>", audio_path="<ref>.wav",
  seed=<one per voice>, name="l03", dest="acces_refuse/ep01/voices")` uploads the
  reference like a keyframe (a data URL in the worker's `images` list) and the
  FLAC lands in `dest` as `l03.flac`; `comfy_fetch` reports its duration.
- Which endpoint: `RUNPOD_AUDIO_ENDPOINT_ID` when set, else the image endpoint,
  else the video one — point the chosen one at `ghcr.io/rzdhop/worker-comfyui-tts:latest`
  (the image also runs every existing image and video template). The ledger bills
  audio at that endpoint's rate (`RUNPOD_AUDIO_GPU_USD_PER_HOUR` when it has its own).
- Cost: a line is about 8 GPU-seconds warm (≈ $0.004 on the RTX 5090 flex price);
  a cold worker loads 3.2 GB first (≈ 90 s, ≈ $0.04), so send the lines of an
  episode one after the other rather than all at once. The first job ever
  downloads the weights onto the volume (≈ 2 min) unless
  `docker/worker-comfyui-tts/fetch_weights.sh` ran on the dev pod.
- **From the app (plan 32).** The same template is also a voice link of the
  app's own TTS chain: `runpod/tts_chatterbox`
  (`clipping/providers/tts.py`, `RunPodTtsAdapter`). A story that speaks on it
  gives each character one frozen reference recording; every line is cloned from
  it on the worker, with the character's fixed seed (CRC32 of its id, so the
  same voice every line and every episode). The adapter uploads the reference
  `.wav` like a keyframe, queues the job on the audio endpoint (same three
  `RUNPOD_AUDIO_*` variables as above, forwarded by `docker-compose.yml`),
  follows and journals it like a clip, turns the FLAC it gets back into a WAV and
  writes the line's timing file. The `own_gpu` budget profile's TTS chain is
  `runpod/tts_chatterbox`, then `gemini/flash-lite-tts` as the fallback; the price
  row is about $0.004 a line (a few GPU-seconds warm; not measured, a high figure
  is kept), and what RunPod really billed is logged per line. The references are
  made at cast time, see "The voices on the worker" below.
- The tools around it: `tools/make_voice_refs.py` (the four frozen reference
  voices of "Accès refusé"), `tools/voice_ep01_comfy.py` (the 18 lines of
  episode 1 through this template), `tools/render_ep01.py --use-existing-voices`.

## The fruit drama in a handful of calls (plan 32)

Before plan 32 a story could not leave the bible from the chat (no `style` step,
no way to price a step, no preset), and the fruit-drama skill drove the GPU by
hand from a shell. Now one episode of a talking-fruit drama, in a Pixar-style
3D look, goes from a sentence to a finished video through the tools of this
page, with Claude writing every document and the human's own RunPod GPU making
the pictures, clips and voices. The skill that drives it is
[`.claude/skills/fruit-drama-episode/SKILL.md`](../.claude/skills/fruit-drama-episode/SKILL.md)
(it lives in the repository; the copy Claude Desktop loads is replaced by hand).

### The flow

| # | Call | Cost | What happens |
|---|---|---|---|
| 1 | `story_create(language, seed_text, preset="fruit_drama")`, `story_get(story_id)` | free | the story, with the preset's look, profile, recipe and episode format; `story_get` shows its `recipe` |
| 2 | `story_step_start(story_id, "concepts")`, answers through `story_step_answer`, `story_choose_concept` | free | concept cards, one chosen |
| 3 | `story_step_start(story_id, "bible")`, `story_approve(story_id, "bible")` | free | the bible |
| 4 | `story_step_start(story_id, "style")`, `story_approve(story_id, "style")` | free | builds the look from the preset (no writing, no picture); cast and places refuse until it is approved |
| 5 | `story_estimate(story_id, "cast")`, `story_step_start(story_id, "cast")`, `story_approve_all(story_id, "cast")` | pictures | the characters; each answer carries a voice pick and a sample text (below) |
| 6 | `story_step_start(story_id, "places_proposal")`, `"places"`, `story_approve_all(story_id, "places")` | pictures | places and props |
| 7 | `story_step_start(story_id, "season", params={"episodes": n})`, `story_approve(story_id, "season")` | free | the season arc |
| 8 | `story_estimate(story_id, "episode", episode=1)` | free | the figure in dollars, before any paid step |
| 9 | `story_make_episode(story_id, 1)` | pictures, clips, voices | the episode in one run |
| 10 | `episode_sheet(story_id, 1)`, `episode_export(story_id, 1)`, `comfy_download(<path>)` | free | look at it; get the video |

Claude answers every writing prompt of steps 2 to 9 itself, with
`story_step_answer(handle, answer)`; nothing is billed for the writing.

### The new tools

| Tool | Does |
|---|---|
| `story_create(..., preset?)` | `preset` is an id of `story_options().presets`. What the caller names (style, episode format, `generation_profile` keys) wins over the preset's, key by key. An unknown preset is refused with the list. |
| `story_options()` | now also lists `presets` (id, label, summary, what each sets, its values), `budget_profiles` (id, label, `cap_usd`) and `episode_formats` as objects (id, label, `window_s`, `target_s`, `scenes`). |
| `story_step_start(story_id, step, ep?, params?, wait_s=25)` | the docstring now lists every step and its params. New steps: `style` (params `template_id`, `overrides`, `consistency_mode`; runs on the server, no writer, no cost), `fast-track` (one episode, `ep`; params `storyboard`, `stop_at_keyframes`, `stop_on_script_issues`) and `story-fast-track` (an agent-mode story from its idea to episode 1). |
| `story_make_episode(story_id, episode, stop_at_keyframes=False, wait_s=25)` | the short form of `fast-track` for one episode: script, storyboard, keyframes, clips, render and metadata pack in ONE run instead of eight starts. Claude still answers every prompt. The run approves the script, storyboard and assets itself when they pass their checks; by default it approves the keyframes too (one the check still flags is kept with its warning and named at the end). With `stop_at_keyframes=true` it stops there: look at the sheet, `story_approve(story_id, "keyframes:<n>")`, call it again. It checks the money before the clips; any stop keeps what was made and the next call resumes. |
| `story_estimate(story_id, what, episode?)` | free, calls nothing. `what` is `cast`, `episode` (needs `episode`), `render` (free, needs `episode`) or `story` (the one-run story). Answers `{what, episode, ready, est_usd, message, details}`; when the story is not far enough along `ready` is false and the message says what comes first. |
| `episode_sheet(story_id, episode, columns=4, max_px=1600)` | one picture of the whole episode: every shot as a tile, in order. A shot with a clip shows a frame of the clip (at 1 s when the clip lasts 2 s or more), one without shows its keyframe, one with neither is a grey tile; each tile says its shot id and which one it is. Saved as `episode_sheet.png` in the episode's folder. Refused while there is no storyboard. |
| `episode_export(story_id, episode, max_mib=25)` | the finished `episode_final.mp4` as is when it is already under `max_mib` (at most 50, the `comfy_download` ceiling); otherwise re-encoded in steps of lower quality (CRF 23, 26, 28, 30, 32; same picture size, sound untouched) into `episode_share.mp4` until it fits. Answers `{path, size_mib, crf, download_hint, message}`; call `comfy_download` with the path it names. The original is never touched. |
| `story_get(story_id)` | now carries the story's `recipe`. |

The writer is `chat/claude`; the server sets `STORY_CHAT_WRITER` so the key
checks a step makes before writing count the conversation as a keyed writer.

### Presets

`fruit_drama` (`clipping/aistory/presets.py`) sets, in one word:

- the look: the `fruit_drama` style, a Pixar-style 3D cartoon;
- how pictures and clips are made: the v2 quality profile on the
  **`own_gpu`** budget profile (RunPod first, fal behind; cap **$2 an episode**),
  reference images, universe `fruits`, **agent mode** (the mode the one-run
  story needs; it changes nothing else for a story driven step by step);
- the recipe: `fruit_drama`;
- the episode format: `fruit_drama_75s_v2` (60 to 90 s, 4 to 6 scenes; plan 32
  stage 4).

### The recipe

A genre recipe is a record (`clipping/aistory/templates/recipes/fruit_drama.json`,
read by `clipping/aistory/recipes.py`) that the writers read through one gate:
the story's `recipe` field. A story with no recipe gets byte-identical prompts
to before plan 32. The `fruit_drama` recipe says:

- **names**: a French telenovela pun on the character's own species, `-ito/-ita`
  (Fraisita, Bananito, Citronello, Avocadina, Kiwito, Mangualdine); one species
  per character; no brand, no plain human first name (the concepts step checks
  it and sends the reason back, so Claude renames and answers again; a first
  name the idea itself gives is kept);
- **the cast**: fixed, 5 to 8 recurring characters, each with a role (matriarch,
  villain, schemer, innocent, heir, best friend, newcomer);
- **the beats**: a recap of at most 6 words (from episode 2), the confrontation,
  the peak, a cliffhanger of at most 40 words; one conflict an episode;
- **the closing question** "Team X ou Team Y ?" in the next-episode teaser and in
  the pinned comment, and the **end card** "Partie N demain" (1.5 s);
- **the voice direction** (over-acted telenovela, crisp and quick) and the
  **guardrails** (no sexist or racist trope, no sexualisation, nobody judged by
  body, looks or love life); the posting cadence (one a day) is a note.

It is injected into the writers' set-up block, the concept's names and cast ask,
the script's shape line, the teaser ask and the publication pack's line.
`docs/AI_STORY.md`, "The fruit drama product", has the detail.

### The voices on the worker

For a story on `own_gpu` (or following the `fruit_drama` recipe), each
character's text answer carries two more fields: `voice_pick` (one of the Gemini
voices of `templates/voices.json`) and `voice_sample_text` (25 to 40 words, about
12 seconds, in character, in the story's language). At cast time the app speaks
that text once with the picked Gemini voice (one Gemini call per character) and
keeps it as the character's `voice_reference.wav` (mono, 24 kHz, 16-bit, 5 to
30 s). That recording is frozen: a rewritten text never changes the voice. Every
line afterwards is cloned from it on the RunPod worker (`runpod/tts_chatterbox`)
with a fixed seed per character (CRC32 of the character id), at about $0.004 a
line. The references are synthetic voices only (DEC-281): never a real person's.
Without a Gemini key the pick and the text are kept and the reference is made
once a key is there; without a RunPod key, the voice falls back to the Gemini
voice picked. Code: `clipping/aistory/voice_clone.py`, `voice_reference.py`,
`steps/cast.py`, `clipping/providers/tts.py`.

### Talking mouths: the S2V template and its gate

`s2v_wan22` (task `s2v`, kind `video`, `verified_live: false`; Wan 2.2 S2V 14B
fp8 on core ComfyUI nodes only) takes a keyframe (`image_path`) and a spoken
line (`audio_path`, a WAV) and makes a clip of up to 5 s at 480x832 and 16 fps
in which the mouth follows the voice. It needs the `audio_encoders` symlink of
`docker/worker-comfyui-tts` and three weight files on the volume
(`docker/worker-comfyui-tts/fetch_weights_s2v.sh`; its README has the sizes).
It is **not in the episode's clips step**: nothing says yet how it looks on a
cartoon fruit or in French, and nothing was timed. The gate is one paid test line
the human runs on purpose on a fruit keyframe:

```
comfy_submit(template="s2v_wan22", prompt="<a motion sentence>",
             image_path="<keyframe>", audio_path="<line>.wav", seconds=5)
```

Only if that looks right is S2V wired into the clips step (plan 32 stage 8).

### What it needs

`RUNPOD_API_KEY` and the endpoint ids (images, videos, and `RUNPOD_AUDIO_*` for
the voices), plus a Gemini key for the voice references. The money gates are the
app's own (`ALLOW_PAID`, the per-episode cap of the budget profile); a refusal
names the figure.

## Tests

`tests/test_mcp_runpod_jobs.py` (the job client, fake transport, a voice line
among the jobs), `tests/test_tts_chatterbox_template.py` (the TTS template renders
and validates), `tests/test_worker_tts_handler_patch.py` (the worker's handler
patch),
`tests/test_mcp_director.py` (steps with the chat as writer, through the real
`call_json`, the `style` step included) and `tests/test_mcp_server.py` (the tools
through an in-process MCP client, the real `concepts` step included; plan 32 adds
`story_estimate`, `story_make_episode`, `story_create(preset=...)`,
`episode_sheet` and `episode_export`; skipped when `fastmcp` is not installed).
Plan 32 also adds `tests/test_story_presets.py` (the presets and their merge),
`tests/test_story_recipes.py` (the recipe record, its gate and the writers'
prompts), `tests/test_tts_runpod.py` (the app-side voice adapter),
`tests/test_story_voices.py` and `tests/test_story_cast_steps.py` (the frozen
references), and `tests/test_s2v_wan22_template.py` (the S2V template renders and
validates). Nothing leaves the machine; nothing is spent.
