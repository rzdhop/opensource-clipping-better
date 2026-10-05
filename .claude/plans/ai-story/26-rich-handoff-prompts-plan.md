# Plan 26 — Rich prompts on every link (a master prompt per story, templates, ≥500 words) and working Copy buttons

Written 2026-10-05 from the human's report: "the copy buttons do not work", "the prompts are supposed to have
very detailed descriptions of personas, universe, context", "use templates; the handoff prompt ≥ 500 words",
"the principle of a template for the master prompt that can be reused at each generation"; then (v2): "the longer the
context, the better: it lessens regeneration and consistency failures; everything planned before the generation is
given to the provider, manual, API or local". Evidence: a Gemini
render of e7412a3efcc6 sh11 that came out as a photoreal live-action office (Rida without his dragon-fruit skin,
no fruit_drama rendering, no palette), from a 188-word prompt that carried one style line.


**APPROVED 2026-10-05 (v3).** The v2 design (hashing the fitted text, grandfathering) was rejected by the Opus
design review: it would stale assets on every record edit, live-limit re-read and storyboard re-run. The approved
design H1 below adds the template at the send layer and in the brief; no hash changes. The human's answers: keep
made assets; drop the quality ceilings (DEC-247, not DEC-240) for the full prompt — the ceiling stays on the core.

## EXPLORE record (two Sonnet agents, 2026-10-05)

### E1 — Copy buttons
- Four copy-pasted implementations, no shared helper: `HandoffCard.jsx:65-98` (`useCopy`), `PromptDrawer.jsx:73-105`
  (`CopyText`), `PreviewPane.jsx:56-90` (`CopyButton`), `ActivityFeed.jsx:388-395`.
- All gate on `navigator.clipboard && window.isSecureContext`. The app is reached over plain http (tailnet) → the
  clipboard API is absent → the fallback runs: it un-hides a `readOnly` textarea, `.select()`s it, sets a hint.
  Nothing ever calls `document.execCommand('copy')`, which works on http from a click.
- On the Handoff the textarea sits at the bottom of the card body (below prompt, references, checks, upload), no
  scroll, no toast → the button looks dead. On iOS `.select()` on a hidden/readOnly textarea is unreliable and a phone
  has no Ctrl+C.
- Tests are text contracts over the JSX (CI has no node): `tests/test_dashboard_handoff.py:76-95`,
  `tests/test_dashboard_prompt_drawer.py:28-44` pin the strings `navigator.clipboard.writeText(text)`,
  `window.isSecureContext`, `<textarea`, `area.select()`.

### E2 — The prompts
- Package `clipping/aistory/`. The pasted clip prompt = `prompting.speech_clip_prompt` (studio) or
  `speech_clip_prompt_action` (action) via `clips.speech_request_parts` (`clips.py:390-410`), capped at
  `SPEECH_CLIP_MAX_WORDS = 200`, then `brief.platform_prompt` (`brief.py:241-251`: whitespace, the preset closing,
  the Higgsfield reference sentence). `brief.shot_entry` (`brief.py:379-422`) builds the entry; `brief.handoff`
  (`brief.py:875-1040`) copies `entry["prompt"]` into `shots[i].clip.prompt`.
- The hash `clips.clip_prompt_hash` (`clips.py:210-220`) is computed inside `speech_request_parts` on the raw
  builder output, before `platform_prompt` → the brief layer can grow the text freely without staling any clip
  (DEC-292).
- The studio template carries: camera phrase, "{Speaker}, {look≤12w}, {action≤24w}, looks at {listener} and says…",
  the listener line, the place descriptor ≤18 words, `IDENTITY_KEEPS`, `tier2_prompt_suffix_v2`, the audio closings.
  NOT carried: `style_lock.rendering / camera / lighting / character_design_rules / environment_rules / palette /
  quality_tail`, the universe, `story.tone / genre_tags / logline / world.*`, the characters' full `look`
  (build, face, hair, skin_material, wardrobe, bearing), `signature_items`, `personality`, `voice.direction`, the
  place `layout_notes / look.lighting / props_here`, the props, the scene `summary / emotion / function`, the line's
  `delivery / emotion`, the staging. The style `negative_prompt` sits in a separate field that Flow/Veo ignore.
- Keyframe handoff prompt = the stored `shot.image_prompt` (≈300 words, hashed by `assets.prompt_hash` with the
  negative, size and reference shas) — not to be changed in storyboard.json; it can be wrapped in the brief layer.
  Entity prompts (sheets/plates/props) are rebuilt per request by `refimages.*_prompt` → `prompting.*_v2`
  (130–220 words), pinned by `tests/fixtures/aistory_variants/image_brief_before_d5follow.json` (image brief JSON +
  markdown sha) — a brief-layer pin.
