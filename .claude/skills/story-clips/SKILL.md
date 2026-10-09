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
- **You write every prompt.** You are the writer and the director; the connector only makes the pictures, the
  clips and the sound, checks them and cuts the episode. Write each prompt in full from the story's files and the
  patterns that worked (the prompt guide in the cast, shots and clips steps), show it to Rida with the batch, and
  send it as `values.prompt` of `comfy_submit`.
- **Words that reach a model.** A character's `## Head` is about 70 words (65–80), written "<Name>, a ...: ...",
  in the positive, no final period. Nothing that reaches a prompt names what must not appear (text, subtitles,
  captions, extra people): naming it draws it (the models ignore the negative prompt). Keyframes face the camera
  unless the three-quarter check has passed on this story.
- **One universe per story** (any universe, not fruit only): only what the story's own files say is assumed.
<!-- rules:end -->

<!-- prompts:start -->
## Writing the prompts (you write every one; the server only makes the picture or the clip)

You write each prompt in full, show it to Rida with the batch's count and cost, and send it as `values.prompt` of
`comfy_submit`. The story's files are your material: `01-universe.md` `## Medium`, each `sheet.md` `## Head` and
`## Voice (en)`, each `03-places/<place>/plate.md` `## Setting`, the shot's lines. Write the prompt into the shot
too (`keyframe_prompt`, `clip_prompt` in `shots.json`) so the story keeps what was sent; the job journal keeps it as
well. The prompts are in English; only the quoted lines are in the story's language.

**What has worked (batch a, chosen by Rida 2026-10-08).** The patterns below are the prompts Rida preferred (take
s33 for one speaker, s22 for two or three), with one change since: the closing sentence. Keep their order and their
fixed sentences; change only what comes from this story's files. Departing from a pattern is fine when the story
needs it — say so to Rida and note what you changed and why, so a good result can be kept.

**Every prompt**

- Opens with the universe's `## Medium` word for word, then each character's `## Head` word for word (+ ".").
- Never names what must not appear: no "subtitles", "captions", "text", "no people", "nobody else in the room"
  (the models ignore the negative prompt and draw what they read). Ask for what you want instead.
- A clip prompt ends with exactly: `One continuous, clean cinematic shot from the first frame to the last.`
- A keyframe or cast image names nobody but the characters in it: never "someone off-screen", "a person beside
  the camera", "another" (A-222: "turned toward someone just off-screen" drew a stray human).

**Cast: the full body** (`t2i_flux2_klein`, 832×1216, 3–5 seeds)

```
<Medium> <Head>. A full-body character reference: the whole figure from head to feet, standing upright in a relaxed
neutral pose, arms loose at the sides, facing the camera, centred on a plain neutral light-grey background, flat even
studio light, only this one character, nobody else.
```

**Cast: turnaround and expressions** (`edit_flux2_klein_multiref`, 832×1216, the locked `full_body.png` in
`ref1`–`ref4`)

```
<Medium> A character turnaround sheet of <Head>. Four full-body views side by side on a plain light-grey background,
flat even studio light: front view, three-quarter view, side view, back view. The same character in every view,
same colours, same outfit and same proportions as the reference image.
```

```
<Medium> An expression sheet of <Head>: six head-and-shoulders portraits in a 3x2 grid on a plain light-grey
background, the same lighting and the same framing in each: joy, anger, doubt, love, thinking, sadness. The same
character in every portrait, same colours and same outfit as the reference image.
```

**Keyframe** (704×1280; `t2i_flux2_klein`, or `edit_flux2_klein_multiref` with the locked full bodies of the
characters in frame as references)

```
<Medium> <Head of each character in frame>. Setting: <Setting>. Medium close shot from the waist up, <framing>,
mouth closed, <expression>, soft cinematic light, vertical 9:16 framing, <who>.
```

- `<framing>`, one character: `<Name> faces the camera` (the default). The three-quarter wording
  `<Name> with the head turned three-quarters to the right, eyes looking past the right edge of the frame` only once
  it has passed its check on this story. Two or three characters: `<A> and <B> stand close together, facing each
  other mid-conversation` (three: `<A>, <B> and <C> stand …`).
