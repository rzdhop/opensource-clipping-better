---
name: story-assemble
description: Step 6 of an AI Story series on the showrunner connector. Use when every shot of an episode has an approved take, to choose the music bed, sound effects, hook text and end card, assemble the final vertical mp4, write its metadata and hand it to Rida. Free.
---

# Story, step 6: the assembly (free)

The server cuts the episode from the approved takes only: each clip trimmed after its last word, never slowed,
subtitles from the clip check, the music ducked under the voices, the end card. You choose the music, the
effects, the hook text and the card; Rida watches the result.

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

- Every shot of `epNN/shots.json` has an approved take (`assemble_episode` refuses otherwise, naming the shots).
- Re-assembling after Rida changed the music, the card or a take.

## Read first

- `store_read epNN/shots.json`, `epNN/script.md` (the cliffhanger), `00-brief.md` (the comment bait),
  `04-season.md` (this episode's place in the season).

## Ask first

Before cutting the episode:

- Which music mood (suspense, sad, epic, chill, upbeat), which track (name the candidates), and how loud?
- Sound effects: yes or no, and where (a sting on the cliffhanger)?
- Which hook text on the first seconds (or none), and which lines on the end card?
- Which title, and which comment-bait question?

## Template

Fill the top of `epNN/shots.json` (`store_write`; unlock first if Rida locked it, with his reason):

```json
"bgm": {"file": "assets/bgm/suspense/the_mountain-cinematic-mood-129193.mp3", "gain": 0.25},
"hook": {"text": "<the hook in ≤ 6 words, the story's language>", "seconds": 2.5},
"card": {"lines": ["<Title>", "<the comment bait, short>", "Partie 2 demain"], "seconds": 2.0},
```

and per shot, when a pause needs one: `"sfx": [{"file": "assets/sfx/soap/dramatic_sting.wav", "at": 4.2,
"gain": 0.8}]` (`at` = seconds into that shot's clip, inside a pause, never over a line).

The music beds (`assets/bgm/`): `suspense/` (the_mountain-cinematic-mood, the_mountain-epic-mood,
dueling-music-box-horror) · `sad/` (melancholy calm piano, silhouette-2026ver, 1800s ambient mood) · `epic/`
(rock, adventure trailer, epic cinematic thriller) · `chill/` (background music, peaceful café jazz) · `upbeat/`
(talking-about-love romantic piano; the same folder also holds a lullaby, a devotional song and a nature ambient —
pick by name). Gain 0.2–0.3; the bed is ducked under the voices.

The effects (`assets/sfx/<pack>/`): `soap/` (dramatic_sting, gasp_crowd, door_slam, slap, phone_ring,
waves_soft) · `real/` (breath, car_pass, footsteps_concrete, phone_buzz, room_tone) · `anime/` (chime, heartbeat,
impact_hit, rain_loop, whoosh_sharp) · `cartoon/` (boing, honk, pop, record_scratch, slide_whistle) ·
`cartoon_soft/` (footsteps_tiny, twinkle, whoosh_soft) · `foley/` (clay_squish, footsteps_felt, paper_rustle,
tiny_bell, wood_knock) · `gentle/` (birds, footsteps_grass, page_turn, wind_soft). Choose the pack that fits the
universe (soap for a telenovela, foley for claymation…); a sting on the cliffhanger.

Then `assemble_episode(story, N)` → a contact sheet and the report (length, loudness, each shot's cut) →
`file_download epNN/final.mp4` for Rida.

`epNN/metadata.md`:

```
# <Title> — episode <N>

## Title
<series label + episode number, e.g. "Faille d'amour — Partie 1">

## Hook text (2 variants)
- <variant A, ≤ 6 words>
- <variant B>

## Comment bait
<the question pinned under the video>
```

## Checklist

- [ ] Every shot approved (the tool names any that is not).
- [ ] Music matches the episode's mood; gain 0.2–0.3; no effect over a line.
- [ ] The hook is readable without sound; the card says "Part N+1" in the story's language.
- [ ] The report checked before Rida sees it: total length (≈ 60–90 s), no clip slowed, loudness not clipping,
      subtitles off the faces on the contact sheet.
- [ ] `metadata.md` written: title with the series label and episode number, two hook variants, the comment bait.

## Gate question

"Episode N is ready (<length> s): is it good to post, or what do we change?"

## What to show Rida

The contact sheet and the numbers in one line, then the mp4 as a file to play, then the metadata (title, the
two hooks, the comment bait). If he wants a change: music, card, an effect, a take — say which step it goes back to.

## Tools

- `store_read`, `store_write` — free: `shots.json` (bgm, hook, card, sfx), `metadata.md`.
- `store_unlock` — free: only if `shots.json` is locked, with Rida's reason.
- `assemble_episode` — free (this server's CPU, ≈ 1 min for 30 s): the final mp4, its contact sheet and report.
- `view_file` — free: the final's contact sheet again.
- `file_download` — free: hand Rida `epNN/final.mp4`.
- `cost_ledger` — free: what the episode cost in all, said with the delivery.

## Next

`story-next-episode`: the series memory and the next episode's direction.
