# Plan 30 — The keyframe check warns, never blocks (DEC-311)

Date: 2026-10-06. Decided in chat by the human during the first RunPod story
(`df1544f0641f`): "we want fun videos, not exactly the right things … all this
time I'm paying for no results at all." Approved in substance in chat; the
stages below ran under that go.

## Goal

A shot image the J2 judge still flags after its two automatic redraws, or that
was never checked, no longer stops anything: it is approved with its issues
kept as a warning, the clips are made from it, the Handoff takes an upload for
it, Approve all and the one-click run go on, and the screens say "Approved …
despite: …" the way the cast and places tiles do since plan 29 (DEC-307).

## Stages

1. **Warn-only gate** (one stage, one worktree, Opus): `keyframe_findings`
   routes app-made failed/unjudged shots to `warnings`; `workflow.approve_keyframes`
   drops the verdict refusal and records `keyframes_approved.shots {sid:
   {issues, image_hash}}` (schema + validator); the fast track's
   `approve_keyframes` no longer raises; `brief.keyframe_check` gives no
   `upload_refusal` for failed/unjudged (missing and stale keep theirs); the
   dashboard shows the warning; docs, CLI help, module docstrings, CHANGELOG.
   Files: clipping/aistory/{schemas,workflow,cli}.py,
   clipping/aistory/steps/{judge,assets,fast_track,brief}.py,
   web/api/routes/stories.py, web/api/models.py,
   web/dashboard/src/pages/story/{ReviewPane,AssetsCards,EpisodeStudio,
   EpisodeApproveAll,HandoffChecks}.jsx, docs/AI_STORY.md, CHANGELOG.md,
   tests (re-pins listed in the agent prompt; one new hash-match test).
   Risk: medium (cross-cutting readers of the verdict). Verification: the
   keyframe/fast-track/handoff/dashboard selections in both envs, the vite
   build, the 3.11 in-image compile. Rollback: `git revert` of the one commit.
   Regression contract at risk: RC-Q3 (no clip before the approval is
   current — preserved, the approval stays the gate), RC-E2, RC-G1, the sheet
   gate (tests/test_story_sheet_gate.py untouched), DEC-303 hashes
   (tests/test_story_send_layer.py), the no-auth routes.
2. **Deploy at 0 jobs** (`rm -sfv` + `up -d --build`: the bundle changed), then
   `POST /api/stories/df1544f0641f/steps/story-fast-track` to continue the
   parked run: 12 RunPod clips (est $1.20) + lip-sync ($0.14), no image spend.

## Rejected alternative

Approving the five flagged shots by hand through the API for this story only
and keeping the hard gate: it would have shipped the episode but left the
rule that produced the roulette; the human asked for the rule to change.

## DECISIONS.md check

Touches DEC-305 point 5 (the hard judge), DEC-306 item 4 (the Handoff gate),
DEC-307 (keyframes "an open question"), DEC-308 item 5, DEC-309 item 7 — all
amended for keyframes by DEC-311. The sheet gate (DEC-307) is untouched.

## Riskiest stage

Stage 1: a reader of the verdict that still expects a refusal (the Handoff,
the fast track's summary, the review block) would show a refusal that never
comes, or hide the warning. The re-pinned tests name each reader.
