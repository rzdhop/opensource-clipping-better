"""Which episode formats a new story's clips can fit (plan 28 stage A4).

A story whose characters speak in their own clips (``defaults.speaks_natively``)
plans every scene on its links' clip lengths, and a format whose slots no
such plan fits makes an episode the Script step must refuse (plan 28 stage
A1). This module asks the zero-call oracle, ``timing.plan_floor_preview``,
before the story exists: a format is said to fit when the cheapest plan of
episodes 1 and 2 -- on the story's speech and silent links' lengths
(``steps.clips.speech_lengths``), in its language, with the narrator off (a
new story's, plan 28 stage B1), for its look (the style's scene clamp and
whether its cliffhanger cuts to black; with no look yet, every shipped one)
-- holds every scene and ends inside the window.

A story whose lines are voiced by TTS re-times its scenes to the window:
every shipped format fits it, as before (no check).

Pure: no call, no write. ``store.create`` refuses an impossible pick with
:data:`FORMAT_REFUSAL`; the new-story offer lists the formats that fit each
choice (``media_policy.new_story_offer``).
"""

from __future__ import annotations

from . import defaults, templates, timing

# The one sentence a pick that cannot fit is refused with (the new-story form shows it as it is).
FORMAT_REFUSAL = "This format cannot fit the clips this story makes. Let the app choose one."
# When no shipped format fits (no shipped link does this): the story is not made.
NO_FORMAT_FITS = "No episode format fits the clips this story makes. Pick other clips, or ask for help."
# The v1 formats: made for stills and short scenes, never for clips that speak (their hook is 1.5-3.5 s).
V1_FORMATS = ("serial_60s_v1", "serial_90s_v1")
# The episodes checked: the first (no recap) and one with the recap.
CHECKED_EPISODES = (1, 2)
# A slot's function in plain words, for the reason a format is hidden.
_PLAIN_PART = {"hook": "opening", "cliffhanger": "ending", "recap": "recap"}


def checks(profile) -> bool:
    """Whether a story on *profile* has its format checked: one whose
    characters speak in their own clips (``defaults.speaks_natively``)."""
    return defaults.speaks_natively(profile)


def clip_lengths(profile) -> tuple:
    """``(speech lengths, silent lengths)`` a story on *profile* plans its
    clips at (``steps.clips.speech_lengths``, its links' own tables inside
    the native shot window)."""
    from .steps import clips as clips_step

    return clips_step.speech_lengths({"generation_profile": dict(profile or {})})


def _looks(style_template_id):
    """``[(style lock, cliffhanger cuts to black)]``: the story's look, else every shipped one."""
    ids = [style_template_id] if style_template_id else templates.list_style_ids()
    looks = []
    for style_id in ids:
        lock = templates.load_style(style_id)
        cut = (lock.get("episode_defaults") or {}).get("cliffhanger_style") == "cut_to_black"
        looks.append((lock, cut))
    return looks or [(None, False)]


def format_refusal(profile, template_id, *, language, style_template_id=None):
    """Why a story on *profile*, in *language*, with the look
    *style_template_id* (None: whichever is chosen later), cannot be made on
    the format *template_id* -- one plain clause ending with a full stop --
    or None when it fits (always None on a story that does not speak in its
    own clips, :func:`checks`)."""
    if not checks(profile):
        return None
    reason = _oracle_refusal(profile, template_id, language=language, style_template_id=style_template_id)
    if reason is None and template_id in V1_FORMATS:
        # Never offered to such a story, whatever its links (plan 28 stage A4).
        return "it is made for stills and short scenes, not for clips that speak."
    return reason


def _oracle_refusal(profile, template_id, *, language, style_template_id):
    """:func:`format_refusal`'s answer from ``timing.plan_floor_preview`` alone."""
    template = templates.load_episode_template(template_id)
    lengths = clip_lengths(profile)
    window_hi = float(template["window_s"][1])
    for lock, cut in _looks(style_template_id):
        for ep in CHECKED_EPISODES:
            preview = timing.plan_floor_preview(template, ep, lengths, False, lang=language, style_lock=lock,
                                                end_card=cut)
            unplannable = preview.get("unplannable")
            if unplannable:
                part = _PLAIN_PART.get(unplannable["function"], "middle scene")
                return (f"its {part} needs at least {unplannable['need_s']:g} s of clips, more than the "
                        f"{unplannable['slot_hi_s']:g} s it has.")
            if preview["floor_s"] > window_hi + 1e-6:
                return (f"its clips need at least {preview['floor_s']:g} s, more than its "
                        f"{window_hi:g} s limit.")
    return None


def formats_that_fit(profile, *, language, style_template_id=None) -> tuple:
    """``([template ids that fit, in the shipped order], {hidden id: reason})``
    for a story on *profile* in *language* (:func:`format_refusal`)."""
    fit, hidden = [], {}
    for template_id in defaults.EPISODE_TEMPLATE_IDS:
        reason = format_refusal(profile, template_id, language=language, style_template_id=style_template_id)
        if reason is None:
            fit.append(template_id)
        else:
            hidden[template_id] = reason
    return fit, hidden


def choose_format(profile, chosen=None, *, language, style_template_id=None) -> str:
    """The format a story on *profile* is created on: *chosen* when it fits
    (``ValueError(FORMAT_REFUSAL)`` when it does not -- never replaced
    silently); else ``defaults.episode_template_for``'s (the confrontation
    format on a story that speaks in its own clips) when it fits, else the
    first that fits (``ValueError(NO_FORMAT_FITS)`` when none does)."""
    if chosen is not None:
        if format_refusal(profile, chosen, language=language, style_template_id=style_template_id):
            raise ValueError(FORMAT_REFUSAL)
        return chosen
    default = defaults.episode_template_for(profile, None)
    if not format_refusal(profile, default, language=language, style_template_id=style_template_id):
        return default
    fit, _hidden = formats_that_fit(profile, language=language, style_template_id=style_template_id)
    if not fit:
        raise ValueError(NO_FORMAT_FITS)
    return fit[0]
