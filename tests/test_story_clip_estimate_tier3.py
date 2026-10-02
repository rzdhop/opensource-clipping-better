"""A tier-3 estimate on a link whose clips carry no sound says so before any
clip is bought (the log sweep, phase 6 A-108: "seedance, the cheapest default
link, carries no audio, so tier-3 shots on it always fall back, and the
estimate does not warn"). Tier 2 and a link with sound are unchanged.
``tests/test_story_clip_estimate.py``'s episode and helpers. Stdlib + pytest
(DEC-012).
"""

from __future__ import annotations

import test_story_assets_step as tas
import test_story_clip_estimate as tce
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are

SILENT = "keeps a clip's own sound, but fal/seedance-1-pro-fast makes clips with none"


def test_a_tier_3_estimate_on_a_silent_link_says_it_falls_back(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, tier=3)

    video = tce._units(store, story_id, tce._settings(ALLOW_PAID="1"))["video"]

    assert video["link"] == tce.SEEDANCE and video["plan"]
    assert SILENT in video["message"] and "rendered as at tier 2" in video["message"]


def test_a_tier_2_estimate_says_nothing_of_sound(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    tce._tier(store, story_id, tier=2)

    video = tce._units(store, story_id, tce._settings(ALLOW_PAID="1"))["video"]

    assert video["link"] == tce.SEEDANCE and "sound" not in video["message"]
