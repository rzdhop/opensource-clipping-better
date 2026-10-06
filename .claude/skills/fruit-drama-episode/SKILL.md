---
name: "fruit-drama-episode"
description: "Use instead of story-director when making a Fruit Drama episode (a short vertical drama with talking fruit, Pixar-style) from a chat with the rzdhop-story MCP server: idea, cast, one episode with pictures, clips and voices on the RunPod GPU, then the finished video. Asks the need first. Needs no shell."
---

# Fruit Drama episode

Source of truth: this file lives in the repository (`.claude/skills/fruit-drama-episode/SKILL.md`). The copy that Claude Desktop loads (the `skills-plugin/.../skills/fruit-drama-episode/SKILL.md` folder) is not updated by a commit: replace it by hand after every change here.

You make one finished vertical episode with the `rzdhop-story` MCP server. You are the writer and the director; the server is the crew (it keeps the story, draws the pictures, makes the clips and the voices on the human's own RunPod GPU, and assembles the video). Everything goes through tool calls: no shell, no scripts, no files to copy by hand.

Talk to the human in French, in plain words (unless he writes in another language). He is not a developer: say "les personnages", "les images", "les clips", "le budget", not "step", "profile", "chain" or "keyframe". Name an internal word only where a tool or a key has to be named. All code and code comments stay in English.

The tools below are named exactly as the server registers them. If one is missing from your list, say so in one line and stop: the server is not the one this skill was written for.

## 0. Ask the need first, always

Before any tool call that writes or spends, ask with AskUserQuestion (multiple choice, 1 to 4 questions). Never start on a guess. Cover:

- **L'idée**, in one sentence (a free answer is fine: "une famille de fruits, un héritage, un secret").
- **La langue**: français (default for this series) or english.
- **Le style**: "Drame de fruits en 3D façon Pixar" by default (the `fruit_drama` preset). Offer another only if he asks.
- **Le budget** en dollars pour un épisode: the default is the plafond of the `own_gpu` profile, 2 $ (story_options shows it under `budget_profiles`). He can lower it; do not propose to raise it.
- **Les voix**: oui (each character gets one fixed voice, cloned on his GPU; the estimate of section 8 includes them) or non (subtitles only).
- **Regarder avant les clips ?** oui = you stop once the pictures are made, show him the contact sheet and wait for his go before the clips are bought; non = it runs through.

Then say back in three lines what you are about to do and wait for his yes.

## 1. The story

`story_create(language="fr", seed_text="<the idea>", preset="fruit_drama")`, then `story_get(story_id)`. Keep the `story_id`.

The preset sets the Pixar look, the pictures and clips on his own GPU, the fruit cast, the recipe of the genre (section 13) and the 60-90 s episode format. Check in `story_get` that `recipe` says `fruit_drama`. Never pass `style` or `episode_format` unless he asked for something different: what you name replaces the preset's choice. If he said no to the voices, add `generation_profile={"voices": "none"}` (only that key: the preset's other keys stay); the cast may still ask you for a voice pick and a sample text, answer them anyway.

`story_options()` lists the presets, the budget profiles with their caps in dollars and the episode formats, if you need to show him a choice.

## 2. The concept

`story_step_start(story_id, "concepts", params={"count": 3})`. The run asks you to write: each call returns state `waiting` with a `pending` prompt (system, user, schema, max_tokens). Answer it with `story_step_answer(handle, answer)`, an object exactly in the schema, in the story's language, inside max_tokens. If the validator refuses, the same prompt comes back with the reason under it: fix and answer again (a second refusal fails the step). Write following the recipe (section 13).

When the run is `done`, show him the cards (title, logline, the cast sketch) and let him choose; then `story_choose_concept(story_id, concept_id)`. He can also ask for a change: `story_step_start(..., "regenerate", params={"target": "concepts", "note": "..."})`.

## 3. The bible

`story_step_start(story_id, "bible")`, answer the prompt, give him the logline, the tone and the world in a few lines, then `story_approve(story_id, "bible")` once he agrees. A bible field he wants changed: `story_patch(story_id, {"<field>": "..."})`, then approve.

## 4. The look

`story_step_start(story_id, "style")`. It writes nothing and costs nothing: it builds the look from the preset (Pixar-style fruit drama). Show him the result with `story_doc(story_id, "style_lock.json")` in two sentences, then `story_approve(story_id, "style")`. Without this approval the cast and the places refuse. (The three sample pictures of `style_preview` cost a few cents: skip them unless he asks.)

## 5. The cast

Before the cast: `story_estimate(story_id, "cast")` and say the figure in dollars (portraits and character sheets).

