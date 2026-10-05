"""A story made before plan 28 stage B2 that pinned Edge voices keeps working (DEC-305 section 2).

Edge is out of every default chain and out of the voice catalogue: no new pin, proposal, alternate or
sample lands on it. A story that already pins an Edge voice (the Dragon Fruit shape: a cast of French
voices on ``edge/<voice_id>``, line audio already made on them) must still load, list its pinned voices,
speak a sample and a line through the one-link chain of that voice alone (DEC-122), and offer only
non-Edge voices when its picker is opened.

Offline and hermetic like ``tests/test_story_voices.py`` (its fixtures, used as they are); the speech is
``tts.EDGE`` itself with its ``synthesize`` replaced (``test_story_measure.Edge``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_story_measure as tsm
from clipping.aistory import voices, workflow
from clipping.cancel import CancelToken
from clipping.providers.registry import Link
from test_story_voices import (GEMINI_KEY, NOW, _char, _k1_voice, _write_char,  # noqa: F401
                               hermetic, store)

# The Dragon Fruit shape: three characters with Edge voices pinned, one of them a French-Canadian one.
PINS = {"char_dragon": ("lead", "fr-FR-HenriNeural"),
        "char_lychee": ("lead", "fr-FR-DeniseNeural"),
        "char_rambutan": ("support", "fr-CA-ThierryNeural")}


def _edge_pinned_story(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    for char_id, (role, voice_id) in PINS.items():
        edge = voices.Voice(provider="edge", voice_id=voice_id, lang=voice_id[:5], gender="any", age="adult",
                            style_tags=(), link=Link("edge", voice_id))
        pinned = voices.pin(_char(char_id, voice=_k1_voice(sample_line="Salut tout le monde !")), edge)
        _write_char(store, story_id, char_id, role=role, name=char_id.split("_")[1].title(), voice=pinned)
    return story_id


def test_an_edge_pinned_story_still_loads_and_lists_its_voices(store):
    story_id = _edge_pinned_story(store)

    story = store.get(story_id)
    for char_id, (_role, voice_id) in PINS.items():
        doc = store.read_entity(story_id, "characters", char_id)
        assert (doc["voice"]["provider"], doc["voice"]["voice_id"]) == ("edge", voice_id)

        picker = workflow.character_voices(store, story, char_id, env=GEMINI_KEY)
        assert picker["pinned"] == {"provider": "edge", "voice_id": voice_id}
        others = {f"edge/{other}" for cid, (_r, other) in PINS.items() if cid != char_id}
        assert set(picker["taken"]) == others, "the other leads' Edge pins are still listed as taken"
        # What the picker offers is the new catalogue: no Edge voice, whatever the pins say.
        assert picker["alternates"] and all(alt["provider"] != "edge" for alt in picker["alternates"])


def test_a_pinned_edge_voice_still_speaks_its_sample_and_its_lines_through_its_one_link_chain(store):
    story_id = _edge_pinned_story(store)
    edge, gemini, local = tsm.Edge(), tsm.NeverCalled(), tsm.NeverCalled()
    adapters = tsm._adapters(edge, gemini=gemini, local=local)

    sample = voices.synthesize_sample(store, story_id, "char_dragon", env=GEMINI_KEY, on_log=lambda line: None,
                                      cancel=CancelToken(), adapters=adapters)
    assert sample["provider"] == "edge" and sample["voice_id"] == "fr-FR-HenriNeural"
    assert Path(store.media_path(story_id, "characters", "char_dragon", sample["name"])).is_file()

    gates = voices.LineGates(store, story_id, env=GEMINI_KEY)
    voice = store.read_entity(story_id, "characters", "char_lychee")["voice"]
    line_dir = Path(store.story_dir(story_id))
    result = voices.synthesize_line(gates, voice=voice, text="Je ne perds jamais, chéri.",
                                    dest_for=lambda ext: str(line_dir / f"line_01.{ext}"),
                                    on_log=lambda line: None, cancel=CancelToken(), adapters=adapters)

    assert result["voice"] == "edge/fr-FR-DeniseNeural" and result["ext"] == "mp3" and result["paid"] is False
    assert (line_dir / "line_01.mp3").is_file()
    sidecar = json.loads((line_dir / "line_01.json").read_text(encoding="utf-8"))
    assert sidecar["provider"] == "edge" and sidecar["duration_s"] > 0
    assert [call["link"] for call in edge.calls] == [Link("edge", "fr-FR-HenriNeural"),
                                                     Link("edge", "fr-FR-DeniseNeural")]
    assert gemini.calls == 0 and local.calls == 0, "no fallback to another engine (DEC-122)"


def test_edge_is_in_no_default_chain_so_a_new_story_cannot_pick_it(store):
    """The other side of the same contract: a story with nothing pinned is offered no Edge voice."""
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    for v2 in (False, True):
        pool = voices.catalogue("fr", env=GEMINI_KEY, v2=v2)
        assert pool and all(v.provider != "edge" for v in pool)
    _write_char(store, story_id, "char_new", name="New")
    picker = workflow.character_voices(store, store.get(story_id), "char_new", env=GEMINI_KEY)
    assert picker["pinned"] is None and all(alt["provider"] != "edge" for alt in picker["alternates"])
