# Plan 32 — the fruit drama as a product: a preset, a genre recipe, one look, a format, frozen voices on the worker, the whole flow from a Claude chat (DEC-315)

Base: `feat/comfy-tts-chatterbox` (plan 31). **Merge order: plan 31's PR first, then this one.**

## What changes

- **One call to start:** `story_create(language, seed_text, preset="fruit_drama")` sets the look, the `own_gpu` profile (pictures and clips on your RunPod GPU, $2 an episode), the fruit cast, the recipe and the format. `story_options` lists presets, budget profiles with their caps and formats.
- **The flow runs from the chat end to end:** the `style` step is exposed (cast and places no longer refuse), `story_make_episode(story_id, ep, stop_at_keyframes?)` makes one episode in one run (Claude still answers every writing prompt), `story_estimate(story_id, cast|episode|render|story)` says the cost before any paid step, `episode_sheet` shows every shot on one picture, `episode_export(max_mib)` makes a copy small enough for `comfy_download`.
- **The genre recipe** (`templates/recipes/fruit_drama.json`): -ito/-ita names (no brands, no plain first names, checked), a fixed cast of 5–8 with roles, the beats (recap ≤ 6 words from episode 2 → confrontation → peak → cliffhanger ≤ 40 words), the closing "Team X ou Team Y ?", the end card "Partie N demain", the voice direction, the guardrails (no sexist/racist trope, no sexualisation). Only a story whose `recipe` names it gets the text: every other story's prompts are byte-identical (a guard test pins nine of them).
- **One look:** `fruit_drama` is the Pixar-style 3D cartoon everywhere (the "whole fruit head at human head scale" rule kept). Locked styles untouched.
- **The format `fruit_drama_75s_v2`:** 60–90 s, 5 scenes on episode 1 (6 with the recap after), 1–2 shots of 5–10 s, one silent reaction shot, the end card drawn on a hard stop when the recipe asks, the hook's on-screen text burned.
- **Frozen voices on the worker:** the `runpod/tts_chatterbox` voice link in the app (`clipping/providers/tts.py`), one Gemini-made reference per character made at cast time and cloned on every line with a fixed seed (≈ $0.004 a line); `own_gpu` speaks through it, Gemini behind.
- **Talking mouths as a tested bet:** the `s2v_wan22` template (Wan 2.2 S2V on core nodes) and the worker's `audio_encoders` symlink. **Not in the episode yet**: one paid test line first (below).
- The skill `.claude/skills/fruit-drama-episode/SKILL.md` rewritten on the new tools; `docs/MCP.md`, `docs/AI_STORY.md`, CHANGELOG, DEC-315, A-202…A-210.

## Tests

Per stage in both envs (local and CI-like) plus the MCP tests in the venv, then one full local run of `tests/` at the end (numbers in `.claude/CHECKPOINT.md`). Goldens moved only for the look (dated comments). Render golden unchanged.

## Manual steps after the merge (in this order)

1. **Deploy** as usual (`rm -sfv` + `up -d --build`; the dashboard `dist` rebuilt for the new format in the wizard). Restart the MCP unit: `sudo systemctl restart rzdhop-story-mcp`.
2. **Replace the plugin skill copy** by hand: `~/.config/Claude/local-agent-mode-sessions/skills-plugin/*/*/skills/fruit-drama-episode/SKILL.md` ← `.claude/skills/fruit-drama-episode/SKILL.md`.
3. **Voices:** plan 31's steps if not done yet (the GHCR image, the image endpoint on `ghcr.io/rzdhop/worker-comfyui-tts:latest`, `fetch_weights.sh` on the dev pod). Optional: `RUNPOD_AUDIO_ENDPOINT_ID` in the A1's `.env` (else the image endpoint speaks).
4. **S2V weights on the volume** (dev pod): `sh docker/worker-comfyui-tts/fetch_weights_s2v.sh` (16.4 GB + 0.63 GB + 1.2 GB). Then rebuild the worker image (the GHCR workflow, the `audio_encoders` symlink is in the Dockerfile) and let the endpoint pull `:latest`.
5. **The S2V live test (the gate of stage 8):** from the chat, one line on one fruit keyframe: `comfy_submit(template="s2v_wan22", prompt="<one sentence>", image_path="<a 480x832 keyframe>", audio_path="<a line .wav>", seconds=5)`. Estimated cost: about $0.03–0.07 warm, plus about $0.10 for the first cold start (a guess, A-204). Look at the clip: does the mouth move with the French line on a cartoon fruit? Tell me "ça marche" or "non" — stage 8 (S2V in the episode's clips) starts only on a yes.
6. **The first fruit-drama story from the chat** with the skill: `story_create(preset="fruit_drama")` … `story_make_episode(1)`; say the estimates before the paid steps (A-205…A-210 are the checks).