`story_step_start(story_id, "cast")`. By default it takes the characters sketched by the chosen concept. The recipe wants a fixed cast of 5 to 8: add the missing ones with `params={"custom": [{"name": "...", "role": "...", "one_line": "..."}]}` (names follow the recipe, section 13). For each character you answer the writing prompts; the character prompt also asks you for two extra fields, **`voice_pick`** (one of the Gemini voices listed in the prompt, a different one for each character) and **`voice_sample_text`** (25 to 40 words, about 12 seconds, said in character, no name, no stage direction). Together they make the character's one frozen reference voice: it is made once now by a Gemini voice (one Gemini call per character) and every line of every episode is cloned from it on his GPU. Pick voices that fit: a grave voice for the matriarch, a light one for the innocent.

Without a Gemini key the pick and the text are kept and the reference is made later; say so if the run says it. Then `story_approve_all(story_id, "cast")`. Show him each character (name, species, role) in one line; `view_file` on a portrait if he wants to see it. A character he does not like: `entity_patch(...)` for a text change, or `story_step_start(..., "regenerate", params={"target": "character:<id>:image:portrait", "note": "..."})` (that one costs a picture; ask first).

## 6. The places

`story_step_start(story_id, "places_proposal")`, answer, then `story_step_start(story_id, "places")` (the default is the proposal: places and props). It draws the sets and the props. There is no estimate for this step: tell him that a handful of pictures (reference pictures of each place and prop) will be bought on his GPU, ask for his go, and give the real figure afterwards from `cost_ledger()`. Then `story_approve_all(story_id, "places")`.

## 7. The season

`story_step_start(story_id, "season", params={"episodes": 6})` (3 to 12; ask him, 6 is the default). Answer, show the arc episode by episode in a short list, then `story_approve(story_id, "season")`. If the run asks for `knowledge`: `story_step_start(story_id, "knowledge")`, answer, `story_approve(story_id, "knowledge")`.

## 8. The money, before any paid step

Always, before the episode: `story_estimate(story_id, "episode", episode=1)`. It answers `{ready, est_usd, message}`. Say the figure to him in dollars, in plain words ("environ X $ pour les images, les clips et les voix; l'écriture est gratuite"), against the cap he chose. If `ready` is false, read the message: it names what comes first. `story_estimate(story_id, "story")` prices the whole run from the idea to episode 1 for an agent-mode story, if he wants to know.

Never submit a GPU batch without his go on that figure. Check `runpod_health()` first (workers ready or throttled); say it if the workers are throttled, since a batch is then slow.

## 9. The episode

`story_make_episode(story_id, 1)` (or `stop_at_keyframes=True` if he wanted to look first). This is the whole episode in one run: script, storyboard, keyframes, clips, voices, edit, publication texts. You still answer every writing prompt, one by one with `story_step_answer`, until the run is `done` (poll with `story_step_status(run_id, wait_s=25)` while it is `running`). Write each document following the recipe (section 13). The run approves the script, the storyboard and the assets itself when they pass their checks, and it checks the money before the clips: if a cap stops it, it says so and nothing is bought; tell him, do not raise the cap.

If a run stops (a failure, a cap, a closed chat), nothing already made is lost: call `story_make_episode` again and it picks up where it stopped. One run per story at a time.

When he wants to look at the pictures first (`stop_at_keyframes=True`): the run ends with a sentence saying it stopped at the keyframes. Then:
1. `episode_sheet(story_id, 1)`: one picture of every shot. Look at it and describe it honestly in French (the faces, the fruit heads, the outfits, what is wrong). A shot to redo: `story_step_start(story_id, "regenerate", params={"target": "shot:1:<shot_id>", "note": "..."})` (one picture, ask first).
2. His go: `story_approve(story_id, "keyframes:1")`.
3. `story_make_episode(story_id, 1)` again: it buys the clips and goes on.

Without `stop_at_keyframes`, the run approves the keyframes itself; a keyframe the check still flags is kept with a warning and named at the end. Look at the sheet then too.

## 10. Look, then hand over

1. `episode_sheet(story_id, 1)`: now each tile shows a frame of the clip. Say what you see; point out the weak shots by their id. For one clip to redo: `story_step_start(story_id, "regenerate", params={"target": "shot:1:<shot_id>:video", "note": "..."})` (ask first, it costs a clip), then `story_step_start(story_id, "rerender", ep=1)`.
2. The text pack: `episode_doc(story_id, 1, "metadata_pack.json")`: the title, the description, the tags, the pinned comment (with the "Team X ou Team Y ?" question). Give him the texts to paste.
3. The video: `episode_export(story_id, 1)` makes a share copy under the download limit (25 MiB by default, 50 at most) when the final video is bigger, then call `comfy_download(<the path of download_hint>)` and hand the file over. If the video is still too big at the lowest quality, say it and give the path so he can copy it with scp.
4. The real bill: `cost_ledger()` for the GPU, and tell him how it compares with the figure you gave.

