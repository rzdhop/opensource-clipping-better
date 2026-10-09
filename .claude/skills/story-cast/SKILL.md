---
name: story-cast
description: Step 3 of an AI Story series on the showrunner connector. Use when the universe is locked, to propose every character's sheet for Rida to correct, then make their pictures and voices on the GPU without stopping, lock them, and present the whole cast with locked voices. Costs money (images and casting clips, a few cents each).
---

# Story, step 3: the cast (GPU: images and casting clips)

Two moments for Rida: he corrects the sheets you propose (gate 3), then he meets the finished cast — every character's
picture with their locked voice (gate 4). Between the two you make the pictures and the voices yourself on the GPU,
choose the best, lock them, and say what it cost.

<!-- rules:start -->
## Rules (every step, every story)

- **Propose, Rida corrects.** You never decide silently and you do not quiz him. In the writing steps (concept,
  universe, cast sheets, script) you write a complete proposal from what he said and from what he already approved
  in earlier stories, and he corrects it or adds what he wants. Every choice you made is visible in the proposal, so
  he can change it. Ask a question only when nothing he said or approved gives you a basis (then one short message,
  multiple choice, your recommendation first). Nothing comes from another story unless it was approved there or he
  says so.
- **Rida's gates — and only these.** (1) the concept, (2) the universe, (3) the cast sheets, (4) the finished cast:
  every character's picture with their locked voice, (5) the script, (6) the whole episode. In between you produce,
  choose and lock yourself: cast pictures and voices, the shot list, keyframes, clips, locked voices, the assembly.
  After the whole episode is confirmed, ask whether he wants the next episode.
- **Money.** Before a production run (the cast's pictures and voices; an episode's keyframes and clips) say in one
  line what it makes and its estimated cost, then go on without waiting. Stop and ask only if the spend would pass
  twice that estimate. Report the real cost (`cost_ledger`) when you present the result. Rough costs: one image
  ≈ $0.01 warm, ≈ $0.03 on a cold worker; one 5 s clip ≈ $0.03–0.05 warm, ≈ $0.13 cold (10 s ≈ double); a voice
  conversion ≈ $0.001 a line (+ ≈ $0.15 once if cold). A GPU queue of 20–35 minutes is normal and free.
- **Your own choices are honest.** When you pick a picture or a take, look at it and say why in the note
  (`store_lock`, `approve_take`, `voice_ref_from_take`: "Claude's pick: <why>"); never keep one with a wrong
  identity, an extra person, burned-in text or a failed clip check. When Rida rejects something at a gate, unlock it
  with his reason (`store_unlock`) and make it again.
- **Roles and words.** You (Claude) write and direct; the `showrunner` connector's tools are your hands; Rida
  decides at his gates. Talk to him in plain words, in the language he writes in: no tool names, paths, JSON or
  internal words (plans, stages, batches, decision numbers) unless he asks. The story's files are in English, except
  the spoken lines, which are in the story's language. Never real people, brands or copyrighted characters.
- **Always moving pictures.** Every shot is a real clip: never a still, never a Ken Burns, never a slowed clip.
  A failed take is made again with a new seed and a note, never filled. No edge-tts, no voice laid over a clip, no
  LLM API call from the app: you are the writer.
- **You write every prompt.** The connector only makes the pictures, the clips and the sound, checks them and cuts
  the episode. Write each prompt in full from the story's files and the patterns that worked (the prompt guide in
  the cast, shots and clips steps) and send it as `values.prompt` of `comfy_submit`; keep it in the story
  (`shots.json`) and the job journal keeps it too.
- **Words that reach a model.** A character's `## Head` is about 70 words (65–80), written "<Name>, a ...: ...",
  in the positive, no final period. Nothing that reaches a prompt names what must not appear (text, subtitles,
  captions, extra people): naming it draws it (the models ignore the negative prompt). Keyframes face the camera
  unless the three-quarter framing has been checked on this story.
- **One universe per story** (any universe, not fruit only): only what the story's own files say is assumed.
<!-- rules:end -->