- Pins that must stay byte-identical (hashes): `tests/fixtures/aistory_action_prompts/studio_prompts.json`,
  `tests/fixtures/aistory_variants/before_d5.json`. Brief-layer pins to adjust: `tests/test_story_manual_link.py:154-206`
  (endswith the closing, contains the quoted line, "nobody speaks or sings", the Higgsfield "References: image 1 is"),
  `tests/test_api_handoff.py:63-139` (handoff prompt == brief prompt byte for byte).
- `templates/platforms/flow.json` already carries unused `prompt_order`, `dialogue_syntax`, `ambient_syntax`, `sfx_syntax`.
- Dragon Fruit's cast is mixed by design: Rida (dragon-fruit hair and skin), Victor (eggplant head), Marie-Jeanne,
  Chloe, Sam (humans). sh11's prompt named Rida without any look → Gemini drew a man in a suit.

## Design

**H1 — The template lives at the send layer; no hash changes.** Every `*_request_parts`, every hash, `clip_state`,
`shot_state`, `image_state` and the storyboard's stored prompts stay byte-identical. The master + scene text is
prepended where the request is sent and where the brief is built, the same treatment DEC-249 gives refits and DEC-292
gives the brief's formatting. Made assets stay made by construction; a record edit changes only the next request.
The ceilings of DEC-247 stay on the hashed core (it is the hash basis); the **full** prompt is bounded only by the
link's limit (DEC-303 amends DEC-247's reading: the ceiling bounds the core, not the prompt). v2 stories only
(`media_policy.is_v2`); v1 output byte-identical (RC-Q1).

**New module `clipping/aistory/prompt_templates.py`** (stdlib only, no I/O):

```python
MIN_PROMPT_WORDS = 500
Section = namedtuple("Section", "key label text rank")      # rank = position in DROP_ORDER
def master_sections(story, style_lock, entities, *, language, present=(), place_ids=(), prop_ids=(),
                    speaker=None, image=False) -> list[Section]
def master_prompt(story, style_lock, entities, *, language) -> dict   # {text, words, sections:[{key,label,words}]}
def shot_clip_prompt(ec, shot, script, core, *, limit_words, fits=None) -> dict
def shot_keyframe_prompt(ec, shot, script, core, *, limit_words, fits=None) -> dict
def entity_prompt(story, style_lock, kind, doc, core, *, limit_words, fits=None, variant=None) -> dict
def fit(sections, core, *, limit_words, fits=None) -> dict   # {text, words, full_words, limit, dropped:[label]}
def short_warning(full_words) -> str | None
```

Sections of the master, each a labelled paragraph:
1. SERIES — title, logline, tone, genre tags, the world (setting, time period, rules, motifs), the universe when set,
   the dialogue language. (Image prompts leave out the title and any text that could be drawn as lettering.)
2. ART STYLE — the style lock verbatim: rendering, character design rules, environment rules, camera, lighting,
   palette (line + primary/accent hexes + forbidden), quality tail, motion rules, the audio direction.
3. CHARACTERS — one paragraph per character of the episode, **by handle** (`shots.character_handles`, `shots.py:226`;
   named casts by name as DEC-302 does): species/head, build, silhouette, face, hair, skin, height, the wardrobe set
   in use, signature items, bearing, personality, relationships to the others present; voice direction only for the
   speaker. Humans say "a human". The ones in this shot are marked "(in this shot)". Free text (logline,
   relationships, personality) swept with `names.without_names(..., shots.name_map(entities, v2=True))`.
4. PLACES — descriptor, layout, scale, lighting of the scene's time variant, props here.
5. PROPS — descriptor, material, colour, scale, owner.
6. AVOID — the style negative + the shot negatives as one sentence; dropped first on links that have a negative
   field (`video.py:296`; Veo lite and Seedream have none).
