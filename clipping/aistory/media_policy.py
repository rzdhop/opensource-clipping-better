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


def writing_v3(story) -> bool:
    """Whether *story* writes on the v3 prompts (plan 22 stages 2-3,
    ``generation_profile.writing == "v3"``; ``store.create`` stamps it on
    every new story): an episode's spine and cause-and-effect scene
    summaries (E1v3), complete spoken sentences that each move the story
    (E2v3/E3v3), judged for it (J1v3). Absent or "v2": every prompt as it
    was (RC-W2, RC-W3). The one gate: the concepts and bible steps read it
    with a non-empty ``seed_text`` (stage 2), the script step on a v2 story
    (stage 3)."""
    return ((story or {}).get("generation_profile") or {}).get("writing") == defaults.WRITING_V3


def images_manual(story) -> bool:
    """Whether *story*'s sheets, plates, props and keyframes are the human's
    own uploads (plan 22 stage 5: ``generation_profile.images: "manual"``):
    a v2 story whose every image role resolves to ``manual/upload``."""
    profile = (story or {}).get("generation_profile") or {}
    return is_v2(story) and profile.get("images") == defaults.IMAGES_MANUAL


def sheet_mode(story) -> str:
    """How *story*'s characters' reference sheets are drawn (plan 23 stage
    D4, ``generation_profile.sheet_mode``): ``three_sheet`` (absent, and
    every story not on the v2 pipeline: a portrait, a turnaround and an
    expressions sheet), ``two_view`` (one 9:16 sheet showing the front and
    the back, in the ``refs.portrait`` slot) or ``two_view_expressions``
    (that sheet and an expressions sheet, an edit of it)."""
    chosen = ((story or {}).get("generation_profile") or {}).get("sheet_mode")
    if is_v2(story) and chosen in defaults.SHEET_MODES:
        return chosen
    return defaults.SHEET_THREE


def two_view(story) -> bool:
    """Whether *story*'s identity image (``refs.portrait``) is a front+back
    sheet (:func:`sheet_mode` is one of the two-view modes)."""
    return sheet_mode(story) != defaults.SHEET_THREE


def sheet_edits(story_or_mode) -> int:
    """How many of a character's sheets are edits of its portrait in a sheet
    mode (a story's, or the mode's name): two in ``three_sheet`` (turnaround,
    expressions), none in ``two_view``, one in ``two_view_expressions``."""
    mode = story_or_mode if isinstance(story_or_mode, str) else sheet_mode(story_or_mode)
    return SHEET_EDITS.get(mode, SHEET_EDITS[defaults.SHEET_THREE])


def body_rule(story, style_id=None) -> str:
    """How *story*'s characters' bodies are drawn (plan 23 stage D4,
    ``generation_profile.body_rule``): its own choice, else the default of
    the style *style_id* (else the story's ``style_template_id``) -- the
    template's ``default_body_rule`` -- else ``human_body``, the rules every
    style had before. Read when the style is locked
    (``stylelock.lock_style``); a locked style is never changed after."""
    chosen = ((story or {}).get("generation_profile") or {}).get("body_rule")
    if chosen in defaults.BODY_RULES:
        return chosen
    style_id = style_id or (story or {}).get("style_template_id")
    if style_id:
        try:
            fallback = templates.load_style(style_id).get("default_body_rule")
        except KeyError:
            fallback = None
        if fallback in defaults.BODY_RULES:
            return fallback
    return defaults.BODY_HUMAN


