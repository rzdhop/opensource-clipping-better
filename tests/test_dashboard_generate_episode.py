"""The episode studio's "Generate episode" -- one click up to the finished
render -- and the review screen it ends on (phase 7 follow-up, stage C; the
human, 2026-10-02: "I generate directly the whole thing at once, ready to
read and approve").

A text contract over the dashboard sources (CI has no node, like the other
dashboard tests): ``EpisodeStudio.jsx``'s header sends the fast track's own
params (``stop_at_keyframes`` off by default), its confirm says what the
click does and costs, it shows the sub-step the job's feed names and switches
to the review when the job ends; ``episode/ReviewPane.jsx`` reads the page's
``review`` block (``workflow.episode_review``) -- the verdict states it
labels are the backend's closed list -- and approves what is pending with the
existing approve routes, in order. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pathlib
import re

from clipping.aistory import workflow
from clipping.aistory.steps import fast_track

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "web" / "dashboard" / "src"
EPISODE_STUDIO = DASH / "pages" / "story" / "EpisodeStudio.jsx"
REVIEW_PANE = DASH / "pages" / "story" / "episode" / "ReviewPane.jsx"
# Dashboard overhaul stage 4 (DEC-256): the keyframe card moved out of
# StoryboardPane.jsx into storyboard/AssetsCards.jsx.
STORYBOARD_PANE = DASH / "pages" / "story" / "episode" / "storyboard" / "AssetsCards.jsx"
INDEX_CSS = DASH / "index.css"


def _component(src, name):
    start = src.index(f"function {name}(")
    ends = [index for index in (src.find("\nfunction ", start + 1), src.find("\nexport default function", start + 1))
            if index != -1]
    return src[start:min(ends)] if ends else src[start:]


def _object_literal_keys(src, const_name):
    match = re.search(rf"const {const_name} = \{{([^}}]*)\}}", src)
    assert match, f"{const_name} object literal not found"
    return set(re.findall(r"([a-z0-9_]+)\s*(?::[^,]*)?(?:,|$)", match.group(1)))


# ------------------------------------------------------------ Generate episode

def test_the_header_is_generate_episode_with_the_stop_at_keyframes_checkbox_off():
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    header = _component(src, "FastTrackHeader")
    assert "'Generate episode'" in header and "'Fast track'" not in header
    assert _object_literal_keys(header, "fastTrackParams") == set(fast_track.PARAMS)
    assert f"{fast_track.STOP_PARAM}: stopAtKeyframes" in header
    assert "useState(false)" in header.split("stopAtKeyframes", 1)[1][:80]
    assert 'type="checkbox"' in header and "Stop at the keyframes for my review" in header


def test_the_confirm_says_what_the_click_does_and_costs():
    """The confirm reads the estimate (GET /estimate/fast-track) as
    fast_track.estimate shapes it: the keyframes block (stage C), the auto-fix
    ceiling, the clips, the render, the total against the caps -- and says in
    words that there is no stop for the keyframe review."""
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    header = _component(src, "FastTrackHeader")
    confirm = header[header.index("const confirmMessage"):header.index("const handleRun")]
    for read in ("est.keyframes", "kf.v2", "kf.fix_usd", "kf.tier", "est.video", "est.video.count",
                 "est.render.minutes", "est.est_usd", "est.paid.caps", "est.images.count", "est.tts.lines"):
        assert read in confirm, read
    assert "up to the finished render" in confirm
    assert "No stop for keyframe review" in confirm and "review the finished episode" in confirm
    assert "redrawn automatically" in confirm and "checked (J2)" in confirm
    assert "stops once the keyframes are made and checked" in confirm
    assert "caps:" in confirm and "Total: est. $" in confirm
    assert "It stops before any paid spending" in confirm


def test_the_header_shows_the_sub_step_the_feed_names_and_the_studio_ends_on_the_review():
    src = EPISODE_STUDIO.read_text(encoding="utf-8")
    # The feed line the fast track prints for each sub-step ("⏩ Fast track 3/6: paid check").
    match = re.search(r"const FAST_TRACK_STEP_LINE = /(.+)/\n", src)
    assert match, "no FAST_TRACK_STEP_LINE regex"
    pattern = re.compile(match.group(1))
    found = pattern.search(f"⏩ Fast track 3/{len(fast_track.SUB_STEPS)}: {fast_track.LABELS['paid_check']}")
    assert found and found.group(1) == "3" and found.group(3) == "paid check"
    header = _component(src, "FastTrackHeader")
    assert "Generating…" in header and "progress.number" in header and "progress-bar-fill" in header
    # The review tab, shown for a v2 episode (its review has a keyframe approval), and the switch at the end.
    assert "'review'" in src.split("const TAB_IDS", 1)[1][:120]
    assert "episode.review" in src and "<ReviewPane" in src and 'id="episode-review"' in src
    assert "job.step === 'fast-track'" in src and "setReviewAfterJob(true)" in src
    assert "onTabChange('review')" in src and "scrollIntoView" in src


# ---------------------------------------------------------------- the review

def test_the_review_pane_reads_the_page_s_review_block_and_labels_every_verdict_state():
    src = REVIEW_PANE.read_text(encoding="utf-8")
    assert "episode.review" in src
    for read in ("review.headline", "review.status", "review.pending", "review.auto_approved", "review.flagged",
                 "review.spend", "review.render", "review.shots", "review.approvals"):
        assert read in src, read
    for field in ("total_usd", "images_usd", "fixes_usd", "voices_usd", "clips_usd"):
        assert f"spend.{field}" in src, field
    chip = _component(src, "verdictChip")
    for state in workflow.REVIEW_VERDICT_STATES:
        assert f"case '{state}':" in chip or ("default:" in chip and state == "unchecked"), state
    assert "fixed after" in chip and "still flagged" in chip and "not current" in chip and "passed" in chip
    for read in ("verdict.state", "verdict.issue", "verdict.redraws"):
        assert read in src, read
    # Each tile: the keyframe thumbnail, the clip when made, the shot's lines, tap to see it large.
    assert "fetchShotImageUrl(storyId, ep, shot.image_name)" in src
    assert "fetchEpisodeClipUrl(storyId, ep, shot.clip.name)" in src
    assert "shot.lines" in src and "<video" in src and 'role="dialog"' in src


def test_the_review_approves_what_is_pending_in_order_and_regenerates_in_place():
    src = REVIEW_PANE.read_text(encoding="utf-8")
    approve = _component(src, "ApproveAll")
    keyframes = approve.index("approveStoryDoc(storyId, review.approvals.keyframes.target")
    assets = approve.index("approveStoryDoc(storyId, `assets:${ep}`)")
    assert keyframes < assets
    assert "approve_anyway: true" in approve and "Approve anyway" in approve
    assert "Approve keyframes and assets" in approve and "Everything is approved" in approve
    assert "story-step-error" in src
    # The regenerate controls are the existing ones, on the existing targets.
    assert "regenerateStory(storyId, { target: `shot:${ep}:${shot.shot_id}`, note })" in src
    assert "regenerateStory(storyId, { target: shot.clip.target, note })" in src
    assert "<RegenerateControl" in src and "fetchStoryEstimate(storyId, 'regenerate', { target: shot.clip.target })" in src


def test_the_storyboard_pane_s_keyframe_card_points_to_the_review():
    """The per-shot text rows left StoryboardPane for the review grid; the
    card keeps the approval (for a story that stopped at the keyframes)."""
    src = STORYBOARD_PANE.read_text(encoding="utf-8")
    body = _component(src, "ApproveKeyframes")
    assert "assets.doc.keyframe_verdicts" not in body
    assert "Review" in body and "episode.review" in body
    assert "Approve keyframes" in body and "Approve anyway" in body


def test_the_review_grid_fits_three_tiles_at_375px():
    css = INDEX_CSS.read_text(encoding="utf-8")
    block = css[css.index(".story-review-grid {"):]
    block = block[:block.index("}")]
    match = re.search(r"minmax\((\d+)px", block)
    assert match and int(match.group(1)) <= 104, block
    assert "story-review-overlay" in css and "story-review-tile" in css
