# Phase 7 — quality overhaul: every shot animated, quality media, prompts with context, a story you can follow

Written 2026-10-01 at the end of phase 6, from the human's phone watch (walk step 12) and a read-only audit of the
code and the live stories. **This phase replaces the old phase 7** (reference-video import,
`08-phase-7-reference-import.md`): the human took that off the schedule, and its file is kept for later. The
direction is DEC-219. This file frames the next session. That session still runs EXPLORE → CLARIFY → PLAN and
waits for the human's approval before any code.

---

## 1. The human's verdict (2026-10-01, after watching story A `979c8376e43e` and story B `04feb539840f` ep 1)

| # | Finding | In the human's words (lightly translated) |
|---|---|---|
| T2-P6-F2 | Only one shot moves | "Only one shot has been animated. I want the full video animated, each shot animated." |
| T2-P6-F3 | The story cannot be followed | "The story is not understandable." |
| T2-P6-F4 | Cheap image AI looks bad | "The images of pollinations AI are awfully ugly. Do not use cheap AI to generate characters, decors and props. If the PC cannot run good image/text or video generation, strongly recommend billed APIs." |
| T2-P6-F5 | Prompts lack context | "The prompts are too short: not enough context for proportions, traits and the specific image needed. Upgrade the prompts." |

Unchanged by the verdict (standing rules): no auth, ever; `allow_paid`, the caps, a shown estimate and the human's
explicit go gate every paid call; keep every story.

## 2. What the audit found (facts, 2026-10-01; file:line at `feat/ai-story-phase-6` HEAD)

### 2.1 Defects to fix first (they undercut everything else)
- **T2-P6-F6 — The I2V prompt sends raw tags.** `steps/clips.py:220` passes the storyboard shot to
  `video_plan.build_video_prompt` (`video_plan.py:104`), which uses `shot["action"]` as stored. That action still
  holds the T1 tags; only `shots.resolve_shot` (`shots.py:342-373`, the image path) resolves them.
  - Story A's paid seedance clip was prompted with "@char_captain_obvious and @char_miss_overthink stand in
    #place_city_square:day looking shocked at a giant toaster. snappy 2D animation, …".
  - So the video model never saw a description. Every clip prompt is 21–42 words and tag-laden.
- **T2-P6-F7 — Name stripping eats descriptions.** `names.without_names` (`names.py:19-34`) replaces every entity
  name as a whole word in the final prompt.
  - A place named "City square" turns its own descriptor "A bustling urban city square…" into "A bustling urban the
    place…" in every shot prompt (`979c8376e43e/places/place_city_square`).
- **Small:** `character_prompt_block` (`prompting.py:239`) joins with ", wearing", which yields "mouth., wearing".
- **Dead config:** the budget profiles' `images`/`tts` policies (`budget_profiles.json`, validated at
  `budget.py:96`) are never read. Only `animate` is (`clips.py:423,485`).

### 2.2 Animation (F2)
- **New-story default:** `defaults.py:18-21` gives tier 1, no clips at all, with the `free` profile ("animate none").
  Animation therefore needs tier ≥ 2 and another profile.
- **The profiles.** `one_dollar` animates key shots within a $1.00 cap: after the images, that buys about one 2–3 s
  clip, which is exactly what both live episodes show. `quality` animates all shots, with no cap of its own.
- **Live caps:** $1.00 an episode, $3.00 a day, $10.00 a story.
- **Every shot animated, priced from story A's and B's real shot durations** (`pricing.py:44-48`, the link minimums
  in `video.py:51-57`):

  | Link | Minimum clip | Story A (20 shots, 59.9 s) | Story B (18 shots, 48.3 s) |
  |---|---|---|---|
  | `fal/seedance-1-pro-fast` 720p | 2 s, $0.022/s | 72 s billed → **$1.58** | 60 s → **$1.32** |
  | `fal/kling-2.5-turbo-std` | 5 s, $0.042/s | 115 s → **$4.83** | 100 s → **$4.20** |
  | `gemini/veo-3.1-lite` 720p | 4 s, $0.05/s | 96 s → **$4.80** | 80 s → **$4.00** |
  | `fal/ltx-2.3-fast` 1080p | 6 s, $0.06/s | 126 s → **$7.56** | 108 s → **$6.48** |

  - A link with a long minimum clip pays for seconds the render then trims away (kling sells 5 s for a 2.5 s shot).
  - fal bills seedance on the output's own size × (24·s + 1) frames: A-100, confirmed against fal's export.

