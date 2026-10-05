"""AI Story plan 23 stage D2: the Universe select of the new-story form and the profile card's line.

Source-reading contract, the style of ``test_dashboard_new_story_format.py`` (no browser, stdlib + pytest):
the wizard lists the universes the chosen style takes (hidden when it takes none), labels them in the story
language, shows the audience note, and sends ``generation_profile.universe`` -- a field of the API model --
from the endpoint ``GET /api/stories/universes``; the profile card shows the story's universe.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from clipping.aistory import defaults

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
WIZARD = SRC / "pages" / "story" / "NewStoryWizard.jsx"
CARD = SRC / "pages" / "story" / "GenerationProfileCard.jsx"
API_JS = SRC / "api.js"
ROUTES = ROOT / "web" / "api" / "routes" / "stories.py"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_the_wizard_filters_the_select_by_the_chosen_style_and_hides_it_when_the_style_lists_none():
    src = _read(WIZARD)
    assert "import { createStory, fetchNewStoryProfile, fetchSettings, fetchStyles, fetchUniverses } from '../../api'" in src
    assert "fetchUniverses().then((data) => { if (!cancelled) setUniverseCatalogue(data) }).catch(() => {})" in src
    assert "const styleUniverses = universeCatalogue.by_style[styleTemplateId]" in src
    assert "styleUniverses.universes" in src and ".filter(Boolean)" in src
    assert "{universeOptions.length > 0 && (" in src                       # hidden when there is nothing to pick
    # Re-pinned on purpose (plan 28 stage S1): under Advanced, in plain words.
    assert '<label className="form-label" htmlFor="new-story-universe">Characters made of</label>' in src
    select = src.split('id="new-story-universe"', 1)[1].split("</select>", 1)[0]
    assert "value={shownUniverse}" in select
    assert "onChange={(e) => choose(setUniverseChoice)(e.target.value)}" in select
    assert "universeLabel(universe)" in select
    # The pick the style does not list is dropped (a style change), the style's default shows instead.
    assert "universeOptions.some((universe) => universe.id === universeChoice) ? universeChoice : ''" in src
    assert "(styleUniverses ? styleUniverses.default : '')" in src


def test_the_labels_are_in_the_story_language_and_the_audience_note_sits_under_the_select():
    src = _read(WIZARD)
    assert "const universeLabel = (universe) => universe.label[language || 'en']" in src
    assert "shownUniverseEntry.audience_note[language || 'en']" in src
    after_select = src.split('id="new-story-universe"', 1)[1].split("</select>", 1)[1].split("\n          )}", 1)[0]
    assert "shownUniverseEntry.audience_note" in after_select


def test_the_wizard_sends_the_universe_in_the_generation_profile_and_the_api_model_has_the_field():
    src = _read(WIZARD)
    create = re.search(r"const createFields = \{(.*?)\n      \}", src, re.DOTALL).group(1)
    assert "          ...(shownUniverse ? { universe: shownUniverse } : {})," in create
    # Re-pinned on purpose (plan 28 stage S1): the universe is part of the look, said under the style cards.
    # The form no longer sends a profile just because a universe shows: untouched, the server's profile
    # names the look's default itself (media_policy.new_story_profile, tests/test_story_new_story_choices.py).
    assert "setProfileChosen(true) }, [shownUniverse]" not in src
    assert "The characters are {universeLabel(shownUniverseEntry).toLowerCase()}." in src
    assert "fruits" in defaults.UNIVERSES


def test_the_api_model_has_the_universe_field():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert "universe" in GenerationProfileModel.model_fields
    assert GenerationProfileModel.model_validate({"universe": "fruits"}).universe == "fruits"


def test_the_endpoint_exists_before_the_story_id_route_and_the_api_client_reads_it():
    routes = _read(ROUTES)
    assert routes.index('@router.get("/universes")') < routes.index('@router.get("/{story_id}")')
    api = _read(API_JS)
    assert "export async function fetchUniverses()" in api and "request('/stories/universes')" in api


def test_the_profile_card_shows_the_stories_universe_or_its_styles_default():
    src = _read(CARD)
    assert "fetchUniverses" in src
    assert "const universeId = profile.universe || (styleUniverses ? styleUniverses.default : null)" in src
    assert "{universeEntry && (" in src and "<span className=\"form-label\">Universe</span>" in src
    assert "universeEntry.label[story.language] || universeEntry.label.en" in src
    assert "universeEntry.audience_note[story.language] || universeEntry.audience_note.en" in src
