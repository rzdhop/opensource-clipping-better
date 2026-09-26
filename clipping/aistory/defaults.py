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

# ------------------------------------------------------------- story (spec 2.1, 6.2)

EPISODE_TEMPLATE_ID = "serial_60s_v1"

# In order. Phase 1 derives the first four from ``approvals``; the last three
# arrive with the cast, places and season steps.
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
