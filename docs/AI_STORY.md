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
and capped per episode, per day and per story (**$4 / $12 / $40** by default,
Settings → Budget). Nothing paid ever runs unless you turn it on. With a fal
key, a new story starts on the **quality pipeline** — every shot a video
clip, quality images — described in "The quality pipeline (v2)" below.

This document covers what exists today. AI Story is being built in phases;
this is the phase-1 through phase-5 foundation, all 13 steps of the workflow
— enough to take an episode from concept to a finished vertical `.mp4` with
its metadata pack, then carry it into episode 2 and beyond: a series memory
that opens each new episode on a recap and a hook to pay off, audience
feedback that can steer where the story goes, new characters and twists
proposed between episodes, and a re-edit path that changes one line or one
shot without rebuilding the rest. It is extended as each later phase lands —
see "Where it stands" below for what is not here yet.

## Where it stands

| # | Step | What it produces | Status |
|---|---|---|---|
| 1 | New story | a draft story (language, optional seed text, optional style) | available |
| 2 | Concepts | fourteen library concept cards to choose from, or generate ten more | available |
| 3 | Bible | logline, premise, tone, world, themes, audience | available |
| 4 | Style | a locked style (palette, typography, consistency mode) + a preview strip | available |
| 5 | Cast | characters: reference sheets, voices | available |
| 6 | Places & props | locations and recurring objects | available |
| 7 | Season arc | the season's episode-by-episode arc | available |
| 7b | Knowledge base (v2 stories) | world, beat timeline per episode, props registry, starting state; approved before episode 1 | available |
| 8 | Episode script | scenes and dialogue for one episode | available |
| 9 | Storyboard | shots, framing, camera moves for the script | available |
| 10 | Assets | the images, voice lines, SFX/BGM for the storyboard | available |
| 11 | Render | the final `.mp4` with burned subtitles | available |
| 12 | Metadata pack | title/description/hashtags per platform | available |
| 13 | Next episode | series memory, audience-feedback digest, proposed characters/twists | available |

In other words: today you can create a story, pick or invent a concept,
write and edit its bible, build and lock its visual style, cast its
characters with reference sheets and voices, populate its places and props,
and plan its season arc — a story reaches **ready** once the cast, the
places and props, and the season are each approved. From there you can
write and storyboard episode 1: a script (scenes, dialogue, timing), then
shots for it, then its assets (shot images, voice lines, SFX and BGM), then
render it to a finished vertical `.mp4` and write its metadata pack — or run
the whole thing in one go with the fast track. Once an episode's script is
approved, its series memory can be written and approved (step 13) — that
unlocks episode N+1's script, storyboard and fast track, so the season can
carry on past episode 1. A line, or one shot, can then be changed and
re-rendered on its own, without rebuilding the whole episode.

**2026-10-04.** A round prompted by watching the agent-mode episodes
against a reference performance answered four complaints: the spoken lines
read poorly and the story was hard to follow; the lipsync looked wrong,
since a clip's invented mouth movement was glued, after the fact, to a flat
text-to-speech read; a concept card could drift from the idea typed into
the seed; and the pipeline spent more for a worse result than a creator
picking a take by eye. Four changes follow — each described in full where
it belongs below: the writing calls that matter most (concepts, the bible,
the episode script, the first-watch judge) now run on a **premium Gemini
chain** when its key is set, not the free chain alone ("Costs and
providers"); every concept card is now checked and judged against the seed
as a **binding brief**, never free to drift ("Concepts"); each character's
spoken line can now be its own clip that **speaks it on camera**, lips and
voice one take, instead of a silent clip relipped afterwards (the **native
speech** profile, "Native speech (API route)"); and, because a hosted clip
able to speak costs several times a subscription's per-clip credit price, **a
new story on a host with its quality keys set now defaults to the manual
mode** — the app writes a crafted shot brief per clip and the human makes it
on their own Google Flow or Higgsfield / Freepik subscription and uploads it
("Your own clips (the manual mode)"). The fully automated Veo API route for
native speech is still there, a per-story switch, bounded by the same caps.

**2026-10-05.** A cast the app refused with a sentence nobody could read
("would bring today to $8.58 of the $4.00 daily cap", for a cast that costs
$0.60) led to a second round, on the cap first and then on a list of
upgrades. The cap now says three numbers (what was spent today, what this
job costs, the cap), offers to allow more for today only, and counts its day
in your own time zone ("Budget: today's limit"). Gemini became a second link
for the character sheets, the place plates and the props, and a story may
prefer it ("Costs and providers"). Around that: Clips mode takes B-roll from
Pexels, Pixabay and a folder of your own ("Stock footage for Clips mode");
ElevenLabs voices exist, paid and never chosen for you; a story carries its
own subtitle look ("11. Render"); a story can be made at 16:9 or 1:1
("1. New story"); `fal/ltx-2.5-fast` ends the video chain;
Claude can write the premium calls; and the creators' method came in as
choices on a v2 story, not as a new pipeline: ten universes and a Viral 3D
style, one front-and-back character sheet, bodies made of the character's
own matter, clip prompts written as one continuous action, and appearance
variants of a character ("What else the form decides").

**Plan 23 is complete (code).** Its last stages added a voice recording a
character can speak with, cloned locally by chatterbox ("5. Cast"); the story's
frame, 16:9 and 1:1 as well as 9:16 ("1. New story"); stock cutaways that fill
establishing shots for free ("Stock cutaways (AI Story)"); and an A/B bench of
the writing models ("Benchmarks"). What is left is yours: the one-clip French
probe for LTX-2.5, the writer A/B, the rebuild with `INSTALL_LOCAL_TTS=1` if you
want cloned voices, and your own walk of each.

## The quality pipeline (v2)

Phase 7 rebuilt how an episode looks and reads, after the first episodes
were judged hard to follow, mostly still, and drawn on draft-quality free
image links. Everything below applies to a **v2 story**
(`generation_profile.pipeline: "v2"`); a story created before it keeps its
behaviour exactly.

**Who gets it.** With `FAL_KEY` in Settings, a story created without a
profile — the new-story form leaves the profile to the server unless you
change it — is a v2 story on the **Quality (billed APIs)** budget profile:
tier 2, route `api`, `references` consistency, the `serial_60s_v2` episode
template, the narrator on. The form says so ("Fully animated: every shot is
a video clip") or says why not (tier 1 is stills with motion; `free` buys no
clip; `FAL_KEY` missing). An existing story moves onto it with **Animate
every shot** on its Visual tier card, or by patching its
`generation_profile`, while no episode has a script; if its cast already
exists, run the Cast step again afterwards (it writes each character's
dossier and look and redraws the sheets).

**Every shot is a clip.** An episode is 6–18 beat shots of 3–12 s (the hook
3–6 s) in a 55–75 s window, and each shot is one image-to-video clip on
`fal/seedance-1-pro-fast` at 720p (1080p is a per-story switch,
`generation_profile.video_resolution`). On a v2 story that animates every
shot, the assets approval and the render refuse while a shot has no current
clip — never made, failed, stale or still generating — naming each shot and
what to do; no shot is ever shown as a still with a zoom. The one exemption
is a shot you pin `keep_still` yourself. A scene is planned as two beat shots
when its voices will run past the longest clip the link sells (12 s on
seedance, 8 s on Veo) — judged on the length the voices will measure, not on
the text estimate alone: Gemini's voices speak about a third longer than it.
A shot that still runs past the longest clip once the voices are measured is
covered by that clip slowed to the shot's length, at most 1.25x (the estimate
and the feed say "sh03 runs 14.133 s: its 12 s clip is slowed to cover it
(0.85x speed)"); only a shot longer than that is refused, and the message
then says to plan the scene as two shots or shorten its lines.

**The clips perform.** Each clip asks for a performance, not stillness:
whoever speaks one of the shot's lines on screen speaks with the mouth moving
on the words and the face carrying the emotion (the words themselves are never
in the prompt — your TTS voice is the voice), the others react visibly with a
reaction drawn from the line's emotion (shocked: stepping back, eyes widening;
tense: leaning in, jaw set; scheming: narrowing the eyes, a slow smile), and
the staging's turns, faces and held props follow. The clip keeps every
character's look, the set and the light from the keyframe and lets the
characters move; each style ends it on its own lively motion suffix
(`tier2_prompt_suffix_v2`, no camera direction — the camera sentence says
it). The storyboard asks each shot for one clear action per character and
never the previous shot's camera motion (a repeat is moved to the next
motion), and a shot's own camera beats the style's "push in on peaks". For
rhythm, a body scene with two lines or two characters and room for two shots
of at least 3 s is planned as **two beat shots** (the recap, hook and
cliffhanger keep one unless they run past the clip), so an episode is now
about 6–18 shots. A storyboard planned before keeps its shots; a scene takes
the new count when it is planned again. Legacy stories are unchanged.

**Images by role, never on draft links.** Character sheets, place plates and
props are made on fal Seedream 4.5 (text-to-image, then edits of that image
for the turnaround, expressions and variants), keyframes on
`fal/seedream-4.5-edit` with up to 10 reference images. The free draft links
(cloudflare, pollinations, flux-schnell, gpt-image-2-low) are never used for
these; the style preview stays a free draft. A v2 keyframe is centre-cropped
to an exact 9:16 when it is made.

Since plan 23 the sheet, plate and prop roles have a second link:
`gemini/nano-banana-2-lite` ($0.0336 an image, 1K output), after
`fal/seedream-4.5` and `fal/seedream-4.5-edit`. Fal stays first. Lite is the
link a cast falls to when a fal call is refused (near the daily cap a $0.04
call can be refused where $0.0336 still fits) or when fal is down. It needs
`GEMINI_PAID_API_KEY` and is skipped, with the reason shown, while that key is
missing (the free `GOOGLE_API_KEY` never serves it). Within one cast or places
job the provider that answered first is tried first for the rest of the job, so
one job does not mix providers; a later run, or one regenerated image, starts
from fal again, so a portrait and its sheets can come from different
providers. Gemini keeps no seed, so a portrait it draws is not reproducible
by seed. A story can put Gemini first instead (`image_preference:
gemini_first`, "What else the form decides").

**Writing.** AI Story writes on its own chain (`STORY_LLM_CHAIN`, default:
NVIDIA NIM nemotron-3 ultra, then super, then OpenRouter mistral-medium-3.1 —
paid, skipped while `allow_paid` is off — then free Gemini). A reply that
fails validation twice moves on to the next model.

**Looks, dossiers and the knowledge base.** Each character gets a dossier
(backstory, goal, need, fears, secrets, relationships, voice patterns) and a
structured look (build, silhouette, face, hair, material, height, palette,
wardrobe sets, presentation) before its sheets are drawn; each place a look
(layout map, scale, light per time of day, props that live there); each prop
a look (material, colour, real scale). After the season, the **Knowledge
base** step writes the world, a beat-by-beat timeline per planned episode, a
props registry and each character's starting state; you approve it in the
dashboard, and episode 1's script waits for that approval (a later change
makes it stale until approved again). Every writing call then gets its
slice of it — the goals, secrets and relationships of who is in the scene,
what each knows so far, the place's layout and light — and after each
episode a continuity ledger records where everyone is, what they wear and
hold.

