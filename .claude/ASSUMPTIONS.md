# ASSUMPTIONS

## Unconfirmed
- **A-021** — GitHub's `ubuntu-latest` runner has ffmpeg, so
  `tests/test_loudnorm.py::test_a_quiet_clip_comes_out_at_minus_14_lufs` runs in
  CI rather than skipping. UNCONFIRMED (it skips cleanly if not).
- **A-022** — On the Ubuntu container, `Popen.kill` (SIGKILL) ends a job's ffmpeg
  and frees the worker slot as fast as it did on Windows (0.2s / 0.3s measured).
  UNCONFIRMED on the VPS: verified on Windows only.
- **A-023** — The three notebooks run end to end on Colab and Kaggle, including
  the Kaggle dashboard build when npm exists. UNCONFIRMED: verified
  structurally only (every code cell compiles after IPython's transformer,
  exact JSON round-trip, every `main.py` flag declared).
- **A-024** — `tests/test_transcript_dispatch.py::test_bypass_does_not_import_ctranslate2`
  fails on Windows only because ctranslate2 is installed here. It may be a real
  import leak on the transcript path that CI cannot see (its env lacks
  ctranslate2). UNCONFIRMED -- worth a look.
- **A-025** — Camera-switch rendering still works after the render-layer changes
  (DEC-079/080/081, the temp cleanup). UNCONFIRMED: it needs pyannote and an HF
  token. Hybrid, split-screen (face trigger), hook-v2 and edge-glow were verified
  frame-identical; camera-switch shares their imports but was never rendered.
- **A-013** — Output language defaults to the transcript's language (reported by
  the hosted STT, else stopword detection); `output_language` overrides it.
  English titles/keywords/hashtags are still produced alongside. UNCONFIRMED.
- **A-014** — The default clip count stays 7 (DEC-021: no silent change). The
  three-pass analyzer removes the reason it mattered. UNCONFIRMED.
- **A-015** — Platform presets: `tiktok` 15–90 s (target 34), `reels` 15–90 (30),
  `shorts` 15–59 (45), `auto` 20–75 (40), `long` 60–179 (90); cuts snap to
  sentence boundaries. UNCONFIRMED.
- **A-016** — The full image (torch, pyannote, ultralytics, faster-whisper) stays
  the default build because every feature must survive; a slim build is opt-in
  via a build arg. UNCONFIRMED.
- **A-010** — NIM model ids in this project have a shelf life measured in weeks:
  **four** defaults have now died in about six weeks. The current pin is
  `deepseek-ai/deepseek-v4.1-flash`, chosen by live benchmark on 2026-09-21
  against the real Pass-A workload (1.3–2.9s, schema-valid) and defined once, in
  `registry.NVIDIA_DEFAULT_MODEL` (DEC-052). Re-pick with
  `tools/bench_llm.py --nim-shortlist`. Two traps are now proven rather than
  suspected: a model listed by `/v1/models` may answer 404 for a given account,
  and the fast candidates are reasoning models that return `content=null` or
  unparseable prose unless thinking is switched off. UNCONFIRMED (the shelf-life
  estimate; the measurements are facts).

- **A-009** — *(being revisited 2026-09-22: the human states they have or can
  obtain a Groq key, and Settings now has a field for it — DEC-057. Confirm
  on the next real run, then move this to Confirmed or Invalidated.)*
  Groq and Mistral do not reliably offer a usable free API key,
  despite their own documentation describing free tiers on 2026-09-21. This rests
  on the human's own attempt, not on a page we can cite, and it is the reason
  neither is recommended anywhere in the UI. It does not affect correctness:
  both are reachable through the generic `openai_compat` provider, which makes no
  claim about their pricing. Recheck before ever promoting either to a
  recommended provider. xAI is a separate and firmer case: its own pricing page
  confirms the free API tier ended in May 2025.
  *(2026-09-23: DEC-073's refusal names Groq AND Gemini, both of which the default
  chain lists, so a user who cannot get a Groq key still has Gemini. Gemini is the
  one to recommend if A-009 holds.)*

- **A-019** — NVIDIA's 120s probe timeout (DEC-072) is enough headroom for its
  free-tier queue. It rests on five pings over one day with one key (48.9,
  57.0, 49.7, 39.1, 57.4s). The queue is load-dependent, so a busier day could
  exceed it. Recheck with Settings → Test provider chain if NVIDIA starts
  failing preflight again. UNCONFIRMED.

- **A-020** — OpenRouter and Mistral are marked `primary` (fast enough to
  carry the analysis alone) based on their published free tiers. **OpenRouter
  half CONFIRMED 2026-09-24** by `tools/bench_llm.py` on the real pass-A request
  (mistral-small-3.2-24b: 2.5-2.7s on the fixture, 5-13s on real windows). The
  Mistral half is A-026. `primary` is a speed claim, not a price claim.

- **A-026** — `mistral/mistral-small-latest` can carry the analysis. It joined
  the default chain on the human's decision (DEC-088) with no key on this
  project to measure it. The chain test measures it the moment a key is set.
  UNCONFIRMED.
- **A-027** — Groq's `openai/gpt-oss-120b` can carry the analysis. Named as
  `GROQ_DEFAULT_MODEL` but never benchmarked here (no key). Note: gpt-oss models
  returned `content=null` on OpenRouter at small budgets (2026-09-24), a
  reasoning-model trait worth checking first. UNCONFIRMED.
- **A-028** — The Tailscale serve proxy in front of the app does not cut a
  request shorter than the chain test's 290s worst case (DEC-091). On
  2026-09-24 `tailscale serve status` said "No serve config", so nothing
  proxied the app on this box. **Superseded 2026-09-26 (DEC-105):** the app is
  now served on the tailnet at `:8000` with `DISABLE_AUTH=1`, so this
  assumption no longer describes the deployment.
- **A-029** — "No longer available to new users" 404s are per account and
  permanent, so remembering the working model per key for the life of the
  server is safe. Nothing is blacklisted, so a wrong guess costs one fast 404.
  UNCONFIRMED.

- **A-030** — The logo source is `web/dashboard/public/icon.svg`; everything else
  (favicon, the shell's mark) is derived from it rather than kept as a second
  copy. UNCONFIRMED for any export size not yet needed.
- **A-031** — Generation chains stay env/payload-only: there is no per-chain UI
  editor, and the Test button is the only write path from the page. Revisit if
  users start hand-editing `.env` to reorder links. UNCONFIRMED.
- **A-032** — The OpenRouter free vision model is **unmeasured**. Live evidence
  on 2026-09-25: `openrouter/qwen/qwen3.8-27b:free` answered 429 "temporarily
  rate-limited upstream" during the stage-13 chain test. It is in the chain as a
  free fallback, not as something known to answer. UNCONFIRMED until a bench run.
- **A-033** — `extra_hosts: host.docker.internal:host-gateway` works on this
  Docker (29.1), which is what lets a container reach a ComfyUI or Ollama on the
  host. Verified only on this engine version. UNCONFIRMED elsewhere.
- **A-034** — The API model ids behind the friendly link names are as spec §8.7
  lists them. **Live-checked only for the keyed ones**: fal, Gemini TTS
  (`gemini-3.8-flash-lite-tts` confirmed against the real endpoint), pollinations.
  The rest are read from documentation. An unknown id raises a 404 so the runner
  swaps rather than stalling — that is the safety net, not a verification.
  UNCONFIRMED.
- **A-035** — The ComfyUI workflow templates and the stdlib `/ws` reader are
  verified **against a fake server only**. No real ComfyUI has run them: the
  probe on this host reports unreachable. First contact with a real daemon is
  the test. UNCONFIRMED.
- **A-036** — Pollinations' keyless rate limit is unpublished; the configured
  value is a guess that has not been driven to refusal. UNCONFIRMED.
- **A-037** — The appendix prices hold until re-read. `pricing.PRICES_AS_OF`
  carries the date; a stale table yields a wrong *estimate*, never a wrong
  charge — the caps are the real protection (DEC-099). UNCONFIRMED by design:
  this one is expected to rot.
- **A-038** — Spec §14 day-one assumptions that concern phase 0, restated so
  phase 0 does not inherit them silently: Gemini image models have **no free
  tier**; Cloudflare allows roughly **170 images/day** on the free plan; Edge TTS
  is usable **without a key**. Only the last is exercised here (edge answered in
  1.6 s during the stage-13 test). UNCONFIRMED.
- **A-039** — (2026-09-26) The human answered the "finish phase 0" options
  (fix the double-bill first / run the paid test and switch `allow_paid` back off /
  confirm the phone steps) by rejecting the dialog and then saying "go on". Taken
  as approval of the **recommended** options: fix first (DEC-106), and
  `allow_paid` back off after the test. **Not** taken as a yes to spend: the
  est. $0.03 fal call waits for an explicit yes in chat. Partly answered at
  10:00 UTC: no spend now, the paid test is deferred by the human (no budget);
  finish phase 0, then merge and push. The fix-first reading was not objected
  to. UNCONFIRMED for the fix-first part only.

- **A-041** — (spec §14) The free tiers' daily request limits (Gemini Flash-Lite
  ~1,000, Groq 1k, OpenRouter free 50/1,000) are enough for one episode's ≈ 25 LLM
  calls. Phase 1 evidence only: steps 1–4 took 15 calls on Gemini's free tier on
  2026-09-26 with no refusal. UNCONFIRMED for a whole episode.
  **Update (phase 3, stage 13):** the exact shape is E1 + E2 × speakable body scenes + E3 + E4, plus T1 × scenes
  (≈ 20–21 calls for a 60-second first episode), plus any rechecks or regenerations on top. The live Tier-2 walk
  made 23 calls end to end (the full write, one recheck, one scene regenerate, T1 planning), all on Gemini's free
  tier, no refusal — comfortably under the ~1,000/day limit. Still UNCONFIRMED over a full day's use across
  several episodes.
