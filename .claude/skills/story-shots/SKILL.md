---
name: story-shots
description: Step 5a of an AI Story series on the showrunner connector. Use when an episode's script is locked, to write its shot list (shots.json) and make, pick and lock one keyframe per shot. Costs money (keyframe images).
---

# Story, step 5a: the shot list and the keyframes (GPU: images)

The locked script becomes `epNN/shots.json`, one entry per clip. Each shot then gets its start picture: the
clip model keeps the keyframe's pose and framing, so the keyframe is drawn the way the shot must look.

<!-- rules:start -->
## Rules (every step, every story)

- **Ask, never assume.** Rida decides; you never fill a gap with a guess. At the start of every step, ask him the
  questions of the step's `## Ask first` that his messages and the story's files do not already answer — in one
  message, short, multiple choice when you can (the question tool if the chat has one; your recommendation
  first, and he can always answer in his own words). A name, an age, a species, a look, a colour, a line, a place, a length, a number of episodes, a music, a
  budget: if Rida has not said it and no file of this story says it, ask. When he says "you decide", give your pick
  in one line and wait for his yes. Show what you will write before writing a file; ask before every lock and every
  paid batch. Nothing comes from another story or from an example unless he says so. When in doubt, ask.
- **Roles.** You (Claude) write and direct in the chat; the `showrunner` connector's tools are your hands;
  Rida decides. Talk to Rida in plain words, in the language he writes in; no tool names, paths or JSON unless he
  asks. The story's files are in English, except the spoken lines, which are in the story's language (fr or en).
  Never real people, brands or copyrighted characters.
- **Money.** Every GPU job costs money (`comfy_submit`, `vc_clip`). Before each batch say what it makes, the
  number of jobs and the cost, then wait for Rida's explicit go for that batch. After it, report what it really
  cost (`cost_ledger`). Everything else is free and needs no go. Rough costs: one image ≈ $0.01 warm, ≈ $0.03 on
  a cold worker; one 5 s clip ≈ $0.03–0.05 warm, ≈ $0.13 cold (10 s ≈ double); a voice conversion ≈ $0.001 a line
  (+ ≈ $0.15 once if cold). A GPU queue of 20–35 minutes is normal and free: fetch again later.
- **Rida's gate.** Every picture and every clip is shown to Rida before it is used, and nothing goes on until he
  says so. Say honestly what a picture or a clip shows (identity, outfit, framing, defects): he decides on your
  description and his own eyes. `approve_take`, `store_lock` and `voice_ref_from_take` carry a note quoting his words. A locked file is
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

- `epNN/script.md` is locked and `epNN/shots.json` does not exist (or a shot must be redone with Rida).

## Read first

- `store_read epNN/script.md`, the sheets of the characters in it, the plates of its places.
- `store_read 01-universe.md` (`## Camera`).

## Ask first

Before the shot list and before any keyframe:

- The shot list as a table (shot, seconds, who, place, lines): OK, or what to change?
- Any shot he sees differently (closer, two characters instead of one, a silent reaction)?
- The keyframe prompts you wrote: OK to send?
- How many candidates per keyframe (2 or 3), and the most he wants to spend on this batch.

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
- `keyframe_prompt`, `clip_prompt`: the prompts you write (below), kept with the shot.

Then, for each shot, you write its keyframe prompt (the keyframe pattern of the guide above) and check its words
against the budget. Rida reads the prompts with the batch.

The keyframes, after Rida's go: 2–3 candidates per shot, 704×1280, with `edit_flux2_klein_multiref` and the locked
full bodies of the characters in frame as `ref1`–`ref4` (in order, repeated to fill the four slots; text to image
`t2i_flux2_klein` only for a character with no locked full body yet):
`comfy_submit(story, "edit_flux2_klein_multiref", {"prompt": <yours>, "seed": <k>, "width": 704, "height": 1280},
files={"ref1": "02-cast/<a>/full_body.png", …}, dest="epNN/keyframes/candidates/sNN_c<k>")` → `comfy_fetch` → you
look at each and propose one → Rida picks → `store_copy` to `epNN/keyframes/sNN.png` → `store_lock` with his words.

## Checklist

- [ ] One shot per script block, same order, same words; every shot 5 or 10 s.
- [ ] Every shot's words fit its budget (5 s ≤ 10 words, 10 s ≤ 22); a line that does not fit is cut in the
      script with Rida, never sped up.
- [ ] Every character in frame has a locked sheet; every place has a plate.
- [ ] `three_quarter` only if its check passed; otherwise `faces_camera`.
- [ ] Every keyframe prompt written by you from the pattern (Medium and Heads word for word, nobody else named),
      kept in `shots.json`; count × candidates and the cost said; Rida's go.
- [ ] Each keyframe checked by you before Rida sees it: the right characters and only them, identity held
      (head, colours, outfit, signature item), the universe's look, hands, the framing of the shot.
- [ ] A keyframe that shows a person not in the shot, text, or a broken face is not proposed: a new seed.

## Gate question

"Keyframes for episode N: here is my pick for each shot — OK, or which one do we change?"

## What to show Rida

The shot list as a short table (shot, seconds, who, place, words/budget) and the keyframe prompts. Then the candidates shot by shot with
your read and your pick; he validates or picks another. Then what the batch cost.

## Tools

- `store_read`, `store_write` — free: the script, sheets, plates; `epNN/shots.json`.
- `templates_list` — free: the image templates, their sizes and reference slots.
- `comfy_submit` — COSTS MONEY: one keyframe candidate per call, with your prompt, after Rida's go.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `store_copy`, `store_lock` — free: the picked candidate becomes `epNN/keyframes/sNN.png`, locked.
- `cost_ledger`, `runpod_health` — free.

## Next

`story-clips`: one clip per shot, each checked and approved.
