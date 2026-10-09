---
name: story-assemble
description: Step 6 of an AI Story series on the showrunner connector. Use when every shot of an episode has an approved voiced take, to choose the music, effects, hook and end card yourself, cut the final vertical mp4, present the locked clips and the episode to Rida for his review, then ask whether he wants the next episode. Free.
---

# Story, step 6: the assembly and Rida's review of the episode (free)

You choose the music bed, the effects, the hook text and the end card yourself; the server cuts the episode from
the approved takes (each clip trimmed after its last word, never slowed, subtitles from the clip check, the music
ducked under the voices). Then Rida reviews the whole episode — his gate — and, once he confirms it, you ask
whether he wants the next one.

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

- Every shot of `epNN/shots.json` has an approved take (`assemble_episode` refuses otherwise, naming the shots).
- Re-assembling after Rida's review changed a take, the music or the card.

## Read first

- `store_read epNN/shots.json`, `epNN/script.md` (the cliffhanger), `00-brief.md` (the comment bait),
  `04-season.md` (this episode's place in the season), `epNN/defects.md`.

## Run (no questions)

1. Fill the top of `epNN/shots.json` (`store_write`) with your choices:

   ```json
   "bgm": {"file": "assets/bgm/suspense/the_mountain-cinematic-mood-129193.mp3", "gain": 0.25},
   "hook": {"text": "<the hook in ≤ 6 words, the story's language>", "seconds": 2.5},
   "card": {"lines": ["<Title>", "<the comment bait, short>", "Partie 2 demain"], "seconds": 2.0},
   ```

   and per shot, when a pause needs one: `"sfx": [{"file": "assets/sfx/soap/dramatic_sting.wav", "at": 4.2,
   "gain": 0.8}]` (`at` = seconds into that shot's clip, inside a pause, never over a line; a sting on the
   cliffhanger).
2. `assemble_episode(story, N)` → check the report: total length (≈ 60–90 s), no clip slowed, loudness not
   clipping, subtitles off the faces on the contact sheet. Fix and re-assemble if needed.
3. Write `epNN/metadata.md` (template below).
4. Present the episode (below) and wait for Rida's review.

The music beds (`assets/bgm/`): `suspense/` (the_mountain-cinematic-mood, the_mountain-epic-mood,
dueling-music-box-horror) · `sad/` (melancholy calm piano, silhouette-2026ver, 1800s ambient mood) · `epic/`
(rock, adventure trailer, epic cinematic thriller) · `chill/` (background music, peaceful café jazz) · `upbeat/`
(talking-about-love romantic piano; also a lullaby, a devotional song and a nature ambient — pick by name).
Gain 0.2–0.3. The effects (`assets/sfx/<pack>/`): `soap/` (dramatic_sting, gasp_crowd, door_slam, slap,
phone_ring, waves_soft) · `real/` (breath, car_pass, footsteps_concrete, phone_buzz, room_tone) · `anime/` (chime,
heartbeat, impact_hit, rain_loop, whoosh_sharp) · `cartoon/` (boing, honk, pop, record_scratch, slide_whistle) ·
`cartoon_soft/` (footsteps_tiny, twinkle, whoosh_soft) · `foley/` (clay_squish, footsteps_felt, paper_rustle,
tiny_bell, wood_knock) · `gentle/` (birds, footsteps_grass, page_turn, wind_soft). Choose what fits the universe
(soap for a telenovela, foley for claymation…).

When Rida asks for changes: a take → `store_unlock` it with his reason and redo that shot (`story-clips`, or
`story-shots` first for a new keyframe); the music, an effect, the hook or the card → edit `shots.json`;
then re-assemble and present again.

## Template

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
- [ ] The report checked before Rida sees the episode.
- [ ] `metadata.md` written: title with the series label and episode number, two hook variants, the comment bait.
- [ ] After Rida confirms: `store_lock epNN/final.mp4` with his words, then the next-episode question.

## Gate question

"Episode N is ready (<length> s). Good to post, or what do we change?" — and once he confirms: "Do you want
episode N+1?"

## What to show Rida

1. The final mp4 as a file to play (`file_download epNN/final.mp4`), with one line: its length, what it cost in all
   (`cost_ledger`).
2. The locked clips, each with its voice, as files to play (`file_download epNN/clips/sNN_vK.mp4`, the approved
   take of each shot), in order, one line each (shot, who speaks).
3. The music, the hook, the card and the metadata in three lines, so he can change them.

## Tools

- `store_read`, `store_write` — free: `shots.json` (bgm, hook, card, sfx), `metadata.md`.
- `store_lock`, `store_unlock` — free: the final once confirmed; a take Rida rejects, with his reason.
- `assemble_episode` — free (this server's CPU, ≈ 1 min for 30 s): the final mp4, its contact sheet and report.
- `view_file` — free: the final's contact sheet again.
- `file_download` — free: hand Rida the final and the locked clips.
- `cost_ledger` — free: what the episode cost in all.

## Next

If Rida wants the next episode: `story-next-episode`.
