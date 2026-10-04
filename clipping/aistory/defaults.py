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

# DEC-258: the optional ``generation_profile.lipsync``, the per-story switch of
# the clips' lipsync -- ``none`` turns it off for that story, ``kling`` asks it
# whatever the budget profile says. Absent: the budget profile's ``lipsync``
# (``media_policy.lipsync``). The values are ``budget.LIPSYNC_MODES``.
LIPSYNC_MODES = ("none", "kling")

# Plan 21 stage 1 (agent mode): the optional ``generation_profile.mode``.
# Absent is Studio -- every step waits for the human's approval, as always;
# ``agent`` lets one ``story-fast-track`` job take the story from its seed
# to episode 1, approving each document by rule. Never in the fresh profile
# below: a story is in agent mode only when it is created (or patched) so.
MODE_STUDIO = "studio"
MODE_AGENT = "agent"
STORY_MODES = (MODE_STUDIO, MODE_AGENT)

# ------------------------------------------------------------- story (spec 2.1, 6.2)

EPISODE_TEMPLATE_ID = "serial_60s_v1"
# The template a v2 story is created on (phase 7 stage 4, DEC-227): 6-10 beat
# shots of 5-12 s, one clip each. A legacy story keeps EPISODE_TEMPLATE_ID.
EPISODE_TEMPLATE_ID_V2 = "serial_60s_v2"
# The episode templates shipped in templates/episodes/ (spec 6.2), in the
# order story_bible_v1.episode_template_id's enum lists them. Plan 20 stage 1
# (the fruit-drama pack): serial_90s_v2, the v2 shape at 80-100 s, and
# narrated_drama_60s_v2, the v2 beats told by one narrator (narrator_share,
# character_lines) -- both chosen per story, never a pipeline default.
EPISODE_TEMPLATE_ID_90_V2 = "serial_90s_v2"
EPISODE_TEMPLATE_ID_NARRATED = "narrated_drama_60s_v2"
EPISODE_TEMPLATE_IDS = ("serial_60s_v1", "serial_90s_v1", EPISODE_TEMPLATE_ID_V2,
                        EPISODE_TEMPLATE_ID_90_V2, EPISODE_TEMPLATE_ID_NARRATED)

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


def episode_template_for(profile, chosen=None) -> str:
    """The episode template a story created with *profile* starts on: the
    story's own *chosen* one when it names a shipped template (plan 20
    stage 1: the new-story form sends it, pre-filled from the style's
    suggestion), else the v2 one for a v2 pipeline (DEC-227), else
    :data:`EPISODE_TEMPLATE_ID`."""
    if chosen in EPISODE_TEMPLATE_IDS:
        return chosen
    if (profile or {}).get("pipeline") == PIPELINE_V2:
        return EPISODE_TEMPLATE_ID_V2
    return EPISODE_TEMPLATE_ID


def quality_generation_profile() -> dict:
    """The profile a new story gets when the quality keys are set (phase 7,
    the human's answer: "tier 2 + the quality preset when the keys are
    present"): the v2 pipeline on the Quality (billed APIs) budget profile,
    hosted links, reference images. Tier 3 since the phase 7 follow-up
    (stage E, the human's choice of 2026-10-02: every clip brings its own
    ambience and sound effects): the quality profile keeps a clip's sound
    as ambience under the dialogue, never in place of it
    (``media_policy.ambience``). ``StoryStore.create``'s own default stays
    :func:`default_generation_profile`."""
    return {
        "tier": 3,
        "route": "api",
        "consistency_mode": "references",
        "budget_profile": "quality",
        "pipeline": PIPELINE_V2,
    }