<!-- prompts:start -->
## Writing the prompts (you write every one; the server only makes the picture or the clip)

You write each prompt in full and send it as `values.prompt` of `comfy_submit` (Rida does not need to read prompts;
show one only if he asks). The story's files are your material: `01-universe.md` `## Medium`, each `sheet.md` `## Head` and
`## Voice (en)`, each `03-places/<place>/plate.md` `## Setting`, the shot's lines. Write the prompt into the shot
too (`keyframe_prompt`, `clip_prompt` in `shots.json`) so the story keeps what was sent; the job journal keeps it as
well. The prompts are in English; only the quoted lines are in the story's language.

**What has worked (chosen by Rida 2026-10-08).** The patterns below are the prompts Rida preferred (take
s33 for one speaker, s22 for two or three), with one change since: the closing sentence. Keep their order and their
fixed sentences; change only what comes from this story's files. Departing from a pattern is fine when the story
needs it — note in the shot what you changed and why, so a good result can be kept.

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
clip at most 22 (all its lines together). A line that does not fit is cut in the script, never sped up.

**Before you send, check:** the Medium and every Head are word for word; the lines are the script's exact words,
in order; each speaker's voice is their sheet's `## Voice (en)`; the closing sentence is there (clips); nothing
unwanted is named; the size and seconds match the template; the batch is inside the cost you announced.
<!-- prompts:end -->

## When to use

- `01-universe.md` is locked and the chosen concept's cast has no locked sheets yet, or a new character joins.

## Read first

- `store_read 00-brief.md` (the cast, their wants) and `store_read 01-universe.md` (Medium, heads, palette, Forbidden).
- Characters Rida already approved in another story, when the brief brings them back: their locked files there
  (`store_read <other story>`), reused as they are (see "Returning characters").
- The proven sheets: `stories/faille-d-amour/02-cast/*/sheet.md` (≈ 70-word heads that held).

## Propose

No questions first. All the sheets in one message, every choice visible: name, age, gender, role, what they are
(fruit / species), outfit and colours, one mark, one signature item, personality, want, fear, secret, voice, how
they talk. Then: "Here is the cast — correct anyone, or add what you want." Once Rida says yes, lock the sheets
(gate 3) and start the run.

## Run (no questions)

