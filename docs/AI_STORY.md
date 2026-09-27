# AI Story

AI Story is the second mode of rzdhop AI, next to **Clips** (the long-form →
vertical-highlights pipeline the rest of this repository is about). Where
Clips turns one video into several, AI Story turns a concept into a
**persistent story workspace** — a world, a cast, a locked visual style, a
season arc — and produces serialized ~60-second episodes from it, with the
same characters, places and voices holding together across every episode.

The point is not "one prompt → one video". It is consistency and
serialization: a style that is locked once and then injected into every
image and voice prompt afterwards, and a season structure meant to make
someone come back for episode 2. Generation runs on free hosted APIs by
default (or on a local GPU when you point it at one); paid APIs are opt-in
and capped at **$1 per episode** by default (Settings → Budget). Nothing
paid ever runs unless you turn it on.

This document covers what exists today. AI Story is being built in phases;
this is the phase-1 through phase-3 foundation, steps 1–9 of the 13-step
workflow. It is extended as each later phase lands — see "Where it stands"
below for what is not here yet.

## Where it stands

| # | Step | What it produces | Status |
|---|---|---|---|
| 1 | New story | a draft story (language, optional seed text, optional style) | available |
| 2 | Concepts | ten concept cards to choose from, or generate ten more | available |
| 3 | Bible | logline, premise, tone, world, themes, audience | available |
| 4 | Style | a locked style (palette, typography, consistency mode) + a preview strip | available |
| 5 | Cast | characters: reference sheets, voices | available |
| 6 | Places & props | locations and recurring objects | available |
| 7 | Season arc | the season's episode-by-episode arc | available |
| 8 | Episode script | scenes and dialogue for one episode | available |
| 9 | Storyboard | shots, framing, camera moves for the script | available |
| 10 | Assets | the images, voice lines, SFX/BGM for the storyboard | phase 4 |
| 11 | Render | the final `.mp4` with burned subtitles | phase 4 |
| 12 | Metadata pack | title/description/hashtags per platform | phase 4 |
| 13 | Next episode | recap, audience-feedback digest, new characters/twists | phase 5 |

In other words: today you can create a story, pick or invent a concept,
write and edit its bible, build and lock its visual style, cast its
characters with reference sheets and voices, populate its places and props,
and plan its season arc — a story reaches **ready** once the cast, the
places and props, and the season are each approved. From there you can
write and storyboard episode 1: a script (scenes, dialogue, timing), then
shots for it. You cannot yet render anything — that's phase 4 — and episode
2 is refused until phase 5's memory step exists, so only episode 1 can be
produced right now.

## Walkthrough (dashboard)

Open **AI Story** in the mode switch, or go to `/story`. It lists your
stories as cards (title, status, style, language); **New story** starts one.

### 1. New story

- **Language** — `Français` or `English`. Required: nothing is picked for
  you, so a story is never silently written in the wrong language.
- **Seed text** (optional) — a rough idea, a scene, a vibe; up to 2000
  characters. It seeds the concepts, nothing more.
- **Style** (optional) — pick one of the seven shipped templates now, or
  "Decide later" and pick it at step 4. The seven: Fruit Drama, 3D Animated
  Family Film, Anime/Manga, Realistic Cinematic, 2D Cartoon/Flat, Storybook
  Watercolor, Claymation/Stop-motion.
- **Generation profile** (collapsible, defaults are fine to leave alone):
  tier (1 = stills, 2/3 = animated — tiers above 1 have no adapter yet, so
  they do nothing until phase 6), route (`auto` / `local` / `api` — where
  images are made), consistency mode (`references` / `prompt_only`), budget
  profile (`free` / `one_dollar` / `quality`).

**Create story** opens the story page, a vertical stepper: New story (done),
Concepts, Bible, Style. Each step unlocks once the one before it is
approved; a locked step shows why ("Approve the bible first.").

### 2. Concepts

