---
name: story-cast
description: Step 3 of an AI Story series on the showrunner connector. Use when the universe is locked, to write each character's sheet, make and lock their full-body picture (then turnaround and expressions), and cast and lock their voice from a take Rida picks. Costs money (images and casting clips).
---

# Story, step 3: the cast (GPU: images and casting clips)

Per character, in this order, each approved before the next: the sheet (free) → the canonical full-body picture
→ the turnaround and the expression grid → the voice, cast the stage-0 way (a short casting clip, Rida picks
the take, its line becomes the character's locked voice for the whole series).

<!-- rules:start -->
## Rules (every step, every story)

- **Ask, never assume.** Rida decides; you never fill a gap with a guess. At the start of every step, ask him the
  questions of the step's `## Ask first` that his messages and the story's files do not already answer — in one
  message, short, multiple choice when you can (your recommendation first, and he can always answer in his own
  words). A name, an age, a species, a look, a colour, a line, a place, a length, a number of episodes, a music, a
  budget: if Rida has not said it and no file of this story says it, ask. When he says "you decide", give your pick
  in one line and wait for his yes. Show what you will write before writing a file; ask before every lock and every
  paid batch. Nothing comes from another story or from an example unless he says so. When in doubt, ask.
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

- `01-universe.md` is locked and the cast of the chosen concept has no sheets yet, or a new character joins.
- Not to "fix" a locked character between episodes: if identity drifts, rebuild the pack with Rida, never
  re-roll shots to hide it.

## Read first

- `store_read 00-brief.md` (the cast, their wants) and `store_read 01-universe.md` (Medium, heads, proportions,
  palette, Forbidden).
- The proven sheets: `stories/faille-d-amour/02-cast/*/sheet.md` (≈ 70-word heads that held in batch a).

## Ask first

Per character, before writing the sheet (ask only what the brief and the universe do not already say):

- What is their name, age and gender, and their role in the story?
- What are they (which fruit / species), and what do people notice first (colours, outfit, one mark, one
  signature item)?
- Their personality in three words? What they want, what they fear, their secret (or shall you propose)?
- Their voice: what age, tone, pace and accent (regional or not)?
- How do they talk: formal or slang, a catchphrase, "vous" or "tu" in French?

Before each paid batch: how many candidates per picture (3–5 for the full body), and the most he wants to spend on
the cast. Before the casting reel: the line each character says (or approve yours).

## Template

**1. The sheet** — `02-cast/<id>/sheet.md` (skeleton `showrunner/prompts/character_sheet.md`; `<id>` lowercase,
`_` between words):

```
# <Name>

## Head
<Name>, a <who, in the universe's words>: <the head and face: shape, colours, eyes, brows, mouth, one permanent
mark>; <the body and outfit, colours named>, <one signature item>, <the hands rule of the universe>

## Voice (en)
<age, tone, pace, texture, in English: "a calm young man, dry and ironic, slightly amused, quick but composed">

## Voice (fr)
<the same in French>

## Colour
<the subtitle colour of their name, hex>

## Wants
## Fears
## Secret
## How they speak
<rhythm, words they use, what they never say>
## Signature item
```

`## Head` is 65–80 words, one sentence, no final period, every visible thing named once with its colour; it opens
every keyframe and clip prompt of this character, word for word. `## Voice (en)` is what the clip prompt says.

**2. The full body** — write its prompt (the full-body pattern of the guide above), show it to Rida, then 3–5
candidates (one seed each): `comfy_submit(story, "t2i_flux2_klein", {"prompt": <yours>, "seed": <k>, "width": 832,
"height": 1216}, dest="02-cast/<id>/candidates/full_body_c<k>")`. Rida picks ONE → `store_copy` it to
`02-cast/<id>/full_body.png` → `store_lock` with his words. It is never regenerated.

**3. Turnaround and expressions** — once the full body is locked: write both prompts (the guide's patterns) and
send each with `edit_flux2_klein_multiref`, 832×1216, `files={"ref1": "02-cast/<id>/full_body.png", "ref2": …,
"ref3": …, "ref4": …}` (the same locked file in the four slots), 2 candidates each, picked and locked as
`turnaround.png` / `emotions.png`. If the turnaround or the grid shows a different character, the full body is
not a good reference: say so; Rida decides whether to pick another full body.

**4. The voice (casting)** — a casting reel in `ep00`, one shot per character:

- a plate for the casting place if the story has none yet: `03-places/<place>/plate.md` with `## Setting` (one
  sentence of objects and light, no people in it);
- `ep00/shots.json`: `{"shots": [{"id": "s01", "seconds": 5, "place": "<place>", "characters": ["<id>"],
  "lines": [{"speaker": "<id>", "text": "<8–10 words in the story's language, said the way they speak>"}]}, …]}`
  (a 5 s clip holds 10 words at most);
- its keyframe: you write the prompt (the keyframe pattern, `faces_camera`) → 2 candidates with
  `edit_flux2_klein_multiref` and the locked full body as references, 704×1280, `dest="ep00/keyframes/candidates/
  s01_c<k>"` → Rida picks → `store_copy` to `ep00/keyframes/s01.png`;
- the clip: you write the prompt (the one-speaker pattern) → 3 seeds (11, 22, 33, as batch a) with
  `comfy_submit(story, "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": 5},
  files={"image": "ep00/keyframes/s01.png"}, episode=0, shot="s01")` → `comfy_fetch` each → `verify_take` each;
- Rida watches and listens (`file_download` each take) and picks the voice → `approve_take(…, note=<his words>)`
  → `voice_ref_from_take(story, 0, "s01", <take>, "<id>", note=<his words>)` → `file_download
  02-cast/<id>/voice_ref.wav` so he hears exactly what is locked.

Several characters can share one casting batch (one shot each); say the total count and cost once.

## Checklist

- [ ] Every sheet: a `# Name` title, a 65–80-word `## Head` in the positive, both voices, a colour.
- [ ] Nothing in a Head names what must not appear; the universe's `## Forbidden` is respected.
- [ ] Two characters are told apart at a glance (silhouette, main colour, signature item).
- [ ] Sheet locked (Rida's words) before its first picture; full body locked before turnaround/expressions.
- [ ] Every prompt written by you from the guide's patterns, the Medium and the Head word for word, shown to Rida.
- [ ] Count and cost said, Rida's go, for each batch (full bodies, turnaround + grid, casting keyframes,
      casting clips).
- [ ] The voice comes from a take Rida approved, checked by `verify_take` (its line heard, in time).
- [ ] `voice_ref.wav` is 2–10 s of that character alone, and Rida heard it.

## Gate question

Per character, in turn: "Is this <Name>?" (the sheet) · "Which one is <Name>: 1, 2, 3…?" (the full body) ·
"Is this <Name>'s voice for the whole series?" (the casting take).

## What to show Rida

The sheet in short (the Head, the voice, the want); every candidate picture (`comfy_fetch` shows it; `view_file`
again side by side) with your honest read of each (identity, hands, the universe kept or not) and the one you
would pick; every casting take as a file to play (`file_download`) with what the clip check heard. After each
batch, its real cost.

## Tools

- `store_read`, `store_write` — free: the brief, the universe, the sheets, plates and `ep00/shots.json`.
- `templates_list` — free: the templates, their sizes and the files they take.
- `comfy_submit` — COSTS MONEY: each image or clip, with the prompt you wrote, only after Rida's go.
- `comfy_fetch`, `comfy_jobs` — free: collect and see the results; what is still in the queue.
- `view_file` — free: look at a picture or a clip's contact sheet again.
- `store_copy`, `store_lock` — free: make the picked candidate the canonical file, lock it with Rida's words.
- `verify_take` — free: the clip check of a casting take.
- `approve_take` — free: Rida's pick of the casting take, his words as the note.
- `voice_ref_from_take` — free: cut and lock the voice from that approved take.
- `file_download` — free: hand Rida a take or the voice to play.
- `cost_ledger`, `runpod_health` — free: what was spent; whether workers are warm or the queue is long.

## Next

`story-script`: the season map and episode 1's script.