- **A-042** — (spec §14) Prompt-only consistency (locked prompt block + seed reuse)
  is acceptable as an explicit, labelled degraded mode when no reference-capable
  editor is available. Phase 1 only records the choice. UNCONFIRMED.
- **A-043** — (spec §14) `MAX_CONCURRENT_JOBS=1` is acceptable for story steps: a
  step can wait behind a clip render, and the wizard says "queued — waiting for the
  worker". UNCONFIRMED.
- **A-044** — The CLI and a running server do not coordinate writes to the same
  story across processes (the RLock is per process; writes are atomic; the index is
  rebuildable, DEC-110). Acceptable for a single-user tool. UNCONFIRMED.
- **A-045** — The concept library's fields the spec does not give (`main_line`,
  `episode_seed`, `content_flags`, `signature_hint`, archetypes where fewer than
  three characters are named) and **all French text** were authored in phase 1;
  so were `emotion_to_mood` for six styles (fruit_drama's is the spec's). No native
  French review yet. UNCONFIRMED.
- **A-046** — French tokenises at about 1.3 × chars/4 on Gemini. **Not measured**;
  the story caps (DEC-107) are sized with it, and live French replies fit with room
  (C1 ≈ 290–335 of 700). A `countTokens` run would settle it. UNCONFIRMED.
- **A-047** — The `≈N tokens out` figure is chars/4 of the returned JSON, not the
  provider's usage (`run_chain` returns none and `llm.py` stays untouched); the real
  cap is the `max_tokens` sent. UNCONFIRMED by design.
- **A-048** — Keyless Pollinations gives about one fresh image per IP per hour and
  serves repeated prompts from cache. Seen live 2026-09-26: three fresh preview
  images in 3 s, 44 s and 46 s; the same prompts again in 0.2 s each. UNCONFIRMED.
- **A-049** — Tests run from a worktree under `.claude/worktrees/` load the main
  checkout's real `.env` into `os.environ` (`clipping.config` searches upward).
  Phase-1 tests clear the keys they depend on and make no network call; a guard
  (a conftest clearing provider keys) is a follow-up. Recorded as a hazard.

- **A-050** — Gemini's image editors do not honour seeds (`seed_honoured: False` in the adapter); a
  prompt-only or edited sheet on Gemini would not reproduce. Not exercised live (paid). UNCONFIRMED.
- **A-051** — Keyless Pollinations answers a fresh image in ~2–46 s (live 2026-09-26: 2.5 s, 44 s, 45 s, 11 s…;
  repeated prompts from cache in 0.2 s). A cast of 3 + places costs ~10 minutes of waiting. Live 2026-09-28/29,
  keyless pollinations admits about one image a minute and answers HTTP 402 otherwise (T2-F1), occasionally
  HTTP 500. UNCONFIRMED over time.
- **A-052** — The free vision chain (Gemini flash-lite) is available for upload descriptions (live: 2.6 s, $0).
  UNCONFIRMED against quota over a day.
- **A-053** — Eight French Edge voices (5 fr-FR, 3 fr-CA) cap a cast's distinct voices at 8 (MAX_CAST = 8).
  Their gender/age/style tags in `voices.json` are authored (e.g. Eloise "young"). UNCONFIRMED.
- **A-054** — K1 follows a design reference's colours and accessories but bends a reference that conflicts
  with the style (a human-looking girl for a fruit_drama character) toward the style. Seen once. UNCONFIRMED.

- **A-057** — An E-prompt's JSON validity holds up across the free providers. Bench (stage 12, seeded copy, 3
  reps): `gemini/gemini-3.5-flash-lite` E1 0/3 @ 19 s ("expected 8–12", 7 scenes), E2 3/3 @ 8.3 s, T1 3/3 @ 1.5 s;
  `nvidia/nemotron-3.5-lightning` E1 0/3 (6 scenes + `InternalServerError`), E2 3/3 @ 6.4 s, T1 0/3
  (`InternalServerError`, one 300 s timeout). After stage 12b's exact-count fix, E1 re-bench: Gemini 3/3 @ 3.8 s,
  NVIDIA 1/3 (a duplicated cliffhanger, a truncated reply). The live Tier-2 walk: 23/23 LLM calls first-attempt OK
  on Gemini. The fix round's re-check on a copy: T1 had 3/10 first replies rejected (a shot short of the range, or
  a tag not among the scene's subjects), one scene (s04) failed twice before "Plan remaining" finished it on a
  later call. Reading across all of this: Gemini's free tier is reliable enough to carry a whole episode; NVIDIA's
  free tier is not usable for story calls at all (see A-062). UNCONFIRMED as a general free-provider claim — it
  holds for the one free provider actually used.
- **A-059** — `serial_90s_v1`'s numbers (window 75–100, target 85, tighten above 95, body 5–10 × 6–10) are
  authored, mirroring `serial_60s_v1`'s shape. No 90-second episode was written this phase — every Tier-2 walk
  used the 60-second template. UNCONFIRMED.
- **A-061** — The free Gemini flash-lite LLM sometimes drops a French elision's apostrophe and occasionally
  garbles a diacritic in its raw reply (seen live: "trâne" came back "tr¤ne"). `prompts.repair_fr_elisions` fixes
  the first deterministically after every reply (DEC-144); the second has no code path treating it at all (F2).
  Seen on the live walk and again on the fix round's copy. UNCONFIRMED how often either happens over more text.
- **A-062** — The NVIDIA free link never serves a story call: its registry default timeout is 330 s, above
  `STORY_CALL_BUDGET_SECONDS` (300 s), and the predictive deadline check (`providers/llm.py`, DEC-020) refuses to
  start a request whose timeout would outlast the budget — so the link is skipped before any request, even with
  `--allow-slow-chain` (seen 2026-09-27 on a keyless-Gemini CLI run: every link failed before a model was
  contacted). The bench (which calls a link alone, outside the story budget) also saw plain
  `InternalServerError` on E1 and T1. Not chased this phase — a hazard, not a fix. UNCONFIRMED whether a shorter
  NVIDIA timeout or a longer story budget is the right cure.
- **A-063** — Gemini's free TTS answered 429 once in 5 lines during the stage-13 measurement walk; the existing
  retry in the TTS path succeeded on the second attempt. UNCONFIRMED whether that rate holds over more lines or
  more days.
