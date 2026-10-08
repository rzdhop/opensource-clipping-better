# Plan 36 stage 3 — dry run of steps 1–4 (no GPU, $0)

Story `stories/les-heritiers-du-fournil/`, French, claymation animal people (a non-fruit universe, D8). Every step went through the showrunner server's own tools, in process, with no GPU endpoint configured.

## Step 1 — concepts (`story-concepts`)

Three concepts written to `00-brief.md` (inheritance / double life / the new arrival), each with the last 5 s of episode 1 first; concept 1 picked for the dry run; the 10-episode season map in `04-season.md` with a rotating cliffhanger shape.

## Step 2 — universe (`story-universe`)

`## Medium` (79 words), the style lock of every prompt:

> A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand, with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of field like a tabletop model.

## Step 3 — cast (`story-cast`): the sheets, and the prompts it would send

| Character | Head words | full_body prompt words | template | size |
|---|---|---|---|---|
| Mireille | 70 | 192 | t2i_flux2_klein | 832x1216 |
| Théo | 70 | 192 | t2i_flux2_klein | 832x1216 |
| Suzon | 70 | 192 | t2i_flux2_klein | 832x1216 |
| Maître Corbeau | 69 | 191 | t2i_flux2_klein | 832x1216 |

- Turnaround before a full body is locked: refused as it should be — *turnaround starts from the locked 02-cast/mireille/full_body.png: pick and lock the full body first*
- The casting reel `ep00/shots.json`: one 5 s shot per character, a line in their voice; every keyframe and clip prompt built (t2i until the full bodies are locked), budgets:

  - Mireille (s01): « Ici, c'est moi qui décide. Même le pain m'obéit. » — 9/10 words, fits: True
  - Théo (s02): « Je suis revenu pour papa. Enfin… surtout pour son coffre. » — 10/10 words, fits: True
  - Suzon (s03): « Trente ans que je garde ses secrets. Et les vôtres. » — 10/10 words, fits: True
  - Maître Corbeau (s04): « Le testament sera lu ce soir. Pas avant. » — 8/10 words, fits: True

- A paid call in the dry run is refused (no endpoint): *no images endpoint configured (RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID / RUNPOD_IMAGE_ENDPOINT_ID)*

What the cast step would cost, after Rida's go per batch (4 characters): 4 × (4 full-body candidates + 2 turnaround + 2 expression grids + 2 casting keyframes) = 40 images ≈ $0.40 warm / $1.20 cold; 4 × 3 casting clips = 12 clips ≈ $0.40–0.60 warm / ≈ $1.60 cold. Total ≈ $0.80–2.80.

## Step 4 — script (`story-script`)

`ep01/script.md`: 11 shots, 12 lines, 80 s of clips (+ the end card), cliffhanger first (« Suzon ? Notre vendeuse ? », cut on Suzon before her answer), two silent reactions, four two-character shots (three with both speaking, D7). The word budget of every shot, from `prompt_clip`:

| Shot | s | In frame | Words / max | Fits |
|---|---|---|---|---|
| s01 | 5 | Mireille | 10 / 10 | yes |
| s02 | 5 | Théo | 9 / 10 | yes |
| s03 | 10 | Suzon, Mireille | 14 / 22 | yes |
| s04 | 5 | Suzon | 0 / 10 | yes |
| s05 | 10 | Maître Corbeau | 10 / 22 | yes |
| s06 | 10 | Maître Corbeau | 11 / 22 | yes |
| s07 | 5 | Théo | 0 / 10 | yes |
| s08 | 10 | Maître Corbeau, Théo | 14 / 22 | yes |
| s09 | 10 | Mireille, Théo | 15 / 22 | yes |
| s10 | 5 | Maître Corbeau | 8 / 10 | yes |
| s11 | 5 | Mireille, Suzon | 3 / 10 | yes |

Lines: 12 (≤ 12); words per line 3–11.

## The prompts the GPU would receive (built by the server, not typed)

### Cast: Mireille, full body

```
A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand, with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of field like a tabletop model. Mireille, a plasticine fox woman in her forties: a pointed russet-orange clay head with a cream muzzle and white-tipped ears, sharp amber bead eyes under thin arched brows, a tight proud mouth, a small pale scar on her left cheek; a slim body in a navy linen apron over a mustard-yellow knit cardigan and a grey wool skirt, a pencil tucked behind one ear, four-fingered clay hands dusted with flour. A full-body character reference: the whole figure from head to feet, standing upright in a relaxed neutral pose, arms loose at the sides, facing the camera, centred on a plain neutral light-grey background, flat even studio light, only this one character, nobody else.
```

### ep00 s01: Mireille's casting keyframe

```
A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand, with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of field like a tabletop model. Mireille, a plasticine fox woman in her forties: a pointed russet-orange clay head with a cream muzzle and white-tipped ears, sharp amber bead eyes under thin arched brows, a tight proud mouth, a small pale scar on her left cheek; a slim body in a navy linen apron over a mustard-yellow knit cardigan and a grey wool skirt, a pencil tucked behind one ear, four-fingered clay hands dusted with flour. Setting: a tiny handmade Paris bakery shop at dawn: a wooden counter with baskets of golden baguettes, glass jars of sweets, wooden shelves of round loaves, warm lamplight and a misted window. Medium close shot from the waist up, Mireille faces the camera, mouth closed, a tense and composed expression, soft cinematic light, vertical 9:16 framing, only this one character, nobody else.
```

### ep01 s03: a two-speaker clip (Suzon, Mireille)

