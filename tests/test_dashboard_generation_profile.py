"""The story page's "Visual tier" card (phase 6 stage 12): the per-route
video estimate must be priced on an episode that can actually answer it.

Browser-check finding F5: before this, the card always priced the episode
"after the last created one" (or the first with no timed script), which --
for a story mid-season, its early episodes already approved and rendering,
later ones not started -- is an episode with no script at all, so all three
routes answered "Episode N has no script yet" instead of a useful estimate.
It must instead prefer the latest episode with an *approved storyboard*
(the assets step, and so the video estimate, can actually run on it), and
only fall back to "the next one to work on" when no episode has one yet.

Text contract over GenerationProfileCard.jsx (the card moved out of NewStoryWizard.jsx
with the story workspace, DEC-255), like the rest of the dashboard test
suite (DEC-012: stdlib + pytest only, no JS runner).
"""

import re
from pathlib import Path

WIZARD = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "pages" / "story" / "GenerationProfileCard.jsx"


def _src():
    return WIZARD.read_text(encoding="utf-8")


def test_the_video_estimate_prefers_the_latest_episode_with_an_approved_storyboard():
    src = _src()
    assert "storyboard_state === 'approved'" in src, (
        "the priced episode must prefer the latest one with an approved storyboard"
    )
    # The fallback ("the next one to work on") must still be there for a
    # story where no episode has an approved storyboard yet.
    assert "unfinishedEpisode" in src


def test_the_card_title_does_not_call_it_the_next_episode():
    """The priced episode is the latest *approved* one, not necessarily the
    next to work on, so the heading must not say "next"."""
    src = _src()
    match = re.search(r"<p className=\"form-hint\">Episode \{nextEp\}'s ([^<]*), per route:</p>", src)
    assert match, "the per-route estimate heading is missing"
    assert "next" not in match.group(1).lower(), f"the heading should not say 'next': {match.group(1)!r}"
