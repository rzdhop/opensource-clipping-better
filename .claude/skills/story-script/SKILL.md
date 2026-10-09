---
name: story-script
description: Step 4 of an AI Story series on the showrunner connector. Use when the cast is locked, to propose the season map and the episode's full script (beats and lines, cliffhanger first, places) for Rida to correct, every line sized to its clip. Free.
---

# Story, step 4: the script (free)

You write the episode in full and Rida corrects it: the 90-second beat template, cliffhanger first, the lines in
the story's language, every line sized to the clip that will say it, and any new place. Free.

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

- Episode 1, once the cast is locked (sheets, pictures, voices).
- Episode N+1, after `story-next-episode` and Rida's pick of a direction.

## Read first

- `store_read 00-brief.md` (the chosen concept, the last 5 s of episode 1, the language), `04-season.md`,
  `memory.md` (from episode 2).
- Every `02-cast/<char>/sheet.md` (Wants, Fears, Secret, How they speak) and the `03-places/` that exist.

## Propose

No questions first: the full script, ready to read, with its choices visible (the length, the places, the register
of the lines, the cliffhanger). Then: "Here is episode N — correct any line or shot."

## Template

**The season** (`04-season.md`, episode 1 only, then kept up to date): `## Arc` in two sentences; `## Episodes`,
10+ one-liners — the hook block (E1–3), the reversal, the all-is-lost, the payoff — and the cliffhanger shape of
each, rotating: revelation / reversal / deadline / intrusion.

**The beats of one episode** (≈ 90 s, 11–13 clips, 10–12 lines):

| Time | Beat | Clips | Length |
|---|---|---|---|
| 0–5 s | Hook: mid-conflict, one sharpening line | 1 | 5 s |
| 5–15 s | Setup: what is at stake | 1–2 | 5 s |
| 15–55 s | Escalation: 3 beats, each raises the stakes | 4–5 | 10 s dialogue, 5 s reactions |
| 55–60 s | The turn: a choice | 1 | 5 s |
| 60–80 s | Peak: the shareable confrontation | 2 | 10 s |
| 80–90 s | Cliffhanger: cut before the reaction + "Part 2" | 1 | 5 s |

**The script** (`epNN/script.md`), one block per shot, in order:

```
# <Title> — episode <N>

## s01 — <place id> (5 s)
- **<Name>**: <the line, in the story's language>
- **<Name>**: <a second or third speaker in the same clip, multi-speaker preferred>

## s02 — <place id> (5 s)
- *(silent — <Name>: what the face shows)*
```

**A place** (`03-places/<place id>/plate.md`), for every place the script uses that has no plate yet:

```
# <Place name>

## Setting
<one sentence: the set, its objects, its light and time of day — nobody else in it>
```

## Checklist

- [ ] The cliffhanger (last shot) is written first and matches the season map's shape for this episode.
- [ ] The hook (s01) is mid-conflict and readable with the sound off; no establishing shot, no title card.
- [ ] ≤ 12 lines, 6–12 words each; 1–3 speakers per shot, two- and three-speaker shots preferred.
- [ ] Budget per shot: a 5 s clip says ≤ 10 words in all, a 10 s clip ≤ 22 (≈ 2.4 words/s after a beat of
      silence). Count the words of every shot's lines together.
- [ ] One action per shot; a silent shot says what the face shows.
- [ ] Every speaker has a locked sheet and a locked voice; every place has a plate whose Setting names no people
      and no text.
- [ ] Each character sounds like their `## How they speak`; nobody explains the plot.
- [ ] From episode 2: the recap is in the first line, not a recap shot; the open threads of `memory.md` move.

## Gate question

"Here is episode N — correct any line or shot. Once you say OK, I make the whole episode and come back with it."

## What to show Rida

The script as it will be heard: shot by shot, the lines in the story's language, the silent shots in one line, the
cliffhanger last, and each shot's length. The season map once, for episode 1. Before you go on, one line: what the
episode will cost (keyframes + clips + voices, ≈ $1–3) — then go on without waiting.

## Tools

- `store_read` — free: brief, season, memory, sheets, places.
- `store_write` — free: `04-season.md`, `epNN/script.md`, `03-places/<place>/plate.md`.
- `store_lock` — free: lock the script once Rida approves, with his words.

## Next

`story-shots`, then `story-clips` and `story-assemble`, without stopping: Rida's next gate is the whole episode.