- **A-068** — The 15 `assets/bgm/` tracks (Clips mode, Pixabay-style names, no licence record in the repo) may be
  mapped into `bgm_index.json` for AI Story; their licence is recorded as "shipped with Clips, source unrecorded".
  *Human's choice at phase-4 CLARIFY (2026-09-28): "Self-made SFX + existing BGM".* UNCONFIRMED as a licence fact.
- **A-072** — A Gemini/OpenAI image 4xx is unbilled; a 5xx or timeout may be billed. UNCONFIRMED.
- **A-073** — ubuntu-24.04's apt ffmpeg stays at 6.1.1; `-threads 1` makes x264 in the golden profile
  thread-independent. UNCONFIRMED over time: CI's `6.1.1/x86_64` golden key was recorded at stage 7 and needs
  re-checking whenever the runner image's ffmpeg build moves.
- **A-074** — The mood → BGM track mapping is authored. UNCONFIRMED: the EN episode's `warm_family` mood picked a
  romantic-piano track under what plays as a noir mystery — a taste point noted for the human's watch, not a code
  defect.
- **A-078** — M1's per-platform length and hashtag limits, as of 2026-09. UNCONFIRMED: the limits are authored,
  not sourced from each platform's own current documentation.

- **A-080** — No public deployment besides the Caddy `domain` profile and the Kaggle notebook's ngrok tunnel relied
  on the generated `data/api_token`; an install exposed another way (a hand-made reverse proxy, a port opened to the
  internet) becomes open on upgrade, with only the startup banner and the CHANGELOG to say so. UNCONFIRMED (DEC-173
  accepts the risk).
- **A-081** — Making `tools/rzclips-fetch.py` run without a token (it refused before, and pointed at
  `/app/data/api_token`) is part of "remove all access restrictions", done in auth stage 2 though the approved plan
  did not list it. UNCONFIRMED: named in the stage-2 report for the human to veto.


- **A-083** — The suite is safe under pytest-xdist (`-n 4`): tests share no process-global state across workers that
  changes an outcome. Verified once on phase 5's stage-9 tree (identical counts serial vs parallel in both
  environments, 0 failed). UNCONFIRMED over time: a test that passes serially but fails or flakes in parallel is a
  finding (logged as flaky per Section 9), never re-run until green; the serial command stays the tiebreaker.

- **A-084** — E4 on the free Gemini flash-lite link judges hook payoffs with variance, and the "approve anyway"
  checkbox is the accepted way past a false `hook_payoff` issue (T2-P5-F6, no code by the human's call, 2026-09-30).
  Live evidence, FR `b1104ec66b05` ep 2: E1 marked `s02` (setup) `pays_off` "Kiwilo va-t-il trahir Mangella dès ce
  soir ?"; its first line is "Tu m'as trahie au bord de l'eau, Kiwilo !" — a direct answer — yet E4 flagged `s02`
  `hook_payoff` ("ne paie pas correctement le hook … intégrer une réplique explicite") across 2 regenerate rounds, a
  text-only `pays_off` edit and the re-check after stage 13(e)'s line edits (report rev 6, `passed: false`), and the
  script was approved anyway (`approved_anyway` 11:21:30 UTC). Before F7 (`7b9cc92`) part of the same flags were E1
  putting a payoff on the hook scene, which E4 rightly refused. UNCONFIRMED: whether a stronger free judge or a
  second E4 opinion would stop the false flags — measure with `tools/bench_llm.py` before any prompt change.

- **A-085** — `anime` (v1) holds on the free route, except the look (stage 14, 2026-09-30, EN `detective_dawn`,
  story `dcc0998db8ae`, Rin + Kaito, 1 place, prompt-only). Episode 1: 52.7 s (under the 55–75 s window, the known
  short-English-script gap), −14.12 LUFS / TP −2.18, 1080×1920 30 fps, 9 scenes / 14 lines / 18 shots, render 147 s.
  Subtitles word_pop in **Bangers** from the shipped file (no fallback). Motion push_in, pan 4 %, no modifiers or
  overlays. **Held:** both characters stay recognisable in every shot (Rin's red braid and olive trench, Kaito's
  black coat and locket). **Drifted:** pollinations draws semi-realistic faces, not anime; the note-covered apartment
  comes out as a generic corridor; keyless pollinations stamps its logo bottom-right. The run found T2-P5-F10/F11
  (fixed) and the Gemini TTS daily quota (Rin moved to Edge Aria). UNCONFIRMED: the look on a stronger image link.

- **A-086** — `cinematic_real` (v1) renders right but its French scripts run short (stage 14, 2026-09-30, FR
  `last_bus_3am`, story `560e901c1b3d`, Chauffeur Sam + Le Passager, Edge voices). Episode 1: **41.3 s** (far under
  55–75 s: 13 lines over 10 scenes; the fast track only warns on "under"), −14.19 LUFS / TP −2.37, 20 shots, render
  123 s. Subtitles two_line in **Bebas Neue** from the shipped file; French accents burn correctly (ARRÊT, ÉTRANGE,
  VERROUILLÉE); highlight #D98E04; "Généré par IA" label. Motion push_in + handheld, pan 3 %, no overlays. **Held:**
  the photographic night-bus look; the passenger's trench coat. **Drifted:** the driver's face and cap change shot
  to shot (prompt-only); shots mix pollinations (logo) and Cloudflare (no logo, square native size, cropped to 9:16).
  12 fast-track presses on pollinations before Cloudflare worked, 1 after. UNCONFIRMED: why E1–E3 wrote so few lines
  (compare with the FR `fruit_drama` episodes at 58 s before any length fix).

- **A-087** — `cartoon_flat` (v1) renders right, but **mixing image providers inside one episode breaks the look**
  (stage 14, 2026-09-30, EN `two_minutes_heroes`, story `979c8376e43e`, Captain Obvious + Miss Overthink). Episode
  1: 56.9 s (in the window), −14.49 LUFS / TP −2.32, 10 scenes / 15 lines / 20 shots, render 145 s, one press after
  the Cloudflare fix. word_pop and the hook overlay ("GIANT TOASTER ATTACKS CITY!") in **Luckiest Guy** from the
  shipped file. Motion hold, pan 4 %, no overlays. **Held:** the flat, bright city-square look on every Cloudflare
  shot. **Drifted:** Cloudflare's flux-1-schnell draws the heroes as flat mascot shapes (a red triangle, a yellow
  block with glasses and a cape) while the pollinations shots draw realistic humans — prompt-only consistency does not
  survive a provider switch mid-episode (the chain falls through per shot). UNCONFIRMED: whether pinning one image
  provider per episode (or per story) is worth a setting — a phase-6 candidate.

- **A-088** — `claymation` (v1) holds best of the five on one provider (stage 14, 2026-09-30, FR
  `clay_town_confessions`, story `04feb539840f`, Maire Pâton + Boulangère Pim, Edge voices). Episode 1: **45.2 s**
  (under 55–75 s: 12 lines over 9 scenes), −14.29 LUFS / TP −2.30, 18 shots all on Cloudflare, render 104 s.
  two_line in **Chewy** from the shipped file, French accents correct (immédiatement, maléfique, calomnie), speaker
  accents readable. Motion hold + jitter_stopmotion, pan 4 %, no overlays. **Held:** the clay miniature-town look in
  every shot; the mayor (bald, grey suit, "MAYOR" sash) in every shot; the baker's pink apron and rolling pin.
  **Drifted:** the baker's hair colour and age now and then (prompt-only). **Friction:** 12 fast-track presses —
  T1 refused scene s02 ten times ("the reply failed validation twice") before it passed (the known T1 under-shoot
  follow-up), then one E4 approve-anyway. The human watches this one. UNCONFIRMED: the short French scripts
  (A-086) share one cause.

