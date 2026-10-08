---
name: story-cast
description: Step 3 of an AI Story series on the showrunner connector. Use when the universe is locked, to write each character's sheet, make and lock their full-body picture (then turnaround and expressions), and cast and lock their voice from a take Rida picks. Costs money (images and casting clips).
---

# Story, step 3: the cast (GPU: images and casting clips)

Per character, in this order, each approved before the next: the sheet (free) → the canonical full-body picture
→ the turnaround and the expression grid → the voice, cast the stage-0 way (a short casting clip, Rida picks
the take, its line becomes the character's locked voice for the whole series).

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

- `01-universe.md` is locked and the cast of the chosen concept has no sheets yet, or a new character joins.
- Not to "fix" a locked character between episodes: if identity drifts, rebuild the pack with Rida, never
  re-roll shots to hide it.

## Read first

- `store_read 00-brief.md` (the cast, their wants) and `store_read 01-universe.md` (Medium, heads, proportions,
  palette, Forbidden).
- The proven sheets: `stories/faille-d-amour/02-cast/*/sheet.md` (≈ 70-word heads that held in batch a).

## Template

**1. The sheet** — `02-cast/<id>/sheet.md` (skeleton `showrunner/prompts/character_sheet.md`; `<id>` lowercase,
`_` between words):

```
# <Name>

## Head
<Name>, a <who, in the universe's words>: <the head and face: shape, colours, eyes, brows, mouth, one permanent
mark>; <the body and outfit, colours named>, <one signature item>, <the hands rule of the universe>

## Voice (en)
<age, tone, pace, texture, in English: "a calm young man, dry and ironic, slightly amused, quick but composed">

## Voice (fr)
<the same in French>

## Colour
<the subtitle colour of their name, hex>

## Wants
## Fears
## Secret
## How they speak
<rhythm, words they use, what they never say>
## Signature item
```

`## Head` is 65–80 words, one sentence, no final period, every visible thing named once with its colour; it opens
every keyframe and clip prompt of this character, word for word. `## Voice (en)` is what the clip prompt says.

**2. The full body** — `prompt_cast(story, <id>, "full_body")`, then 3–5 candidates (one seed each):
`comfy_submit(story, "t2i_flux2_klein", {"seed": <k>}, prompt_from="cast:<id>:full_body",
dest="02-cast/<id>/candidates/full_body_c<k>")`. Rida picks ONE → `store_copy` it to `02-cast/<id>/full_body.png`
→ `store_lock` with his words. It is never regenerated.

**3. Turnaround and expressions** — once the full body is locked: `prompt_cast(…, "turnaround")` and
`prompt_cast(…, "emotions")`, each sent with the template and files the tool gives
(`edit_flux2_klein_multiref`, the locked full body as reference), 2 candidates each, picked and locked as
`turnaround.png` / `emotions.png`. If the turnaround or the grid shows a different character, the full body is
not a good reference: say so; Rida decides whether to pick another full body.

**4. The voice (casting)** — a casting reel in `ep00`, one shot per character:

- a plate for the casting place if the story has none yet: `03-places/<place>/plate.md` with `## Setting` (one
  sentence of objects and light, no people in it);
- `ep00/shots.json`: `{"shots": [{"id": "s01", "seconds": 5, "place": "<place>", "characters": ["<id>"],
  "lines": [{"speaker": "<id>", "text": "<8–10 words in the story's language, said the way they speak>"}]}, …]}`
  (`prompt_clip` checks the budget: a 5 s clip holds 10 words at most);
- its keyframe: `prompt_keyframe(story, 0, "s01")` → 2 candidates with the template and files it names →
  Rida picks → `store_copy` to `ep00/keyframes/s01.png`;
- the clip: `prompt_clip(story, 0, "s01")` → 3 seeds (11, 22, 33, as batch a) with
  `comfy_submit(story, "ltx25_i2v_speech", {"seed": <s>}, files={"image": "ep00/keyframes/s01.png"},
  prompt_from="clip:0:s01")` → `comfy_fetch` each → `verify_take` each;
- Rida watches and listens (`file_download` each take) and picks the voice → `approve_take(…, note=<his words>)`
  → `voice_ref_from_take(story, 0, "s01", <take>, "<id>", note=<his words>)` → `file_download
  02-cast/<id>/voice_ref.wav` so he hears exactly what is locked.

Several characters can share one casting batch (one shot each); say the total count and cost once.

## Checklist

- [ ] Every sheet: a `# Name` title, a 65–80-word `## Head` in the positive, both voices, a colour.
- [ ] Nothing in a Head names what must not appear; the universe's `## Forbidden` is respected.
- [ ] Two characters are told apart at a glance (silhouette, main colour, signature item).
- [ ] Sheet locked (Rida's words) before its first picture; full body locked before turnaround/expressions.
- [ ] Count and cost said, Rida's go, for each batch (full bodies, turnaround + grid, casting keyframes,
      casting clips).
- [ ] The voice comes from a take Rida approved, checked by `verify_take` (its line heard, in time).
- [ ] `voice_ref.wav` is 2–10 s of that character alone, and Rida heard it.

## Gate question

Per character, in turn: "Is this <Name>?" (the sheet) · "Which one is <Name>: 1, 2, 3…?" (the full body) ·
"Is this <Name>'s voice for the whole series?" (the casting take).

## What to show Rida

The sheet in short (the Head, the voice, the want); every candidate picture (`comfy_fetch` shows it; `view_file`
again side by side) with your honest read of each (identity, hands, the universe kept or not) and the one you
would pick; every casting take as a file to play (`file_download`) with what the clip check heard. After each
batch, its real cost.

## Tools

- `store_read`, `store_write` — free: the brief, the universe, the sheets, plates and `ep00/shots.json`.
- `prompt_cast` — free: the full body, turnaround and expression prompts (and template, files, size).
- `prompt_keyframe`, `prompt_clip` — free: the casting reel's keyframe and clip prompts (and the speech budget).
- `comfy_submit` — COSTS MONEY: each image or clip, only after Rida's go, always with `prompt_from`.
- `comfy_fetch`, `comfy_jobs` — free: collect and see the results; what is still in the queue.
- `view_file` — free: look at a picture or a clip's contact sheet again.
- `store_copy`, `store_lock` — free: make the picked candidate the canonical file, lock it with Rida's words.
- `verify_take` — free: the clip check of a casting take.
- `approve_take` — free: Rida's pick of the casting take, his words as the note.
- `voice_ref_from_take` — free: cut and lock the voice from that approved take.
- `file_download` — free: hand Rida a take or the voice to play.
- `cost_ledger`, `runpod_health` — free: what was spent; whether workers are warm or the queue is long.

## Next

`story-script`: the season map and episode 1's script.
