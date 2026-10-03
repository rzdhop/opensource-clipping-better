"""The word budget of each v2 image and clip prompt, from the link it goes
to (phase 7 follow-up, stage F2; the human, 2026-10-02: "More prompt context
is more accuracy and details that make the story good. ... some providers
have limited prompt size so check that also").

Stage F1 (``clipping.providers.prompt_limits``) knows each generation link's
prompt limit and refuses an over-limit prompt at dispatch. The v2 builders
(``prompting``, ``shots``) took one fixed budget per kind whatever the link:
a keyframe 220 words, a clip 80, a sheet 130, a plate 150, a prop 80. Here
each kind's budget is its link's own (``prompt_limits.budget_words``: the
limit in words, at 6.5 characters a word), bounded by a quality ceiling per
kind, and -- with no link known -- the kind's fixed number, which stays the
floor every v2 prompt was built to until this stage.

The ceilings. A very long prompt dilutes an image or video model rather than
guiding it: past its text encoder's window the tail is read partially or not
at all (FLUX's T5 reads 512 tokens, about 320 words at 6.5 characters -- the
widest window among the image models we know whole), and the video models'
own guides ask for one focused paragraph. So a keyframe stops at 320 words
(the T5 window), a clip at 160 (twice the 80 that animated well, room for
the beat's emotion, the micro-actions and the camera's intent, under the
230 words of our own seedance cap), an ambience clip at 160 plus the sound
brief's 60-word share, a sheet at 200 (one character and the design rules),
a plate at 220 (a whole set: layout, light, scale, dressing) and a prop at
120 (one object). Every link of the quality preset accepts more than any
ceiling (seedream 461 words, nano-banana 5041, Veo 630, kling 384, seedance
230), so there the ceiling is the budget; a live limit fal publishes under it
(``provider_limits.json``) wins, and the ladder in ``shots`` fits the prompt
to it or refuses.

Who knows the link. The storyboard step builds every shot to its episode's
planned links (``steps/clips.episode_budgets``: the recorded sticky link,
else the keyframe role chain's first link; the clips' planned link), so the
stored prompt is the one sent. The assets step checks each stored prompt
against the link it actually uses (:func:`over_sentence`: a link switch, or
a limit that moved since) and refuses one it cannot take, with what to do;
the dispatch check of F1 is the backstop. The sheets, plates and props take
their role chain's first link (``refimages``).

Stdlib only (DEC-012). Pure but for ``prompt_limits.limit_for``, which reads
the live limits file by path; nothing here calls a provider.
"""

from __future__ import annotations

from clipping.providers import prompt_limits
from clipping.providers.registry import describe

from . import prompting

KEYFRAME_CEILING_WORDS = 320
CLIP_CEILING_WORDS = 160
CLIP_AUDIO_CEILING_WORDS = CLIP_CEILING_WORDS + prompting.CLIP_AUDIO_SHARE_WORDS
SHEET_CEILING_WORDS = 200
PLATE_CEILING_WORDS = 220
PROP_CEILING_WORDS = 120


def _label(link):
    return link if isinstance(link, str) or link is None else describe(link)


def words_for(link, *, default, ceiling, live=None) -> int:
    """The word budget of a prompt on *link* (a link or its label): its
    ``prompt_limits.budget_words`` bounded by *ceiling*; *default* with no
    link, or a link with no known limit. *live* as ``prompt_limits.limit_for``'s."""
    label = _label(link)
    if not label:
        return default
    return min(prompt_limits.budget_words(label, default=default, live=live), ceiling)


def keyframe_words(link, *, live=None) -> int:
    """A v2 keyframe prompt's budget on *link*: at most :data:`KEYFRAME_CEILING_WORDS`."""
    return words_for(link, default=prompting.KEYFRAME_V2_MAX_WORDS, ceiling=KEYFRAME_CEILING_WORDS, live=live)