- **A-089** — `storybook_watercolor` (v1) holds well (stage 14, 2026-09-30, FR `grandmas_rules`, story
  `14ff154d3bff`, Mamie Nell + Tomi, Edge voices). Episode 1: **48.0 s** (under 55–75 s: 13 lines over 10 scenes),
  −14.21 LUFS / TP −2.37, 21 shots, render 154 s, **29.9 MB** (the paper_texture overlay roughly doubles the bitrate
  of the others' 10–17 MB). two_line in **Patrick Hand** from the shipped file + the hook overlay ("NE JAMAIS
  OUVRIR"); French accents correct. Motion pan_lr, pan 5 %, overlay paper_texture. **Held:** the watercolor cottage
  (fireplace, bookshelves) across every shot; Mamie Nell (grey bun, round glasses, patchwork shawl) and Tomi (blue
  beret) recognisable. **Drifted:** flux paints a fake artist signature now and then. **Friction:** the fast track
  refused the script 21 times as "under its length window: 54.3 s estimated — under 55–80 s", which a Continue can
  never pass (it asks for an edit or a manual approval); approved by me through the API like the UI's button, then
  3 presses. UNCONFIRMED: a "fast track stops at the length gate" should stop the automatic Continue loop (driver
  rule, not app code).

- **A-090** — Phase 5's new prompts hold JSON on the free Gemini flash-lite link: the stage-13 bench (scratch copy,
  the steps' own code) gave S3 3/3, F1 3/3, N1 3/3 and episode-2 E1 3/3 first-try valid (12/12; S3 1.3 s, F1
  1.0–2.4 s, N1 1.5–1.8 s, E1 4.1–4.6 s). UNCONFIRMED on groq/mistral (no keys here) and over more runs.

- **A-091** — Gemini's free TTS (`flash-lite-tts`) is unfit for a whole cast on the free route: it read a re-voice
  note aloud (T2-P5-F9, fixed: notes are recorded, never sent) and its free tier allows **10 requests a day per
  model** (live 2026-09-30: `GenerateRequestsPerDayPerProjectPerModel-FreeTier 10`, spent by the stage-13 re-voices,
  samples and one cast); DEC-168's pacing cannot help a daily quota. Edge has no daily cap. UNCONFIRMED: the voice
  picker should prefer Edge on the free route (phase-6 follow-up).

- **A-092** — Keyless pollinations serves about one image a minute per IP (A-048/A-051), and the paced rounds (DEC-168,
  cast since DEC-190) cope with one run at a time; **runs in parallel starve each other** (each round sees no progress
  and gives up) — stage 14 had to serialise until Cloudflare worked. The places step still has no pacing (its single
  plate needed up to 4 presses). Keyless images carry a pollinations logo bottom-right.

