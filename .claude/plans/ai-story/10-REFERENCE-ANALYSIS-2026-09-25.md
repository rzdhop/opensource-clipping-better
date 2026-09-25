# Reference-video analysis — two inspiration TikToks (analysed 2026-09-25)

Method: `ffprobe`; scene cuts with `select='gt(scene,0.30)'` (also 0.20/0.40 for
sensitivity); one frame per cut → contact sheets (`reference-v1-contact-sheet.jpg`,
`reference-v2-contact-sheet.jpg` next to this file); caption band sampled at 4 fps for
the burned words; `loudnorm` measurement and `silencedetect` on the audio. **Audio could
not be transcribed** (model downloads and hosted STT are blocked from the analysis
sandbox), so dialogue comes from the burned captions of video 1; video 2 has no burned
captions, its dialogue is inferred from the images only. Everything below feeds the
assumptions of spec §6.2 / §14 and the two styles `fruit_drama` and `family_3d`.

## 1. Video 1 — `Download 1.mp4` (account watermark `@il_etait_un_fruit`, French)

| Measure | Value |
|---|---|
| Container | 576×1024 (9:16), 24 fps, 77.3 s, AAC stereo |
| Detected cuts at 0.30 | 30 (31 segments); several are caption flips, not cuts — real cuts ≈ 24 |
| Shot length | mean ≈ 3.2 s (real cuts), median ≈ 2.5 s; longest 7.8 s (empty manor) and 11 s (couch scene); reaction cuts down to 0.8 s |
| Audio | continuous bed (no silence > 0.4 s in 77 s except one 0.65 s gap at 62.3 s); integrated −21.3 LUFS, TP −1.8, LRA 5.4 |
| Captions | **one word at a time**, uppercase, white bold italic sans with black outline, centred ≈ 75–80 % of height; a new word roughly every 0.25–0.5 s; no two-line subtitles |
| Overlays | "AI-generated" small label bottom-left (platform disclosure); TikTok handle |
| Narrator | none — 100 % character dialogue |
| Language | French |

Style: photorealistic 3D — **fruit head at human head scale on a human body wearing real
clothes** (banana in torn tee, jeans, backpack; kiwi agent in black suit and tie;
tomato/lemon/peach/apple squatters in hoodies and leather jackets). Faces are carved into
the fruit skin (eyes, brows, teeth). Real-world set (French manor gate, gravel courtyard,
derelict interior with chandeliers, dust, candles, pizza boxes). Golden-hour exterior,
cold blue-grey interior with warm candle practicals. Lens: 35–50 mm feel, shallow DoF,
medium singles and two-shots for dialogue, tight close-ups for reactions, **insert shots
of props** (the sign, the contract clause) used as plot devices. Motion is true video
(lip movement, head turns, walking, the agent running off) — Tier 2/3 quality.

Structure (beats, from the frames and captions):

| t (s) | Beat | Function | Shots |
|---|---|---|---|
| 0.0–1.6 | banana walks past a manor gate with a sign | hook (visual) | 1 wide |
| 1.6–2.5 | **insert: "À VENDRE — MANOIR — PRIX : 1 €"** | hook (diegetic text) | 1 insert |
| 2.5–9.5 | "attends… quoi ? si… pour tout ça, c'est vraiment bizarre… c'est une erreur, non ?" — banana calls the number | setup | 3 |
| 9.5–27 | the kiwi agent appears instantly, contract on clipboard, "je travaille vite… pas besoin de visiter pour 1 € ? allez, signez-là… et la clé, voilà, félicitations, le manoir est à vous"; **insert of the contract clause**: "Le nouveau propriétaire accepte l'intégralité des occupants déjà présents" — "quels occupants ?" — "trop tard, vous avez signé" | rising → turn | 8 (inserts, close-ups, two-shots) |
| 27–30 | the agent runs away (wide, back view) | turn | 1 |
| 30–49 | inside: derelict, dust, "ouah… mais pourquoi… c'est là-dedans… vieilles maisons font des bruits… les maisons ne sont pas censées marcher" | rising (dread) | 4 long shots (5–8 s) |
| 49–59 | occupants appear one by one (peach, apple behind a door, group in the hall) — "quoi ?" | peak | 5 quick cuts (0.8–3 s) |
| 56–70 | couch, candles, pizza: the squatters greet the new owner ("vous êtes le nouveau propriétaire… derrière… le même…") | peak → turn | 3 (incl. an 11 s two-shot) |
| 70–77 | apple over the banana's shoulder, "non… mais…" — **ends mid-confrontation, no resolution** | cliffhanger | 3 |

Retention mechanics observed: a **price/absurd-premise hook readable in 1.6 s**, a
**contract insert** that plants the twist in writing, escalation by cheap reaction
cuts, one long dread section with no dialogue, then a cast reveal, and an **abrupt open
ending** (no "part 2" card in this export — the platform "reply" carries it).

## 2. Video 2 — `Download.mp4` (account watermark `@fruit.and.vegg.dr`)

