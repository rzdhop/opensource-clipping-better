---
name: story-concepts
description: Step 1 of an AI Story series on the showrunner connector. Use when Rida gives a pitch for a new vertical drama series (any universe), to propose 3 complete concepts he corrects or picks, then create the story folder. Free.
---

# Story, step 1: concepts (free)

Rida gives a pitch. You turn it into three complete concepts — every choice already made and visible — and he picks,
mixes or corrects. Then you create the story folder. Nothing here costs money.

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

- A new pitch ("a series about…", "fais-moi une série où…"), or "start a new story".
- Not for a story that already exists (`story_list` shows them): `story-director` sends you to its step.

## Read first

- `story_list`: the existing stories (never reuse a slug).
- What Rida approved before, to build on it: the universe and the locked cast of earlier stories
  (`store_read <story>/01-universe.md`, `02-cast/*/sheet.md`) when the pitch is close to one of them.

## Propose

No questions first. Each concept states its own choices, so Rida corrects them by reading:

- the language (the one of Rida's message, unless he named another);
- the tone, the platform and the episode length (vertical, 60–90 s);
- the main cast (3–4) with names, roles, and — when the pitch fits — the characters he already approved, said as
  "brought back from <story>, or new?";
- what the series will not show (your proposal, one line), which he corrects if needed.

## Template

Three concept cards, each one:

```
### <Title> — <one-line hook>
- Logline: one sentence, who wants what, against whom.
- The secret: what the audience learns before the characters (or the reverse).
- Cast (3–4): <Name> — what they want, in one line. (One line each.)
- The last 5 s of episode 1: one image, one line of dialogue, one open question. Written first.
- The trope the audience already knows: enemies to lovers / cheating reveal / who's the father / inheritance /
  the new arrival / the double life… (name it).
- Comment bait: the question viewers will argue about ("Team X or Team Y?").
- Why they come back: what "Part 2" promises.
- Season sketch (10+ episodes): hook block (E1–3), the reversal, the all-is-lost, the payoff, in one line each.
- Language · tone · length · what it will not show.
```

What makes a concept travel: emotional clarity beats render quality; a recurring cast with a relationship graph;
one known trope; a daily cadence; every episode ends on a cut before the reaction. Make the three really different
(different trope, different secret), not three versions of one.

When Rida picks (or mixes), write `00-brief.md`:

```
# Brief

## Pitch
<Rida's words>

## Concepts
<the three cards>

## Chosen concept
<the card he picked, with his corrections>

## Language
fr | en
```

## Checklist

- [ ] Three concepts, each with the last 5 s of episode 1 written before anything else.
- [ ] Each names its trope, its secret, its comment bait, a 10-episode sketch, and its language, tone, length.
- [ ] The cast is 3–4 characters; each has one want in one line; returning approved characters are flagged.
- [ ] The story folder is created only after Rida picked, with the title and language of his pick.

## Gate question

"Which one do we make — 1, 2 or 3, or a mix? Correct anything you want."

## What to show Rida

The three cards, in his language, short enough to read on a phone. After his pick: the brief, then go on.

## Tools

- `story_list` — free: the stories that exist.
- `store_read` — free: what earlier stories approved (universe, cast).
- `story_create` — free: the folder after Rida's pick (title, language, universe name if already known).
- `store_write` — free: `00-brief.md`, and the season sketch into `04-season.md` (`## Arc`, `## Episodes`).
- `store_lock` — free: lock `00-brief.md` with Rida's words.

## Next

`story-universe`: the art, proposed in full.
