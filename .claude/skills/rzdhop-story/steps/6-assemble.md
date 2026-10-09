# Story, step 6: the assembly and Rida's review of the episode (free)

You choose the music bed, the effects, the hook text and the end card yourself; the server cuts the episode from
the approved takes (each clip trimmed after its last word, never slowed, subtitles from the clip check, the music
ducked under the voices) and makes the faster edit by itself: inside each clip the first line plays wide, then the
picture punches in on each next speaker (a crop of the same moving clip toward the speaker, from the order of
`characters` or the shot's `positions`), and a long one-character clip punches in once mid-way. Then Rida reviews
the whole episode — his gate — and, once he confirms it, you ask whether he wants the next one.

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
   cliffhanger; a soft hit such as `whoosh_soft` or `impact_hit` on the one or two strongest punch-ins, at the
   piece's start).
2. `assemble_episode(story, N)` → check the report: total length (≈ 60–90 s), no clip slowed, loudness not
   clipping, subtitles off the faces on the contact sheet, and the punch-ins (`cuts`, each segment's `pieces`):
   about two to three framings per two-character clip, each punch-in on its speaker with the head whole. A speaker
   on the wrong side → set the shot's `positions`; a clip that should stay wide → `"punch_in": false`; then
   re-assemble (free).
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

When Rida asks for changes: a take → `store_unlock` it with his reason and redo that shot (`steps/5b-clips.md`, or
`steps/5a-shots.md` first for a new keyframe); the music, an effect, the hook or the card → edit `shots.json`;
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
- [ ] The report checked before Rida sees the episode, the punch-ins included (each on its speaker, heads whole).
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

If Rida wants the next episode: `steps/7-next-episode.md`.
