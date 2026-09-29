## IN PROGRESS — Auth becomes opt-in (no token unless `API_TOKEN` is set), then AI Story phase 5
- **Asked** 2026-09-29: "remove all access restrictions to the app, it's only local or via tailscale". Answers:
  auth **off by default with an opt-in token**; a **separate task before phase 5**. Classified FULL (auth).
  Both plans approved in chat ("Ok go"): `.claude/plans/auth-opt-in-token.md` (stages 0–4, riskiest stage 1) and
  `.claude/plans/ai-story/11-phase-5-plan.md` (phase 5, stages 0–15 + 14b; it starts after this task).
- **Phase-5 answers already given** (for when it starts): plan against merged `main`; export/import bundle
  **deferred** (follow-up, no DEC); **ship the OFL fonts** the five styles name; memory runs on an **approved
  script**; at the end of phase 5 the human funds the chosen providers for a paid live test (plan stage 14b).
- **Current phase:** IMPLEMENT. **Current stage:** auth stage 1 (backend). **Checkpoint:** branch
  `fix/auth-opt-in` from `main` **`b60938e`** (clean tree), on the Windows workstation clone.
- **Tier-1 baseline (b60938e, Windows 11, Python 3.11.15, ffmpeg 8.1.1):** local **5492 passed / 57 failed / 67
  errors / 28 skipped** (1300 s). Every failure is platform-only: WinError 1314 symlink privilege (83, incl. all
  67 errors = `test_story_media_serving.py`'s fixture), POSIX file modes 0600/0644 (~27), cp1252 encoding (3),
  CRLF checkout changing `test_render_layer_guard.py`'s sha256s (2), no framemd5 key for ffmpeg 8.1.1 (1). List:
  scratch `baseline_local_failed.txt` (124 ids). Rule for this task: **no new failure on Windows**, and the auth
  test files run green on **Linux** (a throwaway container from the local backend image) — the VPS reference is
  local 5643/1, CI 4936/677.
- **Stage 1 state (uncommitted on `fix/auth-opt-in`):** `web/api/auth.py` (opt-in rule, no-key signing refused,
  plain media paths when open, banner, `open_public_exposure`, `CrossSiteWriteGuard`), `web/api/app.py` (start
  refusal + guard), new `tests/test_auth_opt_in.py` (**fail-first: 42 failed / 5 passed on `b60938e`**, 47 pass
  now). Pinned tests changed on purpose: `test_auth_token.py` token-storage section (generator gone), 
  `test_generation_chain_api.py` signed-URL test (sets its own token), `test_stories_api_phase4.py:912` (page
  URL is the plain path when open). **Finding fixed in-stage:** `PreviewPane.jsx` appended `&download=1` to
  a URL that is now a plain path when open (→ 404) — separator now follows the URL, as `JobDetail.jsx` does;
  `test_story_payload_contract_episode.py`'s download pin updated. Targeted Windows runs: only baseline
  failures; compileall clean; vite build green (scratch outDir).
- **HANDOFF → the Ubuntu VPS (2026-09-29, the human: "you are running on the wrong machine").** Work moved
  off the Windows workstation, whose C: kept filling up (Docker Desktop's `docker_data.vhdx` is 111 GB, 78.8 GB
  of it the `nwodtuhs/exegol:nightly` image; the human decides any cleanup there — nothing was pruned). Stage 1's
  code was committed as **WIP** on `fix/auth-opt-in` and pushed. Windows evidence only: targeted auth files had
  no failure outside the baseline; the full Windows suite died on ENOSPC at ~9 min (its partial output showed
  only baseline ids, not a result). **Not verified yet — do this first on the VPS:**
  1. `git fetch && git switch fix/auth-opt-in` in a worktree (as phases 1–4 did; the container runs main's
     on-disk code via the bind mount — never switch the main checkout's branch).
  2. Tier-1 on Linux: `python -m pytest -p no:warnings` (never `-q`) and the CI env (`/tmp/cilibs`);
     compileall; `npm run build` to a scratch outDir (move a built `web/dashboard/dist/` aside first).
     Expected: VPS baseline local 5643/1, CI 4936/677 **plus** the new `tests/test_auth_opt_in.py` (47 tests;
     its app tests skip in the CI env) and minus the 4 removed token-generator tests. Any other change is a
     finding. `test_story_media_serving.py` (RC-U1/RC-A5) must pass **unedited** — it could not run on Windows.
  3. If green: a normal commit closing stage 1 (ledger row + log line), then stage 2 per the plan.
- **Next action:** the three steps above, on the VPS.
- **Open questions:** none blocking. The Windows disk/Docker cleanup is the human's, outside this task.

### Regression contract (auth task)
| ID | Must keep working | Proven by |
|---|---|---|
| RC-U1 | With `API_TOKEN` set, auth behaves exactly as before (header, clip/story signatures, 401 not 422) | `test_auth_token.py` client-fixture tests + `test_story_media_serving.py` unedited (Linux) |
| RC-U2 | With no token, every page, clip and episode video works unsigned | new `tests/test_auth_opt_in.py` + Tier-2 |
| RC-U3 | No `DISABLE_AUTH` in committed compose files; backend port bound to loopback | `test_auth_token.py` compose guards unedited |
| RC-U4 | The public paths never start open (Caddy `DOMAIN`; Kaggle ngrok) | new tests + Tier-2 |
| RC-A5 | A story signature opens exactly one file (token on) | phase-4 stage-12 tests unedited |

### Stage ledger (auth task)
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline | **done** (this commit) |
| 1 | opt-in auth in the backend (**RISKIEST**) [Opus] | **code committed as WIP** (fail-first 42/47 shown); Linux Tier-1 pending on the VPS |
| 2 | exposure paths, notebook, docs [Sonnet] | — |
| 3 | deploy + Tier-2 on the VPS | — |
| 4 | decisions + artifacts | — |

---

## CURRENT STATE — AI Story **phase 4 is DONE** (the MVP) and pushed. Next: **phase 5**.
- **Started** 2026-09-28 ("Go with phase 4"), FULL; plan `~/.claude/plans/ai-story-phase-4-assets-render.md`
  (stages 0–17, riskiest stage 6) approved ("Yes go go"). **Closed 2026-09-29** on the human's "Finish, update
  artefact, push then merge", read as the step-16 acknowledgement of both episodes (no specific remarks: A-065,
  A-067, A-075, A-079 recorded as accepted at that go-ahead).
- **Code:** `main` == `feat/ai-story-phase-4` at the close-out commit (the one carrying this line), deployed (bind
  mount; the last backend restart 08:33 UTC carries every code change — stage 17 is docs and artifacts only).
  **Pushed 2026-09-29:** `feat/ai-story-phase-4` `4f48515..b68f018`, CI run 36550933190 **green** (compile +
  test incl. the x86_64 golden render, 09:47 UTC), then `main` `fb5bbf7..b68f018` (fast-forward = the merge);
  this checkpoint line is one commit later on both. Tier-1 at close: see the stage-17 ledger row. `git diff --stat 1367d75 -- clipping/studio clipping/story` **empty**;
  `tests/test_render_layer_guard.py` green (RC-A1). Dashboard lockfile `npm audit`: 0 vulnerabilities; phase 4
  changed no Python or npm manifest (only `.github/workflows/ci.yml`'s apt ffmpeg pin); `pip-audit` is not
  installed here, so the Python audit stays on VISION's carried dependency pass.
- **Episodes:** FR `/story/b1104ec66b05/episodes/1` (58.2 s, word_pop, I −14.2 / TP −2.3) and EN
  `/story/0a9572a6a8be/episodes/1` (48.8 s, no subtitles, I −14.1 / TP −2.1), both $0.00, both stories `ready`.
  The FR `style_preview` job d95e7a469714 still awaits the human (pre-existing, theirs to approve or reject). The
  old FR ep01 (pre-phase-4) is kept at `/home/ubuntu/backups/ai-story-phase4/` (tar sha256 0da2a73e…d015).
- **Stage 17 (docs + decisions):** `docs/AI_STORY.md` (steps 10–12, Fast track, CLI, costs/pacing, render facts,
  on-disk layout, limits), README (AI Story bullet + "MVP: AI Story"), VISION (phase 4 done; next phase 5),
  DECISIONS DEC-151…DEC-172 appended (+ one "Amended by DEC-161" line on DEC-108 and DEC-109; nothing else
  edited), ASSUMPTIONS (A-069 and A-071…A-078 added; A-056, A-065, A-067, A-069, A-076, A-077, A-079 confirmed;
  A-058 and A-066 invalidated; A-051 extended; A-068, A-071–A-074, A-078 stay UNCONFIRMED).
- **Close-out per artifact:** CHECKPOINT — this header, ledger rows 16/17, follow-ups; claude-action.log — the
  stage-17 line; ASSUMPTIONS — as above; DECISIONS — DEC-151…172; VISION — "Where it stands (2026-09-29)" + next.
- **Next session:** phase 5 (series memory → episode 2, audience steering, per-scene re-edit, remaining styles),
  starting with LOAD + EXPLORE; the phase-4 follow-ups below are candidates for its plan or a small fix round.
- **Stage 16 log:** `main` first ff-merged to `bc4dfb8` 2026-09-28 ~22:35 UTC at 0 jobs, backend rebuilt
  (`rm -sfv` + `up -d --build`); fixes T2-F1 `d9a2e92`, T2-F4 `2d26371`, T2-F5 `d360b66`, T2-F6/F7 `207f8c7` each
  ff-merged + `docker compose restart backend` at 0 jobs. Live artifacts: this worktree's `.claude/`.
- **Tier-2 so far:** (before) FR story tar (now `/home/ubuntu/backups/ai-story-phase4/b1104ec66b05-before.tgz`) sha256 0da2a73e…d015 (1.8 MB),
  `usage.json` a11610f6…9d96, no `spend.json`; (1) golden render in the container: `7.1.5-0+deb13u1/aarch64` matches,
  7.5 s — PASS; (2) old ep01 moved aside (now `/home/ubuntu/backups/ai-story-phase4/ep01-pre-phase4/`, also in the tar); (3) script job
  `bdf8c2124a53` started 22:39:40 UTC from the dashboard at 375 px (estimate $0.00 · 11 LLM calls, gemini free).
  The awaiting `style_preview` job d95e7a469714 is the human's to approve/reject; it did not block the script step.
  (3 cont.) script done 22:41:06 (86 s): 10 scenes, 18 lines, 58.7 s estimated (ok); E4 flagged s10 repeating
  Mangella's s09 line (F12) → regenerate `scene:1:s10` with a note from the UI (new line distinct) → Check again → E4
  found 3 other issues (s01/s05 Kiwilo's tone, s03 bushes vs the pool: E4 variance) → **approved anyway** 22:44:20
  (recorded; plan-sanctioned for the manual walk). (4) T1 storyboard 22:44:56→22:46:02: 21 shots (2/scene, s08 3),
  approved 22:46:34. Assets estimate: 21 images pollinations/flux free (cloudflare keyless), 18 lines/708 chars free
  (Kiwilo edge/fr-CA-AntoineNeural, Mangella gemini/Kore, …), fal/flux-schnell listed "refused: est $0.059 …
  allow_paid is off". **Step 12 (cap refusal with allow_paid on) moved to the EN story before its fast track** (needs
  the human's OK to flip the live setting). (6) assets job started ~22:47 from the UI.
  (6 cont.) assets job 3a415855191c 22:47:17→22:50:58 ended awaiting with **4/21 images, 15/18 lines**: TIER-2 FINDING
  T2-F1 — free-tier rate limits fail items instead of pacing them: pollinations answers HTTP 402 except ~1 image per
  minute (successes at :54/:57/:54/:54 each minute; 402 treated as fatal), Gemini TTS answered 429 after ~4 calls per
  minute (the runner's 3 s retry is too short). SFX all resolved; BGM telenovela_tension ← dominant 'scheming'
  (assets/bgm/epic/trailer-score-room-epic-cinematic-thriller-538890.mp3); ledger $0.00 / 33 rows. Fix attempt 1
  (Opus): paced follow-up passes for rate-limited free items inside `steps/assets.py`, providers untouched —
  committed on the branch (local 5616/1, CI 4909/677), ff-merged, backend restarted at 0 jobs, then Continue.
  Also seen: l04 and l08 carry identical text (E-prompt echo, F12 family) → the cache served l08 at $0 ('kept answer').
  (6 cont.) after T2-F1 (`d9a2e92`, deployed): Continue → **21/21 images, 18/18 lines** in 17 min (14 paced rounds,
  ~1 image/min), $0. T2-F2/F3 (recorded, not fixed): pollinations flux draws HUMAN faces for the fruit-head
  descriptors (the phase-2 portraits the human approved are human too — A-058 invalidated on this route) and
  stamps a 'pollinations.ai' watermark; prompt-only shots reuse the portrait seed → many near-identical portraits.
  (7) UI regenerate `shot:1:sh09` with a note → only sh09 changed (seed 130852438); lock sh02 → regenerate answers 409
  'Shot sh02 is locked: unlock it first.'; approve assets 23:48:59 (fingerprint current, 21/21 approved; the awaiting
  assets/regenerate jobs completed). (8) render 80af58a4c007 final profile 160 s, 28 stages: 58.2 s, 1080×1920, 30/1,
  I −14.07 but **TP +0.57 dBTP** (T2-F4) → fixed `2d26371` (TP −2.5 + `-aac_pns 0`; A-066 invalidated, DEC-157
  amended); ducking median 8.9 dB over 8 line/gap pairs (≥ 6 dB), no bed gap; frames: word_pop, 'Généré par IA',
  push-in; a lone '?' popped at 45 s (T2-F5) → fixed `d360b66`. (9) sh13 regenerated + re-approve + re-render
  10c70d28c364: **20/21 shots cached, 8 stages ran, 96 s, I −14.2 / TP −2.3 / LRA 5.0, no warnings**. (10) subtitles →
  two_line 5b1d4dad9846: 21/21 cached, 7 stages, 90 s; FOUND T2-F6 Broccolia's accent #1E1E24 unreadable on the black
  outline and T2-F7 provider word cues drop the script's punctuation ('sécurité Ils') → fix in progress (Sonnet).
  Next: re-render word_pop, metadata (11), EN story (13) — phase-2 image steps have no pacing, so the EN cast on
  pollinations will need ~1 Continue per image unless the human adds a free Cloudflare key.
  T2-F6/F7 fixed `207f8c7` (deployed 01:50 UTC): two_line re-render → Kiwilo #E4572E, Mangella #FFFFFF, Broccolia
  #C5C5C5, all readable, punctuation kept ('sécurité. Ils se trompent.'). Final **word_pop render 13ff2e4e296e: 58.2 s,
  1080×1920, 30/1, I −14.2 / TP −2.3 / LRA 5.0, 21/21 shots cached, 89 s, no warnings**. (11) metadata 8c3c2379a5f2 (UI,
  6 s, 3 free calls): tiktok/shorts/reels FR + title_en/hashtags_en, descriptions end on the teaser, pinned 'PARTIE 2 →',
  hook 'LE JEU COMMENCE', cover = hook shot + hook text (looked at). **FR episode ready for the human's phone watch.**
  (12) paid-cap refusal with allow_paid on: **deferred by the human** ("We'll try a paid run soon enough don't worry for
  now", 2026-09-29); today's estimate already lists fal 'refused: est $0.059 … allow_paid is off'. EN story (13): human
  chose the free route with me pressing Continue per image (small cast: 2 characters, 1 place). Pending: (13) EN family_3d story; (14) table;
  (15) resilience checks; (16) the human watches both.
  (13 started) EN story `0a9572a6a8be` created + concept `midnight_fridge` chosen via the API (the wizard steps 1–7 were
  walked in the UI in phases 1–2); paused there at the human's request.
  (13 resumed 2026-09-29 07:25 UTC, new session; `usage.json` cb322b15…6723, no `spend.json`, 0 jobs) bible
  857441d84b25 14 s (3 Gemini calls) → approved; style draft (family_3d) → approved, no preview; story switched to
  `prompt_only` (PATCH generation_profile); cast 73162f83d087 (Pickle + Egg): Pickle text + portrait OK, turnaround/
  expressions HTTP 402 (pollinations 1/min, expected), **T2-F8: Egg's K1 text refused twice — `schemas.py` K1 check
  is a substring match of the name, so a character named for what it is ("Egg") can never describe itself** →
  phase-2 rule, follow-up, not fixed; `char_egg` deleted, cast re-run with **Detective Pickle + Madame Brie** once a
  minute (scratch `step_loop.sh`): 5 rounds 07:26:57→07:31:01, one image each, 6/6 images, voices pinned by the cast
  step (edge en-US-GuyNeural / en-US-JennyNeural, samples made); both portraits HUMAN + watermark (T2-F2 again) →
  approved; places_proposal 3 s → places with ONE place "Top Dairy Shelf", props [] (an empty params object would
  fall back to the saved proposal and make everything) → 402 then 1 paced round → plate OK → approved; season
  1ff9ef62e866 31 s (8 eps) → approved → **`ready` 07:32:37** (bible→ready 7 min 20 s, $0). **Fast track pressed from
  the dashboard at 375 px 07:32:54 → job `e4cff4059060`** (storyboard t1). Its confirm said "Voices: 0 lines / 1231
  chars" before a script exists (cosmetic, follow-up). FR ep01 re-measured by scratch `measure_ep.py`: 58.2 s,
  12.31 MB, h264 1080×1920 30/1, aac 48 kHz 2 ch, ebur128 I −14.2 / TP −2.3 / LRA 4.9, ledger 55 rows all with
  `ep`, $0, 0 paid; ducking median 11.6 dB over 9 line/gap pairs adjacent to ≥ 0.8 s gaps (two pairs under 6 dB:
  the intro while the bed rises, and a 1 s line at 38.5 s where the bed falls −12 → −19 dB across the line).
  **T2-F9 (blocks step 13):** fast track e4cff4059060 stopped at the script after 8 s — E1 rejected twice: on a story
  with NO props gemini filled every scene's `props` with free text ('magnifying glass', 'notepad', …) vs the
  `^prop_…$` pattern. Root cause (Explore/Sonnet, verified): `prompts.e1_schema` enumerates prop ids only when there
  are some — with none, `props` items fall back to a bare string — and `_E1_ASK_TEMPLATE` still asked "0 to 4 of the
  existing props" with no roster; the FR story passed because its enum constrains the model. Fix attempt 1 (inline,
  files scoped by the agent): `_E1_NO_PROPS_LINE` "- props: always [] -- this story has no props" + schema
  description (no `maxItems`: the strict-mode subset, `schemas.py:670-677`) + `script._repair_e1_reply` empties
  every scene's props before validation when the story has none (DEC-144-style; a story WITH props still has an
  unknown prop refused and retried). Fail-first: 2 new tests fail on HEAD, pass after; 1 pin passes both. Tier-1
  local **5641/1**, CI env **4934/677**, compileall clean → committed **`2fbd9f0`**, `main` ff-merged, `docker compose
  restart backend` 07:48:14 at 0 running jobs (container carries the fix). **Step 15 restart check PASS:** FR assets
  job 27294a95911d (a $0 re-run: 0 images, 0 lines) awaited before and after the restart, same `updated_at`; the
  FR style_preview d95e7a469714 also still awaits; FR assets re-approved 07:48:24 → the job completed, render still
  current (sha 53cb3bcc…), metadata current. **Fast track re-pressed at 375 px 07:48:49 → job `efec1d210410`.**
  T2-F9 fix PROVEN live: E1 first attempt OK, script written 07:48:47→07:49:54 (67 s: E1 + 8 E2, one E2 over-budget
  retry accepted, E3, E4). The fast track then stopped at its own gate (DEC-162, correct): E4 2 issues (s05 summary
  and line l28 said 'dairy aisle') and **T2-F10: 48.7 s estimated, under 55–80** — 14 lines / 101 words / 587 chars,
  every E2 reply inside its word range (9–14 of a 13-word budget), 5 single-speaker scenes with one line each; a
  scene regenerate with a 'fuller back-and-forth' note gave s05 8 words (E2's range caps near the budget, so a
  regenerate cannot lengthen much). EN Edge samples measure **0.0688 s/char** (Pickle 52 ch 3.58 s, Brie 65 ch 4.46 s)
  vs A-056's 0.065. Operator actions (recorded): PATCH s05 summary + l28 'dairy aisle' → 'dairy shelf' (rev 3);
  Fast track 07:52:47 (b5b17d5515c9): E4 passed, stopped on 'under' only; **script approved by me 07:53:07** (the
  stop message's "approve it yourself"; like FR's approve-anyway, no length criterion in step 13) → Fast track
  07:53:20 → job **`9184bffd17dd`** (script 0 calls; storyboard t1 → assets → render → metadata).
  Storyboard: T1 refused s02 and s09 twice each ('1 shot(s), expected 2-4' — both one-line single-speaker
  scenes, the known phase-3 T1 under-shoot); Continue a671bbbdee09 planned s02, s09 refused twice more; Continue
  **b20642641a62** (07:55:30) planned s09 first try → 20 shots / 10 scenes, storyboard auto-approved, paid check
  "nothing paid" ($0), assets started 07:55:31: 14/14 lines voiced by 07:55:39 (Edge, ~0.5 s each), 20 images
  paced on pollinations. **A-056 measured:** 14 EN lines, 586 chars, 38.04 s of speech (tts_word_timestamps) =
  **0.0649 s/char** (median 0.0662, range 0.0528–0.0854; Guy + Jenny) → CONFIRMED at 0.065; the 0.0688 of the two
  voice samples includes mp3 padding. Same script-level method on FR ep01: 0.0687 s/char over 18 lines.
  Assets (b20642641a62): 5 images 07:55:43→07:59:46 one a minute (sh01, sh07, sh13, sh19 in the first pass, then
  sh02 in paced round 1), measured length 48.7 s (under); **T2-F11:** paced round 2 got a pollinations HTTP 500
  (transient) instead of an image → "still rate-limited after a 60 s pause: 15 shots left as failed" at 08:00:52 —
  one no-progress round gives the provider up (T2-F1's rule), so a single 5xx ends the pacing; follow-up, not fixed
  (Continue resumes). Also seen: sh07/sh13/sh19 reuse Pickle's portrait seed 1224875959 (known follow-up).
  Fast track Continue 08:01:47 (15 images left, render ~3.7 min, metadata 3 calls) → c91b639504e1 made 8 images then
  T2-F11 again at 08:10:53 (HTTP 500 after 13 s, the retry 402, round gave up; 2 of 2 continues); Continue 08:11:34 →
  **f91f204b484d COMPLETED 08:20:57**: last 7 images by 08:17:51, assets auto-approved (fingerprint e7d18c484304),
  render 08:17:51→08:20:32 (**161 s, 28 stages, 0 cached**, 20 shots), metadata 3 M1 calls (25 s), "🏁 Fast track
  done … (9.4 min; auto-approved: assets)". **EN ep01 measured:** 48.8 s, 9.68 MB, h264 1080×1920 30/1, aac 48 kHz
  2 ch, ebur128 I −14.1 / TP −2.1 / LRA 2.7 (manifest −14.16/−2.06/2.6), render warning "48.8 s long, outside
  55-75 s" (T2-F10), ducking median 10.2 dB over 16 adjacent line/gap pairs (min 2.3), bed silent only in its first
  100 ms, ledger 34 rows all `ep`, $0, 0 paid; subtitles none ✓, cut_to_black + 'PART 2' + 'Midnight Fridge' end
  card ✓, 'AI-generated' label ✓, mood `warm_family` (dominant tension; a romantic-piano bed under a noir mystery —
  A-074 taste point for the watch) ✓. Frames: human faces + watermark (T2-F2), near-identical shots (seed reuse).
  Fast-track wall time 07:32:54→08:20:57 (48 min incl. the T2-F9 fix + restart); summed job time ≈ 26 min over 8
  presses; any one job ≤ 9.4 min (A-076: 60 min covers it).
  **Step 15 cancel:** EN render 374ac47f5cc4 (subtitles word_pop; E cached, A, L1 ran) cancelled 10 s into F:
  API status `cancelled` 0.17 s after the POST, manifest F `cancelled`, partial episode_pre.mkv (621 KB, unfinalised)
  kept, the previous episode_final.mp4 untouched (4fe85df1…), no ffmpeg left — but the worker's '⏹ … cancelled during
  F' came **3.38 s** after the request: **T2-F12** — ffmpeg answers SIGTERM by flushing the x264 lookahead (~3 s at
  15 fps on 1080×1920; its log stops at frame 146, no exit line) and was SIGKILLed at KILL_GRACE_S = 3.0. Fix attempt
  1 (inline; runner.py scoped): `CANCEL_GRACE_S = 1.0` for a cancel only (timeouts keep 3.0; both pins unedited);
  fail-first (killed 3.0 s after the cancel on HEAD); Tier-1 local **5643/1**, CI **4936/677**. Next: commit →
  ff-merge → restart → repeat the live cancel (expect ≤ 2 s) → re-render EN subtitles none (restores the
  pack's render sha if the output is byte-identical, else regenerate metadata).
  → committed **`7083c39`**, `main` ff-merged, restart 08:33:00 at 0 jobs. **Live cancel repeated: 1.43 s** from
  'Cancel requested' (08:33:22.282) to '⏹ … cancelled during F' (08:33:23.709), partial mkv 2.6 MB kept, previous
  mp4 untouched → **step 15 cancel PASS**. EN restored: render e61411b5d8c5 subtitles none, 7 ran / 21 cached, 84 s,
  output **byte-identical** (sha 4fe85df1…) → metadata pack current again. EN Preview/Script/Storyboard: no overflow
  at 375/820/1280; video readyState 4, 48.8 s, 1080×1920. Cosmetic: "Write remaining metadata · 0 LLM calls" shows
  on a complete pack. **Step 15 DONE.**

### Tier-2 measurement table (step 14; both on container ffmpeg 7.1.5 aarch64, final profile, libx264)
| | FR ep01 (b1104ec66b05, fruit_drama) | EN ep01 (0a9572a6a8be, family_3d) |
|---|---|---|
| Route | manual walk steps 3–11 | Fast track (8 presses; see T2-F9/F10/F11) |
| Length | 58.2 s (55–75 ✓) | **48.8 s (under 55–75; render warns; T2-F10)** |
| Size / video / audio | 12.31 MB · h264 1080×1920 30/1 · aac 48 kHz 2 ch | 9.68 MB · h264 1080×1920 30/1 · aac 48 kHz 2 ch |
| ebur128 I / TP / LRA | −14.2 / −2.3 / 4.9 (manifest −14.2 / −2.3 / 5.0) | −14.1 / −2.1 / 2.7 (manifest −14.16 / −2.06 / 2.6) |
| Ducking (adjacent line/gap pairs) | median 11.6 dB, 9 pairs (≥ 6 ✓; 2 pairs < 6 dB) | median 10.2 dB, 16 pairs (≥ 6 ✓; min 2.3) |
| Subtitles / ending | word_pop (two_line tried) · hard_stop | none · cut_to_black + PART 2 + title |
| Full render | 160 s (80af58a4c007, 21 shots, pre-T2-F4) | 161 s (f91f204b484d, 20 shots, 28 stages) |
| Per shot at 4× | mean 3.76 s, median 3.80, max 6.5 | mean 3.62 s, median 3.05, max 9.4 |
| Final pass F / mux M | 61.5 s / 11.4 s | 69.8 s / 10.1 s |
| Cached re-render | 96 s (1 shot new), 90 s / 89 s (subtitles) | 84 s (subtitles), byte-identical output |
| Ledger | 55 rows, all `ep`, $0.00, 0 paid (15 voice_measure + 40 assets) | 34 rows, all `ep`, $0.00, 0 paid (assets) |
| Speech rate | 0.0687 s/char over 18 lines (A-055 0.070) | **0.0649 s/char over 14 lines (A-056 0.065 ✓)** |
| Metadata | FR + title_en/hashtags_en, PARTIE 2 → | EN, PART 2 →, no EN duplicates |
After: 0 jobs running (only FR style_preview d95e7a469714 awaits the human, pre-existing); `usage.json` free counters
only (edge 16, pollinations 82 today; sha d3257c79…); no `spend.json`; both stories `ready`, cost $0.00.
**Next: step 16 — ask the human to watch both episodes on the phone and acknowledge; then stage 17.** Step 15 done early: RC-P1 `/clips/job/b37b36a9b34e` 7 clips, a clip 200
  (22.5 MB) and range 206, the SPA route 200 and 7 `<video>` at 375; no overflow at 375/820/1280 on /story, both
  story pages, FR ep 1 Script/Storyboard/Preview, the clip job and /settings (EN Preview re-checked once rendered).
- **Tier-2 findings kept as follow-ups (not fixed):** T2-F2 pollinations flux draws human faces for fruit-head
  descriptors (A-058 invalidated on this route); T2-F3 its 'pollinations.ai' watermark; prompt-only shots reuse the
  portrait seed → near-identical portraits; the pacing feed prints '✖ Shot shNN failed' just before a paced retry;
  per-link call allowance (300 s sized for fal) would buy ~3 more paced rounds; a three-pass linear loudnorm chain;
  listen to the ep01 TTS noise burst (13.19–13.31 s); phase-2 image steps have no pacing; E4 variance (approve
  anyway used); E-prompt echo duplicates (l04 = l08 text); the script pane's flag links render as default blue
  links; the Node 20 deprecation notice on checkout@v4/setup-python@v5 (CI itself green on the close-out push);
  `test_clip_serving.py`'s spa fixture leaks a '/' mount; a line regenerate's take is not persisted; a T1 re-plan
  drops shot locks/notes; T2-F8 K1's own-name check is a substring match (a character named "Egg" cannot pass);
  T2-F10 E2 lands at the low end of its word range (EN ep 1: 48.7 s est. with 2 characters and 5 one-line
  single-speaker scenes; a regenerate note cannot lengthen past the budget); T2-F11 a single pollinations HTTP 500
  in a paced round (its retry then 402s) gives the provider up and fails the rest (2 of 2 continues); T1 still
  under-shoots one-line scenes (s02/s09 refused 5 times on EN); the fast-track confirm said "0 lines / 1231 chars"
  before a script existed; "Write remaining metadata · 0 LLM calls" shows on a complete pack; after a fast-track stop
  the episode page's first screen at 375 px showed no stop reason (it is in the job's feed) — check before fixing.
- **Tier-1 baseline (86e7f4d):** local **4486 passed / 1 skipped** (210 s); CI env (`/tmp/cilibs`) **3901 passed /
  555 skipped** (155 s); compileall clean (`PYTHONPYCACHEPREFIX` in scratch); vite build green to a scratch outDir.
  ffmpeg: host `6.1.1-3ubuntu5`, container `7.1.5-0+deb13u1`. Health: 0 jobs.
- **Next action:** phase 5 — LOAD, then EXPLORE (see the header).
- **Stage 14 notes:** api.js `fetchShotImageUrl`, `patchEpisodeAssets`, `fetchStoryEstimate(…, {alignWords, storyboard})`;
  StoryboardPane `ShotStateBadge`, `ShotImageBlock`, `AssetsHeader`, `ApproveAssets`; ScriptPane `LineRow` word-source
  label + voice regenerate + cast link; EpisodeStudio `FastTrackHeader` (window.confirm split dialog); layout risk:
  `.page-header` now holds two flex children — check at 375 px.
- **Stage 13 notes for the docs stage:** `--ai-story step <id> assets --ep N [--auto-approve] [--align-words]`,
  `step <id> render --ep N [--subtitles MODE] [--encoder ENC]`, `step <id> metadata --ep N`, `render <id> --ep N …`
  (alias), `fast-track <id> --ep N [--storyboard t1|fast]`; `--auto-approve` only for assets (refuses a stale/
  incomplete grid, exit 1); metadata + fast-track meet the LLM key gate; assets stops on its own plan refusal;
  every `StepFailed` → exit 1.
- **Stage 12 notes for stages 14/15:** `GET /episodes/{ep}` → `render.media.video_url` / `render.media.cover_url`
  (null until the file exists); `<video src={video_url}>` keyed by `render.output.sha256` (URL byte-identical across
  polls within a bucket); `<img src={cover_url}>`; download `href={video_url + '&download=1'}`; on a media
  `onError` re-fetch the episode page (as `recoverExpiredMedia` does for clips). Route `GET /api/stories/{story_id}/
  episodes/{ep}/media/{name}` (name ∈ episode_final.mp4, cover.jpg), `Cache-Control: no-cache`, ranges 206;
  tokenless machines still mint signed URLs (as clip `media_url` does; `require_token` returns early).
- **Stage 11 notes for stages 12–15:** episode page adds `assets{doc, consistency, fingerprint none|current|stale,
  approved_at, shots[{shot_id, scene_id, state, image_name, route, consistency, provider, model, seed, note,
  est_usd, generated_at, locked, approved, pending, target}], lines[{line_id, scene_id, speaker, voiced, voice,
  words_source, aligned_by, approximate, target}]}|null`, `render{state, profile, params, duration_s,
  loudness{i,tp,lra}, fps, width, height, output{file, sha256}, stages{total, ran, cached, shots, shots_cached},
  seconds, started_at, finished_at, warnings, ffmpeg, out_of_date, media{video_url: None, cover_url: None}}|null`,
  `metadata{pack, current}|null`, `ledger{entries, totals{est_usd, paid_usd, entries}}`; stage 12 fills
  `_episode_media(story_id, ep, render)` in `routes/stories.py`; shot blob `GET /api/stories/{id}/episodes/{ep}/
  shots/{image_name}`; `PATCH /episodes/{ep}/assets {shots:[{shot_id, locked}]}` (only an imaged shot locks; a lock
  stales the assets approval); models `AssetsStepParams`, `RenderStepParams`, `FastTrackStepParams`,
  `AssetsPatchRequest`, `AssetsShotPatch`; estimates take `align_words`/`subtitles`/`encoder`/`storyboard` query
  options; `routes.stories.complete_approved_jobs(story_id, ep)` called by the worker after any fast-track ends;
  `out_of_date` cached per (root, story, ep) on doc `updated_at`s + (size, mtime) of media; an `encoder: auto`
  render always reads out of date; stage 13: the CLI lacks assets approval (`refuse_approval("assets:1")` →
  `not_found`).
- **Stage 10 notes for stages 11/13/14/16:** `steps/fast_track.run(ctx, …)` (param `storyboard` t1|fast; helpers
  `read_params`, `script_refusal`, `paid_verdict` free|paid_within_caps|stops_before_paid|blocked, `render_seconds`
  8 s/shot + 60 s authored pending A-069, `estimate(ec, *, env, storyboard, …)`, `STORYBOARD_CHOICES`); every stop
  raises `StepFailed`; `steps.ends_completed(step, params)` → worker ends render/metadata/fast-track/metadata
  regenerate COMPLETED ("Story step 'X' is done."); `workflow.approve_assets(stories, story_id, ep, *, now)` sets
  each shot's `approved` + `approved{at, fingerprint}` — stage 11 wires the route and must also complete older jobs
  awaiting `script:/storyboard:/assets:<ep>` once a fast track approved them in-process; `render.current_render(ec,
  params, …)` (no process; hashes inputs + mp4 — cache it against the 4 s poll); `budget=None` seam on script/
  storyboard/assets/metadata runners; feed prefixes `⏩ Fast track n/6`, `✅ … auto-approved`, `💲`, `🏁`.
  **Tier-2 step 12 (paid estimate refused by the cap) must run while shots are still unmade** — once every asset
  is current the estimates price $0.
- **Stage 9 notes for stages 10/11/14/15/16:** `steps/render.run(ctx, *, profile="final", …)` (params `subtitles`
  style|word_pop|two_line|none, `encoder` libx264|auto — `render.SUBTITLE_CHOICES/ENCODER_CHOICES`) returns `{ep,
  state, profile, params, ran, cached, duration_s, loudness, warnings, output{file, sha256, width, height, fps},
  subtitles_file, manifest, seconds, fingerprint}`; `render.require_renderable(ec)` / `metadata.require_render(ec)`
  check preconditions with no process; `steps/metadata.run` (no params) + regenerate kind `metadata`
  (`regenerate_platform`) — stage 11: `parse_episode_target` → `("metadata", ep, platform)` + `EPISODE_KINDS`;
  both steps and the metadata regenerate must end `completed` (stage 10); `store.episode_doc_path` new; the
  manifest carries no fingerprint → "out of date" = manifest `inputs` hashes vs current files (cache it against
  the 4 s poll); Preview: video = `manifest.output.path` keyed on `output.sha256`, cover = `cover.jpg`, pack stale
  unless `metadata.is_current(pack, script, sha)`; M1 cap 330 (FR Reels worst case 216 × 1.3 + 15 %); e2e TP came
  out −0.8 dBTP on synthetic tones (A-066 to check on real voices at Tier-2).
- **Stage 8 notes for stages 9/10/11/14:** `steps/assets.run(ctx, …)` returns `{ep, shots{total, made, cached, locked,
  states}, lines{measured, unvoiced}, aligned, sfx, bgm, failed[{what, target, reason}], complete, fingerprint}`; hard
  stops raise `StepFailed`; only param `align_words`; helpers `asset_units(...)` (estimate shape: images/voices/
  alignment/paid_links/caps/est_usd/over_cap/ready), `assets_fingerprint`, `current_fingerprint(ec, …)`,
  `shot_state` (`none|current|stale|locked_stale|failed`), `require_approved(ec)`; regenerate kinds `shot_image`
  (returns `{target, shot, seed, provider, cached}`, locked → "unlock it first", pending seed reused for the same
  note) and `line` (`{target, line, voice, duration_s, take}`) — stage 11 must make `parse_episode_target` return
  them and add them to `regenerate.EPISODE_KINDS`; SFX/BGM paths are repo-relative; aligned sidecars carry `words`,
  `words_source: alignment`, `aligned_by`; identical shot requests share one cached image (24 shots → 20 calls on
  the fixture); a T1 re-plan resets shot `assets` (locks/notes lost; images return at $0 from the cache).
- **Stage 7 notes for stages 8/9/10/16:** `runner.render(plan_args={…, story:{story_id, title, language}, inputs:{shots,
  lines, sfx, bgm, overlay, font, word_timings}}, render_dir=episode_render_dir(…, create=True),
  manifest_path=<ep>/render_manifest.json, final_path=episode_file_path(…, "episode_final.mp4", create=True),
  cancel=token)` → `{state completed|failed|cancelled, error, failed_stage, manifest, output, warnings, ran, cached}`
  (on cancelled the step raises `Cancelled`); inputs via `runner.file_record`, `runner.paper_texture_record()`,
  `fonts.resolve_font`; the runner writes `render/logs/` (NOT in `EPISODE_RENDER_SUBDIRS` — stage 9 adds it or
  moves logs) and writes the manifest directly (stage 9 must pass a store-derived path); `subtitles.ass` lands in
  `render/` → stage 9 copies it to the episode folder; `encoder="auto"` is a PlanError until stage 9 wires it;
  mix/stems are `pcm_f32le`; P:loudness warns above −1 dBTP; the container has Python 3.11 and maybe no pytest →
  run `tools/render_golden.py` there.
- **Stage 6 notes for stage 7:** `filtergraph.sequence_plan/xfade_offsets/final_pass_argv/audio_mix_argv`,
  `GraphError`; offsets in whole frames (transitions must be whole frames; clips must have exactly the timeline's
  `frames` or `sequence_plan` raises — report GraphError text); lengths equal by construction (silent base +
  `amix duration=first`; no `-shortest`); DEC-157 constants live at the end of `profiles.py` (add the loudnorm
  target there); **the mix is s16 before loudnorm and peaked −3.2 dBFS with real SFX → mix in float or check peaks
  in L1**; GOLDEN final pass ≈ 19 s for 34 s of video → the golden fixture must stay a few seconds long; the AI
  label also covers the end card (kept: it is the disclosure). ffmpeg quirks handled: concat → `settb=1/30`, looped
  mp3 bed re-stamped `asetpts=N/SR/TB`, sidechain padded to full length, fadeblack's black falls at frames 2–3/12.
- **Stage 5 notes for stages 6/7/9:** `subtitles.build_subtitles_ass(*, timeline, script, subtitle_mode, language,
  hook_style, ai_label_enabled, palette, typography, word_timings=None) -> (doc, meta)` with
  `meta["approx_line_ids"]` for even-split lines; `end_card_ass(language, next_ep, story_title, typography,
  duration_s)`, `cover_ass(text, typography)`; callers must set `typography["font_family"]` to
  `fonts.resolve_font(...)["family"]` first — libass matches the fallback as **"Montserrat Black"**;
  `fonts.stage(record, fonts_dir)` needs an existing `render/fonts/`; two_line speaker accents keep ≥ 100 RGB units
  from the highlight and each other (fruit_drama #E4572E/#3A7D44/#FFFFFF, family_3d #6B7A8F/#8BC34A/#FFFFFF).
- **Stage 4 notes for stages 5/6/7:** timeline = `{total_s, fps, total_frames, shots:[{shot_id, scene_id, start_s,
  duration_s, frames, motion, modifiers, transition_after}], lines:[{line_id, scene_id, start_s, duration_s}],
  sfx_anchors:[{scene_id, at, cue, start_s}], end_card:{fade_start_s, fade_duration_s, start_s, duration_s, end_s}|None}`
  (`render.timeline.build_timeline(script, storyboard, template, language, *, style_lock)`); lines placed by
  `timing.line_offsets` (covering board only); new additive `timing.scene_starts`; per-shot `frames` by cumulative
  rounding (Σ = round(Σ durations × 30)); `filtergraph.PAPER_TEXTURE_REL = "in/paper_texture.png"` — the runner
  stages the bundled PNG there; the no-line-in-a-transition check covers a scene's outgoing window only (an
  incoming 0.35 s pre-roll under a 0.4 s fade is allowed: audio is one absolute timeline, DEC-158); GOLDEN's
  bitexact flags split into `global_bitexact_args()` (before `-i`) and output-side flags.
- **Stage 3 notes for stages 7/8/9/11:** `episode_render_dir(story_id, ep, sub=None|in|fonts|cache|stems, *, create=False)`,
  `gen_cache_dir(story_id, *, create=False)`, `episode_file_path(story_id, ep, name, *, create=False)` with
  `EPISODE_FILE_NAMES = (episode_final.mp4, subtitles.ass, cover.jpg, cost_ledger.json)` — all KeyError on refusal;
  shot images via `episode_asset_path(..., "shots", "shot_NN.<png|jpg|jpeg|webp>")` and a shot's non-null
  `assets.image` must be `assets/shots/shot_NN.<ext>` for its own number; new docs carry `ep/created_at/updated_at`;
  `render_manifest_v1` adds `params{subtitles, encoder}`, `warnings[]`, `ffmpeg{version, machine}`, `output{sha256,
  width, height, fps}`, `framemd5{file, sha256}`, nullable `font`; only `shot`/`end_card` stages may be `cached`
  (with a `cache_key`); `metadata_pack_v1` = `{language, platforms{tiktok|shorts|reels: …}, script_rev,
  render_sha256}`, hashtags carry `#` (3–6), FR requires `title_en/hashtags_en`, EN forbids them;
  `episode_assets_v1.bgm` may be null; `schemas.PLATFORMS` is the one platform list.
- **Stage 2 notes for stages 4/6:** SFX WAVs are 22.05 kHz mono 16-bit (upsample to 48 kHz stereo in the mix);
  `assets/overlays/paper_texture.png` 256×256 8-bit grey, no alpha; `audio_assets.resolve_sfx/pick_track` return
  `abs_path`, `sha256`, `licence` for the manifest; a shared cue lives in its alphabetically-first pack.
- **LOAD done:** five artifacts, `05-phase-4-assets-render-metadata.md`, master spec §0, §2.7–2.11, §3, §4,
  §5.1–5.2, §6, §8, §9, §10, §11, §13 read.
- **EXPLORE map (closed):** render-infra map back (host ffmpeg 6.1.1 arm64 has zoompan/xfade/acrossfade/
  sidechaincompress/loudnorm/ass/drawtext; **CI installs no ffmpeg**; `loudness.TARGET` is TP −1.5 vs spec −1;
  no `word_pop`/`two_line`/zoompan code anywhere — all new; every `clipping/studio` module but `ffmpeg_utils`/
  `helpers` imports cv2/numpy at load; none of the template fonts ship (only Montserrat Black/Regular, Anton);
  no `assets/sfx/`; the 15 `assets/bgm/` mp3s carry no licence record; **no framemd5 clip-parity test exists** —
  RC-E3 is the `git diff --stat` check). Paid-generation map back: no cache, no request-id hook, no booking of
  a post-submit failure (fal raises after `request_id` is known, `images.py:247-271`; `_attempt` returns None);
  booking is the caller's after success (`imaging.book`, `voices._book`, settings chain test); seams = optional
  `cache=` in `_run_candidates` before the paid/limiter gates, optional `on_submit` from `_attempt` (fal between
  `images.py:245/247`), booking in the caller's `on_submit` + skip at success; tests pinning today's booking:
  `test_story_measure.py:457`, `test_story_refimages.py:716`, `test_generation_chain_api.py:216`,
  `test_generation_chain.py:132/197/289`, `test_image_adapters.py:215`; storyboard `assets` is a closed object;
  `EPISODE_ASSET_KINDS = ("voice",)`; `gating.budget_check` passes no `ep_spent` (episode cap not applied).
  Story/API map back: new steps = entries in `steps/__init__.RUNNERS` + removal from `workflow.LATER_STEPS`
  (worker dispatch is generic); `LATER_APPROVALS = ("assets",)`, `LATER_TARGETS = ("shot","line","metadata")`;
  `shot:<ep>:<shid>` collides with `:plan` in `EPISODE_TARGET_DOCS` (needs disambiguation); no staleness link from
  script/storyboard edits to `shot.assets`; `line_offsets` must not be used on a partial storyboard (use
  `episode_pass` scene timings); `CostLedger.append/totals(ep=)` already filter per episode; the story defaults
  agreement test is **four**-place (`tests/test_story_defaults.py`); tests changing on purpose:
  `test_story_workflow.py:288/293/302`, `test_story_workflow_episode.py:129-155`, `test_stories_api.py:660-709`.
  Dashboard/media map back: DEC-048 signs `(job_id, filename, exp)` under `SIGNABLE_PREFIXES = ("/api/outputs/",)`
  (`auth.py:206-334`); Starlette `FileResponse` already serves ranges; story media today is token-gated blob fetch
  (`story_file`, `entity_media`, `episode_voice` in `routes/stories.py`; `fetch*Url` in `api.js`) — fine for PNG/mp3,
  wrong for the mp4 (DEC-048 rejected blob video). Cleanest seam: an additive story-scoped signature with its own
  HMAC context constant. Preview placeholder inline at `EpisodeStudio.jsx:43-52`; `ShotCard` lives inside
  `StoryboardPane.jsx:276-505`; `RegenerateControl` (`fields.jsx:185`) reusable; no per-shot lock exists;
  `CostLedger.episode_view` exists; contract tests in `tests/test_story_payload_contract_episode.py`.
  Bench back (scratch, deleted): 4-core Neoverse-N1, 23 GB; host ffmpeg 6.1.1, **container ffmpeg 7.1.5** (PIL
  12.3.0, every needed filter present) → a golden framemd5 cannot be one value across host/CI/container. 3.0 s shot
  at 4× medium 7.9 s (2× 5.5, 1× 5.2); tiny-input 4× 6.5 s; 1 s ultrafast 1.8 s; 10 s 1080×1920 medium pass 6.7 s;
  3-clip xfade 1.6 s → projected episode render ≈ 200 s at 4× (spec's factor kept).
- **CLARIFY answers (human, 2026-09-28):** (1) **self-made SFX** from a committed generator for every cue of the
  seven templates + **existing BGM** mapped by `bgm_index.json` (licence "shipped with Clips, source unrecorded",
  A-068); (2) **rewrite live ep 1** of `b1104ec66b05` (script + storyboard on the free chain), then assets/render/
  metadata; (3) **I walk and measure, the human watches both episodes on the phone and acknowledges**; (4) paid path
  proven with **fakes only, $0**; Tier-2 shows one paid estimate refused by the cap; the live paid step stays
  deferred.
- **Defaults taken (A-065…A-067, UNCONFIRMED):** Montserrat Black for both styles; TP −1 passed explicitly by the
  renderer; 4× upscale.
- **Open questions:** none.
- **Next free ids:** DEC-173, A-080.

### Stage ledger (phase 4)
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline + worktree | **done** (`86e7f4d` on `main`; local 4486/1, CI 3901/555) |
| 1 | generation cache + journal [Opus] | **done** (local 4562/1, CI 3977/555; 76 new tests) |
| 2 | audio + font assets [Sonnet] | **done** (local 4584/1, CI 3999/555; 22 new tests; +1.9 MB binaries) |
| 3 | documents + store [Opus] | **done** (local 4816/1, CI 4231/555; 232 new tests; 2 phase-3 closed-list pins re-pinned) |
| 4 | timeline + motion + shot builders [Sonnet] | **done** (local 4899/1, CI 4314/555; 83 new tests; 13/13 real ffmpeg sanity renders) |
| 5 | ASS text + fonts [Sonnet] | **done** (local 4983/1, CI 4398/555; 84 new tests; libass proofs checked by eye) |
| 6 | sequence + audio graph (**RISKIEST**) [Opus] | **done** (local 5069/1, CI 4484/555; 86 new tests; real renders of both endings frame- and sample-exact) |
| 7 | runner, manifest, cache, loudness, golden render, CI (+ one push of the branch) [Opus] | **done** (local 5136/1, CI 4551/555; 67 new tests; golden keys host 6.1.1 aarch64, container 7.1.5 aarch64, CI 6.1.1 x86_64 from the pushed run's annotation) |
| 8 | assets step [Opus] | **done** (local 5183/1, CI 4598/555; 47 new tests; phase-3 measurement lifted, `test_story_measure.py` unedited) |
| 9 | render + metadata steps [Opus] | **done** (local 5286/1, CI 4701/555; 103 new tests; e2e 67.3 s FR episode rendered + metadata pack) |
| 10 | fast track + completed terminal [Opus] | **done** (local 5366/1, CI 4766/570; 80 new tests, 15 worker cases skip in CI like every pydantic-backed worker test) |
| 11 | workflow + API [Opus] | **done** (local 5426/1, CI 4787/609; 67 new tests; later-phase pins moved in 6 pre-existing test files) |
| 12 | signed story media [Opus] | **done** (local 5535/1, CI 4828/677; 109 new tests; auth.py +119 lines, none removed) |
| 13 | CLI [Sonnet] | **done** (local 5552/1, CI 4845/677; 17 new tests) |
| 14 | dashboard: storyboard assets + fast track [Sonnet] | **done** (local 5564/1, CI 4857/677; 12 new contract tests; build green; browser check with stage 15) |
| 15 | dashboard: Preview [Sonnet] | **done** (local 5578/1, CI 4871/677; 14 new contract tests; browser check 375/820/1280 by me: 1 overflow + 2 polish fixed) |
| 16 | merge, deploy, Tier-2 (me; human watches on the phone) | **done** (fixes T2-F1/F4/F5/F6/F7/F9/F12; both episodes accepted 2026-09-29; local 5643/1, CI 4936/677) |
| 17 | docs + decisions [Sonnet] | **done** (docs/AI_STORY.md, README, VISION, DEC-151…172, A-069/071…079; Tier-1 local 5643/1, CI 4936/677) |

### Regression contract (phase 4)
RC-P1…P11, RC-S1…S4, RC-T1…T3 and RC-E1…E5 (tables below) stay in force, plus:
| ID | Must keep working | Proven by |
|---|---|---|
| RC-A1 | Clip renderers and the legacy assembler untouched | `tests/test_render_layer_guard.py` (stage 7, committed sha256 manifest) + `git diff --stat 1367d75 -- clipping/studio clipping/story` empty at close (carries RC-E3/RC-P9) |
| RC-A2 | Default generation behaviour unchanged when no cache is passed | unedited: `test_story_measure.py:457`, `test_story_refimages.py:716`, `test_generation_chain_api.py:216`, `test_generation_chain.py:132/197/289`, `test_image_adapters.py:215` |
| RC-A3 | No paid generation without `allow_paid` + budget check incl. the per-episode cap; one submit per paid request; every billed request booked | stage 1/8/10 tests; `test_budget.py`, `test_limits.py`, `test_generation_chain*.py` unedited |
| RC-A4 | Steps 1–9 unchanged except the deliberate `later_phase` edits and the prompt-registry pins | `test_stories_api*.py`, `test_story_steps.py`, phase-3 step/API tests unedited except `test_story_workflow.py:288/293/302`, `test_story_workflow_episode.py:129-155`, `test_stories_api.py:660-709` (+ `:1050/1057`), `test_stories_api_episode.py`, `test_stories_api_phase2.py`, `test_story_episode_steps.py` (stage 11: later-phase examples moved to `memory` / `shot:…:video`, phase-4 grammar re-pinned; none loosened), and `test_story_prompts.py`'s `PROMPT_VERSION`/`MAX_TOKENS`/`SCHEMA_NAMES` pins (M1 added, stage 9 — the same three lines phase 3 changed) (each edit named in the action log) |
| RC-A5 | DEC-048 clip signatures unchanged; a story signature opens exactly one file | `test_auth_token.py` unedited + stage-12 tests |
| RC-A6 | Phase-3 voice measurement identical after the lift | `test_story_measure.py` unedited |
| RC-A7 | Clip loudness unchanged | `test_loudnorm.py` unedited; `loudness.TARGET` unchanged |
| RC-A8 | Phase-3 episode documents on disk still read and validate | store/schema tests unedited except two exact closed-list pins in `test_story_episodes_store.py` re-pinned on purpose at stage 3 (`test_the_episode_documents_and_their_validators`, `test_the_asset_kinds_are_closed`); a scratch copy of live ep01 validated and read at stage 3 (script rev 4, board rev 11, both approved) |
| RC-A9 | Clip auto-BGM unchanged by the index | stage-2 guard: `audio_bgm` reads only mood folders |

---

## CURRENT STATE — AI Story **phase 3 is DONE**. Next: **phase 4**.

- **Stages 0–14 done** on `feat/ai-story-phase-3`, ff-merged to `main`. `main` == `origin/main` == `fb5bbf7`
  (2026-09-28: phase 3 + the live-clocks merge); the container was rebuilt at that head at 0 jobs, health OK. **Pushed 2026-09-28** on the human's word ("Push and merge"), together
  with the live-clocks fix merged beside it (section below); push key per memory `github-push-key`.
- **Tier-1 at close:** local **4477 passed / 1 skipped**; CI env **3892 passed / 555 skipped**; compileall clean;
  vite build green (scratch outDir). Baseline at stage 0 was 3737 / 1 (CI 3172 / 535). After the live-clocks
  merge (2026-09-28): local **4486 passed / 1 skipped**, CI env **3901 passed / 555 skipped** (+9 clock tests).
- **Tier-2:** walked live by me at 375 px on `localhost:8000` (plan §4, 11 steps, all PASS): 23 LLM calls (E1 1,
  E2 8, E3 1, E4 3, T1 10), all Gemini free, 0 retries, 0 over cap, the paid link skipped every time; 15 lines
  measured (Edge 10, Gemini TTS 5, one 429 retried); ep 2 refused naming phase 5; no overflow at 375/820/1280;
  clips still serve (RC-P1). Findings F1–F13 recorded below; F3 (episode length) and F7 (storyboard timing
  ignoring the episode-level window pass) were MAJOR and took two fix rounds (F3) plus one (F7), each re-walked
  and redeployed. **Acknowledged by the human** ("Acknowledged, go to 14", 2026-09-27 ~22:00 UTC).
- **Plan:** `~/.claude/plans/ai-story-phase-3-episode-writer.md` (now headed "Status: done 2026-09-27").
- **Decisions:** DEC-126…DEC-148. **Assumptions:** A-055…A-064 (+ an update line on A-041). **Next free ids:**
  DEC-151, A-065 (DEC-149 left unused; DEC-150 and A-070 belong to the live-clocks fix — never renumber).
- **Docs:** `docs/AI_STORY.md` gains steps 8–9 (episode script, storyboard) and extends the CLI and
  where-it-lives-on-disk sections; `VISION.md` "Where it stands" updated.

### Live data (as left, 2026-09-27)
- Story `b1104ec66b05` is `ready`; Kiwilo and Mangella were re-approved in the UI at 20:07:49 / 20:07:54 (their
  voices had been regenerated live before stage 13 began, which had cleared DEC-123's approval on each).
- Live episode 1 carries the **pre-fix** 39.7 s script and a storyboard whose **stored shot durations predate the
  F7 timing fix** (s01/s04/s10 1.0 s short of the script's own scene durations) — both left **approved**, as the
  human chose; re-timing, re-planning or rewriting the episode picks up every fix at once.
- A `style_preview` job ran on the live story at 20:42:52 UTC from the dashboard (not by me or an agent): 3
  preview images replaced, **awaiting the human's approval**; story status and style approval untouched.
- The fix round's verification copy of the story lives only in the session's scratchpad (`verify/`) — never part
  of the repo or `outputs/`.

### Stage ledger (phase 3)
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline + worktree | **done** (`153e83f` on `main`) |
| 1 | episode templates + script/storyboard schemas | **done** (local 3800 / CI 3235 + 535 skipped) |
| 2 | timing engine | **done** (local 3871 / CI 3306 + 535 skipped) |
| 3 | episode documents in the store | **done** (local 4001 / CI 3436 + 535 skipped) |
| 4 | prompts E1–E4, T1, T1r + context | **done** (local 4119 / CI 3554 + 535 skipped) |
| 5 | shot resolution + fast storyboard + rule pass | **done** (local 4205 / CI 3640 + 535 skipped) |
| 6 | step runners (RISKIEST) | **done** (local 4280 / CI 3715 + 535 skipped) |
| 7 | opt-in voice measurement | **done** (local 4302 / CI 3737 + 535 skipped) |
| 8 | workflow + API | **done** (local 4359 / CI 3774 + 555 skipped) |
| 9 | CLI | **done** (local 4375 / CI 3790 + 555 skipped) |
| 10 | dashboard: Tabs + EpisodeStudio script pane | **done** (local 4393 / CI 3808 + 555 skipped; build green; browser check in stage 11) |
| 11 | dashboard: storyboard pane + preview placeholder | **done** (local 4407 / CI 3822 + 555 skipped; build green; 375/820/1280 reviewed on the phase3 throwaway) |
| 12 | E-prompt bench (free links only) | **done** (local 4416 / CI 3831 + 555 skipped); live bench found E1 0/3 on both free links → stage 12b |
| 12b | E1 asks for an exact numbered scene list; validator accepts the legal range | **done** (local 4430 / CI 3845 + 555 skipped); live E1 Gemini 3/3, NVIDIA 1/3 |
| 13 | merge, deploy, Tier-2 (me at 375 px, human acks) | **done** (2 fix rounds: F3 ×2 (length), F1 (elisions), F7 (storyboard timing), F5/F6/F8/F9/F11 (UI + estimate); redeployed `d466f83`) |
| 14 | docs + decisions | **done** (DEC-126…148, A-055…064, `docs/AI_STORY.md`, `VISION.md`, this close-out) |

### Regression contract (phase 3)
RC-P1…P11, RC-S1…S4 and RC-T1…T3 (tables below) stay in force, plus:
| ID | Must keep working | Proven by |
|---|---|---|
| RC-E1 | Steps 1–7 unchanged | `test_stories_api.py`, `test_story_steps.py`, phase-2 step/API tests unedited, except tests asserting `script`/`storyboard`/`scene…` answer `later_phase` (changes on purpose; each edit named in the action log) |
| RC-E2 | Episodes never change the story's `approvals` or `status` | new tests (stages 3, 8) + phase-2 status tests unedited |
| RC-E3 | Render layer and the legacy assembler untouched | `git diff --stat 4280333 -- clipping/studio clipping/story` empty |
| RC-E4 | No paid LLM call from an episode step without `allow_paid` | stage-6 tests + the DEC-115 tests unedited |
| RC-E5 | Reference images keep stripping names exactly as before | `test_story_refimages*.py` unedited after the helper lift |

### Follow-ups, deliberately not done
- F2 the free model occasionally garbles a French diacritic (`trâne` → `tr¤ne`) in raw output — no code path touches it.
- F10 the skipped-paid-link note lives only in the estimate chip's tooltip, not the button row itself.
- F12 E3 sometimes echoes a neighbouring scene's line into the hook or the cliffhanger.
- F13 T1 sometimes answers a short scene with a single shot (expected 2–4 per DEC-133); "Plan remaining with T1"
  finishes it on a later call when the first is rejected or fails.
- `workflow.patch_script` and a scene regenerate re-time the script but not the storyboard: a line edit that
  shifts the episode-level window pass leaves other scenes' shots off until the next re-plan (the edited scene
  itself is stale, so the board can't be approved meanwhile).
- `timing.line_offsets` refuses a partial storyboard (its boundary rule is strict) — phase 4 must place lines
  using `episode_pass`'s own boundary rule instead.
- `prompts.repair_fr_elisions` skips a text that already holds any apostrophe, even one unrelated to an elision.
- A voice's rate or pitch change does not trigger re-measurement (`timing.voice` records only provider/voice_id).
- `workflow.py` calls the private `prompts._t1_shot_errors` / `_TAG_PATTERN` to keep an edited shot's tag rules
  identical to T1's own.
- The fast storyboard's over-clamp fallback (drops a trailing wordless shot) and a both-protected repeated
  framing (logged with a note, not fixed) — `shots.py`'s `fast_plan` / `rule_pass`.
- The Clips "Live activity" timer keeps running on an already-completed job (pre-existing, not phase 3; a
  separate task was offered and not taken up).
- `pyproject.toml`'s `addopts` already carries `-q` — never pass `-q` again, it hides the pass/fail count line.
- Carried from earlier phases: the Settings per-task route selector (DEC-112); the clip-upload token-before-spool
  fix; `generation.in_container()` reading its file twice; the CLI's progress output not probing local editors; a
  day-plate regenerate not remaking its derived variants; `prompts._cast_section`/`_places_section` trimming
  without naming it in `pack.trimmed`; `series_memory.relationship_state` keys not cleaned on delete; `jobs.json`
  atomic write; tests inheriting a real `.env`; `cost_ledger.json` file mode 0600; concept diversity.

### Where phase 4 starts
Read the spec's phase-4 brief and DEC-126…148 first (episode timing, approvals, tags/handles, the measurement
and step-budget rules all carry forward unchanged). Phase 4 renders episode 1: the Tier-1 assets pipeline and
renderer, the metadata pack (MVP), and the generation cache that never loses a paid generation (DEC-106's
follow-up, carried since phase 0). The Preview tab's placeholder ("Rendering arrives in phase 4.") is exactly
what phase 4 replaces.

---

## DONE — Live activity clocks freeze on finished jobs (2026-09-27; merged, deployed and pushed 2026-09-28)
- **Where:** branch `Feature/frosty-spence-4ddbf5` (worktree `.claude/worktrees/magical-greider-2955e5`): checkpoint
  `2282372` (on `ede5116`), fix `bc324c9`, docs `2319aa4`. **Merged** into `feat/ai-story-phase-3` and `main` with a
  merge commit on 2026-09-28 (human: "Push and merge"; `.claude/*` conflicts resolved keeping both sides), deployed
  with the phase-3 code (backend rebuilt at 0 jobs) and pushed with it.
- **Fix:** `jobClocks` in `web/dashboard/src/time.js`; `LiveActivity` uses it. Finished job (TERMINAL, awaiting
  included): no "on this step", total = `created_at` → earlier of `updated_at` and the last feed line (DEC-150,
  A-070). Running jobs unchanged.
- **Tier-1:** baseline local 4430/1 skipped, pytest-only venv 3820/580 → now **4439/1** and **3829/580**; compileall
  clean; vite build green (scratch outDir). This worktree has a stale git-ignored `web/dashboard/dist/` (Sep 18):
  move it aside before running the suite (Operating notes).
- **Tier-2:** PASS on live data at 375 px (action log): clip `b37b36a9b34e` "54m 29s total"; approved story step
  "2s total" (approved 9 h later); cancelled "42s"; awaiting "0s". Container untouched.
- **Tier-3:** `tests/test_dashboard_activity_clocks.py` (8 Node-run cases, skip without Node/`node_modules`; 1 guard).
- **Regression contract:** RC-L1 running jobs keep both ticking clocks — `test_a_running_job_keeps_both_clocks_ticking`;
  `test_dashboard_story_shared.py` unedited and green.
- **Follow-ups (action log):** finished story steps' headline "Waiting to start..."; "Live activity" title on
  finished jobs; the wizard's step-job row time is the approval time for approved steps.
- **Open questions:** none.

## CURRENT STATE — AI Story **phase 2 is DONE** and pushed. Next: **phase 3**.
- **Stages 0–12 done** on `feat/ai-story-phase-2`, ff-merged to `main` (last code commit `d20fa28`), deployed
  (container rebuilt at 0 jobs). **Pushed** 2026-09-27 at the human's word ("Push it we'll fix it later").
- **Tier-1 at close:** local **3737 passed / 1 skipped**; CI env **3172 passed / 535 skipped**; compileall clean;
  vite build green. Baseline was 2846 / 2368. Never run the suite with a built `web/dashboard/dist/` on disk.
- **Tier-2:** walked live by me at 375 px (table below): PASS after one fix (voice age) and a polish round; the
  voice listening on the phone is **deferred by the human** ("we'll fix it later") — still open.
- **Plan:** `~/.claude/plans/ai-story-phase-2-cast-places-season.md`. Prompt: `03-phase-2-cast-places-season.md`.
- **Decisions:** DEC-117…DEC-125. **Assumptions:** A-050…A-054. **Next free ids:** DEC-126, A-055.
- **Docs:** `docs/AI_STORY.md` steps 5–7; VISION updated.
- **Open task chip:** "Check the token before clip uploads spool" (pre-existing clip upload issue, found in stage 7).

### Where phase 3 starts
Read the spec, `04-phase-3-episode-writer.md`, DEC-117…125 and the follow-ups. The live story `b1104ec66b05`
(FR, Tentafruit) is `ready` with a cast of 3 (prompt-only sheets), 2 places, 1 prop and an 8-episode arc — a
natural fixture for the episode writer.

### Follow-ups, deliberately not done
- Settings per-task route selector (DEC-112 — carried again; no phase-2 need surfaced).
- Clip upload routes spool before the token check (task chip).
- `generation.in_container()` reads the file twice, so its containerd check never matches.
- The CLI's progress output does not probe local editors (the API does).
- A day-plate regenerate does not remake the variants derived from it (portraits do).
- `prompts._cast_section/_places_section` cut > 12 characters / > 8 places without naming it in `pack.trimmed`.
- `series_memory.relationship_state` keys are not cleaned on delete (nothing writes them before phase 3).
- From phase 1: concept diversity; `jobs.json` atomic write; tests inherit a real `.env` (conftest now resets the
  ComfyUI cache only); ledger file mode 0600.

### Tier-2 (phase 2) — live, walked by me at 375 px (2026-09-26 23:30–23:46 UTC)
Deployed: ff `feat/ai-story-phase-2` → `main` (`c855446`, then `72d3140` for the voice fix), container rebuilt/restarted at 0 jobs.
Story `b1104ec66b05` (FR, Tentafruit, fruit_drama locked from phase 1; its phase-1 story.json upgraded in memory).
| # | Step | Result |
|---|---|---|
| a | Cast estimate before anything runs: 3 LLM · 3 images · 6 edits · voice ~360 chars, $0.00; edit chain not ready with every reason, incl. **paid editors refused with their numbers** (est $0.202 gemini/nano-banana-2-lite, $0.180 fal/seedream-4-edit; allow_paid off) | **PASS** |
| b | Cast of 3 ticked from the sketch (Kiwilo, Mangella, Broccolia): K1 ×3 on Gemini free (≈294–317 of 750), portraits on Pollinations ($0.000, base), sheets **stopped before any call** ("needs an editor"), 3 distinct FR Edge voices pinned, samples made; 🟡 line | **PASS** |
| c | FINDING: Broccolia (female, elder) got fr-FR-EloiseNeural (young) — age scored all-or-nothing, catalogue order broke the tie → fixed (`72d3140`, age distance); re-picked in the UI: top alternate fr-FR-VivienneMultilingualNeural (adult) → voice regenerate + sample | **FIXED + PASS** |
| d | Upload for Mangella (a style-preview JPEG via the API — the browser cannot pick files): stored as a uuid PNG with no metadata; `character:char_mangella:text` → U1 on gemini/flash-lite (free, 2.6 s) described it, K1 ran with the notes (followed the colours; a conflicting human-looking reference is bent to the fruit style — A-entry) | **PASS** |
| e | Banner offered the prompt-only switch (confirm) → mode prompt_only → Continue cast: 6 sheets via t2i with each portrait's seed, labelled prompt-only, nothing redone | **PASS** |
| f | Approve characters (UI) → `cast_approved`; places proposal (P0 ≈192/420) edited in the UI to 2 places + 1 prop → places job (P1 ≈128/121 of 260, R1 ≈42/100, 3 free images base); night variant for Le Parloir (prompt-only, the plate's seed); approve → `places_approved` | **PASS** |
| g | Season: 8 episodes (S1 ≈297/950, S2 ×8 ≈109–176/350), arc coherent (setup, midpoint twist ep 4, climax ep 8, uses the new places) → approve → **`ready`**, final card | **PASS** |
| h | Files: each character folder = character.json + portrait/turnaround/expressions + voice_sample.mp3, all validate; ledger 24 rows, 0 paid, $0.00 (9 character images, 4 samples, 3 place images, 1 prop, 1 upload description, 6 phase-1 previews); 32 LLM lines, 0 over cap, the paid link skipped and printed every time; no spend.json | **PASS** |
| i | 375 px: no overflow on the story at every step; three voice samples **played on the human's phone and distinct** | **PENDING (human)** |
Polish findings (fixed in `d20fa28`, live-checked: empty slots say Make): prompt-only sheets estimated as "edits"; the places estimate ignores the edited list; empty variant slots say "Regenerate" instead of "Make"; icon-only ✕ buttons without aria-label.

### Regression contract (phase 2)
RC-P1…P11 and RC-S1…S4 stay, plus:
| ID | Must keep working | Proven by |
|---|---|---|
| RC-T1 | Steps 1–4 unchanged | `test_stories_api.py`, `test_story_steps.py`, `test_style_preview.py` unedited except moving grammar tuples |
| RC-T2 | Clip voiceover output unchanged by the rate/pitch kwargs | existing voiceover/TTS tests unedited + a call-shape guard |
| RC-T3 | No paid generation without `allow_paid` + budget check | `test_generation_chain*.py`, `test_budget.py` unedited + stage-5 tests |

### Stage ledger (phase 2)
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline + worktree | **done** `5d20a8a` |
| 1 | entity documents + status | **done** (local 3242 / CI 2764 + 448 skipped) |
| 2 | prompts K1/P0/P1/R1/S1/S2/U1 | **done** (local 3362 / CI 2884 + 448 skipped) |
| 3 | voices | **done** (local 3399 / CI 2921 + 448 skipped) |
| 4 | upload handling + vision describe | **done** (local 3456 / CI 2978 + 448 skipped) |
| 5 | reference images across routes (RISKIEST) | **done** (local 3525 / CI 3047 + 448 skipped) |
| 6 | step runners | **done** (local 3556 / CI 3078 + 448 skipped) |
| 7 | API | **done** (local 3639 / CI 3081 + 528 skipped) |
| 8 | CLI (+ clean dangling ids on entity delete) | **done** (local 3686 / CI 3128 + 528 skipped) |
| 9 | dashboard: CastEditor | **done** (local 3701 / CI 3138 + 533 skipped; build green; browser check with stage 10) |
| 10 | dashboard: PlacesProps + SeasonBoard + wiring | **done** (local 3725 / CI 3162 + 533 skipped; build green; 375/820/1280 checked, 2 findings fixed) |
| 11 | merge, deploy, Tier-2 | **done** (walk passed; voice age fixed `72d3140`; polish `d20fa28`; human voice check pending) |
| 12 | docs + decisions | **done** (DEC-117…125, A-050…054, docs/AI_STORY.md, VISION) |

---

## CURRENT STATE — AI Story **phase 1 is DONE**. Next session starts **phase 2**.

Finished 2026-09-26 ~17:40 UTC. Stages 0–13 done; Tier-2 passed live after one fix
round (DEC-116). The human asked to push and start phase 2.

- **Branch:** `main`, fast-forwarded from `feat/ai-story-phase-1` (worktree
  `.claude/worktrees/ai-story-phase-1`, can be removed once pushed). **Push:** see
  the action log (the human asked for it; the repo's own key, memory
  `github-push-key`).
- **Deployed:** container rebuilt from the phase-1 head (dashboard bundle
  `index-BpzgkBO5.js`); 0 jobs at every restart.
- **Tier-1 at close:** local **2846 passed / 1 skipped**; CI env **2368 passed /
  448 skipped**; `compileall clipping web tests main.py` clean; `vite build` green.
  Baseline was 1883 / 1683. Run the suite **without** a built
  `web/dashboard/dist/` on disk (see Operating notes).
- **Plan:** `~/.claude/plans/ai-story-phase-1-workspace.md` (approved "Go").
  Spec `.claude/plans/ai-story/00-MASTER-SPEC.md` v1.1 — phase 2 prompt is
  `03-phase-2-cast-places-season.md`.
- **Decisions:** DEC-107…DEC-116. **Assumptions:** A-040…A-049 (A-004 invalidated).
  **Next free ids:** DEC-117, A-050.
- **Docs:** `docs/AI_STORY.md` (new: steps 1–4 guide), README "Two modes",
  VISION rewritten for the two-mode product (phase 0 had left all three undone).
- **Open questions:** none. The human did not walk the wizard on their phone; the
  substitute run is DEC-116.

### Where phase 2 starts
Read the spec, `03-phase-2-cast-places-season.md`, DEC-107…116 and the follow-ups
below. Carried in deliberately: the Settings per-task route selector (DEC-112);
signed story media for audio/video (DEC-113, phase 4); LLM spend is not estimated
or booked when `allow_paid` is on (DEC-115); concept diversity (below).

### Follow-ups, deliberately not done
- **Concept diversity:** with no seed text and no style, all ten generated
  concepts came back `cinematic_real` with similar mystery themes. Rotate the
  requested style per C1 call when none is chosen.
- `jobs.json` is written non-atomically (`web/api/store.py:113`, DEC-110);
  `needs_upload` is still failed at restart (pinned by a test so a fix changes it
  on purpose).
- Tests from a worktree inherit the main checkout's `.env` (A-049): add a conftest
  that clears provider keys.
- `ledger.py` writes `cost_ledger.json` 0600 (mkstemp): unreadable from the host
  when the container writes it.
- A French native review of the concept library (A-045); measure French
  tokenisation (A-046).
- CLI has no `choose`/`regenerate` subcommands (the bible's partial-failure
  message names a regenerate target the CLI cannot run).
- From phase 0, still open: the deferred paid Tier-2 step; dependency pass
  (setuptools ≥ 83, pip-audit finding); `$0.000` sub-cent refusal text.

### Operating notes that bite
- **A built `web/dashboard/dist/` on disk breaks ~15 auth/clip-serving tests**
  (the app mounts the SPA at import; fixtures assume no mount). CI has none; build
  to a scratch `--outDir`, or move `dist/` aside before running the suite.
- A throwaway backend for UI checks: `.claude/launch.json` config
  `phase1-throwaway` (excluded from git in `info/exclude`) runs the worktree on
  127.0.0.1:8010 with every provider key blanked and IMAGE_CHAIN local-only.
- Container / deploy / push notes of phase 0 (below) still apply: a Python fix
  needs `sudo -n docker compose restart backend`, a dashboard change
  `rm -sfv backend && up -d --build backend`, both only at 0 jobs.

### Tier-2 (phase 1) — live, on the deployed container
Deployed: `git merge --ff-only feat/ai-story-phase-1` → `main` `d3d05f4` (not pushed);
`sudo -n docker compose rm -sfv backend && sudo -n docker compose up -d --build backend` at 0 jobs;
startup restored 4 saved settings. Before: `data/usage.json` sha256 `babfee13…f743`, no `spend.json`.
| # | Step | Result |
|---|---|---|
| a | New dashboard bundle served; `/api/stories`, `/api/stories/styles` (7), `/story` 200 | **PASS** |
| b | Live bible job on the free chain: 3 calls on `gemini/gemini-3.5-flash-lite`, every call printed its hop and `≈N tokens out (cap …)` (133/250, 223/250, 111/200), `awaiting_approval` in ~15 s | **PASS** |
| c | `docker compose restart backend` with that job awaiting → still `awaiting_approval`; approve → `bible_approved`, job `completed` | **PASS** (acceptance) |
| d | `story.json` validates (`story_bible_errors == []`); delete of a throwaway story removed only its folder + index entry; check story then deleted (1 step job removed) | **PASS** |
| e | `usage.json` byte-identical after the LLM calls; no `spend.json`, no `chain_test_ledger.json` | **PASS** |
| f | RC-P1: `/clips/job/b37b36a9b34e` at 375 px no overflow, 7 videos, `highlight_rank_1_ready.mp4` loads (duration 44.9 s, range 206 video/mp4); playback not observable (the browser pane was hidden) → on the phone checklist | **PASS (serving)** |
| g | 375/820/1280, pre-deploy on a keyless throwaway backend: no overflow on /story, /story/new, three wizard states, /clips, /settings; create/choose/approve/save/lock work; 5 UX findings fixed and re-checked | **PASS** |
| h | FR story → Generate 10 more → pick `tentafruit_island` → write bible → regenerate one field with a note → approve → `fruit_drama`, change one accent, save → preview → approve & lock. Not walked by the human ("if it's good, push"); run by me in the built-in browser at 375 px | **FAILED at Generate 10 more**: every C1 reply truncated at the 500-token cap (2 French cards need ~1,000), 3 Gemini attempts, then 1 attempt on the **paid** OpenRouter link (~$0.0001, untracked) before I cancelled job `8650b2350c2b`. Story `b1104ec66b05` kept for the re-run |
| h′ | Same script after the fixes (DEC-107, DEC-115, two dashboard fixes), story `b1104ec66b05`, 375 px | **PASS**: 10 concepts in 24 s (C1 ≈290–335 of 700 each, the paid link skipped and printed every call); `tentafruit_island`; bible B1/B2/B3 ≈151/266/122 of 400/520/300; regenerate tone ("plus sombre" → "Sombre, cynique, …") and premise ("plus court"), only the target field changed, page refreshed itself; approve; `fruit_drama` with accent `#ffd400` (overrides = only that); preview 3/3 on Pollinations $0.00 (3 s, 44 s, 46 s; repeat 0.2 s from cache); approve & lock → `style_approved`, controls disabled, no overflow. story/style_lock/concepts/style_preview JSON validate; ledger 6 free entries; 15 LLM lines, 0 over cap; no `spend.json` |
Fix round (attempt 1 of 2, succeeded): C1 one concept per call + French-sized caps + story steps skip paid LLM links (`da7ce01`); the stream hook reacts to a terminal progress frame (`6f0f068`); the wizard polls its story while a step is in flight (`bd16eec`). Each deployed at 0 jobs.

### Regression contract (phase 1)
Phase 0's RC-P1…RC-P11 (history table below) stay in force, plus:
| ID | Must keep working | Proven by |
|---|---|---|
| RC-S1 | A running clip job is still failed at restart; `needs_upload` unchanged | `tests/test_stale_jobs.py` existing cases unedited |
| RC-S2 | A clip job's record, response and SSE unchanged apart from `kind: "clip"` | `test_job_stream.py`, `test_web_reuse_bypass.py`, `test_dashboard_payload_contract.py` unedited |
| RC-S3 | Story-step jobs obey the clip queue cap and key gate | `test_queue_cap.py` unedited + story cases in `test_stories_api.py` |
| RC-S4 | `llm.py` and its log wording untouched | `git diff 25abdd1 -- clipping/providers/llm.py` empty; `test_preflight.py` unedited |

### Stage ledger (phase 1)
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline + worktree | **done** `c6bb6c1` |
| 1 | templates as data (7 styles, 10 concepts, schemas) | **done** (stage-1 commit; local 1968 / CI 1768 + 173 skipped) |
| 2 | prompting.py + style lock builder | **done** (local 2043 / CI 1843 + 173 skipped) |
| 3 | story store (+ reserved `outputs/stories` guard, see action log) | **done** (local 2268 / CI 2052 + 189 skipped) |
| 4 | story-step jobs (RISKIEST) | **done** (local 2324 / CI 2056 + 238 skipped) |
| 5 | story prompts + context pack | **done** (local 2401 / CI 2133 + 238 skipped) |
| 6 | LLM step runners | **done** (local 2450 / CI 2182 + 238 skipped) |
| 7 | stories API | **done** (local 2627 / CI 2184 + 413 skipped) |
| 8 | style preview strip | **done** (local 2677 / CI 2213 + 434 skipped) |
| 9 | CLI (+ shared `workflow.py` so API and CLI apply one set of rules) | **done** (local 2778 / CI 2313 + 435 skipped) |
| 10 | dashboard shared pieces | **done** (local 2792 / CI 2327 + 435 skipped; build green) |
| 11 | StoriesList + NewStoryWizard (+ `GET /api/stories/styles`) | **done** (local 2807 / CI 2339 + 438 skipped; build green; 375/820/1280 checked) |
| 12 | merge, deploy, Tier-2 | **done**: step h failed, fixed in one round (3 commits), h′ passed |
| 13 | docs + decisions | **done** (DEC-107…116, A-040…049, docs/AI_STORY.md, README, VISION) |

---

## History below this line
Phase 1's record is above; phase 0's closing state and every earlier task follow.

## Previous: AI Story **phase 0** (DONE 2026-09-26)

Finished 2026-09-26 10:45 UTC. Every Tier-2 step has run except the one paid
call, which the human **deferred** (no budget right now, "we'll do it later").
Stages 0–14 done, plus the finish-up stage (`32f8346`: DEC-106).

- **Tier-1 at close (on main at `32f8346`, run today):** local **1883 passed /
  1 skipped**; CI env (`PYTHONNOUSERSITE=1`, pytest-only) **1683 passed / 173
  skipped**; `compileall clipping web tests main.py` clean. +4 on the phase-0
  close baseline (1879 / 1679), all four from the DEC-106 test, which runs in
  CI too. No dashboard change, so no vite build this round.
- **Deployed:** backend restarted 10:25 UTC at 0 jobs. The container runs
  `32f8346`'s Python (checked: `_attempt` has the `paid` parameter). The
  dashboard bundle is unchanged since the stage-13 rebuild.
- **Branch:** `main`, pushed to `origin/main`. Verify with `git status -sb`.
- **Plan:** `~/.claude/plans/pasted-content-id-a0ee-the-spec-resilient-tulip.md`
  (v2; v1 `ai-story-phase-0-foundation.md` is superseded).
- **Spec:** `.claude/plans/ai-story/00-MASTER-SPEC.md` v1.1, plus
  `09-APPENDIX-research-2026-09-25.md` and `10-REFERENCE-ANALYSIS-2026-09-25.md`.
- **Decisions:** DEC-093…DEC-105 (stage 14) and **DEC-106** (a paid link gets
  exactly one attempt). Assumptions A-030…A-039.

### Tier-2 results (2026-09-26)
| # | Step | Result |
|---|---|---|
| 1 | Paste `FAL_KEY`, survives reload | **PASS**, done by the human 00:31 UTC; `fal_key_set: true` on every later GET, `data/settings.json` 0600 |
| 2 | `allow_paid` off → IMAGE_EDIT_CHAIN refusals, no network call | **PASS** twice (before and after the DEC-106 restart): verdict `blocked`, comfyui unreachable, refusals at $0.034 / $0.030 / $0.040 / $0.067 with "today $0.00 of $3.00", elapsed ≤ 0.01 s, `usage.json` hash unchanged, no ledger, no spend file |
| 3 | **One paid fal call** | **DEFERRED by the human.** Not run. See below |
| 4 | Edge TTS sample playable on the phone | **PASS by substitute:** the built-in browser at 375 px with an Android user agent, TTS chain test → `edge/fr-FR-HenriNeural` ✅ 1.2 s, the 3.8 s MP3 **played** (`currentTime` advanced). The human's own phone listen (00:33 UTC fetch) was never confirmed |
| 5 | Clone & Rerun of `b37b36a9b34e` completes; layout at 375 | **PASS:** started by the human 09:29 UTC, COMPLETED 10:23:36 UTC, 7 clips of 40–51 s, 1080×1920, clip 1 plays. `/clips/job/b37b36a9b34e` measured at 375 / 820 / 1280: `scrollWidth == clientWidth`, no overflowing element. The phone check itself was not confirmed. **Script correction:** the rerun keeps its id **by design** (DEC-022), and "`<new id>`" was wrong |

Also run: `allow_paid` is **off** and the effective profile is `free`.
`data/usage.json` counted today's free TTS calls (edge 2, gemini 2,
pollinations 1). No `spend.json` and no `chain_test_ledger.json` exist, so
**no paid call has ever been made by this code.**

### ⚠️ The deferred paid step — run it when there is budget
The paid path is proven by unit tests and by refusals only. Nothing in phase 1
depends on it. It costs est. **$0.03**. It needs an explicit yes in chat,
because it spends money.
1. Settings → Budget: turn `allow_paid` on (the effective profile becomes
   `one_dollar`). **Snapshot `data/usage.json` immediately before**
   (`sha256sum`). It resets at the UTC day boundary, so an older hash is worthless.
2. Press **Test (est $0.030)** on `fal/seedream-4-edit` **once**, or
   `POST /api/settings/test-generation-chain`
   `{"kind":"image_edit","chain":"","link":"fal/seedream-4-edit"}`.
3. Expect: one 9:16 image in the row; one entry in
   `data/chain_test_ledger.json` (`provider fal, model
   fal-ai/bytedance/seedream/v4/edit, unit image, qty 1, est_usd 0.03, paid
   true`); $0.03 today in `data/spend.json`; `data/usage.json`
   **byte-identical** (`cmp`). That last one is the proof that a paid call
   consumed no free allowance (DEC-098).
4. Turn `allow_paid` back **off**: DEC-105 lets every tailnet device reach
   Settings with no token.
Since DEC-106, a failure on that link is **not retried**. It says "paid link:
not retried". Check the fal dashboard before pressing again, because a failed
attempt may still have been billed.

Reachable from the tailnet at `http://main-network-interface.tail01346d.ts.net:8000`
or `http://100.112.96.111:8000`, **with no token** (DEC-105). Settings is at
`/settings` (`/clips/settings` lands on the dashboard).

### Where phase 1 starts
Read the spec and the plan first. Neither is summarised here on purpose.
Carried in deliberately from phase 0:
- **Per-task route selector:** deferred to phase 1. The `route=` parameter is
  already reserved in `run_generation_chain`, and the gate order honours it.
- **VIDEO_CHAIN has no adapter** (DEC-102) until phase 6. It parses and tests.
- Phase 0's regression contract (RC-P1…RC-P11, table below) still applies.
  RC-P10 is now also proven by
  `test_generation_chain.py::test_a_paid_link_is_never_retried_so_one_click_cannot_bill_twice`.

### Close-out checks
Re-run today: `git diff --stat 429c9e7 -- clipping/studio` **empty** (RC-P9);
`npm audit --omit=dev` **0**; the Tier-1 checks above.

Carried forward from the stage-13 run (`pip-audit` is no longer installed in
the container, and this round changed no dependency):
- `pip-audit` 2.10.1: **1 finding**, setuptools 79.0.1 PYSEC-2026-3447 /
  CVE-2026-59890 (fix 83.0.0). It's an sdist-build bug on macOS filesystems, so
  it isn't reachable here. It goes to the dependency pass, not phase 0.

### Operating notes that bite
- **The container runs the bind-mounted source.** A Python fix needs
  `sudo -n docker compose restart backend` (check `GET /api/health` says 0 jobs
  first); `voiceover` is cached in-process. A dashboard change needs the full
  `sudo docker compose rm -sfv backend && sudo docker compose up -d --build backend`
  — `-v`, never `down -v`, which would delete the Caddy certificates.
- **CI is `pip install pytest` and nothing else** (DEC-012), where ~173 tests
  skip. A green local run proves less than it looks. Reproduce it:
  `pip install --target /tmp/cilibs pytest` then
  `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest`. Don't add `-q`:
  `addopts` already has one, and a second hides the pass/fail summary line.
  For a *drift guard*, read the source as text rather than `importorskip` — an
  importorskip means the guard never runs in the one place that checks every push.
- **Pushing needs the repo's own key** (`github_osc_better` **and** `-F /dev/null`;
  see memory `github-push-key`) — the ssh_config identity is an Adversium deploy
  key that silently wins otherwise.
- **Known, pre-existing, not from phase 0:** running `tests/test_clip_serving.py`
  before `tests/test_auth_token.py` fails
  `test_traversal_attempts_over_http_are_refused[…%2e%2e…]` — the SPA fixture's
  route swap leaks into the auth client. Alphabetical order passes, so CI never
  sees it. Present at `d960525`.

### Follow-ups, deliberately not done
`pyproject.toml` has no `[build-system]`/package-data; setuptools ≥ 83 in the
image; the budget refusal text prints sub-cent estimates as `$0.000` (cosmetic);
per-task route selector (phase 1); Freesound toggle (phase 4); `httpx` is
transitive only. **A paid generation must never be lost** (DEC-106's open gap: a
paid attempt that fails after the provider accepted it may be billed, unrecorded
and lost). The human's direction for **phase 4**: a generation cache keyed by the
inputs, the provider request id journaled at submit so a retry resumes instead of
re-submitting, and spend booked at submit. Written into
`.claude/plans/ai-story/05-phase-4-assets-render-metadata.md` §1 (Asset generation).

---

### Earlier tasks below this line
Everything that follows is the stage-by-stage record of phase 0 and of the tasks
before it. Read it for the contracts each stage established — they are the
detail this header deliberately does not repeat.

### Regression contract (phase 0)
| ID | Must keep working | Proven by |
|---|---|---|
| RC-P1 | Existing clip flow: create, stream, serve, clone & rerun, cancel, delete — under `/clips/*` after stage 2 | `tests/test_job_stream.py`, `test_clip_serving.py` (SPA rows move to `/clips`), `test_web_reuse_bypass.py`, `test_auth_token.py` media tests + one real job in Tier-2 |
| RC-P2 | Existing Settings: keys persist, empty clears, chain test verdicts | `test_settings_store.py`, `test_chain_gate_api.py`, `test_dashboard_payload_contract.py` |
| RC-P3 | Auth: every router token-gated, `/api/health` open, media signatures | `tests/test_auth_token.py` (AST guard, MC-6, MC-7) |
| RC-P4 | Legacy story-clip assembly: flag parses, loader works, `main.py` branches | `tests/test_story_loader.py`, new label test (stage 3), `main.py --help`; end-to-end **UNVERIFIED** (sample sources carry no media) |
| RC-P5 | LLM chain semantics: parser, probes, readiness, swap | `tests/test_preflight.py` **unedited**, `test_llm_negotiation.py`, `test_provider_registry.py`, `test_chain_readiness.py`, `test_model_fallback.py` |
| RC-P6 | Branding guard: slug allowed, old name forbidden, CLI entry points | `tests/test_branding.py` |
| RC-P7 | Dashboard mount last; token never in a URL; SPA fallback | `test_auth_token.py::test_the_dashboard_mount_is_the_last_route_registered`, `::test_the_token_never_travels_in_a_query_string`, `test_clip_serving.py` SPA rows |
| RC-P8 | Suite runs with pytest alone (DEC-012) | CI-env run after every stage |
| RC-P9 | Render layer untouched | `git diff --stat 429c9e7 -- clipping/studio` empty at close-out |
| RC-P10 | No paid call by default; free counters count free calls only; no chain test spends more than one paid call | `test_budget.py`, `test_limits.py`, `test_generation_chain_api.py` (stages 5, 6, 11), Tier-2 step 4 |
| RC-P11 | DEC-092 override still opens this machine without a token | `test_auth_token.py` (both DEC-092 tests), Tier-2 step 1 |

### Stage ledger
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline | **done** (`429c9e7` + this header commit) |
| 1 | rename to rzdhop AI | **done** `d960525` |
| 2 | two-mode shell (RISKIEST) | **done** `3336932` |
| 3 | relabel legacy story-clip "Story Clip (assembly)" | **done** `5c5a287` |
| 4 | generation chain core (`providers/generation.py`) | **done** `9375bc0` |
| 5 | pricing + free-tier limits | **done** `3bb9201` |
| 6 | budget + cost ledger (caps 1.00 / 3.00 / 10.00) | **done** `7b1bdfa` |
| 7 | image adapters | **done** `d3ce4bb` |
| 8 | TTS + vision adapters | **done** `7e80b54` |
| 9 | local ComfyUI / Ollama clients + 3 workflow templates | **done** `4028237` |
| 10 | hardware profiler + `GET /api/hardware` | **done** `545af1b` |
| 11 | settings API (keys, budget fields, test-generation-chain) | **done** `c5a7995` |
| 12 | settings UI (four tabs) | **done** `533647c` |
| 13 | deploy + Tier-2 (plan §5, human ack) | **done** `c8d5574` (live fixes); Tier-2 finished 2026-09-26 except the paid step, deferred by the human |
| 14 | docs + decisions (DEC-093…, A-030…) | **done** `68e9890` |
| 15 | finish-up: a paid link gets one attempt (DEC-106) + the remaining Tier-2 | **done** `32f8346` + the close-out docs commit |

### Deploy
`sudo docker compose rm -sfv backend && docker compose up -d --build backend` — only when no job runs (the container executes the bind-mounted on-disk Python; the dashboard needs the rebuild). Never `down -v` (Caddy certs). `.claude/` is dockerignored.

---

## CURRENT TASK — every key tested with the real request; a chain that survives a retired model (COMPLETE, awaiting Tier-2 ack)
- **Phase:** DOCUMENT done. All stages committed on main; deployed 2026-09-24 13:12 UTC.
- **Plan:** `~/.claude/plans/zany-bouncing-brook.md` (approved in chat 2026-09-24).
- **Checkpoint commit before the work:** `8fa873d` (clean tree).
- **Tier-1:** baseline 1361 -> **1444 passed**; CI env **1292 passed / 124 skipped**;
  compileall clean. Every behaviour test verified to FAIL before its change; guards named.
- **Tier-2:** no browser E2E suite exists in this repo. Substitutes, all live:
  the human's real job `b37b36a9b34e` COMPLETED with 7 rendered clips (1080x1920,
  21-50s, French titles; analysis 298s, all on gemini-3.5-flash-lite, zero NVIDIA);
  the new chain test in a real browser; a forced retired-model swap; a dry-run analysis
  on the final code (7 clips in 95s); the deployed chain test after redeploy (verdict
  ready, 117s: gemini 4.1s, openrouter 7.3s, nvidia 117s, all found the test clip).
  **Awaiting the human's acknowledgement of this substitute.**
- **Audit:** pip-audit (deployed container, 170 packages): none; npm audit: 0.
- **Merge:** origin/main had 28 commits from the Windows session (onboarding, job
  lifecycle, render fixes; its checkpoint is the next section). Merged, not rebased or
  forced. Both sides wrote DEC-075..079 and A-021..024: theirs were published, so this
  task's became **DEC-087..091** and **A-026..029**, everywhere they are cited. Push and
  redeploy status: see the action log.
- **Open questions:** none.
- **Security note:** an exploration agent printed `data/settings.json` once in its own
  local transcript (GOOGLE_API_KEY + OPENROUTER_API_KEY). Told the human; rotation is
  their call.
- **Follow-ups, deliberately not done:** the web worker ignores `dry_run_analysis` (chip spawned); dead GEMINI_MODEL /
  GEMINI_FALLBACK_MODEL config surface (config.py:208-209); Groq and Mistral defaults
  unmeasured (A-026, A-027); Gemini free-tier latency swings 1s-100s (DEC-091);
  `tests/test_clip_length.py` leaves an empty outputs/jobid (pre-existing).

### Why (measured 2026-09-24)
Test provider chain: groq skipped (no key); `gemini/gemini-2.5-flash-lite` 404 "no longer
available to new users, use gemini-3.5-flash-lite"; nvidia ok in 81.6s; summary said
"Jobs can start" -- false: a job would hop to NVIDIA alone. OpenRouter key set but
OpenRouter is not a link in the default chain, so it is never used or tested.
`gemini-3.5-flash-lite` answers via the OpenAI-compat endpoint in 0.53s.

### Regression contract for this task
| ID | Must keep working | Proven by |
|---|---|---|
| RC-C1 | A keyless or unlisted provider is never contacted (DEC-023) | `test_llm_negotiation.py::test_a_provider_not_in_the_chain_is_never_contacted` |
| RC-C2 | A failing link is reported, never removed | `test_preflight.py` report-not-remove tests, `test_every_link_failing_raises_with_all_the_reasons` |
| RC-C3 | Slow-but-healthy never cut off; probe caps below request timeout | `test_preflight.py::test_the_work_probe_is_bounded`, budget tests |
| RC-C4 | `effective_timeout` value and role unchanged | `test_provider_registry.py`, `test_the_budget_check_uses_the_clients_own_timeout` |
| RC-C5 | NIM model id defined once | `test_no_former_copy_grew_a_model_literal_back` |
| RC-C6 | Settings page <-> backend field contract | `test_dashboard_payload_contract.py` |
| RC-C7 | Render-only rerun needs no key, probe or gate | `test_a_render_only_rerun_needs_no_key_and_meets_no_gate` |
| RC-C8 | `providers/` never imports `analysis/`; suite runs with pytest alone | CI-env run |
| RC-C9 | `probe_chain` contract: 4-tuples, byte-identical log lines | `tests/test_preflight.py` passes UNEDITED |
| RC-C10 | `POST /api/jobs` refusal stays pure and key-based (DEC-073) | `test_settings_report_the_same_verdict_the_job_route_would` |
| RC-C11 | Three-pass analysis still yields renderable clips | `test_a_full_run_produces_renderable_clips` |

### Stage ledger
| S | Stage | State |
|---|---|---|
| 0 | checkpoint + baseline | **done** (`8fa873d`, 1361 passed) |
| 1 | shared real pass-A request + bench that sends it | **done** `ea87486` |
| 2 | Gemini default -> gemini-3.5-flash-lite, defined once | **done** `57be632` |
| 3 | OpenRouter + Mistral join the default chain | **done** `02670f0` |
| 4 | deploy + the user's real job | **done** job `b37b36a9b34e`: 7 clips |
| 5 | same-provider model swap on "model unavailable" (RISKIEST) | **done** `7738224` (committed before the job; bind mount) |
| 6 | diagnostic sends real work to every keyed link | **done** `2e7756f` |
| 7 | dashboard | **done** `62531e0` |
| 8 | docs + decisions | **done** `29715db` + DEC-087..091 |
| 9 | redeploy, diagnostic, second real job | **done** (second job = dry-run analysis on the final code; deployed chain test ready) |

---

## Previous task (Windows session) — onboarding, job lifecycle, render-layer fixes (COMPLETE; deployed on Ubuntu with the merge above)
- **Origin:** a fork-vs-upstream analysis (2026-09-23). Upstream has had no
  commits since `3c72b75`, so nothing needs porting. The human chose three
  areas: onboarding (A1-A7), job lifecycle (B1-B5), render layer (D1-D7).
  Everything else is on the roadmap (VISION.md, "Where it stands").
- **Plan:** `C:\Users\ridap\.claude\plans\on-this-sessions-we-snoopy-flurry.md`
  (approved in chat): findings table, roadmap, 21-stage ledger.
- **Host:** Windows 11 (Python 3.11.15, ffmpeg 8.1.1, node 26), with cv2,
  mediapipe and fastapi, so real renders and a live backend ran here. Docker
  and the deploy are on the human's Ubuntu VPS.
- **Checkpoint commit before the work:** `8fa873d`. **Head:** see the ledger;
  everything is on `origin/main`.
- **Tier-1:** baseline at `8fa873d` 1353 passed / 8 failed locally, 1202 / 125
  skipped / 6 failed in the clean pytest-only venv. **Now 1553 passed / 8 failed
  locally, 1371 passed / 157 skipped / 6 failed in the venv** -- the same 8
  (6) pre-existing Windows-only failures, never a new one. `compileall` clean,
  `vite build` green. +200 tests, each behaviour test verified to fail against
  its parent commit.
- **The 8 Windows-only failures** (pre-existing, untouched): 5 POSIX
  chmod/unwritable-dir tests (`test_auth_token`, `test_settings_store` x3,
  `test_clip_srt_export`); `test_chain_readiness::test_the_cli_gates_after_the_key_gate_and_before_the_probe`
  (reads a file with cp1252); `test_clip_serving::test_the_api_is_untouched_by_the_fallback`
  (SPA fallback answers 200 for `/api/nope` on Windows);
  `test_transcript_dispatch::test_bypass_does_not_import_ctranslate2`
  (ctranslate2 is installed here -- may be a real leak CI cannot see, A-024).
- **Tier-2 -- verified here:** framemd5 render parity for every render change
  (hybrid+watermark, split-screen face trigger, hook-v2, edge-glow: identical);
  watermark render 27.3s -> 23.1s; a live backend: cancel mid-render (ffmpeg
  dead in 0.2s, slot free in 0.3s, status cancelled), cancel while queued, 429
  at the queue cap, delete with a shared upload kept then removed, 404s after;
  a browser pass of Cancel/Delete at 375/820px; `--loudnorm` on a real render
  (-21.8 -> -14.0 LUFS, frames identical, default output byte-identical).
- **Tier-2 -- NOT done, needs the human:** no E2E suite exists. On the Ubuntu
  box: `docker compose rm -sfv backend && docker compose up -d --build backend`
  (sudo), then one real dashboard job, one Cancel mid-render and one Delete
  (A-022). Notebooks on Colab/Kaggle (A-023). Camera-switch render (A-025,
  needs pyannote + HF token). **The task is not done until the human
  acknowledges these deferrals.**
- **Dependency audit:** no dependency was added or raised (A3 only copied
  existing specifiers into pyproject); pip-audit is not installed and is
  roadmap item F2.
- **Open questions:** none.

### Stage ledger
| S | Item | Commit |
|---|---|---|
| 0 | sync + baseline | `719ca59` |
| 1 | A6 health version | `d5bc04b` |
| 2 | A2 retire .env.sample | `50934a2` |
| 3 | A3 pyproject mirrors requirements | `7fbf57b` |
| 4 | A4 README/wiki links (+ branding-guard fix `213006e`, DEC-086) | `8c1408a` |
| 5 | A5 retire docs/studio | `06e2d10` |
| 6 | A1 notebooks (+ README Colab recipe) | `12f43e9` |
| 7 | A7 source_manager docstring | `7d374b2` |
| 8 | D7 diarization stderr | `c586320` |
| 9 | D6 hook download (stale file, fallback, timeout/cap/deadline) | `07d1a7b` |
| 10 | D4 memoise encoder probes | `99cbcdb` |
| 11 | D1 watermark loaded once + settings-keyed cache | `ae82632` |
| 12 | D5 render temp cleanup | `786a0ab` |
| — | Group 2 render parity | `b6202c9` |
| 13 | B5 LLM_CHAIN note (no code) | `b05a895` |
| 14 | B1a cancel token + checkpoints | `214f242` |
| 15 | B1b web cancel + child kill | `92421a6` |
| 16 | B2 delete removes files | `5c04051` |
| 17 | B4 queue cap | `7d96402` |
| 18 | B3 dashboard Cancel/Delete | `1e89708` |
| — | Group 3 live verification | `c627ad7` |
| 19 | D2 studio real package (riskiest) | `2549c96`, parity `2632033` |
| 20 | D3 opt-in loudnorm | `577c98e`, render `b7e611a` |
| 21 | docs, DEC-075..086, A-021..025, VISION, close-out | this commit |

### Follow-ups, deliberately not done
- **Security:** `reuse_job_id`, `upload_filename` and `transcript_filename` are
  not validated at the API (cleanup.py refuses anything but a plain name, so
  delete is safe; the pipeline's own use of them is not audited).
- Upload names are not unique: a second `talk.mp4` overwrites the first job's
  source. Store uploads under a unique name.
- Per-item render temp files are relative to the working directory, so two
  concurrent jobs (`MAX_CONCURRENT_JOBS` > 1) would collide in `/app`.
- An age/size retention sweep for `outputs/` (delete is manual today).
- The web worker duplicates `run_pipeline`'s stage sequence.
- Only send Google Drive URLs through gdown (it is tried first for every URL).
- A PEP 562 lazy `clipping/studio/__init__` so stdlib studio modules can be
  imported in CI without the render stack.
- Diarization and the server-side yt-dlp download cannot be cancelled.
- A cancel's feed shows the render layer's own `❌ ERROR ... Broken pipe` line
  from the killed ffmpeg before "Cancelled." -- cosmetic.
- `fail_stale_jobs` fails `needs_upload` jobs at restart.
- `docs/index.html` privacy/terms links point at upstream's Pages domain.
- The Tier-1 helper's Windows CI simulation (clean venv) should replace the
  `PYTHONNOUSERSITE` recipe below for Windows hosts.
- Roadmap (VISION.md): CI coverage of the web layer, render-layer tests,
  download-all + per-clip re-edit, multi-platform ingest, upload guardrails,
  dependency pass, app-level security headers.

### Regression contract for this task
| ID | Must keep working | Proven by |
|---|---|---|
| RC-7 | The render layer produces the same frames | framemd5 identical before and after S10/S11/S19 on hybrid+watermark, split-screen (face trigger), hook-v2, edge-glow (local render) |
| RC-8 | Split-screen renders | the same local render; camera-switch is **UNVERIFIED** (needs pyannote) |
| RC-10 | The web API runs a job end to end | existing `test_job_stream.py`, `test_clip_serving.py` + a manual pass |
| RC-12 | The suite imports with pytest alone (DEC-012) | the clean-venv run above |
| RC-A* / RC-B* | Chain behaviour, budgets, readiness gate (previous task) | `test_nvidia_retry.py`, `test_preflight.py`, `test_chain_readiness.py`, `test_provider_registry.py` |
| RC-B7 | A render-only rerun needs no key | `test_web_reuse_bypass.py` |
| RC-L1 | Clone & Rerun reuses the saved transcript (DEC-022) | `test_transcript_persistence.py` |

---

## Previous task — chain readiness gate + per-provider probe timeout (COMPLETE, deployed 2026-09-24 09:22)
- **Phase:** DOCUMENT done. All 8 stages committed; head `9050839`.
- **Plan:** `~/.claude/plans/still-not-working-groovy-puffin.md` (approved in chat).
- **Checkpoint commit before the work:** `e991ff8` (+ `dafcf0b`, the checkpoint note).
- **Tier-1:** baseline 1299 passed -> now **1361 passed, 0 failed**
  (`python -m pytest -p no:warnings`). Every behaviour test was verified to
  FAIL against its pre-change code; guards that pass both ways are named in the log.
- **Tier-2:** no E2E browser suite exists in this repo. Verified instead:
  live CLI refusal (0.08s), live probe of the real NVIDIA key (39.1s / 57.4s --
  both alive now, the latter dead under the old 45s), live test-chain route, and
  a manual browser pass of New Job + Settings on a throwaway app serving the
  fresh build. **Awaiting the human's acknowledgement, and a container rebuild**
  (dashboard + backend changed): `docker compose rm -sfv backend && docker compose up -d --build backend` (sudo).
- **Dependency audit:** pip-audit is not installed on this box; this task changed
  no dependency file (requirements.txt, pyproject.toml, package*.json untouched).
- **Next action:** the human sets a free GOOGLE_API_KEY (and/or GROQ_API_KEY,
  see A-009), rebuilds, and reruns the job.
- **Open questions:** none.
- **Follow-ups, deliberately not done:** ~~`LLM_CHAIN` is not in
  `settings_store.PERSISTED_KEYS` (a runtime-set chain vanishes on restart)~~
  -- **not a bug** (checked 2026-09-23, DEC-085): nothing sets `LLM_CHAIN` at
  runtime. `SettingsRequest` has no chain field, the settings route only
  *reads* it (`routes/settings.py:65,227`), and a job carries its own
  `llm_chain` (`models.py:202`). It comes from `.env`/compose, which survive a
  restart; persisting it would do nothing. A Settings field for the chain would
  be a feature, not this fix. Adding new free providers needs a benchmark per model; the web path still has
  no `--no-preflight` equivalent; `tests/test_clip_length.py` leaves an empty
  `outputs/jobid` behind (pre-existing).

### Why (measured 2026-09-23)
The failing job had ONLY `NVIDIA_API_KEY`. With that key the NIM ping
succeeded (`ok`) in 48.9 / 57.0 / 49.7s -- all queue wait -- so the 45s probe
cap reported a live provider as dead. Groq/Gemini keys were never set.

### Regression contract for this task
| ID | Must keep working | Proven by |
|---|---|---|
| RC-B1 | A chain with a keyed primary link runs, no new gate in the way | `test_preflight.py::test_a_live_chain_returns_no_complaint`, new readiness tests |
| RC-B2 | A failing link is reported, never removed (DEC-003/023) | `test_preflight.py` report-not-remove tests |
| RC-B3 | A slow-but-healthy call is never cut off (DEC-020) | `::test_a_failed_work_probe_falls_back_to_the_ping` + new nvidia-50s-is-live |
| RC-B4 | `effective_timeout` unchanged in value and role | `test_provider_registry.py`, `test_llm_negotiation.py` |
| RC-B5 | The NIM model id exists in one place | `test_provider_registry.py::test_no_former_copy_grew_a_model_literal_back` |
| RC-B6 | Every Settings field the page sends is declared by the backend | `test_dashboard_payload_contract.py` |
| RC-B7 | A render-only rerun needs no key, no probe, no gate | `test_web_reuse_bypass.py` + new API test |
| RC-B8 | Suite importable with pytest alone (DEC-012) | CI env / importorskip |

### Stage ledger
| S | Stage | State |
|---|---|---|
| 1 | registry: probe_timeout, primary, signup_url | **done** `e8fa95c` |
| 2 | probes resolve their cap per link | **done** `f66328e` |
| 3 | chain_readiness + CLI gate | **done** `d9c5f55` |
| 4 | web plumbing: allow_slow_chain | **done** `fbf41c4` |
| 5 | refusal at POST /api/jobs | **done** `7a28316` |
| 6 | POST /api/settings/test-chain | **done** `e6e73dc` |
| 7 | dashboard | **done** `abd0125` |
| 8 | docs + DECISIONS | **done** `9050839` |

---

## Previous task — analysis round S1-S12 (COMPLETE)
- **Phase:** COMPLETE. All twelve stages are committed. DOCUMENT done;
  awaiting the human's Tier-2 verdict on the deployed app.
- **Checkpoint commit before the work:** `6dbc431`. **Head:** see the ledger.
- **Tier-1:** CI env **1170 passed, 101 skipped, 0 failed** (baseline 1110/101).
  Every behaviour-guarding test was verified to FAIL against its pre-change
  code. Concurrency suites: 20/20 clean reruns.
- **Tier-2:** no E2E browser suite exists. Verified instead by live runs
  against the real NVIDIA endpoint (below) and by a byte-identical `.ass`
  render for S10. **The human is testing S1–S6 on the deployed app and has not
  reported back.**
- **Next action:** deploy and test S7–S12, or act on what the human reports.

### The live measurement that changed a decision
S12's plan said default to 2 workers. The measurement said otherwise — same
transcript, same NVIDIA key, cache off:

| | pass A | for | per window |
|---|---|---|---|
| `workers=1` | 330s | 4 windows | 82s |
| `workers=2` | 346s | 4 windows | 86s |

Two overlapping requests should have finished those four in ~180s. **They did
not overlap at all**: NVIDIA's free tier serialises requests on one key, so a
batch cost the sum of its members and the run was 16s *worse*. The default
shipped as **1**. The flag remains because the chain's first link is Groq
(fast, published 30 rpm) and that is where it should pay — **untested, because
there is still no Groq key on this box.**

### Still the one thing that would help most
**Set a Groq key.** Both live runs above used NVIDIA alone, at ~85s per
request, and both lost windows 5 and 6 to the budget:
`⏱ Window 5/6 skipped: 300s left ... one request to this chain can take 330s`.
Groq is the chain's first link and Settings has had a field for it since
DEC-057. With it the analysis finishes in seconds, none of the budget
machinery binds, and `--analysis-workers 2` finally gets a fair test.

### Stage ledger
| S | Stage | Commit |
|---|---|---|
| 1 | gist/kind survive snapping; reach pass C | `597f06d` |
| 2 | Pass B sees hook lines; topic + variety | `6e6c096` |
| 3 | Pass A prompt rewrite + `--topic` | `08e0c5e` |
| 4 | `hook_beats` + temperature 0.5 for pass C | `2f93f02` |
| 5 | Voice-over prompt into `prompts.py` | `60193e5` |
| 6 | Mid-sentence-start guard | `e8778f5` |
| — | *(pushed to origin as `5a6f687`)* | |
| 7 | Per-window pass-A cache | `7195582` |
| 8 | Preflight does real work | `11276f6` |
| 9 | Delete the legacy monolith | `7d01041` |
| 10 | Karaoke colour into config | `02cddc0` |
| 11 | Analysis trace | `604aba8` |
| 12 | Batched scan windows (default 1) | `06b88b8` |

### Notes a fresh session will want
- `pyproject` sets `addopts = "-q"`; passing `-q` again makes it `-qq` and the
  summary line vanishes. Run pytest with no `-q` of your own.
- The render stack is **not** in `~/.local` — cv2/mediapipe/PIL are at
  `/tmp/claude-1001/scratch/renderdeps`, usable via `PYTHONPATH`.
- `clipping/studio.py` **shadows** the `clipping/studio/` package, so
  `from clipping.studio import subtitles` fails; import `clipping.studio`.
- `npm run build` in `web/dashboard` fails with EACCES — `dist/` is owned by
  root from the container build. `node_modules` is installed locally
  (gitignored); build with `npx vite build --outDir <scratch> --emptyOutDir`.
- `viral_score` prefers `meta["score"]` over `span.score` (`adapter.py:92`), so
  a test asserting which candidate pass B picked needs a metadata fixture with
  no score.

### Deploy
The dashboard changed in S3, S10 and S12, so the container needs a rebuild:
`docker compose rm -sfv backend && docker compose up -d --build backend`
(the `-v` matters; `down -v` would delete the Caddy certificates). Docker
needs sudo on this box.
- **Open questions:** none. Three scope calls were made in chat and are
  recorded in the plan: cover everything ranked, concurrency last, legacy
  single-request path deleted with `openai_compat` kept as a chain alias.

### Stage ledger
| S | Stage | State |
|---|---|---|
| 1 | gist/kind survive snapping; reach pass C | **done** `597f06d` |
| 2 | Pass B sees hook lines; topic + Python variety | **done** `6e6c096` |
| 3 | Pass A prompt rewrite + `--topic` | **done** `08e0c5e` |
| 4 | `hook_beats` + temperature 0.5 for pass C | **done** `2f93f02` |
| 5 | Voice-over prompt into `prompts.py`, English | **done** `60193e5` |
| 6 | Mid-sentence-start guard on b0 | **done** `e8778f5` |
| 7 | Per-window pass-A cache | **done** `7195582` |
| 8 | Preflight does real work when it can | **done** `11276f6` |
| 9 | Delete the legacy monolith; `openai_compat` alias | **done** `7d01041` |
| 10 | Karaoke highlight colour into config | **done** `02cddc0` |
| 11 | Persist the analysis trace | **done** `604aba8` |
| 12 | Concurrent pass-A windows (riskiest) | **done** `06b88b8` |

### Regression contract for this task
Each item names what proves it. Nothing here may break.
- **RC-A1** Three-pass analysis still produces renderable clips —
  `test_analysis_windows.py::test_a_full_run_produces_renderable_clips`.
- **RC-A2** A failed window loses one window, not the run (DEC-027/054) —
  `::test_a_failed_window_does_not_fail_the_run`, `::test_every_window_is_scanned`.
- **RC-A3** The requested clip count is never silently reduced (DEC-021) —
  `::test_a_failed_ranking_falls_back_to_the_scan_scores`, plus the new
  variety-backfill and never-empty-valve tests in S2/S6.
- **RC-A4** A model-invented beat id never becomes a cut (DEC-028) —
  `::test_a_beat_id_outside_the_window_is_discarded`, `::test_a_malformed_candidate_is_skipped`.
- **RC-A5** Provider failure and empty transcript stay distinguishable
  (DEC-055) — the `ScanStats` tests in `test_analysis_windows.py`.
- **RC-A6** The per-window time budget holds (DEC-053/054/059) — the three
  `Clock`/`greedy` tests; S12 must pin them to `analysis_workers=1`.
- **RC-A7** The render layer's clip-dict contract is unchanged (DEC-029, RC-7) —
  `test_slim_schema_adapter.py`, `test_manifest_fields.py`.
- **RC-A8** Transcription and transcript parsing survive the S9 deletion —
  `test_cpu_transcription_warning.py`, `test_json3_parser.py`,
  `test_transcript_dispatch.py`, `test_transcript_persistence.py`.
- **RC-A9** Karaoke word alignment unchanged at the default colour (RC-4/RC-7,
  **UNVERIFIED** — no automated cover; S10 requires a byte-identical `.ass` diff).

### Unstaged at checkpoint time
- `.claude/settings.local.json.tmp.3012965.9704ffe2b8fd` — a stray editor temp
  file, unrelated to this task. Left alone, not staged.

---

## Previous task — COMPLETE
- **Task:** A job run with video only failed after 47 minutes of CPU Whisper:
  the analysis collapsed and blamed the transcript. **All six stages committed
  and verified against the real job.**
- **Checkpoint commit before the work:** `4703679`. **Head:** `d844177`.
- **Tier-1:** CI env **1110 passed, 101 skipped, 0 failed** (baseline was
  1069/101). Local **1236 passed**.
- **Tier-2:** no E2E browser suite exists in this project. Verified instead by
  re-running the real failed job against the live provider (below).
- **Tier-3:** ~30 new tests across `test_provider_registry.py`,
  `test_llm_negotiation.py`, `test_analysis_windows.py`, `test_preflight.py`
  (new) and `test_dashboard_payload_contract.py`. Every one that guards a
  behaviour change was verified to FAIL against the pre-change code.
- **Not pushed.** `git push origin main` when you are ready — remember the
  memory note: `github_osc_better` AND `-F /dev/null`.
- **Plan:** `/home/ubuntu/.claude/plans/i-wanted-to-test-effervescent-hearth.md`
- **Decisions written:** DEC-052 … DEC-059.

### The result
The job that died after 63 minutes claiming the video was "all housekeeping"
now produces **5 clips in 590s** from the same transcript (Whisper not re-run).

### What was wrong — four things, not one
1. **The shipped NIM model answered nothing.** `google/gemma-4-31b-it` returns
   no reply in 120s to an 8-token request. Not 410, not an error, still in
   `/v1/models` — it simply hangs. DEC-021's reading of those 504s ("the request
   asks for too much work") is falsified.
2. **The budget check weighed the backoff, not the request.** DEC-020's
   predictive rule lived only in the legacy `engine.py`; `llm.py` compared a
   4/12s sleep against the deadline while the request ran up to 330s.
3. **One window could spend the whole run's budget**, so windows 2-6 were
   skipped untried — breaking DEC-027's "a failure is local".
4. **The error blamed the content.** `_pass_a` discarded each window's failure
   reason, so "every window answered and found nothing" and "no window ever
   answered" produced the same message.

### The two defects the test suite could not have found
Both surfaced only by running the real job, and both are worth remembering:

- **A model can be fast, schema-valid and useless.** `deepseek-v4.1-flash`
  shipped for one commit and answers `{"candidates": []}` in seven tokens on
  every real transcript, including one that had already yielded seven clips.
  Every guard in the project passes it. Only the real workload catches it
  (DEC-058). The default is now `nvidia/nemotron-3.5-lightning-30b-a3b`, which
  two earlier benchmark rounds had rejected as "reasoning prose" — that was a
  **missing flag**, not the model: `_extra_body` turned thinking off for
  "deepseek" and nothing else. **Check `_NIM_REASONING_FAMILIES` before judging
  any new NIM candidate.**
- **A window granted exactly one request's worth was refused**, because the
  clock moves between granting the deadline and measuring it. The never-tries
  bug, reintroduced by rounding inside the mechanism written to prevent it. The
  fake runners in the tests trusted the allowance instead of re-reading the
  clock, so nothing caught it (DEC-059).

### Where it still hurts, and the one thing left to do
**Set a Groq key.** NVIDIA is the last link and the slowest: ~90s per request,
so the 590s run lost window 5 to a truncated reply it had no budget to retry,
skipped window 6, and gave clip 5 a basic title. All reported honestly, but it
is running at the edge of the 900s budget. Groq is the chain's **first** link
and the fastest free tier, and Settings now has a field for it (it did not
before — that gap is why this job had a single point of failure). With it the
analysis finishes in seconds and none of the budget machinery binds.

### Deploy
The dashboard changed, so the container needs a rebuild:
`docker compose rm -sfv backend && docker compose up -d --build backend`
(the `-v` matters; `down -v` would delete the Caddy certificates). Docker needs
sudo on this box. Then re-run the failed job with **Clone & Rerun**, which
reuses the job id so `config_adapter` adopts the existing `transcript.vtt` and
skips Whisper entirely.

### Still true
- `sudo chown -R "$(id -u):$(id -g)" data` on any older clone.
- CI installs pytest and nothing else. Reproduce it exactly:
  `pip install --target /tmp/cilibs pytest` then
  `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest -q`.
  `PYTHONNOUSERSITE=1` is the load-bearing half.
- `.env` is gitignored; it now declares each key exactly once. Backup of the
  old one: `.env.backup-20260922-080505`.

### Follow-ups, deliberately not done
- `cfg.llm_timeout` is declared, parsed, documented and tested — and reaches
  nothing. Same shape as DEC-051's clip length.
- `analysis_budget_seconds` is read by `analyzer.py` and settable from nowhere.
  It is the binding constraint on a slow provider.
- `MAX_TOKENS_CANDIDATES = 700` truncated one real reply mid-object.
- `.claude/ASSUMPTIONS.md` has **two** entries numbered A-010 (pre-existing).

---

## Previous task (complete, committed)
- **Task:** Two defects the human found while using the deployed app: a
  "blackscreen like bug" a second into every clip, and no way to make clips
  longer. **Both fixed and committed**; the container is rebuilding.
- **Phase:** IMPLEMENT complete → verify on the rebuilt container, then push.
- **Checkpoint commit:** `ee2c1fe` (the media-access task, deployed and working).
- **Tier-1:** pytest **1198 passed, 0 failed**.
- **Next action:** once `docker compose up --build` finishes, confirm the Clip
  Length select is present, render one clip with Hook Glitch ON and check the
  transition frame is static (~126/255) rather than black (~19/255), then
  `git push origin main`.

### The black frame
It was the **Hook Glitch** transition, and it was real. The lavfi fallback — the
only path since the pinned source video went private — built its noise on
`color=c=black`. `noise` adds a *signed* offset, so on pure black every negative
value clamps to 0 and only the positive half survives: **19.2/255 mean luma**, a
black frame with faint speckle. `blackdetect` never fired because it is not
*quite* black, which is why nothing caught it. On mid-grey the identical chain
measures **126.9/255, stddev 25.8** — actual static.

Also flipped the default **off** in all five places it is defined (config
constant, `JobCreateRequest`, `config_adapter`, the CLI, `NewJob.jsx`): a
one-second full-frame effect on every clip is an opt-in. `--hook-glitch` enables
it; `--no-hook` is kept and still wins.

**The font bug's twin, caught before it bit:** `glitch_ready_{w}x{h}.ts` is
cached by filename and returned unconditionally, exactly like
`custom_fonts/Montserrat-Regular.ttf` was (DEC-049). Any machine that had
rendered once would have kept the black `.ts` forever and the fix would have
looked inert. The cache key now carries `GLITCH_RECIPE_VERSION` (now 2), so
changing the filter chain invalidates every cached file automatically.

### Clip length
`platform` picks the window each clip is snapped into — `auto` 20–75s,
`tiktok`/`reels` 15–90s, `shorts` 15–59s, `long` 60–179s. The API has declared
it since the snapper landed and it reaches `cfg`, but `NewJob.jsx` never sent
it, so every job from the UI was `auto` — which is why the reported clips came
out 22–41s. Same class of gap as the provider select that could not reach
`chain`. A Clip Length select now sends it; **default stays `auto`** so nothing
is silently re-cut.

### ⚠️ Run the CI suite, not just the local one, before pushing
CI is `pip install pytest` and nothing else (DEC-012). A test that reaches
`web.api.models` without a guard passes here and fails on every push. **101
tests skip in that environment**, so a green local run proves less than it
looks. Reproduce it exactly:

```
pip install --target /tmp/cilibs pytest
PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest -q
```

`PYTHONNOUSERSITE=1` is the load-bearing half: it hides `~/.local`, where
pydantic, fastapi, httpx and cv2 live on this box. Expect
**1069 passed, 101 skipped, 0 failed**.

Two ways to handle a test that needs a heavy import, and the choice matters:
`pytest.importorskip(...)` when the test genuinely exercises the object, but
**read the source as text** when the test is a guard against drift — an
importorskip on a guard means it never runs in the one place that checks every
push.

### Still true from the previous task
- `sudo chown -R "$(id -u):$(id -g)" data` on any older clone, or the API token
  changes every restart and settings silently fail to save. Fixed at the source
  by tracking `data/.gitkeep`.
- Deploy with `docker compose rm -sfv backend && docker compose up -d --build
  backend`. The `-v` matters; `down -v` would delete the Caddy certificates.
- No E2E browser suite exists in this project.

---

## In progress
- **Task:** The clips rendered but the app could not show them; then land
  everything on `main`. **COMPLETE — all stages committed and verified in a
  real browser.**
  Plan: `/home/ubuntu/.claude/plans/i-want-the-code-breezy-starlight.md`
- **Phase:** DONE, pending the human's acknowledgement on Tier 2 (below).
- **Open questions:** none.
- **Branch:** `feature/rzdhop-clips-rearchitecture`.
- **Checkpoint commit:** `233b860` (pre-task). Tier-1 there: **1008 passed**.
- **Tier-1 now:** pytest **1187 passed, 0 failed**. +179: 46 came from
  `origin/main` in the merge, 133 are new here.
- **Tier-2:** this project has **no E2E browser suite** — no playwright, no
  cypress, no `e2e/` — so there is nothing to run. Done instead: the real app
  was started with the dashboard built in a scratch dir, and job
  `2773bd83c7b6` was opened and driven in a browser (results below).
  **The human still owes an explicit acknowledgement that no E2E suite exists
  and that the browser check stands in its place.**

### What was wrong, in one paragraph
`c53949b` put `Depends(require_token)` on the whole files router and the token
is header-only by design, but `<video src>` and `<a href download>` are requests
the BROWSER makes and cannot carry a header. All three reported symptoms were
one `401 {"detail": ...}`: the player got it instead of video, the `download`
attribute saved that JSON body and the browser renamed it `.json`, and a pasted
URL got it too. The clips were never the problem. Fixed by signed, expiring,
per-file URLs (DEC-048).

### Stages, all committed
| # | Commit | What |
|---|---|---|
| 0 | `0b4ff43` | checkpoint + regression contract |
| 1 | `5ac08b9` | merge `origin/main`, both provider paths kept (DEC-046/047) |
| 2 | `114cef3` | HMAC sign/verify/mint helpers in `web/api/auth.py` |
| 3 | `87477f4` | `require_token` accepts a media signature — **riskiest**, mutation-tested |
| 4 | `17750e7` | inline vs `?download=1`; Range asserted live |
| 5 | `516305b` | `thumbnail_url` + `srt_url`, derived on read |
| 6 | `b19672f` | URLs signed at serialization — **the fix** |
| 7 | `2fb25b1` | dashboard: poster, `.srt` button, expiry recovery |
| 8 | `a383fa4` | the subtitle font was DejaVuSans on every clip (DEC-049) |
| 9 | `9bfa0ff` | stop fetching the private glitch video |
| 10 | `65d0e52` | docs, DEC-048/049, CHECKPOINT reordered |
| 10b | `5207985` | SPA deep links 404'd — found in the browser, not by a test |

### Verified in a real browser, against the human's real job `2773bd83c7b6`
All 7 cards render with their thumbnail as the `poster`. All 7 videos fetched
**206 Partial Content carrying no credential**; `readyState` 4, 1080x1920,
27.3 s. A seek to 0:20 succeeded and stayed at `readyState` 4, so Range seeking
works. The Download link is
`/api/outputs/…/highlight_rank_1_ready.mp4?exp=…&sig=…&download=1` with
`download="highlight_rank_1_ready.mp4"`, answering **200 `video/mp4`,
`Content-Disposition: attachment`** — an `.mp4`, not the `.json` that started
this. The `.srt` button is present and signed. Every client-side route serves
the app, missing assets still 404, `/api/jobs` still 401.

### And at the HTTP level
All 7 clips: `.mp4` + `.jpg` + `.srt` fetched over HTTP with **no headers at
all**, 206 on a Range request, `?download=1` giving `attachment` with the right
filename, two consecutive reads returning byte-identical URLs, the token
appearing nowhere in the response, the same URL stripped of its signature still
`401 application/json` (the exact response that was being saved as `.json`),
that signature on `/api/jobs` and `/api/shutdown` still 401, and
`outputs/jobs.json` never written. The font fix was proved with a real libass
burn: `fontselect: (Montserrat, 400, 0) -> Montserrat-Regular`, where the
human's run said `-> DejaVuSans.ttf`.

### Deployed and verified on the real container (2026-09-21 20:36 UTC)
The human rebuilt and started `rzc-backend`. Confirmed against it: every SPA
route serves the app and `/assets/nope.js` still 404s; the served bundle is
`index-tVovS33m.js`, the build carrying the JobDetail changes; all three clip
URLs come back signed; the clip plays with **no headers** (200 `video/mp4`), a
range request answers **206**, and `?download=1` returns a real ISO Media MP4 as
`attachment; filename="highlight_rank_1_ready.mp4"`. The unsigned URL is still
401, and a clip signature on `/api/jobs` is still 401.

**One silent deployment bug was found in that log and fixed** (`d55271c`):
`./data` is a bind mount, gitignored, so it does not exist in a fresh clone —
and Docker creates a missing bind-mount source as **root**, after which the
container (host uid, 1001 here) cannot write it. The API token could not be
persisted, so it **changed on every restart**; and settings persistence answered
200 while failing. Now `data/.gitkeep` is tracked so a clone owns the directory.
On an older clone the one-time fix is `sudo chown -R "$(id -u):$(id -g)" data`.

### ⚠️ Blocker the human must clear once, before building the dashboard LOCALLY
`web/dashboard/node_modules` and `web/dashboard/dist` are both **empty
root-owned directories** — mount points docker created — so `npm ci` dies with
EACCES. Unrelated to this task, but it blocks the documented build step:

```
sudo rm -rf web/dashboard/node_modules web/dashboard/dist
```

The Docker build is unaffected (`.dockerignore` excludes both). The JSX in this
task was verified by copying `web/dashboard/` to a scratch dir and building
there: vite 6.4.3, 48 modules, clean.

### Deploying this
```
docker compose rm -sfv backend && docker compose up -d --build backend
```
`rm -sfv`, **not** `docker compose down -v`: the latter also deletes the
`caddy_data`/`caddy_config` volumes and any issued TLS certificates. The `-v`
matters — the anonymous volume at `/app/web/dashboard/dist` survives
`up --build`, so a rebuilt image otherwise keeps serving the old bundle and the
fix looks like it did nothing.

### Regression contract for this task — all green
| # | Must keep working | Proven by |
|---|---|---|
| MC-1 | Every non-media route still refuses an unauthenticated request | `tests/test_auth_token.py`, green unchanged |
| MC-2 | A media signature is not a general credential | a valid clip signature on `/api/jobs`, `/api/settings`, `/api/upload`, `/api/shutdown` → 401 |
| MC-3 | The outputs listing stays private | `GET /api/outputs/{job}` has no `filename` param, so no signature can reach it |
| MC-4 | The traversal guard still refuses | `tests/test_auth_token.py:309-360` plus a signed-escape test |
| MC-5 | The manifest/worker contract stays exact | `tests/test_manifest_fields.py` — `srt_path` added to the literal, not worked around |
| MC-6 | The dashboard mount stays last | `test_the_dashboard_mount_is_the_last_route_registered` |
| MC-7 | The token never travels in a URL | `test_the_token_never_travels_in_a_query_string`, plus `test_the_token_is_not_in_the_url` |
| MC-8 | `outputs/jobs.json` is never rewritten | derived on read; mtime unchanged after a live read |

### Follow-ups deliberately not done
- **`docs/studio/`** — the stale GitHub Pages client. Its `api.js` has no token
  support and uses `EventSource`, which cannot send headers, so it is already
  wholly non-functional against a token-gated API. Retire it, or give it a token
  field and a `fetch`-based stream. Not a media-playback fix.
- **`web/api/settings_store.PERSISTED_KEYS` has no UI for `GROQ_API_KEY`,
  `OPENROUTER_API_KEY`, `MISTRAL_API_KEY` or `LLM_CUSTOM_*`.** The Settings page
  covers Google, NVIDIA, Pexels, HF and the `openai_compat` trio only, so four of
  the chain's providers can be configured by `.env` alone.
- **Stage 11 of the previous task** (retiring the legacy analysis path) is still
  deliberately undone, and is now *more* entangled: `openai_compat` joined that
  path in the merge.

---

## History below this line

Everything that follows is closed work, including the banner and sections
that arrived from `origin/main` in the 2026-09-21 merge. Read it for
context, not for what to do next.

> **Two streams of work were merged on 2026-09-21.** Both are complete. One made
> the analysis provider pluggable and reworked the job-creation UI; the other
> fixed the NVIDIA retry behaviour, capped the time budget and persisted the
> Whisper transcript. They touched the same file, `clipping/engine.py`, and the
> merge kept both: the retry ladder, the SDK-retry fix and the time budget now
> apply to **every** provider, because they live in the shared core that the
> NVIDIA and custom-endpoint wrappers both call.
>
> **Tier-1 on the merged tree: 419 passed, 0 failed.** `compileall` clean.
> Decisions DEC-023 to DEC-026 were renumbered from DEC-016 to DEC-019 during
> this merge; see the note in DECISIONS.md.


## Last task — provider settings, job guidance, render options (COMPLETE, VERIFIED)
- **Task:** Add a generic OpenAI-compatible analysis provider, persist the Settings
  page values, add provider guidance + warnings to New Job, expose the render
  quality options. Plan approved 2026-09-21.
- **Phase:** closed out. All 8 planned stages committed (S6 and S7 merged — see below).
- **Open questions:** none.
- **Baseline before the work:** `5bdd31c`, 254 tests passing.
- **Tier-1 now:** 304 passed locally; **282 passed / 16 skipped in a clean
  pytest-only venv**, which is what CI runs (DEC-012); compileall green;
  `main.py --help` green.

| Commit | What |
|---|---|
| `1d65fd7` | S1 stale settings defaults (`cuda`→`auto`, `gemini`→`nvidia`) |
| `6a755cf` | S2 settings persist to `.local/settings.json` |
| `96a92c2` | S3 generic `openai_compat` provider (engine + CLI) |
| `140b752` | S4 web API surface for it |
| `a0f7769` | S5 Settings page custom-endpoint card |
| `a8505a7` | S6+S7 New Job banner, warnings, quality preset, Advanced, dead toggle removed |
| `43a6f1a` | Fix: the new settings test aborted CI collection |
| `a154579` | S8 env samples, compose passthrough, README, DEC-023/024, A-009 |

**S6 and S7 were merged** into one commit: the warnings S6 adds depend on controls
S7 introduces (split-screen and its trigger), so two commits on the same file
could not have been reverted independently — the only reason to split them.

### Verified against a running stack (not just unit tests)

## Previous task (closed — rearchitecture stages 1-10)
- **Task:** Job `756c7ee8a2c3` burned 2h22m and produced nothing. **COMPLETE** — 5 stages, last `c68eb3d`, plus `e5f473d`/`f8ad8b4` CI fixes.
- **Phase:** closed out. Merged to `main` and pushed; `origin/main` is `f8ad8b4`.
- Its findings (the NVIDIA 504 arithmetic, the live probes, the transcript
  persistence design) are preserved below and in DEC-019..022.

### The 45 minutes were 9 requests, not 3 — measured, not inferred
`_make_nvidia_client` (`clipping/engine.py:317`) builds `OpenAI(...)` with
**neither `timeout` nor `max_retries`**. The installed SDK (2.24.0) defaults to
`DEFAULT_MAX_RETRIES = 2` and a 600s read timeout, and
`BaseClient._should_retry` returns `True` for any status >= 500 — so **504 is
retried twice inside the SDK, invisibly**. Each `attempt N/3` line in the log is
1 + 2 = **3 HTTP requests**; the ladder is 9 requests, reported as 3.

Proof from the job's own timestamps, before any probe was run:
- Gaps *between* attempts are **5s** and **15s** — exactly
  `NVIDIA_BACKOFF_SECONDS = (5, 15)`. So all ~15 minutes elapsed *inside* one
  `create()` call.
- A single 900s request is impossible: it would have exceeded the SDK's 600s
  read timeout and raised `APITimeoutError`, not `InternalServerError: 504`.

### Live probe results (2026-09-18, real endpoint, `max_retries=0`)
Each row is ONE http request. Model `deepseek-ai/deepseek-v4-flash-0731`.

| Variant | Elapsed | Outcome |
|---|---|---|
| 60s transcript, 1024 tok, 1 clip | **144.1s** | OK — but `completion_tokens=2`, an empty clip array |
| 1211s transcript, 16384 tok, 7 clips (what the job sent) | **302.1s** | **`InternalServerError: 504`** |

| 1211s, 4096 tok, 7 clips | 124.0s | OK — but `completion_tokens=2`, empty array again |
| 1211s, 16384 tok, **3 clips** | **291.8s** | **OK — 3911 tokens, 3 real clips** |
| 1211s, 16384 tok, 7 clips, **no schema** | 302.1s | **504** |
| 1211s, 4096 tok, **3 clips** | **278.8s** | **OK — 3371 tokens, 3 real clips** |

So the gateway cuts a request off at **~300s**, and 3 × 302s ≈ the 15:09 / 15:08
/ 15:07 seen per attempt. The failure is generation time, not input size — the
1211s transcript is only ~27 KB.

### What the probe ruled out, and what it implicates
Two plausible causes are **eliminated**: `max_tokens` is not the driver (4096 vs
16384 changes nothing about success), and neither is the strict `response_format`
schema — dropping it entirely still 504s at 302.1s, so this is not
constrained-decoding overhead.

**Clip count is the driver.** The arithmetic closes:
- Generation rate measured at **~12–13 tokens/s** (3911 tok / 291.8s; 3371 tok /
  278.8s).
- Cost per clip **~1200 tokens** (23 required fields each).
- A 300s gateway window therefore allows **~3800 tokens ≈ 3 clips**.
- 7 clips needs ~8400 tokens ≈ **~660s**, i.e. more than twice the limit. It can
  never complete, which is why all three attempts failed identically.

**The shipped default is `clips = 7`** (`web/api/models.py:108`,
`Field(7, ge=1, le=30)`). So the default request is ~2.3x what this provider can
deliver, and the documented maximum of 30 is ~10x unreachable — it would need
roughly 3000s against a 300s limit. Even 3 clips lands at 291.8s against ~302s,
about 3% of headroom.

Unresolved and possibly separate: every 7-clip request that did *not* 504
returned an **empty array** with `completion_tokens=2` — the same
`ValueError: NVIDIA returned an empty clip array` the artifacts record from an
earlier job. 3-clip requests never did this.

### Verification of this round
| # | Claim | Evidence |
|---|---|---|
| V-1 | Each visible attempt was 3 http requests | SDK source (`DEFAULT_MAX_RETRIES=2`, `_should_retry` true for >=500) + the job's own 5s/15s inter-attempt gaps + the impossibility of a 900s request under a 600s read timeout |
| V-2 | The gateway cuts off at ~300s | Live probe: the job's exact payload returned 504 at **302.1s** in a single request |
| V-3 | `max_tokens` is not the cause | 4096 and 16384 behave identically (124.0s empty array vs 302.1s 504) |
| V-4 | The strict schema is not the cause | Dropping `response_format` entirely still 504s at **302.1s** |
| V-5 | Clip count is the cause | 3 clips → **291.8s, 3911 tokens, 3 real clips**; 7 clips → 504. ~12–13 tok/s × ~1200 tok/clip ⇒ ~3 clips per 300s window |
| V-6 | The live 504 will trigger the proposal | The probe read `status_code` off the real exception and printed `InternalServerError(504)`, exactly what `_nvidia_request_too_large` keys on |
| V-7 | Unit behaviour | 352 tests; every new test checked against the pre-fix code — `KeyError: 'max_retries'`, the unbudgeted loop running all 3 attempts, the 3 proposal tests, and the duplicate CUDA warning reporting "appeared 2 times" |

### Live end-to-end against the real API (2026-09-18)
Ran the real `analyze_with_nvidia` against the live endpoint with `clips=7`,
on the *auto-degrade* build that preceded `46d341c`:

```
🔁 attempt 1/3 -> ⚠️ InternalServerError: 504
✂️ 7 clips is more than this model can generate — asking for 3
🔁 attempt 2/3 (3 clips) -> RESULT: 3 clips in 581.0s, ranks [1, 2, 3]
```

That is the evidence behind the number the proposal hands the user: **3 clips
really does succeed on this endpoint immediately after a 7-clip 504**, and the
581.0s total matches the probe (302s + 279s).

### The transcript is discarded — found while checking the proposal's cost
`resolve_transcript(cfg)` (`web/api/worker.py:221`) returns the transcript in
memory and `analyze_with_ai` (`:244`) consumes it. **Nothing ever writes it to
disk** — verified on a *successful* job too: `outputs/275d7caf2436/` holds
`gemini_response.json`, the source mp4, clips, thumbnails and
`render_manifest.json`, and no transcript. The failed job's directory is empty.

Consequence, and why it matters here: the proposal added in `46d341c` tells the
user to re-run with fewer clips, and on a CPU job doing so **re-runs the
94-minute transcription**. Measured cost of the two designs:

| | Time | Outcome |
|---|---|---|
| Auto-degrade (rejected in review) | 581s | 3 clips delivered |
| Propose (chosen) + re-run on CPU | ~604s + **~94 min** | 3 clips, after a full re-transcribe |

It is also a standalone bug: a 94-minute artifact is destroyed by any failure at
or after analysis, in a project whose premise is local-first and which already
accepts `--transcript file.vtt`. Stage 5 fixes it.

### Regression contract for this task
| # | Must keep working | Proven by |
|---|---|---|
| N-1 | The retry ladder still retries genuine transient failures | `tests/test_nvidia_retry.py` (37 tests) stays green |
| N-2 | Fatal errors (bad key, 4xx) still fail fast, not after 3 attempts | same suite |
| N-3 | `response_format` 400-fallback to prompt-only still works | same suite |
| N-4 | The activity feed still shows each attempt and its reason | it reads stdout; print sites unchanged |
| N-5 | Gemini path untouched | `REQUEST_TIMEOUT_MS` at `:1120` is Gemini's and is not in this diff |

## Previous task (closed)
- **Task:** Close the four remaining follow-ups, then merge to `main` and push.
  **COMPLETE** — stages `eb1feca`, `d4d5c78`, `a269a8f`, `f296eb3`.
  Scope approved by the human: (1) the remaining layout items, (2) the `gdown`
  packaging bug, (3) `run_upload.py`'s broken `youtube_uploader.safety` import,
  (4) the dead yt-dlp imports in `clipping/studio/`.
- **Phase:** closed out. Pushed: `origin/main` moved `105cddc` → `d03fd41`,
  and the main checkout was fast-forwarded to match.

## Previous task (closed)
- **Task:** The dashboard scrolled sideways at phone width. **COMPLETE.**
- **Phase:** closed out.
- **Checkpoint commit:** `105cddc` was the baseline. Stages: `ac622fb` the
  content column, `cbc389b` unbreakable tokens. Artifacts: `36aa45f` (before),
  and this commit.
- **Tier-1:** `python -m pytest -q` = **327 passed, 0 failed** before and after
  (needs `PYTHONPYCACHEPREFIX` locally — see the root `__pycache__` note below).
  `npm run build` green; the stylesheet went 13.19 kB -> 13.38 kB.
- **Tier-2:** no E2E suite exists in this project, so Tier 2 is browser
  measurement — done, see below.
- **Tier-3:** no test added. There is no frontend test infrastructure at all
  (no vitest, no playwright, no `test` script in `web/dashboard/package.json`);
  a real guard needs a headless browser asserting `scrollWidth == clientWidth`
  per route, which is a new dependency and harness and so its own task. Listed
  under follow-ups.
- **Open questions:** none.

### Root cause, measured (do not re-derive)
`.main-content` is a flex item of `.app-layout` (`display:flex`) with `flex: 1`
and **no authored `min-width`**, so it kept the flex default `min-width: auto`,
which resolves to its **min-content width** and overrides `flex-shrink: 1`
entirely. Measured in a 375px viewport: `main` = 427.234px, its `min-content` =
427px, and `min-width: 0` brings it to exactly 375px.

All three suspects in the original brief were absent — `grep` finds **zero**
`min-width` and **zero** `calc()` in the whole 897-line stylesheet, and the
`@media (max-width: 768px)` block *does* correctly override `margin-left` and
`padding`. The minimum was implicit, which is why it was not greppable.

**It was never a phone-only bug.** At 820px — sidebar on screen, media query not
applied — a job page with a real `source_url` gave `scrollWidth` 959 against
`clientWidth` 805. See DEC-016 for why the rules are base declarations.

### Contract for the layout fix (do not undo)
- `.main-content { min-width: 0 }` is load-bearing, not defensive. Remove it and
  the column goes back to refusing to shrink below its widest child.
- The wrap and `overflow-wrap` rules are **base declarations on purpose**
  (DEC-016). Moving them into `@media (max-width: 768px)` re-breaks 769–1100px
  while looking correct on every phone.
- `overflow-wrap: anywhere`, **not** `break-word` (DEC-017). Only `anywhere`
  reduces min-content width, and min-content is what travels back up the tree.
  A `break-word` swap looks identical in a screenshot and leaves `scrollWidth`
  wrong.
- `.log-viewer` and every `.activity-*` rule are **outside this diff**. The feed
  is contained because `.log-viewer` is its own scroll container; that is what
  keeps `.activity-message`'s `word-break: break-word` from mattering.

### Verification of the layout fix (2026-09-18)
Measured against a Vite dev server running **this worktree** on `:5174` against
the live backend on `:8000`. The `:5173` server serves the main checkout and
would not have shown the edit.

| # | Contract item | Result |
|---|---|---|
| R-1 | No horizontal scroll at 375px | **PASS** — `scrollWidth == clientWidth == 375` on `/`, `/new`, `/settings` and three job pages; zero elements extend past the viewport |
| R-2 | Same at 414px and 820px | **PASS** — 414/414 and 820/820 (805 where a scrollbar is present) |
| R-3 | Desktop unchanged | **PASS** — at 1280px the sidebar is still 260px, `main` 1020px, and all six step dots sit on one row |
| R-4 | Live activity panel intact | **PASS** — headline, `🤖 NVIDIA` + `deepseek-ai/deepseek-v4-flash-0731` chip, both clocks (`34m 41s on this step · 35m 55s total`), console header, 90 feed lines across three severity classes. `.chip-sub` measures 205px against its 204.697px `max-width` |
| R-5 | Feed follows the tail only when not scrolled up | **PASS** — console scrolled to top stayed at `scrollTop 0` across a poll cycle. `.log-viewer` is not in the diff |
| R-6 | Job cards keep their fields | **PASS** — cards 343px wide, still showing `36% · Analyzing with AI...` |
| R-7 | Python suite unaffected | **PASS** — 327 passed, 0 failed |

Two **stressed** cases, both real overflows found by injecting realistic content
rather than by reading code, both fixed and re-measured at 375px:

| Case | Before | After |
|---|---|---|
| Job page with a real YouTube `source_url` | `scrollWidth` 432 | 375 |
| Job card with a long uploaded filename | `scrollWidth` 568, card 552px | 375 |

### Verification of the follow-up round (2026-09-18)
| Stage | Result |
|---|---|
| `eb1feca` layout remainder | `scrollWidth == clientWidth` on all five routes at **320**, 375 and 1280px. `.config-grid` renders the same three 303px columns at 1280px as the inline style did |
| `d4d5c78` gdown | Declaration only; `pytest` 327 passed |
| `a269a8f` dead imports | AST pass: `YoutubeDL` occurred exactly once in each of the ten (the import). Diff is 10 files / 10 deletions / 0 insertions. `compileall` clean, 327 tests green. **RC-7 not re-verified by a live render** — no cv2, no mediapipe, no docker access on this host |
| `f296eb3` run_upload | With only the absent google-auth chain stubbed: `import run_upload` OK, `--help` builds, every kwarg it passes is accepted by `upload_manifest_to_youtube` |

### Status of the previous task
Live progress / debug feed — **COMPLETE**, stages `58c07a5`, `e83c364`,
`19fd3d7`, `06fb8bc`, `6c325df`, `96d22ec`, docs `105cddc`. Its contract is
below and still binding.

### The feature, in one line
Everything the pipeline prints now reaches the job that printed it, and the job
page says which provider and model is being asked, which retry attempt it is on,
which clip of how many is rendering, and how long it has been on this step.

### Contract for the activity feed (do not undo)
- `clipping/` is **untouched**. Progress is read from the pipeline's stdout, not
  from a callback (DEC-014). Do not thread `on_progress` through `runner.py` /
  `engine.py` / `studio/core.py` without revisiting that decision.
- The tee writes the real stream **first** and records inside a `try`. Recording
  must never be able to break a `print`.
- `store._lock` is an **RLock** on purpose: the tee turns any `print` into a
  store write, so a plain `Lock` deadlocks the worker if anything prints while
  the lock is held (DEC-015).
- `web/api/signals.py` is the **only** place that matches on the pipeline's
  wording. Keep it that way; everything else is generic.
- Event appends use `_persist(force=False)`. Anything a client waits on
  (status, progress, completion) must keep forcing.
- ffmpeg output is **not** in the feed and cannot be — it is a subprocess on the
  real file descriptors. Documented in the README; do not claim otherwise.
- **Progress-bar redraws must not enter the feed.** They go to `progress.detail`
  only. Without this, two 12-second clips fill the 500-entry buffer (90% bars)
  and evict everything worth reading. A bar's identity is the label *before* the
  percentage — a digit-blind normalization folds Rank 2's bar into Rank 1's —
  and the percent-sign requirement is what keeps the retry counters out of the
  coalescing entirely.

### Docker / device verification (2026-09-18, live daemon)
| Item | Status |
|---|---|
| `d845413` container uid | **VERIFIED.** `osc-backend` runs `uid=1001 gid=1001`; `/app/uploads` and `/app/outputs` owned `1001:1001`; in-container write probe OK; `HOME=/tmp`; `/tmp/Ultralytics` 0777. The host `.env` correctly sets `DOCKER_UID/GID=1001` (this host's uid is 1001, not 1000) |
| CUDA branch of the device resolver | **As verified as a CPU-only host allows.** `resolve_whisper_runtime` takes an injectable `cuda_available`; `test_auto_with_cuda_picks_cuda_and_float16`, `test_explicit_cuda_is_respected_when_available` and `test_detection_uses_ctranslate2_when_it_reports_a_device` drive the CUDA-true path. Only `whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build is left, and only a GPU host closes it |
| `9a9adc5` Vite timeout | **VERIFIED at runtime.** After restarting the frontend so the new config was actually loaded: a 20MB upload rate-limited to 50KB/s through the dev proxy returned `HTTP 200 in 390.56s`. That is 90s past Node's default 300s `requestTimeout`, which is the timeout that used to kill the request with nothing in the backend log. The earlier 300MB test never reached the window because loopback was too fast |

### Tier-2 — verified in a browser against the running containers
A real job (`video.mp4` + `subtitles.vtt`, 2 clips) run end to end on the live
stack. The panel rendered:
- the provider/model chip `NVIDIA · deepseek-ai/deepseek-v4-flash-0731`
- `attempt 2 of 3`, amber once past the first attempt
- time-on-step and total, both ticking
- the quiet notice after 45s of silence
- the feed, severity-coloured, carrying things that were previously invisible:
  `⚠️ 718 of 2095 words (34%) in subtitles.vtt had backwards timestamps and were
  dropped` and `⚠️ NVIDIA attempt 1 failed | ValueError: NVIDIA returned an
  empty clip array.`
- the job list showing `36% · Asking nvidia... ⏱ 6m 26s`

### Known state of the running stack
- Job `d4a133c4e9cd` is stuck in `analyzing` forever: its worker thread died when
  the containers were recreated at 08:26. It is a dead record, not a running job;
  delete it when convenient. It predates this work.
- The repo-root `__pycache__/` is owned by root (from a docker run predating the
  uid fix), so a local `compileall` needs `PYTHONPYCACHEPREFIX`. CI is unaffected.
- ~~The dashboard scrolls sideways at 375px~~ — **FIXED** 2026-09-18
  (`ac622fb`, `cbc389b`). See the layout-fix contract above.

### Verified against real services (previous task, 2026-09-17/18)
| What | Evidence |
|---|---|
| S1 fix is live | `GET /api/settings` returns `default_whisper_device: "auto"`, `default_ai_provider: "nvidia"` |
| Settings survive a restart | PUT a custom endpoint → stop the backend → start it → `🔐 Restored 3 saved setting(s)` and all three values come back |
| Empty value clears an override | PUT `""` for the base URL → the key is **absent** from the file, not stored empty |
| Settings page | Rendered in a browser: NVIDIA first with its free-key link, custom-endpoint card with presets, "✅ Configured" in System Info |
| New Job page | Banner, three-provider select, Fast/Balanced/Best chips, Advanced section all render |
| Missing-key warning | Selecting Gemini (key unset) shows the analysis-step warning |
| Diarization warning | Split screen + diarization with no HF token shows the switch-to-face warning |
| Split trigger default | `face` in the UI, so the unverified pyannote path is not the default one a user hits |
| Dead toggle | "YouTube Subs" absent from the rendered page |
| **Payload round-trip** | Captured the real POST the browser sends (fetch stubbed, no live API call), fed it through `JobCreateRequest` + the adapter: **0 fields dropped**, and every Fast-preset value reached cfg (`render_output_height` 720, cq 30, crf 26, bilinear) |
| Custom endpoint gate | With settings loaded from disk the worker gate passes; clearing the model returns `('openai_compat_model', 'OPENAI_COMPAT_MODEL')` |

### Verified against the LIVE free NVIDIA endpoint (the user authorised it)

### Still unverified
- **RC-8 diarization / split-screen.** Needs `pyannote.audio` + `torch` + an
  accepted HuggingFace model agreement. Unchanged.
- **`whisper_cuda_available()` against a real CUDA-enabled CTranslate2 build.**
  Needs a GPU host; the branch logic around it is covered by injection.
- Everything else previously carried here — the container uid fix, the Vite
  timeout fix, the CUDA branch — is resolved above.

Running it for real found two bugs that no test could have caught.

| What | Result |
|---|---|
| **Default model was dead** | `deepseek-v4-flash-0731` hit EOL at 2026-09-21T08:00:00Z — **the same day**. Every default job failed with 410. Replaced with `nvidia/nemotron-3-super-120b-a12b` (DEC-025) |
| Retry classification, live | The 410 was correctly called fatal and **not** retried: one call, not three |
| NVIDIA path after the S3 refactor | Full CLI run: 55 segments, AI picked 15.2–32.8s and 61.0–86.7s with titles and BGM moods, **2 real clips rendered at 720x1280 h264**, first attempt |
| **Custom endpoint, first live run** | Failed all 3 attempts on `JSONDecodeError` — a reasoning model leaked a bare `[` before its own valid array. Fixed by salvaging the first balanced JSON value (DEC-026) |
| Custom endpoint after the fix | Same run succeeds on **attempt 1** and renders at 720x1280 |
| Fast preset, end to end | `--render-height 720` produced genuine 720x1280 output |

### Not verified
- ~~No live call through `openai_compat`~~ — **done**, and it found a real bug
  (DEC-026). Both providers now verified end to end against a live endpoint.
  Still untested: a *non-NVIDIA* host (OpenRouter, Groq, Ollama). The protocol is
  the same, but each provider's quirks are its own.
- RC-8 (diarization / split-screen render) remains unexercised, as before. S7
  makes split-screen reachable from the dashboard for the first time, which is
  exactly why its trigger defaults to `face`.
- Docker: the compose passthroughs were added but not run against a daemon.

### Follow-ups deliberately not done
- `docs/studio/*.html`, the static GitHub Pages UI: still has the dead
  `use_dlp_subs` toggle, a two-provider select, no custom-endpoint field, and
  still posts `url`/`source` fields the backend purged. It is now further behind
  the React dashboard than it was.
- `notebooks/Quick_Start.ipynb` and `kaggle-studio-server.ipynb` never collect
  `NVIDIA_API_KEY`, so a default-provider run from either fails on a missing key.
- `wiki/2-Getting-Started.md` still calls `NVIDIA_API_KEY` optional.
- `use_camera_switch` is still unexposed in the dashboard; it always needs
  diarization, with no `face` escape.
- **`--clips N` is only a prompt hint.** Nothing truncates the model's list, so a
  run with `--clips 1` rendered 3 clips when the model returned 3. Pre-existing;
  clamping it would change output for existing users, so it was logged not fixed.

## Status of the previous task
- **Task:** Local-first refactor — **COMPLETE and VERIFIED END-TO-END**
- **Phase:** closed out
- **Open questions:** none blocking. One open risk: see A-007.

## Checkpoint commit
Branch `refactor/local-first-engine`. Baseline before the work: `3c72b75`
(clean tree, `main`).

| Commit | What |
|---|---|
| `8947641` | S1–S3 lazy Whisper import, test scaffolding, `clipping/transcript.py` |
| `0825828` | S4 `--video` / `--transcript` CLI surface |
| `1da2c65` | S5 runner wiring + Whisper bypass |
| `6d8bfd8` | S6 NVIDIA hardening (retry, fail-fast, `openai` declared) |
| `9a5db01` | S7 NVIDIA becomes the default provider |
| `253e90d` | S8 story mode local sources |
| `abb0286` | S9 web API local-first jobs |
| `fa7dbad` | S10 the purge (deletions only) |
| `b60fc4b` | S11 docs, notebooks, CI, 2.0.0 |
| `a2cacc9` | Fixes found by end-to-end verification (voiceover import, Windows ffmpeg escaping, worker key gate) |
| `bf797c0` | The shipped NIM model default was retired — replaced |
| `47ca1af` | Web job fields Pydantic was dropping |

## Tier-1
```
python -m pytest -q                                            # 166 tests
python -m compileall -q clipping web main.py youtube_uploader youtube_tracker
PYTHONIOENCODING=utf-8 python main.py --help
```
All green. CI runs the first two on every push.

`PYTHONIOENCODING=utf-8` is needed on Windows only: the help text contains emoji
and the console defaults to cp1252. Pre-existing, unrelated.

## Tier-2 — verified on real media

Everything below was run against a genuine 70s 1280x720 h264 video with a
realistic YouTube-style auto-caption VTT (inline `<00:00:01.234>` word tags,
rolling repetition, 10ms bridge cues).

| # | Behaviour | Result |
|---|---|---|
| RC-1 | `data_segmen` contract | ✅ `assert_valid_data_segmen` across every parser, fixture and producer |
| RC-2 | JSON3 byte-identical after helper extraction | ✅ golden test |
| RC-3 | Whisper fallback | ✅ real inference (`tiny`/CPU) on SAPI-generated speech → contract-valid segments |
| RC-4 | **Karaoke word alignment** | ✅ regenerated the burned-in ASS and compared every Dialogue timing to the source VTT: **0 word mismatches / 44 words, every delta ≤0.010s** (ASS centisecond resolution). The one 0.833s outlier is a word starting before the cut, correctly clamped |
| RC-5 | Gemini still reachable | ✅ dispatch test |
| RC-6 | NVIDIA retry / fail-fast | ✅ 25 unit cases, **plus a live 410 correctly classified fatal and not retried** |
| RC-7 | **Render layer intact** | ✅ real 1080x1920 h264+aac clip (34.1s) + thumbnail from local mp4+vtt |
| RC-8 | Diarization / split-screen | ❌ **STILL UNVERIFIED** — needs `pyannote.audio` + `torch` + an accepted HF model agreement. The audio-path bug it depended on is unit-tested, but the split-screen render was never exercised |
| RC-9 | Story mode | ✅ assembled `hook_1.mp4` + `highlight_1.mp4` from two local sources; one used its VTT (Whisper bypassed), the other fell back to Whisper |
| RC-12 | Stdlib-only CI suite | ✅ verified in a clean venv with pytest as the only dependency (180 passed, 5 skipped). Any new web test must be checked there, not just locally |
| RC-11 | Job settings reach the pipeline | ✅ `tests/test_web_job_fields.py` — audit guard + round-trip for `video_cq`, `split_trigger`, `yolo_size`, `diarization_speakers`. Proven non-vacuous (8/11 fail against the pre-fix model) |
| RC-10 | Web API | ✅ upload mp4 → upload vtt → POST job → **`completed`** with a real 1080x1920 render; provenance persisted, `url` is `None` |

Dedupe effectiveness, measured on the realistic fixture: **86 words with dedupe
vs 244 without** — the LLM would otherwise have seen every sentence ~3x.

## What verification found (none caught by unit tests)

1. **`--voiceover` made `google-genai` mandatory for every run.** Optional
   feature, unconditional import.
2. **Subtitle burn-in failed on every Windows path** — pre-existing;
   `clipping/studio/` had an empty diff across all 11 stages. Correct escaping
   established by probing ffmpeg with 7 candidate forms.
3. **The shipped `--nvidia-model` default was dead** — `deepseek-v4-pro` returns
   `410 Gone` (EOL 2026-08-07). The brief's alternative
   `meta/llama-3.1-70b-instruct` is retired too.
4. **The web worker demanded a key for a render-only rerun.**
5. **`load_gemini_json` / `nvidia_model` never reached the backend** — undeclared
   on `JobCreateRequest`, so Pydantic dropped them.

## Remaining risks

- **A-007 (substantive):** `deepseek-ai/deepseek-v4-flash-0731` is confirmed to
  exist (probe returns 401, not 410) but has **not been called with a real key**,
  so its `guided_json` conformance is unverified. First real run will confirm.
- **RC-8:** split-screen / diarization not exercised.
- ~~21 more `JobCreateRequest` fields are still dropped by Pydantic~~ — **DONE**
  (`91b7712`, `832300c`); guarded by `tests/test_web_job_fields.py`.

## Known pre-existing breakage (not from this task)
- ~~`run_upload.py:17` imports `youtube_uploader.safety`~~ — **FIXED**
  (`f296eb3`). **Two corrections to what this entry used to say:** it was *not*
  excluded from the `compileall` CI job — `.github/workflows/ci.yml:29` has
  always included `run_upload.py`, and `compileall` byte-compiles without
  executing imports, so it structurally cannot catch a missing module. And the
  file had **two** breakages, not one: the dead import plus two keyword
  arguments (`safety_config`, `skip_approval`) that `upload_manifest_to_youtube`
  no longer accepts, so restoring `safety.py` alone would only have turned the
  `ImportError` into a `TypeError`. See DEC-018.
- `gdown` is declared in `pyproject.toml` only, so `--hook-source <drive-url>`
  fails in every documented install path.

## Follow-ups deliberately not done
- **No frontend test infrastructure.** A `scrollWidth == clientWidth` guard per
  route needs a headless browser (playwright/vitest + a harness), which is a
  dependency decision of its own. Until then the layout fix has Tier-2 evidence
  only.
- **`web/dashboard` ships no lockfile.** Only `node_modules/` is gitignored, so
  `npm install` produces an untracked `package-lock.json` that nothing pins.
  That conflicts with the "pin and verify" rule; adding one is a dependency task.
- ~~`.config-grid` missing from the media query~~ and ~~inline
  `minmax(300px, 1fr)` at `NewJob.jsx:384`~~ — **both FIXED** (`eb1feca`), and
  both were mis-described. `.config-grid` was *dead CSS* that no JSX referenced,
  not a rule missing a breakpoint; the grid now uses it. The NewJob grid does
  not overflow at 375px or 320px — it breaks below ~316px — and the real
  overflow at 320px was the provider chip, which nothing had listed.
- **CI cannot catch an orphaned import.** `.github/workflows/ci.yml:28` claims
  the `compileall` step "catches orphaned references in modules the test suite
  does not import". It cannot — `compileall` byte-compiles without executing
  imports, which is why `run_upload.py` stayed broken. A real guard is one line:
  `python -c "import run_upload"`. Not added here because CI's installed deps
  were not verified against it.
- **The two dependency manifests diverge in both directions.**
  `requirements.txt` carries 9 packages `pyproject.toml` lacks (`numpy<2.0.0`,
  `pyannote.audio`, `torch`, `torchaudio`, `fastapi`, `uvicorn`,
  `python-multipart`, `pydantic`, `edge-tts`). Only the `gdown` case was fixed;
  reconciling them is a dependency task.
- **Product question, not a bug: do YouTube uploads want guardrails again?**
  `upload_safety.json` is tracked but orphaned, and `Safety.md` exists to
  re-enable the feature. See DEC-018 — restore from `5bf93d5`, never from
  Safety.md.
- `hook_manager.py` (`--hook-source`) is now the only network fetch left in the
  CLI pipeline, which is inconsistent with local-first.
- ~~9 dead `from yt_dlp import YoutubeDL` imports remain in `clipping/studio/`~~
  — **FIXED** (`a269a8f`). The count was wrong: there were **10**, across 12
  files carrying the import. `studio/effects.py` and `studio/transitions.py`
  keep theirs (real call sites at `:86` and `:156`, per DEC-001).
