# Plan 31 — A text-to-speech route on the RunPod ComfyUI worker (Chatterbox Multilingual, French), and episode 1 of "Accès refusé" voiced through it

Date: 2026-10-06. Branch `feat/comfy-tts-chatterbox` (one commit ahead of main: `tools/render_ep01.py`).
Status: PLAN, awaiting the human's answers to the open questions and the "Go".

## Goal

Everything additive: a worker image that adds the Chatterbox Multilingual node to the
image the endpoints already run and hands audio back like images; a `tts_chatterbox`
workflow template; the MCP server submitting a reference voice, collecting `.flac`/`.wav`
into `dest`, and reporting duration and size; four frozen reference voices made once;
the 18 lines of episode 1 voiced through the route and mixed by `render_ep01.py` without
Gemini.

## What the exploration found (the facts the plan rests on)

- The MCP's job client (`mcp_server/runpod_jobs.py`) reads **only** `output.images`
  (`OUTPUT_KEY`, DEC-310); the worker's handler collects only the `images` key of each
  node output and logs the rest as "unhandled" — SaveAudio's `audio` key is dropped.
- Two endpoints exist: video `RUNPOD_COMFY_ENDPOINT_ID` (e14bceyj7rrdxl, $3.49/h, "RTX PRO
  6000 first") and image `RUNPOD_IMAGE_ENDPOINT_ID` (aq6qg1pykxa2st, $1.58/h — the RTX 5090
  figure of the brief, $0.00044/s). `Settings.KINDS = ("image", "video")`.
- The repo's own worker Dockerfile (`deploy/runpod/worker-comfyui.Dockerfile`) is pinned to
  `runpod/worker-comfyui:5.10.0-base-cuda12.8.1`; the klein templates were verified on
  worker-comfyui 5.10.0 / ComfyUI 0.34.0 (8f93af4). `5.5.0-base` (the brief) is CUDA
  12.6 — a Blackwell GPU (5090, RTX PRO 6000) needs CUDA >= 12.8.
- `render_template` substitutes `{{x}}` only for `KNOWN_PLACEHOLDERS`; ints are typed by
  `TYPED`. `tests/test_comfyui_video.py::test_every_shipped_workflow_parses_and_uses_only_known_placeholders`
  globs every template: non-empty `requires` whose `graph[node].inputs[field] == file`,
  placeholders ⊆ `KNOWN_PLACEHOLDERS`.
- Node pack: `filliptm/ComfyUI_Fill-ChatterBox` @ `f7d7a16187430abcaf91a3039b9c83aa9960816a`
  (v1.0.5, 2026-08-24): one node `FL_ChatterboxMultilingualTTS` (inputs `text`,
  `language` = `"French (fr)"`, `exaggeration`, `cfg_weight`, `temperature`,
  `repetition_penalty`, `min_p`, `top_p`, `seed`, optional `audio_prompt` AUDIO ≥ 6 s);
  Chatterbox vendored (no torch/transformers pin); README says MIT but **no LICENSE
  file**. Weights: `ResembleAI/chatterbox` (MIT), six files ≈ 3.2 GB, read from
  `<ComfyUI>/models/chatterbox/chatterbox_multilingual/` (not via extra_model_paths);
  the node downloads them itself when missing.
- Core nodes: `LoadAudio` (input `audio` = a file name in `/comfyui/input`), `SaveAudio`
  (FLAC, history key `audio: [{filename, subfolder, type}]`).
- `worker-comfyui` is AGPL-3.0; the handler is `/handler.py`; Python packages live in
  `/opt/venv`; `comfy-node-install` wraps the registry (1.0.4 there, wants resemble-perth).
- `render_ep01.py`: `spoken_lines()` → `[{id, speaker, text, start, room}]` (18 lines:
  ANANAS 3, RIDA 7, MARIE-JEANNE 7, INÈS 1); `voice_lines()` writes
  `outputs/acces_refuse/ep01/voices/lNN.wav`; `voice_filters()` consumes them; Gemini via
  stdlib REST with `gemini-3.8-flash-lite-tts`. `edge-tts` and `google-genai` are already
  dependencies; stdlib `urllib` is the convention (no requests).

## Decisions proposed (DEC-314 once approved)

