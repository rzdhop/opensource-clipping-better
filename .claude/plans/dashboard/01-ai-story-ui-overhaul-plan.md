# AI Story dashboard — UI/UX overhaul plan (2026-10-03)

The human's ask: "upgrade the whole UI/UX of the AI Story pages". Answers (2026-10-03): full redesign in stages;
refined dark studio (keep the violet-on-slate identity, add hierarchy, covers, a real icon set); small dependencies
allowed (lucide-react only).

## What the walk and the map found (EXPLORE)
- Stack: React 19 + react-router 7 + Vite 6, plain JS, one 3,183-line `index.css` with tokens, no component library,
  emoji icons, Inter only (`--font-mono` dead), dark only, no JS tests (DEC-012: pytest-only CI — the 12
  `tests/test_dashboard_*.py` files are regex contracts over the JSX source), no lint. Node 18.19 / npm 10 here;
  the image bakes `web/dashboard/dist` (rebuild: `sudo -n docker compose rm -sfv backend && up -d --build backend`).
- Pain: `/story/:id` is one 912-line wizard shell stacking every step; the Cast step prints every field of every
  character inline (4 × 12 fields); the episode Script tab shows 10 emotion chips and a full speaker list under
  every line; `StoryboardPane.jsx` is 1,550 lines; `fmtUsd` is copied six times; 12 `window.confirm()` sites; no
  toast/dialog/empty-state primitives; the stories list has no cover, no progress, a red Delete on every card; the
  fast-track progress is a regex over log lines; mobile = a collapsed sidebar only.

## Stages (each one commit, built with `npm run build`, deployed by an image rebuild at 0 jobs)
| # | Stage | Goal | Files | Risk | Verified by | Rollback |
|---|---|---|---|---|---|---|
| 1 | Foundation | `lucide-react@1.51.0` (pinned); `src/ui/`: Button, IconButton, Card, Badge/Chip, Dialog (promise `confirmDialog()` replacing every `window.confirm`), Toast, EmptyState, Skeleton, Field, Money (one `fmtUsd`), icon map; tokens: spacing/type scales, focus ring, z-index; the 12 text contracts updated where a literal moved | `web/dashboard/{package.json,package-lock.json,src/ui/*,src/index.css}`, every page's confirm sites, `tests/test_dashboard_*.py` | medium | `npm run build`; the 12 dashboard tests both envs; a browser walk of every page (nothing generated) | `git revert` of the one commit |
| 2 | Stories list | Cards with a cover (first approved portrait, else the style swatch), progress ribbon (steps done, episodes), last activity, overflow menu (Delete behind the Dialog), hero empty state | `StoriesList.jsx`, `api.js`, maybe `web/api/routes/stories.py` (a `cover` field on the list payload) + its test | low | build; `tests/test_stories_api*.py` if the payload grows; browser | revert |
| 3 | Story workspace | Routes `/story/:id/:step`; a left step rail (Concepts…Knowledge) with status and the next CTA; a sticky header (title, language·style, visual tier, Open episode); one step per screen; Cast and Places as portrait card grids with an expandable editor per entity | `NewStoryWizard.jsx` → `StoryWorkspace.jsx` + `StepRail.jsx` + the steps; `App.jsx` routes; `test_dashboard_{generation_profile,phase7_editing,switch_pipeline}.py` | high | build; the dashboard tests; a browser walk of all 7 steps on d0ee5ebd745d | revert |
| 4 | Episode studio (**riskiest**) | A progress stepper (Script → Storyboard → Keyframes → Clips → Render → Review) from the episode payload; the Generate button with its sub-step; Script: compact line rows (speaker avatar, one emotion select, duration, play, hover toolbar), scene headers, timing warnings in a panel; Storyboard: a filmstrip with keyframe/clip state badges and a shot detail; Review: hero player + approvals checklist + flagged shots | `EpisodeStudio.jsx`, `episode/*.jsx` (StoryboardPane split in three), `test_dashboard_{episode_reedit,generate_episode,clip_controls,keyframes_approve}.py` | high | build; the dashboard tests; a browser walk of the four tabs on episode 1; the one-click walk stays the human's | revert |
| 5 | Feed, settings, responsive, a11y | The activity feed as a grouped timeline (step headers, errors, copy); settings as status cards; mobile layouts (rail → bottom tabs, studio → tabs); labels/`htmlFor`, focus rings, reduced motion; the consistency sweep | `ActivityFeed.jsx`, `Settings.jsx`, `index.css`, `App.jsx` | medium | build; tests; browser at 375 px and 1280 px | revert |

Each stage is built by an Opus agent in its own worktree (cross-cutting UI), reviewed and verified by me in the
built-in browser, then merged, rebuilt and deployed. Tier 2 has no E2E suite (DEC-012): the browser walk with
screenshots is the substitute, said in each stage's log line.

## Rejected
- A TypeScript rewrite with a component kit (Radix): the regex contracts over the source make a rewrite cost a
  re-pin of every dashboard test at once; the human chose small dependencies.
- A second SPA for AI Story: DEC-094 keeps two modes behind one shell.

## DECISIONS check
DEC-012 (pytest-only CI: no JS test runner; the text contracts stay and move with the code), DEC-094 (one shell,
two modes, the mode remembered), DEC-173 and the no-auth memory (never a forced sign-in; `test_dashboard_no_sign_in`
unedited), DEC-227/246 (the one click and the Review tab keep their behaviour; only their surface changes). No conflict.

## Regression contract (dashboard)
| Item | Must keep working | Proof |
|---|---|---|
| RC-D1 | no forced sign-in | `tests/test_dashboard_no_sign_in.py` unedited |
| RC-D2 | every API call and payload the pages make today | the 12 dashboard contracts (moved only where a literal moved, named per stage) |
| RC-D3 | Clips mode untouched | `Dashboard.jsx`, `JobDetail.jsx`, `NewJob.jsx` not in any stage's diff (shared primitives aside) |
| RC-D4 | the image builds and serves the SPA | `npm run build` green; health 200 after the rebuild |
