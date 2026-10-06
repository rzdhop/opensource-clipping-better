"""Story presets (plan 32 stage 1): one word that makes a story ready for a
product, instead of a dozen choices.

A preset names the look (``style_template_id``), how the pictures and clips
are made (``generation_profile``), the recipe the story follows (``recipe``)
and its episode format (``episode_template_id``; None would be the default that
``defaults.episode_template_for`` gives the profile). :func:`apply` turns
one into the keyword arguments of ``StoryStore.create``; :func:`merge` lays
a caller's own choices over them (the caller's win); :func:`list_presets`
is what a front end offers.

``fruit_drama``: the fruit-drama look on the quality profile
(``defaults.quality_generation_profile``: animated beats, reference images,
hosted image links) moved onto the human's own GPU (``own_gpu``: RunPod
first, fal behind), the cast made of fruits, and agent mode -- the mode the
one-call story run (``story-fast-track``) requires
(``workflow.require_agent_mode``). Agent mode changes nothing else for a
story driven step by step: the only other branch on it is the assets
step's automatic retake of a speaking clip (``assets.retake_shot``), which
runs on a native-speech story only (``media_policy.speech_retake``), and
``own_gpu`` is not one.

Calls nothing and reads no key: a preset is the same whatever the Settings
hold. Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy

from . import defaults, templates

FRUIT_DRAMA = "fruit_drama"

PRESETS = {
    FRUIT_DRAMA: {
        "label": "Fruit drama",
        "summary": ("Short vertical drama episodes starring talking fruit, in the fruit-drama look. Pictures and "
                    "clips are made on your own GPU (RunPod), with the same reference pictures of each "
                    "character in every shot; one call can take the story from its idea to episode 1."),
        "style_template_id": FRUIT_DRAMA,
        "generation_profile": dict(defaults.quality_generation_profile(), budget_profile=defaults.OWN_GPU_PROFILE,
                                   universe="fruits", mode=defaults.MODE_AGENT),
        "recipe": FRUIT_DRAMA,
        # Plan 32 stage 4: the recipe's own format (60-90 s, 5 scenes, 6 with the recap).
        "episode_template_id": defaults.EPISODE_TEMPLATE_ID_FRUIT_DRAMA,
    },
}

# What a preset sets, in plain words, for the options list (the dict keys stay the store's own names).
_SETS = {
    "style_template_id": "the look",
    "generation_profile": "how pictures and clips are made",
    "recipe": "the recipe the story follows",
    "episode_template_id": "the episode format",
}


class UnknownPreset(ValueError):
    """No preset of that name; the message names the known ones."""

    def __init__(self, preset_id):
        self.preset_id = preset_id
        super().__init__(f"There is no preset called {preset_id!r}. The presets are: {', '.join(PRESETS)}.")


def get(preset_id) -> dict:
    """A copy of the preset *preset_id*; :class:`UnknownPreset` otherwise."""
    if preset_id not in PRESETS:
        raise UnknownPreset(preset_id)
    return copy.deepcopy(PRESETS[preset_id])


def apply(preset_id) -> dict:
    """The keyword arguments of ``StoryStore.create`` that the preset sets:
    ``style_template_id``, ``generation_profile``, ``episode_template_id``
    and ``recipe`` (``language``, ``seed_text`` and ``now`` stay the
    caller's)."""
    preset = get(preset_id)
    return {key: preset[key] for key in ("style_template_id", "generation_profile", "episode_template_id", "recipe")}


def merge(preset_id, *, style_template_id=None, generation_profile=None, episode_template_id=None) -> dict:
    """:func:`apply` with the caller's own choices on top: a style or an
    episode format named wins over the preset's; a *generation_profile*'s
    keys win over the preset profile's, key by key (the others stay the
    preset's). When the style named does not accept the preset's universe
    (its template's ``universes``), the preset's universe is left out -- a
    universe the caller named is kept, for the store to judge."""
    kwargs = apply(preset_id)
    profile = dict(kwargs["generation_profile"])
    own_profile = dict(generation_profile or {})
    if style_template_id is not None:
        kwargs["style_template_id"] = style_template_id
        if "universe" not in own_profile and profile.get("universe") is not None:
            try:
                accepted = templates.load_style(style_template_id).get("universes") or []
            except KeyError:
                accepted = []  # an unknown style is the store's refusal, with the shipped list
            if profile["universe"] not in accepted:
                profile.pop("universe")
    profile.update(own_profile)
    kwargs["generation_profile"] = profile
    if episode_template_id is not None:
        kwargs["episode_template_id"] = episode_template_id
    return kwargs


def list_presets() -> list:
    """``[{id, label, summary, sets: {key: plain words}, values: {key: value}}]``,
    one per preset, for a front end's options."""
    out = []
    for preset_id in PRESETS:
        kwargs = apply(preset_id)
        preset = PRESETS[preset_id]
        out.append({"id": preset_id, "label": preset["label"], "summary": preset["summary"],
                    "sets": {key: words for key, words in _SETS.items()},
                    "values": kwargs})
    return out