def universe(story, style_id=None, *, explicit=False):
    """What *story*'s cast is made of (plan 23 stage D2,
    ``generation_profile.universe``): the id of a ``templates/universes.json``
    entry -- its own choice, else the default of the style *style_id* (else
    the story's ``style_template_id``), the template's ``default_universe``
    -- else None: no universe.

    With *explicit* only the story's own choice counts. Everything that
    changes what is written or frozen -- the concepts' species block, the
    brand check, the universe the style lock records -- reads it that way, so
    a story that never chose a universe (every story made before this stage,
    Fruit Drama's included) is written and locked byte for byte as it always
    was (RC-W2); the style's default is what the new-story form pre-selects
    and the profile card shows."""
    chosen = ((story or {}).get("generation_profile") or {}).get("universe")
    if chosen in defaults.UNIVERSES:
        return chosen
    if explicit:
        return None
    style_id = style_id or (story or {}).get("style_template_id")
    if style_id:
        try:
            fallback = templates.load_style(style_id).get("default_universe")
        except KeyError:
            fallback = None
        if fallback in defaults.UNIVERSES:
            return fallback
    return None


def is_manual_link(label) -> bool:
    """Whether the link *label* is the human's own upload (``manual/upload``)."""
    return isinstance(label, str) and gen.is_manual(label)


def clips_manual(story) -> bool:
    """Whether every clip of *story* is the human's own upload (plan 22 stage
    5): a native-speech story whose speech link and silent link are both
    ``manual/upload``."""
    if not native_speech(story):
        return False
    return is_manual_link(speech_link(story)) and is_manual_link(silent_link(story))


def _role_links(role, story):
    """The links the story's budget profile names for *role*, or None when
    its images policy is not ``quality_roles``. ``ChainError`` when the
    profile cannot be read. Plan 22 stage 5: a story whose images are manual
    (:func:`images_manual`) names ``manual/upload`` for every role."""
    if images_manual(story):
        return [gen.MANUAL_LINK]
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


# DEC-258: the budget profiles' ``lipsync`` (and the story's own switch).
LIPSYNC_KLING = "kling"
assert defaults.LIPSYNC_MODES == budget_mod.LIPSYNC_MODES
# The longest clip Kling LipSync takes (fal: 2-10 s of input video): a
# lipsyncing story buys no longer clip, so its storyboard plans a scene past
# it as two shots (DEC-250's stretch still covers a shot up to 12.5 s).
LIPSYNC_MAX_CLIP_S = 10


def lipsync(story) -> bool:
    """Whether *story*'s clips get their characters' lips moved to the
    dialogue once bought (DEC-258; the human: "decide for me", option B): a
    fully animated story (:func:`fully_animated`: v2, tier >= 2, every shot
    a clip) whose ``generation_profile.lipsync`` says ``kling`` -- or, absent,
    whose budget profile's ``lipsync`` does (the quality preset). ``none`` on
    the story turns it off whatever the profile says. A profile that cannot
    be read counts as one that says nothing."""
    if not fully_animated(story) or native_speech(story):
        # Plan 22: a native-speech story's clips speak their own lines -- the
        # face and the voice are one performance, nothing to sync after.
        return False
    profile = story.get("generation_profile") or {}
    chosen = profile.get("lipsync")
    if chosen in defaults.LIPSYNC_MODES:
        return chosen == LIPSYNC_KLING
    try:
        settings = budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return settings.get("lipsync") == LIPSYNC_KLING


# ------------------------------------------------------------ native speech

# Plan 22: ``tier3_native_audio``'s value of the native-speech profile.
SPEECH = budget_mod.SPEECH_AUDIO_MODE
assert defaults.SPEECH_MODELS == budget_mod.SPEECH_MODELS


def _profile_of(story) -> dict:
    """*story*'s budget profile's settings, or {} when it cannot be read."""
    profile = (story or {}).get("generation_profile") or {}
    try:
        return budget_mod.profile_settings(profile.get("budget_profile"))
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def native_speech(story) -> bool:
    """Whether *story*'s character lines are spoken by their clips (plan 22,
    the ``native_speech`` profile): a v2 story at tier 3 whose budget
    profile's ``tier3_native_audio`` is ``speech``. Such a story plans one
    shot a character line (``shots.speech_shot_plan``), buys a speaking
    shot's clip on its speech link and a silent one on its silent link,
    voices only the narrator (TTS), never lipsyncs, takes each speaking
    clip's own sound as its line (the native take) and renders it in place
    of the line. A profile that cannot be read counts as one that says
    nothing."""
    if not is_v2(story):
        return False
    if int(((story or {}).get("generation_profile") or {}).get("tier") or 1) != 3:
        return False
    return _profile_of(story).get("tier3_native_audio") == SPEECH