**Prompts.** A keyframe prompt (≤ 220 words) says, in order: what each
reference image is, the beat (the action and the line's delivery), where
each character stands with its look and relative height, the camera, the
place, the style and a clean-frame clause ("no captions, lettering, logos or
watermarks"). A crowded shot is shortened in steps — compact image roles,
shorter looks and place, then the layout and prop sentences the sent images
already show. A clip prompt (≤ 80 words) says what moves, the camera, and
that the set and the looks stay as in the first frame. Entity names never
reach a prompt; a name that is only the thing's noun ("Monocle" for a golden
monocle) is kept so its description is never garbled.

**Voices and subtitles.** The narrator is on with its own voice, distinct
from the cast's; voices are proposed in the story's locale (fr-FR, en-US);
each line is spoken with a rate and pitch from the character's voice and the
line's emotion. Subtitles default to two lines, and a word-pop card is never
shorter than 150 ms.

**Two looks before money goes on clips.**
- *The script.* After the consistency check, a first-watch check (J1) reads
  the episode as a first-time viewer would — who wants what, what happens,
  why it matters — and flags an unclear goal, an unmotivated turn, an
  unintroduced character, an object never shown, a repeated line or a hook
  with no on-screen text (the last two are also checked without the LLM, and
  a reply repeating a line is refused and asked again). It knows the format
  (the episode's length and spoken words, that a scene's first line is what
  is on screen) and that a serial keeps questions open on purpose — the
  hook's tease, the cliffhanger's reveal, a secret kept for later are never
  issues — and it marks each issue **blocking** (a first-time viewer cannot
  follow who the main character is, what they want, what happens or why it
  matters) or **minor** (they follow, but it could be clearer). The check
  passes when nothing is blocking; the minor issues stay listed for you to
  read (the Review tab shows them) and are never repaired. When something
  is blocking, the step repairs it itself before asking you: the scenes
  named are rewritten with the fix as the note (an unintroduced character or
  an unseen object also rewrites the earlier scene where it can be
  introduced or shown, and an unseen object that is one of the story's props
  is listed on the scene so its keyframe shows it — an object that is not a
  prop of the story is never added to the library), then the checks run
  again — at most two passes of eight rewrites, on the free writing chain.
  The check after a pass is a re-check: it is shown what the pass tried to
  fix and keeps blocking only what is still there, so each pass can only
  shrink the list. The writers also hear the first-watch rules before they
  write. Only what is left after that reaches you ("after 2 repair passes, 1
  blocking issue remains"). The script is not approvable while the check is
  missing or out of date (a check made by the previous version of the judge
  counts as out of date until the script is approved); with blocking issues,
  only with **Approve anyway**.
- *The length.* A v2 episode must land in its 55–75 s window: the script and
  the storyboard are refused outside it (estimated), the assets and the
  render too (measured) — there is no "anyway". A script that comes out short
  gets a fill pass (at most two rewrites of its shortest scenes) when the
  script step runs.
- *The keyframes.* The assets step with **animate off** makes every keyframe
  and voice. Each keyframe is drawn with the previous keyframe of its scene
  among its references (set, light and positions carry over) and with the
  character's outfit of that moment named — the sheets show the first
  wardrobe set, the text says which set is worn now. Then a free vision
  check (J2) looks at each keyframe next to the previous one and the
  characters' sheets and says whether it shows its beat, what is missing,
  whether its framing is the one planned, and what changed that should not
  have (across a scene change it compares only who the characters are). A
  flagged keyframe is redrawn by the step itself with a correction taken
  from the verdict, ending with the shot's own framing as an order ("Frame
  this as tight close-up on the face, nothing wider."), and checked again — up
  to two redraws a shot and $0.40 an episode on the Quality profile, counted
  in the estimate — so most never reach you. The storyboard's **Keyframes**
  card lists each verdict and what was fixed; **Approve keyframes** (or
  **Approve anyway** after a refusal) is the gate: until it is approved and
  current, no clip is bought, and a changed keyframe makes it stale.
  Regenerating one shot image by hand runs the check again on it and on the
  shot after it. Then run the assets step with animate on: it buys the clips.
- *Generate episode* (the one click, "Fast track" below) follows the same
  rules for the script — it stops at one it cannot approve and never
  approves outside the window — except once the script step's two repair
  passes are spent: then it approves the script anyway over the blocking
  issues they could not fix and names them on the **Review** tab (tick "stop
  at the script" to keep the stop). The keyframes likewise: once they
  are made, checked and auto-fixed it approves them for you (your click is
  the consent, the confirm says so; a shot still flagged is named), buys the
  clips, approves the assets, renders and ends "ready for review" on the
  **Review** tab. Tick "stop at the keyframes" in the confirm to keep the
  stop — approve them on the Review tab, then **Continue**.

**Editing what it writes.** On a v2 story the Cast step edits each
character's dossier and look, Places & props each place's layout and light
and each prop's look, and the Knowledge base step edits the world, the
timeline's beats, the props registry and the starting state inline (each
edit makes the base "Approve again"). An edited look makes that entity's
prompts outdated, as a descriptor edit does; its sheets stay until you
regenerate them.

**Advice on weak hosts.** Settings' hardware card, on a host with no GPU,
recommends the Quality preset with its price worked out from the price
table — today ≈ $3.52 an episode (8 shots animated, with their own sound)
and ≈ $0.56 once per story — and the keys to add; the new-story form shows
the same numbers.

**What it costs (720p).** Per episode ≈ $3.52: about 8 keyframes at $0.04
on fal and about 60 s of Veo 3.1 lite at $0.05/s with its own sound (clips of
8 s at most — a fully animated storyboard plans no shot longer than its link
sells), plus rounding each clip up to whole seconds and up to $0.40 of
keyframe redraws — inside the default $4 episode cap. Without
`GEMINI_PAID_API_KEY` the clips go to seedance (silent, $0.022/s, ≈ $1.73 an
episode) and the estimate says "No ambience". The lipsync of the clips with
an on-screen line adds about $0.15–0.30 (see "Lips follow the voices"
below): with Veo, the up-to-$0.40 redraw ceiling and the lipsync the paid
check reaches about $4.1, over the default $4 episode cap -- raise
`per_episode_cap_usd` (to $5, say) or turn the story's lipsync off. Once per
story: sheets, plates and props at $0.04 each (≈ $0.5–1.0 for a small cast).
Every paid step shows its estimate before it runs and is refused whole when
it would go over a cap. Settings → **Allow paid** must be on, and caps saved earlier in
Settings (1 / 3 / 10 or 2 / 6 / 20 before this) win over the new defaults
until you change them.

**Sound.** On the Quality preset (tier 3, `tier3_native_audio: ambience`)
every clip is bought on a link whose clips carry sound (Veo) and asked, inside
its prompt, for the place's ambience and the shot's effects — and for no
voice, music or narration. In the mix that sound sits under the lines,
ducked like the music bed: every line is still spoken by its character's
pinned voice, the same in every shot. The burst of static Gemini voices add
after the last word ("crshhh") is cut and faded when a line is recorded —
the real burst, read from a live episode, is a short buzz louder than the
speech itself after a moment of silence, running to the file's end, and the
guard's second version looks for exactly that; lines recorded before, or
cleaned by the first version, are cleaned on the next assets run without
being spoken again (`voice-tails` shows what was cut), and every line fades
at its edges in the mix.

**Lips follow the voices.** On the Quality preset every bought clip whose
shot holds a line spoken by a character **in its frame** is sent once more,
with a dialogue track, to Kling LipSync on fal (`fal/kling-lipsync`,
`LIPSYNC_CHAIN`): the characters' mouths then move on your TTS voices. The
track is a silent 24 kHz WAV exactly as long as the clip, with each of those
lines at the moment the episode places it in the shot; a line said by
someone off screen is left out (it would move the wrong mouth), and a shot
with no such line keeps its plain clip. The lip-synced take is kept beside
the clip as `assets/clips/shot_NN.lipsync.mp4` with the clip's own sound
(never the track: your voices stay the only voices), and it is what the
render uses; if a lipsync fails, the plain clip is kept and the shot is
named in the feed ("✖ Lip-sync sh04 failed: …"). If Kling finds no face in a
clip (a fruit head is not always a face to it), that clip is never sent
again: the plain clip is the take, the feed says so once ("👄 Shot sh08: no
face for the lip-sync …"), the refusal costs nothing (its booking is given
back), and the next estimate no longer counts it — only a new clip of that
shot is tried again. It costs $0.014 per started
5 s of clip -- $0.014 for a clip of 5 s or less, $0.028 for 6-10 s, about
$0.15-0.30 an episode -- shown in the estimate ("+ $0.280 lip-sync (8
clips)") and counted against the caps; each one takes about a minute. A
lipsyncing story buys no clip longer than 10 s (Kling's limit), so on
seedance a scene past 10 s is planned as two shots. A re-voiced or re-timed
line, or a new clip, is lipsynced again on the next assets run; nothing else
is bought twice. To turn it off for one story, patch its profile:
`{"generation_profile": {"lipsync": "none"}}` (`"kling"` turns it back on).

**Prompt size limits, and richer prompts within them.** Each link's prompt
limit is known (`prompt-limits` lists them with their source) and a prompt
over it is refused before anything is sent — the chain moves on to its next
link. The free key check also reads the limit fal publishes for each video
model and keeps it. Within those limits every v2 prompt fills its own
link's budget (a keyframe up to 320 words on the quality links, a clip up to
160, 220 with its ambience brief; sheets 200, plates 220, props 120) with
the story's richest context, dropped least valuable first when room runs
out: the beat's mood and who is mid-sentence (never the words), what
changed since the previous shot of the scene, the character's bearing and
distinctive marks (the look's new `bearing` field, written by the Cast
step), the time of day; a clip adds the emotion, micro-actions and the
camera's intent. A shot whose prompt cannot fit its link is refused when the
storyboard is written, naming the shot and the link, never sent trimmed.
When a prompt is asked with a note at its tail — the keyframe check's
correction on a redraw, your own on a regenerate — and the two together run
over the link's budget, the prompt is resolved again to the room the note
leaves: the lowest context layers go first, the note is sent whole, and the
feed says from and to how many words ("ℹ️ Shot sh03's keyframe prompt: its
note (18 words) takes it to 326 words, over fal/seedream-4.5-edit's budget
of 320; resolved again to 302 words"). A prompt built to a roomier link is
fitted the same way. Only when even the shortest form cannot fit with the
note is the shot refused, and the message then says how long a note fits.

**Moving a written story to v2.** **Animate every shot** on a story whose
episode already has a script offers **Regenerate episode N on v2**: the
episode's script, storyboard, images, clips and render are archived
(`episodes/_discarded/`, recoverable by hand), its series memory and feedback
cleared, and its spend no longer counted against the new episode's cap (it
stays in the story's total); then the Cast step starts — dossiers, looks and
the images drawn again from them — followed by Places & props, the knowledge
base and the episode, each with its estimate first.

**Checking the video keys.** Settings → Video → **Ask the providers (free)**
asks fal (its pricing for the model's endpoint) and Gemini (`models.get` for
Veo) whether each key is accepted and each model live, and shows fal's live
price. Nothing is generated or billed; the chain test itself still never
calls a hosted video link.

**A render is checked before it is published.** After the audio mix, the mix
and its stems are measured against the episode's length; after the final
pass, its frames are counted against the timeline. A music bed cut short or a
final missing frames fails the render (render again; cached shots are
reused) and the last good final stays in place.

## Native speech (API route)

The **Native speech (Veo)** budget profile (`native_speech`; v2, tier 3) gives
each character's spoken line its own clip instead of a silent clip whose
mouth is guessed and relipped afterward. The storyboard plans one `speaks`
shot per line — the speaker in frame, the listener reacting beside them —
sized to the shortest sold length (4, 6 or 8 s) that holds the line's words;
a narrator line, or a reaction with nobody speaking, stays a silent shot on
the cheaper Lite link. Each speaking shot's prompt follows Google's own Veo
syntax in one sentence: who looks at whom and says the line, in which voice,
over the place's camera and style, closing with "Audio: only {speaker}'s
voice speaking {language}… no music, no narrator, no other voice. No
subtitles, no captions, no on-screen text." — the quoted line and that audio
sentence are never dropped.

No text-to-speech and no lipsync run for an on-screen line: Veo's own clip is
the voice and the lips together, one take. The narrator, when the story has
one, stays exactly as before — a voice-over read by its own TTS voice, never
a shot of its own. Subtitles are never guessed from the written line: the
clip's own soundtrack is transcribed and aligned word by word (the **native
take**), so the burned-in words follow what the clip actually says, and the
shot's final length follows the take, trimmed to the last word plus 0.3 s.

The per-story **speaking-clips model** (the generation profile's
`speech_model`, a select on the story's Visual tier card and the new-story
wizard) picks which link speaks: **Lite** (Veo 3.1 Lite, the cheapest,
unproven for speech until the probe), **Fast** (Veo 3.1 Fast, the default)
or **Premium** (the full Veo 3.1). Silent shots always use Lite regardless.
Each choice's estimate shows the split before anything runs: speaking
seconds × its link's price, plus silent seconds × Lite's, plus a small
per-shot retake contingency (one free retake of a mismatched take, bounded
by its own small cap) and the premium text cost.

**A fourth speaking model, not yet.** `fal/ltx-2.5-fast` (open weights,
native audio; the last link of the video chain, "Costs and providers") could
speak as well, but it is not offered as a speaking-clips model, and
`speech_model` has no `ltx` value. It is not cheaper for speech: its
shortest clip is 6 s where Veo sells 4 s, so a nine-line episode is about
$5.46 of clips against about $4.60 on Veo Fast, at $0.09 a second. And
nobody has heard it speak French. Before it becomes a speaking model, a
one-clip probe (about $0.54, run only on your go) has to show that at least
80 % of the line's words come back in the take and that it sounds right to
you; that probe has not been run. Its value would be a second speaking
provider that is not Google, and clips of 10 s for a long line.

**Why it is refused, and how to allow it.** Caps are global Settings →
Budget numbers (per-episode / daily / per-story); this host's are set to
**$2 / $4 / $10**. Every native-speech API price is well over the $2
per-episode cap (Lite ≈ $3.4, Fast ≈ $5.4, Premium ≈ $17.4, for a 50 s
episode), so the step refuses before buying anything: *"estimated $5.40 over
the per-episode cap $2.00; raise PER_EPISODE_CAP_USD or use your own
clips."* To run one story on the automated route anyway, raise
`per_episode_cap_usd` in Settings before that story's assets step (caps are
global, so set it back afterwards if you don't want every story reaching
that high) — or switch the story to the **manual mode** below, which never
buys a clip at all.

## Your own clips (the manual mode)

A hosted clip able to speak costs four to eight times what a Google Flow or
Higgsfield credit does at subscription prices, and the people making videos
like the reference performance this plan was built against already pick
their takes by eye rather than trust the first one a model renders. The
**native speech — your own clips** profile (`native_speech_manual`) makes
the human the clip provider: the app still writes the concept, bible, cast,
places, script and storyboard, draws every keyframe, times the subtitles and
renders the final video — only the clip itself comes from your own
subscription. It is the **default for a new story** on a host whose quality
keys (`FAL_KEY`) are set; pick a different budget profile on the new-story
form, or the Visual tier card, if you'd rather not.

**The walkthrough.**

1. Start a new story on the default profile (or switch an existing one to
   **"Native speech — your own clips (Flow / Higgsfield)"** on its Visual
   tier card). Concepts, bible, cast and places & props work exactly as
   described above.
2. Write the episode's script, then its storyboard, as usual.
3. Run **Generate assets**. The app makes every keyframe and voices the
   narrator's lines, then stops: with no clip to buy, the step ends
   **"Waiting for N clips — download the brief"** rather than failing — the
   Generate button itself reads this once a storyboard exists.
4. Open the episode's **Shot list** tab. Per shot it shows: its **purpose**
   (one sentence — what the shot must show, so you can judge a take by eye),
   the **prompt** with a copy button, the **reference images** (the
   keyframe, the speaker's and listener's sheets, the place's plate — each
   downloadable), the **line** in the story's language with its character's
   **voice** direction, the **length to pick**, and the **checks** to make
   before uploading. A platform select switches every prompt between Flow's
   and Higgsfield's own phrasing; **"Download brief (zip)"** saves the
   `.md`, the `.json` and every reference image in one file.
5. On **Google Flow**: set the project to **9:16** (16:9 on a landscape story), use **Frames to Video**
   with the shot's keyframe as the first frame when one exists (else
   **Ingredients to Video** with the character and place sheets), paste the
   prompt, and generate on **Veo 3.1 Fast** (20 Flow credits a clip on AI
   Pro, 10 on Ultra). Download the take you like as an MP4 — Flow always
   renders 8 s; the app keeps the clip's real length and trims a speaking
   shot to 0.3 s past its last word.
