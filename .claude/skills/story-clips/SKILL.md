---
name: story-clips
description: Step 5b of an AI Story series on the showrunner connector. Use when an episode's keyframes are locked, to make each shot's clip (LTX-2.5, picture and voice together), check it, re-voice it with the locked voices, and get Rida's approval clip by clip. Costs money (clips, voice conversion).
---

# Story, step 5b: the clips (GPU: clips and voice conversion)

One clip per shot, made from its locked keyframe with the lines spoken in the clip itself (D7), checked by the
server, re-voiced with each speaker's locked voice (DEC-323), then seen and approved by Rida. Nothing is
assembled from a clip he did not approve.

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

- Every shot of `epNN/shots.json` has a locked `epNN/keyframes/sNN.png`.
- A single shot to redo (a new seed with a note, or a changed line after Rida's call).

## Read first

- `store_read epNN/shots.json` and `epNN/takes.json` (what exists, what is approved).
- `comfy_jobs(open_only=True)`: jobs already sent and not collected — collect them before sending more.

## Template

For each shot (a batch can cover the whole episode; say the count once):

1. `prompt_clip(story, N, "sNN")` — free: the prompt, the seconds, the budget, `keyframe_ready`.
2. After Rida's go: 2 seeds per shot,
   `comfy_submit(story, "ltx25_i2v_speech", {"seed": <s>}, files={"image": "epNN/keyframes/sNN.png"},
   prompt_from="clip:N:sNN")`. Each clip becomes the shot's next take (`epNN/clips/sNN_vK.mp4`).
3. `comfy_fetch(story, job, wait_s=240)` — the contact sheet and the numbers. A long queue is normal: fetch later.
4. `verify_take(story, N, "sNN", "vK")` — free: what was heard against the script. States: `ok` · `mismatch`
   (a line not heard, or out of order) · `late` (the last word too close to the end) · `no_speech`.
5. For an `ok` take Rida may keep: `vc_clip(story, N, "sNN", "vK")` (≈ $0.001 a line) → `vc_fetch(…)` gives a new
   take, the same picture with every line in its speaker's locked voice.
6. Rida watches it (`file_download epNN/clips/sNN_vK.mp4`) → `approve_take(story, N, "sNN", "vK", note=<his words>)`.

When a take fails:

- `mismatch` / `no_speech` / `late`, or Rida says no: a new seed, with a one-line note of why in
  `epNN/defects.md` (shot, take, what was wrong, what changed). Two failed seeds on one shot: stop and look at the
  cause with Rida (the line too long, the keyframe's framing, the place) instead of a third blind roll.
- A two- or three-speaker take where one line fails: split it into one shot per speaker (shot / reverse-shot) in
  the script and `shots.json` with Rida, then new keyframes for the new shots (D6) — not a blind re-roll.
- Never fill a failed shot with a still, a hold or a slowed clip.

## Checklist

- [ ] No open jobs left behind (`comfy_jobs open_only`) before a new batch.
- [ ] Count × seeds and the cost said; Rida's go for this batch.
- [ ] Every take checked with `verify_take` before Rida sees it; the result told in plain words.
- [ ] Every kept take re-voiced (`vc_clip`) once all its speakers have a locked voice.
- [ ] Your own look at each sheet first: identity held, mouths move only on their own lines, no extra person,
      no burned-in text, the universe kept, no camera drift.
- [ ] `approve_take` only after Rida said so, quoting him; one approved take per shot.
- [ ] Every defect written down in `epNN/defects.md`.

## Gate question

Per shot: "sNN: take vK — approve, another seed, or change the line?"

## What to show Rida

Per shot: the contact sheet, what the check heard ("both lines heard, in order, last word at 4.1 s of 5"), your
read of the picture, then the voiced take as a file to play. A short running table of the episode (shot →
approved take or what is pending). After each batch, its real cost.

## Tools

- `store_read`, `store_write` — free: `shots.json`, `takes.json`, `defects.md`.
- `prompt_clip` — free: the clip prompt and its budget.
- `comfy_submit` — COSTS MONEY: one clip per call, with `prompt_from="clip:N:sNN"`, after Rida's go.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `verify_take` — free: the clip check (this server's CPU, ≈ 35 s a clip).
- `vc_clip` — COSTS MONEY (tiny): re-voice a checked take with the locked voices; then `vc_fetch` (free).
- `file_download` — free: hand Rida the clip to play.
- `approve_take` — free: Rida's approval, his words as the note.
- `cost_ledger`, `runpod_health` — free.

## Next

`story-assemble`: the episode from the approved takes.
