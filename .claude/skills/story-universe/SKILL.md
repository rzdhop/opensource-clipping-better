---
name: story-universe
description: Step 2 of an AI Story series on the showrunner connector. Use after the concept is chosen, to propose the full universe of the story (medium, heads, proportions, palette, light, camera) for Rida to correct, and lock 01-universe.md before any picture. Free.
---

# Story, step 2: the universe (free)

You propose the art of the story in full, in words, before any picture; Rida corrects it and adds what he wants.
`01-universe.md` `## Medium` opens EVERY image and clip prompt of the story: it is the style lock. Free.

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

## When to use

- The story has a chosen concept (`00-brief.md`) and an empty `01-universe.md`.
- Rida wants to change the look of a story: unlock it with his reason; every picture made in the old look is then
  out of date (say so).

## Read first

- `store_read 00-brief.md` (the chosen concept, the language).
- A universe Rida already approved that fits (e.g. `stories/faille-d-amour/01-universe.md` for fruit people: the
  look he chose). When one fits, it is your proposal, word for word — not one option among others.

## Propose

No questions first. Propose the whole file at once: the universe he approved before when it fits the concept, or
a new one written from the concept. Then: "Here is the universe — correct anything, or add what you want."

## Template

The skeleton is `showrunner/prompts/universe.md`. Fill every section:

```
# Universe — <name>

## Medium
<3–5 sentences, the positive description of the picture: the medium (3D cartoon / claymation / anime / painted
2D…), how a character is built (the head, the face, the body, the hands, the clothes), the surfaces and the
finish. Say what IS on screen. It opens every prompt, word for word.>

## Negative
<a comma list; sent as the negative prompt, which the models ignore — so the Medium alone must hold>

## Heads allowed
<what a head may be in this universe>

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

## Checklist

- [ ] An approved universe that fits is reused word for word (its Medium proved itself).
- [ ] The Medium says in the positive what the picture is; it names no character and no place.
- [ ] Head types, proportions and the hands rule are explicit; the palette is in hex.
- [ ] Camera: vertical 9:16, the camera holds still on the speaker.
- [ ] Locked only after Rida's yes, with his words.

## Gate question

"Is this the look of the series? Correct or add anything."

## What to show Rida

The Medium sentence first, then the rest of the file in short (heads, palette, light, what never appears).

## Tools

- `store_read` — free: the brief, an approved universe to reuse.
- `store_write` — free: `01-universe.md`.
- `store_lock` — free: lock `01-universe.md` with Rida's words.
- `store_unlock` — free: only to change a locked universe, with Rida's reason.

## Next

`story-cast`: the characters, their pictures and their voices.
