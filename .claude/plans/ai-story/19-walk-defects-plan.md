# 19 — Plan: the episode-2 walk's six defects (task A of DEC-263)

Date 2026-10-04. Approval: the human's "decide for me and go" (DEC-263). EXPLORE: three Sonnet agents (F5; F1+F2;
F3+F4+F6), their maps reconciled below; no contradictions. Checkpoint: main `5dfc598` (docs only since `fb0bb38`,
the deployed code).

## Stages (each on its own branch and worktree, merged in order after the running episode job ends)

### Stage 1 — F1 + F2: an unbilled refusal releases its booking; a purged request is re-sent once; "no face" is final
- **Goal.** `generation._resume()` (`clipping/providers/generation.py:710-738`) today settles any 404/410 as `LOST`
  ("stays booked") and keeps a 422 "for the next run". Fix: a status in `gencache.UNBILLED_STATUSES` (404, 422…)
  reaching `_resume` is *proven unbilled*: a new `Journal.void(reason)` writes state `VOID` and calls a new
  `cache.release(entry)` hook (a negative ledger row through `LineGates.booker` in `clipping/aistory/voices.py`,
  `budget.DailySpend.add(-usd)`), then — F1 — the journal entry is cleared and the same link submits once more in
  the same run (inside `MAX_ATTEMPTS`); 410 keeps today's `LOST`. F2: a Kling `face_detection_error` (422) ends the
  shot's lipsync in a new final state `no_face` (`schemas.LIPSYNC_STATES`), keyed on the clip's sha256 so a new clip
  clears it; `lipsync_todo`, `is_current`-style checks and `lipsync_units` (the estimate) skip it; one distinct log
  line; the plain clip stays the take (DEC-258).
- **Files.** `clipping/providers/generation.py`, `gencache.py`, `budget.py`, `clipping/aistory/ledger.py`,
  `voices.py`, `schemas.py`, `steps/assets.py` (`fail_lipsync`, `lipsync_todo`), `steps/lipsync.py`.
- **Tests.** `tests/test_gencache_runner.py`: split the 404/410 case (410 lost; 404 voided and re-sent once); a new
  422 case. `tests/test_budget.py` (or beside it): a void row is negative and today's/the episode's spend drops back.
  `tests/test_story_lipsync.py`: `test_a_face_detection_422_is_final_voids_the_booking_and_is_never_retried` + the
  second run's estimate no longer counts the shot.
- **Risk.** First code path that ever reverses a booking (RC-A3 "every billed request booked" must stay true;
  the new rule is "an unbilled one does not stay booked"). Rollback: revert the stage commit.

### Stage 2 — F5: a storyboard re-plan keeps the shots, ids and assets of the scenes it did not plan again
- **Goal.** `shots.build_storyboard` (`clipping/aistory/shots.py:2035-2130`) rebuilds every shot with empty assets
  and renumbers `shot_id` sequentially, so a 3-scene repair discarded 15 bought keyframes and clips. Fix: shot ids
  are stable keys — a scene not in the planned set keeps its shots verbatim (ids, `assets`, verdicts, prompt hashes),
  a re-planned scene's shots get fresh ids (the next free numbers), and the `shots` list order follows the script.
  Seeds (`derive_seed(story_id, ep, shot_id)`) therefore stay stable. Audit every reader that assumes `shot_id`
  encodes order or contiguity (sorting by id, `int(shot_id[2:])`, `shot_NN` file names) — list them in the report;
  order must come from list position. Optional, same stage if the request can be built at estimate time: the image
  and clip quotes (`assets.py:1399-1463`) ask `GenCache.lookup` and price a hit at $0.
- **Files.** `clipping/aistory/shots.py`, `steps/storyboard.py` (`build()` already receives `previous`/`stale`),
  possibly `steps/assets.py`, `steps/clips.py`, `steps/judge.py` (readers), docs/AI_STORY.md (the promise, now
  kept).
