"""Which image links a story's images may run on (AI Story phase 7, DEC-221).

A story created from phase 7 on carries ``generation_profile.pipeline: "v2"``
(:func:`is_v2`). Its images are made per **role** -- ``sheet`` (a character's
portrait, turnaround and expressions), ``plate`` (a place's master plate and
time variants), ``prop`` and ``keyframe`` (a shot's image) -- on the links its
budget profile's ``roles`` table names (``templates/budget_profiles.json``,
images policy ``quality_roles``), never on a draft-quality link
(:data:`LOW_QUALITY_LINKS`). A story without the key is a legacy story: its
chains are exactly the env chains they always were (``IMAGE_CHAIN``,
``IMAGE_EDIT_CHAIN``, or their shipped defaults), byte for byte.

The style preview is not a role: it keeps the free chain and is labelled a
draft. Every site that picks a chain for one of these images -- the estimate
and the run alike -- asks :func:`role_chain`, so the two always agree
(RC-V6), and the episode's sticky image link (DEC-204, ``steps/sticky_link``)
is chosen from and pinned within the chain built here.

Stdlib only (DEC-012).
"""

from __future__ import annotations

from clipping.providers import budget as budget_mod
from clipping.providers import gating
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError, describe

from . import defaults

ROLES = budget_mod.PROFILE_ROLES  # ("sheet", "plate", "prop", "keyframe")

# Links whose pictures are drafts: never a sheet, plate, prop or keyframe of a
# v2 story, whatever a chain or a profile says (A3).
LOW_QUALITY_LINKS = frozenset({
    "cloudflare/flux-1-schnell",
    "pollinations/flux",
    "fal/flux-schnell",
    "openai/gpt-image-2-low",
})

# Links that only edit (need at least one reference image) and so can never
# answer a gen.IMAGE (text-to-image) request. A role's links may name one of
# these next to its text-to-image sibling (stage 2c, DEC-235: the quality
# sheet/plate/prop roles are ``[fal/seedream-4.5, fal/seedream-4.5-edit]``),
# and role_chain keeps only the one that serves the request's kind. A link in
# neither this set nor TEXT_ONLY_LINKS (e.g. a nano-banana link) serves
# gen.IMAGE and gen.IMAGE_EDIT alike.
EDIT_ONLY_LINKS = frozenset({
    "fal/seedream-4-edit",
    "fal/seedream-4.5-edit",
    "fal/flux-kontext-pro",
})

# Links whose model only takes a prompt and can never honour a gen.IMAGE_EDIT
# request's references, even when offered one -- the complement of
# EDIT_ONLY_LINKS a role_chain kind filter also needs. Only the quality
# roles' own text-to-image link is listed here: every low-quality link is
# already dropped by LOW_QUALITY_LINKS before this filter runs, so listing
# them again would be dead weight.
TEXT_ONLY_LINKS = frozenset({
    "fal/seedream-4.5",
})

# The links a role_chain of *kind* keeps: an edit-only link is never offered
# for gen.IMAGE, a text-only link is never offered for gen.IMAGE_EDIT; a link
# in neither set (e.g. a nano-banana link) serves both kinds.
_KIND_EXCLUDES = {gen.IMAGE: EDIT_ONLY_LINKS, gen.IMAGE_EDIT: TEXT_ONLY_LINKS}

# The keys a new story needs for the quality preset to be its default (the
# human's answer, 2026-10-01: "fal only" -- DEC-235 moved every quality
# image, sheet/plate/prop included, to fal; GEMINI_PAID_API_KEY stays usable
# for nano-banana (DEC-222) but is no longer required to pick the preset).
QUALITY_KEYS = ("FAL_KEY",)


def is_v2(story) -> bool:
    """Whether *story* (a ``story.json`` document) is on the v2 pipeline."""
    profile = (story or {}).get("generation_profile") or {}
    return profile.get("pipeline") == defaults.PIPELINE_V2


def _role_links(role, story):
    """The links the story's budget profile names for *role*, or None when
    its images policy is not ``quality_roles``. ``ChainError`` when the
    profile cannot be read."""
    name = story["generation_profile"]["budget_profile"]
    try:
        settings = budget_mod.profile_settings(name)
    except (OSError, ValueError, KeyError) as exc:
        raise ChainError(f"the {name} budget profile cannot be read ({exc})") from None
    if settings.get("images") != budget_mod.QUALITY_ROLES_POLICY:
        return None
    links = (settings.get("roles") or {}).get(role)
    if not links:
        raise ChainError(f"the {name} budget profile names no link for the {role} images")
    return list(links)


