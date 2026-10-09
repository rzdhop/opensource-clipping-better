---
name: story-universe
description: Step 2 of an AI Story series on the showrunner connector. Use after the concept is chosen, to agree the art of the story with Rida (medium, heads, proportions, palette, light, camera) and write 01-universe.md before any picture. Free.
---

# Story, step 2: the universe (free)

The art is agreed in words before any picture (D8: any universe, fruit is only one of them). `01-universe.md`
`## Medium` opens EVERY image and clip prompt of the story: it is the style lock. Nothing here costs money.

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

## When to use

- The story has a chosen concept (`00-brief.md`) and an empty `01-universe.md`.
- Rida wants to change the look of a story: the universe is locked, so unlock it with his reason first, and
  know that every picture already made in the old look is out of date.

## Read first

- `store_read 00-brief.md` (the chosen concept and the language).
- `store_read 01-universe.md` (the skeleton `story_create` wrote).
- The proven example: `stories/faille-d-amour/01-universe.md` (fruit people, a 3D cartoon in the manner of a
  feature animation). Its Medium is the one batch a was made with; borrow its shape, not its fruit.

## Template

The skeleton is `showrunner/prompts/universe.md`. Fill every section:

```
# Universe — <name>

## Medium
<3–5 sentences, the positive description of the picture: the medium (3D cartoon / claymation / anime / painted
2D…), how a character is built (the head, the face, the body, the hands, the clothes), the surfaces and the
finish. Say what IS on screen. It opens every prompt, word for word.>

## Negative
<a comma list; sent as the negative prompt, which the models ignore at cfg 1.0 — so the Medium alone must hold>

## Heads allowed
<what a head may be in this universe: one species, several, objects, animals…>

## Proportions
<head-to-body ratio, height range, the hands rule>

## Palette
<4–8 colours in hex, with what they are for>

## Lighting
<day / night looks; practical lights>

## Camera
Vertical 9:16; medium close-ups and two-shots; the camera holds still on the speaker. <anything specific>

## Forbidden
<what never appears — for YOU when writing sheets, plates and lines; never copied into a prompt>
```

Propose two or three short directions first (one paragraph each, the Medium sentence of each), let Rida choose,
then write the full file from his choice.

## Checklist

- [ ] The Medium says in the positive what the picture is; it does not list what must not appear (that goes in
      `## Forbidden`, read by you, and `## Negative`, ignored by the models).
- [ ] The Medium names no character and no place (they come from the sheets and the plates).
- [ ] Head types, proportions and the hands rule are explicit (identity drift starts there).
- [ ] The palette is in hex.
- [ ] Camera: vertical 9:16, the camera holds still on the speaker (batch a: a moving camera relit and invented).
- [ ] Rida said yes to the words before any picture; the file is locked with his words.

## Gate question

"Is this the look of the series? (Every picture and clip will start with this Medium sentence.)"

## What to show Rida

The two or three directions as short paragraphs, then the Medium sentence of the chosen one in full, then the
whole file. Say plainly that the first real test of the look is the first full-body picture of step 3.

## Tools

- `store_read` — free: the brief and the skeleton.
- `store_write` — free: `01-universe.md`.
- `store_lock` — free: lock `01-universe.md` with Rida's words.
- `store_unlock` — free: only to change a locked universe, with Rida's reason.

## Next

`story-cast`: the characters, their pictures and their voices.