Say once, in one line, what the run makes and its estimate (e.g. "3 characters: 12 pictures, 3 keyframes, 9 casting
clips ≈ $0.60 warm, up to $1.50 cold"), then go on. Per character:

1. **Full body** — 4 seeds of your full-body prompt: `comfy_submit(story, "t2i_flux2_klein", {"prompt": <yours>,
   "seed": <k>, "width": 832, "height": 1216}, dest="02-cast/<id>/candidates/full_body_c<k>")` → `comfy_fetch`
   → look at each: the right fruit/species and colours, the outfit, hands, one character only, nothing written.
   Pick the best → `store_copy` to `02-cast/<id>/full_body.png` → `store_lock` ("Claude's pick: <why>").
   None good → 4 new seeds (adjust the prompt from what went wrong).
2. **Turnaround** — 1 edit of the locked full body (`edit_flux2_klein_multiref`, the full body in `ref1`–`ref4`,
   832×1216) → check it is the same character → `store_copy` to `turnaround.png` → `store_lock`.
3. **Casting keyframe** — a plate for the casting place if the story has none (`03-places/<place>/plate.md`,
   `## Setting`, one sentence, nobody in it); `ep00/shots.json` with one 5 s shot per character
   (`{"id": "sNN", "seconds": 5, "place": …, "characters": ["<id>"], "lines": [{"speaker": "<id>", "text": <8–10
   words in the story's language, said the way they talk>}], "keyframe_prompt": …, "clip_prompt": …}`); 2 seeds
   of the keyframe (`edit_flux2_klein_multiref`, the full body as references, 704×1280, `faces_camera`) → pick →
   `store_copy` to `ep00/keyframes/sNN.png` → `store_lock`.
4. **Casting clip** — 3 seeds (11, 22, 33) of your one-speaker clip prompt: `comfy_submit(story,
   "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": 5}, files={"image": "ep00/keyframes/sNN.png"},
   episode=0, shot="sNN")` → `comfy_fetch` → `verify_take` each.
5. **The voice** — among the takes whose check is `ok`, pick the one whose voice best fits the sheet's voice and
   whose picture holds → `approve_take(…, note="Claude's pick: <why>")` → `voice_ref_from_take(story, 0, "sNN",
   <take>, "<id>", note="Claude's pick: <why>")`. No `ok` take → 3 new seeds.

Then present the cast (gate 4). A correction → `store_unlock` with Rida's reason, redo only that part, present again.

**Returning characters** — a character Rida approved in another story keeps everything: copy their locked
`sheet.md`, `full_body.png`, `turnaround.png` and `voice_ref.wav` with `store_copy(story, src, dest,
from_story=<other story>)`, lock them ("approved in <other story>"), and skip steps 1–5 for them. Their sheet is part
of your gate-3 proposal, marked "returning, unchanged" — Rida may still change their outfit or role (then the
picture is made again; the voice stays).

## Template

`02-cast/<id>/sheet.md` (skeleton `showrunner/prompts/character_sheet.md`; `<id>` lowercase, `_` between words):

```
# <Name>

## Head
<Name>, a <who, in the universe's words>: <the head and face: shape, colours, eyes, brows, mouth, one permanent
mark>; <the body and outfit, colours named>, <one signature item>, <the hands rule of the universe>

## Voice (en)
<age, tone, pace, texture, in English>

## Voice (fr)
<the same in French>

## Colour
<the subtitle colour of their name, hex>

## Wants
## Fears
## Secret
## How they speak
<rhythm, words they use, what they never say; "vous" or "tu" in French>
## Signature item
```

`## Head` is 65–80 words, one sentence, no final period, every visible thing named once with its colour; it opens
every picture and clip prompt of this character, word for word.

## Checklist

- [ ] Every sheet: a `# Name` title, a 65–80-word `## Head` in the positive, both voices, a colour.
- [ ] Two characters are told apart at a glance (silhouette, main colour, signature item).
- [ ] Sheets locked with Rida's words before any picture; the run's estimate said in one line.
- [ ] Each pick looked at and explained in its note; no pick with a wrong identity, an extra person or text.
- [ ] Each voice comes from an `ok` casting take, 2–10 s of that character alone.
- [ ] The spend stayed under twice the estimate (else you stopped and asked).

## Gate question

Gate 3 (sheets): "Here is the cast — correct anyone, or add what you want." Gate 4 (finished cast): "Here is the
cast with their voices — keep it, or what do we change?"

## What to show Rida

Gate 3: the sheets in short (the Head, the voice, the want, the secret). Gate 4, per character: the full body and
the turnaround (`view_file`), the casting clip and the locked voice as files to play (`file_download`), one line on
why you picked them. Then what the run really cost.

## Tools

- `store_read`, `store_write` — free: the brief, the universe, the sheets, plates and `ep00/shots.json`.
- `templates_list` — free: the templates, their sizes and the files they take.
- `comfy_submit` — COSTS MONEY: each picture or casting clip, inside the estimate you announced.
- `comfy_fetch`, `comfy_jobs` — free: collect and see the results; what is still in the queue.
- `view_file` — free: look at a picture or a clip's contact sheet again.
- `store_copy`, `store_lock`, `store_unlock` — free: the picked candidate becomes the canonical file, locked.
- `verify_take` — free: the clip check of a casting take.
- `approve_take` — free: the chosen casting take.
- `voice_ref_from_take` — free: cut and lock the voice from that take.
- `file_download` — free: hand Rida a casting clip or a voice to play.
- `cost_ledger`, `runpod_health` — free: what was spent; whether workers are warm or the queue is long.

## Next

`story-script`: the season map and episode 1's script.
