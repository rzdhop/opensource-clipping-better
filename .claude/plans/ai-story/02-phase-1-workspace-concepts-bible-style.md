# Phase 1 prompt — story workspace, concepts, bible, style lock

Paste this into Claude Code from the repository root. Phase 0 must be merged and its
Tier-2 acknowledged.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0 (plan →
checkpoint → stages → Tier-1 → Tier-2 with my ack → docs + decisions). All code and
prompts in English; user-facing generated text in the story's language.

## Goal of this phase

The first four steps of the creation workflow (spec section 3, steps 1–4), the story
store, and the data the whole mode relies on.

1. **Story store** (spec 2): `outputs/stories/<story_id>/` layout, `outputs/stories.json`
   index with the same RLock/atomic-write discipline as `outputs/jobs.json`; document
   loaders/savers with `$schema` validation in `clipping/aistory/store.py` and
   `clipping/aistory/schemas.py` (plain dicts, stdlib); `updated_at` on every save;
   delete frees only that story's folder (mirror the job delete rules and tests).
2. **Story-step jobs** (spec 9.1): `kind: "story_step"` on the existing store/worker,
   `awaiting_approval` status (terminal for the worker so the single slot is freed,
   flipped to `done` by `/approve`, survives restart — fix `fail_stale_jobs` accordingly
   and record the DEC; spec 9.1), progress through the existing stdout tee, cancel token
   honoured; the job list groups story-step jobs under their story. Endpoints from spec 9.2 needed for steps 1–4 plus
   `GET/POST /api/stories`, `GET/PATCH/DELETE /api/stories/{id}`, `/concepts`,
   `/concepts/generate`, `/concepts/choose`, `/steps/bible`, `/steps/style`,
   `/approve/{bible|style}`, `/regenerate` (bible fields), `/estimate/{step}`. Pydantic
   models with `model_fields_set`; payload-contract tests for every endpoint.
3. **LLM plumbing for stories** (spec 4.1): `clipping/aistory/prompts.py` with
   `PROMPT_VERSION`, `IDEATION_TEMPERATURE = 0.9`, pure builders
   `build_<id>(ctx) -> (system, user, schema)` for **C1, B1, B2, B3** with golden-string
   tests; `clipping/aistory/context.py` context pack (≤ ~1,200 input tokens, tested);
   all calls through the existing `complete_json` ladder and `LLM_CHAIN`; each response
   ≤ ~400 output tokens; a new DEC extending DEC-027 ("one request, one artifact") to
   story writing.
4. **Concept library** (spec 7): the ten concepts as JSON in
   `clipping/aistory/templates/concepts/`, FR and EN fields, validated by a test;
   `GET /concepts` filtered by language/style; "Generate 10 more" = C1 × 2 seeded by
   language, style and optional user text; concept cards carry `value`,
   `retention_mechanics`, `style_fit`.
5. **Style templates** (spec 5): the seven templates as JSON in
   `clipping/aistory/templates/styles/` **with the complete text from spec 5.1–5.7**,
   validated against a schema (all fields present, palette hex valid, motion rules from
   the closed lists in 6.3); `style_lock.json` creation = expanded template + overrides
   (pure function, tested); optional preview strip (3 tiny images through `IMAGE_CHAIN`,
   estimate shown first, skipped when the route is unavailable).
6. **Prompt assembly** `clipping/aistory/prompting.py`: the common shot-prompt skeleton,
   negative base + template negative, reference-sheet and master-plate prompts as string
   templates with placeholders (spec 5) — implemented and tested now (golden strings),
   used from phase 2 on.
7. **Dashboard** (spec 10): `StoriesList` and `NewStoryWizard` steps 1–4 (language,
   seed text, optional style pick; concept cards with pick/"generate more"; bible cards
   with inline edit / regenerate-with-note / approve; style step with palette and
   typography tweaks, preview strip, approve). Estimate chip and route chip on every
   generating button. 375/820/1280 px.
8. **CLI** (spec 9.3): `main.py --ai-story new …`, `--ai-story step <id> bible|style
   [--auto-approve]`; five-place default agreement test for any new default.
9. **Docs + decisions**: `docs/AI_STORY.md` (steps 1–4 walkthrough), VISION "Where it
   stands", CHECKPOINT, DEC/A entries (including the A-entries of spec 14 not yet
   registered).

## Out of scope

Characters/places/props (phase 2), episodes (phase 3+), rendering, imports.

## Before you plan

Ask only what the spec and code leave open. Then propose stages (riskiest labelled),
regression contract (clip jobs, settings, auth, phase-0 chains), expected DEC/A, and the
Tier-2 script: on the phone, create a FR story from `tentafruit_island`, generate 10 more
concepts on the free LLM chain, approve a bible after one regenerate-with-note, lock the
`fruit_drama` style, and confirm every LLM call printed its hop and stayed under the
token cap in the activity feed.

## Acceptance

- Tier-1 green in both envs; each behaviour test shown failing pre-change.
- A story survives a backend restart in `awaiting_approval`.
- `story.json` and `style_lock.json` validate; deleting the story removes only its folder.
- Concept and style JSON files pass their schema tests; every template's text matches
  spec 5 (a test can assert key phrases per template).