```
Use the provided start image as the first frame. A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand, with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of field like a tabletop model. Suzon, a plasticine hedgehog woman in her sixties: a round warm-brown clay head with a crown of short stubby clay spines, small black bead eyes behind round gold-rimmed glasses, rosy cheeks and a gentle closed smile; a plump little body in a pale pink striped baker's smock over a lavender blouse, a white cotton headscarf, a sprig of dried lavender pinned to her chest, four-fingered clay hands, flat brown shoes. Mireille, a plasticine fox woman in her forties: a pointed russet-orange clay head with a cream muzzle and white-tipped ears, sharp amber bead eyes under thin arched brows, a tight proud mouth, a small pale scar on her left cheek; a slim body in a navy linen apron over a mustard-yellow knit cardigan and a grey wool skirt, a pencil tucked behind one ear, four-fingered clay hands dusted with flour. Setting: a tiny handmade Paris bakery shop at dawn: a wooden counter with baskets of golden baguettes, glass jars of sweets, wooden shelves of round loaves, warm lamplight and a misted window. They speak in turn, each one's mouth moving only on their own line, the other listening and reacting: Suzon says in French, with the voice of an older woman, soft and warm, slow and careful, a voice that has learned to say little: "Le notaire a appelé. Il vous attend tous à dix-sept heures." Mireille says in French, with the voice of a woman in her forties, low and clipped, proud and controlled, every word a small order: "Tous ? Pourquoi « tous » ?" Medium two-shot, the camera holds still. Audio: two distinct voices close to the microphone, quiet room tone, no music. One continuous, clean cinematic shot from the first frame to the last.
```

### ep01 s04: a silent reaction (Suzon)

```
Use the provided start image as the first frame. A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand, with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of field like a tabletop model. Suzon, a plasticine hedgehog woman in her sixties: a round warm-brown clay head with a crown of short stubby clay spines, small black bead eyes behind round gold-rimmed glasses, rosy cheeks and a gentle closed smile; a plump little body in a pale pink striped baker's smock over a lavender blouse, a white cotton headscarf, a sprig of dried lavender pinned to her chest, four-fingered clay hands, flat brown shoes. Setting: a tiny handmade Paris bakery shop at dawn: a wooden counter with baskets of golden baguettes, glass jars of sweets, wooden shelves of round loaves, warm lamplight and a misted window. Suzon listens to someone just off-screen beside the camera, in three-quarter view, the eyeline passing just past the lens, and reacts in silence with the eyes and brows: her smile freezes, then she looks down at her flour-dusted hands; lips closed, a small breath, soft natural motion only. Medium close-up, the camera holds still on the listener. Audio: quiet room tone only. One continuous, clean cinematic shot from the first frame to the last.
```

## What the dry run found (for Rida)

1. **The silent-reaction template still says "listens to someone just off-screen"** (`prompts/clip_reaction.md`, s04 and s07 above). That is the wording that drew a stray human in a keyframe (A-222). In a clip the start image holds the frame, and the one-speaker golden (s33) says the same and passed batch a, so it is left as it is; watch the first reaction clips, and if one invents a person, the fix is the reaction template, not a re-roll.
2. **"animal people" in the Medium.** The universe's own noun (like the demo's "fruit people") contains *people*; batch a held with "fruit people". Watch the first full bodies for a human.
3. **A one-line shot with a silent listener (s11, the cliffhanger) uses the exchange golden**, whose audio sentence says "two distinct voices" (kept word for word, `clip_exchange.md`). The clip check will show whether the listener starts talking; if so, the cliffhanger becomes a one-character shot of Mireille followed by a silent reaction of Suzon.
4. Nothing was locked: every lock needs Rida's words. To go on with this story: Rida picks the concept (or another), approves the universe, then the cast step's first paid batch (4 full-body candidates per character, ≈ $0.16–0.48).

## Money

Spent by the dry run: $0.00 (43 tool calls, 2 refusals, all expected).

## Tool calls

| # | Tool | Error |
|---|---|---|
| 1 | story_list |  |
| 2 | story_create |  |
| 3 | store_write |  |
| 4 | store_write |  |
| 5 | store_write |  |
| 6 | store_write |  |
| 7 | store_write |  |
| 8 | store_write |  |
| 9 | store_write |  |
| 10 | store_write |  |
| 11 | store_write |  |
| 12 | store_write |  |
| 13 | store_write |  |
| 14 | prompt_cast |  |
| 15 | prompt_cast |  |
| 16 | prompt_cast |  |
| 17 | prompt_cast |  |
| 18 | prompt_cast | refused |
| 19 | prompt_keyframe |  |
| 20 | prompt_clip |  |
| 21 | prompt_keyframe |  |
| 22 | prompt_clip |  |
| 23 | prompt_keyframe |  |
| 24 | prompt_clip |  |
| 25 | prompt_keyframe |  |
| 26 | prompt_clip |  |
| 27 | comfy_submit | refused |
| 28 | store_write |  |
| 29 | store_write |  |
| 30 | prompt_clip |  |
| 31 | prompt_clip |  |
| 32 | prompt_clip |  |
| 33 | prompt_clip |  |
| 34 | prompt_clip |  |
| 35 | prompt_clip |  |
| 36 | prompt_clip |  |
| 37 | prompt_clip |  |
| 38 | prompt_clip |  |
| 39 | prompt_clip |  |
| 40 | prompt_clip |  |
| 41 | prompt_clip |  |
| 42 | prompt_clip |  |
| 43 | cost_ledger |  |