Hygiene: no double quotes anywhere in the master (Veo speaks quoted text; `prompting.speech_prompt_sentences`'
regex at :1132 and the voice parse depend on the core's quotes), no `Audio:`, no `says in`, no dialogue lines.

Shot templates: clip = master + a SCENE section (scene number, summary, emotion, function, time variant, framing,
staging per subject, camera motion, the line's delivery and emotion for the speaker) + **today's core unchanged**
(the action, the quoted line, the Audio sentence, the closing — last, so the manual-link pins keep). Keyframe =
master (image=True) + the stored layered core. Sheet / plate / prop / variant = SERIES + ART STYLE + that entity's
own full paragraph + the v2 core. A user-written `prompt_override` is sent as written, no master.

**Fit (D4).** Over the link's limit, sections drop in this order until it fits: AVOID (when the link has a negative
field) → characters not in the shot → places not in the shot → props not in the shot → SERIES lore (themes, motifs,
rules) → palette hexes (the line stays) → personality → relationships → the SCENE summary. Never dropped: rendering +
design rules, the present characters' looks, the place, SCENE staging, the core. The last rung is the core alone, so
the template never adds a refusal that does not exist today; `prompt_limits.check` at dispatch stays the backstop
(DEC-240). `fits` = `lambda t: prompt_limits.fits(link, t)[0]`, so characters and tokens are checked, not words only.
The fit is reported as `fit: {limit, words, full_words, dropped[]}` on the handoff row and as a job-log line next to
`_refit_line` (`assets.py:~4797`): "Veo accepts 630 words: 812 → 618, dropped Chloe, Sam, the props".

**Limits.** New `prompt_budgets.link_words(link, *, live=None) -> int | None` = `prompt_limits.budget_words` with no
ceiling; `None` (unbounded) for no link, `manual/*`, `local/*`; `chain_words(labels)` = the smallest over a role
chain (a long prompt fitted to the first link would be refused by a smaller fallback and lose the chain's fallbacks).
The FLUX T5 window is already in `budget_words` via `Limit.window_tokens` (`prompt_limits.py:107-115`). Local Wan
templates get a `window_tokens` of 512 (umT5) so the shot block is never cut on a local box. The existing
`keyframe_words` / `clip_words` / `speech_clip_words` and the ceiling constants stay (hash basis). The entity
ceilings (`sheet_words`, `plate_words`, `prop_words`, `two_view_words`) are dropped: sheets carry no hash
(`refimages.py:619-620`; state = file present, `brief.py:657,670,679`).

**Where it is composed (send + brief, nowhere else).**
- Clips to API/local: `assets.clip_request` (`assets.py:1854-1878`) via a thin `clips.sent_clip_prompt(ec, shot,
  script, parts, *, link)`; covers make_clip (:3688) and regenerate (:4938). Fit to `video["link"]`.
- Keyframes: `_Assets.make_image` (`assets.py:2860-2905`) via `assets.sent_image_prompt(ec, shot, parts, *, link)`
  once the chain is known; fit to the chain's smallest limit when no link is recorded.
- Sheets/plates/props/variants: inside `refimages.character_image` (:654) / `variant_image` (:788) / `place_image`
  (:896) / `prop_image` (:987) before `_Plan`, via `refimages._sent(story, lock, kind, doc, core, *, links)`.
- Brief/handoff: `brief.shot_entry` (:384-400) → `platform_prompt(preset, model, sent_clip_prompt(..., link=MANUAL_LINK)
  ["text"], refs)` (formatting after; DEC-292 kept); `_keyframe_entries` (:736) uses `assets.shot_image_link(...)` so
  a hand-made keyframe shows the unbounded text; `_entity_entries` / `_variant_entries` (:656-720) use `_sent`;
  `handoff` (:875-1040) adds `master_prompt` {text, words, sections}, `fit` per row and `prompt_warning` (the
  <500-word check, on the unfitted count, not on stock or keep-still rows; `checks` stays the list of things to verify
  in the finished clip); `render_markdown` puts the master prompt first.
- Untouched: the storyboard step's stored prompts (`shots._layered`), `refresh_prompts`, the CLI (reaches prompts
  through `shot_brief`, `cli.py:1914`), `video_plan.build_video_prompt`, the stock-cutaway hash path, the two-view branch.

**Copy.** One `web/dashboard/src/lib/clipboard.js` `copyText(text) -> Promise<boolean>`: `navigator.clipboard.writeText`
when `isSecureContext`, else an off-screen textarea + `setSelectionRange(0, len)` + `document.execCommand('copy')`.
Call sites: true → toast "Copied — …"; false → the text revealed selected, `scrollIntoView`, a toast telling the user
to long-press and copy. The four sites use it; none calls `navigator.clipboard` directly.

**UI.** A "Master prompt" card at the top of the Handoff (word count, Copy, hint: "every shot prompt already carries
it; paste it alone in a chat that keeps context"); the word count and the fit line on every Copy prompt; the
`prompt_warning` row; the PromptDrawer on the Cast/Places/Props tiles shows the same; the Export markdown leads with
the master prompt.

## Stages (one commit each, worktree off main, DEC-234 selections in both envs, push after each)

1. **Copy that works on http** — `lib/clipboard.js` (new), `HandoffCard.jsx`, `PromptDrawer.jsx`, `PreviewPane.jsx`,
   `ActivityFeed.jsx`. Tests: `tests/test_dashboard_handoff.py`, `tests/test_dashboard_prompt_drawer.py` (the pinned
   strings move to the helper; new pins: `document.execCommand('copy')`, `setSelectionRange`, the four sites import
   `copyText`, no direct `navigator.clipboard` outside the helper). Risk low. Verify: those two tests both envs; `npx
   vite build --outDir <scratchpad>/dist-x --emptyOutDir`. Rollback: revert. RC-M9 no-auth guards untouched.
2. **`prompt_templates.py` + `prompt_budgets.link_words/chain_words`** — pure addition. Tests (fail-first):
   `tests/test_story_prompt_templates.py`: ≥500 words on a Dragon-Fruit-shaped fixture (five characters with full
   looks) and `short_warning` on a thin one; the fit ladder (drop order, within the limit, a limit smaller than the
   core returns the core alone, never-drop sections survive a 230-word fit); hygiene (handles not names, no `"`, no
   `Audio:`, no `says in`); a `link_words` case in `test_story_prompt_budgets.py`. Risk low.
3. **Clips at the send layer (riskiest)** — `clips.sent_clip_prompt`, `assets.clip_request` + the fit log line,
   `brief.shot_entry` + `handoff` (`master_prompt`, `fit`, `prompt_warning`) + `render_markdown`. Tests: new
   send-layer guard (a v2 fixture with a stored clip hash stays "current"; `GenRequest.prompt` starts with the
   master and ends with the core); `test_story_manual_link.py` (its pins hold; add: starts with "SERIES", ≥500 words,
   `fit` present), `test_api_handoff.py` (holds: the handoff copies the brief byte for byte), `test_story_native_speech_clips.py`,
   `test_story_action_prompts.py` (the goldens untouched — the core is unchanged), `test_story_variant_shots.py`.
   Risk: the gencache key includes the prompt (`gencache.py:142-165`): a pending paid clip would not resume across the
   deploy → **deploy only with no pending clips** (already the rule: 0 running jobs). Rollback: revert; nothing stored
   changes. RC: DEC-292, DEC-294, DEC-295, the `studio_prompts.json` and `before_d5.json` goldens byte-identical.
4. **Keyframes (4a) and entities (4b)** — 4a: `assets.sent_image_prompt`, `make_image`, `brief._keyframe_entries`;
   4b: `refimages._sent` + the four `*_image` functions, `brief._entity_entries` / `_variant_entries`, the entity
   ceilings dropped. Tests: request-level pins become "starts with the master / ends with the core" in
   `test_story_keyframe_fix.py:132,341`, `test_story_assets_step.py:925`, `test_story_look.py:422-425,465`,
   `test_story_v2_redraw.py:109,203`, `test_story_two_view_sheets.py:190,224,238`, `test_story_variant_shots.py:554`;
   `image_brief_before_d5follow.json` re-recorded once (reason in the action log; `before_d5.json` untouched);
   `test_story_prompt_budgets.py:74-77`, `test_story_two_view_sheets.py:157-160` for the dropped entity ceilings;
   the send-layer guard extended to a keyframe and a sheet. Server check after the deploy: `GET …/e7412a3efcc6/
   episodes/1/handoff` still shows 10 keyframes made. Risk moderate. Rollback: revert + the fixture.
5. **The Handoff screen and the tiles** — `HandoffCard.jsx`, `HandoffPage.jsx`, `PromptDrawer.jsx`, `api.js`, css;
   the two dashboard text contracts. Risk low. Verify: tests + the vite build + live at 375 px after the deploy.
6. **Docs + close-out** — `docs/AI_STORY.md` ("Your own clips": the master prompt, the template, the fit line, the
   500-word check; "Prompts": the ceiling bounds the core, the link bounds the prompt), DEC-303 (amends DEC-247,
   extends DEC-302), A-169… (http tailnet; H1; the Wan window; action style gets the master too; `prompt_override`
   sent as written), CHECKPOINT, action log. Deploy at 0 running jobs: `rm -sfv` + `up --build` (dashboard) — one
   deploy after stage 5 covers stages 1–5.

## Verification (end to end)

- Per stage: the named selection in both envs (`PYTHONPATH=$HOME/.cache/rzc-xdist python3 -m pytest -q -o addopts=""
  -p no:cacheprovider -p no:warnings -n 4 <files>` and the `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs:…` twin), gated
  on the exit code; CI (full suite) on every push.
- Before Tier 2 on the server: confirm whether Rida is a `shots.named_character` on e7412a3efcc6 (the review's
  unverified root-cause for "Rida" with no look).
- Tier 2 (the human): (a) on the phone over http, tap Copy prompt on sh11 → "Copied"; paste into Gemini/Flow → Rida
  with dragon-fruit hair and skin, the fruit_drama rendering, the palette; (b) the Master prompt card copies; (c) one
  auto shot on the API path shows the fit line in the job log and the clip hash of the made shots unchanged (10
  keyframes still "made" on the Handoff).

## Rejected
- Enriching the hashed builders / grandfathering hashes: stales assets on every record edit, every live-limit
  re-read and every storyboard re-run; sheets have no hash to grandfather.
- Rewriting stored `image_prompt` at storyboard time: stales keyframes, churns `test_story_shots.py`.
- Storing the master prompt once per story: drifts from edits; rebuilding per request costs nothing.
- Handoff-only enrichment (plan v1): the API and local paths would keep the thin prompt.

## DECISIONS check
DEC-247 (ceilings) — amended: the ceiling bounds the hashed core, the link's limit bounds the full prompt. DEC-240
(dispatch check) — kept as the backstop. DEC-249 (refit outside the hash) and DEC-292 (brief formatting outside the
hash) — the pattern H1 extends. DEC-302 (the handoff document) — extended. DEC-294/295 — untouched. No conflicts.

## Stage 7 — "In a fruit world every head is a fruit" (added 2026-10-05 on the human's rule; EXPLORE by a Sonnet agent)

**Root cause.** The species is never a field. K1 (`prompts.py:970/1005`) and D2 (`:1369/1412`, `schemas.d2_schema` :5383)
get the style's `character_design_rules` but no species block (the universe reaches only the concept writer,
`steps/concepts.py:257-286`; `media_policy.universe(explicit=True)` is None for Dragon Fruit; `_k1_character`
`steps/cast.py:292` drops the sketch's species). So three casts were written as humans ("Fair human skin"); the sheet
model, told "a whole fruit head", picked pear / pear / avocado on its own; the J2 judge (`steps/judge._character_look`
:~556, `_IDENTITY_FIELDS` :482) compares the image against "Fair human skin" and flags the pear as a continuity error.
`shots.named_character` (:216) / `named_look` (:688) say "a woman in her thirties in a charcoal blazer" — no head;
`render_look` (:450) has no species slot; the storyboard `_layered` prompt carries no head word.

**The human's answers:** Marie-Jeanne pear, Chloe pear, Sam avocado (as drawn, no sheet redrawn); Marie-Jeanne's eight
keyframes stay made (no storyboard refresh); only the sh11 verdict is re-asked.

- **7a — the field and its readers (Opus; byte-identical when the field is absent).** `look.species` (optional string)
  in `CHARACTER_LOOK_SCHEMA` (schemas.py ~1655) and `CHARACTER_SCHEMA`; `prompt_templates._species` reads it first;
  `shots.named_look` / `character_anchor` / `speech_look` say "with a pear head" when set (the hashed core moves only
  for characters that carry the field — the human's edit already marks their storyboard prompts outdated);
  `render_look` puts "<species> head" first and never drops it; `visual_cues` / the sheet prompts say it once;
  `judge._character_look` adds "Head: <species>" and drops a "human" skin line when a species is set;
  `_J2_SHEET_ISSUE` compares the head. Tests: a fixture character with `species` → the anchor, the render look, the
  sheet prompt, the judge brief and the template all carry the head; without it every output byte-identical (the
  existing goldens prove it).
- **7b — the writers (Sonnet).** In a species world (`media_policy.universe(story)` non-explicit → the style default,
  or the lock's rules naming fruit/vegetable heads) K1 and D2 get a species block (the pool, the species already taken
  by the other casts, "every character is a <species>; no human head"); `d2_schema`/`d2_look` gain `species`, required
  in a species world; `d2_errors` rejects "human" in `skin_material` there; D2 writes the head into `face`;
  `_k1_character` keeps the sketch's species; the pool check advisory ("dragon fruit" is outside the fruits pool).
- **7c — the Cast tile + the repair (Sonnet).** `species` in `LOOK_TEXT_FIELDS` (CastStep.jsx ~626) as a select over
  `universes.universe_of(story)["species"]` + free text (`patch_entity` already merges look keys). The repair of
  e7412a3efcc6 by three PATCHes (face "<species> head, carved face", `skin_material` fruit skin, `species`) on the
  human's go; no sheet redrawn; the storyboard NOT refreshed; sh11's verdict re-asked.
