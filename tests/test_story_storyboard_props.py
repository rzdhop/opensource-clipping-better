"""Tests for the storyboard step's v2 prop-image gate (AI Story phase 7,
stage 3c, A11): :func:`clipping.aistory.steps.storyboard.require_prop_images`
refuses to plan shots -- before any LLM call -- while a scene references a
prop with no approved image yet. A new-object prop a v2 episode's script
just created (``steps/script.py``'s ``new_objects`` handling, stage 3c) has
no image until the places step draws it; the storyboard must wait rather
than plan shots around an entity that will never render. A legacy story is
never refused for this (RC-M1): v1 never creates a prop with no image a
scene can already reference (DEC-171 keeps its props always an existing,
already-drawn id).

Reuses ``tests/test_story_episode_steps.py``'s fixtures (the same
``_continuity_story``/``_script_llm``/``FakeLLM`` machinery every other
phase-3 step test is built on), the established cross-module test import
pattern in this suite (see ``test_aistory_render_partial.py``,
``test_story_assets_pacing.py``).

Stdlib + pytest (DEC-012). Offline and hermetic (``hermetic``, imported
below): no key, chain, cap or limit of the machine reaches a test, no
request leaves the process, nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipping.aistory import steps

import test_story_episode_steps as eps
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
NEW_PROP_ID = "prop_giant_toaster"
NEW_PROP_NAME = "Giant Toaster"
NEW_PROP_ONE_LINE = "The runaway toaster chasing the whole cast."


def _plant_image(store, story_id, kind, eid, name=".".join(("image", "jpg")), data=PNG) -> str:
    """A real file on disk for *eid*'s ``refs/<name>`` (``entities.has_file``
    needs more than a document reference -- the file itself, never through a
    symlink): the same plant-a-file pattern ``tests/test_story_refimages.py``
    uses."""
    src = Path(store.outputs_dir).parent / f"plant-{kind}-{eid}-{name}"
    src.write_bytes(data)
    return store.write_media(story_id, kind, eid, name, str(src))


def _v2_script_with_new_prop(store):
    """A v2, ready story (episode 1 already written and its memory approved,
    :func:`test_story_episode_steps._continuity_story`) whose episode 2
    script references a brand-new object -- ``new_objects`` in E1's reply,
    the scene tagging it ``%prop_giant_toaster`` -- resolved by the script
    step to a real prop stub with no image yet."""
    story_id = eps._continuity_story(store, v2=True)
    # The fixture prop (PHONE) carries an ``image`` reference but, like every
    # other fixture in this suite, no real file behind it (nothing needed
    # ``entities.has_file`` before this stage) -- plant one so only the new
    # object the test is about is ever missing its image.
    _plant_image(store, story_id, "props", eps.PHONE)
    e1 = eps._e1_ep2_paying({"s04": [eps.HOOK_PHONE]})
    e1["new_objects"] = [{"name": NEW_PROP_NAME, "one_line": NEW_PROP_ONE_LINE, "owner_char_id": None}]
    rising = e1["scenes"][(["s00"] + eps.ALL_SCENES).index("s03")]
    assert rising["props"] == []  # the base fixture's s03 (rising) has no prop
    rising["props"] = [f"%{NEW_PROP_ID}"]
    llm = eps._script_llm(E1=[e1], E3=[eps.E3_EP2], E4=[eps.E4_PASSED])
    eps._run(eps._new().script, store, story_id, llm=llm, ep=2)

    script = eps._script(store, story_id, 2)
    assert eps._scene(script, "s03")["props"] == [NEW_PROP_ID]
    prop = store.read_entity(story_id, "props", NEW_PROP_ID)
    assert prop["image"] is None  # not drawn yet: the places step's job

    # Creating the prop leaves it unapproved, which drops the story's status
    # below "ready" (store.write_entity re-folds the "places" group approval
    # from every place and prop, DEC-123's own invariant) -- the real
    # ``check_episode_preconditions`` gate would then refuse the whole
    # episode with a generic "not ready" message before this stage's own,
    # more specific one is ever reached. To isolate what this stage adds
    # (the storyboard naming the prop and its cost, not the pre-existing
    # readiness gate), the fixture restores "ready" the same way
    # ``_ready_story`` seeds every other entity's approval: writing
    # ``approved_at`` on the document directly, bypassing
    # ``workflow.approve_entity`` (which would itself refuse an unimaged
    # prop) -- a human could reach the same state by approving out of order,
    # or a future workflow may not require it; either way the storyboard
    # must still catch an unapproved-for-render prop on its own.
    prop["approved_at"] = eps.NOW
    store.write_entity(story_id, "props", prop, now=eps.NOW)
    assert store.get(story_id)["status"] == "ready"
    return story_id


def test_storyboard_waits_for_new_prop_image(store):
    m = eps._new()
    story_id = _v2_script_with_new_prop(store)

    message, _ = eps._failed(m.storyboard, store, story_id, llm=eps.FakeLLM(), step="storyboard", ep=2)
    assert NEW_PROP_NAME in message
    assert "no approved image yet" in message
    assert "$0.067" in message  # pricing.py: gemini/nano-banana-2, the quality prop role's own link
    assert "places step" in message

    # The fast path (no LLM call at all) refuses too, before it plans anything.
    with pytest.raises(steps.StepFailed) as caught:
        m.storyboard.build_fast(store, story_id, 2, now=eps.NOW, on_log=eps.Log())
    assert NEW_PROP_NAME in str(caught.value)
    assert eps._storyboard(store, story_id, 2) is None  # nothing was written

    # Once the prop has an approved image, the storyboard proceeds -- the
    # fast path, no call, same as any other complete episode.
    _plant_image(store, story_id, "props", NEW_PROP_ID)
    prop = store.read_entity(story_id, "props", NEW_PROP_ID)
    prop["image"] = {"name": "image.jpg", "consistency": "base", "source": "gemini/nano-banana-2", "seed": 7,
                     "created_at": eps.NOW}
    prop["descriptor"] = "A chrome runaway toaster the size of a car"
    store.write_entity(story_id, "props", prop, now=eps.NOW)

    board = m.storyboard.build_fast(store, story_id, 2, now=eps.NOW, on_log=eps.Log())
    assert sorted(board["scenes"]) == ["s00"] + eps.ALL_SCENES
    for shot in board["shots"]:
        assert NEW_PROP_NAME not in shot["image_prompt"]  # name-free, like every other entity (RC)


def test_a_legacy_story_never_refuses_for_a_missing_prop_image(store):
    """A v1 story is never refused by this gate, whatever a prop's image
    looks like: DEC-171 means a legacy episode's script can only ever
    reference a prop that already existed, already drawn, before the script
    was written -- but the gate itself is unconditionally v2-only, proven
    here even against a prop whose image reference was removed by hand."""
    m = eps._new()
    story_id = eps._written_script(store)
    prop = store.read_entity(story_id, "props", eps.PHONE)
    prop["image"] = None
    store.write_entity(story_id, "props", prop, now=eps.NOW)

    board = m.storyboard.build_fast(store, story_id, 1, now=eps.NOW, on_log=eps.Log())

    assert sorted(board["scenes"]) == eps.ALL_SCENES
