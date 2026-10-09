---
name: story-next-episode
description: Step 7 of an AI Story series on the showrunner connector. Use when Rida confirmed an episode and wants the next one, to write the series memory and propose 3 directions for the next episode, which he picks or corrects. Free.
---

# Story, step 7: the next episode (free)

The series remembers. After an episode Rida confirmed, you write what happened into `memory.md` and propose three
directions for the next episode; he picks one or corrects it, and the loop goes back to the script. The universe,
the cast pictures and the voices stay locked: the next episode reuses them.

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

- Rida confirmed an episode and said yes to the next one.
- Rida pastes comments, numbers or his own notes on an episode.

## Read first

- `store_read memory.md`, `04-season.md`, the last `epNN/script.md`, `epNN/metadata.md` and `epNN/defects.md`.
- The audience's feedback, if Rida gave any.

## Propose

No questions first: the memory, then three directions. One line at the end invites what Rida knows and you don't:
"If you have comments or numbers from the audience, paste them and I'll adjust."

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

Three directions for episode N+1, each: the hook (first 5 s, picking up from the last frame), the escalation in one
line, the cliffhanger and its shape (rotate: revelation / reversal / deadline / intrusion), which open thread it pays
or opens, which characters it needs (a new character → `story-cast` first), and its estimated cost (from
`cost_ledger` of the last episode).

## Checklist

- [ ] `memory.md` covers every episode so far; the relationships are current; the last frame is written.
- [ ] The audience's feedback is in it when Rida gave some.
- [ ] Three directions that differ; each picks up from the last frame; each cliffhanger shape differs from the
      last episode's.
- [ ] A new character or a new place is flagged.
- [ ] `04-season.md` updated once Rida picks.

## Gate question

"For episode N+1: direction 1, 2 or 3 — or a mix? Correct anything."

## What to show Rida

The memory in short (what happened, who knows what, the open threads), then the three directions as cards with
their cost.

## Tools

- `store_read` — free: memory, season, the last script, metadata, defects.
- `store_write` — free: `memory.md`, `04-season.md`.
- `cost_ledger` — free: what the last episode cost (the base of the estimate).
- `story_list` — free: the story's episodes so far.

## Next

`story-script` for episode N+1 (and `story-cast` first if a new character joins).
