---
name: story-clips
description: Step 5b of an AI Story series on the showrunner connector. Use right after an episode's keyframes are locked, to make each shot's clip (picture and voice together), check it, re-voice it with the locked voices and lock the best take, without stopping for questions. Costs money (clips, a few cents each).
---

# Story, step 5b: the clips (GPU: clips and voice conversion, no questions)

One clip per shot, made from its locked keyframe with the lines spoken in the clip itself, checked by the server,
re-voiced with each speaker's locked voice, chosen and locked by you. Rida sees the locked clips and the finished
episode at the next gate; nothing is assembled from a take that is not approved.

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

- Every shot of `epNN/shots.json` has a locked `epNN/keyframes/sNN.png`.
- A shot to redo after Rida's episode review (his reason in the note).

## Read first

- `store_read epNN/shots.json` and `epNN/takes.json` (what exists, what is approved).
- `comfy_jobs(open_only=True)`: jobs already sent and not collected — collect them before sending more.

## Run (no questions)

For every shot, the whole episode in one batch:

1. Write the clip prompt (the one-speaker, two/three-speaker or silent pattern of the guide above) into the shot's
   `clip_prompt`.
2. 2 seeds per shot: `comfy_submit(story, "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": <5|10>},
   files={"image": "epNN/keyframes/sNN.png"}, episode=N, shot="sNN")`. Each clip becomes the shot's next take.
3. `comfy_fetch(story, job, wait_s=240)` each (a long queue is normal: fetch again later), then
   `verify_take(story, N, "sNN", "vK")`. States: `ok` · `mismatch` (a line not heard, or out of order) · `late`
   (the last word too close to the end) · `no_speech`.
4. Among the `ok` takes, look at the contact sheets: identity held, mouths moving only on their own lines, no
   extra person, no burned-in text, the universe kept, no camera drift. Keep the best.
5. `vc_clip(story, N, "sNN", "vK")` → `vc_fetch(…)`: the same picture with every line in its speaker's locked voice
   (a new take). → `approve_take(story, N, "sNN", <the voiced take>, note="Claude's pick: <why>")`.

When a shot fails (no `ok` take, or every take has a picture defect):

- 2 new seeds, with a line in `epNN/defects.md` (shot, takes, what was wrong).
- Still failing after 3 rounds on a two- or three-speaker shot: split it into one shot per speaker (same lines, same
  order) in `shots.json`, make their keyframes (`story-shots` step 2–3), and go on — the script's words never change.
- A one-speaker shot still failing after 3 rounds: stop and tell Rida what fails, with the takes to see.
- Never fill a failed shot with a still, a hold or a slowed clip.

When every shot has an approved voiced take, go straight on to `story-assemble`.

## Template

`epNN/defects.md`, one line per failed round (it tells the next episode what to avoid):

```
- s04 v1, v2 — mismatch: Théo's line not heard (the strawberry spoke it); next: one-speaker shot of Théo
- s07 v3 — picture: a second kiwi appeared at the edge; next: new seed, "only this one character" kept
```

## Checklist

- [ ] No open jobs left behind before a new batch.
- [ ] Every clip prompt written from its pattern (Medium and Heads word for word, the script's exact lines, each
      speaker's voice, the closing sentence).
- [ ] Every take checked with `verify_take`; only an `ok` take is kept.
- [ ] Every kept take re-voiced with the locked voices before it is approved.
- [ ] Every approval's note says why; every defect written in `epNN/defects.md`.
- [ ] The spend stays under twice the episode's estimate (else stop and ask).

## Gate question

None: this step does not stop. Rida's next gate is the whole episode (`story-assemble`).

## What to show Rida

Nothing during the step (a one-line progress note at most). If a one-speaker shot fails three rounds: the failing
takes as files (`file_download`), what the check heard, and your read.

## Tools

- `store_read`, `store_write` — free: `shots.json`, `takes.json`, `defects.md`.
- `comfy_submit` — COSTS MONEY: one clip per call, inside the episode's estimate.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `verify_take` — free: the clip check (this server's CPU, ≈ 35 s a clip).
- `vc_clip` — COSTS MONEY (tiny): re-voice a checked take with the locked voices; then `vc_fetch` (free).
- `approve_take` — free: the chosen voiced take, with why.
- `file_download` — free: a take for Rida, only when a shot fails.
- `cost_ledger`, `runpod_health` — free.

## Next

`story-assemble`, straight away.
