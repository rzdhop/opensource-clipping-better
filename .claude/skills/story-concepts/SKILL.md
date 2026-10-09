---
name: story-concepts
description: Step 1 of an AI Story series on the showrunner connector. Use when Rida gives a pitch for a new vertical drama series (any universe), to propose 3 concepts and create the story folder. Free.
---

# Story, step 1: concepts (free)

Rida gives a pitch. You turn it into three concepts he can choose from, the cliffhanger of episode 1 written
first, then create the story folder with the one he picks. Nothing here costs money.

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

## When to use

- A new pitch ("a series about…", "fais-moi une série où…"), or "start a new story".
- Not for a story that already exists (`story_list` shows them): go to the step it has reached.

## Read first

- `story_list`: the existing stories (never reuse a slug; see what Rida already made).
- Ask Rida only what the pitch leaves open, in one message: the language of the episodes (French or English,
  D4), and anything the pitch does not settle about tone. Do not ask about the art yet: that is step 2.

## Ask first

Before writing any concept (skip what the pitch already says):

- The language of the episodes: French or English?
- The tone: telenovela melodrama, romantic comedy, dark thriller, pure comedy — or a mix?
- Where it is posted (TikTok, Reels, Shorts) and the episode length he aims at (60, 75 or 90 s)?
- How many main characters (3, 4, 5)? Any names, ages or roles he already has in mind?
- A new story, or a continuation / reuse of an existing one (`story_list`)? Never reuse a cast unless he says so.
- What must happen, and what must never happen (no-go topics, lines he will not cross)?
- Any reference he likes (a series, a viral clip, a scene) to aim at?

## Template

Three concept cards, each one:

```
### <Title> — <one-line hook>
- Logline: one sentence, who wants what, against whom.
- The secret: what the audience learns before the characters (or the reverse).
- Cast (3–4): <Name> — what they want, in one line. (One line each.)
- The last 5 s of episode 1: one image, one line of dialogue, one open question. Written first.
- The trope the audience already knows: cheating reveal / who's the father / inheritance / the new arrival /
  the double life / the rival… (name it).
- Comment bait: the question viewers will argue about ("Team X or Team Y?").
- Why they come back: what "Part 2" promises.
- Season sketch (10+ episodes): hook block (E1–3), the reversal, the all-is-lost, the payoff, in one line each.
```

What makes a concept travel (the research behind plan 36): emotional clarity beats render quality; a recurring
cast with a relationship graph; one known trope; a daily cadence; every episode ends on a cut before the reaction.
Make the three concepts really different (different trope, different secret), not three versions of one.

When Rida picks (or mixes), write `00-brief.md`:

```
# Brief

## Pitch
<Rida's words>

## Concepts
<the three cards>

## Chosen concept
<the card he picked, with his changes>

## Language
fr | en
```

## Checklist

- [ ] Three concepts, each with the last 5 s of episode 1 written before anything else.
- [ ] Each names its trope, its secret, its comment bait and a 10-episode sketch.
- [ ] The cast is 3–4 characters; each has one want in one line.
- [ ] Nothing about the art yet (medium, species, style): step 2 decides it with Rida.
- [ ] The story folder is created only after Rida picked, with the title and language he agreed.

## Gate question

"Which concept do we make: 1, 2 or 3 (or a mix)? And in French or in English?"

## What to show Rida

The three cards, in his language, short enough to read on a phone. After his pick: the title, the slug and the
language you will create the story with, then the brief once written.

## Tools

- `story_list` — free: the stories that exist.
- `story_create` — free: the folder after Rida's pick (title, language, universe name if already known).
- `store_write` — free: `00-brief.md`, and the season sketch into `04-season.md` (`## Arc`, `## Episodes`).
- `store_read` — free: read a file back.
- `store_lock` — free: lock `00-brief.md` once Rida agrees, with his words as the note.

## Next

`story-universe`: agree the art before any picture.
