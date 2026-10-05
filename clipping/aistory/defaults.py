"""The story defaults and closed lists of a StoryBible (spec 2.1, 8, 8.1, 8.5).

One place for the values a new story starts with, so the API request model,
the CLI and the dashboard form can each be tested against the same constants.

There is deliberately **no default language**: a story must name ``fr`` or
``en``. A silent default would be exactly the "silent fallback" spec 0
forbids -- a French user who forgot a field would get an English season.

Constants only, no imports of the package: ``schemas.py`` builds the
``story_bible_v1`` enums from these tuples, so this module must not import it
back. (The one file it reads is ``templates/universes.json``, with the
standard library, for the ``UNIVERSES`` ids below.)
"""

from __future__ import annotations

import json
from pathlib import Path

# ------------------------------------------------------ generation profile (spec 8)

DEFAULT_TIER = 1
DEFAULT_ROUTE = "auto"
DEFAULT_CONSISTENCY_MODE = "references"
DEFAULT_BUDGET_PROFILE = "free"

TIERS = (1, 2, 3)
ROUTES = ("auto", "local", "api")
CONSISTENCY_MODES = ("references", "prompt_only")
BUDGET_PROFILES = ("free", "one_dollar", "quality", "native_speech", "native_speech_manual")
# Plan 22: the native-speech budget profile (each character line spoken by
# its own clip, ``media_policy.native_speech``).
NATIVE_SPEECH_PROFILE = "native_speech"
# Plan 22 stage 5: the same, every clip the human's own (``manual/upload``):
# the app writes the shot brief, the human makes the clips on their own
# subscription and uploads them. The profile a new story starts on when the
# quality keys are set (``media_policy.new_story_profile``).
NATIVE_SPEECH_MANUAL_PROFILE = "native_speech_manual"
NATIVE_SPEECH_PROFILES = (NATIVE_SPEECH_PROFILE, NATIVE_SPEECH_MANUAL_PROFILE)

# Plan 22 stage 5: the optional ``generation_profile.images``, the per-story
# switch that makes the sheets, plates, props and keyframes the human's own
# uploads too (``manual``). Absent: the budget profile's image links.
IMAGES_MANUAL = "manual"
IMAGE_MODES = (IMAGES_MANUAL,)

# Plan 23 stage A9: the optional ``generation_profile.image_preference``, which
# provider a v2 story's sheets, plates, props and keyframes try first. Absent:
# the budget profile's order (fal first). "gemini_first" re-sorts every role's
# chain so the ``gemini/*`` links come first, no link removed
# (``media_policy.role_chain``); ignored on a legacy or manual-images story.
IMAGE_GEMINI_FIRST = "gemini_first"
IMAGE_PREFERENCES = (IMAGE_GEMINI_FIRST,)

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

# Plan 22: the optional ``generation_profile.speech_model``, the per-story
# switch of a native-speech story's speaking clips -- a key of its budget
# profile's ``speech_links`` (``lite``, ``fast``, ``premium``). Absent: the
# profile's ``speech_model`` (``media_policy.speech_link``). The values are
# ``budget.SPEECH_MODELS``.
SPEECH_MODELS = ("lite", "fast", "premium")

# Plan 21 stage 1 (agent mode): the optional ``generation_profile.mode``.
# Absent is Studio -- every step waits for the human's approval, as always;
# ``agent`` lets one ``story-fast-track`` job take the story from its seed
# to episode 1, approving each document by rule. Never in the fresh profile
# below: a story is in agent mode only when it is created (or patched) so.
MODE_STUDIO = "studio"
MODE_AGENT = "agent"
STORY_MODES = (MODE_STUDIO, MODE_AGENT)

# Plan 22 stage 2 (DEC-274): the optional ``generation_profile.writing``.
# Absent or "v2" keeps every prompt exactly as it was (C1/B1); "v3" gates the
# brief-faithful concept and bible prompts (C1v2/C1J, B1v3) -- never without
# a non-empty ``seed_text`` too (``steps/concepts.py``, ``steps/bible.py``).
# Never in ``default_generation_profile`` below: ``store.create`` stamps
# "v3" on every story made from now on; a story read from before this stage
# has no key at all and reads as "v2" (RC-W2: byte-identical without it).
WRITING_V2 = "v2"
WRITING_V3 = "v3"
WRITING_VERSIONS = (WRITING_V2, WRITING_V3)

# Plan 23 stage D4: the optional ``generation_profile.sheet_mode``, how a
# character's reference sheets are drawn. Absent is "three_sheet", today's
# portrait + turnaround + expressions; "two_view" draws ONE 9:16 sheet per
# character (the front and the back side by side) into the ``refs.portrait``
# slot, the turnaround and the expressions left out; "two_view_expressions"
# adds the expressions sheet, an edit of it (``media_policy.sheet_mode``).
SHEET_THREE = "three_sheet"
SHEET_TWO_VIEW = "two_view"
SHEET_TWO_VIEW_EXPRESSIONS = "two_view_expressions"
SHEET_MODES = (SHEET_THREE, SHEET_TWO_VIEW, SHEET_TWO_VIEW_EXPRESSIONS)

