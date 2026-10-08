---
name: story-script
description: Step 4 of an AI Story series on the showrunner connector. Use when the cast is locked, to write the season map and an episode's script (beats and lines, cliffhanger first), its places, and check every line against its clip's speech budget. Free.
---

# Story, step 4: the script (free)

You write the episode: the 90-second beat template, cliffhanger first, the lines in the story's language, every
line sized to the clip that will say it. Nothing here costs money; the shot list and the pictures are step 5.

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
- **Prompts are built, never typed.** `prompt_keyframe`, `prompt_clip` and `prompt_cast` build every prompt from
  the story's files through the tested templates; `comfy_submit(prompt_from="keyframe:<ep>:<shot>" |
  "clip:<ep>:<shot>" | "cast:<char>:<kind>")` sends exactly that. To change a prompt, change the story file it comes
  from (the universe, a sheet, a plate, `shots.json`) and build it again.
- **Words that reach a model.** A character's `## Head` is about 70 words (65–80), written "<Name>, a ...: ...",
  in the positive, no final period. Nothing that reaches a prompt names what must not appear (text, subtitles,
  captions, extra people): naming it draws it (the models ignore the negative prompt). Keyframes face the camera
  unless the three-quarter check has passed on this story.
- **One universe per story** (any universe, not fruit only): only what the story's own files say is assumed.
<!-- rules:end -->

## When to use

- Episode 1, once the cast is locked (sheets, full bodies, voices).
- Episode N+1, after `story-next-episode` chose the direction with Rida.

## Read first

- `store_read 00-brief.md` (the chosen concept, the last 5 s of episode 1, the language),
  `store_read 04-season.md`, `store_read memory.md` (from episode 2).
- Every `02-cast/<id>/sheet.md` (Wants, Fears, Secret, How they speak) and the `03-places/` that exist.

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

## s01 — <place id>
- **<Name>**: <the line, in the story's language>
- **<Name>**: <a second or third speaker in the same clip, multi-speaker preferred>

## s02 — <place id>
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
- [ ] ≤ 12 lines, 6–12 words each; 1–3 speakers per shot, two- and three-speaker shots preferred (D7).
- [ ] Budget per shot: a 5 s clip says ≤ 10 words in all, a 10 s clip ≤ 22 (≈ 2.4 words/s after a beat of
      silence). Count the words of every shot's lines together.
- [ ] One action per shot; a silent shot says what the face shows.
- [ ] Every speaker has a locked sheet and a locked voice; every place has a plate whose Setting names no people
      and no text.
- [ ] Each character sounds like their `## How they speak`; nobody explains the plot.
- [ ] From episode 2: the recap is in the first line, not a recap shot; the open threads of `memory.md` move.

## Gate question

"Is this the episode? (Once you say yes, I lock the script and plan the shots.)"

## What to show Rida

The script as it will be heard: shot by shot, the lines in the story's language, the silent shots in one line, the
cliffhanger last. Then the word count per shot against its budget, in one short table. The season map once, for
episode 1.

## Tools

- `store_read` — free: brief, season, memory, sheets, places.
- `store_write` — free: `04-season.md`, `epNN/script.md`, `03-places/<place>/plate.md`.
- `store_lock` — free: lock the script once Rida approves, with his words.

## Next

`story-shots`: the shot list, the keyframes.
