# Plan 27 — Shots of 5–10 s that carry an exchange (several lines per shot, sized to the clip)

Written 2026-10-05 from the human's ask: "narrow the clips to 5–10 seconds; more text per shot, more talk; I don't
want only one dialogue line per clip — see how many lines fit in 5 s or 10 s". EXPLORE by a Sonnet agent (the map
is in this file's appendix).

## Facts (from the map)
- A shot's length is never written by the model: on a native-speech board `shots.speech_shot_plan` makes ONE shot per
  character line and `clip_s` = the smallest sold length that holds the line (`native_speech.speech_clip_s`;
  `SPEECH_LENGTHS = (4, 6, 8)`; `SPEECH_WPS = 2.4`, 0.7 s overhead → 5 s: 10 words, 6 s: 12, 8 s: 17, 10 s: 22).
  Reaction shots `REACTION_S = 4`. The line plan (plan 24) gives each line only a MAX of words (a 5-word line on a
  6 s clip is legal → the line lands early, then dead air).
- Sold lengths: Veo 4/6/8 (never 10); Flow 8 only; kling 5/10; seedance 2–12; ltx 6–20. The handoff shows "planned
  6 s, pick 8 s" on Flow.
- Every native consumer reads ONE line per shot: `clips.speech_line`, `speech_prompt_inputs`,
  `prompting.speech_clip_prompt` / `_action` ("only X's voice… no other voice"), `speech_prompt_sentences`,
  `brief._checks` ("the one speaker"), `native_speech.evaluate_take` / `line_placements` (one `line_id` per take).
  A shot's `lines` is already a list in the schema and in T1v2.
- The clip hash excludes `clip_s`; a one-line prompt stays byte-identical if the multi-line path is a separate branch
  (the `studio_prompts.json` / `before_d5.json` goldens hold). A rewritten line stales its clip (the quote is in the
  hash); a changed line COUNT gives new shot ids and new keyframes.

## Design
**Capacity.** A shot of L seconds holds `words(L) = floor((L − 0.7) × 2.4)`: 5 s → 10, 6 s → 12, 8 s → 17, 10 s → 22.
An exchange fills ≥ 75 % of it. Lines per shot by length (French, short lines of 5–8 words):

| clip | words | lines |
|---|---|---|
| 5 s | 10 | 1–2 |
| 6 s | 12 | 2 |
| 8 s | 17 | 2–3 |
| 10 s | 22 | 3–4 |

**The window.** Shots are 5–10 s, clamped to what the link sells: Veo API 6/8, Flow 8, kling 5/10, seedance 5–10,
ltx 6/8/10, manual 6/8 (Flow). `REACTION_S` 6. Template `min_shot_s` 5, `max_shot_s` 10; slots reworked (hook 5–8,
body 10–16, cliffhanger 6–10); the take trim floors at 5 s.

**The exchange.** The scene plan (plan 24's `line_plan`) groups consecutive lines into shots by capacity: each shot
gets `clip_s` from the sold lengths and a word budget `[0.75 × words(L), words(L)]` split over 1–4 lines with
alternating speakers (the narrator's line stays its own silent-clip shot). The writer (E2v3/E3v3) is told per line
"between lo and hi words" and per shot "these N lines are one continuous exchange in one shot of L seconds; the last
line ends the shot". `speech_shot_plan` stops splitting: a shot keeps its `lines` list.

**The clip prompt.** A new branch when `len(lines) > 1`: `prompting.speech_exchange_clip_prompt` quotes each line in
order with its speaker ("Marie-Jeanne says in French, in a sharp whisper, "…"; Rida answers, "…"; …"), the listeners
"listen without speaking" between their lines, `Audio: the voices of Marie-Jeanne and Rida only, speaking French in
turn, lips in sync with the words; no other voice`. One-line shots byte-identical (the goldens hold). The pacing line
of the master template (plan 26) says "the exchange fills the whole L-second clip". The take: `line_placements` per
line in order; `evaluate_take` matches every line (the ear's transcript split by speaker turn); the brief's checks say
"N lines, the speakers in turn".

## Stages
1. **Lengths (5–10, clamped to the link).** `clips.link_lengths` / `sold_lengths` window; `SPEECH_LENGTHS` /
   `PLAN_SPEECH_LENGTHS` (4 dropped); `REACTION_S` 6; the two v2 templates' `min_shot_s`/`max_shot_s`/slots;
   `native_speech.shot_seconds` + `assets.py:4005/4064` floor at 5; `_speech_fields` keeps a kept shot's clip_s.
   Pins: test_story_native_speech_plan (the 4/6/8 table), test_story_timing_plan, test_story_confrontation_template,
   test_story_native_take, test_story_manual_link (`length_s == 8`). Risk: low on made assets (clip_s outside the hash;
   kept shots keep ids).
2. **The line plan sizes lines to the shot and groups them into exchanges.** `timing.scene_plan` / `plan_budget`:
   per-line `min_words` + the exchange grouping (`shots: [{clip_s, line_ids}]` on the scene plan); `plan_line_caps` /
   `_plan_line_errors`; the E2v3/E3v3 asks (`PLAN_LINE_V3` "between lo and hi words", `NATIVE_LINE_V3` the exchange
   sentence); the trim pass keeps the floor. Pins: test_story_prompts_v3 (measured lengths re-measured with the reason),
   test_story_timing_plan, test_story_native_speech_plan. Risk: a re-scripted scene gets new shot ids (new keyframes).
3. **One shot per exchange + the multi-line clip prompt (riskiest).** `shots.speech_shot_plan` stops splitting;
   `clips.speech_line(s)` / `speech_prompt_inputs` carry the list; `prompting.speech_exchange_clip_prompt` (+ action
   style); `speech_prompt_sentences` multi-quote; `brief._checks` + `shot_entry` wording; the master's pacing line.
   One-line shots byte-identical: the goldens must not move. Pins: test_story_native_speech_clips, test_story_manual_link.
4. **The take per line.** `native_speech.line_placements` / `evaluate_take` / `assets._take` per line in order;
   `manual_uploads` the same. Pins: test_story_native_take, test_story_render_native_speech.
5. **Docs + close-out** (docs/AI_STORY.md "The timing harness" + "Your own clips"; DEC-304; A-177…; CHECKPOINT) +
   the deploy at 0 jobs + the human's regen of one episode to see 2–4 lines per shot.

## Decisions for the human
- D1 On Flow/Veo the real range is 6–8 s (Veo never sells 10): the window clamps to the link. OK? (default yes)
- D2 Dragon Fruit: the exchanges need a re-script of the episode (new lines → new shot ids → new keyframes, ≈ $1.50
  + the script calls). Apply on your next "Regenerate episode"; nothing changes for made assets until then. OK?

## Rejected
- Longer single lines only (2–3 sentences per line): fills the clip but is not "more story"; the human asked for lines.
- Letting the model write durations: T1v2 says "never output durations" on purpose (plan 24); the derivation stays.
- Re-timing only (no new lines) for Dragon Fruit: a longer line overflows its old clip_s (the retime path does not
  re-run the shot plan).

## DECISIONS check
DEC-298/300 (plan 24: the per-line plan, the hard caps, one shot per line) — amended: lines get a floor and shots
carry an exchange. DEC-292 (prompt pins) — one-line prompts byte-identical. DEC-258 (lipsync lengths) — the window
composes with it. No other conflict.