1. **Base image** `runpod/worker-comfyui:5.10.0-base-cuda12.8.1` as a build ARG (the
   repo's existing pin), not `5.5.0-base`: the endpoints' GPUs need CUDA 12.8.
2. **Weights on the network volume, not baked**: the image symlinks
   `/comfyui/models/chatterbox → /runpod-volume/models/chatterbox`; the node's own
   first-run download lands there once (≈ 3.2 GB, ~1–2 min of one job), or the human
   pre-fetches from the dev pod with `docker/worker-comfyui-tts/fetch_weights.sh`. Keeps
   the image at the base's size, the CI build light (no 3.2 GB in Actions), and the
   weights where every other model lives.
3. **Handler patch**: a build-time Python script (`patch_handler.py`) that rewrites the
   output loop to collect `images` and `audio` alike (same item shape, base64 or S3) and
   returns them under their own keys; it fails the build if its anchors are not found.
   The MCP reads both keys (`images` first).
4. **An `audio` job kind** in the MCP: `RUNPOD_AUDIO_ENDPOINT_ID` / `_API_KEY` /
   `_GPU_USD_PER_HOUR`; empty = the image endpoint, else the video one (whichever the human
   re-points at the TTS image).
5. **Placeholders** `audio_path` (uploaded like `image_path`, lands in `LoadAudio`),
   `exaggeration`, `cfg_weight` (floats); `language` fixed to French in this template.
6. **`requires` entries with an empty `field`** mean "the node loads this file from `dir`
   itself" (the six Chatterbox files); validation and the schema test skip the graph check
   for them.
7. Reference voices: Gemini prebuilt (REST `generateContent`, the call `render_ep01.py`
   already makes on this host) first, `edge-tts` fr-FR second; 10–15 s, mono 24 kHz 16-bit
   WAV; uploaded with every job (no copy to the volume needed).
8. Episode voicing: one job per line, sequential by default (one warm worker), a fixed
   seed per speaker, FLAC converted to `lNN.wav` with ffmpeg; the estimate printed and a
   `--go` required before any GPU second.

## Stages

Stage 0 — CHECKPOINT: Tier-1 baseline on the touched-area selection; CHECKPOINT.md header.

Stage 1 — Template engine + `tts_chatterbox.json` (Sonnet). Files:
`clipping/providers/local_comfyui.py` (KNOWN_PLACEHOLDERS, TYPED floats, implicit
`requires`), `clipping/aistory/templates/workflows/tts_chatterbox.json`,
`tests/test_comfyui_video.py` (the schema test learns the implicit entry),
`tests/test_tts_chatterbox_template.py` (render against FakeComfy, validation names the
missing node and files). Risk: low. Verify: the two test files + `test_local_comfyui.py`.
Rollback: revert the commit. Contract at risk: byte-identical rendering of the six
existing templates.

Stage 2 — Worker image + CI (Sonnet; the riskiest stage — unverifiable offline). Files:
`docker/worker-comfyui-tts/{Dockerfile,patch_handler.py,fetch_weights.sh,README.md}`,
`.github/workflows/worker-tts-image.yml`, `tests/test_worker_tts_handler_patch.py` (the
patch applied to a fixture of the 5.10.0 handler loop collects both keys, refuses an
unknown handler). Verify: the test; the build itself is the human's (CI). Rollback:
revert; the endpoints keep their image. Contract: `ci.yml` untouched.

Stage 3 — MCP audio end to end (Opus: cross-cutting over config/jobs/media/server).
Files: `mcp_server/config.py`, `runpod_jobs.py`, `media.py`, `server.py`,
`comfy_download.py` (`.flac` mime), `.env.example`, `docs/MCP.md`,
`tests/test_mcp_runpod_jobs.py`, `tests/test_mcp_server.py`, `tests/test_env_template.py`
if it pins names. Verify: the five `test_mcp_*` files + `test_env_template.py` +
`test_runpod_comfyui.py`. Rollback: revert. Contract: the three named MCP tests; image/video
jobs byte-identical (naming, S3 refusal, "without a file" error).

Stage 4 — Tools (Sonnet). Files: `tools/make_voice_refs.py`, `tools/voice_ep01_comfy.py`,
`tools/render_ep01.py` (`--use-existing-voices`, `--voices-dir`), tests
`tests/test_voice_tools.py` (texts and fallback without network; 18 planned jobs with
per-speaker seeds against a fake transport; flac→wav call; the flag skips Gemini and
finds the files). Verify: the tests. Rollback: revert. Contract: `render_ep01.py`'s default
path unchanged (UNVERIFIED by any test today → covered by an argparse/flow test).

Stage 5 — Docs + close-out (Haiku for the mechanical appends): CHANGELOG, DEC-314,
A-197…, action log, CHECKPOINT, the PR with the manual checklist.

## Rejected alternatives

- `diodiogod/TTS-Audio-Suite`: MIT with a LICENSE file, but `transformers>=5.3`, a custom
  installer and bitsandbytes/modelscope — a high conflict risk against the base image.
- A home-made node on the `chatterbox-tts` pip package: it pins `torch==2.6.0`.
- Baking the weights: +3.2 GB per rebuild in Actions (runner disk), a 14 GB image to pull
  on every new host, and the weights would be the only model not on the volume.
- Returning audio inside the `images` list: no MCP change, but every consumer would have
  to sniff extensions; a key of its own is clearer and the MCP reads both.

## DECISIONS.md check

Touches DEC-310 (the `images`-only collection, now extended for `audio`; the link = template
name shape kept), DEC-312/313 (the MCP tools — additive; the unit's `.env` loading gains
three optional names), DEC-281 (voice references: synthetic voices, no consent statement
needed — recorded). No conflicts.

## Riskiest stage

Stage 2: the vendored Chatterbox against the base image's `transformers`, the handler
anchors at 5.10.0, the Actions runner's disk for an 11 GB base — none testable here.
