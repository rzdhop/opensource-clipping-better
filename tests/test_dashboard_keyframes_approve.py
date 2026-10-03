"""The storyboard pane's keyframe approval (phase 7 stage 6b, DEC-230) and,
since stage C, the review grid it points to: each shot's keyframe check (J2)
is read there, per shot, from the page's ``review`` block
(``workflow.episode_review``); the card keeps "Approve keyframes" / "Approve
anyway", before which no v2 clip is bought (RC-Q3), for a story that stopped
at the keyframes.

A text contract over ``StoryboardPane.jsx`` and ``ReviewPane.jsx`` (CI has
no node, like the other dashboard tests): the names they read from the
episode payload are the backend's own (``workflow.episode_view``'s
``keyframes`` block, the review's per-shot ``verdict``), and they post
``approve_anyway`` to the target the payload gives. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PANE = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode" / "StoryboardPane.jsx"
REVIEW = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode" / "ReviewPane.jsx"
WORKFLOW = ROOT / "clipping" / "aistory" / "workflow.py"


def _component(src, name):
    start = src.index(f"function {name}(")
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


def test_the_pane_approves_with_the_payloads_target_and_the_review_shows_each_check():
    src = PANE.read_text(encoding="utf-8")
    body = _component(src, "ApproveKeyframes")

    # The payload's keyframes block, as workflow.episode_view writes it.
    view = WORKFLOW.read_text(encoding="utf-8")
    block = view[view.index('view["keyframes"] = {'):]
    block = block[:block.index("}") + 1]
    for key in ("approval", "approved_at", "anyway", "target"):
        assert f'"{key}"' in block, key
        assert f"keyframes.{key}" in body, key
    assert re.search(r"approveStoryDoc\(storyId, keyframes\.target, anyway \? \{ approve_anyway: true \}", body)
    assert "Approve anyway" in body and "Approve keyframes" in body
    # Stage C: each shot's check is the review grid's (its verdict per shot), not a text row here.
    assert "assets.doc.keyframe_verdicts" not in body
    review = REVIEW.read_text(encoding="utf-8")
    for field in ("state", "issue", "redraws", "gave_up"):
        assert f"verdict.{field}" in review, field
    assert re.search(r"approveStoryDoc\(storyId, review\.approvals\.keyframes\.target", review)


def test_the_panel_sits_between_the_keyframes_and_the_video_phase():
    src = PANE.read_text(encoding="utf-8")
    page = src[src.index("export default function StoryboardPane"):]
    assert (page.index("<ImageOfferBanner") < page.index("<ApproveKeyframes")
            < page.index("<VideoPhaseHeader") < page.index("<ApproveAssets"))