### 2.3 Images (F4)
- **Shot images across all 9 live stories (193):** pollinations 56 %, cloudflare flux-1-schnell 28 %,
  fal seedream-4-edit 11 %, fal flux-schnell 4 %.
- **Character sheets (54):** pollinations 87 %. **Place plates (11):** pollinations 73 %. **Props (1):**
  pollinations.
- **`IMAGE_CHAIN` puts free links first:** cloudflare, pollinations, local, fal flux-schnell*, openai gpt-image*
  (`generation.py:55-70`; * = paid).
- **Consistency mode.** 8 of the 9 stories run `prompt_only`, so they never touch `IMAGE_EDIT_CHAIN`
  (nano-banana-2*, seedream-4-edit*, flux-kontext-pro*). Why they ended up there is UNKNOWN: the coded default is
  `references`.
- **Keys set:** google, fal, cloudflare, nvidia, openrouter. **Not set:** openai, groq, mistral, gemini_paid.

### 2.4 Prompts (F5)
- **Shot image prompts run 187–386 words**, but most of that is the same style/camera/lighting boilerplate on every
  shot. The shot-specific part is T1's one action sentence, capped at **30 words** with entities as tags
  (`prompts.py:1860-1861`).
- **Inside that action** a character becomes a handle of ≤ 10 words (`shots.py:71,118-144`). Their full traits
  appear only once, in a separate subjects block, not tied to the action.
- **Caps at the source:**
  - the K1 character descriptor ≤ 45 words, and signature items ≤ 8 words each (`prompts.py:450-451`);
  - a prop descriptor ≤ 30 words, with no owner or scale context (`prompts.py:550`, `prompting.py:228`);
  - place layout notes (left/right/back/foreground) are stored but **left out of the master plate**
    (`prompting.py:204` vs `:248`).
- **No structured visual spec exists** for proportions, height relative to others, face, hair, wardrobe layers,
  colours or materials, so consistency rests on free text.
- **Real example**, story A, Captain Obvious: "A tall yellow geometric cylinder with a solid rectangular blue cape,
  wearing oversized round glasses, sporting simple dot eyes and a permanent flat line mouth., wearing Comically
  oversized magnifying glass, Stiff rectangular blue cape. …"

### 2.5 Story comprehension (F3)
- **LLM: 100 % `gemini/gemini-3.5-flash-lite`** (318 of 318 writing calls). groq has no key; OpenRouter is paid
  and off.
- **Story A's script repeats itself.** Line 1 is "That object is a giant toaster."; line 3 is "That large object in
  the square is a giant toaster."
- **Lengths.** Story A runs 56.9 s, just inside the window. Story B runs **45.1 s, 9.9 s under**. The other French
  episodes ran 41–48 s (A-086/A-088/A-089).
- **Structure.** No narrator (off by default). No recap in ep 1. Story B shows no hook text. 5 of 20 and 6 of 18
  shots carry no line.
- **Subtitles:** `word_pop` (one word at a time) on story A.
- **Images vs lines:** the shot images match the line's topic but show generic poses, not the plot beat.

### 2.6 Hardware (F4's "if the PC cannot …")
- **This host:** 4 ARM cores, 23 GB RAM, no GPU, running in a container. ComfyUI and Ollama are unreachable.
- **App profile:** `container_no_gpu` (`hardware.py:177-189`).
- **Verdict: no good local image, LLM or video generation is possible here.** Quality has to come from billed APIs.
- **Today's advice is too weak.** For this profile `hardware.py:254-262` says only: "no local image model without a
  GPU; the free hosted links or a paid editor within the caps."

## 3. Goals (acceptance in the human's terms)
- **G1, every shot moves.** A story set to animate gets a clip for every shot. The estimate before the run shows
  the episode's total, and refusals carry the numbers.
- **G2, media that look good.** Characters, places, props and shots come from a quality image model, kept
  consistent through references. No free or cheap link (pollinations, cloudflare flux-schnell, fal flux-schnell)
  is used for those roles. When the host cannot run good models, the app says so plainly and recommends billed
  APIs with their real per-episode cost.
- **G3, prompts with context.**
  - Every entity has a structured visual spec: proportions, scale against the others, face, hair, wardrobe,
    palette, materials and signature items.
  - Every shot prompt says who does what, where, with which expression and composition.
  - Every clip prompt describes the motion with the entities resolved.
  - Boilerplate is short and comes last.
