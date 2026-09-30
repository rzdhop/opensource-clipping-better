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
- **A-071** — fal bills a queued request at submit even when it later fails. UNCONFIRMED; the live paid step is
  deferred.
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

- **A-082** — fal.ai as measured in the paid test (2026-09-29, DEC-174):
  - 35 requests with 0 failures: 13 `fal-ai/flux/schnell` (4–13 s each; booked $0.0028 at 720x1280, $0.0018 for a
    style-preview image) and 22 `seedream-4-edit` (30–40 s each; $0.03).
  - Total booked $0.6934, equal to `spend.json`.
  - A request cancelled 80 ms after its submit was resumed by poll on Continue and delivered, with no re-buy.
  - flux-schnell draws a fruit-head descriptor as the fruit (a pickle detective); pollinations drew human faces
    (A-058).
  - fal's dashboard, read by the human on 2026-09-29, shows a **cost estimate of $0.70 and 32 requests** over the last
    7 days. **The cost matches** once flux-schnell is billed per whole megapixel (DEC-175: 13 × $0.003 + 22 × $0.03 =
    $0.699). **The count is 3 short of our 35 and stays UNCONFIRMED.** The cause is unknown: dashboard aggregation
    lag, or fal counting some requests differently. fal's per-endpoint request list would settle it.
  - A-071 (is a queued request that later *fails* billed?) is still open: no request failed.

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

## Confirmed
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
