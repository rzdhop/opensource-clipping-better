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

What the Quality (billed APIs) preset costs (:func:`preset_estimate`, phase 7
stage 7) is computed here too, from the price table, the quality profile and
the v2 episode template, for the weak-host advice and the new-story form.

Stdlib only (DEC-012).
"""

from __future__ import annotations

from clipping.providers import budget as budget_mod
from clipping.providers import gating
from clipping.providers import generation as gen
from clipping.providers import pricing
from clipping.providers import video as video_providers
from clipping.providers.registry import ChainError, describe

from . import defaults, templates, video_plan

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

# ``tier3_native_audio``'s value for a clip's sound kept as ambience under the
# dialogue (stage E; ``budget.TIER3_AUDIO_MODES``).
AMBIENCE = "ambience"


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


def fully_animated(story) -> bool:
    """Whether every shot of *story* must be a video clip (the human, 2026-10-02:
    "only fully animated episodes, no diaporama"): a v2 story at tier >= 2
    whose budget profile animates all shots (``animate: all_shots``, the
    quality preset). Such a story never shows a shot as a still with motion:
    its assets approval and its render refuse while a shot the user did not
    pin ``keep_still`` lacks a current clip. A profile that cannot be read
    counts as one that does not animate every shot."""
    if not is_v2(story):
        return False
    profile = story.get("generation_profile") or {}
    if int(profile.get("tier") or 1) < 2:
        return False
    try:
        settings = budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return settings.get("animate") == "all_shots"


def ambience(story) -> bool:
    """Whether *story*'s clips bring their own ambience (phase 7 follow-up,
    stage E; the human's choice of 2026-10-02: the video model's sound is
    AMBIENCE + SFX ONLY, never dialogue): a v2 story at tier 3 whose budget
    profile's ``tier3_native_audio`` is ``ambience`` (the quality preset).
    Such a story buys every clip on a link that makes sound
    (``video_link_policy: first_with_audio``), asks each clip for the
    place's ambience and the shot's sound effects with no voice, and renders
    each clip's sound under its shot's lines, ducked -- every line still in
    its pinned TTS voice; the per-shot ``keep_native_audio`` opt-in is not
    read. Tier 1 and 2 never keep a clip's sound; a legacy story at tier 3
    keeps the opt-in (DEC-201). A profile that cannot be read counts as one
    that says nothing."""
    if not is_v2(story):
        return False
    profile = story.get("generation_profile") or {}
    if int(profile.get("tier") or 1) != 3:
        return False
    try:
        settings = budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return settings.get("tier3_native_audio") == AMBIENCE


def keyframe_fix(story):
    """``{"max_redraws_per_shot", "cap_usd"}`` -- how a v2 story's assets
    step redraws the keyframes the keyframe check (J2) flagged (phase 8
    stage B: its budget profile's ``keyframe_fix``, the quality preset's
    "up to 2 redraws a shot, at most $0.40 an episode") -- or None: a
    legacy story, a profile without the key (free, one_dollar: never on its
    own), or one that cannot be read."""
    if not is_v2(story):
        return None
    profile = story.get("generation_profile") or {}
    try:
        settings = budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    fix = settings.get("keyframe_fix")
    if not isinstance(fix, dict):
        return None
    return {"max_redraws_per_shot": int(fix["max_redraws_per_shot"]), "cap_usd": float(fix["cap_usd"])}


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


def new_story_offer(settings_env) -> dict:
    """What the new-story form starts from (``GET /api/stories/new-profile``)::

        {"profile", "quality": bool, "missing_keys": [name, ...], "sound_missing_keys": [name, ...],
         "allow_paid": bool, "estimate": <preset_estimate>}

    ``sound_missing_keys`` (stage E): the keys the preset's clips still need
    for their own sound (GEMINI_PAID_API_KEY for Veo) -- the preset runs
    without them, its clips silent, said in ``estimate.summary``.

    ``profile`` is the one a story created now without a profile gets
    (:func:`new_story_profile`, else the store defaults); ``quality`` whether
    the quality preset -- v2, every shot animated -- is that default,
    ``missing_keys`` the :data:`QUALITY_KEYS` still unset, ``allow_paid``
    whether paid calls may run at all (without it no clip is ever bought),
    ``estimate`` what the preset costs (:func:`preset_estimate`, phase 7
    stage 7: the numbers the weak-host advice says too), whether or not it
    is the default yet. Never a key's value. Calls nothing."""
    merged = gating.merged_env(settings_env)
    try:
        allow_paid = bool(gating.budget_of(merged).allow_paid)
    except ValueError:
        allow_paid = False
    profile = new_story_profile(settings_env)
    estimate = preset_estimate(merged)
    return {
        "profile": profile if profile is not None else defaults.default_generation_profile(),
        "quality": profile is not None,
        "missing_keys": [name for name in QUALITY_KEYS if not (merged.get(name) or "").strip()],
        "sound_missing_keys": [row["key"] for row in estimate["keys_needed"]
                               if SOUND_LINK in row["for"] and not row["set"]],
        "allow_paid": allow_paid,
        "estimate": estimate,
    }


