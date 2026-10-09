---
name: fruit-drama-episode
description: Use when Rida asks for a Fruit Drama (fruit people telenovela) story, cast or episode on the showrunner connector. Same flow as story-director; the fruit look and the fruit characters Rida already approved are proposed first, and he corrects.
---

# Fruit Drama (an entry point for one kind of story)

A Fruit Drama is one universe among others: fruit people in a vertical telenovela. It runs through exactly the
same steps and skills as any story. **First, load `story-director`** and follow it; load each step's skill before
doing the step, every time. This page only says what Rida already approved for fruit, so you propose it first.

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

## What Rida already approved for fruit (proposed first, he corrects)

- **The look.** The universe of *Faille d'amour* (`store_read faille-d-amour 01-universe.md`): a stylised 3D cartoon
  in the manner of a feature animation, each character's whole head IS the fruit, cartoon eyes and mouth drawn on
  the skin, a slim human body in real clothes, human hands. In the universe step it is your proposal, word for word.
- **The characters.** Rida the kiwi hacker, Marie-Jeanne the strawberry who runs the deal, Paloma the mango: their
  sheets, pictures and voices are locked in *Faille d'amour*. When a new pitch fits them, propose them in the cast
  ("returning: Rida as the hacker, Marie-Jeanne as the director — or new characters?") and Rida says what he wants
  more or less; a returning character keeps their picture and voice (`story-cast`, returning characters).
- **One fruit per character**, readable at a glance; new characters get a fruit you propose.
- **The telenovela beats**: a hook mid-conflict, one known trope (enemies to lovers, cheating reveal, who's the
  father…), the comment bait "Team X or Team Y?", a cut before the reaction, "Partie 2 demain".

## Propose

No questions first: go to `story-director`, which sends a new pitch to `story-concepts`. Use the approved look and
characters above inside your proposals, visibly, so Rida can keep or change them.

## Gate question

None here: each step has its own.

## Tools

- `story_list` — free: the stories (is there a Fruit Drama already?).
- `store_read` — free: the approved fruit universe and characters of *Faille d'amour*.

Everything else is used by the step skills, through `story-director`.