- **Tests.** Extend `tests/test_story_episode_steps.py::test_a_t1_run_replans_only_what_is_fast_and_a_complete_rerun_makes_no_call`
  (unchanged scenes' `shot_id` and `assets` byte-identical after a partial re-plan); a T1-path sibling of
  `tests/test_story_reedit.py::test_no_re_edit_path_rebuilds_the_storyboard_and_untouched_shots_keep_their_assets`;
  a renumbering case (a scene grows from 2 to 3 shots: later scenes keep their ids). Estimate: a gencache-hit case
  beside `tests/test_stories_api_phase4.py::test_the_render_metadata_and_fast_track_estimates` if built.
- **Risk — the riskiest stage.** Readers assuming contiguous ids; RC-M3 (stored episodes re-render byte-identical:
  a storyboard with no re-plan must come out identical), RC-V6 (estimate and run agree), RC-M1 (ep-1 prompt
  goldens unedited). Rollback: revert the stage commit.

### Stage 3 — F3 + F6 + F4: the redraw note restates the framing; a check-only script run; the fast track approves over repeated blocking issues once the repairs are spent
- **F3.** `assets.correction_note` (`steps/assets.py:1757-1772`) echoes J2's prose at the prompt's tail. Fix: when a
  shot is flagged, the note ends with the imperative built from the shot's own data —
  `Frame this as {FRAMING_PHRASES[shot["framing"]]}, nothing wider.` (v2 path only; v1 prompts untouched). Add a
  `framing_issue` field to `_J2_ASK` (`prompts.py:3041-3044`) so framing stops overloading `continuity_issue`; the
  brief (`judge.keyframe_brief`) is unchanged.
- **F6.** `POST /steps/script` gains `params.check_only` (`steps/script.py` `_Run.run()`): writes nothing, runs
  `consistency()` + `first_watch()` on the current revision, never `repair()`; `workflow.approve_script`'s stale
  message names it ("run the script step with check only"). The dashboard's script header gets a "Check again"
  action calling it (small; `web/dashboard/src/pages/story/episode/...`, the contracts re-pointed if a literal moves).
- **F4.** `fast_track`: when `repair()` has spent `REPAIR_PASSES_MAX` and only blocking issues remain, the one click
  approves the script anyway (`approve_script(approve_anyway=True)`, `by: fast_track`) and names the issues in the
  review, exactly as it does for keyframes (DEC-246); `params.stop_on_script_issues: true` keeps today's stop.
  Only for v2 reports carrying severities (DEC-261). Revise
  `tests/test_story_fast_track.py::test_the_auto_approval_rule_is_pure_and_never_approves_anyway` and
  `::test_a_consistency_check_with_issues_stops_at_the_script_and_is_never_approved_anyway` on purpose (the rule
  changes), add `test_the_fast_track_approves_anyway_after_the_repair_passes_are_spent` and the stop param's case.
- **Tests.** `tests/test_story_keyframe_fix.py` (the note), `tests/test_story_keyframe_gate.py` (the J2 ask field),
  new `tests/test_story_script_check_only.py`, `tests/test_story_fast_track.py`, `tests/test_story_workflow_episode.py`
  (`test_editing_a_line_re_times_it_stales_the_check_and_clears_both_approvals` stays unedited).
- **Risk.** RC-E2 (episodes never touch the story's approvals), RC-M1/RC-Q1 (v1 prompts and hashes untouched).
  Rollback: revert the stage commit.

## Verification
Tier 1 per stage: the touched test files in both environments (DEC-234), plus `tests/test_render_layer_guard.py`
and `tests/test_story_prompts_episode.py` goldens. Once before main: the full local suite. Tier 2: the next episode
walk (episode 3, or a re-run) — the human's phone verdict. Tier 3: the tests named above, in the same commit as each
fix.

## Rejected
- Fixing F5 by renaming `shot_NN` files on renumber (fragile; ids as stable keys is simpler and keeps the seeds).
- Dropping the hard length gate or the judges to avoid F4/F6 (DEC-231/248 stand; the fast track only gains the same
  "anyway" the keyframes already have).

## DECISIONS check
Touches DEC-153/174/175 (bookings: adds a release for proven-unbilled requests, consistent with their wording),
DEC-162/248 (the fast track's never-anyway rule — amended on purpose for spent repairs), DEC-243 (the note),
DEC-258 (plain clip kept: unchanged), DEC-129 (text-only edits keep the storyboard: unchanged). No conflict with
DEC-173 (no auth) or DEC-219 (quality).

## Riskiest stage
Stage 2 (shot ids as stable keys across every reader).