| Measure | Value |
|---|---|
| Container | 576×576 (1:1 export of a vertical or 4:3 source), 25 fps, 99.4 s |
| Detected cuts at 0.30 | 23 (24 segments), no captions so all are real cuts |
| Shot length | mean 4.1 s, median 3.4 s, min 1.3 s, max 11.2 s |
| Audio | continuous bed, no silence > 0.4 s; integrated −20.4 LUFS, TP −6.2, LRA 4.7 |
| Captions | none burned |
| Cast | 2: an orange-skinned mother (orange-peel skin texture, green leaf hair with a white blossom, torn cream blouse and brown corset, patched skirt) and her small daughter (same design, grey pinafore) |
| Place | one: a ruined stone room, cracked plaster, broken window with grey daylight, wooden table, standing mirror, knife on the table |

Style: **3D animated family-film look** (large eyes, soft rounded shapes, subsurface
skin, painterly set), muted grey-blue environment with the orange skin as the only warm
accent, soft window key light. Coverage is almost entirely **the same setup reframed**:
medium single at the table, close-up on the mother's face, two-shot with the child in the
mirror or background, insert on the hands holding a strip of peel. The story: the mother
peels a strip of her own skin (orange peel) at the mirror, smiles through tears, hands it
to her daughter to eat — a sacrifice melodrama in one room, one continuous emotion arc,
ending on the child holding the peel (tender, unresolved).

Retention mechanics: one strong emotional image repeated with escalation (peel → smile →
tears → gift), no dialogue needed to follow, long holds (8–11 s) on the emotional beats,
a single consistent location and lighting that makes 24 shots look like one scene.

## 3. Measured vs assumed (spec §6.2 / §14)

| Assumption | Measured | Consequence |
|---|---|---|
| Average shot 3–6 s | v1 ≈ 3.2 s mean / 2.5 s median; v2 4.1 / 3.4 | **Confirmed** for the body; reaction cuts go down to 0.8–1.3 s → allow 2–3 shots per scene and a 0.8 s minimum shot in the closed rules |
| ≤ 90 characters of dialogue per scene | v1: 1–2 short lines per shot, 3–8 words each | **Confirmed**; line cap ≤ 22 words is generous — set the default target to 6–12 words |
| Hook text in the first 1.5 s | v1: no overlay text; the hook is a **diegetic insert shot** (sign with price) at 1.6 s; v2: no text at all, the hook is the image (mother peeling her own skin) | **Invalidated as a rule** → hook on-screen text becomes optional; add `insert_prop` and "shocking image" hook variants |
| Cut-to-black cliffhanger | v1 ends abruptly mid-line, no black, no end card; v2 ends on a held tender shot | **Partially invalidated** → cut-to-black + end card stays an option (default on, matches "Part N" serialization) but "hard stop mid-beat" is added as a cliffhanger style |
| Dialogue-driven, no narrator (Fruit Drama) | v1 100 % dialogue; v2 (family_3d) has no captions — dialogue or silence, no visible narrator | **Confirmed** |
| Episode 55–75 s | v1 77 s, v2 99 s | **Widen**: keep target 60 s, default window 55–80 s, add template `serial_90s_v1` (75–100 s) |
| 8–12 scenes | v1 ≈ 8 beats / 24 shots; v2 ≈ 5 beats / 24 shots | Scenes confirmed; **shots per scene 2–4**, not 1–2 |
| Two-line karaoke subtitles | v1 uses **single-word pop captions**; v2 none | Add subtitle mode `word_pop` (default for `fruit_drama`), `two_line` (default for `cinematic_real`, `storybook_watercolor`), `none` (default for `family_3d`, user-switchable) |
| Music + SFX bed | continuous bed in both, no silence; ≈ −21 LUFS integrated | Keep the −14 LUFS target (platform normalises), never leave silence between shots: BGM/ambience always runs under the whole episode |
| One or two places per episode | v1: 2 (gate, interior); v2: 1 | Confirmed → master plates matter more than variety; E1 should default to ≤ 2 places |
| AI disclosure | both carry "AI-generated" | Add an optional small `ai_label` overlay (default on) |
| Visual tier | both are true video with lip motion (Tier 2/3) | Tier 1 (stills + motion) is a **budget approximation** of this genre, not a match; the per-shot "animate" upgrade path (phase 6) is what closes the gap |

## 4. Template refinements to apply

- `fruit_drama`: confirmed rendering rules (fruit head on human body, real clothes,
  carved faces). Add to `character_design_rules`: "clothing tells social status (torn tee
  vs suit)". Palette confirmed (saturated fruit vs warm neutral exteriors, cold blue-grey
  interiors with candle practicals). Subtitle mode `word_pop`; hook default
  `insert_prop`; cliffhanger default `hard_stop` with end card optional.
- `family_3d`: confirmed look. Add "environment muted, character carries the only warm
  accent colour" as a palette rule and "reframe the same setup" as a coverage rule
  (cheap and consistent). Subtitle mode `none` by default; longer holds allowed (up to
  11 s on emotional beats → scene clamp up to 12 s for `peak`/`tender` in this template).
- Both: continuous BGM/ambience bed; `ai_label` overlay on.