def role_chain(role, kind, merged, story) -> list:
    """The chain (``[Link, ...]``) an image of *role* is made on, for *kind*
    (``gen.IMAGE`` or ``gen.IMAGE_EDIT``) with the merged settings *merged*.

    A legacy story: ``gen.chain_from_env(kind, merged)``, unchanged. A v2
    story: the links of its budget profile's ``roles[role]`` (parsed as an
    env chain of *kind* is), or -- a profile with no ``quality_roles`` policy
    -- the env chain; either way with every :data:`LOW_QUALITY_LINKS` link,
    and every link that cannot serve *kind* (:data:`EDIT_ONLY_LINKS` for
    ``gen.IMAGE``, :data:`TEXT_ONLY_LINKS` for ``gen.IMAGE_EDIT``), left out
    -- a roles list may name a text-to-image link next to its edit sibling
    (stage 2c, DEC-235) so the role serves both kinds. ``ChainError`` when
    nothing is left or a link cannot be one of *kind*'s providers."""
    if not is_v2(story):
        return gen.chain_from_env(kind, merged)
    if role not in ROLES:
        raise ValueError(f"Unknown image role {role!r}. Known: {', '.join(ROLES)}.")
    links = _role_links(role, story)
    chain = gen.parse_generation_chain(kind, links) if links is not None else gen.chain_from_env(kind, merged)
    exclude = LOW_QUALITY_LINKS | _KIND_EXCLUDES[kind]
    chain = [link for link in chain if describe(link) not in exclude]
    if not chain:
        raise ChainError(f"no quality link is left for the {role} images (draft links, and links that cannot "
                         f"serve a {kind} request, are never used on a v2 story)")
    assert not {describe(link) for link in chain} & exclude
    return chain


def chain_name(role, kind, story) -> str:
    """How the chain :func:`role_chain` builds is named in a message: the env
    variable for a legacy story (today's words), the role for a v2 one."""
    if not is_v2(story) or role is None:
        return gen.ENV_NAMES[kind]
    return f"the quality {role} links ({story['generation_profile']['budget_profile']} budget profile)"


def video_resolution(story) -> str:
    """The size *story*'s clips are bought at (phase 7 stage 4, DEC-227):
    its ``generation_profile.video_resolution`` (the per-story 1080p switch),
    else its budget profile's ``video_resolution``, else 720p. A profile
    that cannot be read counts as one that says nothing."""
    profile = (story or {}).get("generation_profile") or {}
    chosen = profile.get("video_resolution")
    if chosen in defaults.VIDEO_RESOLUTIONS:
        return chosen
    try:
        settings = budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        settings = {}
    chosen = settings.get("video_resolution")
    return chosen if chosen in defaults.VIDEO_RESOLUTIONS else defaults.VIDEO_RESOLUTION_DEFAULT


def quality_keys_present(merged) -> bool:
    """Whether *merged* holds every :data:`QUALITY_KEYS` value."""
    return all((merged.get(name) or "").strip() for name in QUALITY_KEYS)


def new_story_profile(settings_env):
    """The ``generation_profile`` a story created without one gets: the
    quality preset (``defaults.quality_generation_profile``) when the
    Settings values *settings_env*, over the process environment, hold every
    :data:`QUALITY_KEYS` value (FAL_KEY alone, stage 2c, DEC-235); else None
    -- the store's own default."""
    if quality_keys_present(gating.merged_env(settings_env)):
        return defaults.quality_generation_profile()
    return None


# The source crop of a v2 keyframe (A6): an exact 9:16 whose multiple of 9x16
# is even, so the render's scale lands on 1080x1920 with square pixels and no
# near-9:16 still ever reaches the final concat (DEC-217's follow-up).
_UNIT_W, _UNIT_H = 9, 16


def keyframe_crop(size):
    """``(width, height)`` of the centre crop that makes a keyframe of *size*
    an exact, even 9:16, or None when it is one already or *size* is unknown
    (or too small to hold one)."""
    if not size:
        return None
    width, height = size
    k = min(width // _UNIT_W, height // _UNIT_H)
    k -= k % 2
    if not k:
        return None
    crop = (_UNIT_W * k, _UNIT_H * k)
    return None if crop == (width, height) else crop
