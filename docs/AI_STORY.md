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
this is the phase-1 foundation plus steps 1–4 of the 13-step workflow. It is
extended as each later phase lands — see "Where it stands" below for what is
not here yet.

## Where it stands

| # | Step | What it produces | Status |
|---|---|---|---|
| 1 | New story | a draft story (language, optional seed text, optional style) | available |
| 2 | Concepts | ten concept cards to choose from, or generate ten more | available |
| 3 | Bible | logline, premise, tone, world, themes, audience | available |
| 4 | Style | a locked style (palette, typography, consistency mode) + a preview strip | available |
| 5 | Cast | characters: reference sheets, voices | phase 2 |
| 6 | Places & props | locations and recurring objects | phase 2 |
| 7 | Season arc | the season's episode-by-episode arc | phase 2 |
| 8 | Episode script | scenes and dialogue for one episode | phase 3 |
| 9 | Storyboard | shots, framing, camera moves for the script | phase 3 |
| 10 | Assets | the images, voice lines, SFX/BGM for the storyboard | phase 4 |
| 11 | Render | the final `.mp4` with burned subtitles | phase 4 |
| 12 | Metadata pack | title/description/hashtags per platform | phase 4 |
| 13 | Next episode | recap, audience-feedback digest, new characters/twists | phase 5 |

In other words: today you can create a story, pick or invent a concept,
write and edit its bible, and build and lock its visual style, with a live
preview of what that style looks like. You cannot yet generate characters,
write an episode, or render anything — that is phases 2 through 5.

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
  though it only starts to matter from phase 2 on (no references exist yet).

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

### Estimate and route chips

Every action that might call a model shows two chips before you press it.
**Estimate**: `est. $0.00 · 10 LLM calls` (LLM steps always cost $0.00 —
there's no LLM price table, so it's honestly zero, not guessed); a
greyed/warn chip means the step isn't ready, and its tooltip says why.
**Route**: where it will run — 🖥 `local` (your own ComfyUI/Ollama), 🆓
`free`, 💸 `paid` (only reachable with `allow_paid` on), ⛔ `blocked`
(nothing in the chain can run it) — plus the specific link, e.g.
`gemini/gemini-3.5-flash-lite`.

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

Only one step of a given story runs at a time: a second one while the first
is queued or running is refused (409), telling you to wait or cancel.

## From the CLI

`python main.py --ai-story` covers the same four steps for scripting or
testing, without a browser. Three subcommands: `new`, `step`, `list`.

```
python main.py --ai-story new --lang fr --concept tentafruit_island --style fruit_drama
python main.py --ai-story step STORY_ID concepts [--note "darker, please"]
python main.py --ai-story step STORY_ID bible --auto-approve
python main.py --ai-story step STORY_ID style --template fruit_drama \
    --override palette.accents='["#FFD400"]' --auto-approve
python main.py --ai-story step STORY_ID style_preview
python main.py --ai-story list
```

`new` creates a draft story and, with `--concept`, chooses a library concept
in the same call. `--lang` is required — there is no default, on the CLI
any more than in the API. `--tier`, `--route`, `--consistency-mode` and
`--budget-profile` set the generation profile; left out, they take the same
defaults as the dashboard (`1`, `auto`, `references`, `free` — an agreement
test keeps the CLI, the API's request model and the dashboard's form from
drifting apart).

`step` runs one step in this same process, printing the same lines the
dashboard's activity feed shows. `--auto-approve` applies to `bible` and
`style` only (a concept is approved by choosing it, and a preview is
approved together with the style it belongs to — the CLI's usage error says
so if you try it elsewhere). `--allow-slow-chain` (or `ALLOW_SLOW_CHAIN=1`)
lets an LLM step run on a chain whose only reachable link is the slow floor
(mirrors the clip CLI's own flag). `list` prints one line per story: id,
status, language, title. Run `python main.py --ai-story --help` (or
`... new --help`, `... step --help`) for the full option list.

Keys and `LLM_CHAIN` come from the **environment or `.env`**, the same file
the clip CLI reads — never from the dashboard's Settings. A story created or
stepped from the CLI shows up in the dashboard immediately (same
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

**Image calls** (the style preview, later phases' character/place sheets
and shots) go through their own chains, `IMAGE_CHAIN` and
`IMAGE_EDIT_CHAIN`. The shipped default `IMAGE_CHAIN` is:

```
cloudflare/flux-1-schnell, pollinations/flux, local/comfyui,
fal/flux-schnell*, openai/gpt-image-2-low*        (* = paid)
```

On a deployment with no paid keys set, this reaches Pollinations — free,
keyless — so the style preview strip costs $0.00 by default.

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
not this document. Phase 1 itself doesn't need any of this: the free hosted
chain is enough for concepts, the bible and the style preview. It starts to
matter once character and place references (phase 2) push more image
generation through the pipeline.

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
    cost_ledger.json                 # every call this story has made, with its cost
    activity.log                     # everything a step has printed, one line each
```

Everything here is plain JSON and PNG, so a story folder can be copied,
backed up or inspected by hand. The index (`stories.json`) is a cache of
these folders — missing or corrupted, it's rebuilt from them automatically,
and it says so in the log when it does.

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
