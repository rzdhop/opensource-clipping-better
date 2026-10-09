---
name: fruit-drama-episode
description: Use when Rida asks for a Fruit Drama (fruit people telenovela) story, cast or episode on the showrunner connector. Same flow as story-director - it asks him at every step and loads the step skills; the fruit universe is only a proposal to discuss, never assumed.
---

# Fruit Drama (an entry point for one kind of story)

A Fruit Drama is one universe among others (D8): fruit people in a vertical telenovela. It runs through exactly the
same steps and skills as any story — this page only says what is known to work for fruit, so you can **propose** it;
Rida decides every part of it.

**First, load `story-director`** and follow it: it finds where the story stands and loads the step skill. Load each
step's skill before doing the step, every time.

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

## What is known to work for fruit (to propose, never to assume)

- **The look of the stage-0 tests** (Rida liked batch a): a stylised 3D cartoon in the manner of a feature
  animation, fully computer-generated; each character's whole head IS the fruit (stem, leaves and skin intact),
  large cartoon eyes and a wide mouth drawn on the skin; a slim human body in real clothes, human hands. The proven
  Medium sentence is in `stories/faille-d-amour/01-universe.md` (`store_read`). In the universe step, show it to
  Rida as one option among two or three and ask.
- **One fruit per character**, readable at a glance (mango, strawberry, kiwi worked); ask which fruit for each.
- **The existing cast** of *Faille d'amour* (Rida the kiwi hacker, Marie-Jeanne the strawberry who runs the deal,
  Paloma the mango) has locked sheets, pictures and voices. A new story never reuses them unless Rida says so: ask.
- **The telenovela beats**: a hook mid-conflict, one known trope (enemies to lovers, cheating reveal, who's the
  father…), the comment bait "Team X or Team Y?", a cut before the reaction, "Partie 2 demain".

## Ask first

Before loading the first step (skip what Rida already said):

- A new Fruit Drama story, or a new episode of an existing one (which)?
- The language: French or English?
- Keep the look of the stage-0 tests, or discuss another fruit look?
- New characters, or does any of the *Faille d'amour* cast come back?

## Gate question

"OK: a <new / continued> Fruit Drama in <language>, starting with <step> — shall we go?"

## Tools

- `story_list` — free: the stories (is there a Fruit Drama already?).
- `store_read` — free: the proven fruit universe and sheets of *Faille d'amour*, to show Rida.

Everything else is used by the step skills, through `story-director`.