- **G4, a story you can follow.** A first-time viewer can tell who wants what, what happens and why it matters.
  The episode sits inside its length window, has no repeated lines, and each shot shows its beat.
- **The phase's acceptance walk:** one new story, episode 1, made end to end on the new quality preset, every shot
  animated, under a budget the human sets. The human watches it on the phone and says the story is clear, the
  characters and places look good, and every shot moves.

## 4. Workstreams: what the next session explores, asks about and plans

**W0. Fix the defects first** (2.1): resolve tags in the clip prompt, make name stripping exact (strip only names,
never descriptor text), the ", wearing" join, and the dead profile config (use it or remove it). One fail-first test
each (DEC-192). A clip whose prompt changes goes stale, so the estimate shows the cost of redoing it.

**W1. Media quality policy (images).**
- **Explore:** today's best image models for character sheets with references, and their live prices, read at
  session time on the providers' pages:
  - on fal (seedream 4.x, flux-kontext-pro or its successor, …);
  - Google (nano-banana 2 on a paid key, Imagen);
  - OpenAI (gpt-image, which needs a key).
- **Design:** choose a link per role (cast / places / props / shots) from a quality-tier chain. Retire the free
  links for those roles, and decide what free links may still do, if anything (draft previews?). Make
  `references` mode the real default and find out why 8 of 9 stories are `prompt_only`.
- **Ask:** which keys the human will fund (fal only? Gemini paid? OpenAI?). The resolution. Whether existing
  stories are regenerated (opt-in, with estimates) or only new ones change.

**W2. Every shot animated (video).**
- **A per-episode budget that fits:** CLARIFY offers the 2.2 table, plus any better model found in EXPLORE
  (e.g. a higher-tier seedance, kling pro, veo 3.x, or others current at that time, with live prices).
- **Fitting shot lengths to link minimums,** so seconds are not bought and then trimmed: the storyboard shot length
  vs the link's sizes, or a different link per shot length.
- **Continuity between neighbouring clips** (a start frame from the keyframe; the end-frame question).
- **The keyframe sent to the model is 9:16** (the phase-6 follow-up: crop at the source).
- **Clip resolution** (720p vs 1080p) against cost.
- **Profile defaults:** what `quality` means now, and whether `one_dollar` survives.

**W3. Prompts with context.**
- **A structured visual spec** per character, place and prop. This is a schema change, with backward compatibility
  for stored stories. Who writes it: K1 / the places step on a stronger LLM?
- **Raise or rework the caps:** K1's 45 words, T1's 30-word action, the ≤ 10-word handle.
- **Assembly order:** specific first (subject + action + expression + composition), then the place with its layout,
  then a compact style tail.
- **Place layout notes go into the master plate.** Props carry owner and scale.
- **Per-model prompt adapters:** video models want motion verbs and camera language; image editors want reference
  roles.
- **A prompt preview on the shot card,** read-only, so the human can see what is sent.

**W4. A story you can follow.**
- **Diagnose before fixing.** Re-read story A and B's scripts and storyboards against the episode on the phone,
  and list concretely where a viewer gets lost.
- **Candidate fixes:**
  - a stronger writing LLM: the human chooses a provider (OpenRouter-funded, or direct: Claude / GPT / Gemini
    Pro), which the paid-LLM booking from phase 6 (DEC-206) already supports;
  - a "first-watch" judge prompt (who wants what, what happens, why it matters) that blocks approval with reasons;
  - a duplicate-line check;
  - the length window enforced, with French scripts fixed (A-086/88/89);
  - a hook text on every episode;
  - an optional narrator or context captions;
  - subtitle defaults (`word_pop` vs `two_line`);
  - shots that show the beat (T1 asked for the plot action, not a pose).

**W5. Hardware-aware advice and a quality preset.**
- **Advice:** on a host that cannot run good models (`cpu_only`, `container_no_gpu`, small VRAM), Settings and the
  new-story wizard show a clear recommendation to use billed APIs. It names the keys to add and the per-episode
  cost of the quality preset.
- **One "Quality (billed APIs)" preset** sets tier, profile, chains per role and suggested caps. Paid calls still
  run only with `allow_paid`, the caps, the shown estimate and the human's go.
- **The free route stays available,** labelled for what it is.