6. Upload the file on the shot's slot (or `aistory upload-clip <story> <ep>
   <shot_id> <file>`). The upload is checked before it is stored and
   refused, with the reason, rather than silently cropped or accepted: not
   an **MP4 or MOV** ("the clip is not an MP4 (or MOV): download the take
   from the platform as MP4 and send that"), shorter than **2 s**, not
   **the story's frame within 2 %** (9:16, or 16:9 on a 16:9 story — the render would crop any other shape, cutting the
   characters out of frame — refused instead of cropped), or a speaking
   shot's clip with **no sound track**.
7. Once stored, the clip is **taken** for free: its audio is transcribed and
   aligned against the line, and the shot badges the result — **"🗣 matched
   92 %"** when most of the line was heard and ends in time, a **mismatch**
   badge when it wasn't (one retake is yours to try, nothing stops the run
   otherwise), or **"approximate"** when no speech-to-text key is set to
   check it at all (the subtitles still show, evenly split over the clip;
   set a **Groq** — free — or **Mistral** key in Settings for a checked take
   instead).
8. The run **resumes by itself**: the upload that leaves nothing missing
   restarts the paused assets step as a new job, with nothing already made
   bought or built again. When every shot and every line is in place,
   approve the assets and render exactly as any other episode.

**Higgsfield / Freepik.** The same brief, rephrased for Higgsfield's own
reference syntax, offers **Veo 3.1**, **Seedance 2.0** or **Kling 3.0**. For
a French story (or any language but English and Chinese) pick **Veo 3.1 or
Seedance 2.0** — **Kling 3.0 speaks English and Chinese only** and is named
by the preset's own notes as the wrong pick for anything else.

**Your own images too.** A manual-mode story can also make its **cast
sheets, place plates, props and keyframes** by hand — a checkbox on the
Visual tier card ("My own images too"), the per-story `images: "manual"`
switch. Each tile (a character's sheet, a place's plate, a prop's image)
then carries an upload slot the same way a shot's clip does, and `GET
/api/stories/{id}/image-brief` (or `ep=` for one episode's keyframes) lists
every one still missing, with its own prompt, size and upload slot — the
same brief the Shot list reads from for clips.

**The CLI.** `aistory brief STORY_ID EP [--platform flow|higgsfield] [--zip
PATH]` prints (or zips) the shot brief without calling anything; `aistory
upload-clip STORY_ID EP SHOT_ID FILE` uploads one clip through the same
checks the API route runs, then prints the take's result and what is still
missing.

## Walkthrough (dashboard)

Open **AI Story** in the mode switch, or go to `/story`. It lists your
stories as cards (title, status, style, language); **New story** starts one.

### The dashboard

- **Stories list** — one cover card per story (its lead's portrait, else the
  style's colours), the steps done and the next one, the latest episode.
- **Workspace** (`/story/<id>/<step>`) — one step per screen under a sticky
  header (cover, title, language, style, the Visual tier popover, and the one
  next action: the step to do, or *Open episode N* once the story is ready).
- **Step rail** — the seven steps down the left (a strip above the step on a
  narrow screen): done, to do or locked (hover for why), a spinner on the step
  whose job runs. Cast and Places are tile grids; a tile opens its editor.
- **Episode stepper** — on `/story/<id>/episodes/<n>`, Script → Storyboard →
  Keyframes → Clips → Render → Review, each done, active or stale (a warning
  mark); a click jumps to that part. *Generate episode* runs what is left.
- **Review hero** — the rendered episode beside an approvals checklist (who
  approved what, when, which shots are still flagged) and the spend.
- **Activity feed** — a running job's log grouped by step, errors and warnings
  tinted and opened, *Copy log*, *Jump to latest*. On a phone the menu button
  in the top bar opens the navigation.
- **Agent run** — on a story created in Agent mode, a card above the rail with
  one button, *Run the agent*, that shows the summed estimate and the caps
  before it runs. While it runs the card and the rail follow the job's part
  ("Agent 4/9: cast"); a stop shows the runner's last line and offers to
  *Continue the agent run*, and episode 1 rendered links to its Review tab.

### 1. New story

- **Language** — `Français` or `English`. Required: nothing is picked for
  you, so a story is never silently written in the wrong language.
- **Seed text** (optional) — a rough idea, a scene, a vibe; up to 2000
  characters. It seeds the concepts, nothing more.
- **Style** (optional) — pick one of the eight shipped templates now, or
  "Decide later" and pick it at step 4. The eight: Fruit Drama, Viral 3D,
  3D Animated Family Film, Anime/Manga, Realistic Cinematic, 2D Cartoon/Flat,
  Storybook Watercolor, Claymation/Stop-motion.
- **Episode format** — the episode template the story is written on (see
  "Episode formats" below). It follows the style's suggestion when the
  suggestion fits the pipeline (Fruit Drama suggests the narrated drama on
  v2), else the pipeline's default; a one-line hint says what each format
  is. Change it here, or on the episode page until an episode has a script.
- **Generation profile** (collapsible; it starts from what the server would
  give a new story — the quality pipeline when `FAL_KEY` is set — and is sent
  only if you change it): pipeline (`v2` quality / legacy), tier (`1` =
  stills with motion; `2` animates shots into image-to-video clips; `3`
  keeps a kept clip's own native audio as an opt-in on top of tier 2 — see
  "Tier 2/3: animating shots" below), route (`auto` / `local` / `api` —
  governs video as well as images: `local` is your own ComfyUI, `api` the
  hosted video links), consistency mode (`references` / `prompt_only`; a v2
  story is always `references`), budget profile (`free` buys no clip,
  `one_dollar` animates key shots, `quality` — "Quality (billed APIs)" —
  animates every shot). A line above it says whether the story will be fully
  animated, and why not.

**What else the form decides (v2 stories).** Six optional keys of the
generation profile carry the choices below. Each one left out leaves the
story exactly as it was before the key existed, and only a v2 story reads
them. The wizard has a select for each but `variants` (the universe's list
follows the style), the story's Visual tier card shows them and changes all
but the universe and `variants`, and they can be patched with `PATCH
/api/stories/{id}` (`{"generation_profile": {...}}`); a key is cleared by
patching `null`.

| Key | Values | What it does |
|---|---|---|
| `universe` | an id of `GET /api/stories/universes` | what the cast is made of |
| `sheet_mode` | `three_sheet` (default), `two_view`, `two_view_expressions` | how a character's sheets are drawn |
| `body_rule` | `human_body` (default), `all_matter` | whether the bodies are the character's own matter |
| `image_preference` | `gemini_first` | which provider the images try first |
| `prompt_style` | `studio` (default), `action` | how a clip's prompt is written |
| `variants` | `on` | lets characters carry appearance variants without choosing a sheet mode |

**Universes.** A universe says what a story's cast is made of. Ten ship in
`templates/universes.json`, each with a French and an English label and a
pool of generic species (a cola can, a smartphone, a chocolate bar, a
burger, a wine bottle, a light bulb...): fruits, vegetables, drinks and
sodas, tech gadgets, snacks and sweets, fast food, bottles, household
objects, melting materials, and gross and funny. Bottles and gross-and-funny
carry an audience note (adult audience and no drinking shown as a reward;
cartoon-clean, no bodily fluids). The Viral 3D style takes all ten; Fruit
Drama takes fruits and vegetables only; the other styles take none, and a
universe that does not fit the style is refused when the story is made or
patched, naming both. The universe enters the writing only when you chose
it: the concepts' prompt then carries the species pool and a lead species
for each card, assigned in a fixed order that depends on the story and the
batch, so ten cards open on ten different species while the pool allows, and
a character's species is a field of the card. The style lock records the
universe when you lock the style. A story that never chose one (Fruit
Drama's default, fruits, only pre-selects the form) is written exactly as
before. No generated text may name a brand once a universe is chosen: a
reply that says "Coca", "iPhone", "Lego" and so on (the list lives in
`schemas.BRAND_DENYLIST`; "monster" and "sprite" count only beside a drink
word) is asked again with the brand named and told to write the generic
thing instead.

**Character sheets.** The default is three images per character: a portrait,
a turnaround and an expressions sheet, about $0.12 a character at Seedream's
$0.04 an image. `two_view` draws one 9:16 image (1080×1920 where the link
allows) with the front on the left half and the back on the right, head to
toe and never cut across the middle line, about $0.04 a character;
`two_view_expressions` adds an expressions sheet, about $0.08. The sheet is
stored as the character's portrait, so keyframes, the first-watch check and
the shot brief need no change, and a keyframe's prompt says the image shows
the character twice and to draw it once. The wizard shows each mode's price
from the estimate. Regenerate a character's images to draw them again in
another mode; a story that is not v2 keeps three sheets whatever the key
says.

**Bodies.** `all_matter` replaces the style's character-design rules, once,
when you approve and lock the style, with a rule saying the whole body,
hands and legs included, is made of the character's own matter and no human
skin shows anywhere (the matter comes from the universe, else from the
style: for Fruit Drama, the character's own fruit or vegetable flesh). Only
a style that defines the rule can be approved with it (Fruit Drama and Viral
3D; Viral 3D has it by default), and a style that is already locked is never
touched.

**Image provider.** `gemini_first` makes every image role of the story try
its Gemini link before its fal link (nothing is removed, so a link an
episode already uses stays valid). It needs `GEMINI_PAID_API_KEY`: the
select is disabled with the hint while the key is missing. See "Costs and
providers" for the links and their prices.

**Clip prompts.** `studio` is the layered prompt every story has had.
`action` writes the clip prompt as one continuous physical action in the
present tense (what the shot's motion does), naming every character at every
mention by an anchor built from its look (its colours, whether it is a
fruit, a can, a gadget, its name, its first outfit item: "the green-yellow
female strawberry character in a dirty burlap dress"), the place once in ten
words at most, the sounds in the sentence, and exactly one camera phrase;
the quoted line and the closing "Audio: only … no other voice" sentences are
never cut, and over the link's budget the sounds go first, then the
reaction, the place, the listener, the action and last the camera. **A
clip's prompt is part of what makes it current, so changing the style makes
every clip already made, uploads included, stale.** The patch answers with
the count and a warning ("Switching the clip prompts to action rewrites
every clip's prompt: 6 current clips (uploads included) will be marked stale
and must be made again.") and the card shows it as a toast. Native audio
kept on a clip keeps the studio prompt.

**Appearance variants.** A character may carry up to three named variants of
its look, such as a "ghost version": a label (40 characters at most) and
what changes in the look (60 words at most, no name). Variants exist on a v2
story that has chosen a sheet mode, or with `variants: "on"`; any other
story is untouched, down to the byte. In Cast, each character has **Add
variant**; **Make sheets** then draws the variant's sheets as edits of the
character's base portrait, one per sheet of the story's mode, priced like
the sheets ("N variant sheets" in the estimate), and **Approve variant**
approves them on their own: the character's own approval is never reopened.
A variant's id is a slug of its label, fixed when it is added. In an
episode, a scene can say which variant each character wears and every shot
inherits it; a shot card has a look select to override it for one shot (the
shot's keyframe is then stale, on purpose). A shot that names a variant that
is not approved refuses to make its keyframe, with one sentence. The
variant's sheet is the identity image of its keyframes, the delta is said
after the look (and in the action anchor under `action`), and the keyframe
check and the shot brief use the variant's sheet. The script's writing
prompt offers the states only when the cast has an approved variant, and the
next-episode proposals may bring a twist as a variant: accepting it creates
the record and queues its sheets behind the usual estimate. Adding or
approving a variant marks the storyboards that have the character as
outdated; their prompts refresh, and a shot without a variant is byte for
byte what it was. On a story whose images are your own, the image brief
lists each variant's sheets after the character's own ("Kiwilo (Ghost
version) — character sheet (portrait)"), with the prompt the app would send,
the base portrait to edit as the reference, and the upload slot (the sheet
route with `&variant=<id>`); the variant's row in the cast step has an upload
tile per sheet. Uploading a variant sheet clears that variant's approval and
never touches the character's own sheets.

**Frame (16:9 and 1:1).** The generation profile's **Frame** select picks
the story's output frame: Vertical 9:16 (the default, and every story made
before), Landscape 16:9 or Square 1:1 (`generation_profile.aspect`; sent on
creation, absent = 9:16). It is **chosen when the story is made and never
changes after**: its plates, keyframes and clips are all made at it, so
`PATCH` and the pipeline switch answer 409 "the frame is chosen when the story
is made" (make a new story for another frame), and an existing story is never
converted. The page shows it read-only on the profile card.

What a frame changes: the plates and keyframes are asked at the frame and cut
to its exact even size; each clip says it to its link; the render, the cover
and the subtitles follow it (see "11. Render"); a manual story's clips and
keyframes must match it (see "Your own clips"); and the metadata pack records
it with a note ("upload as a regular YouTube video, not Shorts" for 16:9; a
YouTube landscape entry is not written yet). A 9:16 story is byte for byte
what it was.

**Which link makes which frame.** A link that cannot make the story's frame is
skipped by the clip estimate, with the reason, and refused before anything is
sent.

| Link | 9:16 | 16:9 | 1:1 |
|---|---|---|---|
| `fal/seedance-1-pro-fast`, `fal/kling-2.5-turbo-std` | yes | yes | yes |
| `fal/ltx-2.3-fast`, `fal/ltx-2.5-fast` | yes | yes | no |
| Veo 3.1 (`lite`, `fast`, standard) | yes | yes | no |
| Your own clips: Google Flow, Higgsfield | yes | yes | no |
| Local ComfyUI clips | yes | no | no |

(Kling follows its keyframe's shape; seedance and LTX take `aspect_ratio`, Veo
`aspectRatio`.) The form disables a frame a profile cannot make, with the
server's own reason, and creation refuses it with a 400 for the same cases:

- 16:9 and 1:1 need the **v2 pipeline** (a legacy story stays 9:16);
- the `local` route, and the `free` profile at tier 2 or above (it animates on
  a local ComfyUI only), take **9:16 only**;
- **1:1** is refused with native speech on Veo, and with your own Flow clips;
  a 1:1 story is a Tier 1 story, or one whose clips are on Seedance or Kling;
- a story that is made at 16:9 or 1:1 cannot later be moved off v2.

**Still 9:16 only in v1:** the character sheets (they are references, not
output), the style preview, local ComfyUI clips, the tier-2 render golden,
Clips mode (the other mode of the app), and every story that already exists.

**Episode formats.** Five templates ship, each a story-level choice
(`episode_template_id`, sent on creation or patched while no episode has a
script): `serial_60s_v1` (60 s, 55–80) and `serial_90s_v1` (90 s, 75–100)
for the legacy pipeline; `serial_60s_v2` (6–10 beat shots in 55–75 s, the
v2 default), `serial_90s_v2` (the same shape at 80–100 s, target 90) and
`narrated_drama_60s_v2` (58–78 s, 5–7 longer scenes) for v2. The narrated
drama is told by one narrator in a telenovela tone: its template carries the
narrator's share of the words (60–85 %) and 2–4 short character lines an
episode — the only lines in frame, so the only ones lip-synced — and, with
the story's narrator on, the beat sheet and each body scene's dialogue are
asked for exactly that. A style only suggests a format; the story keeps its
own, and an episode keeps the one it was written against.

**Writing v3 and the confrontation format.** Every story created since
plan 22 writes on "writing v3": the beat sheet opens with the episode's
**spine** — a one-sentence logline (who wants what, what they do, where it
leaves them), the want, the obstacle, the stakes and the turn — and each
scene's summary is one or two complete sentences saying what happens and
why. Each body scene's dialogue is written with the whole episode's lines so
far in view, and every spoken line is one or two complete sentences that do
one job (a demand, an accusation, a fact, a refusal, a threat, a reveal),
with the reason inside the line; a fragment, a lone name or a repeat is
refused and rewritten. The first-watch judge checks the same things (a line
that adds nothing, an incomplete sentence, scenes that do not tell the
logline), and the script pane shows the spine as "What happens" above the
scenes. The sixth template, `confrontation_50s_v2`, is a continuous,
one-place, real-time confrontation of about 50 seconds (4–6 scenes, 9–16
shots of 2–8 s, 95–125 spoken words, lines of 5–17 words, every boundary a
cut, the narrator only in a later episode's recap, the cliffhanger's last
line stating the act about to happen). It is the format a new story on a
native-speech profile starts on — preselected in the wizard, still a choice
— because one shot per line only fits a script written for it. A story
whose episodes began on the older prompts keeps them; an older story opts
in through its generation profile's `writing: v3`.

**Create story** opens the story page, a vertical stepper: New story (done),
Concepts, Bible, Style. Each step unlocks once the one before it is
approved; a locked step shows why ("Approve the bible first.").

### 2. Concepts

Two sources: the **library** (the fourteen shipped concepts, filterable by
style) and **Generated** (anything you've asked for). Filtered on the Fruit
Drama style it shows seven: *Tentafruit Island* (a rigged reality-show
contest), *The Orchard Inheritance*, *Midnight Fridge*, and the four of the
fruit-drama pack, each a fruit kingdom on its own plot archetype — *The
Citrus Ball* (infidelity), *The Pineapple Crown* (betrayal), *Seeds of the
Past* (forgiveness) and *The Kitchen Heir* (a secret child). Each card shows a
title, logline and "value" (the substance the story carries) up front;
"Details" expands the world, cast sketch, hook formula and retention
mechanics. **Pick this concept** chooses it — no separate approval step; the
choice *is* the approval, and it also completes any concepts job left
awaiting approval.

**Generate 10 more** queues a job of ten LLM calls, one concept each, each
told not to repeat a title the story already has (library titles first,
then every one it has generated; up to 24 titles, so a first run never
drops one). The estimate chip reads something like
`est. $0.00 · 10 LLM calls`; the route chip next to it names where they'll
run (see "Estimate and route chips", below). Cards land on disk as each
call returns, so a couple of failed calls out of the ten still leave you
cards to pick from — the activity feed shows which calls failed and why.

Picking a different concept than before clears the bible (a bible written
for one concept does not carry over to another).

**Keeping to your idea.** With a seed typed in step 1, every generated card
is written against it as a **binding brief**: the same named characters
(name, role, relationships), the same setting, premise, central conflict,
genre and tone as the brief gives them — the ten calls differ only in which
angle of the brief each one leads with (played straight, opened on its first
confrontation, from the antagonist's want, a ticking clock, and so on), never
in the premise itself. Each card is rule-checked as it is written (a named
entity missing from its title, logline, world or cast is refused and asked
again) and then judged by a second model call; the card shows **"✓ Kept to
your brief"**, or **"⚠ Drifted: …"** naming what the brief gave that the card
dropped, when even a second try could not fix it — never silently. In **agent
mode** a drifted card stops the run with "The concept drifted from your
brief: …" rather than carrying on with it. A seed is capped at 400 words for
this check (the full 2000-character seed from step 1 is still kept and
shown; only this much of it is read back to the writer and the judge).

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

Pick a **style template** (the same eight), then tweak:

- **Primary palette** / **Accent palette** — colour swatches, add/remove.
- **Font family**, **Highlight colour**, **Subtitle mode** (`word_pop` /
  `two_line` / `none`).
- **AI label** — the on-screen "AI-generated" disclosure, on by default.
- **Subtitle look** — size, position, colours, outline and a box behind the
  text, saved on its own and editable at any time, the lock included (see
  "11. Render").
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
keeps whatever it already finished — see "Troubleshooting" below. A free
tier that pushes back (Pollinations' image limit, Gemini TTS's per-minute
quota) is paced the same way the assets step paces an episode's shots: the
run keeps going in further rounds, a minute's pause before each, for
whatever was held back, instead of failing the character outright.

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

#### Your own voice (chatterbox)

A character can speak with a voice you give it: your own, or a friend's who
agreed. Each character card has a **Voice recording** slot under its voice.
It takes **5 to 30 seconds** of one voice speaking clearly (WAV, MP3, M4A,
OGG or FLAC), up to **10 MB**, and a **consent box** must be ticked first:
"This is my voice, or I have the speaker's permission to use it." Without
it the upload is refused before a byte is read. The box is your statement,
not something the app can check; the story keeps it with the recording
(`voice_reference: {name, sha256, duration_s, uploaded_at, consent: true}`).

The file is untrusted: it is re-encoded on upload to `voice_reference.wav`
(mono, 24 kHz, 16-bit, no metadata, no original file name) at the root of
the character's folder, and a file that has no audio track, is not 5 to 30
seconds long or is over the size limit is refused with the reason. The same
upload over the API is `POST /api/stories/{id}/characters/{char_id}/voice-reference?consent=true`
(multipart, field `file`; see `docs/api.md`). A new recording replaces the
old one; if the old one was the pinned voice, the character's voice sample
and the cast approval go with it (the sample was spoken with the old file).

**Use as voice (chatterbox)** pins the recording as the character's voice
(`chatterbox/reference`), through the same voice regenerate as any other
pick, and the character's voice sample is spoken again with it. From then on
every line of the character is spoken by the local chatterbox engine from the
recording. Nothing is sent to a provider and nothing is billed, but:

- **chatterbox must be installed.** The default image does not have it:
  build with `INSTALL_LOCAL_TTS=1` (`docker compose build --build-arg
  INSTALL_LOCAL_TTS=1`, or set it in the shell or `.env` compose reads), which
  adds the `[local-tts]` extra — about **2 GB** of torch and the engines. Until
  then the **Use as voice** button stays disabled and says why (the engine
  probe's own sentence). Uploading the recording itself works either way.
- **It is slow on this host.** On a 4-core ARM box with no GPU, cloning runs
  slower than real time: a minute of speech takes more than a minute. The cost
  is time, not money.
- chatterbox returns no word timings, so the subtitles of such lines are timed
  by estimate ("approximate", as for any voice without cues).
- A recording is the character's own: no other lead can take it, and it **cannot
  be removed while it is the pinned voice** (`DELETE` answers 409 — pin another
  voice first). Removing it afterwards deletes the file and the entry.
- **Lines already voiced keep their audio** when you upload a new recording or
  replace the old one: a measured line is re-voiced only by a **regenerate** of
  that line's voice (the generation cache follows the file's bytes, so a new
  recording speaks anew, an unchanged one is served from the cache).

An upload is refused with 409 while a step of the story is queued or running
(it may be speaking in this voice): add or remove the recording once that step
is done, or cancel it. This amends the old rule on references (DEC-281): a
photo is still a design reference for stylised characters only, and no hosted
voice-cloning service is used.

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

**Plot archetypes (v2 stories).** On a v2 story the skeleton is built on a
library of seven telenovela plot archetypes — infidelity, inheritance,
betrayal, forgiveness, secret child, rigged contest, reality-show parody —
each with one beat per arc function (the midpoint is always a reversal, the
crisis a cliffhanger-ready corner), three twists and a payoff, in French and
English. S1 picks the season's **primary** archetype and at most one
**secondary** that pairs well with it, and puts every episode on one of the
two (episode 1 and the finale on the primary); S2 is then told the beat its
episode plays. The choice is saved in `season.json` (`archetypes`, and each
entry's `archetype`), the log line names it ("plot archetypes: infidelity +
secret_child"), and the knowledge step and the episodes' series-memory block
mention it on one line. A legacy (v1) story's season is planned exactly as
before. The archetypes live in `clipping/aistory/templates/archetypes/`.

The timeline shows one card per episode: its number, its function badge,
its summary, any hooks in/out, the characters in it, and a **Regenerate**
(S2 again for that entry alone, with an optional note). **Re-plan** replaces
the whole arc from scratch (a confirm dialog warns this is a full
replacement, at the same episode count).

**Approve season** needs the places and props already approved and every
planned episode to carry a summary; approving makes the story **ready** —
the state phase 3's episode script picks up from.

**Series memory, audience feedback and next-episode proposals.** The
**"Series memory"** panel on this page is where the season keeps going past
episode 1. It fills in per episode, once that episode's own script is
approved:

- **Update memory** — a card per episode reads its script and writes one
  memory entry: a recap (at most 40 words), the hooks it opened and the ones
  it closed, and any relationship changes between characters, shown by name.
  It's one small model call, ending awaiting approval like any other step;
  **Approve memory** stamps it. Editing the script again afterwards — even
  just re-approving it — marks the entry **stale** (a banner, and the card
  greys out); write it again once the new script is settled. Episode N+1's
  script, storyboard and the fast track are refused until episode N's entry
  is both approved and fresh — the refusal names exactly which of the three
  is missing.
- **Audience feedback** — paste what viewers said about an episode (up to
  6,000 characters; the live counter follows what you typed, refused, never
  silently cut, if you go over) and press **Digest feedback**. One call reads
  it back as a short digest (at most 60 words) and three possible directions
  the next episode could take (at most 25 words each); pick one, or leave
  none picked. Only the *latest* direction you've picked steers anything, and
  only the next episode's opening (its E1 beat sheet and, if you use it,
  "Propose next episode") — it never rewrites anything already written, and
  it never reaches past one episode.
- **Propose next episode** — needs the episode before it to have its memory
  approved and fresh. One call proposes up to two new characters (recurring
  or guest by default — pick `lead` or `support` instead and the picker warns
  you that it will fold the cast's approval, same as adding one by hand) and
  up to two twists for a later episode, each with why it's suggested. Accept
  or reject each one — a confirm dialog says the decision is final. Accepting
  a character queues the same build the Cast step runs (text, portrait,
  turnaround, expressions, voice) for it alone; accepting a twist rewrites
  that episode's arc entry on the spot (the season keeps its approval — a
  twist is additive, not a replan) and keeps the old summary, in case you
  want to see what changed. Once every item is decided, the card reads
  **Approved**.

The recap, hooks and relationships read by episode N+1's script (and by
"Propose next episode") show only the hooks still open when N+1 starts and
the memory of episodes before it — never a later episode's own memory, and
never more of the season than the character reading it would plausibly
know.

### 8. Episode script

Unlocked once the story is **ready** — the story page's **Ready** card links
straight to **Open episode 1 →** (and lists any other episode already
started). An episode opens at its own three-pane page, `/story/<id>/episodes/<ep>`
(Script, Storyboard, Preview — see "The episode page" below).

**Episode length**, before any episode has a script: `60 s (55–80)`
(`serial_60s_v1`, target 60, tightens above 75), `90 s (75–100)`
(`serial_90s_v1`, target 85, tightens above 95), or one of the v2 formats
(see "Episode formats" under 1. New story). Once *any* episode of the
story has a script, the length is fixed for the whole story — the select
disables itself and says so.

**Write / Continue / Check again** is one button whose label follows the
episode's own state: `Write episode 1` with nothing yet, `Continue writing`
mid-run, `Check again` once everything is written and the check is out of
date — after an edit or a regenerate. `Check again` is a check-only run
(`params.check_only`, CLI `step ID script --ep N --check-only`): the
consistency check and, on a v2 story, the first-watch check on the script
exactly as it stands — nothing is written, filled or repaired, so your edit
is never rewritten; a script with a scene still unwritten is refused (409)
naming it. Approving a script whose check is out of date says "check it
again (run the script step with check only)". Its estimate chip reads `est. $0.00 · N LLM calls` — the
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

Under the hood, every scene and shot is timed to the nearest whole frame (at
30 fps) rather than to a fraction of a second — the number shown is rounded
the same way either way, but timing to a whole frame means an edit that only
moves a scene by a fraction of a frame moves nothing measurable after it, so
the shots that weren't touched keep the same render cache key. That's what
makes the partial re-render (see "Render", below) able to tell "changed"
from "unchanged" so precisely. A storyboard planned before this shipped
converts to whole frames automatically, the one time it's next fully
re-timed — most of its shots re-render once when that happens, then settle.

**Consistency** shows the E4 report under the duration bar: "Passed ✓", a
stale notice once the script has changed since the last check, or the list
of issues, each one linked to the scene it's about.

**Editing** — every scene's summary and on-screen text, and each line's
text, delivery, speaker and emotion, are editable inline, no job needed. A
line's chip shows its duration and whether it's `estimated` or `measured`,
with a ▶ to play a measured line back, and, once one has been recorded, its
**"Re-voice this line"** control — an optional note ("more urgent", "quieter")
that reaches the voice as a style direction (a provider that can't follow a
spoken direction — Edge, a local engine — records it without applying it;
the note and the take it produced are kept either way). Editing anything
marks the consistency report stale, and always clears the script's own
approval — but what happens to the **storyboard's** approval depends on the
edit: a **text-only** change (a line's words or delivery, or which hook a
scene pays off — same lines, same speakers, same emotions) keeps the
storyboard approval and every shot exactly as they were, re-timing the
scene in place the next time its lines are re-voiced. A **structural**
change — a speaker swapped, an emotion changed, a line added or removed —
clears the storyboard's approval too and calls for a re-plan, exactly as
editing the script always has.

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
and the CLI alike — until the previous episode's series memory is written,
approved and still fresh (step 13, in the Season arc panel). Once it is,
episode N ≥ 2 opens on a recap scene built from it, and E1 is shown the
season's still-open hooks (the oldest four) and asked to pay at least one
off on a body scene — the consistency check then verifies the payoff really
lands, alongside its other checks. If you picked an audience direction on
the previous episode's feedback (step 13), E1 also sees it as a steer, never
an instruction it must follow literally.

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
  the whole board again. Every scene it does not plan again keeps its shots
  exactly as they are, with their keyframes, clips, locks, notes and
  verdicts: re-planning three scenes never throws away the images and clips
  already bought for the other ones. A scene that is planned again gets new
  shots with new numbers (the next ones free — a number is never given out
  twice), so a shot's number is its name, not its place in the episode: the
  filmstrip and the render follow the script's order. Building the fast
  board again plans every scene again.

Shots are grouped by scene. Each shot's card has editable **framing** and
**camera motion** (closed-list selects — a motion the style fixes for that
function is refused, not silently overridden), **modifiers**
(`handheld`, `jitter_stopmotion`) and **keep still**, its **subject tags**
shown as name chips, its **action** (read with entity names filled in;
editing it shows the raw tag text with a hint listing the scene's own tags),
a **prompt accordion** (the resolved image and negative prompts, plus an
editable prompt override), **reference thumbnails** (the character/place/
prop images the shot would send), and its own **regenerate** — T1 re-plans
just that one shot, the rest of the scene held fixed, with an optional note
(the shot it makes is a new one, with a new number; every other shot keeps
its own and its image).
Between two shots of the same scene the transition is a fixed `cut`; at a
scene boundary it's an editable select (`cut`, `dissolve`, `fadeblack`,
`fadewhite`, `wipeleft`, `wiperight`, `slideup`). A shot made under
`prompt_only` consistency carries the same `consistency: prompt-only`
warning chip used everywhere else in the app for that mode.

Editing a shot has different consequences depending on what changed.
Swapping the **camera motion**, toggling a **modifier**, or changing the
**transition** keeps the shot's existing image and only changes what the
render does with it. Editing the **framing**, the **action**, or the
**prompt override** invalidates the image instead — the shot's card shows
"needs a new image" until you press its own regenerate, and the render (and
the re-render, below) refuses to run on a shot whose image has gone stale
this way, naming it, rather than quietly using the old picture. A framing
edit that would break one of the cross-scene rules (no two shots back to
back with the same framing, say) is refused outright — it never silently
moves a neighbouring shot to make room.

When the script changes after shots exist, the affected scenes are named in
a banner ("The script changed: re-plan scene s04.") and marked stale on
their own card; when only an entity's text or image changed underneath (not
the script itself), a second banner offers **"Refresh prompts"** instead of
a re-plan.

**Approve storyboard** needs an approved script, every scene planned and
current against it, and no outdated prompts — in that order, named by
whichever the button is still waiting on.

**Preview** is the render and metadata tab — see "11. Render" and "12.
Metadata pack" below.

### 10. Assets

Unlocked once the storyboard is approved. **Assets** sits at the bottom of
the same Storyboard tab, under the shots themselves — there is no separate
tab for it. An **"Align words"** checkbox (opt-in forced-alignment word
timings, through the STT chain, for a line whose voice timed no words of
its own; without it, such a line's on-screen words fall back to an even
split, labelled approximate) sits above **"Generate assets"**
("Generate remaining assets" once some exist already), with an estimate
chip (the paid part, the image and line counts) and a route chip in front
of it. The button is disabled with "Approve the storyboard first." until
the storyboard is approved.

Generating fills in, for every shot neither locked nor already current and
every line not yet voiced: the shot's image (`prompt_only` mode sends no
reference image; `references` mode edits from the shot's own reference
images, and — exactly as Cast and Places & props do — stops before any call
and asks when no editor can run), then the line's voice, through its
speaker's pinned voice alone, then the episode's SFX cues (resolved against
the style's own sound pack; a cue the pack does not carry is skipped and
reported, never a failure) and one BGM track (its mood follows the
episode's dominant emotion — the emotion with the most total scene
duration, ties going to whichever comes first — through the style's own
mood table, then a track is picked deterministically from the shipped
library). A free tier that pushes back (Pollinations' roughly
one-image-a-minute limit, Gemini TTS's own per-minute limit) is paced
automatically: everything it held back is asked again in rounds, a minute's
pause before each, until it succeeds or a round makes no more progress at
all. The run then ends **awaiting approval**, naming every shot and line
still missing and its regenerate target; running the step again ("Continue")
retries only what is still missing, buying nothing twice.

Back among the shots themselves, each shot's card now also shows its own
generated image (or "No image yet" / "Failed to load"), a state badge —
`no image` / `current` / `stale` / `locked · stale` / `failed` — the route
it was made on, a **"Lock this image"** checkbox (a locked image is never
remade by the step and cannot be regenerated until unlocked), and its own
regenerate: a fresh seed, an optional note.

**Approve assets** (its own card, a fingerprint chip reading `none` /
`current` / `stale`) needs every shot current or locked and every line
voiced. Approving stamps the fingerprint the render step checks against, so
a later change — a regenerated shot, a re-voiced line — makes the assets
stale again: regenerate what changed, or run the step again, then approve
again before rendering.

### Tier 2/3: animating shots

At tier ≥ 2, the same **Generate assets** run that fills in images and
voices also turns some or all of the episode's shots into short animated
clips — the last thing the step does, after every image, voice, SFX cue and
the BGM pick are in place. Three tiers:

- **Tier 1** — stills only, each with its own Ken Burns move at render time.
  No video call of any kind, ever.
- **Tier 2** — shots the planner picks (see below) are sent to a video model
  as **image-to-video**: the shot's own current image is always the one and
  only keyframe — a video model is never asked to invent a character from
  text — and the clip plays back with our own TTS dialogue, not whatever
  audio the model may have produced.
- **Tier 3** — the same clips, plus an opt-in per shot (`keep_native_audio`)
  to keep that one model's own audio track instead of discarding it. The
  shot's subtitles and every other shot's dialogue still come from our own
  TTS and line timing, so a kept track's lip movement is not guaranteed to
  match what's burned on screen — an expected mismatch, not a bug report. A
  clip whose link carries no audio at all (seedance, the cheapest default
  link) simply falls back to the Tier-2 behaviour for that one shot, with a
  printed note.

**Route** governs video the same way it already governs images: `local`
(your own ComfyUI — see "Local generation" below), `api` (the hosted
`VIDEO_CHAIN` links — see "Costs and providers" below), or `auto`, which
picks whichever is actually ready.

**Budget profile decides which shots animate**, and how many:

- `free` animates only shots a ready local route can make, since local
  generation is the only way to animate at $0; with no local ComfyUI
  reachable, nothing animates under `free`.
- `one_dollar` runs a planner over what's left of the episode's cap: pinned
  shots (set with the per-shot **Animate** checkbox) go first, then shots
  ranked hook → cliffhanger → peak reveal → turn, then the longest remaining
  dialogue shot, picking as many as fit — ties go to shot order. The
  estimate shows the split and the running total before anything is spent.
- `quality` animates every shot that isn't individually kept still.

A separate **clips** chip next to the usual estimate — "N clips (est $X, not
now)" — and a **Video** card on the Assets panel show the planner's current
pick before you press anything. This selection is never stored — it's
recomputed from the current cap, the day's spend and what already exists
every time the estimate or the step runs, so it can never go stale the way
a saved plan could. A shot can still be pinned in or out by hand: each
shot's card gets its own **Animate** / **Keep still** toggle (pressing the
active one again clears it back to the planner's own pick) and its own
**Re-animate** with a note, mirroring the
image regenerate; the storyboard's own per-shot "Keep still" is replaced by
this one once the tier is 2 or above. A clip shows a state badge — `current`
/ `stale` / `failed` / a pending regenerate — and its route, next to a
preview of the clip itself.

**Sticky image and video links.** Starting this phase, both the image chain
and the video chain settle on **one provider per episode**, offered once the
first asset of that kind is served — mixing providers inside one episode was
found to break the look (a character drawn flat-cartoon by one link and
photoreal by another no longer reads as the same character). While a link is
in force, every image (or every clip) in that episode comes from it alone; a
link that becomes unreachable mid-episode — no key, the day's free-tier or
paid allowance spent, a budget refusal, an outright rejection, or a local
server gone — stops before any further call and offers a switch: which link
would run next, which shots would need to be redone, and what that would
cost. Nothing is generated or charged by the offer itself. Switching is a
deliberate action — a confirmation on the dashboard's sticky-link offer, or
`links.image` / `links.video` on the assets PATCH — and stales exactly the
assets made on the link you're leaving, nothing else.

**"Animate off first."** An episode's shot durations are not fully settled
until its lines are actually voiced — a voice can run a little faster or
slower than estimated, which moves a shot's length and, with it, which clip
length the length table picks, which link is sticky, or the dollar total.
The video phase recomputes its plan once, right before it would spend
anything, and if that recomputed plan disagrees with the one shown before
the run — even by one shot or one cent — it refuses, naming both plans, while
keeping whatever images and voices it already made. In practice this means a
**first** tier-2 (or tier-3) run on an episode with unmeasured voices often
hits this refusal once, after making every image and voice but before buying
any clip; running the step again (the "Continue" you'd press anyway) now
sees the real, measured durations, and that second plan matches what's
actually there — it animates. Unticking **Animate** (or `--no-animate` on
the CLI) for that first pass makes this deliberate: images and voices only,
reviewed, then animated as a second, separate pass once everything is
settled.

### 11. Render

The **Preview** tab. Unlocked once the assets are approved (otherwise:
"Approve the episode's assets first (the Storyboard tab)."). A
**Subtitles** select — `Style default`, `Word pop`, `Two line` or `None` —
sits above **Render** (`Render again` once one exists), with an estimate
chip (the paid part, the shot count, an estimated render time) and a route
chip. At tier ≥ 2, once some shot's clip isn't current (failed, stale, still
generating or never animated), a **"Fill failed shots with motion"**
checkbox appears too, off by default: leave it unticked and such a shot
blocks the render, named, with its regenerate target or a Continue offer;
tick it and those shots render with Tier-1 motion instead — the estimate
re-fetches with it set, so the chip reflects what pressing Render would
actually do.

The renderer is pure FFmpeg: 1080×1920, 30 fps, `libx264`. Every shot's
still picture gets its own Ken Burns move — a pan/zoom, eased in and out —
from the style's own motion rules, including how far a pan travels across
the frame (`pan_pct`, each style's own value now, not one fixed number), and
shots cross-fade into each other per the storyboard's own transitions. The
audio is one continuous music bed,
ducked under the dialogue by sidechain compression, mixed with the dialogue
itself and the episode's self-made sound effects (from `assets/sfx`) and
the BGM track the assets step picked (from the shipped `assets/bgm`
library, by way of `assets/bgm/bgm_index.json`); the whole mix is levelled
in two passes to an integrated loudness of −14 LUFS, a true peak of
−2.5 dBTP and a loudness range of 11, with the AAC encoder's perceptual
noise substitution turned off (measured on two real episodes: I −14.2 / TP −2.3 on
one, I −14.1 / TP −2.1 on the other). Every render carries the
"AI-generated" ("Généré par IA" in French) disclosure, and ends either on
an end card — "PART 2" ("PARTIE 2" in French) plus the story's own title,
cut to black — or a hard stop, per the style. Subtitles burn whichever mode
you picked: `word_pop` (one word at a time, popping in), `two_line` (each
speaker keeps its own colour, always readable against the outline), the
style's own default, or none; switching subtitles re-runs only the final
pass, not the shots themselves. Text is set in the template's own font:
Montserrat Black for the two original styles, and, for the five added since,
one committed face each — Bangers, Bebas Neue and Patrick Hand (OFL-1.1),
Luckiest Guy and Chewy (Apache-2.0) — or your own font in `custom_fonts/`
when you drop one in there.

**Subtitle look.** A story can carry its own subtitle look, set in the Style
step ("Subtitle look") or on the episode page, in the **Subtitles** panel
(which also has the font). The fields: the font (Montserrat, Bangers,
Luckiest Guy, Bebas Neue, Chewy or Patrick Hand), the size (60–160 % of the
style's), the position (15–95 % of the frame's height from the top: the
word's centre in word pop, the block's bottom edge in two line), the text
colour (word pop only: two line keeps each speaker's own colour), the
highlight colour (two line: the word being spoken), the outline width (0–8
px) and colour, and an optional box behind the text (colour and an opacity
of 0–100 %). A box replaces the outline: the outline fields are kept but not
drawn. A field left empty keeps the style's own value. Text and highlight
must reach a contrast ratio of at least 4.5 against what they are drawn on
(the box, else the outline; no outline and no box means no check), or the
save is refused with the ratio named. The look is saved on its own, at any
time and after the style is locked, and it touches no image, clip or cache
and clears no approval; it is refused while a render runs. **Render again**
burns it: only the final pass runs, with the cover and the end card taking
the story's font. A story with no look renders exactly as before. `PATCH
/api/stories/{id}/subtitle-style` sets it (see `docs/api.md`); `null`
clears it.

**Framing.** Every still and, since phase 6, every Tier ≥ 2 clip is fit to
the story's frame (9:16 unless the story was made at 16:9 or 1:1, "1. New
story") the same way: centred and **cropped** to the exact frame
when it isn't already close to one — a still more than 2% off, or any clip,
since a hosted video model can hand back its own shape regardless of what
was asked for (Kling, for one, keeps the input keyframe's own aspect ratio
rather than cropping or padding to what was requested) — never stretched and
never letterboxed. A source already at the frame, or within 2% of it, keeps
exactly the encode it had before.

**Frame geometry.** The renderer draws three frames: 9:16 (1080×1920),
16:9 (1920×1080) and 1:1 (1080×1080), with the text sizes unchanged (the
short side is 1080 in all three). A story's frame is the one it was made at
("1. New story"): the encoder, the cover and the subtitles follow it, and a
9:16 render is byte for byte what it was before. The tier-2 render golden stays
9:16; the other two frames have tested builders and reference frames
(`python3 tools/render_golden.py --aspect 16:9`).

The finished video plays back in a player below (its poster is the cover),
with a summary strip — duration, size and fps, loudness (I / TP / LRA),
seconds spent rendering, shots served from the cache versus freshly made,
stages run versus cached — any warning (a length outside the template's
window, or a loudness or true peak outside spec, is a warning here, never a
failure), and a **"Download video"** link. A render that no longer matches
the episode's current assets is flagged **"Out of date"**, asking you to
render again. Cancelling a render stops it in about a second and a half and
keeps whatever it had already finished, including every shot already
rendered.

Once an episode has a finished render, editing it — a line, a shot's image,
its motion, a transition — shows a **"Changes since last render"** list
here: one line per shot that would be made again and why (its image
changed, its frames moved, its motion or modifiers changed, …), with a
**Re-render** button carrying its own estimate ("≈3 of 11 shots"). Pressing
it makes again only the shots the list named — every other shot's clip is
reused byte-for-byte from the last render that actually finished, verified
by its own recorded hash, never merely by the file being present — and ends
immediately with nothing left to approve. The summary strip then reads "3
of 11 shots re-rendered · 8 reused" instead of the old render's own count.
Re-render is blocked, with the reason shown instead of the button, on
exactly what a full render would also refuse on — most often a shot whose
image needs regenerating first ("needs a new image", in the Storyboard tab).

Measured on this project's 4-core VPS: a full 20–21-shot render took
160–161 seconds; re-rendering with every shot already cached (switching
subtitles, say) took 84–96 seconds; a live 23-shot episode re-rendered after
two text-only line edits and one shot's image (3 of 23 shots remade) took
2 min 7 s against 2 min 49 s for the same episode's full render — most of a
render's time is the final mux and audio pass, shared by a partial and a
full render alike, so a partial re-render saves the shot-encoding time, not
the whole thing.

### 12. Metadata pack

Unlocked once the episode is rendered (otherwise: "Render the episode
first."). **Write metadata** (`Write remaining metadata` once some
platforms are written already) asks the model once per platform — TikTok,
YouTube Shorts, Instagram Reels — for a title, a description, hashtags (3
to 5 on TikTok and Instagram Reels, exactly 3 on YouTube Shorts) and the
cover's hook text; Python builds the rest: the description ends with the
script's own next-episode teaser, and the pinned comment is that same
teaser followed by the call to comment `Comment "PART 2" for the next one →`
(`Commente « PARTIE 2 » pour la suite →` in French), and an episode template
that opts in with `end_card_cta: true` puts the same call, without the
arrow, on the end card under "PART 2". A French story's
cards also carry an English title and English hashtags.

The **cover** — the hook scene's first shot, filled to 1080×1920, with its
hook text burned over it in the style's own font, one frame kept as
`cover.jpg` — is made the first time the pack is written for this render,
and again whenever it goes missing; regenerating one platform's text alone
never remakes it.

Each written platform gets its own card: title (and an English title where
one exists), description, hashtags (and English hashtags where they exist),
hook text and pinned comment, each with its own **Copy** button, and its
own regenerate — an optional note re-asks that one platform alone, leaving
every other platform and the cover untouched. The pack is marked **stale**
once the script or the render changes under it; writing metadata again then
fills in whatever is missing, keeping any platform already written for the
current render and script.

The episode's **cost ledger** sits below the metadata cards: one row per
call this episode has made — step, provider/model, quantity, cost, free or
paid — with totals underneath.

### Fast track ("Generate episode")

**Generate episode**, in the episode page's header, runs the script, the
storyboard (T1, one call per scene — the dashboard always plans this way;
the fast, no-call storyboard is CLI-only), the assets (keyframes, their
check and auto-fix, voices, then the clips), the render and the metadata as
a single job, under a budget derived from the plan (an hour, plus ten
minutes a clip and the checks' own time, four hours at most), picking up
wherever the episode already stands: a document already approved is kept as it is,
assets already approved and current are kept, a render already current is
kept — so pressing it again after a partial run, or after fixing whatever
it stopped on, repeats nothing already done. Whatever it writes fresh is
auto-approved by that document's own approval rule: a complete script with
a fresh, passed consistency check inside the template's length window (on a
v2 story, a fresh first-watch check with nothing blocking — its minor issues
are named in the feed's approval line and on the Review tab); a storyboard
that covers it; a complete assets grid. On a v2 story it approves two
things **anyway**, naming what it went over: the keyframes still flagged
after their auto-fix, and the script once the script step's two repair
passes are spent and only blocking issues remain (both checks fresh, the
length inside the window) — the same issues found again on every pass are
a judgement for you, not for a third pass. The Review tab's checklist says
"Approved anyway by Generate episode — still found: s00 (continuity), …"
and lists each fix. Tick **Stop at the script if its repairs leave issues**
(`stop_on_script_issues`, CLI `fast-track --stop-on-script-issues`) to keep
the stop instead; a legacy story always stops there. Before making or spending anything it checks the plan against
the budget and stops before any paid image or voice unless paid generation
is allowed and every cap — the episode's, the day's and the story's — fits,
naming the numbers.

Pressing it asks you to confirm first, with the estimate's own split (LLM
calls, images and up to $0.40 of keyframe redraws, voices, every clip,
render minutes, a total against the caps) and, on a v2 story, "no stop for
keyframe review — you review the finished episode" with a checkbox to keep
that stop. On a v2 story the whole episode is checked against every cap
before the first call and refused whole when it would not fit. While it
runs the header shows the sub-step and a progress bar; when it ends the
page opens the **Review** tab: one tile per shot — the keyframe, its check
(passed, fixed after N redraws, still flagged and why), its line — tap a
tile for the keyframe large, the clip and the regenerate controls; above
the grid the episode's status, what was auto-approved, what is still
pending and the spend by kind; one **Approve keyframes and assets** for
whatever is pending. If it stops partway — a script outside its window or
with a check it could not refresh, a plan over a cap, a scene T1
under-planned — it names
the sub-step, what happened and what to do next, then **Generate episode**
again to continue exactly from there.

### Agent mode (one job from the idea to episode 1)

A story is in **Studio** mode unless you ask otherwise: every step waits for
your approval, exactly as this page describes. A story created in **agent
mode** (`POST /api/stories` with `"mode": "agent"`, stored as
`generation_profile.mode`; a PATCH of `generation_profile.mode` switches an
existing story) can instead be taken from its one-line seed to episode 1
rendered with its metadata pack by **one job**, `story-fast-track` (`POST
/api/stories/{id}/steps/story-fast-track`, no parameters; refused with a 409
on a Studio story). From the CLI: `new --mode agent --seed-text "..."
[--format ID]` then `step STORY_ID story-fast-track` (or the `agent`
command, doing both in one call) -- see "From the CLI" below. The
dashboard's Mode choice arrives in the next stage of plan 21; the backend
and the CLI are there now.

The job runs nine parts in order, each a line `⏩ Agent n/9: <part>` in the
feed and the job's `sub_step`: the **concept** (the concepts step asked for
one card from the seed — the idea is the concept — then chosen), the
**bible**, the **style** (built from the story's style, else the concept's,
with its preview strip), the **cast** (the concept's cast sketch, at most
five, voices pinned by the cast step), the **places proposal**, the
**places** (the proposal made), the **season** (eight episodes), the
**knowledge base** (v2 only; any prop it adds is drawn by the places step
too) and **episode 1** — handed to the fast track above with its usual
rules (no stop at the keyframes or at the script's leftover issues), on the
story's own episode format.

**What it approves without a taste check.** Each document is approved by
the same rule as your own click — complete, nothing missing — and recorded
as approved by the agent (`approved_by: "agent"` on story.json's approvals,
on each character, place and prop, on the season and the knowledge base;
episode 1's documents say `fast_track`, as the fast track's always do). The
**style, the portraits and the plates are approved as soon as they are
complete — nobody looks at them first.** That is the trade-off: one click
instead of a dozen, in exchange for reviewing the looks afterwards. Open the
story in Studio when it is done: every document stays editable, every
regenerate stays available, and your own approval replaces the agent's mark.
Script and keyframes keep their judges (E4, J1, J2) and the fast track's
rules.

**One estimate first.** `GET /api/stories/{id}/estimate/story-fast-track`
sums every part still to do — LLM calls (on the free links first, $0 here as
every LLM estimate counts them), the preview strip, the cast's portraits and
sheets, the places' plates and props, the props the knowledge base may add,
and episode 1 (priced exactly once the pre-production is approved, before
that from the budget profile: nothing on the free profile, the Quality
preset's own figure, the profile's cap otherwise) — into one `est_usd`, names
the **paid** parts, shows the caps line, and gives a time budget (a quarter
of an hour a part of pre-production, the fast track's own for episode 1, six
hours at most). What cannot be counted yet is an upper bound ("up to"): five
characters before the concept exists, three places and three props before
the proposal. The job asks the same estimate before its first part and
**stops before anything is called or bought** when a part cannot run: no
key, a concept with no style or no cast sketch, an image chain or editor that
cannot run, paid parts while `allow_paid` is off, or the sum over the day's
or the story's cap (episode 1's predicted price against its own cap) — each
named with its numbers. The route refuses the job with the same sentence
(409) before it exists.

**Continue.** Every stop ends the job with "Agent run stopped at <part> (n
of 9): <reason>. Continue the agent run: it picks up here and repeats
nothing already done." Fix the reason and run the same job again: a part
whose document is approved is kept as it is, and each step fills only what
is missing (the bible's missing parts, the season's entries not written yet,
the characters' missing images) — nothing paid for is bought twice.

### The episode page

The episode header carries the episode's number and, beside it, the
**Fast track** button (see "Fast track" above) — present whichever tab or
layout you are on. Above about **1,100 px** wide, Script, Storyboard and
Preview sit as three panes side by side; narrower, they're **tabs** below
the episode header (arrow keys/Home/End move between them). Only one layout
is ever mounted — a pane hidden by the tab layout still isn't left polling
in the background.

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

`python main.py --ai-story` covers all thirteen steps for scripting or
testing, without a browser. Ten subcommands: `new`, `step`, `render`,
`fast-track`, `agent` (plan 21 stage 2: `new --mode agent` then `step
STORY_ID story-fast-track` in one call), `feedback`, `approve`, `list`,
`voice-tails` (what the Gemini tail guard cut, or would cut, from each line
of an episode — read only) and `prompt-limits` (every link's prompt size
limit and its source).

Keys, chains, caps and `allow_paid` come from the environment (or `.env`);
add **`--settings`** to any subcommand to read the ones the dashboard's
Settings stored (`data/settings.json`, or `WEB_SETTINGS_FILE`) over it — the
run says how many values it read, never a value. A v2 episode's keyframes
are approved with `approve STORY_ID keyframes:1 [--anyway]`, between an
assets run with `--no-animate` (keyframes, voices and their checks) and one
without (the clips):

```
python main.py --ai-story step STORY_ID assets --ep 1 --no-animate --settings
python main.py --ai-story approve STORY_ID keyframes:1
python main.py --ai-story step STORY_ID assets --ep 1 --settings --auto-approve
```

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
python main.py --ai-story step STORY_ID assets --ep 1 --auto-approve
python main.py --ai-story step STORY_ID assets --ep 1 --align-words
python main.py --ai-story step STORY_ID render --ep 1 --subtitles word_pop
python main.py --ai-story render STORY_ID --ep 1 --encoder auto
python main.py --ai-story step STORY_ID metadata --ep 1
python main.py --ai-story fast-track STORY_ID --ep 1 --storyboard fast
python main.py --ai-story step STORY_ID memory --ep 1 --auto-approve
python main.py --ai-story feedback STORY_ID --ep 1 --text-file comments.txt --auto-approve
python main.py --ai-story step STORY_ID propose-next --ep 1
python main.py --ai-story step STORY_ID script --ep 2 --auto-approve
python main.py --ai-story step STORY_ID rerender --ep 1 --dry-run
python main.py --ai-story step STORY_ID rerender --ep 1
python main.py --ai-story list
```