def speech_model(story) -> str:
    """The speaking clips' model of *story* (plan 22): its own
    ``generation_profile.speech_model`` (the per-story switch), else its
    budget profile's ``speech_model``, else ``fast``."""
    chosen = ((story or {}).get("generation_profile") or {}).get("speech_model")
    if chosen in defaults.SPEECH_MODELS:
        return chosen
    chosen = _profile_of(story).get("speech_model")
    return chosen if chosen in defaults.SPEECH_MODELS else "fast"


def speech_link(story, model=None):
    """The link label *story*'s speaking clips are bought on (plan 22): its
    budget profile's ``speech_links[model or speech_model(story)]``, or None.
    Resolved by name: whether that provider exists is the clip plan's
    question, never this one's (a link may be named before its adapter is)."""
    links = _profile_of(story).get("speech_links") or {}
    link = links.get(model or speech_model(story))
    return link if isinstance(link, str) and link else None


def silent_link(story):
    """The link label *story*'s silent clips (reaction and narrator shots)
    are bought on: its budget profile's ``silent_link``, or None. By name,
    as :func:`speech_link`."""
    link = _profile_of(story).get("silent_link")
    return link if isinstance(link, str) and link else None


def speech_retake(story):
    """``{"max_per_shot", "cap_usd"}``: how a native-speech story retakes a
    speaking clip whose take missed its line (its budget profile's
    ``speech_retake``), or None."""
    if not native_speech(story):
        return None
    retake = _profile_of(story).get("speech_retake")
    if not isinstance(retake, dict):
        return None
    return {"max_per_shot": int(retake["max_per_shot"]), "cap_usd": float(retake["cap_usd"])}


def stt_missing_keys(merged) -> list:
    """The keys the native take's transcriber needs when none of STT_CHAIN's
    hosted links has one (``assets.default_transcriber``'s rule), else []."""
    from clipping.providers import stt
    from clipping.providers.registry import PROVIDERS

    merged = merged or {}
    try:
        chain = stt.parse_stt_chain(merged.get("STT_CHAIN") or stt.DEFAULT_STT_CHAIN)
    except ChainError:
        chain = []
    names = [PROVIDERS[link.provider].env_key for link in chain
             if link.provider != "local" and link.provider in PROVIDERS]
    if any((merged.get(name) or "").strip() for name in names):
        return []
    return list(dict.fromkeys(names))


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
    """The ``generation_profile`` a story created without one gets: when the
    Settings values *settings_env*, over the process environment, hold every
    :data:`QUALITY_KEYS` value (FAL_KEY alone, stage 2c, DEC-235), the
    manual native-speech profile (plan 22 stage 5: the manual mode is the
    default -- v2, tier 3, the keyframes made by the app, every clip the
    human's own upload; ``defaults.manual_speech_generation_profile``); else
    None -- the store's own default."""
    if quality_keys_present(gating.merged_env(settings_env)):
        return defaults.manual_speech_generation_profile()
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
        # Plan 22 stage 5: the default is the manual native-speech profile (your own clips).
        "manual": profile is not None,
        "missing_keys": [name for name in QUALITY_KEYS if not (merged.get(name) or "").strip()],
        "sound_missing_keys": [row["key"] for row in estimate["keys_needed"]
                               if SOUND_LINK in row["for"] and not row["set"]],
        "allow_paid": allow_paid,
        "estimate": estimate,
        # Plan 22: what the native-speech profile costs, for each speaking-clip model.
        "native_speech": native_speech_estimate(merged),
        # Plan 22 stage 5: what the manual profile costs (keyframes and text; the clips are yours).
        "native_speech_manual": native_speech_estimate(
            merged, story={"generation_profile": native_speech_manual_profile()}),
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
# Plan 23 stage D4: the edits of the portrait per sheet mode (``sheet_edits``);
# ``three_sheet`` is :data:`_SHEET_EDITS`.
SHEET_EDITS = {defaults.SHEET_THREE: _SHEET_EDITS, defaults.SHEET_TWO_VIEW: 0,
               defaults.SHEET_TWO_VIEW_EXPRESSIONS: 1}


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


