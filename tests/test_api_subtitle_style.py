"""``PATCH /api/stories/{id}/subtitle-style`` (plan 23 stage B5): the story's
own subtitle look -- set as a whole, cleared with ``null`` -- allowed at any
time (the style lock's freeze included), refused with 409 while a render of
the story runs and with 400 (``{"message", "errors"}``, the contrast ratio
named) for a value the rules refuse.

The fixtures are the phase-2 API tests' own (``tests/test_stories_api_phase2.py``).
The routes need pydantic, fastapi and httpx and skip without them (DEC-012).
"""

from __future__ import annotations

import json

import pytest

from test_stories_api_phase2 import (  # noqa: F401 -- api and hermetic are fixtures
    _story,
    _url,
    api,
    hermetic,
)

LOOK = {"size_pct": 120, "position_pct": 60, "text_colour": "#FFFF00", "outline_px": 4,
        "box": {"colour": "#202020", "opacity_pct": 60}}


def _patch(api, story_id, body):
    return api.client.patch(_url(story_id, "/subtitle-style"), json=body)


def _stored(api, story_id):
    return api.store.get(story_id).get("subtitle_style")


def test_the_look_is_set_as_a_whole_and_shown_with_the_story(api):
    story_id = _story(api.store)

    response = _patch(api, story_id, LOOK)

    assert response.status_code == 200, response.text
    assert response.json()["subtitle_style"] == LOOK
    assert _stored(api, story_id) == LOOK
    assert api.client.get(_url(story_id)).json()["story"]["subtitle_style"] == LOOK

    # the body replaces the stored look, it is not merged onto it
    assert _patch(api, story_id, {"font_family": "Bangers"}).json()["subtitle_style"] == {"font_family": "Bangers"}
    assert _stored(api, story_id) == {"font_family": "Bangers"}


@pytest.mark.parametrize("clear", [None, {}])
def test_null_or_an_empty_object_clears_the_look(api, clear):
    story_id = _story(api.store)
    _patch(api, story_id, LOOK)
    assert _stored(api, story_id) == LOOK

    response = api.client.patch(_url(story_id, "/subtitle-style"), content=json.dumps(clear),
                                headers={"content-type": "application/json"})

    assert response.status_code == 200, response.text
    assert "subtitle_style" not in response.json()
    assert _stored(api, story_id) is None


def test_a_look_is_editable_after_the_style_lock_froze(api):
    from clipping.aistory import schemas, stylelock, templates

    story_id = _story(api.store)
    lock = stylelock.lock_style(stylelock.build_style_lock(templates.load_style("fruit_drama"), now="2026-10-04T10:00:00+00:00"),
                                now="2026-10-04T10:00:00+00:00")
    api.store.write_doc(story_id, "style_lock.json", lock, now="2026-10-04T10:00:00+00:00",
                        validator=schemas.style_lock_errors)

    response = _patch(api, story_id, {"size_pct": 70})

    assert response.status_code == 200, response.text
    assert api.store.read_doc(story_id, "style_lock.json") == lock


@pytest.mark.parametrize("body, needle", [
    ({"size_pct": 200}, "size_pct"),
    ({"font_family": "Papyrus"}, "font_family"),
    ({"outline_px": 9}, "outline_px"),
    ({"position_pct": 5}, "position_pct"),
    ({"text_colour": "yellow"}, "text_colour"),
    ({"box": {"colour": "#000000", "opacity_pct": 150}}, "opacity_pct"),
    ({"unknown": 1}, "unknown"),
    ({"size_pct": "big"}, "size_pct"),
])
def test_a_value_the_rules_refuse_is_a_400_naming_it_and_nothing_is_written(api, body, needle):
    story_id = _story(api.store)
    _patch(api, story_id, {"size_pct": 80})

    response = _patch(api, story_id, body)

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert needle in detail["message"] and any(needle in error for error in detail["errors"])
    assert _stored(api, story_id) == {"size_pct": 80}


def test_a_text_colour_the_outline_would_make_unreadable_is_refused_naming_the_ratio(api):
    story_id = _story(api.store)

    response = _patch(api, story_id, {"text_colour": "#777777", "outline_colour": "#888888"})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "contrast ratio 1.26" in detail["message"] and "4.5" in detail["message"]
    assert _stored(api, story_id) is None

    response = _patch(api, story_id, {"box": {"colour": "#FFFFFF", "opacity_pct": 100}})
    assert response.status_code == 400 and "box colour #FFFFFF" in response.json()["detail"]["message"]


@pytest.mark.parametrize("step", ["render", "rerender", "fast-track"])
def test_it_waits_for_a_render_of_the_story_that_is_queued_or_running(api, step):
    story_id = _story(api.store)
    api.jobs.create_job(kind="story_step", story_id=story_id, step=step, ep=1, params={})  # queued

    response = _patch(api, story_id, LOOK)

    assert response.status_code == 409, response.text
    assert f"Story step '{step}'" in response.json()["detail"] and "is queued" in response.json()["detail"]
    assert _stored(api, story_id) is None


def test_another_steps_job_does_not_hold_the_look_back(api):
    story_id = _story(api.store)
    api.jobs.create_job(kind="story_step", story_id=story_id, step="cast", params={})  # queued

    assert _patch(api, story_id, LOOK).status_code == 200


def test_a_finished_render_does_not_block_it(api):
    from web.api.models import JobStatus

    story_id = _story(api.store)
    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step="render", ep=1, params={})
    api.jobs.set_status(job_id, JobStatus.COMPLETED)

    assert _patch(api, story_id, LOOK).status_code == 200


def test_an_unknown_or_malformed_story_is_a_404(api):
    assert _patch(api, "0123456789ab", LOOK).status_code == 404
    assert api.client.patch("/api/stories/not-an-id/subtitle-style", json=LOOK).status_code == 404