# ------------------------------------------------------ the preset's price

# What a story's one-off images are counted on (DEC-235's count, the plan's
# acceptance walk): a cast of three, two places with one master plate each,
# three props. A story's own numbers come from its cast and places estimates;
# this is the preset's price before any story exists.
PRESET_STORY_CHARACTERS = 3
PRESET_STORY_PLACES = 2
PRESET_STORY_PROPS = 3
# A character's sheets: the portrait (text to image), then the turnaround and
# the expressions sheet, each an edit of it (refimages.CHARACTER_IMAGES).
_SHEET_EDITS = 2


# The keys the preset's clips need for their own sound (stage E): the link
# that makes it, Veo 3.1 lite, is billed on its own key (RC-V4).
SOUND_LINK = "gemini/veo-3.1-lite"


def _preset_video_link(merged, *, sound=False):
    """The link the quality profile's ``video_link_policy`` buys clips on:
    the first hosted link of *merged*'s VIDEO_CHAIN with an adapter's table
    of clip lengths and a price per second -- as ``steps/clips.hosted_rows``
    qualifies one, keys aside (the advice is what to add). With *sound*
    (``first_with_audio`` on an ambience preset, stage E): the first such
    link whose clips always carry sound and whose keys *merged* holds, else
    the first as above (the estimate then says the clips are silent). A
    chain that cannot be read or holds no such link: the shipped default
    chain's."""
    def first(chain, *, sounding=False):
        for link in chain:
            label = describe(link)
            if (link.provider == "local" or label in video_providers.REFUSED_LINKS
                    or label not in video_providers.CLIP_LENGTHS):
                continue
            if sounding and (video_providers.AUDIO.get(label) != "always" or gen.missing_keys(link, merged or {})):
                continue
            try:
                price = pricing.price_for(link)
            except pricing.PriceUnknown:
                continue
            if price.unit == "second":
                return link
        return None

    try:
        chain = gen.chain_from_env(gen.VIDEO, merged or {})
    except ChainError:
        chain = []
    found = first(chain, sounding=True) if sound else None
    return found or first(chain) or first(gen.parse_generation_chain(gen.VIDEO, gen.DEFAULT_CHAINS[gen.VIDEO]))


def _keys_needed(merged, sound_link) -> list:
    """What the preset needs, each key with what it is for and whether
    *merged* holds it -- never its value."""
    rows = [(name, "the images (sheets, plates, props, keyframes) and silent clips") for name in QUALITY_KEYS]
    for name in gen.env_keys_for(gen.parse_generation_chain(gen.VIDEO, sound_link)[0]):
        rows.append((name, f"clips with their own sound ({sound_link})"))
    return [{"key": name, "for": what, "set": bool((((merged or {}).get(name)) or "").strip())}
            for name, what in rows]


