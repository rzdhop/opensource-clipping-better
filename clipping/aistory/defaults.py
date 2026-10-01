"""The story defaults and closed lists of a StoryBible (spec 2.1, 8, 8.1, 8.5).

One place for the values a new story starts with, so the API request model,
the CLI and the dashboard form can each be tested against the same constants.

There is deliberately **no default language**: a story must name ``fr`` or
``en``. A silent default would be exactly the "silent fallback" spec 0
forbids -- a French user who forgot a field would get an English season.

Constants only, no imports: ``schemas.py`` builds the ``story_bible_v1``
enums from these tuples, so this module must not import it back.
"""

from __future__ import annotations

# ------------------------------------------------------ generation profile (spec 8)

DEFAULT_TIER = 1
DEFAULT_ROUTE = "auto"
DEFAULT_CONSISTENCY_MODE = "references"
DEFAULT_BUDGET_PROFILE = "free"

TIERS = (1, 2, 3)
ROUTES = ("auto", "local", "api")
CONSISTENCY_MODES = ("references", "prompt_only")
BUDGET_PROFILES = ("free", "one_dollar", "quality")

# Phase 7 (DEC-221): the optional ``generation_profile.pipeline``. Absent is
# the legacy pipeline; "v2" gates the quality-only image links per role
# (``media_policy``) and the phase-7 behaviour built on it. Never in the
# fresh profile below: a story is v2 only when it is created so.
PIPELINE_V2 = "v2"
PIPELINES = (PIPELINE_V2,)

# Phase 7 stage 4 (DEC-227): the optional ``generation_profile.video_resolution``,
# the per-story 1080p switch of the human's answer 3. Absent: the budget
# profile's ``video_resolution``, else 720p (``media_policy.video_resolution``).
VIDEO_RESOLUTION_DEFAULT = "720p"
VIDEO_RESOLUTIONS = (VIDEO_RESOLUTION_DEFAULT, "1080p")

# ------------------------------------------------------------- story (spec 2.1, 6.2)

EPISODE_TEMPLATE_ID = "serial_60s_v1"
# The template a v2 story is created on (phase 7 stage 4, DEC-227): 6-10 beat
# shots of 5-12 s, one clip each. A legacy story keeps EPISODE_TEMPLATE_ID.
EPISODE_TEMPLATE_ID_V2 = "serial_60s_v2"
# The episode templates shipped in templates/episodes/ (spec 6.2), in the
# order story_bible_v1.episode_template_id's enum lists them.
EPISODE_TEMPLATE_IDS = ("serial_60s_v1", "serial_90s_v1", EPISODE_TEMPLATE_ID_V2)

# In order, each derived from a contiguous prefix of ``approvals``
# (store.derive_status): concept, bible, style, then -- phase 2 -- cast,
# places and season (the last one makes the story ``ready``).
STATUSES = (
    "draft",
    "concept_chosen",
    "bible_approved",
    "style_approved",
    "cast_approved",
    "places_approved",
    "ready",
)


def default_generation_profile() -> dict:
    """A fresh ``generation_profile`` holding the story defaults."""
    return {
        "tier": DEFAULT_TIER,
        "route": DEFAULT_ROUTE,
        "consistency_mode": DEFAULT_CONSISTENCY_MODE,
        "budget_profile": DEFAULT_BUDGET_PROFILE,
    }


def episode_template_for(profile) -> str:
    """The episode template a story created with *profile* starts on: the v2
    one for a v2 pipeline (DEC-227), else :data:`EPISODE_TEMPLATE_ID`."""
    if (profile or {}).get("pipeline") == PIPELINE_V2:
        return EPISODE_TEMPLATE_ID_V2
    return EPISODE_TEMPLATE_ID


def quality_generation_profile() -> dict:
    """The profile a new story gets when the quality keys are set (phase 7,
    the human's answer: "tier 2 + the quality preset when the keys are
    present"): the v2 pipeline on the Quality (billed APIs) budget profile,
    hosted links, reference images. ``StoryStore.create``'s own default
    stays :func:`default_generation_profile`."""
    return {
        "tier": 2,
        "route": "api",
        "consistency_mode": "references",
        "budget_profile": "quality",
        "pipeline": PIPELINE_V2,
    }