Two sources: the **library** (the ten shipped concepts, filterable by
style) and **Generated** (anything you've asked for). Each card shows a
title, logline and "value" (the substance the story carries) up front;
"Details" expands the world, cast sketch, hook formula and retention
mechanics. **Pick this concept** chooses it — no separate approval step; the
choice *is* the approval, and it also completes any concepts job left
awaiting approval.

**Generate 10 more** queues a job of ten LLM calls, one concept each, each
told not to repeat a title the story already has (library titles first,
then every one it has generated). The estimate chip reads something like
`est. $0.00 · 10 LLM calls`; the route chip next to it names where they'll
run (see "Estimate and route chips", below). Cards land on disk as each
call returns, so a couple of failed calls out of the ten still leave you
cards to pick from — the activity feed shows which calls failed and why.

Picking a different concept than before clears the bible (a bible written
for one concept does not carry over to another).

### 3. Bible

**Write the bible** queues a job of three calls (logline/premise/tone,
world, themes/audience) — the "✍️" line for each is echoed live under the
button while it runs. Once written, each part is an editable card:

- **Logline**, **Premise**
- **Tone & genre tags**
- **World** (setting, rules, time period, recurring motifs)
- **Themes & values** (themes, audience age/platforms, "why people come
  back" — exactly 3 lines)

Every field is editable inline, no job needed. Every card also has
**Regenerate**, which takes an optional note ("make it darker", "shorter")
and re-runs just that part's prompt with the note attached — the rest of
the bible is untouched. Editing or regenerating any bible field clears the
bible's approval (a changed bible is an unapproved one); the style stays
approved if it already was, since the style lock doesn't read bible text.

**Approve bible** is blocked until every field is filled in, including all
three "why people come back" lines — the button's error names exactly
what's missing. Approving unlocks the style step.

### 4. Style

Pick a **style template** (the same seven), then tweak:

- **Primary palette** / **Accent palette** — colour swatches, add/remove.
- **Font family**, **Highlight colour**, **Subtitle mode** (`word_pop` /
  `two_line` / `none`).
- **AI label** — the on-screen "AI-generated" disclosure, on by default.
- **Consistency mode** — `references` (image generation is given reference
  images so characters/places stay visually consistent) or `prompt_only`, an
  explicitly labelled degraded mode where only the text prompt holds them
  together, and they may drift. It's a property of the style lock, set here,
  though it only starts to matter from Cast on (no references exist before
  a character has a portrait).

**Save draft** writes the draft without generating anything — it runs
inline, not as a job. The **preview strip** is the part that spends:
**Generate preview** queues a job that makes three small (576×1024) sample
images from the draft — a place, a character portrait, a two-shot — through
the image chain. The estimate/route chips show cost and route before you
press it; each thumbnail shows its own cost (`free` or `$0.030`, etc). A
sample can fail on its own (a keyless provider's rate limit, say) without
failing the whole strip — it's listed with its reason under the grid, and
you can approve with fewer than three images, or none, if the route is
honestly unavailable.

**Approve & lock style** freezes the style (`locked_at` is stamped) — after
that it cannot change; the palette, fonts, motion rules and everything else
become the fixed reference every later image/voice prompt in the story is
built from.

### 5. Cast

Unlocked once the style is approved. A cast has at most **8** characters in
all (Edge, the free default voice provider, speaks 8 French voices — the
limiting resource).

**No cast yet** offers two sources: the chosen concept's **cast sketch**
(name, role, one-line; pre-checked) and **your own** — a name, a role
(`lead` / `support` / `recurring` / `guest`) and a one-line, added with
"+ Add". **Create cast** queues the step for whatever is checked or added;
the estimate chip in front of it counts only what does not exist yet (e.g.
`est. $0.00 · 3 LLM calls · 3 images · 6 edits · ~360 voice chars`) — a
character already in the story is not recounted.

The step then fills in, for **every** character of the story (leads,
supports, recurring, guests), in this order: the text (K1 writes a
**descriptor**, 2–3 **signature items**, a **personality** — traits, wants,
fears, speech style —, **relationships** with the rest of the cast, and a
voice brief), then the **portrait**, then the **turnaround** and
**expressions sheet**. Once every character's text is there, one **voice**
per character left with none, then a ~3-second **voice sample** of each.
Every field the model writes for image or voice prompts is in **English**;
what you type yourself (name, one-line) stays in the story's language.
Everything a run makes is saved as it is made, so a run that fails partway
keeps whatever it already finished — see "Troubleshooting" below.

**Consistency.** The portrait is drawn from text alone. The turnaround and
the expressions sheet are, by default, **edited from the portrait** (plus
any of your own uploads) through a reference-capable editor — local
ComfyUI, or a paid editor once `allow_paid` is on — and carry the chip
**consistency: references**. When no editor can run, the step **stops
before any call and asks**: a banner at the top of Cast names why and offers
**"Switch this story to prompt-only consistency"** (a confirm dialog warns
that shots may then drift slightly); nothing switches automatically, only
you do, from here. Every image made this way afterwards is labelled
**consistency: prompt-only** on the image itself. **"Continue cast"** fills
in whatever is still missing (a stalled sheet, a voice nobody picked, …);
with nothing missing it calls nothing.

Each character's card shows its **portrait / turnaround / expressions**
slots (each with its own **Regenerate** — a fresh seed and an optional note,
its own estimate chip; an empty slot says why: "Write the character first.",
"Make the portrait first.", "Needs an editor, or prompt-only consistency.",
or "Not made yet."), its editable **descriptor**, **signature items**,
**personality** (traits / wants / fears / speech style), **voice direction**
and **sample line** (each saved inline, no job needed), and a
whole-character **Regenerate** (K1 again, with a note — the images and the
pinned voice are untouched).

**Voice** shows the pinned voice (`provider/voice_id`) with a player for its
sample, or "Pick a voice: no catalogue voice was left for this character."
when none could be found. **"Other voices"** lists up to 6 alternates
(gender, age, style tags), best first, never one already pinned by another
lead or support: **no two leads or supports share a voice, and a pinned
voice never falls back to another** if it later becomes unreachable; picking
an alternate repins the voice and makes it a fresh sample.

**Design references**: up to **4** images per character, PNG/JPEG/WebP or
GIF, 10 MiB and 40 megapixels each — re-encoded on upload to a clean PNG
with no metadata at all (no EXIF, no original file name). Each is described
once, through the vision chain, the next time the character's text is
written or regenerated; until then it shows "not described yet — it will be
described before the text is written". The description folds into what K1
writes — a conflicting reference (say, a real photo) is bent toward the
story's own style, never copied. **Design references for stylised
characters. Imitating real people is not supported.**

**Approve** one character at a time; each needs its text, portrait,
turnaround, expressions sheet, a pinned voice and its sample. **The cast is
approved when every lead and support is** — a recurring or guest character
never blocks the cast, and never approves it on its own.

### 6. Places & props

Unlocked once the style is approved and the cast has at least one character.
**Propose places & props** queues one small call (P0) that reads the bible,
the world and the cast — each character's name and signature items, where
props usually come from — and proposes 2–3 places and a few props; nothing
is created yet, only listed. Making the places and props themselves needs
at least one character with its text written.

The proposal is **editable** before anything is made: each place is a name
and a one-line description (up to 6 places and 6 props, "+ Add place" /
"+ Add prop", ✕ to drop one); each prop also picks an **owner** (one of the
story's characters, or none). The estimate chip above **"Create places &
props"** follows the list on screen, not the saved proposal — add or drop an
entry and it recounts. Creating writes only what is not already in the
story by name.

The step then fills in, for every place and every prop: its text (P1 for a
place — descriptor, layout notes, and the time variants it proposes; R1 for
a prop — descriptor and an owner, kept as you set it unless you left it
blank), then its first image: a place's **master plate** (`day`, always
made — the fixed reference every other time of day is drawn from) and a
prop's single **image**. **"Continue places & props"** fills in whatever is
still missing.

Each place's card shows the **day** plate and any time variants you've
added (**"Make night"**, or `dusk`, `rain`, `dawn`, from a dropdown once the
day plate exists), its editable descriptor and layout notes, and a
whole-text Regenerate. A time variant is edited from the day plate the same
way a character's sheets are edited from the portrait (`references` mode,
the same "stop and ask" and prompt-only switch when no editor can run); the
day plate itself and a prop's image are always text-to-image, never an
edit. Each prop's card shows its image, an editable **owner** dropdown and
**descriptor**, and a whole-text Regenerate.

**Approve** each place and prop individually; **places are approved when
every place and every prop is**.

### 7. Season arc

Unlocked once the cast is approved. Pick an episode count, **3 to 12**
(default **8**), and press **"Plan the season"**. The step first writes a
skeleton (S1: every episode's function — setup, escalation, complication,
midpoint twist, crisis, climax & reset — and a short summary), saved before
anything else runs, then expands each entry in turn (S2): its summary in
full (at most 60 words), the hooks it resolves and the ones it leaves open,
and which characters appear in it. Each entry is saved as it is written, so
a run that fails partway keeps every entry already expanded; an entry whose
S2 failed keeps its S1 outline (see "Troubleshooting").

The timeline shows one card per episode: its number, its function badge,
its summary, any hooks in/out, the characters in it, and a **Regenerate**
(S2 again for that entry alone, with an optional note). A **"Series
memory"** panel is present but stays empty until phase 5, when episodes
start being approved. **Re-plan** replaces the whole arc from scratch (a
confirm dialog warns this is a full replacement, at the same episode
count).

**Approve season** needs the places and props already approved and every
planned episode to carry a summary; approving makes the story **ready** —
the state phase 3's episode script picks up from.

### 8. Episode script

Unlocked once the story is **ready** — the story page's **Ready** card links
straight to **Open episode 1 →** (and lists any other episode already
started). An episode opens at its own three-pane page, `/story/<id>/episodes/<ep>`
(Script, Storyboard, Preview — see "The episode page" below).

**Episode length**, before any episode has a script: `60 s (55–80)`
(`serial_60s_v1`, target 60, tightens above 75) or `90 s (75–100)`
(`serial_90s_v1`, target 85, tightens above 95). Once *any* episode of the
story has a script, the length is fixed for the whole story — the select
disables itself and says so.

**Write / Continue / Check again** is one button whose label follows the
episode's own state: `Write episode 1` with nothing yet, `Continue writing`
mid-run, `Check again` once everything is written (re-running only the
consistency check). Its estimate chip reads `est. $0.00 · N LLM calls` — the
exact number of calls the beat sheet's own request will make, not a worst
case — and its tooltip names the 8–12 legal range a first 60-second episode
could still land in, plus any paid link the run would skip rather than call.
A route chip beside it names where the calls run. Writing goes one small
call at a time — a beat sheet (E1), one call per body scene (E2), the
hook/cliffhanger/teaser (E3), then a consistency check (E4) — saving to disk
as each lands, so a run that stops partway (the free tier's latency, a
30-minute step budget) resumes with "Continue writing" rather than starting
over; a finished run ends **awaiting approval**.

**The duration bar** sits above the scene list: the template's window with
its target and tighten marks, one segment per scene (coloured by that
scene's own timing state), a running total labelled `estimated`, `measured`
or `partly measured`, and, below it, any flags — a scene over its slot, a
line to trim — each linking straight to the scene or line it names.

**Consistency** shows the E4 report under the duration bar: "Passed ✓", a
stale notice once the script has changed since the last check, or the list
of issues, each one linked to the scene it's about.

**Editing** — every scene's summary and on-screen text, and each line's
text, delivery, speaker and emotion, are editable inline, no job needed. A
line's chip shows its duration and whether it's `estimated` or `measured`,
with a ▶ to play a measured line back. Editing anything re-times the
episode, clears both the script's and the storyboard's approval, and marks
the consistency report stale.

**Regenerating a scene** takes an optional note and re-runs just that scene
(E2); the hook's on-screen text, the cliffhanger's reveal and the
next-episode teaser each have their own field and their own regenerate (E3,
one part at a time).

**Measure with real voices** is opt-in: it synthesises every line whose
timing is still an estimate (or whose text or pinned voice changed) through
that character's own pinned voice, and keeps the audio as phase 4's line
audio. It costs **$0** on the free tiers (Edge, Gemini's free TTS) — its own
estimate chip shows the lines, characters and cost before you press it. It
stays disabled until the script is complete *and* the consistency check is
current: measuring runs after whatever the script step is still missing, and
the button must never start a call — a stale E4 — that its own chip never
showed.

**Approve** needs a complete script and a consistency check that's both
fresh (checked against the script's current revision) and passed, or, with
issues still open, a ticked **"Approve anyway"**. Approving stamps
`approved_at`; any further edit or regenerate clears it again (and the
storyboard's).

**Episode 2 and beyond** are refused — by the write button, its estimate,
and the CLI alike — until phase 5's memory step has written the previous
episode's recap into series memory. Phase 3 delivers episode 1 only.

### 9. Storyboard

Unlocked once the script is complete (approving it isn't required to start
the storyboard, only to approve the storyboard itself). Two ways to build
the shots:

- **Fast (no calls)** — deterministic, built in this process: roughly one
  shot per speaking turn plus an establishing shot, a reaction close-up, an
  insert on a prop hook, clamped to **2–4 shots per scene**, then the same
  cross-scene rule pass the planned path uses (no back-to-back repeated
  framing, a reaction close-up every few scenes, a push-in on peaks). $0,
  instant.
- **Plan shots** — one T1 call per scene, 2–4 shots each, closed lists for
  framing and camera motion, the scene's characters/place/props named only
  as tags, never as names. A scene whose call fails is named and left as it
  was; **"Plan remaining with T1"** (the button relabels itself once a board
  exists) finishes only the scenes still missing, stale, or built fast — not
  the whole board again.

Shots are grouped by scene. Each shot's card has editable **framing** and
**camera motion** (closed-list selects — a motion the style fixes for that
function is refused, not silently overridden), **modifiers**
(`handheld`, `jitter_stopmotion`) and **keep still**, its **subject tags**
shown as name chips, its **action** (read with entity names filled in;
editing it shows the raw tag text with a hint listing the scene's own tags),
a **prompt accordion** (the resolved image and negative prompts, plus an
editable prompt override), **reference thumbnails** (the character/place/
prop images the shot would send), and its own **regenerate** — T1 re-plans
just that one shot, the rest of the scene held fixed, with an optional note.
Between two shots of the same scene the transition is a fixed `cut`; at a
scene boundary it's an editable select (`cut`, `dissolve`, `fadeblack`,
`fadewhite`, `wipeleft`, `wiperight`, `slideup`). A shot made under
`prompt_only` consistency carries the same `consistency: prompt-only`
warning chip used everywhere else in the app for that mode.

When the script changes after shots exist, the affected scenes are named in
a banner ("The script changed: re-plan scene s04.") and marked stale on
their own card; when only an entity's text or image changed underneath (not
the script itself), a second banner offers **"Refresh prompts"** instead of
a re-plan.

**Approve storyboard** needs an approved script, every scene planned and
current against it, and no outdated prompts — in that order, named by
whichever the button is still waiting on.

**Preview** is a placeholder for now: "Rendering arrives in phase 4."

### The episode page

Above about **1,100 px** wide, Script, Storyboard and Preview sit as three
panes side by side; narrower, they're **tabs** below the episode header
(arrow keys/Home/End move between them). Only one layout is ever mounted —
a pane hidden by the tab layout still isn't left polling in the background.

### Estimate and route chips

Every action that might call a model shows two chips before you press it.
**Estimate**: `est. $0.00 · 10 LLM calls` (LLM steps always cost $0.00 —
there's no LLM price table, so it's honestly zero, not guessed); a
greyed/warn chip means the step isn't ready, and its tooltip says why.
**Route**: where it will run — 🖥 `local` (your own ComfyUI/Ollama), 🆓
`free`, 💸 `paid` (only reachable with `allow_paid` on), ⛔ `blocked`
(nothing in the chain can run it) — plus the specific link, e.g.
`gemini/gemini-3.5-flash-lite`.

Cast and places count in four units — **LLM calls**, **images** (text to
image), **edits** (an image made from a reference, or its text-only
`prompt_only` stand-in), **voice chars** (the sample line each pinned voice
would speak) — shown together, e.g. `est. $0.00 · 3 LLM calls · 3 images ·
6 edits · ~360 voice chars`; only what does not exist yet is counted, so
re-running a step that has nothing left to do reads `est. $0.00` and calls
nothing.

### "Awaiting approval"

A step that calls a model (concepts, bible, the style preview) is a job like
a clip job, visible in the same job list, but it ends in a status called
**awaiting approval** rather than `completed`. That status is terminal *for
the worker* — the one worker slot is freed immediately — but not for you:
the document it wrote isn't official until you approve it (or, for
concepts, until you choose one). Approving flips the job to `completed`. An
awaiting job also survives a backend restart untouched, just sitting there
waiting for you. Starting the same kind of step again before approving the
first supersedes it rather than leaving it dangling.

Cast, places and season work the same way, one level down: approving a
single character, place or prop completes any regenerate job of that one
entity that was awaiting approval, and once every character (for the cast)
or every place and prop (for places) is approved, the cast/places job itself
completes too. The season's job completes when the season is approved.

Only one step of a given story runs at a time: a second one while the first
is queued or running is refused (409), telling you to wait or cancel.

## From the CLI

`python main.py --ai-story` covers all nine steps for scripting or
testing, without a browser. Three subcommands: `new`, `step`, `list`.

```
python main.py --ai-story new --lang fr --concept tentafruit_island --style fruit_drama
python main.py --ai-story step STORY_ID concepts [--note "darker, please"]
python main.py --ai-story step STORY_ID bible --auto-approve
python main.py --ai-story step STORY_ID style --template fruit_drama \
    --override palette.accents='["#FFD400"]' --auto-approve
python main.py --ai-story step STORY_ID style_preview
python main.py --ai-story step STORY_ID cast --characters Kiwilo \
    --custom 'Figuette|support|A shy fig.' --auto-approve
python main.py --ai-story step STORY_ID places_proposal
python main.py --ai-story step STORY_ID places --auto-approve
python main.py --ai-story step STORY_ID places \
    --place 'Le Marché|A bustling fruit market.' \
    --prop 'Panier doré|A golden basket.|Kiwilo'
python main.py --ai-story step STORY_ID cast --prompt-only --auto-approve
python main.py --ai-story step STORY_ID season --episodes 8 --auto-approve
python main.py --ai-story step STORY_ID script --ep 1 --auto-approve
python main.py --ai-story step STORY_ID script --ep 1 --measure-voices
python main.py --ai-story step STORY_ID storyboard --ep 1 --fast --auto-approve
python main.py --ai-story step STORY_ID storyboard --ep 1
python main.py --ai-story list
```

`script` and `storyboard` both take `--ep N`, required — the episode
number, bounded by how many episodes the season planned. `--fast`
(`storyboard` only) builds every scene's shots deterministically in this
process: no LLM call, so no key gate either. `--measure-voices` (`script`
only) measures every line through its speaker's pinned voice, after
writing, and keeps the audio. Episode 2 and beyond are refused, naming
phase 5's memory step, exactly as the API refuses them.

`cast` takes `--characters NAME` (repeatable: a name from the concept's cast
sketch) and `--custom 'Name|role|one line'` (repeatable; `role` one of
`lead`, `support`, `recurring`, `guest`). Neither is required: with no
`--characters` and no character in the story yet, the whole cast sketch is
created; once the story has a cast, leaving `--characters` out creates none
(only `--custom` and "continue what's missing" apply). `places` takes
`--place 'Name|one line'` and `--prop 'Name|one line|Owner'` (both
repeatable; a prop's owner is a character's name or id, or left out); with
neither given it uses the saved proposal. `--prompt-only` (`cast`, `places`)
is the explicit switch to prompt-only consistency, set on the story and
printed before the step runs — nothing else ever sets it. `--episodes N`
(`season`) is 3 to 12, 8 by default. After a `cast` run, whatever each
character still lacks is printed, with a hint to re-run with `--prompt-only`
when a sheet is waiting on an editor.

`--auto-approve` means something different per step: for `bible`, `style`
and `season` it approves the one document the step just wrote; for `cast`
and `places` it approves every character, or every place and prop, that
already has everything, naming anything left short of that instead of
failing the command; for `script` and `storyboard` it approves through the
same rule the dashboard's Approve button uses — a complete script with a
fresh, passed consistency check, or a fully and currently planned
storyboard — and **never** "approve anyway": with issues still open it
prints them and exits 1 instead of forcing the approval through.

`new` creates a draft story and, with `--concept`, chooses a library concept
in the same call. `--lang` is required — there is no default, on the CLI
any more than in the API. `--tier`, `--route`, `--consistency-mode` and
`--budget-profile` set the generation profile; left out, they take the same
defaults as the dashboard (`1`, `auto`, `references`, `free` — an agreement
test keeps the CLI, the API's request model and the dashboard's form from
drifting apart).

`step` runs one step in this same process, printing the same lines the
dashboard's activity feed shows. `--auto-approve` applies to `bible`,
`style`, `cast`, `places`, `season`, `script` and `storyboard` (a concept is
approved by choosing it, and a preview is approved together with the style
it belongs to — the CLI's usage error says so if you try it on either).
`--ep` is required for `script`/`storyboard` and rejected for every other
step. `--allow-slow-chain`
(or `ALLOW_SLOW_CHAIN=1`) lets an LLM step run on a chain whose only
reachable link is the slow floor
(mirrors the clip CLI's own flag). `list` prints one line per story: id,
status, language, title. Run `python main.py --ai-story --help` (or
`... new --help`, `... step --help`) for the full option list.

Keys and `LLM_CHAIN` — and, for `cast` and `places`, `IMAGE_CHAIN`,
`IMAGE_EDIT_CHAIN`, `TTS_CHAIN` and `VISION_CHAIN` — come from the
**environment or `.env`**, the same file the clip CLI reads — never from the
dashboard's Settings. A story created or stepped from the CLI shows up in
the dashboard immediately (same
`outputs/stories/` folder, same index), but the CLI and a running server
don't coordinate: running a step from both at once on the same story lets
both write its files, last write wins. Fine for solo use; don't script the
CLI against a story you also have open in the dashboard.

Exit codes: `0` done, `1` refused or failed (reason on stderr), `2` a usage
error, `130` interrupted (Ctrl-C cancels the step cleanly; whatever it had
already written stays — every write is atomic).

## Costs and providers

**LLM calls** (concepts, bible) run on the same `LLM_CHAIN` clip jobs use —
whatever you've set in Settings → Providers or `.env`. The one AI-Story-only
rule: **a story step never calls a paid link unless `allow_paid` is on**,
even if a clip job would happily fall through to one. A paid link left out
this way is still printed (`⏭ Skipping openrouter/...: paid link, allow_paid
is off`), never silently dropped, and if the chain's *only* keyed link is
paid, the step refuses up front and names which free key to add instead.

**Image calls** — the style preview, a character's portrait, a place's day
plate, a prop's image — go through `IMAGE_CHAIN` (text to image). The
shipped default is:

```
cloudflare/flux-1-schnell, pollinations/flux, local/comfyui,
fal/flux-schnell*, openai/gpt-image-2-low*        (* = paid)
```

On a deployment with no paid keys set, this reaches Pollinations — free,
keyless, but limited to roughly one fresh image per IP per hour (about
45 seconds when it has to make one; a repeated prompt/seed can come back
from its own cache sooner) — so the style preview strip, a character's
portrait and a place's or prop's first image all cost $0.00 by default.

**Reference edits** — a character's turnaround and expressions sheet, and a
place's time variant other than `day` — go through `IMAGE_EDIT_CHAIN`
instead, in `references` consistency mode. The shipped default is:

```
local/comfyui, gemini/nano-banana-2-lite*, fal/seedream-4-edit*,
fal/flux-kontext-pro*, gemini/nano-banana-2*        (* = paid)
```

With no local ComfyUI and no paid keys set, **no link of this chain can
run for free** — every image that needs an edit stops and asks (see the
Cast and Places & props sections above, and "Troubleshooting" below) unless
you switch the story to `prompt_only` consistency, which routes the same
images back through `IMAGE_CHAIN` (free, but degraded: the character or
place may drift slightly across shots) instead.

**Voice calls** run on `TTS_CHAIN`: **Edge** (free, keyless, many
languages, the shipped default's first link), Gemini's TTS model (needs
`GOOGLE_API_KEY`, still free), or a local engine (`piper` / `kokoro` /
`chatterbox`, once its package is installed). A voice sample is a single,
fixed-link chain built from the character's *pinned* voice alone — it never
falls through to another provider or another voice; a failure names other
voices to try instead of the one that failed.

**Design-reference descriptions** (a character's uploaded image, described
once through vision) run on `VISION_CHAIN`: Gemini's `flash-lite` model by
default (needs `GOOGLE_API_KEY`, free), then an OpenRouter free vision
model, a local Ollama vision model, or Gemini's larger `flash` model.

**Settings → Budget**:

| Setting | Default |
|---|---|
| `allow_paid` | off |
| `per_episode_cap_usd` | $1.00 |
| `daily_cap_usd` | $3.00 |
| `per_story_cap_usd` | $10.00 |
| budget profile | `free` while paid is off, `one_dollar` once it's on (or pick `quality`) |

Turning `allow_paid` on does not spend anything by itself — it only lets a
paid link be *reached*, still bounded by the three caps above, still shown
as an estimate before anything runs.

**Where spend is recorded:**

- `outputs/stories/<id>/cost_ledger.json` — every call the story has made,
  free or paid, one entry each. The story page's cost total reads this.
- `data/spend.json` — today's *paid* total across the whole app (clips and
  stories together), against which `daily_cap_usd` is checked.
- `data/usage.json` — free-tier daily call counters (Cloudflare, Gemini,
  Pollinations, …), shared with clip jobs. A paid call never touches this
  file — it's proof a paid call didn't quietly eat into a free allowance.

## Local generation

If you run ComfyUI and/or Ollama, AI Story can use them instead of a hosted
API — set the story's route to `local` (or leave it `auto` and let it fall
back to a local link when one is reachable), pointed at them with
`LOCAL_COMFYUI_URL` / `LOCAL_OLLAMA_URL` (defaults `http://127.0.0.1:8188`
and `http://127.0.0.1:11434`).

Running the backend in Docker, the container has no GPU unless the NVIDIA
container runtime is configured, so the practical setup is **ComfyUI/Ollama
on the host**, reached over `host.docker.internal`
(`docker-compose.yml` already adds the `host-gateway` extra host):

```
LOCAL_COMFYUI_URL=http://host.docker.internal:8188
LOCAL_OLLAMA_URL=http://host.docker.internal:11434
```

Settings → **Local hardware** detects your GPU (or its absence) and
recommends what to install for your tier — that tab is the source of truth,
not this document. Steps 1–4 don't need any of this: the free hosted chain
is enough for concepts, the bible and the style preview. It matters most
from Cast on: `IMAGE_EDIT_CHAIN` has **no free hosted link** at all, so a
character's turnaround and expressions sheet, and a place's non-`day` time
variants, need either a local ComfyUI reachable at `LOCAL_COMFYUI_URL` or a
paid editor (`allow_paid` on) — without either, those steps stop and ask,
and the story can still be finished with `prompt_only` consistency instead.

## Where your story lives on disk

```
outputs/
  stories.json                       # index: id, title, language, style, status, updated_at
  stories/<story_id>/
    story.json                       # the bible: concept, logline, premise, world, ...
    concepts.json                    # every card "Generate 10 more" has produced
    style_lock.json                  # the draft or locked style
    style_preview.json               # the preview strip's record (images, failures)
    styles/preview/preview_<n>.png   # the preview strip's actual images
    places_proposal.json             # the proposed places and props (before they're made)
    season.json                      # the season arc: episodes_planned, arc[], series memory
    characters/<char_id>/
      character.json                 # descriptor, signature items, personality, voice, ...
      refs/portrait.png, turnaround.png, expressions.png
      refs/uploads/<32 hex>.png      # your own design references
      voice_sample.mp3               # (or .wav)
    places/<place_id>/
      place.json                     # descriptor, layout notes, time variants
      refs/variant_day.png           # the master plate; variant_night.png, etc. on demand
    props/<prop_id>/
      prop.json                      # descriptor, owner, image
      refs/image.png
    episodes/ep<NN>/                  # NN = 01..99, one per written episode
      script.json                    # scenes, lines, timing, hook/cliffhanger/teaser, consistency report
      storyboard.json                # shots, transitions, per-scene planning source
      assets/voice/
        line_<NN>.mp3 (or .wav)      # one measured line's audio (opt-in "Measure with real voices")
        line_<NN>.json               # its timing sidecar (source, text hash, provider/voice)
    cost_ledger.json                 # every call this story has made, with its cost
    activity.log                     # everything a step has printed, one line each
```

Everything here is plain JSON and PNG (or MP3/WAV for a voice sample), so a
story folder can be copied, backed up or inspected by hand. The index
(`stories.json`) is a cache of these folders — missing or corrupted, it's
rebuilt from them automatically, and it says so in the log when it does.
An episode's own writes never touch `story.json` or the index — the story
stays `ready` no matter how much its episodes change.

**Deleting a character, place or prop** removes its folder and its id from
the story — from any other character's relationships, a prop's owner, the
season arc's per-episode cast, the places proposal and a character's
current location, all of which keep the approvals they already had. The
group approval (`approvals.cast` / `approvals.places`) re-folds afterwards,
so deleting the last unapproved lead can turn an incomplete cast into an
approved one.

**Deleting a story** removes exactly its folder, its index entry, and its
step jobs (they own no files of their own); it refuses (409) while a step
of that story is queued or running — cancel it first. A sibling story, or
any file sitting next to `stories/`, is untouched.

A clip job's output directory is also just a folder under `outputs/`, so a
clip job is never allowed to be named `stories` (or `stories.json`, or the
other reserved names) — that would collide with this folder, and a delete
would take out every AI Story workspace with it. Checked before the clip
job is even created.

## Limits and troubleshooting

**"No link in the LLM chain has an API key…"** — set one of the named keys
(Settings → Providers, or the environment for the CLI) and try again.

**"The only keyed link(s) of the LLM chain are paid … and allow_paid is
off"** — you have a key, but it's for a billed provider only, and AI Story
won't spend on opt-out. Either set a free provider's key too, or turn
`allow_paid` on in Settings → Budget (bounded by the caps either way).

**"queued — waiting for the worker"** — story steps and clip jobs share the
one worker slot (there's only one, by design, so a story step never slows
down a render you're waiting on and vice versa). A bible can sit behind a
clip render; it will run once the render finishes.

**Pollinations' free rate** — the free image link is keyless and limited to
roughly one fresh image per IP per hour; a repeated prompt/seed can come
back from its own cache faster. If the style preview shows fewer than three
images, or an honest "unavailable" message, this is usually why — nothing
is wrong, and you can still approve the style without a full preview.

**A step that failed partway** — a bible step's three calls (logline/world/
themes) are independent: if the second fails, the first is still saved, and
the job ends failed with a message like `Bible incomplete: B2 failed
(<reason>). Regenerate 'world' to finish it.` — the named regenerate target
is exactly the one that needs re-running, not the whole bible. "Generate 10
more" concepts works the same way per call: if 2 of the 10 calls fail, you
get the 8 that succeeded plus the failure reasons in the activity feed, and
can retry for more.

Cast, places and season follow the same rule, per character/place/prop or
per episode: whatever succeeded is saved, the job ends failed naming each
part that didn't (`Cast incomplete: Broccolia voice sample failed (...).
Run the cast step again to fill what is missing, or regenerate
'character:char_broccolia:voice'.` / `Places incomplete: ... regenerate
'place:place_leparloir:image:night'.` / `Season arc incomplete: episode 4
failed (...). Each keeps its outline; regenerate 'season:4' to finish it.`),
and "Continue cast" / "Continue places & props" re-runs exactly what's
still missing — a character stalled on a sheet that needs an editor is left
for the next run (see below), not retried forever.

**"Needs an editor: ..."** — a character's turnaround/expressions sheet, or
a place's non-`day` time variant, is made by editing an existing image
(the portrait, or the day plate), and no link of `IMAGE_EDIT_CHAIN` can run
right now (no reachable local ComfyUI, no paid editor allowed, or the
budget caps are already spent). Nothing was generated or charged. Three
ways out: **start ComfyUI** (Settings → Local hardware) and re-run the
step; **allow a paid editor** (Settings → Budget → `allow_paid`, still
bounded by the caps); or **switch this story to prompt-only consistency**
(the banner's own button, or `--prompt-only` on the CLI) — the same images
are then made from text alone and labelled `consistency: prompt-only`.

**"Pick a voice"** — a character's text is written, but no catalogue voice
was left unused for it (every voice `TTS_CHAIN` can reach for the story's
language is already pinned by another lead or support). Open "Other
voices" on the character's card and choose one manually — a recurring or
guest character may reuse a voice already given to someone else, but a
lead or support never will.

**An upload refused** — over 10 MiB, over 40 megapixels, not decodable as
an image, or not PNG/JPEG/WebP/GIF: the message says which and nothing is
stored. A character already at 4 design references refuses a fifth until
one is removed. A design reference that could not be described (the vision
chain failed twice) keeps the upload — it is simply not folded into the
character's text yet; regenerating the character's text (K1) tries
describing it again first.

**"...approve episode N and run the memory step (it arrives in phase 5)
first."** — episode 2 needs episode 1's recap in series memory, which
phase 5's memory step writes once episode 1 is approved and that step runs.
Until then, only episode 1 can be written, storyboarded or estimated; the
dashboard, the API and the CLI all refuse the same way.

**"Check the consistency first."** — "Measure with real voices" won't run
while the consistency check is missing or stale, even though the script
itself is otherwise complete: measuring runs after whatever the script step
still needs to fill in, and that can include a fresh E4 call the Measure
button's own estimate never showed. Press "Check again" first.

**A script step that stopped partway** — like the earlier steps, a script
is one small call at a time (the beat sheet, then one call per scene, then
the hook/cliffhanger/teaser, then the consistency check), each saved as it
lands; a run that hits the free tier's latency or the 30-minute step budget
ends failed naming what's left, and "Continue writing" picks up exactly
there rather than starting the episode over. A scene's own call can fail
independently — it's named (`scene:1:s04`) and left as it was; regenerating
just that scene, or running the step again, fills it in without touching
anything already written.

**A storyboard that stopped partway** — the same idea, one T1 call per
scene: a scene whose call fails is named and kept as it was, and "Plan
remaining with T1" finishes only the scenes still missing, stale, or built
by the fast path — never the whole board again.

**Garbled French text** — the free model occasionally drops an elision's
apostrophe or garbles an accent in its raw reply. A dropped elision is
repaired automatically before you ever see it; an occasional garbled accent
(e.g. a `â` coming back wrong) is not — it's a rare raw-model quirk, not
something the pipeline introduces, and it can be fixed the same way any
other line is: edit the line's text by hand.