def _usd(amount) -> str:
    return f"${amount:.2f}"


def preset_estimate(merged=None) -> dict:
    """What the Quality (billed APIs) preset costs (phase 7 stage 7, A18),
    from the price table (``pricing.py``), the quality budget profile and the
    v2 episode template alone, so it can never drift from them::

        {"profile", "label", "episode_usd", "story_usd", "keys": [...],
         "keys_needed": [{"key", "for", "set"}], "ambience": bool,
         "summary": "≈ $X an episode (N shots animated) plus ≈ $Y once per story ...",
         "assumptions": sentence,
         "episode": {"shots", "seconds", "billed_seconds", "video_link", "resolution",
                     "price_per_second", "video_usd", "keyframe_link", "keyframe_usd", "keyframes_usd"},
         "story": {"characters", "places", "props", "images", "sheet_links", "plate_link",
                   "prop_link", "sheets_usd", "plates_usd", "props_usd"},
         "prices_as_of"}

    An episode (episode 1: the hook, the template's default body scenes and
    the cliffhanger, one shot each as T1 v2 plans them) covers the
    template's target length in equal shots; each is a clip on the profile's
    video link (:func:`_preset_video_link`, *merged*'s VIDEO_CHAIN) at the
    profile's size, rounded up to a length the link sells exactly as the
    planner rounds it (``video_plan.requested_seconds``, DEC-208), plus one
    keyframe on the keyframe role's first link. Once per story: each
    character's portrait on the sheet role's text-to-image link and its two
    sheets on the edit link, a master plate per place, an image per prop
    (:data:`PRESET_STORY_CHARACTERS` & co.). Calls nothing; never reads a
    key's value.

    Phase 7 follow-up, stage E: the preset is an ambience story (tier 3,
    ``video_link_policy: first_with_audio``), so its clips are priced on
    Veo 3.1 lite (:data:`SOUND_LINK`) once *merged* holds its key, with
    their own ambience (``ambience`` true, said in the summary); without
    it, on the first link as above, the summary saying there is no
    ambience and which key brings it. ``keys_needed`` names each key the
    preset reads, what it is for and whether it is set (``keys`` stays the
    keys a new story needs for the preset to be its default)."""
    profile = defaults.quality_generation_profile()
    settings = budget_mod.profile_settings(profile["budget_profile"])
    story_doc = {"generation_profile": profile}
    template = templates.load_episode_template(defaults.EPISODE_TEMPLATE_ID_V2)

    def first_link(role, kind):
        return role_chain(role, kind, merged or {}, story_doc)[0]

    def price(link, resolution=None):
        return float(pricing.price_for(link, resolution).usd)

    # --- an episode
    slots = template["slots"]
    shots = sum(slot["count"][0] for name, slot in slots.items() if name not in ("recap", "body"))
    shots += template["default_body_count"]
    seconds = template["target_s"]
    # Stage E: an ambience preset prices its clips where they make sound once that key is set.
    ambient = ambience(story_doc) and settings.get("video_link_policy") == "first_with_audio"
    video_link = _preset_video_link(merged, sound=ambient)
    sound_on = ambient and video_providers.AUDIO.get(describe(video_link), "never") != "never"
    resolution = settings.get("video_resolution") or pricing.DEFAULT_RESOLUTION
    per_second = price(video_link, resolution)
    billed = shots * video_plan.requested_seconds(describe(video_link), seconds / shots)
    keyframe_link = first_link("keyframe", gen.IMAGE_EDIT)
    keyframe_usd = price(keyframe_link)
    video_usd = billed * per_second
    keyframes_usd = shots * keyframe_usd
    episode_usd = video_usd + keyframes_usd

    # --- once per story
    portrait_link, sheet_edit_link = first_link("sheet", gen.IMAGE), first_link("sheet", gen.IMAGE_EDIT)
    plate_link, prop_link = first_link("plate", gen.IMAGE), first_link("prop", gen.IMAGE)
    sheets_usd = PRESET_STORY_CHARACTERS * (price(portrait_link) + _SHEET_EDITS * price(sheet_edit_link))
    plates_usd = PRESET_STORY_PLACES * price(plate_link)
    props_usd = PRESET_STORY_PROPS * price(prop_link)
    story_usd = sheets_usd + plates_usd + props_usd
    images = PRESET_STORY_CHARACTERS * (1 + _SHEET_EDITS) + PRESET_STORY_PLACES + PRESET_STORY_PROPS

    label = settings.get("label") or profile["budget_profile"]
    video_label = describe(video_link)
    keys_needed = _keys_needed(merged, SOUND_LINK)
    summary = (f"≈ {_usd(episode_usd)} an episode ({shots} shots animated) plus ≈ {_usd(story_usd)} once per "
               "story for sheets, plates and props")
    if sound_on:
        summary += "; each clip brings its own ambience and sound effects"
    elif ambient:
        missing = [row["key"] for row in keys_needed if not row["set"] and SOUND_LINK in row["for"]]
        summary += (f"; no ambience: add {' and '.join(missing)} for Veo's sound" if missing
                    else f"; no ambience: {video_label} makes clips with no sound")
    assumptions = (
        f"An episode: {shots} shots over the v2 template's {seconds:g} s target, each a {video_label} clip at "
        f"{resolution} rounded up to whole seconds ({billed} s billed at ${per_second:g} a second = "
        f"{_usd(video_usd)}) and a keyframe on {describe(keyframe_link)} (${keyframe_usd:g} each = "
        f"{_usd(keyframes_usd)}). Once per story: {PRESET_STORY_CHARACTERS} characters × {1 + _SHEET_EDITS} "
        f"sheets, {PRESET_STORY_PLACES} plates and {PRESET_STORY_PROPS} props ({images} images on "
        f"{' and '.join(dict.fromkeys(map(describe, (portrait_link, sheet_edit_link, plate_link, prop_link))))}"
        f" = {_usd(story_usd)}). Prices from the table of "
        f"{pricing.PRICES_AS_OF}. Writing is not counted: the story's writing chain tries its free links first.")
    if ambient:
        sound_keys = " and ".join(row["key"] for row in keys_needed if SOUND_LINK in row["for"])
        assumptions += (f" Keys: {' and '.join(QUALITY_KEYS)} for the images; {sound_keys} for clips with their own "
                        f"sound ({SOUND_LINK}: ambience and sound effects under the dialogue, never a voice).")
    return {
        "profile": profile["budget_profile"],
        "label": label,
        "episode_usd": round(episode_usd, 4),
        "story_usd": round(story_usd, 4),
        "keys": list(QUALITY_KEYS),
        "keys_needed": keys_needed,
        "ambience": bool(sound_on),
        "summary": summary,
        "assumptions": assumptions,
        "episode": {
            "shots": shots, "seconds": seconds, "billed_seconds": billed, "video_link": video_label,
            "resolution": resolution, "price_per_second": per_second, "video_usd": round(video_usd, 4),
            "keyframe_link": describe(keyframe_link), "keyframe_usd": keyframe_usd,
            "keyframes_usd": round(keyframes_usd, 4),
        },
        "story": {
            "characters": PRESET_STORY_CHARACTERS, "places": PRESET_STORY_PLACES, "props": PRESET_STORY_PROPS,
            "images": images, "sheet_links": [describe(portrait_link), describe(sheet_edit_link)],
            "plate_link": describe(plate_link), "prop_link": describe(prop_link),
            "sheets_usd": round(sheets_usd, 4), "plates_usd": round(plates_usd, 4),
            "props_usd": round(props_usd, 4),
        },
        "prices_as_of": pricing.PRICES_AS_OF,
    }


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
