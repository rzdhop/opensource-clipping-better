"""The storyboard pane's keyframe approval (phase 7 stage 6b, DEC-230): the
keyframe check's (J2) verdict per shot and "Approve keyframes" / "Approve
anyway", before which no v2 clip is bought (RC-Q3).

A text contract over ``StoryboardPane.jsx`` (CI has no node, like the other
dashboard tests): the names it reads from the episode payload are the
backend's own (``judge.KEYFRAME_VERDICTS``, ``workflow.episode_view``'s
``keyframes`` block), and it posts ``approve_anyway`` to the target the
payload gives. Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PANE = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode" / "StoryboardPane.jsx"
WORKFLOW = ROOT / "clipping" / "aistory" / "workflow.py"


def _component(src, name):
    start = src.index(f"function {name}(")
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


def test_the_pane_shows_the_keyframe_checks_and_approves_with_the_payloads_target():
    from clipping.aistory.steps import judge

    src = PANE.read_text(encoding="utf-8")
    body = _component(src, "ApproveKeyframes")

    assert f"assets.doc.{judge.KEYFRAME_VERDICTS}" in body
    for field in ("shows_beat", "missing", "continuity_issue"):
        assert f"verdict.{field}" in body, field
    # The payload's keyframes block, as workflow.episode_view writes it.
    view = WORKFLOW.read_text(encoding="utf-8")
    block = view[view.index('view["keyframes"] = {'):]
    block = block[:block.index("}") + 1]
    for key in ("approval", "approved_at", "anyway", "target"):
        assert f'"{key}"' in block, key
        assert f"keyframes.{key}" in body, key
    assert re.search(r"approveStoryDoc\(storyId, keyframes\.target, anyway \? \{ approve_anyway: true \}", body)
    assert "Approve anyway" in body and "Approve keyframes" in body


def test_the_panel_sits_between_the_keyframes_and_the_video_phase():
    src = PANE.read_text(encoding="utf-8")
    page = src[src.index("export default function StoryboardPane"):]
    assert (page.index("<ImageOfferBanner") < page.index("<ApproveKeyframes")
            < page.index("<VideoPhaseHeader") < page.index("<ApproveAssets"))
