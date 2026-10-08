---
name: story-shots
description: Step 5a of an AI Story series on the showrunner connector. Use when an episode's script is locked, to write its shot list (shots.json) and make, pick and lock one keyframe per shot. Costs money (keyframe images).
---

# Story, step 5a: the shot list and the keyframes (GPU: images)

The locked script becomes `epNN/shots.json`, one entry per clip. Each shot then gets its start picture: the
clip model keeps the keyframe's pose and framing, so the keyframe is drawn the way the shot must look.

<!-- rules:start -->
## Rules (every step, every story)

- **Roles.** You (Claude) write and direct in the chat; the `showrunner` connector's tools are your hands;
  Rida decides. Talk to Rida in plain words, in the language he writes in; no tool names, paths or JSON unless he
  asks. The story's files are in English, except the spoken lines, which are in the story's language (fr or en).
- **Money.** Every GPU job costs money (`comfy_submit`, `vc_clip`). Before each batch say what it makes, the
  number of jobs and the cost, then wait for Rida's explicit go for that batch. After it, report what it really
  cost (`cost_ledger`). Everything else is free and needs no go. Rough costs: one image ≈ $0.01 warm, ≈ $0.03 on
  a cold worker; one 5 s clip ≈ $0.03–0.05 warm, ≈ $0.13 cold (10 s ≈ double); a voice conversion ≈ $0.001 a line
  (+ ≈ $0.15 once if cold). A GPU queue of 20–35 minutes is normal and free: fetch again later.
- **Rida's gate.** Every picture and every clip is shown to Rida before it is used, and nothing goes on until he
  says so. `approve_take`, `store_lock` and `voice_ref_from_take` carry a note quoting his words. A locked file is
  never changed silently: `store_unlock` with the reason Rida gave.
- **Always moving pictures.** Every shot is a real clip: never a still, never a Ken Burns, never a slowed clip.
  A failed take is made again with a new seed and a note, never filled. No edge-tts, no voice laid over a clip, no
  LLM API call from the app: you are the writer.
- **Prompts are built, never typed.** `prompt_keyframe`, `prompt_clip` and `prompt_cast` build every prompt from
  the story's files through the tested templates; `comfy_submit(prompt_from="keyframe:<ep>:<shot>" |
  "clip:<ep>:<shot>" | "cast:<char>:<kind>")` sends exactly that. To change a prompt, change the story file it comes
  from (the universe, a sheet, a plate, `shots.json`) and build it again.
- **Words that reach a model.** A character's `## Head` is about 70 words (65–80), written "<Name>, a ...: ...",
  in the positive, no final period. Nothing that reaches a prompt names what must not appear (text, subtitles,
  captions, extra people): naming it draws it (the models ignore the negative prompt). Keyframes face the camera
  unless the three-quarter check has passed on this story.
- **One universe per story** (any universe, not fruit only): only what the story's own files say is assumed.
<!-- rules:end -->

## When to use

- `epNN/script.md` is locked and `epNN/shots.json` does not exist (or a shot must be redone with Rida).

## Read first

- `store_read epNN/script.md`, the sheets of the characters in it, the plates of its places.
- `store_read 01-universe.md` (`## Camera`).

## Template

`epNN/shots.json` (the fields the prompt tools and the assembly read):

```json
{
  "bgm": null,
  "hook": null,
  "card": null,
  "shots": [
    {"id": "s01", "seconds": 10, "place": "cafe", "characters": ["ana", "bo"],
     "lines": [{"speaker": "ana", "text": "…"}, {"speaker": "bo", "text": "…"}],
     "expression": "a tense and composed expression"},
    {"id": "s02", "seconds": 5, "place": "loft", "characters": ["bo"],
     "lines": [{"speaker": "bo", "text": "…"}], "framing": "faces_camera"},
    {"id": "s03", "seconds": 5, "place": "loft", "characters": ["ana"],
     "reaction": "the smile drops, the eyes harden"}
  ]
}
```

- `id`: `s01`, `s02`… in script order. `seconds`: 5 or 10 (the clip model sells nothing else).
- `characters`: who is in the picture, 1–3, in order; a speaking shot's speakers must all be in it.
- `lines`: in the order spoken, the script's exact words. No lines: a silent shot, with `reaction`.
- `framing` (one character in frame): `faces_camera` (the default). `three_quarter` only once its check has
  passed on this story (A-222: its first wording drew a stray person). Two or three characters are always drawn
  together, facing each other.
- `expression`: the face of the keyframe (a few words), else a tense and composed expression.
- `bgm`, `hook`, `card`: left `null` here, filled at the assembly (step 6).

Then, for each shot: `prompt_keyframe(story, N, "sNN")` (free) shows the prompt, the template (the
multi-reference edit when every character's full body is locked, else text to image), the files and the size;
and `prompt_clip(story, N, "sNN")` (free) checks the line budget now, before any picture.

The keyframes, after Rida's go: 2–3 candidates per shot,
`comfy_submit(story, <template it named>, {"seed": <k>}, files=<files it named>, prompt_from="keyframe:N:sNN",
dest="epNN/keyframes/candidates/sNN_c<k>")` → `comfy_fetch` → you look at each and propose one → Rida picks →
`store_copy` to `epNN/keyframes/sNN.png` → `store_lock` with his words.

## Checklist

- [ ] One shot per script block, same order, same words; every shot 5 or 10 s.
- [ ] Every shot's words fit its budget (`prompt_clip` → `budget.fits`); a line that does not fit is cut in the
      script with Rida, never sped up.
- [ ] Every character in frame has a locked sheet; every place has a plate.
- [ ] `three_quarter` only if its check passed; otherwise `faces_camera`.
- [ ] All the prompts built with the tools; count × candidates and the cost said; Rida's go.
- [ ] Each keyframe checked by you before Rida sees it: the right characters and only them, identity held
      (head, colours, outfit, signature item), the universe's look, hands, the framing of the shot.
- [ ] A keyframe that shows a person not in the shot, text, or a broken face is not proposed: a new seed.

## Gate question

"Keyframes for episode N: here is my pick for each shot — OK, or which one do we change?"

## What to show Rida

The shot list as a short table (shot, seconds, who, place, words/budget). Then the candidates shot by shot with
your read and your pick; he validates or picks another. Then what the batch cost.

## Tools

- `store_read`, `store_write` — free: the script, sheets, plates; `epNN/shots.json`.
- `prompt_keyframe`, `prompt_clip` — free: the built prompts, the template and files to use, the speech budget.
- `comfy_submit` — COSTS MONEY: one keyframe candidate per call, after Rida's go, with `prompt_from`.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `store_copy`, `store_lock` — free: the picked candidate becomes `epNN/keyframes/sNN.png`, locked.
- `cost_ledger`, `runpod_health` — free.

## Next

`story-clips`: one clip per shot, each checked and approved.