## 5. Out of scope
- **Reference-video import** (the old phase 7, `08-phase-7-reference-import.md`): removed from this phase by the
  human on 2026-10-01; kept for later.
- **Phase-6 follow-ups that are not about quality** (CHECKPOINT "Follow-ups collected"), unless the plan argues
  for one. Examples: F6/F7 on the story page, the "unknown" route chip, the ffmpeg 7.1.5 music-bed cut, a live
  ComfyUI run (no GPU), the Groq 1010 User-Agent block, and booking $0 for a fal input-download failure (A-109).
- **Auth of any kind.**

## 6. Questions for the human at CLARIFY (ask; do not assume)
1. **Budget:** the per-episode ceiling for every shot animated, plus images and voices (see the 2.2 table), and the
   daily and per-story caps.
2. **Keys:** which providers the human will fund (fal; a paid Gemini key; OpenAI; OpenRouter or a direct LLM
   provider for writing).
3. **Quality vs cost:** 720p or 1080p clips, and which video model after EXPLORE's comparison.
4. **Existing stories:** regenerate (opt-in, estimated) or leave as they are?
5. **Narrator and subtitles defaults,** and whether a "first-watch" judge may block approval.
6. **The free route:** keep it for drafts and previews, or hide it behind the advice?
7. **New-story defaults:** tier 2 with the quality preset whenever the keys are present?

## 7. Constraints for the plan
- **Repo protocol:** a FULL task; plan stages each committable and revertible; the riskiest stage named; a
  rejected alternative; a DECISIONS check (DEC-219, DEC-194/215 paid runs, DEC-204 sticky links, DEC-206 paid LLM,
  DEC-216/217 framing).
- **Ids:** DEC-220+ and A-110+.
- **Tests:** DEC-176 (Tier-1 once per stage, `-n 4`, both environments, by the orchestrator) and DEC-192 (essential
  tests only, fail-first). A change under `clipping/studio/**` re-records the RC-A1 guard.
- **Paid walks:** single CLI processes with per-process caps (DEC-215 / the `walk6.py` pattern), each after a
  shown estimate and the human's go. `allow_paid` stays off in Settings.
- **Regression contract to carry:**
  - RC-V1…V8 (phase 6);
  - RC-M2 (tier-1 golden unedited, unless a stage changes the still path on purpose and says so), RC-M3, RC-M8,
    RC-M9 (no auth), RC-A1 (the clip render layer);
  - stored stories keep rendering byte-identically until the human opts them into regeneration.

## 8. Start prompt for the next session (paste as is)

```text
Start AI Story phase 7 — the quality overhaul — under the repo protocol (FULL task), on this Ubuntu VPS.
It replaces the old phase 7 (reference import, off the schedule; do not plan it).
Work in a new worktree .claude/worktrees/ai-story-phase-7 on a branch feat/ai-story-phase-7 from main; never switch
the main checkout's branch (the rzc-backend container bind-mounts it).

Load first: .claude/plans/ai-story/15-phase-7-quality-overhaul.md (this phase's brief: my verdict, the audit's
facts with file:line, goals G1–G4, workstreams W0–W5, my CLARIFY questions); DEC-219 in .claude/DECISIONS.md (the
direction) and DEC-200…218 (phase 6); .claude/CHECKPOINT.md top section (the phase-6 close and its follow-ups);
.claude/ASSUMPTIONS.md A-100…A-109; .claude/claude-action.log tail; .claude/plans/ai-story/00-MASTER-SPEC.md parts
on prompts (2.x), assets and the budget profiles.

Then EXPLORE (agents; include a live price check of current image and video models on fal, Google and OpenAI,
read on their pages at session time), CLARIFY (section 6's questions plus what EXPLORE opens), and PLAN. Wait for
my approval before any code. W0's defects (tags in the clip prompt, name stripping) are stage 1.

Standing rules: NO AUTH on the app, ever. allow_paid stays OFF in Settings; any paid run needs a shown estimate and
my explicit go, as a single CLI process with per-process caps. Deploy only at 0 jobs. Never run Tier-1 while a
scratch API server from the worktree is writing. Tests: DEC-176 and DEC-192. Commits use explicit paths and no
trailers; never git stash; never revert with git checkout for fail-first. Push with
GIT_SSH_COMMAND="ssh -i ~/.ssh/github_osc_better -F /dev/null -o IdentitiesOnly=yes". Keep all stories.
```