- **A-093** — Edge's voice list drifts: `en-US-DavisNeural` was retired (T2-P5-F11, replaced by AndrewNeural). The
  catalogue has no live check; UNCONFIRMED that the other 16 Edge voices stay (a `edge_tts.list_voices()` probe before
  each phase's Tier-2 is cheap).


- **A-095** — Cloudflare Workers AI `flux-1-schnell` (keys added 2026-09-30) is fast and free on this account: ~2 s
  per image, no logo, square native size cropped to 9:16; it takes no seed (T2-P5-F13) so prompt-only shots are not
  reproducible there, and it draws cartoon characters as mascot shapes where pollinations draws humans (A-087). The
  app counts it against rpd 170 (the free 10,000 neurons); a 401/403 no longer burns a slot (T2-P5-F12). UNCONFIRMED:
  the real neuron cost per 9:16 image.

  **CORRECTION 2026-10-01 (phase 6, T2-P6-F1):** the still path does NOT crop a square image to 9:16. `shot_argv` runs `scale=4320:-2` then `zoompan … s=1080x1920`, which STRETCHES it (a centred 32×32 square came out 364×648). So every Cloudflare still has been rendered vertically stretched since 2026-09-30. Clips now cover+crop (`filtergraph._cover_fill`); the still stretch is a separate decision (the human's call).
  **Fixed 2026-10-01 (phase 6 stage 13b, `3841a1d`):** a still more than 2% off 9:16 is centre-cropped to an exact
  9:16 before the scale; the stored 1:1 stills re-render unstretched. Still UNCONFIRMED: the neuron cost.
- **A-096** — DEC-176's "CI env" run is not a faithful copy of CI: `PYTHONNOUSERSITE=1` hides only the user site, so
  the host's **system** site-packages (PIL among them) stay importable, while CI installs pytest alone. Phase 5's
  three two-word font tests passed here for weeks and failed only in CI (T2-P5-F14, fixed `f06a299`). The faithful
  replica is the app image's Python 3.11 with `python -S` and pytest on `PYTHONPATH`, run as the host user
  (`docker run --user $(id -u):$(id -g) -e HOME=/tmp -v <clone>:/src -v /tmp/cilibs:/cilibs:ro -w /src
  -e PYTHONPATH=/cilibs --entrypoint python opensource-clipping-better-backend -S -m pytest`); only its
  `test_the_data_directory_is_tracked_but_its_contents_are_not` fails there (git ownership of the mounted clone).
  UNCONFIRMED: whether phase 6 swaps DEC-176's CI-env command for it (the human's call).
- **A-097** — No provider needs its credential header to follow a redirect **to another origin** (DEC-195).
  - fal's media download already sends no header.
  - Phase 6's Veo `_download` sends the key only to `generativelanguage.googleapis.com`.
  - A signed-URL or CDN host authenticates through its URL.
  - If a provider ever answers 401/403 after a cross-origin 30x, this assumption is the first suspect.
  - UNCONFIRMED: provider behaviour, not visible from the code.
- **A-099** — A-097's twin for the two callers outside the transport (DEC-196): neither Groq's and Mistral's
  transcription endpoints nor Pexels' video search need their key to follow a redirect **to another origin**.
  - A same-origin redirect still carries it.
  - Pexels' CDN download never had a key.
  - If hosted STT or the B-roll search ever answers 401/403 after a cross-origin 30x, this assumption is the first
    suspect.
  - UNCONFIRMED: provider behaviour, not visible from the code.

- **A-101** — `fal/ltx-2-fast` (`fal-ai/ltxv-2/image-to-video/fast`) cannot make vertical video.
  - Its output is locked to 16:9 at 1080p or larger, for $0.04 a second with audio.
  - Its `fal-ai/ltx-2` twin was deprecated on 2026-08-15 for LTX-2.3.
  - LTX-2.3 fast (`fal-ai/ltx-2.3/image-to-video/fast`) costs $0.06 a second at 1080p and offers `aspect_ratio`
    "9:16", fps 24/25/48/50 and `generate_audio`.
  - Phase 6 stage 3 moves the default-chain link there, as the spec's §8.7 foresaw.
  - UNCONFIRMED: LTX-2.3's clip lengths and its price at the smallest 9:16 size (stage 3 re-reads the page).
- **A-103** — `gemini/veo-3.1-lite` (`veo-3.1-lite-generate-preview`), read on 2026-09-30:
  - **Price:** $0.05 a second at 720p; $0.08 at 1080p (8 s only). Audio is always on and included. No free tier.
  - **Clips:** 4/6/8 s, 9:16 and 720p supported.
  - **Call shape:** REST `predictLongRunning`, then poll the operation until `done`, then fetch
    `response.generateVideoResponse.generatedSamples[0].video.uri`.
  - UNCONFIRMED until the live Veo shot:
    - the image field shape (`{bytesBase64Encoded, mimeType}` by a forum thread and a GitHub PR, not by Google's
      page);
    - whether `durationSeconds` is a string or a number;
    - `negativePrompt` support (undocumented);
    - whether the seed is honoured ("not deterministic" per the docs).

- **A-104** — The three local ComfyUI I2V templates (`i2v_wan22_5b`, `i2v_wan22_14b_lightning`, `i2v_ltx2`) are
  verified **against a fake ComfyUI server only**, the same way A-035's image templates are: no real ComfyUI has
  run them, and the probe on this host reports unreachable (no GPU on this deployment, the human's standing
  choice — see A-035). First contact with a real daemon is the actual test. UNCONFIRMED.
- **A-105** — A Tier-3 kept clip's own lip movement is expected to mismatch our own subtitle and dialogue timing:
  the clip's native audio is kept verbatim (DEC-210) while that shot's subtitles, and every other shot's lines,
  still come from our own TTS. Not measured live — Tier 3 is proven by tests only this phase (budget; no model
  with usable native audio was run against a real shot). UNCONFIRMED how large the mismatch reads in practice.
- **A-106** — fal's and Google's own billing views are expected to lag behind this app's booked ledger rows by
  more than the time a clip itself takes to generate. Measured generation time, Tier-2 walk step 5/9: a seedance
  clip answered in 30.9 s, a kling clip in 70.2 s (both submit to a usable file). Walk step 10 (pending) has the
  human read fal's and Google's billing pages against the ledger later, precisely because an immediate read may
  not yet reflect a request that already ran. UNCONFIRMED how long the billing-side lag actually runs.
- **A-107** — Whether Google bills a Veo clip's audio track (always on per A-103, with no way to turn it off)
  separately from its video seconds, or folds it into the one per-second price, is unconfirmed: Veo was never run
  live this phase — the walk's one Veo shot ran on fal/kling-2.5-turbo-std instead, the human's call (DEC-218).
  UNCONFIRMED.
- **A-108** — `fal/seedance-1-pro-fast`, the cheapest link in the default `VIDEO_CHAIN` and the one the walk's own
  story A shot used, carries no audio track at all (A-100). So a Tier-3 shot whose sticky video link resolves to
  seedance always falls back to rendering as Tier 2 with its own TTS lines (DEC-210's no-sound-track case) — never
  a failure, but never native audio either, on the link most stories will actually reach first. The estimate does
  not warn about this ahead of a run. UNCONFIRMED whether that is clear enough without a UI hint or a line in
  these docs (stage-10 follow-up).
- **A-109** — fal does not bill a queued request whose input fails to download. One sample: A-071's probe
  `01a0f63e…` (queued, status COMPLETED, result HTTP 422 `file_download_error`) is absent from fal's usage export
  (2026-10-01). The app still books such a request at submit (DEC-153), so it over-books by that request's
  estimate ($0.044 on 2026-10-01). UNCONFIRMED: whether a failure after generation started (a model error, a
  timeout) is billed.

- **A-110** — (phase 7) seedance-1-pro-fast's prompt length limit is not published; the v2 clip prompt is kept ≤ 80 words. **Partly confirmed
  2026-10-01** (W-mid): three clips with 56–77-word prompts were accepted and rendered; the limit itself stays unpublished.
- **A-111** — (phase 7) `fal-ai/bytedance/seedream/v4.5/edit` ($0.04, up to 10 references, output ≤ 4 MP) exposes an
  `image_size` that reaches a 9:16 portrait near 1 MP, a `seed` and a prompt length that holds 220 words; its negative
  prompt field is absent or ignored. Read from fal's OpenAPI schema in stage 2a (2026-10-01): `image_urls` ≤ 10, `image_size` presets or a custom size
  of at least 2560×1440 pixels and ≤ 4096 a side (so 1440×2560 is used), `seed`, no `negative_prompt`, no stated
  prompt length. Schema facts read. **Confirmed in use 2026-10-01** (W-mid: 9 keyframes at $0.04, 1440×2560, with
  references); seedream-4.5 text-to-image and edit also made the sheets, plates and props (stage 2c).
- **A-112** — (phase 7) nano-banana-2 / -lite have no negative prompt and no stated prompt limit; positive constraint
  phrasing ("Clean frame: no captions …") is honoured. **MOOT** under DEC-235 (fal only; nano-banana unused by default).
- **A-113** — (phase 7) the project behind `GEMINI_PAID_API_KEY` has the Gemini image models enabled and billed. **MOOT** under DEC-235
  (the human: no money on the Gemini API).
- **A-114** — (phase 7) the NIM model picked by the free `tools/bench_llm.py` run writes valid French JSON within the
  330 s timeout at ≤ ~420 output tokens. **Partly confirmed 2026-10-01** (free bench, DEC-224): with thinking off,
  nemotron-3-super writes valid FR/EN E1/E2/T1 JSON in 1–23 s about 65 % of the time (failures: word-cap overruns),
  nemotron-3-ultra in 11–70 s but often HTTP 500. Writing quality is judged only by reading 3 samples; the walks confirm.
  W-mid (2026-10-01): NIM wrote in story calls only after the DEC-224 amendment (per-model timeouts), with Gemini taking
  over on NIM's 503/500s; the acceptance walk judges the writing.
- **A-115** — (phase 7) openrouter `mistralai/mistral-medium-3.1` is $0.40 in / $2.00 out per M tokens ($0.44 / $2.20 on
  the EU host), read 2026-10-01 at openrouter.ai/api/v1/models/mistralai/mistral-medium-3.1/endpoints. UNCONFIRMED by a bill.
- **A-116** — (phase 7) a 150 ms floor per word_pop card is legible on a phone (31 % of story A's cards were under it). UNCONFIRMED.
- **A-117** — (phase 7) the J2 keyframe judge on the free vision chain has useful recall and few false positives;
  "approve anyway" stays as the escape. UNCONFIRMED.
- **A-118** — (phase 7) seedance keeps a character's identity over a 5–12 s clip when the prompt ends with the
  stays-still clause. UNCONFIRMED until the walks.
- **A-119** — (phase 7) an every-shot episode on the Quality preset costs ≈ $1.70–1.75 (≈ 60 s of seedance 720p $1.32,
  ≈ 0.5 s ceil waste per shot, 8 keyframes $0.32), under the $2 episode cap. Computed from `pricing.py` by
  `media_policy.preset_estimate` (DEC-232): $1.73 an episode, $0.56 once per story. UNCONFIRMED by a bill until the
  acceptance walk.
- **A-120** — (phase 7) 6–10 beat clips of 5–12 s read better on a phone than 18–20 cuts of 1–6 s. UNCONFIRMED until the human's watch.
- **A-121** — (phase 7) Gemini's 9:16 output at 1K is about 768×1376 (inside DEC-217's 2 % tolerance), hence the v2 source crop. **MOOT** under DEC-235 (keyframes on seedream-4.5-edit at 1440×2560, an exact 9:16).
- **A-122** — (phase 7) the v2 prompt inputs fit their budgets (E1v2 2400, E2v2 2200, E3v2 2900, T1v2 2000 tokens by the
  DEC-138 method) with the context-builder slices. **SUPERSEDED** by the measured budgets of DEC-228 part 3 (E1v2 2870,
  E2v2 2420, E3v2 3370 → 3380 with DEC-230's hook ask, in) and DEC-227 (T1v2 2020), each pinned by a budget test.
- **A-123** — (phase 7) 8 of 9 stored stories are `prompt_only` because the edit chain never runs while `allow_paid` is
  off, so the cast step offered the labelled switch (DEC-117) and the human took it. **CONFIRMED 2026-10-01** by the stage-2a scoping read: `b1104ec66b05/activity.log:144` "Kiwilo turnaround needs an
  editor: No link of IMAGE_EDIT_CHAIN … allow_paid is off … switch the story to prompt-only consistency", then `:208`
  prompt-only; the control story `ab8fc500173e` had `allow_paid` on and kept `references`.
- **A-124** — (phase 7) the hook shot's 3–6 s exception to the 5–12 s shot rule is acceptable to the human (accepted with
  the plan's approval on 2026-10-01). Confirmed by that approval; kept here so the template's exception has a home.
- **A-125** — (phase 7, DEC-236) fal's Platform API `GET https://api.fal.ai/v1/models/pricing?endpoint_id=<id>` with
  `Authorization: Key` answers 200 with `prices[]` rows `{endpoint_id, unit_price, unit, currency}` for a good key, 401
  or 403 for a refused one, and bills nothing; Gemini's `GET /v1beta/models/{model}` answers a bad key with 400
  `API_KEY_INVALID` ("API key not valid"). Read from fal's docs as quoted by a web search and the Gemini API reference;
  this cloud session could not reach either host. `video.check_key` reads the answer leniently (a 200 without a price
  row is "no_model"). UNCONFIRMED until the human's first "Ask the providers (free)".
- **A-126** — (phase 7 follow-up, DEC-240) the prompt size limits of `clipping/providers/prompt_limits.py`, read from
  the vendors' docs as quoted by web searches (this cloud session could not reach fal.ai or Google). Published (V):
  fal Kling 2.5 turbo 2500 characters (the same cap on `negative_prompt`); fal LTX-2 fast 5000; Veo 3.1 1024 tokens;
  Cloudflare flux-1-schnell 2048 characters. Ours (U): fal LTX-2.3 fast 5000 (assumed equal to LTX-2; the key check
  reads its own schema); fal Seedance 1 pro fast 1500 characters (unpublished; 56–77-word prompts accepted, A-110,
  about 3× that); fal Seedream 4 edit / 4.5 / 4.5 edit 3000 characters (unpublished; 2× the confirmed 220 words,
  A-111); Gemini nano-banana 2 / lite 8192 tokens (a 32768-token context less 14 reference images, A-112); OpenAI
  gpt-image-2 32000 characters (gpt-image-1's published limit); Gemini flash-lite TTS 8192 tokens; Pollinations 4000
  URL-encoded characters (v1 prompts of 1900–3000 encoded were served, so not "under 2000"); FLUX's T5 window 512
  tokens (a word budget, never a refusal). UNCONFIRMED until "Ask the providers (free)" reads each fal video link's
  `prompt.maxLength` (or "not published") into `data/provider_limits.json`.
- **A-127** — (phase 7 follow-up, DEC-242) Veo 3.1 lite follows a text audio brief: it gives a clip the place's
  ambience and the named effects and keeps voices, music and narration out when told "no music, no voices, nobody
  speaks or sings, no narration" (it has no negative prompt, A-103); and the ambience mix constants (gain 0.5 on the
  SFX bus, duck 0.06 / 3 / 50 ms / 600 ms, 80 ms fades) sit the clip's sound under the lines without pumping.
  Measured only on the synthetic test episode (−9.6 dB under a line). UNCONFIRMED until the human hears a Veo
  episode on the phone.
- **A-128** — (phase 7 follow-up, DEC-243) every VISION_CHAIN link accepts 6 images in one request (J2's two keyframes
  and up to 4 identity sheets), and Seedream 4.5 edit follows an "Image N is the previous shot of this scene" role
  that sits in the middle of its reference list (after the sheets and the plate). UNCONFIRMED until the human's walk
  (the J2 verdicts and the keyframes of a two-shot scene).
- **A-129** — (phase 7 follow-up, DEC-244) Gemini TTS's end-of-clip static ("crshhh") is broadband, near-white noise
  (ZCR ≥ 0.25) of roughly 250–900 ms, either glued to the last word or after a short gap, and never inside speech —
  so `tts_tail`'s detector cuts it and leaves every word-final fricative. Designed from the forum reports and proven
  on synthetic signals only (no real sample reached this session). UNCONFIRMED until the human runs `--ai-story
  voice-tails STORY_ID --ep N` on a real episode before and after an assets run, and hears the result.
- **A-130** — (phase 7 follow-up, DEC-246) a fully animated v2 episode fits the one click's plan-derived time budget:
  3600 s + 600 s a clip (a Veo or seedance clip polls for at most 10 minutes) + 120 s a J2 check + 540 s a redraw,
  4 hours at most (24 clips worst case); the predictive checks still stop early and Continue resumes. UNCONFIRMED
  until the human's first "Generate episode" on a Veo story (the feed announces the budget it derived).
- **A-131** — (phase 7 follow-up, DEC-247) an image model reads a keyframe prompt of up to ~320 words whole and a
  video model a clip prompt of up to ~160 (220 with the ambience brief) without diluting the picture: the keyframe
  ceiling is FLUX's 512-token T5 window (the widest text window published whole); Seedream 4.5 edit's and
  nano-banana's windows are unpublished, so 320 is a tunable, and the measurements (DEC-247) show the core eats most
  of it. UNCONFIRMED until the human's walk compares the richer keyframes with phase 7's 220-word ones (looks hold,
  the mood reads, no lettering).
- **A-132** — (DEC-248) J1 version 2 told the format and asked for a severity calls the human's six issues mostly
  minor (the hook's premise, the cliffhanger's reveal, a backstory) and keeps blocking only what a first-time viewer
  cannot follow, and its re-check after a repair converges; a real comprehension failure (who the main character is,
  what they want) is still called blocking. UNCONFIRMED until the human runs `tools/j1_calibrate.py` on the stuck
  episode (pass rate, blocking issues per run, their stability) and presses Generate episode again.
- **A-133** — (DEC-249) On the live story d0ee5ebd745d, sh03's redraw with its 18-word correction note was the only
  cause of "326 words over the budget of 320" (verified: the stored prompt is 308 words and built to
  fal/seedream-4.5-edit); the re-fit drops at most the lowest context layers of such a shot (between, when, bearing,
  since, mood) and never a role, the beat, the staging or the constraints, so a redrawn keyframe is not visibly
  poorer than its first draw. UNCONFIRMED until the human presses Generate episode again on that episode and the
  redraw passes J2 (the feed shows the ℹ️ line with the two word counts).
- **A-134** — (DEC-250) Gemini's prebuilt TTS voices speak a French line about 1.35x longer than
  `timing.estimate_line` says (measured on d0ee5ebd745d ep 1: 18 lines, 1.16–1.80, mean 1.35, speech alone); Edge
  voices speak it at the estimate (the rate was measured on them). `voices.SPEECH_OVERRUN["gemini"] = 1.35`.
  UNCONFIRMED beyond that one episode and language: confirmed when the next Gemini-voiced episodes' measured
  scene lengths stay within the planned clip (no "slowed to cover it" line past ~1.1x), refuted if English or other
  voices drift differently (then a per-language or per-voice table).
- **A-135** — (DEC-250) A clip slowed to at most 1.25x (0.80x speed) to cover its shot reads as natural motion on
  the phone for these cartoon clips, better than a frozen last frame or a hard stop. UNCONFIRMED until the human
  watches an episode whose feed says "is slowed to cover it" (the live story's sh03 would play at 0.85x).
- **A-136** — (DEC-251) Gemini's end-of-line artifact is always the same shape — a short burst louder than the
  speech after a near-silent gap, to the file's end — so `burst_at_end` (≤ 0.20 s, within 3 dB of the speech level
  measured without it) cuts it on every line and never a shouted last word. UNCONFIRMED beyond the 18 lines of
  d0ee5ebd745d/ep01 (all caught offline): confirmed when the human hears no "crshhh" on the re-rendered episode and
  no word end is clipped; `voice-tails <story> --ep 1` lists each cut.

## Confirmed
- **A-082** — fal.ai as measured in the paid test (2026-09-29, DEC-174):
  - 35 requests with 0 failures: 13 `fal-ai/flux/schnell` (4–13 s each; booked $0.0028 at 720x1280, $0.0018 for a
    style-preview image) and 22 `seedream-4-edit` (30–40 s each; $0.03).
  - Total booked $0.6934, equal to `spend.json`.
  - A request cancelled 80 ms after its submit was resumed by poll on Continue and delivered, with no re-buy.
  - flux-schnell draws a fruit-head descriptor as the fruit (a pickle detective); pollinations drew human faces
    (A-058).
  - fal's dashboard, read by the human on 2026-09-29, shows a **cost estimate of $0.70 and 32 requests** over the last
    7 days. **The cost matches** once flux-schnell is billed per whole megapixel (DEC-175: 13 × $0.003 + 22 × $0.03 =
    $0.699). ~~The count is 3 short of our 35 and stays UNCONFIRMED.~~ Settled below.
  - A-071 (is a queued request that later *fails* billed?) was settled on 2026-10-01: see A-109.
  *Confirmed 2026-10-01 by fal's usage export (2026-09-24…10-01, the human):*
  2026-09-29 bills flux-schnell 13 megapixels ($0.039) and seedream-4-edit 22 images ($0.66) = $0.699, so all 35
  requests were billed; the dashboard's "32 requests" was a display count. (A $0.001 `any-llm` playground request that day is not the app's.)
- **A-094** — fal in the capped re-edit test (14b(e), 2026-09-30): one `seedream-4-edit` request, journaled and booked
  at submit ($0.030 = `pricing.py` = fal's table), answered in 29.4 s; the caps are cumulative (an episode already
  holding $0.60 refused a $0.03 call under a $0.10 episode cap, so a capped test on a story with earlier spend sets
  the **daily** cap as its hard limit and episode/story caps as spent + headroom).
  *Confirmed 2026-10-01 by fal's usage export (2026-09-24…10-01, the human):*
  2026-09-30 bills seedream-4-edit 1 image, $0.03 — the booking.
- **A-100** — `fal/seedance-1-pro-fast` (`fal-ai/bytedance/seedance/v1/pro/fast/image-to-video`), read on 2026-09-30:
  - **Price.** Billed by tokens at $1.00 per million, where tokens = width × height × 24 × seconds / 1024. At 720×1280
    that is $0.0216 a second, kept as 0.022.
  - **720p must be sent explicitly.** The endpoint's default is 1080p, at $0.0486 a second.
  - **Clips:** 2–12 s. No audio, no negative prompt. Takes a seed.
  - **Request fields:** `image_url`, `prompt`, `duration` (string), `aspect_ratio`, `resolution`, `seed`,
    `camera_fixed`. The output is `video.url`.
  *Confirmed 2026-10-01 by fal's usage export (2026-09-24…10-01, the human):*
  the 3 s clip (request `01a0f63b…`, output 704×1248, 73 frames) billed 62,634 tokens = $0.062634 = width × height
  × frames / 1024, at the OUTPUT's own size (not 720×1280) with frames = 24 × seconds + 1. The app's 0.022/s booked $0.066 (+5 %, conservative). The day's seedance amount is
  exactly this clip, so the A-071 probe was not billed (A-109).
- **A-102** — `fal/kling-2.5-turbo-std` (`fal-ai/kling-video/v2.5-turbo/standard/image-to-video`):
  - **Price:** $0.21 per 5 s, then $0.042 per extra second.
  - **Clips:** 5 or 10 s.
  - **Request fields:** `negative_prompt` and `cfg_scale`. No seed, no audio, and no aspect or resolution field.
  - ~~UNCONFIRMED: the output aspect follows the 9:16 keyframe.~~ **Measured 2026-10-01 (walk step 9): it follows
    the INPUT image's aspect.** A 1024×1024 keyframe gave a 960×960 clip at 24 fps, 5.04 s; 70.2 s on fal; $0.21
    booked. Our square Cloudflare keyframes therefore give square clips, which the renderer letterboxed
    (T2-P6-F1, fixed by cover+crop in the renderer).
  *Confirmed 2026-10-01 by fal's usage export (2026-09-24…10-01, the human):*
  the 5 s clip (request `01a0f642…`) billed 5 seconds × $0.042 =
  $0.21, equal to the booking.
- **A-098** — The human's chat request for the redirect fix named the approach (an opener whose redirect handler
  drops the credentials off-origin and keeps them same-origin) and the acceptance tests. So it stands as the plan's
  approval (`.claude/plans/transport-redirect-credentials.md`), including the one consequence the request did not
  name: `test_provider_http.py`'s error-mapping test patches `transport._OPENER.open` instead of the global `urlopen`.
  *Confirmed 2026-09-30.* The explicit question was "record that you approved the plan, including the moved patch
  target", and the human answered "Go go". That confirmation covers this plan only.
- **A-069** — Measured render times on the VPS (container ffmpeg `7.1.5-0+deb13u1/aarch64`, final profile,
  libx264): a full 21-shot FR render 160 s, a full 20-shot EN render 161 s; per shot at 4× mean 3.6–3.8 s (median
  3.05–3.80, max 9.4 s); the final pass 62–70 s; the mux 10–11 s; a cached re-render 84–96 s. *Confirmed, measured
  at phase 4's Tier-2 (2026-09-29); the full numbers are in `.claude/CHECKPOINT.md`'s step-14 table.*
- **A-065** — Montserrat Black (the repo's own `Montserrat-Black.ttf`) is an acceptable stand-in for both phase-4
  styles' typography: fruit_drama asks Montserrat ExtraBold (else Inter Black), family_3d Fredoka Bold / Baloo 2,
  and none of those files ship. A template font is used when its file is dropped into `custom_fonts/`; the
  manifest names the font actually used. family_3d burns no dialogue subtitles (`subtitle_mode: none`), so only
  its end card, hook overlay, AI label and cover are affected. *Confirmed: accepted at the human's go-ahead with
  no specific remark (2026-09-29).*
- **A-067** — The spec's 4× upscale before `zoompan` is affordable on this VPS: a 3.0 s shot renders in 7.9 s at
  4× vs 5.5 s at 2× (4-core Neoverse-N1, host ffmpeg 6.1.1, libx264 medium, 2026-09-28 scratch bench), projecting
  ≈ 200 s for a 24-shot 60 s episode. *Confirmed by A-069's measured container times, plus the human's go-ahead
  after the phone watch, with no remark on visible zoom jitter.*
- **A-075** — The self-made SFX read as the cue names they carry (judged at the human's watch). *Confirmed:
  accepted at the human's go-ahead with no specific remark (2026-09-29).*
- **A-076** — 60 minutes covers a free-chain fast-track episode. *Confirmed: every fast-track job pressed during
  Tier-2 ran ≤ 9.4 minutes, summing to about 26 minutes across the 8 presses the EN episode needed (A-069's
  render seconds plus the paced-image rounds).*
- **A-077** — Label strings: "AI-generated" and "Généré par IA". *Confirmed on rendered frames: the FR word_pop
  and two_line renders show "Généré par IA" and the EN render shows "AI-generated", both checked by eye at
  Tier-2.*
- **A-079** — A 48.8 s English episode (under the 55–80 s window) is acceptable for phase 4's Tier-2 watch. The
  fast track refused the 48.7 s script (DEC-162, correct); I approved it myself, as its stop message offers and as
  the French walk's approve-anyway was, rather than write lines into it; the render warns "outside 55-75 s". The
  shortfall is E2 writing at the low end of its word range with a two-character cast (T2-F10), not the speech
  rate (A-056 confirmed). *Confirmed: the human answered "Finish, update artefact, push then merge" after being
  told the EN episode is 48.8 s and asked whether they'd rather have a longer rewrite (2026-09-29).*
- **A-056** — English speech runs at 0.065 s/char. *Confirmed at phase 4's Tier-2 (2026-09-29)*: the first
  English episode (Midnight Fridge ep 1) measured 14 lines, 586 characters, 38.04 s of speech from Edge's word
  timings (en-US-GuyNeural and en-US-JennyNeural) = 0.0649 s/char (per line median 0.0662, range 0.0528–0.0854).
  Two voice samples read 0.0688 s/char, but a sample's mp3 carries padding the line measure excludes. The same
  script-level measure on the French ep 1 gives 0.0687 s/char over 18 lines (A-055 stands).
- **A-055** — French speech runs at 0.070 s/char. *Confirmed*: three Edge samples at stage 0 (172 chars in 12.03 s
  average across three voices), corroborated live in stage 13's measurement walk — 15 real lines measured
  39.704 s of audio against 39.48 s the estimate had predicted, a +0.6 % error.
- **A-060** — `EPISODE_STEP_BUDGET_SECONDS = 1800` (30 minutes) covers a whole script, and a fast-tier T1 storyboard
  call, on Gemini's free tier. *Confirmed by the live walk*: the script job ran 20:08:54→20:09:15 (about 21 s of
  calls); T1 storyboard jobs measured 61–66 s wall time across the walk and the fix-round copy checks, with the
  single slowest T1 call at 47 s — every one comfortably inside both the 300 s per-call budget and the 1,800 s
  step budget.
- **A-064** — `data/usage.json` counts only media/TTS calls, never LLM calls — pre-existing behaviour, not
  introduced by phase 3. *Confirmed by design*: `clipping/aistory/steps/llm_call.py` records no usage counter,
  and only the image/TTS adapters touch the free-tier counters. This makes the phase-3 Tier-2 script's "usage.json
  moved only by free counters" check trivially true for the LLM side; recorded in the stage-13 action log as a
  surprise finding, not a bug.
- **A-070** — Every path that finishes a job writes a feed line in the instant its last worker `updated_at` write
  lands (clip done/failed: `update_progress`; story step: "…awaiting your approval" / "…failed"; cancel: "Cancel
  requested."), so the earlier of the two is when the job stopped (DEC-150). *Confirmed in code
  (`web/api/worker.py`, `web/api/store.py`) and on four live records 2026-09-27 (completed clip, approved,
  cancelled and awaiting story steps).*
- **A-040** — (spec §14, measured 2026-09-25 on the author's two reference videos)
  Shot mean 3.2–4.1 s, reaction cuts ≥ 0.8 s, lines of 3–8 words, 1–2 places per
  episode, a continuous music bed, single-word pop captions in the fruit-drama
  genre, no narrator. *Confirmed by the analysis file
  `.claude/plans/ai-story/10-REFERENCE-ANALYSIS-2026-09-25.md`.* Two videos is a
  small sample; the phase-7 analyser re-measures every import. **Invalidated by the
  same measurement:** "a hook text overlay in the first 1.5 s" (the hook is a
  diegetic insert or a shocking image) and "cut-to-black is the only cliffhanger"
  (a hard stop mid-beat is common).
- **A-012** — The phone-width overflow is fixable in CSS alone; no JSX change is
  needed. *Confirmed by measurement. The exploration flagged several inline
  `style={{ display: 'flex' }}` rows that no stylesheet can reach — chiefly the
  job header's action group at `JobDetail.jsx:255` — as probable blockers. They
  are not: once `.page-header` wraps, that group measures 222px and fits inside
  the 343px content box unchanged. Every route measured `scrollWidth ==
  clientWidth` at 375, 414 and 820px with the diff confined to `index.css`.*
- **A-011** — The pipeline's Python-level `print` output is enough to tell a user
  what is happening. *Confirmed against a live job on the running containers: the
  feed carried the transcript warning (34% of words dropped for backwards
  timestamps), the segment/word summary, the provider and model line, and the
  NVIDIA retry ladder including `attempt 1 failed | ValueError: NVIDIA returned
  an empty clip array`. Known gap: ffmpeg is a subprocess writing to the real
  file descriptors, so its output is not captured — documented in the README.*
- **A-007 — RESOLVED 2026-09-18, against the live API.**
  `deepseek-ai/deepseek-v4-flash-0731` exists and authenticates, but it
  **rejected** the `nvext.guided_json` the code was sending:
  `400 unknown field 'guided_json'`. So the assumption was wrong and every real
  analysis would have failed on the first attempt. Probing six mechanisms showed
  `response_format={"type":"json_schema"}` accepted; after switching to it a live
  call returned 2 clips with zero missing keys and
  `metadata.normalize_and_validate` accepted the result. See DEC-013.
- **A-010** — Whisper's device failure is a CTranslate2 property, not a torch
  one. *Confirmed: this machine has ctranslate2 4.8.2 with
  `get_cuda_device_count() == 0` and no torch at all, and reproduced both
  reported crashes; `torch.cuda.is_available()` would not have detected it.*
- **A-008** — The 21 undeclared `JobCreateRequest` fields are API-only: no part
  of the dashboard sends them. *Confirmed by grep for all 21 names over the whole
  of `web/dashboard/src` — zero matches. The task brief stated the dashboard sent
  "several of them"; it does not.*
- **A-009** — `reuse_job_id` is live, not dead. *Confirmed at
  `web/api/routes/jobs.py:67` (validation) and `:83` (popped from the payload and
  passed to `store.create_job(job_id=...)` to reuse a prior job directory). It is
  absent from `config_adapter`/`worker` by design, because it is popped before the
  payload reaches them.*
- **A-001** — The transcript contract consumed by `studio/subtitles.buat_file_ass`
  is `{"start": float, "end": float, "words": [{"word","start","end"}]}` with no
  `"text"` key, and word-level timestamps are mandatory.
  *Confirmed by reading `clipping/studio/subtitles.py:172` and `:247`, which index
  `seg["words"]` with `[]` rather than `.get()`.*
- **A-002** — `openai` is an undeclared dependency: imported at
  `clipping/engine.py:901`, present in neither `requirements.txt` nor
  `pyproject.toml`. *Confirmed by grep over both manifests.*
- **A-003 — CONFIRMED, and RESOLVED 2026-09-18 (`a269a8f`).** Only
  `studio/effects.py:86` and `studio/transitions.py:156` actually call yt-dlp
  inside `clipping/studio/`. *The call-site set was right; the count was not —
  there were **10** dead imports across 12 files carrying one, not 9. Re-verified
  by an AST pass rather than a grep: in each of the ten, `YoutubeDL` occurred
  exactly once, as the import itself. The ten are deleted; afterwards no module
  in `clipping/` uses the name unimported.*
- **A-005** — The render layer is unaffected by this refactor. *Confirmed: a real
  1080x1920 h264+aac clip plus thumbnail rendered end-to-end from a local mp4 +
  vtt, after fixing a pre-existing Windows path-escaping bug that blocked all
  subtitle burn-in (see DEC-008).*
- **A-006** — VTT-derived karaoke timing is correct. *Confirmed: regenerated the
  burned-in ASS and compared every Dialogue timing back to the source VTT — 0
  word mismatches across 44 words, every delta <=0.010s (ASS centisecond
  resolution). The single 0.833s outlier is a word starting before the clip cut,
  correctly clamped to the clip start.*

## Invalidated
- **A-071** — INVALIDATED 2026-10-01: "fal bills a queued request at submit even when it later fails". fal's usage
  export shows the probe `01a0f63e…` (failed at input download) was not billed. Replaced by A-109.
- **A-004** — INVALIDATED 2026-09-26: `-v` (`--video`) and `-t` (`--transcript`)
  are taken now, as are `-n` and `-r`; only `-u` is free. Replaced by the grep in
  the phase-1 exploration (`clipping/config.py:303,308,325,334`).
- **A-010's test claim** (2026-09-22) — "`tests/test_config_cli.py` pins the
  string so the next retirement surfaces as a test failure rather than a
  production 410." It cannot, and did not. `google/gemma-4-31b-it` was never
  retired: it stayed in `/v1/models`, accepted requests, returned no 410 and no
  error — it simply answered nothing, for 120s, on an 8-token request. Every
  test on that string passed throughout. Replaced by DEC-056's liveness probe,
  which is the only thing that can catch this: a real request.
- **A-058** — INVALIDATED 2026-09-29 on the pollinations route (T2-F2): fruit/food-head descriptors are drawn as
  human faces, not stylised fruit-people.
- **A-066** — INVALIDATED 2026-09-29: replaced by DEC-157's amended target, loudnorm TP −2.5 with AAC PNS
  disabled (T2-F4).

## Notes
A-007 is closed. The container uid fix is verified against a live daemon
(2026-09-18). The CUDA branch of the device resolver is verified by injection —
`resolve_whisper_runtime` takes `cuda_available`, and three tests drive the
CUDA-true path — so only `whisper_cuda_available()` against a real CUDA-enabled
CTranslate2 build remains, which needs a GPU host and nothing less. Diarization /
split-screen (RC-8) is still unexercised.

## Notes on this round (2026-09-21)
A-013 (output language follows the transcript), A-014 (clip default stays 7) and
A-015 (platform presets) are all now **confirmed by a live run**: the analysis
produced French titles with English tags from a French video, and three clips
inside the tiktok window. A-016 (the full image stays the default) is unchanged
and untested — Stage 11 is where it would be.
- **A-017** — A 12-hour media-URL TTL is long enough that expiry-mid-playback is
  a tab left open overnight, and short enough that a URL pasted into a chat stops
  working the same day. Both halves are judgement, not measurement. The
  `onError` handler in JobDetail.jsx re-fetches once, so the overnight case
  recovers rather than stalling; `MEDIA_URL_TTL` moves it. UNCONFIRMED.
- **A-018** — Nobody relies on `/api/outputs/{id}/{file}` answering with
  `Content-Disposition: attachment` by default. It now answers `inline` unless
  `?download=1` is given. Only the dashboard and `docs/studio/` consume it, and
  `docs/studio/` cannot authenticate against this API at all. UNCONFIRMED.