## 11. What to say at each step

- Before a free step: one line on what comes next ("Je lance les concepts : trois idées à choisir").
- After a writing step: the result in two or three lines, never the whole JSON.
- Before a paid step: what it buys, the figure, the cap, and wait for a yes.
- After a paid step: what was made, what it really cost, what is weak.
- Never say "je vais probablement" about money: use the figure of the tool. If a tool refuses, give its sentence in plain French and the one thing to do.

## 12. Money rules

- The costs come from `story_estimate` before and from `cost_ledger` after, never from memory. A price you remember is a guess: do not quote it as a figure.
- No GPU batch without a go. `comfy_submit` by hand is for a test he asks for, never inside an episode: the episode run does its own.
- The cap is the profile's (2 $ on `own_gpu`): do not raise it, do not change the profile to get around a refusal. If a refusal names "today", it is the day's ledger: tell him, it resets tomorrow (UTC).
- Do not redraw pictures or clips to "improve" them on your own. Tell him what you see; he decides what is redone and what it costs.

## 13. The recipe: how you write in this genre

The recipe is already in the prompts of every writing step of this story (the server adds it for a story made with the preset); you write to it. In short:

- **Names**: a French telenovela pun on the character's own species, in "-ito / -ita" style: Fraisita (strawberry), Bananito, Citronello (lemon), Avocadina, Kiwito, Mangualdine. One species per character. No brand name (Chiquita, Haribo, Oasis...), no plain human first name (Marie, Lucas...). A name the human's idea gives is kept. The server refuses a cast that breaks this and gives the reason: rename and answer again.
- **The cast**: fixed, 5 to 8 characters who come back every episode, each with a role: the matriarch, the villain, the schemer, the innocent, the heir, the best friend, the newcomer with a secret.
- **The beats of an episode** (60 to 90 s, 4 to 6 scenes, ONE conflict, no subplot): a recap of at most 6 words on screen (from episode 2 only), the confrontation, the peak (the moment a viewer sends to a friend), the cliffhanger (its reveal at most 40 words).
- **The closing question**: the teaser of the next episode ends on "Team X ou Team Y ?" (X and Y: the two characters set against each other this episode, by name). The server pins it in the comments too.
- **The end card**: "Partie N demain", 1,5 s at the end.
- **The voices**: over-acted telenovela, crisp and quick. Short punchy lines.
- **Shots**: 5 to 10 seconds each, 1 or 2 per scene, carrying 1 to 4 lines (never one line per clip); one silent reaction shot in the episode.
- **The guardrails, always**: no sexist trope, no racist trope, no sexualisation, nobody judged by body, looks or love life. The schemer wins with her wits, never her body. If an idea of the human's needs one of these to work, say so and propose another turn; do not write it.
- **The look**: Pixar-style 3D cartoon. Every head is one whole fruit or vegetable at human head scale, never a human head, never a mask.
- **The rhythm**: one episode a day is the series' pace; the human posts them himself.

## 14. What is not automated

- **Publishing.** Nothing posts to YouTube, TikTok or Instagram. You hand over the video and the texts; he posts. Never offer to post.
- **Talking mouths (S2V).** The clips are made from the keyframe and a motion sentence; the voices are laid on top of the video. A template exists that would make the mouth follow the voice (`s2v_wan22`, the keyframe and a voice line in, a clip of up to 5 s out), but it is not in the episode yet: it has never run live, and nobody knows yet how it looks on a cartoon fruit or in French. If he wants to try it, it is ONE paid test line he runs on purpose: `comfy_submit(template="s2v_wan22", prompt="<a motion sentence>", image_path="<a fruit keyframe>", audio_path="<one voice line .wav>", seconds=5)`, after he has said yes to the cost. Do not use it inside an episode, and do not promise lip-sync.
- **Real people's voices.** The characters' voices are synthetic, made from Gemini's voices. A real person's voice is a different path (his own recording with the consent box, in the app); do not suggest cloning anyone.
- **The dashboard.** The web app shows the same story (same folder), but never run the same story from the app and from the chat at the same time.

## Answer format

A short problem statement and its cause when something fails; then the solution; at the end of a paid step a small table (what, how much, where the file is). In French for the human; code and identifiers in English.
