"""Plan 29 stage 4b (DEC-308 point 4): the written ``description`` of a
character, a place or a prop is edited inline, on its tile.

``PATCH /api/stories/{id}/places/{place_id}`` (and the characters' and props'
routes) take ``description``: a string of 60 to 160 words, or null to clear it;
a place's or a prop's never mentions a person. A refused value answers 400
with the validator's own sentence and writes nothing.
"""

from __future__ import annotations

import test_stories_api_phase2 as p2
from test_stories_api_phase2 import api, hermetic  # noqa: F401 - the route tests' app (hermetic is autouse)


def _words(n, word="amber"):
    return " ".join([word] * n)


def test_a_place_description_is_stored_refused_when_it_names_a_person_and_cleared_by_null(api):
    story_id = p2._full_via_api(api)
    url = p2._url(story_id, f"/places/{p2.BEACH}")
    text = "A wide beach of pale sand, " + _words(95) + ", warm light."

    response = api.client.patch(url, json={"description": "  " + text + "  "})
    assert response.status_code == 200, response.text
    assert response.json()["description"] == text
    assert response.json()["approved_at"] is None
    assert api.store.read_entity(story_id, "places", p2.BEACH)["description"] == text

    # Too short: the stored rule (60 to 160 words); a person in a set: the writer's rule, kept on an edit.
    response = api.client.patch(url, json={"description": "a person stands here"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "The place would not be valid with these values."
    assert detail["errors"] == ["$.description: 4 words, expected 60 to 160"]
    response = api.client.patch(url, json={"description": _words(98) + " a person"})
    assert response.status_code == 400
    assert response.json()["detail"]["errors"] == [
        "$.description: the set or the object is shown empty -- never mention 'person'"]
    assert api.store.read_entity(story_id, "places", p2.BEACH)["description"] == text

    response = api.client.patch(url, json={"description": None})
    assert response.status_code == 200 and response.json()["description"] is None
    assert api.store.read_entity(story_id, "places", p2.BEACH)["description"] is None