def clip_words(link, *, live=None) -> int:
    """A v2 clip prompt's budget on *link*: at most :data:`CLIP_CEILING_WORDS`."""
    return words_for(link, default=prompting.CLIP_V2_MAX_WORDS, ceiling=CLIP_CEILING_WORDS, live=live)


def clip_audio_words(link, *, live=None) -> int:
    """An ambience clip's whole prompt budget on *link* (stage E's brief
    inside it): the clip's plus the sound's share, at most
    :data:`CLIP_AUDIO_CEILING_WORDS`, never over the link's own."""
    return words_for(link, default=prompting.CLIP_AUDIO_MAX_WORDS, ceiling=CLIP_AUDIO_CEILING_WORDS, live=live)


def sheet_words(link, *, live=None) -> int:
    """A v2 character sheet prompt's budget on *link*."""
    return words_for(link, default=prompting.SHEET_V2_MAX_WORDS, ceiling=SHEET_CEILING_WORDS, live=live)


def plate_words(link, *, live=None) -> int:
    """A v2 plate prompt's budget on *link*."""
    return words_for(link, default=prompting.PLATE_V2_MAX_WORDS, ceiling=PLATE_CEILING_WORDS, live=live)


def prop_words(link, *, live=None) -> int:
    """A v2 prop reference prompt's budget on *link*."""
    return words_for(link, default=prompting.PROP_V2_MAX_WORDS, ceiling=PROP_CEILING_WORDS, live=live)


def for_links(image_link=None, video_link=None, *, live=None) -> prompting.Budgets:
    """The ``prompting.Budgets`` of a shot whose keyframe goes to *image_link*
    and whose clip to *video_link* (links or labels; None: not known yet,
    the fixed numbers)."""
    image, video = _label(image_link), _label(video_link)
    return prompting.Budgets(keyframe_words(image, live=live), clip_words(video, live=live), image, video)


# ------------------------------------------------- a stored prompt, at request time

def over_sentence(kind, shot_id, link, text, *, budget, override=False, live=None):
    """Why the stored *kind* prompt (``"keyframe"`` or ``"clip"``) *text* of
    shot *shot_id* cannot be sent to *link* now, or None when it can: over
    the link's limit (``prompt_limits.check``'s own words), or -- not a
    user's *override*, whose length is their own -- over *budget*, the
    link's word budget: it was built to another link's, or the link's limit
    moved since (a live read). Says what to do; nothing is sent."""
    label = _label(link)
    if not label:
        return None
    head = f"shot {shot_id}'s {kind} prompt"
    try:
        prompt_limits.check(label, text, live=live)
    except prompt_limits.PromptTooLong as exc:
        return (f"{head} is over its link's limit ({exc}): refresh the prompts (the storyboard's Refresh prompts, "
                f"or the storyboard step again) so they are built to {label}; nothing was sent.")
    words = len(text.split())
    if override or words <= budget:
        return None
    return (f"{head} ({words} words) is over {label}'s budget of {budget} words -- built for another link, or "
            f"{label}'s limit moved since: refresh the prompts (the storyboard's Refresh prompts, or the storyboard "
            f"step again) so they are built to {label}; nothing was sent.")


def note_over_sentence(kind, shot_id, link, words, note_words, *, budget, shortest=None) -> str:
    """Why the *kind* prompt of shot *shot_id* (*words* with its note, of
    which *note_words* are the note's tail) cannot be sent to *link* even
    resolved again to the room the note leaves (DEC-249): the ladder's last
    rung (*shortest* words; None: not reached) is still over *budget* with
    the note. Says how long a note fits; nothing is sent."""
    label = _label(link)
    room = max(budget - shortest, 0) if shortest is not None else None
    fits = f" (about {room} words of note fit this shot on {label})" if room is not None else ""
    return (f"shot {shot_id}'s {kind} prompt ({words} words, {note_words} of them its note) is over {label}'s budget "
            f"of {budget} words even with its context shortened to make room for the note: shorten the note{fits}, or "
            f"ask again without one; nothing was sent.")