def preset_estimate(merged=None, *, story=None) -> dict:
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
    keys a new story needs for the preset to be its default).

    Plan 23 stage D4: with a *story* (a ``story.json`` document) the sheets
    are counted in its ``sheet_mode`` (one image and no edit a character in
    ``two_view``, one and one in ``two_view_expressions``); without one, or
    on ``three_sheet``, the text is what it always was. ``story`` also
    carries ``sheet_usd_by_mode``: a character's sheets in each mode."""
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
    edits = sheet_edits(story)
    sheet_usd_by_mode = {mode: price(portrait_link) + count * price(sheet_edit_link)
                         for mode, count in SHEET_EDITS.items()}
    sheets_usd = PRESET_STORY_CHARACTERS * (price(portrait_link) + edits * price(sheet_edit_link))
    plates_usd = PRESET_STORY_PLACES * price(plate_link)
    props_usd = PRESET_STORY_PROPS * price(prop_link)
    story_usd = sheets_usd + plates_usd + props_usd
    images = PRESET_STORY_CHARACTERS * (1 + edits) + PRESET_STORY_PLACES + PRESET_STORY_PROPS

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
        f"{_usd(keyframes_usd)}). Once per story: {PRESET_STORY_CHARACTERS} characters × {1 + edits} "
        f"sheet{'s' if edits else ''}, {PRESET_STORY_PLACES} plates and {PRESET_STORY_PROPS} props ({images} images on "
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
            "sheet_usd_by_mode": {mode: round(usd, 4) for mode, usd in sheet_usd_by_mode.items()},
        },
        "prices_as_of": pricing.PRICES_AS_OF,
    }


# ----------------------------------------------- the native-speech preset's price

# What a native-speech episode is counted on before any story exists (plan
# 22's table: a ~50 s confrontation, nine character lines of about 40 s in
# all, three silent shots -- reactions and a narrator's voice-over -- of
# 4 s, a keyframe each). A story's own numbers are its storyboard's.
PRESET_SPEECH_SHOTS = 9
PRESET_SPEECH_SECONDS = 40
PRESET_SILENT_SHOTS = 3
PRESET_SILENT_SECONDS = 12


def native_speech_profile() -> dict:
    """The ``generation_profile`` of a native-speech story: the quality
    preset's (v2, tier 3, hosted links, references) on the native_speech
    budget profile."""
    return dict(defaults.quality_generation_profile(), budget_profile=defaults.NATIVE_SPEECH_PROFILE)


def native_speech_manual_profile() -> dict:
    """The ``generation_profile`` of a native-speech story whose clips are
    the human's own (plan 22 stage 5): the native_speech_manual budget
    profile."""
    return defaults.manual_speech_generation_profile()


def link_price(label, resolution=None):
    """``(price per second, None)`` of the video link *label* at
    *resolution*, or ``(None, why)`` when it has none (a link that is not
    known yet, or not priced per second). Calls nothing."""
    try:
        link = gen.parse_generation_chain(gen.VIDEO, [label])[0]
        price = pricing.price_for(link, resolution)
    except (ChainError, pricing.PriceUnknown, IndexError, KeyError) as exc:
        return None, str(exc)
    if price.unit != "second":
        return None, f"{label} is priced per {price.unit}, not per second"
    return float(price.usd), None


def _link_keys(label, merged) -> list:
    try:
        link = gen.parse_generation_chain(gen.VIDEO, [label])[0]
    except (ChainError, IndexError):
        return []
    return [{"key": name, "for": f"the clips on {label}", "set": bool(((merged or {}).get(name) or "").strip())}
            for name in gen.env_keys_for(link)]