Agent mode (plan 21 stage 2): `new --mode agent` and `--format` set the
story up, `story-fast-track` runs it, `agent` does both in one call --

```
python main.py --ai-story new --lang fr --mode agent --seed-text "A fruit island reality show" --style fruit_drama --format narrated_drama_60s_v2
python main.py --ai-story step STORY_ID story-fast-track --estimate
python main.py --ai-story agent --lang fr --seed-text "A fruit island reality show" --style fruit_drama
```

`script` and `storyboard` both take `--ep N`, required — the episode
number, bounded by how many episodes the season planned. `--fast`
(`storyboard` only) builds every scene's shots deterministically in this
process: no LLM call, so no key gate either. `--measure-voices` (`script`
only) measures every line through its speaker's pinned voice, after
writing, and keeps the audio. Episode 2 and beyond are refused, naming the
memory step (below), until episode N-1's own series memory is approved and
fresh.

`assets`, `render` and `metadata` also take `--ep N`, required, as phase 3's
steps do. `render` and `fast-track` are their own top-level commands too:
`render STORY_ID --ep N` is exactly `step STORY_ID render --ep N`, spelled
shorter, and `fast-track STORY_ID --ep N` runs the script through the
metadata pack in one job (see "Fast track" in the walkthrough above).
`assets` takes `--align-words` (opt-in forced-alignment word timings,
through the STT chain, instead of an even split) and meets the image and
voice chains' own gates inside the step, stopping before its first call
when a paid part is over a cap, with the numbers — never a wasted call.
At tier ≥ 2 it also takes `--tier N` and `--route local|api|auto`, which
patch the story's own `generation_profile` before the step runs and print
the result — the same profile the dashboard's story page edits, never a
run-only override — `--no-animate` (images, voices, SFX and BGM only;
nothing video-related is attempted or spent — the same switch as the
dashboard's Animate checkbox), and `--estimate`, which prints the step's
plan (including any clips, their seconds and their dollars) and calls
nothing — no job is even created. `render` takes `--subtitles` (`style`,
`word_pop`, `two_line` or `none`, default `style`, the style lock's own),
`--encoder` (`libx264` or `auto`, default `libx264`) and, at tier ≥ 2,
`--fill-failed-with-motion` (default off; lets a shot whose clip is failed,
stale or missing render with Tier-1 motion instead of refusing — the same
box as "Fill failed shots with motion" in the dashboard); `render` calls no
API. `metadata` takes no parameters. There is no CLI command to regenerate
one shot's clip (`shot:<ep>:<shid>:video`) — that's dashboard/API only.
`fast-track` takes `--storyboard` (`t1`, the default, or
`fast`) and meets the LLM key gate exactly as the API does for every
fast-track job.

**Series steps (step 13)**, all taking `--ep N`, required: `memory` writes
episode N's series memory from its approved script (one free-chain call);
`propose-next` proposes new characters and twists for episode N + 1 from
episode N's approved, fresh memory (one call) — each proposed item is then
accepted or rejected from the dashboard or the API, never from the CLI.
`feedback` (episode N's audience comments) is its own top-level command
rather than a `step` subcommand, because it has to store the paste before it
can digest it: `feedback STORY_ID --ep N --text-file F [--stats-file F]
[--auto-approve]` reads `F` (and, optionally, a second file of stats) and
stores it exactly as `POST /episodes/{ep}/feedback` would — refused whole
over 6,000 characters each, never trimmed — then runs the `feedback` step
(F1) in the same call, so one command takes you from a comments file to a
digest. `--auto-approve` on `memory` approves the entry it just wrote; on
`feedback` it approves with no direction chosen (choose one from the
dashboard or the API afterwards, if you want one to steer the next
episode); `propose-next` never takes it — each item needs a human decision.

**`rerender --ep N`** makes the episode's render again, remaking only the
shots that changed since its last good render — the same partial re-render
"Re-render" in the dashboard runs. `--dry-run` prints what it would remake
and why, one "shot: reason" line each, and calls no ffmpeg; without it, the
command renders. Both refuse the same way a normal render does when a
precondition isn't met (an outdated shot image, no finished render yet),
naming what's missing.

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

`--auto-approve` means something different per step: for `bible`, `style`,
`season` and `memory` it approves the one document the step just wrote; for
`cast` and `places` it approves every character, or every place and prop,
that already has everything, naming anything left short of that instead of
failing the command; for `script` and `storyboard` it approves through the
same rule the dashboard's Approve button uses — a complete script with a
fresh, passed consistency check, or a fully and currently planned
storyboard; for `assets` it approves the grid once every shot is current or
locked and every line voiced; for `feedback` it approves with no direction
chosen — and **never** "approve anyway": with issues still open it prints
them and exits 1 instead of forcing the approval through. `render`,
`metadata`, `fast-track`, `rerender` and `story-fast-track` take no
`--auto-approve`: their job ends completed once it is done, with nothing
left to approve (the fast track and the agent run each auto-approve every
document they write fresh by that document's own rule regardless, never
through this flag); `propose-next` never takes it either — each proposed
item needs a human decision, from the dashboard or the API.

`new` creates a draft story and, with `--concept`, chooses a library concept
in the same call. `--lang` is required — there is no default, on the CLI
any more than in the API. `--mode agent` (default `studio`) creates the
story in agent mode, exactly as `POST /api/stories` does; `--format` sets
its own episode template (one of the shipped ones, a usage error naming
them otherwise). `--tier`, `--route`, `--consistency-mode` and
`--budget-profile` set the generation profile; left out, they take the same
defaults as the dashboard (`1`, `auto`, `references`, `free` — an agreement
test keeps the CLI, the API's request model and the dashboard's form from
drifting apart).

`step` runs one step in this same process, printing the same lines the
dashboard's activity feed shows. `--auto-approve` applies to `bible`,
`style`, `cast`, `places`, `season`, `script`, `storyboard`, `memory` and
`feedback` (a concept is approved by choosing it, and a preview is approved
together with the style it belongs to — the CLI's usage error says so if you
try it on either). `--ep` is required for `script`, `storyboard`, `assets`,
`render`, `metadata`, `memory`, `feedback`, `propose-next` and `rerender`,
and rejected for every step before them. `--allow-slow-chain`
(or `ALLOW_SLOW_CHAIN=1`) lets an LLM step run on a chain whose only
reachable link is the slow floor
(mirrors the clip CLI's own flag). `list` prints one line per story: id,
status, language, title. Run `python main.py --ai-story --help` (or
`... new --help`, `... step --help`) for the full option list.

Keys and `LLM_CHAIN` — and, for `cast` and `places`, `IMAGE_CHAIN`,
`IMAGE_EDIT_CHAIN`, `TTS_CHAIN` and `VISION_CHAIN`; for `assets`, those same
three plus `STT_CHAIN` when `--align-words` asks for it; for `metadata`,
`memory`, `feedback` and `propose-next`, `LLM_CHAIN` again — come from the
**environment or `.env`**, the same file the clip CLI reads — **never** from
the dashboard's Settings store, on the CLI, for any step (see "Not yet"
below). `rerender` calls no API at all, so it needs no key. A story created
or stepped from the CLI shows up in
the dashboard immediately (same
`outputs/stories/` folder, same index), but the CLI and a running server
don't coordinate: running a step from both at once on the same story lets
both write its files, last write wins. Fine for solo use; don't script the
CLI against a story you also have open in the dashboard.

Exit codes: `0` done, `1` refused or failed (reason on stderr), `2` a usage
error, `130` interrupted (Ctrl-C cancels the step cleanly; whatever it had
already written stays — every write is atomic).

### Benchmarks

`tools/bench_llm.py` measures the writing models without touching a story.
Its `--episode-ab` mode (plan 23) writes **one episode with the writing-v3
chain once per model** and puts the scripts side by side, so you can read them
and rate the models yourself: complete lines, the hook, the validator pass
rate, retries, the real cost, the latency.

```
python3 tools/bench_llm.py --episode-ab outputs/stories/<id> --dry-run \
    --chains "gemini-paid/gemini-3.8-flash,anthropic/claude-sonnet-5-5" --max-usd 2.50
python3 tools/bench_llm.py --episode-ab outputs/stories/<id> --episode 1 \
    --chains "gemini-paid/gemini-3.8-flash,anthropic/claude-sonnet-5-5" --allow-paid --max-usd 2.50
```

- **What runs.** For each link in `--chains`, the script step's own writing
  calls (the beat sheet E1v3, the body scenes E2v3, the framing E3v3, then the
  first-watch judge J1v3), each link alone — a one-link chain, no fallback to
  another model — with the step's own prompts, validators and retry-once
  ladder. No fill pass, no repair: you read what each model wrote.
- **A throwaway copy.** It runs against a copy of the story's JSON documents in
  a temporary folder. The story's own episode files are never read for writing
  and never written; an existing script of the episode is not in the copy, so
  the episode is written afresh (from episode 2 on, from the memory of the
  episodes before it). If the story is not on writing v3 the copy is stamped v3,
  and the summary says so. Episode 1 is the usual choice.
- **Free links run by default; a paid link needs two keys.** The paid links are
  listed and skipped unless you give `--allow-paid` **and** `allow_paid` is on in
  Settings (the Settings switch wins: `--allow-paid` with it off is refused). A
  paid link also needs `--max-usd`, a hard cap for the whole run: before each
  request — a validator retry counts — its estimate is added to what the run has
  booked, and a request that would cross the cap is refused unsent. Each request
  is also checked against the live caps (the daily cap, the story's cap). This
  is the one exception to the rule that the bench never spends (DEC-296).
- **`--dry-run`** prints each link's estimate (and its upper bound, which doubles
  it when every call retries) and exits without calling anything. The run prints
  today's spending before and after.
- **Where it lands.** `outputs/stories/<id>/bench/<timestamp>/`: `summary.md`
  and `summary.json`, and one `<link>.json` per model with its script and the
  judge's verdict, all rewritten after each link, so Ctrl-C keeps a partial
  summary. Nothing else of the story changes.
- **The ledger.** Every answered paid request is booked as usual (`spend.json`
  and the story's own ledger, step `bench`, the served model on the row), as a
  **story-level** row with no episode: an experiment is not part of an episode's
  production cost, and an episode that has already spent its cap must not decide
  an A/B.

A dry run on one real story estimated about $0.10 for Gemini Flash, $0.53 for
Claude Sonnet and $1.33 for Claude Opus, one try each: `--max-usd 2.50` fits one
try of all three, `3.00` leaves room for retries. The run is yours to start; the
app never starts it.

## Costs and providers

**LLM calls** (concepts, bible) run on the same `LLM_CHAIN` clip jobs use —
whatever you've set in Settings → Providers or `.env`. The one AI-Story-only
rule: **a story step never calls a paid link unless `allow_paid` is on**,
even if a clip job would happily fall through to one. A paid link left out
this way is still printed (`⏭ Skipping openrouter/...: paid link, allow_paid
is off`), never silently dropped, and if the chain's *only* keyed link is
paid, the step refuses up front and names which free key to add instead.

**The premium writing chain.** Once a paid Gemini key is set
(`GEMINI_PAID_API_KEY` in Settings → Providers or `.env`, kept apart from
`GOOGLE_API_KEY`), the calls that matter most for how the episode reads — the
concepts (C1, C1v2), the concept judge (C1J), the bible (B1v3), the episode
script (E1, E2, E3) and the first-watch judge (J1) — move off the free
`STORY_LLM_CHAIN` onto `STORY_LLM_PREMIUM_CHAIN`: default
`gemini-paid/gemini-3.8-flash`, then today's chain as its fallback. Settings
→ **"Story premium writing chain"** sets a different one (same
provider/model grammar as the chain field above); empty uses the default,
and the field is never spent by "Test provider chain" — that button still
probes `LLM_CHAIN` alone. Gemini 3.8 Flash prices at $0.75 / $3.75 per
million input/output tokens until 2026-12-31, then $1.50 / $7.50 — about
**$0.22 of premium writing an episode** today (about $0.45 after the price
change), plus about $0.25 more for a new story's premium concepts, judges
and bible. `allow_paid` off skips the paid link exactly as any other paid
link, printed rather than silently dropped, and the free chain still writes
the whole episode; the estimate's `text_usd` line counts every premium call
before the step runs.

**Claude as the writer (optional).** With an Anthropic key
(`ANTHROPIC_API_KEY`, in Settings → Providers or `.env`), the premium chain
can name Claude: put `anthropic/claude-sonnet-5-5` ($2 / $10 per million
input/output tokens, the provider's default model) or
`anthropic/claude-opus-5-5` ($4 / $20) in **Story premium writing chain**,
for example `anthropic/claude-sonnet-5-5,gemini-paid/gemini-3.8-flash`. The
shipped chain does not name Claude: it is used only when you do. An effort
can follow the model after an `@` (`low`, `medium`, `high`, `xhigh`, as in
`anthropic/claude-opus-5-5@xhigh`); without one each call takes its own,
high for the script calls (E1, E2, E3), medium for the concepts and the
bible, low for the two judges. Every request is billed: a Claude link runs
only with `allow_paid` on, inside the caps, and its estimate is counted
before the step runs. Cached input is priced at its own read and write
rates.

Anthropic's server-side fallback is on: when Claude declines a request,
Anthropic runs it again on the model it recommends inside the same call, and
the reply is booked at the price of the model that actually answered (the
ledger row says which); the pre-call estimate prices the request at the
dearest model that might answer, never low. When the whole fallback chain
declines, the step does not retry the same link at full price: it moves to
the next link of your chain. Claude takes no temperature, so a retry cannot
"cool off" as it does on other links. Settings has a **Check the Anthropic
key (free)** button (`POST /api/settings/check-anthropic-key`): it asks
Anthropic whether the key is accepted and each `anthropic/` model of the
chain is available, which sends no request and bills nothing ("key valid,
model available — not exercised: every request is billed"). The provider
chain test lists Anthropic rows as "listed" for the same reason.

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

**Image links of a v2 story, by role.** A v2 story on a billed profile does
not use the two chains above for its sheets, plates, props and keyframes:
each role has its own short list (`budget_profiles.json`), and the preset's
estimate prices the first link of each.

| Role | Links, in order | Price per image |
|---|---|---|
| sheet, plate, prop | `fal/seedream-4.5`, `fal/seedream-4.5-edit`, `gemini/nano-banana-2-lite` | $0.04, $0.04, $0.0336 |
| keyframe | `fal/seedream-4.5-edit`, `gemini/nano-banana-2-lite` | $0.04, $0.0336 |

`gemini/nano-banana-2` ($0.067) is a paid Gemini image link too, used by the
legacy edit chain above and by no v2 role. `gemini_first` puts the Gemini
link first in every role of one story.

**Voice calls** run on `TTS_CHAIN`: **Edge** (free, keyless, many
languages, the shipped default's first link), Gemini's TTS model (needs
`GOOGLE_API_KEY`, still free), or a local engine (`piper` / `kokoro` /
`chatterbox`, once its package is installed). A voice sample is a single,
fixed-link chain built from the character's *pinned* voice alone — it never
falls through to another provider or another voice; a failure names other
voices to try instead of the one that failed.

**ElevenLabs (paid, the last link).** With `ELEVENLABS_API_KEY` set,
`elevenlabs/flash` ends `TTS_CHAIN` (`elevenlabs/multilingual-v2` can be
named in the chain too). It is billed per character: flash $0.04,
multilingual v2 $0.08 per 1,000 characters (read on ElevenLabs's price page,
2026-10-04). It needs `allow_paid` on, counts against the caps, and books
every line. It returns word timings, so the subtitles follow the voice. Its
eight premade voices appear in a character's **Other voices** only while the
key is set, each badged "paid · ≈ $x per episode" with the refusal that
applies when a cap blocks it; the app never proposes one for you, you choose
it. Speaking rate, pitch and direction are noted and not applied. A refused
key, a spent character quota or a rate limit is named with its status. A
keyless install, or `allow_paid` off, skips the link and nothing changes.

**Design-reference descriptions** (a character's uploaded image, described
once through vision) run on `VISION_CHAIN`: Gemini's `flash-lite` model by
default (needs `GOOGLE_API_KEY`, free), then an OpenRouter free vision
model, a local Ollama vision model, or Gemini's larger `flash` model.

**An episode's images and voices** (the assets step) go through the same
`IMAGE_CHAIN` / `IMAGE_EDIT_CHAIN` / `TTS_CHAIN` as Cast and Places & props,
with one addition: a free link that pushes back — Pollinations' roughly
one-image-a-minute limit, Gemini TTS's own per-minute quota — is not a
failure. The step retries every item it was held back on in rounds, a
60-second pause before each, until they succeed or a round makes no more
progress. A free Cloudflare key (`CLOUDFLARE_API_TOKEN` and
`CLOUDFLARE_ACCOUNT_ID`, still free) skips that limit and is much faster;
Edge TTS is free and effectively unlimited. A paid link — `fal/flux-schnell`
for an image, a paid voice — is only ever reached with `allow_paid` on and
only within the per-episode, daily and per-story caps; the fast track goes
further and stops before spending anything paid unless every cap fits, with
the numbers, before the first call.

**At tier ≥ 2, video calls** run on `VIDEO_CHAIN` — local ComfyUI, or five
hosted links, each adapted the same way an image link is, but priced and
billed by the second instead of per image. The shipped default chain:

```
local/comfyui, fal/seedance-1-pro-fast, fal/ltx-2.3-fast,
fal/kling-2.5-turbo-std, gemini/veo-3.1-lite, fal/ltx-2.5-fast
```

| Link | Price | Clip lengths |
|---|---|---|
| `fal/seedance-1-pro-fast` | $0.022/s at 720p | 2–12 s |
| `fal/ltx-2.3-fast` | $0.06/s at 1080p | 6, 8 or 10 s |
| `fal/kling-2.5-turbo-std` | $0.21 for 5 s, then $0.042/extra s | 5 or 10 s |
| `gemini/veo-3.1-lite` | $0.05/s at 720p; $0.08/s at 1080p (8 s only) | 4, 6 or 8 s |
| `fal/ltx-2.5-fast` | $0.09/s at 720p; $0.16/s at 1080p | 6 to 20 s, even |

Prices as of 2026-09-30 (LTX-2.5: 2026-10-04). Seedance's 720p price only
applies when 720p is requested explicitly — left unset, it defaults to 1080p
at $0.049/s.
Seedance and kling send no negative prompt; kling and veo take no seed.
`ltx-2.3-fast` sends `generate_audio` only for a Tier-3 shot; veo's audio is
always on and included, with no free tier, through its own
`GEMINI_PAID_API_KEY`, kept separate from `GOOGLE_API_KEY`. Kling's clip
keeps the **input keyframe's own aspect ratio** rather than a requested
one — measured live: a 1024×1024 keyframe gave a 960×960 clip (see
"Framing" under Render, above).

**LTX-2.5 Fast, last in the chain.** `fal/ltx-2.5-fast` (Lightricks'
image-to-video model on fal, open weights) sells clips of 6 to 20 s in even
steps at 720p or 1080p: a shorter shot is rounded up to 6 s, as for every
link, and a request for 1440p or more is refused before anything is sent.
Its audio is optional and always asked for explicitly: an ambience clip asks
for sound, a plain tier-2 clip asks for none. It takes no seed, so a
regenerated clip is a different clip. The prices are the highest of the
third-party listings read on 2026-10-04, because fal's own page shows a
placeholder where the price should be: an estimate checked against a cap is
never low, and Settings → Video's free key check shows fal's live price for
the link. It is last on purpose: no automatic pick moves (`one_dollar` stays
on seedance, `quality` on Veo lite or seedance), and an episode reaches it
from its own video-link switch ("Tier 2/3: animating shots"). It is not a
saving: at $0.09 a second it is Veo money, with a 6 s floor (see "Native
speech (API route)" for what that does to speech, and why it is not a
speaking model yet). An old install that pins its own `VIDEO_CHAIN` keeps
its chain; the new link applies where the chain is not pinned, at the next
restart.

**Two more Veo links, for native speech.** Alongside `gemini/veo-3.1-lite`
above, the **native speech** budget profile also reaches
`gemini/veo-3.1-fast` ($0.10/s at 720p, $0.12/s at 1080p) and
`gemini/veo-3.1` ($0.40/s), both audio always on, no free tier, clip lengths
4, 6 or 8 s. Which one speaks a character's lines is the per-story
**speaking-clips model** (`generation_profile.speech_model`): **Lite** (the
cheapest, ≈ $2.6 of clips for a 50 s episode), **Fast** (the default, ≈
$4.6) or **Premium** (the full model, ≈ $16.6); silent shots (the narrator,
a reaction) always use Lite. See "Native speech (API route)" above for the
shot plan and the prompt these links are asked with, and "Your own clips
(the manual mode)" for the `manual/upload` link that replaces all of them at
$0 cash when the clips are the human's own.

A clip never costs more than the length actually sent: each shot's own
length is rounded up to the nearest length the chosen link offers, and the
render trims or holds the result to the shot's exact timing. A video link
needs `allow_paid` on and the same per-episode, daily and per-story caps as
any paid image or voice, shown as an estimate (seconds × price) before
anything runs — local ComfyUI, when reachable, costs $0 and needs none of
this.

Like images and voices, **sticky per-episode links** apply to video too —
one video provider per episode, offered a switch rather than silently mixed
(see "Tier 2/3: animating shots" above). Settings → Providers shows a
`GEMINI_PAID_API_KEY` field and badge next to the rest of your keys;
Settings → Local hardware adds a row naming which of the three local video
workflows your detected hardware would use, if any (see "Local generation"
below).

**The generation cache** (`cache/gen/` in the story's own folder) means a
regenerated or re-run shot or line that asks for exactly the same image or
voice again — the same prompt, seed and references, or the same pinned
voice and text — costs nothing and calls nothing: it is served from the
cache instead. Every call this makes, cached or not, free or paid, is one
row of the story's cost ledger, tagged with the episode it belongs to. A
paid request is booked the moment the provider accepts it and stays booked
if it then fails or its outcome is lost — unless the provider proves it
never ran it (it no longer knows the request, or it refused its input):
then the booking is given back by a negative "void" row, today's spend and
the episode's drop back, and a request the provider lost before running it
is sent once more in the same run, gated and booked again.

**Settings → Budget**:

| Setting | Default |
|---|---|
| `allow_paid` | off |
| `per_episode_cap_usd` | $4.00 |
| `daily_cap_usd` | $12.00 |
| `per_story_cap_usd` | $40.00 |
| `budget_timezone` | UTC |
| budget profile | `free` while paid is off, `one_dollar` once it's on (or pick `quality`) |

Caps you saved earlier win over these defaults (this host's are $2 / $4 /
$10). Turning `allow_paid` on does not spend anything by itself — it only
lets a paid link be *reached*, still bounded by the three caps above, still
shown as an estimate before anything runs. The daily cap has its own section
below, "Budget: today's limit".

**Where spend is recorded:**

- `outputs/stories/<id>/cost_ledger.json` — every call the story has made,
  free or paid, one entry each. The story page's cost total reads this.
- `data/spend.json` — today's *paid* total across the whole app (clips and
  stories together), against which `daily_cap_usd` is checked, plus what was
  allowed for today only and the log of those grants.
- `data/usage.json` — free-tier daily call counters (Cloudflare, Gemini,
  Pollinations, …), shared with clip jobs. A paid call never touches this
  file — it's proof a paid call didn't quietly eat into a free allowance.

## Budget: today's limit

Three caps bound paid spending: per episode, per story and per day. The
per-episode and per-story caps count one thing's ledger; **the daily cap
counts everything the app paid for during one day, every story and every
clip job together**. That is why a refusal can read like nonsense when it is
not: a cast that costs $0.60 is refused because other stories already spent
the day's money. This section is the daily cap.

### What a day is

A budget day runs from 00:00 to 24:00 in the time zone set by
`BUDGET_TIMEZONE` (Settings → Budget → **Day time zone**, or `.env`): an IANA
name such as `Europe/Paris`. Empty, unset or unrecognised means UTC, and an
unrecognised name is shown in Settings with the reason and ignored until
fixed. A save with an unknown zone is refused (400, naming the variable).

What the zone moves: the cap's day, the "resets at" time, the day a refund
goes back to, and the day each story's "spent today" is counted on. What stays on UTC on purpose: the free
allowances of Cloudflare, Gemini, Pollinations and the others (their
providers reset at 00:00 UTC), the price table's dates, and the timestamps
in every ledger. Nothing is migrated when you change the zone: `spend.json`
keeps one total per day, not the instant of each booking, so earlier days
keep the keys they had, the first write under the new zone records the
change, and at most one boundary moves (two hours for Paris): that day may
hold a little more or less than one local day's spending. A refund of a
booking made just before the change may land on the neighbouring day, and
never takes a day below zero. Clearing the setting puts UTC back.

### The refusal

When the daily cap alone stops a paid job, the answer names three numbers
and what to do:

```
Today's paid spending is already $8.38, over the $4.00 daily cap: this cast
(est $0.60: 5 portraits $0.20 + 10 sheet edits $0.40) would bring it to
$8.98. Allow $4.99 more for today only, raise the daily cap in Settings, or
wait for the day to reset at 00:00 Europe/Paris.
```

Under the cap it reads "Today's paid spending is $3.70 of the $4.00 daily
cap: …". The estimate lists the job's parts (portraits, sheet edits,
images, clips) and, for a billed writer, the LLM calls' worst case. "Allow
$X more" is the amount that lets this job through (today's spend, plus the
job, plus its LLM calls' worst case, minus what is already allowed, rounded
up to the cent). A job that would still meet another cap (the episode's or
the story's) says so, since allowing more for today would not lift that one.
The dashboard shows the same numbers as a panel, **Over today's spending
limit**, with the rows Spent today (and which stories), This cast (or
this step), Daily cap, and "Needs $4.99 more today." The API's answer is a
409 with an object `detail` (see "AI Story: the daily cap" in `docs/api.md`).
A paid call the runner refuses meanwhile uses the older plain sentence, with
the allowance named when one exists ("… of the $4.00 daily cap + $4.99 allowed
today").

### Allowing more for today

The panel's buttons: **Allow $X more today** (the exact amount), **Other
amount…** (from $0.01 to $25.00) and **Budget settings**. An allowance is
**for today only**: it is stored beside today's total in `data/spend.json`,
it ends with the day (at the next 00:00 in your zone), the saved cap stays
what it was, and the total allowed in one day cannot pass **$25**. Each grant
is logged three ways: a line on the server's output, an entry in
`spend.json`'s grants list (the last 200), and, when it comes from a story's
panel, a line in that story's activity log. The button only grants: you
press the step again, because a person still says go. Settings shows "Allowed
for today only: +$X" with a **Remove** button that takes it back; the API is
`POST` and `DELETE /api/budget/today/extra`.

### Where you see it

- The **today chip**, beside the estimate chip on every step: `today $8.38 /
  $4.00`, with `+ $4.99` once something is allowed, and in the warning colour
  once spending is over what is allowed.
- **Settings → Budget** shows the day card: the zone (the Daily row says
  "Europe/Paris day"), "Day of 5 Oct (Europe/Paris); it resets at 00:00
  Europe/Paris.", what is allowed for today only, and the five stories that
  spent the most today. When the saved cap is below what was already spent,
  it warns, before and after you save: "This cap is below what was already
  spent today ($8.38). Every paid call is refused until 00:00 Europe/Paris
  unless you allow more for today."
- `GET /api/budget/today` answers the same numbers, and every estimate route
  carries them as `today`.

### The whole cast is checked first

A cast (portraits, then each character's sheet edits) used to check only its
portraits at the gate, so a sheet edit refused later could waste the portraits
already bought. On a v2 story the estimate and the gate now sum the
portraits and the sheet edits and check them as one: a cast that would cross a
cap is refused before the first portrait is bought, and the estimate shows the
same total as the refusal. A legacy story keeps the earlier behaviour: it
stops and asks before its edits. The gate books nothing; each image is still
checked as it runs.

## Stock footage for Clips mode

This is Clips mode's B-roll, listed here because it shares Settings, and
because it spends nothing. Where Clips used to fetch B-roll from Pexels alone,
it now tries up to three sources, in order:

- **`local`** — your own clips. Put `mp4`, `mov` or `webm` files in `./broll`
  (`/app/broll` in Docker; the compose file mounts it read-only) and set
  `BROLL_LOCAL_DIR` to that folder or one inside it (any other place is
  refused). A clip is found by the words of its file name and, better, by an
  optional sidecar `<name>.json` next to it or a folder `index.json`
  (file name → the same fields): `keywords`, `licence`, `author`,
  `source_url`. Symlinks are ignored, and a clip with no licence is used with
  a warning.
- **`pexels`** — `PEXELS_API_KEY`, as before (the Pexels License).
- **`pixabay`** — `PIXABAY_API_KEY`, a free key (the Pixabay Content License;
  100 requests a minute, so answers are kept for 24 hours).

`BROLL_SOURCES` sets the order (default `local,pexels,pixabay`). A source with
no key or no folder is skipped, so an install with a Pexels key alone behaves
as it always did, and with no source at all B-roll is skipped and the clips
still render (the new-job form says so). **Credits:** each stock clip used in
a render is listed under `broll_credits` in that clip's `render_manifest.json`
with a ready line ("Video by {author} on Pexels ({page}), Pexels License"),
the licence and its link; a local clip with an unknown licence carries a
warning to add one before you publish. The credit is data, not burned into the
picture: putting it in a description is up to you. Settings →
**B-roll sources** shows the order, the key and the folder, and
`GET /api/broll/status` answers which sources can serve now and how many clips
the folder holds.

## Stock cutaways (AI Story)

An opt-in per story, on the profile card (**Stock cutaways**, `generation_profile.stock_cutaways: "on"`;
clearable, and editable at any time: it only affects the next assets run). The assets step then fills the
**establishing shots** with free stock footage instead of drawing an image and buying a clip, from the same
sources as Clips mode's B-roll (`BROLL_SOURCES`: your local folder, Pexels, Pixabay).

- **Which shots.** A wide establishing shot with no character and no prop in it that is not a speaking shot
  (a narrator's or a voice-over line over it is fine). The story's frame picks the orientation (9:16, 16:9,
  1:1); a clip must run at least the shot's length plus 0.3 s.
- **What it does.** At the start of the assets step, before any keyframe, each such shot with no keyframe and no
  clip is searched with a fixed query (the place's name, a few words of its descriptor, day or night),
  downloaded to `assets/clips/shot_NN.stock.mp4`, and a frame at 0.5 s becomes the shot's keyframe (so the
  cover, the brief and the thumbnails work). It costs nothing and is not judged. A shot with no match is
  generated as usual, with a line in the feed. Manual stories (your own clips) are filled the same way, and that is the one place a manual story reaches the network: with the switch on, the assets step makes one **free** stock search per eligible shot (a request to Pexels or Pixabay, or a look in your local folder; never a paid call, never a booking). Leave the switch off and a manual story sends nothing, as before.
- **Never replaces.** A keyframe or a clip already there (made, bought or uploaded), and a locked image, are
  never touched. A stock clip stays current only while the switch is on, the shot is still eligible and its
  query still matches (a place renamed, another frame); otherwise the shot is made as any other. You can
  upload your own clip over a stock one (the stock file goes).
- **Render.** A stock shot is cut as plain video at every tier, never with the clip's own sound; a tier-1 story
  without stock renders exactly as before.
- **The estimate** stays at the generated price until the fill has run, with "up to N shots may be stock (free,
  saves about $x)"; afterwards those shots are current and cost $0. Stock footage is **live-action**: only the
  `cinematic_real` style matches it; on the others the card says so.
- **Credits.** `assets/stock_credits.json` and `.txt` list each clip; the metadata pack carries `credits` and
  each description ends with "Stock footage: ..." (only when there are credits), and the episode page shows
  "Videos provided by Pexels / Pixabay" with the links Pexels asks for. The assets estimate carries it as
  `units.stock` (`docs/api.md`).

Rolling back: turn the switch off and run the assets step (the stock shots are made as usual); to remove the
feature, strip `stock_cutaways` and the stock clip records from the stories and delete the `.stock.mp4` files.

## Not yet

A few things phase 5 deliberately leaves for later, and what plan 23 has
not shipped yet:

- **Exporting or importing a story as a bundle** — a folder you can move to
  another install, or share — isn't here. It's a deliberate follow-up, not
  an oversight.
- **Places doesn't pace a free tier's rate limit the way Cast and Assets do**
  — Pollinations pushing back on a place's plate fails that place outright
  rather than retrying it in a later round; run the places step again
  ("Continue places & props") to pick it back up.
- **The CLI only ever reads keys from the environment or `.env`**, never from
  the dashboard's Settings store, for any step — including the ones this
  phase adds. If you keep your keys in Settings, either mirror them into
  `.env` for a CLI session or use the dashboard for the steps that need one.
- **16:9 and 1:1 beyond what "1. New story" lists.** The character sheets,
  the style preview, local ComfyUI clips, Clips mode and stories that already
  exist stay 9:16.
- **LTX-2.5 as a speaking model.** It waits for a one-clip French probe that
  has not been run ("Native speech (API route)").
- **Per-character LoRA training** — teaching a model's own weights a
  character's look, instead of leaning on a keyframe and a prompt for every
  shot — isn't here. It's a deliberate future extension, flagged but not
  built this phase. Neither a fal-hosted training cost nor a local training
  cost has been measured or priced anywhere in this project's own sources
  yet, so none is given here — treat any such number you see elsewhere as
  unverified until this project records one itself.

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

**Local video (Tier 2/3)** works the same way, through `VIDEO_CHAIN`'s
`local/comfyui` link, reached at the same `LOCAL_COMFYUI_URL`. Settings →
Local hardware's probe picks one of three shipped image-to-video workflows
by your detected VRAM; under 8 GB, with no GPU at all, or on Apple MPS,
there isn't a local video workflow — those profiles animate through the
hosted links instead, or not at all under the `free` budget profile.

| Profile | VRAM | Workflow | fps | Max length | 9:16 size |
|---|---|---|---|---|---|
| `mid` | 8–16 GB | Wan 2.2 5B (`i2v_wan22_5b`) | 24 | 2–5 s | 704×1280 |
| `high` | 16–24 GB | Wan 2.2 14B Lightning (`i2v_wan22_14b_lightning`) | 16 | 2–5 s | 480×832 |
| `pro` | ≥ 24 GB | LTX-2 (`i2v_ltx2`) | 25 | 2–4 s, silent | 704×1280 |

Each workflow needs its own model files dropped into ComfyUI's `models/`
folders before it can run. The app checks `object_info` before every queue
and names any file this host doesn't have, with the ComfyUI subfolder it
belongs in:

- **Wan 2.2 5B** — `wan2.2_ti2v_5B_fp16.safetensors` (`models/diffusion_models`),
  `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (`models/text_encoders`),
  `wan2.2_vae.safetensors` (`models/vae`).
- **Wan 2.2 14B Lightning** — `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`
  and `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors`
  (`models/diffusion_models`), `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors`
  and `wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` (`models/loras`),
  `umt5_xxl_fp8_e4m3fn_scaled.safetensors` (`models/text_encoders`),
  `wan_2.1_vae.safetensors` (`models/vae`).
- **LTX-2** — `ltx-2-19b-dev-fp8.safetensors` (`models/checkpoints`),
  `gemma_3_12B_it_fp4_mixed.safetensors` (`models/text_encoders`),
  `ltx-2-19b-distilled-lora-384.safetensors` (`models/loras`),
  `ltx-2-spatial-upscaler-x2-1.0.safetensors` (`models/latent_upscale_models`).

**Unverified live — no GPU yet.** All three templates were authored from
ComfyUI's own published default graphs and are proven only against a fake
ComfyUI server in this repository's own tests; nothing has run them against
a real daemon, because this deployment has no GPU anywhere (the human's
standing choice). First contact with a real ComfyUI is the actual test.

### The future GPU box (Pinokio)

This host is an Oracle Ampere A1 (aarch64, four cores, no GPU), and so is
the container on it: nothing here can run a local model, whatever the
settings say. When you have a computer with a GPU of your own, the local
route works from that machine for $0 of cash, and Pinokio is the way to set
it up. **Pinokio** is an open-source launcher that installs local AI apps in
one click: ComfyUI, Wan2GP (Wan on GPUs from 6 GB of VRAM) and Maestro
(LTX-2.5 and Wan). It is not free video: it runs on your hardware. Renting
an hourly GPU is not set up.

- Install Pinokio on the GPU machine, and ComfyUI from it (ComfyUI must
  listen on an address the server can reach: its `--listen` option).
- Join the machine to the same tailnet as this server
  ([deploy-tailscale.md](deploy-tailscale.md)).
- Set `LOCAL_COMFYUI_URL` (Settings, or `.env`) to ComfyUI's address on the
  tailnet, for example `http://<machine>:8188`.
- Open Settings → Local hardware. The profiler asks that ComfyUI for
  `/system_stats` and trusts it over every other probe: the GPU it reports
  picks the workflow of the table above (`i2v_wan22_5b` for 8–16 GB,
  `i2v_wan22_14b_lightning` for 16–24 GB, `i2v_ltx2` for 24 GB and more,
  silent), and each workflow's model files are named when missing.

All three templates are still unverified against a real ComfyUI (the
paragraph above): the first run on your machine is their first test, and
`i2v_ltx2` makes silent clips only. **LTX-2.5** would make speaking clips
locally: its open weights carry native audio and need 16 GB of VRAM at least
(24–32 GB for comfort), and Lightricks' licence is free below ten million
dollars of annual revenue (read it before you rely on that). It would need a
new template, `i2v_ltx25`, with the audio decode that `i2v_ltx2` lacks. That
template is not written, because it cannot be tested until the box exists.

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
    season.json                      # the season arc: episodes_planned, arc[], series memory (entries per
                                      #   episode, folded recap/hooks/relationships), audience_feedback[]
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
      script.json                    # scenes, lines, timing (whole frames), hook/cliffhanger/teaser, pays_off,
                                      #   consistency report
      storyboard.json                # shots, transitions, per-scene planning source, whole_frames flag
      assets.json                    # word sources, SFX/BGM picks, each line's take/note/pending, the assets
                                      #   grid's own approval
      assets/
        shots/shot_<NN>.png          # (or .jpg/.jpeg/.webp) one image per shot
        clips/shot_<NN>.stock.mp4    # a stock cutaway's clip (stock_cutaways on); its credits are
        stock_credits.json, .txt     #   in assets/, listed in the metadata pack
        voice/
          line_<NN>.mp3 (or .wav)    # a measured line's audio ("Measure with real voices", or the assets step)
          line_<NN>.json             # its timing sidecar (source, text hash, provider/voice)
      proposals.json                 # this episode's new-character/twist proposals for the episode after it
      cost_ledger.json               # this episode's own rows of the story's cost ledger
      render_manifest.json           # every render command, its inputs and their hashes, per stage
      render_manifest.last_good.json # the baseline the next partial re-render compares against; written only
                                      #   by a render that completed
      episode_final.mp4              # the rendered episode
      subtitles.ass                  # the burned-in subtitles, kept as their own file too
      cover.jpg                      # the hook shot + its hook text, for the metadata pack
      metadata_pack.json             # title/description/hashtags/hook/pinned comment per platform
      render/                        # the renderer's own working folder
        cache/                      # each shot's and the end card's own encode, keyed by content
        in/                         # staged copies of every input image, audio file and font
        fonts/                      # the resolved font, staged for libass
        stems/                      # the audio mix's own dialogue/bgm/sfx stems
        logs/                       # ffmpeg's own stderr, one file per stage
    cache/gen/                        # the generation cache and journal: a repeated request (the same
                                       # seed, prompt and references, or the same voice and text) is
                                       # served from here, never asked again
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

**"Approve episode N's memory first" / "Episode N's memory is stale — write
it again."** — episode N+1's script, storyboard and the fast track each need
episode N's series memory written, approved and current with its script
(step 13, in the Season arc panel — see the walkthrough above). Write and
approve it (or write it again, if the script changed since); the dashboard,
the API and the CLI all refuse the same way, naming exactly which of the
three is missing.

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

**Pollinations pushes back hard during assets** — asking for a whole
episode's worth of shots, Pollinations answers `HTTP 402` above roughly one
image a minute; the assets step paces around this on its own (a pause, then
a fresh round for whatever is still held back) and usually needs nothing
from you. As seen live: a single Pollinations `HTTP 500` in the middle of a
paced round currently ends that round early and leaves whatever was still
queued as failed, rather than being retried again in the same run. Press
**Continue** (run the assets step again) — it picks the failures back up
and repeats nothing already made.

**Pollinations' flux and stylised characters** — its free `flux` model
tends to draw a realistic human face for a character described as a fruit
or a piece of food, and stamps a small "pollinations.ai" watermark on every
image it makes; neither is specific to AI Story's own prompts. A shot with
no reference image to send (`prompt_only` consistency) reuses the
character's own portrait seed, so shots made this way can end up looking
more alike than the storyboard's framing alone would suggest.

**A script under the length window** — the fast track's own script
auto-approval refuses a script outside the template's length window, "over"
or "under" alike. A short one — a run of one-line, single-speaker scenes,
say — can land under it. The fast track stops there: lengthen the short
scenes (edit them, or regenerate a scene with a note asking for more
back-and-forth — a regenerate's own word budget caps how much longer that
can make it) and run the fast track again, or approve the script yourself
from the Script tab despite the warning.

**"Fast track stopped at the script … first-watch check (J1): after 2 repair
passes, N blocking issues remain"** — the first-watch check still finds
something a first-time viewer cannot follow after the step's own repairs,
and the run was asked to stop there (`stop_on_script_issues`; without it, a
v2 episode is approved anyway at this point and the issues are named on the
Review tab).
The issues named are the blocking ones; fix them (edit the scene, or
regenerate it with the fix as the note) and press **Generate episode**
again, or approve the script yourself with **Approve anyway** if you judge
them fine. A script stopped here by the judge's previous version (every
issue blocking, "after 2 repair passes, 6 issues remain") is judged again by
the current one when you press **Generate episode**: no edit is needed
first. To see how the judge reads an episode — what it calls blocking, and
how steady that is from one call to the next — run
`python tools/j1_calibrate.py --story STORY_ID --ep 1 --runs 5 --settings`
(read-only; it prints each verdict and a summary).

**T1 plans too few shots for a one-line scene** — T1 sometimes plans a
single shot for a scene with one short line, and the storyboard step
refuses it (every scene needs 2 to 4). "Plan remaining with T1" (or
Continue, from the fast track) re-plans only the scenes still missing —
a scene that already has shots is left alone.

**A character named for exactly what it is** — K1, the cast step's text
call, checks that a character's own description actually names it, and a
name that is already the plain word for the thing itself (a character
called "Egg", say) can fail that check indefinitely: a description of an
egg has no particular reason to contain the word "Egg". Give the character
a name that is not also its own description, and try again.

**"line shNN has N words, more than an 8 s clip can speak (17 words at
most)"** — on a native-speech story (API or manual), a character line longer
than the longest sold clip's capacity (17 words at 2.4 words/s, the figure
until the probe measures it for real) cannot become one speaking shot, and
the storyboard refuses it naming the line and the fix: shorten it, or split
it into two lines, in the script. This gate runs for every speech story
today — a format built to keep every line under it (the confrontation
format, plan 22 stage 3) is not merged yet; until then, keep an eye on line
length yourself when you write for native speech.

**"estimated $X.XX over the per-episode cap $Y.YY; raise
PER_EPISODE_CAP_USD or use your own clips"** — a native-speech episode on
the API route (see "Native speech (API route)" above) was priced over the
per-episode cap before anything was bought — expected under tight caps (this
host's are $2 / $4 / $10): every API speech price is over a $2 cap. Raise
`per_episode_cap_usd` in Settings → Budget for that story's run (caps are
global; lower it again afterwards if you don't want every story reaching
that high), or switch the story to **"Native speech — your own clips"**,
which buys no clip at all.

**"Waiting for N clips — download the brief"** — a manual-mode episode's
assets step has made every keyframe and narrator voice line it can and is
now waiting for you: open the episode's **Shot list** tab, make the clips on
your own Flow or Higgsfield subscription from the brief's prompts, and
upload each on its shot (see "Your own clips (the manual mode)" above). The
job frees the worker and survives a restart while it waits; the upload that
leaves nothing missing starts it again by itself.

**An upload refused** (a shot's clip, a keyframe, a cast sheet, a place
plate or a prop image on a manual-mode story) — the reason is always named,
never a silent crop or a partial save: not an **MP4 or MOV** for a clip
("download the take from the platform as MP4 and send that"), shorter than
**2 s**, not **9:16 within 2 %** (cropping would cut a character out of
frame, so it is refused rather than cropped), a speaking shot's clip with
**no sound track**, or an image under half the size the app would have made
it at. A step already running on the story refuses an upload with 409 until
it finishes; try again once it has.

**A take marked "approximate"** — a speaking shot's clip was accepted, but
no speech-to-text key is set (Settings → Providers: a **Groq** key, free, or
a **Mistral** key) to check what it actually says against the line, so its
subtitles are split evenly over the clip's planned window instead of timed
to the words actually heard. Add either key and re-upload the clip (or run
the assets step again) for a checked take instead — a **mismatch** or **no
speech** badge, by contrast, means a key did check it and the clip did not
match the line well enough; one retake is yours to try from the shot's
card.

**"Today's paid spending is already $8.38, over the $4.00 daily cap: this
cast (est $0.60 …) would bring it to $8.98."** — read it as three numbers,
not one: $8.38 is what the whole app already paid today (every story and
clip job; Settings → Budget lists the stories), $0.60 is this cast, $4.00 is
the daily cap. Nothing is wrong and nothing was spent. Press **Allow $4.99
more today** (the amount that lets it through, for today only), raise the
daily cap in Settings, or wait for the reset the message names. If the cap
was lowered after the day's spending, Settings warns about it and every paid
call is refused until the reset unless you allow more. See "Budget: today's
limit".

**The refusal names another cap, or "Allow" is greyed** — the job would also
go over the per-episode or the per-story cap, which an allowance for today
does not lift. Raise that cap in Settings → Budget (it is global: lower it
afterwards if you do not want every story to reach it).

**The day resets at the wrong hour, or "spent today" looks off after a
change** — the day is UTC until `BUDGET_TIMEZONE` is set (Settings → Budget
→ Day time zone: `Europe/Paris`). A name that is not an IANA zone is shown
with its reason and the day stays UTC. Changing the zone moves at most one
boundary of the history (two hours for Paris), because earlier days are not
re-split; clear the setting to get UTC back.

**A cast refused on a v2 story before any image was made** — the gate checks
the whole cast, portraits and sheet edits together, before it buys the first
portrait: the sum would cross a cap, so nothing is generated or spent. The
message names the images and the edits and the cap; the estimate showed the
same total. Allow more for today, raise the cap, or cast fewer characters
("Cast", the selection). A legacy story instead stops and asks before its
edits.

**A concept, a cast text or a place refused for naming a brand** — on a
story with a universe, a generated text that names a brand ("names the brand
'coca'; write the generic thing instead") is asked again once with the brand
named and told to use the generic thing; a model that keeps naming it fails
the step like any other validation, and running the step again tries
afresh. A story with no
universe is never checked.

**A subtitle look refused: "contrast ratio … is below the 4.5 minimum"** —
the text or highlight colour would not read against the outline or the box
behind it. Change the colour (or the outline's, or the box's) until the
ratio reaches 4.5; the message gives both colours and the ratio. A save is
also refused with 409 while a render of the story runs: wait for it, or
cancel it.

**ElevenLabs: "the character quota of this ElevenLabs account is used up"**
— or a refused key or a rate limit, each named with its status. A
character's pinned ElevenLabs voice is never swapped for another voice, so
its line fails until the quota resets or you pick another voice for the
character; the free voices are not affected.

**"Switching the clip prompts to action rewrites every clip's prompt: N
current clips … will be marked stale"** — the clip's prompt is part of what
makes a clip current, so a different `prompt_style` makes every clip already
made, uploads included, stale, and the next assets run buys them again (a
manual story asks for them again). The message comes with the patch, before
anything is lost to you; patch the style back to undo it.
