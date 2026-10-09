# Story, step 3: the cast (GPU: images and casting clips)

Two moments for Rida: he corrects the sheets you propose (gate 3), then he meets the finished cast — every character's
picture with their locked voice (gate 4). Between the two you make the pictures and the voices yourself on the GPU,
choose the best, lock them, and say what it cost.

**Before writing any prompt, read `PROMPTS.md`** (in this skill's folder): the patterns that worked and the checks
before sending.

## When to use

- `01-universe.md` is locked and the chosen concept's cast has no locked sheets yet, or a new character joins.

## Read first

- `store_read 00-brief.md` (the cast, their wants) and `store_read 01-universe.md` (Medium, heads, palette, Forbidden).
- Characters Rida already approved in another story, when the brief brings them back: their locked files there
  (`store_read <other story>`), reused as they are (see "Returning characters").
- The proven sheets: `stories/faille-d-amour/02-cast/*/sheet.md` (≈ 70-word heads that held).

## Propose

No questions first. All the sheets in one message, every choice visible: name, age, gender, role, what they are
(fruit / species), outfit and colours, one mark, one signature item, personality, want, fear, secret, voice, how
they talk. Then: "Here is the cast — correct anyone, or add what you want." Once Rida says yes, lock the sheets
(gate 3) and start the run.

## Run (no questions)

Say once, in one line, what the run makes and its estimate (e.g. "3 characters: 12 pictures, 3 keyframes, 9 casting
clips ≈ $0.60 warm, up to $1.50 cold"), then go on. Per character:

1. **Full body** — 4 seeds of your full-body prompt: `comfy_submit(story, "t2i_flux2_klein", {"prompt": <yours>,
   "seed": <k>, "width": 832, "height": 1216}, dest="02-cast/<id>/candidates/full_body_c<k>")` → `comfy_fetch`
   → look at each: the right fruit/species and colours, the outfit, hands, one character only, nothing written.
   Pick the best → `store_copy` to `02-cast/<id>/full_body.png` → `store_lock` ("Claude's pick: <why>").
   None good → 4 new seeds (adjust the prompt from what went wrong).
2. **Turnaround** — 1 edit of the locked full body (`edit_flux2_klein_multiref`, the full body in `ref1`–`ref4`,
   832×1216) → check it is the same character → `store_copy` to `turnaround.png` → `store_lock`.
3. **Casting keyframe** — a plate for the casting place if the story has none (`03-places/<place>/plate.md`,
   `## Setting`, one sentence, nobody in it); `ep00/shots.json` with one 5 s shot per character
   (`{"id": "sNN", "seconds": 5, "place": …, "characters": ["<id>"], "lines": [{"speaker": "<id>", "text": <8–10
   words in the story's language, said the way they talk>}], "keyframe_prompt": …, "clip_prompt": …}`); 2 seeds
   of the keyframe (`edit_flux2_klein_multiref`, the full body as references, 704×1280, `faces_camera`) → pick →
   `store_copy` to `ep00/keyframes/sNN.png` → `store_lock`.
4. **Casting clip** — 3 seeds (11, 22, 33) of your one-speaker clip prompt: `comfy_submit(story,
   "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": 5}, files={"image": "ep00/keyframes/sNN.png"},
   episode=0, shot="sNN")` → `comfy_fetch` → `verify_take` each.
5. **The voice** — among the takes whose check is `ok`, pick the one whose voice best fits the sheet's voice and
   whose picture holds → `approve_take(…, note="Claude's pick: <why>")` → `voice_ref_from_take(story, 0, "sNN",
   <take>, "<id>", note="Claude's pick: <why>")`. No `ok` take → 3 new seeds.

Then present the cast (gate 4). A correction → `store_unlock` with Rida's reason, redo only that part, present again.

**Returning characters** — a character Rida approved in another story keeps everything: copy their locked
`sheet.md`, `full_body.png`, `turnaround.png` and `voice_ref.wav` with `store_copy(story, src, dest,
from_story=<other story>)`, lock them ("approved in <other story>"), and skip steps 1–5 for them. Their sheet is part
of your gate-3 proposal, marked "returning, unchanged" — Rida may still change their outfit or role (then the
picture is made again; the voice stays).

## Template

`02-cast/<id>/sheet.md` (skeleton `showrunner/prompts/character_sheet.md`; `<id>` lowercase, `_` between words):

```
# <Name>

## Head
<Name>, a <who, in the universe's words>: <the head and face: shape, colours, eyes, brows, mouth, one permanent
mark>; <the body and outfit, colours named>, <one signature item>, <the hands rule of the universe>

## Voice (en)
<age, tone, pace, texture, in English>

## Voice (fr)
<the same in French>

## Colour
<the subtitle colour of their name, hex>

## Wants
## Fears
## Secret
## How they speak
<rhythm, words they use, what they never say; "vous" or "tu" in French>
## Signature item
```

`## Head` is 65–80 words, one sentence, no final period, every visible thing named once with its colour; it opens
every picture and clip prompt of this character, word for word.

## Checklist

- [ ] Every sheet: a `# Name` title, a 65–80-word `## Head` in the positive, both voices, a colour.
- [ ] Two characters are told apart at a glance (silhouette, main colour, signature item).
- [ ] Sheets locked with Rida's words before any picture; the run's estimate said in one line.
- [ ] Each pick looked at and explained in its note; no pick with a wrong identity, an extra person or text.
- [ ] Each voice comes from an `ok` casting take, 2–10 s of that character alone.
- [ ] The spend stayed under twice the estimate (else you stopped and asked).

## Gate question

Gate 3 (sheets): "Here is the cast — correct anyone, or add what you want." Gate 4 (finished cast): "Here is the
cast with their voices — keep it, or what do we change?"

## What to show Rida

Gate 3: the sheets in short (the Head, the voice, the want, the secret). Gate 4, per character: the full body and
the turnaround (`view_file`), the casting clip and the locked voice as files to play (`file_download`), one line on
why you picked them. Then what the run really cost.

## Tools

- `store_read`, `store_write` — free: the brief, the universe, the sheets, plates and `ep00/shots.json`.
- `templates_list` — free: the templates, their sizes and the files they take.
- `comfy_submit` — COSTS MONEY: each picture or casting clip, inside the estimate you announced.
- `comfy_fetch`, `comfy_jobs` — free: collect and see the results; what is still in the queue.
- `view_file` — free: look at a picture or a clip's contact sheet again.
- `store_copy`, `store_lock`, `store_unlock` — free: the picked candidate becomes the canonical file, locked.
- `verify_take` — free: the clip check of a casting take.
- `approve_take` — free: the chosen casting take.
- `voice_ref_from_take` — free: cut and lock the voice from that take.
- `file_download` — free: hand Rida a casting clip or a voice to play.
- `cost_ledger`, `runpod_health` — free: what was spent; whether workers are warm or the queue is long.

## Next

`steps/4-script.md`: the season map and episode 1's script.