def native_speech_estimate(merged=None, *, model=None, story=None) -> dict:
    """What a native-speech episode costs (plan 22), from the price table
    and the native_speech profile alone -- or *story*'s own profile and
    switch -- calling nothing::

        {"profile", "label", "speech_model", "speech_link", "silent_link", "episode_usd", "story_usd",
         "clips_usd", "retake_usd", "keyframes_usd", "by_model": {model: episode_usd | None},
         "keys_needed": [{"key", "for", "set"}], "missing_keys": [name, ...], "stt_missing_keys": [...],
         "summary", "assumptions", "episode": {...}, "priced": bool, "reason": sentence | None}

    :data:`PRESET_SPEECH_SHOTS` speaking shots (:data:`PRESET_SPEECH_SECONDS`)
    on the speech link of *model* (else the story's or profile's), the
    silent shots on the silent link, a keyframe each on the keyframe role's
    first link, plus the profile's retake budget (``speech_retake.cap_usd``)
    as the contingency; the story's one-off images as the quality preset
    counts them. A link with no price (not known yet) prices nothing and
    says why (``priced`` false)."""
    story_doc = story if story is not None else {"generation_profile": native_speech_profile()}
    settings = _profile_of(story_doc)
    profile_name = (story_doc.get("generation_profile") or {}).get("budget_profile") or defaults.NATIVE_SPEECH_PROFILE
    chosen = model if model in defaults.SPEECH_MODELS else speech_model(story_doc)
    speech, silent = speech_link(story_doc, chosen), silent_link(story_doc)
    resolution = video_resolution(story_doc)
    quality = preset_estimate(merged, story=story)
    keyframe_usd = float(quality["episode"]["keyframe_usd"])
    shots = PRESET_SPEECH_SHOTS + PRESET_SILENT_SHOTS
    keyframes_usd = shots * keyframe_usd
    retake = settings.get("speech_retake") or {}
    retake_usd = float(retake.get("cap_usd") or 0.0) if int(retake.get("max_per_shot") or 0) > 0 else 0.0

    def priced(which):
        link = speech_link(story_doc, which)
        speech_price, why = link_price(link, resolution) if link else (None, "no speech link")
        silent_price, why_silent = link_price(silent, resolution) if silent else (None, "no silent link")
        if speech_price is None or silent_price is None:
            return None, why or why_silent
        clips_usd = PRESET_SPEECH_SECONDS * speech_price + PRESET_SILENT_SECONDS * silent_price
        return {"clips_usd": clips_usd, "speech_price": speech_price, "silent_price": silent_price,
                "episode_usd": clips_usd + keyframes_usd + retake_usd}, None

    by_model = {}
    for which in defaults.SPEECH_MODELS:
        row, _why = priced(which)
        by_model[which] = round(row["episode_usd"], 4) if row else None
    row, reason = priced(chosen)
    if images_manual(story_doc):
        # Plan 22 stage 5: the keyframes (and the story's sheets, plates, props) are the human's own too.
        keyframe_usd, keyframes_usd = 0.0, 0.0
        if row is not None:
            row = dict(row, episode_usd=row["clips_usd"] + retake_usd)
        by_model = {which: (None if value is None else round(value - shots * float(
            quality["episode"]["keyframe_usd"]), 4)) for which, value in by_model.items()}
    keys = list(quality["keys_needed"][:len(QUALITY_KEYS)]) if not images_manual(story_doc) else []
    for label in dict.fromkeys(link for link in (speech, silent) if link):
        for entry in _link_keys(label, merged):
            if entry["key"] not in {item["key"] for item in keys}:
                keys.append(entry)
    stt_missing = stt_missing_keys(merged)
    missing = [entry["key"] for entry in keys if not entry["set"]]
    label = settings.get("label") or profile_name
    manual = is_manual_link(speech) and is_manual_link(silent)
    story_usd = 0.0 if images_manual(story_doc) else quality["story_usd"]
    if row is None:
        episode_usd, clips_usd, summary = 0.0, 0.0, f"Not priced: {reason}."
    elif manual:
        # Plan 22 stage 5: the clips are the human's own -- $0 here, their platform's credits there.
        from . import platforms

        episode_usd, clips_usd = row["episode_usd"], row["clips_usd"]
        own = platforms.own_clips_phrase(shots)
        made = ("every image your own too" if images_manual(story_doc) else
                f"a keyframe each on {quality['episode']['keyframe_link']} = {_usd(keyframes_usd)}")
        summary = (f"≈ {_usd(episode_usd)} an episode: {own}, {made}"
                   + ("" if images_manual(story_doc) else
                      f"; plus ≈ {_usd(story_usd)} once per story for sheets, plates and props"))
    else:
        episode_usd, clips_usd = row["episode_usd"], row["clips_usd"]
        summary = (f"≈ {_usd(episode_usd)} an episode ({PRESET_SPEECH_SHOTS} speaking clips on {speech}, "
                   f"{PRESET_SILENT_SHOTS} silent on {silent}, up to {_usd(retake_usd)} of retakes) plus "
                   f"≈ {_usd(quality['story_usd'])} once per story for sheets, plates and props")
    if missing or stt_missing:
        need = missing + [f"{' or '.join(stt_missing)} (the speech check)"] if stt_missing else missing
        summary += f"; add {' and '.join(need)} in Settings"
    assumptions = (f"An episode: {PRESET_SPEECH_SHOTS} character lines, each one clip that speaks it "
                   f"({PRESET_SPEECH_SECONDS} s on {speech}), {PRESET_SILENT_SHOTS} silent shots "
                   f"({PRESET_SILENT_SECONDS} s on {silent}) at {resolution}, a keyframe each on "
                   f"{quality['episode']['keyframe_link']} (${keyframe_usd:g} each = {_usd(keyframes_usd)}), and the "
                   f"retake budget ({_usd(retake_usd)}). No TTS and no lip-sync for the characters' lines; the "
                   f"narrator stays a free TTS voice-over. Prices from the table of {pricing.PRICES_AS_OF}.")
    if manual:
        assumptions = (f"An episode: {PRESET_SPEECH_SHOTS} character lines, each one clip that speaks it, and "
                       f"{PRESET_SILENT_SHOTS} silent shots, every clip made by you on your own subscription from "
                       f"the shot brief and uploaded ({gen.MANUAL_LINK}: no call, $0 here); "
                       + ("every image uploaded by you too. " if images_manual(story_doc) else
                          f"a keyframe each on {quality['episode']['keyframe_link']} (${keyframe_usd:g} each = "
                          f"{_usd(keyframes_usd)}). ")
                       + "No TTS and no lip-sync for the characters' lines; the narrator stays a free TTS "
                       f"voice-over. Prices from the table of {pricing.PRICES_AS_OF}.")
    return {
        "profile": profile_name, "label": label, "speech_model": chosen, "manual": manual,
        "images_manual": images_manual(story_doc),
        "speech_link": speech, "silent_link": silent, "resolution": resolution,
        "episode_usd": round(episode_usd, 4), "story_usd": story_usd,
        "clips_usd": round(clips_usd, 4), "retake_usd": round(retake_usd, 4), "keyframes_usd": round(keyframes_usd, 4),
        "by_model": by_model, "keys_needed": keys, "missing_keys": missing, "stt_missing_keys": stt_missing,
        "summary": summary, "assumptions": assumptions, "priced": row is not None, "reason": reason,
        "episode": {"speech_shots": PRESET_SPEECH_SHOTS, "speech_seconds": PRESET_SPEECH_SECONDS,
                    "silent_shots": PRESET_SILENT_SHOTS, "silent_seconds": PRESET_SILENT_SECONDS,
                    "speech_price_per_second": row["speech_price"] if row else None,
                    "silent_price_per_second": row["silent_price"] if row else None,
                    "keyframe_link": quality["episode"]["keyframe_link"], "keyframe_usd": keyframe_usd},
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