# Plan 23 stage D4: the optional ``generation_profile.body_rule``, how a
# story's characters' bodies are drawn. Absent is the style's own
# ``default_body_rule`` (``human_body`` unless the template says otherwise:
# today's rules); "all_matter" replaces the style lock's
# ``character_design_rules`` with the template's ``body_rules.all_matter``
# when the style is locked (``media_policy.body_rule``).
BODY_HUMAN = "human_body"
BODY_ALL_MATTER = "all_matter"
BODY_RULES = (BODY_HUMAN, BODY_ALL_MATTER)


def _universe_ids() -> tuple:
    path = Path(__file__).resolve().parent / "templates" / "universes.json"
    with open(path, encoding="utf-8") as fh:
        return tuple(entry["id"] for entry in json.load(fh)["universes"])


# Plan 23 stage D2: the optional ``generation_profile.universe``, what the
# story's cast is made of (a fruit, a drink can, a gadget...), one of the ids
# of ``templates/universes.json``. Absent is the style's own
# ``default_universe``, else none (``media_policy.universe``); a style
# accepts only the universes its template lists (``store.create``).
UNIVERSES = _universe_ids()

# Plan 23 stage D6: the optional ``generation_profile.prompt_style``, how a
# clip's prompt is written. Absent is "studio", today's prompts byte for byte
# (layered context, the style's motion suffix); "action" writes one
# continuous physical action in the present tense, with a colour/species
# anchor per character repeated at every mention, the place once, the sounds
# inline and one camera phrase (Flow / Seedance style;
# ``prompting.clip_prompt_action`` / ``speech_clip_prompt_action``, read
# through ``media_policy.prompt_style``). A clip's prompt hash is of that
# prompt, so a story changing style makes its current clips stale.
PROMPT_STUDIO = "studio"
PROMPT_ACTION = "action"
PROMPT_STYLES = (PROMPT_STUDIO, PROMPT_ACTION)

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
# Plan 22 stage 3: a ~50 s confrontation in one place, in real time -- one
# shot a character line on a native-speech story (line_words 5-17, every
# boundary a cut, the narrator in the recap only). The format a new story on
# a native-speech profile starts on when it names none (episode_template_for).
EPISODE_TEMPLATE_ID_CONFRONTATION = "confrontation_50s_v2"
EPISODE_TEMPLATE_IDS = ("serial_60s_v1", "serial_90s_v1", EPISODE_TEMPLATE_ID_V2,
                        EPISODE_TEMPLATE_ID_90_V2, EPISODE_TEMPLATE_ID_NARRATED,
                        EPISODE_TEMPLATE_ID_CONFRONTATION)

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


def speaks_natively(profile) -> bool:
    """Whether *profile* (a ``generation_profile``) is a native-speech one --
    the v2 pipeline at tier 3 on :data:`NATIVE_SPEECH_PROFILES` -- read from
    the profile alone (``media_policy.native_speech`` reads the budget
    profile's settings too; both native profiles say ``speech``)."""
    profile = profile or {}
    return (profile.get("pipeline") == PIPELINE_V2 and int(profile.get("tier") or 1) == 3
            and profile.get("budget_profile") in NATIVE_SPEECH_PROFILES)


def episode_template_for(profile, chosen=None) -> str:
    """The episode template a story created with *profile* starts on: the
    story's own *chosen* one when it names a shipped template (plan 20
    stage 1: the new-story form sends it, pre-filled from the style's
    suggestion), else -- plan 22 stage 3 -- the confrontation format on a
    native-speech profile (:func:`speaks_natively`: one shot a character
    line, which a 60 s beat-shot format's long lines do not fit; DEC-268: a
    suggestion, the story may name another), else the v2 one for a v2
    pipeline (DEC-227), else :data:`EPISODE_TEMPLATE_ID`."""
    if chosen in EPISODE_TEMPLATE_IDS:
        return chosen
    if speaks_natively(profile):
        return EPISODE_TEMPLATE_ID_CONFRONTATION
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


def manual_speech_generation_profile() -> dict:
    """The profile a new story gets when the quality keys are set (plan 22
    stage 5: the manual mode is the default): the quality preset's (v2, tier
    3, hosted image links, references) on the ``native_speech_manual``
    budget profile -- every character line spoken by its own clip, every
    clip the human's own upload, the keyframes made by the app."""
    return dict(quality_generation_profile(), budget_profile=NATIVE_SPEECH_MANUAL_PROFILE)