- `<who>`: `only this one character, nobody else` · `only these two characters, nobody else` · `only these three
  characters, nobody else`.
- `<expression>`: a few words of the face (default `a tense and composed expression`).

**Clip, one speaker** (`ltx25_i2v_speech`, 5 s; the s33 pattern)

```
Use the provided start image as the first frame. <Medium> <Head>. Setting: <Setting>. <Name> talks to someone just
off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens and never looking into
it, as in a conversation scene of a drama, and says in <French|English>, with the voice of <Voice (en)>: "<line>"
The mouth moves naturally with every word, a small head tilt, a breath before and a beat of silence after the line.
Medium close-up, the camera holds still on the speaker, soft natural motion only. Audio: the clear voice close to
the microphone, quiet room tone, no music. One continuous, clean cinematic shot from the first frame to the last.
```

**Clip, two or three characters** (`ltx25_i2v_speech`, 10 s; the s22 pattern — multi-speaker clips preferred)

```
Use the provided start image as the first frame. <Medium> <Head A>. <Head B>. Setting: <Setting>. They speak in turn,
each one's mouth moving only on their own line, the other listening and reacting: <A> says in <language>, with the
voice of <Voice A>: "<line>" <B> says in <language>, with the voice of <Voice B>: "<line>" Medium two-shot, the
camera holds still. Audio: two distinct voices close to the microphone, quiet room tone, no music. One continuous,
clean cinematic shot from the first frame to the last.
```

**Clip, silent reaction** (`ltx25_i2v_speech`, 5 s)

```
Use the provided start image as the first frame. <Medium> <Head>. Setting: <Setting>. <Name> listens to someone just
off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens, and reacts in silence
with the eyes and brows: <what the face shows>; lips closed, a small breath, soft natural motion only. Medium
close-up, the camera holds still on the listener. Audio: quiet room tone only. One continuous, clean cinematic shot
from the first frame to the last.
```

**The speech budget.** About 2.4 words a second after a beat of silence: a 5 s clip says at most 10 words, a 10 s
clip at most 22 (all its lines together). A line that does not fit is cut in the script with Rida, never sped up.

**Before you send, check:** the Medium and every Head are word for word; the lines are the script's exact words,
in order; each speaker's voice is their sheet's `## Voice (en)`; the closing sentence is there (clips); nothing
unwanted is named; the size and seconds match the template; Rida saw the prompt and said go for the batch.
<!-- prompts:end -->

## When to use

- Every shot of `epNN/shots.json` has a locked `epNN/keyframes/sNN.png`.
- A single shot to redo (a new seed with a note, or a changed line after Rida's call).

## Read first

- `store_read epNN/shots.json` and `epNN/takes.json` (what exists, what is approved).
- `comfy_jobs(open_only=True)`: jobs already sent and not collected — collect them before sending more.

## Template

For each shot (a batch can cover the whole episode; say the count once):

1. Write the clip prompt (the one-speaker, two/three-speaker or silent pattern of the guide above) into the shot's
   `clip_prompt`; check its words against the budget.
2. After Rida's go: 2 seeds per shot,
   `comfy_submit(story, "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": <5|10>},
   files={"image": "epNN/keyframes/sNN.png"}, episode=N, shot="sNN")`. Each clip becomes the shot's next take
   (`epNN/clips/sNN_vK.mp4`).
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
- [ ] Every clip prompt written by you from its pattern (Medium and Heads word for word, the script's exact
      lines, each speaker's voice, the closing sentence); count × seeds and the cost said; Rida's go for this batch.
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
- `comfy_submit` — COSTS MONEY: one clip per call, with your prompt, after Rida's go.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `verify_take` — free: the clip check (this server's CPU, ≈ 35 s a clip).
- `vc_clip` — COSTS MONEY (tiny): re-voice a checked take with the locked voices; then `vc_fetch` (free).
- `file_download` — free: hand Rida the clip to play.
- `approve_take` — free: Rida's approval, his words as the note.
- `cost_ledger`, `runpod_health` — free.

## Next

`story-assemble`: the episode from the approved takes.
