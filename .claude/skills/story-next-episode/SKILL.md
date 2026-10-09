---
name: story-next-episode
description: Step 7 of an AI Story series on the showrunner connector. Use after an episode is delivered, to write the series memory (what happened, relationships, open threads, the last frame), take in the audience's feedback, and propose 3 directions for the next episode. Free.
---

# Story, step 7: the next episode (free)

The series remembers. After each episode you write what happened into `memory.md`, read what the audience
said, and propose three directions for the next episode. Rida picks one; then the loop goes back to the script.
The universe, the cast pictures and the voices stay locked: the next episode reuses them.

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

- An episode has a final mp4 that Rida approved (or posted).
- Rida pastes comments, numbers or his own notes on an episode.

## Read first

- `store_read memory.md`, `04-season.md`, the last `epNN/script.md`, `epNN/metadata.md` and `epNN/defects.md`.
- The audience's feedback Rida pastes (comments, retention, which team the comments chose).

## Ask first

Before writing the memory and the directions:

- How did the episode do: comments, views, what people argued about (he pastes or tells)?
- Anything he wants to change for the next one (pace, a character, the look, the length)?
- A new character or a new place in the next episode?
- Then, after the three directions: which one (or a mix)?

## Template

`memory.md` (rewrite it whole each time; it is the only thing the next script reads about the past):

```
# Series memory

## What happened
- ep01: <two lines: the conflict, the turn, the cliffhanger as it ended>
- ep02: …

## Relationships
- <Name> → <Name>: <what each knows, wants, hides — one line per pair that matters>

## Open threads
- <a promise to the audience not yet paid, one line each, oldest first>

## Last frame
<one line: the picture and the line the last episode ended on — the next hook starts from it>

## Audience
- ep01: <what the comments argued about; what to give more of; what fell flat>
```

Three directions for episode N+1, each: the hook (first 5 s, picking up from the last frame), the escalation
in one line, the cliffhanger and its shape (rotate: revelation / reversal / deadline / intrusion), which open
thread it pays or opens, which characters it needs (a new character → `story-cast` first), and an estimate of its
cost (≈ 12 clips × 2 seeds + keyframes, from `cost_ledger` of the last episode).

## Checklist

- [ ] `memory.md` covers every episode so far; the relationships are current; the last frame is written.
- [ ] The audience's feedback is in it, in a line per episode, in Rida's words where he gave them.
- [ ] Three directions that differ (not three versions of one); each picks up from the last frame.
- [ ] Each direction says its cliffhanger shape, different from the last episode's.
- [ ] A new character or a new place is flagged (cast step first; a new plate in the script step).
- [ ] The season map (`04-season.md`) is updated once Rida picks.

## Gate question

"For episode N+1: direction 1, 2 or 3 (or a mix)?"

## What to show Rida

The memory in short (what happened, who knows what, the open threads), what the audience said in one line, then
the three directions as cards with their cost estimate.

## Tools

- `store_read` — free: memory, season, the last script, metadata, defects.
- `store_write` — free: `memory.md`, `04-season.md`.
- `cost_ledger` — free: what the last episode cost (the base of the estimate).
- `story_list` — free: the story's episodes so far.

## Next

`story-script` for episode N+1 (and `story-cast` first if a new character joins).
