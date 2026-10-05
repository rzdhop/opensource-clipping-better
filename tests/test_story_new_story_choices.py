"""Plan 28 stages A4 + S1 (DEC-305 §9): a new story asks four things and the
server decides the rest, never creating a story whose format cannot fit.

The human: "the UI became too complicated, too much term I do not
understand", and on clips: "me + gen button". The new-story form asks the
idea, the language, the look and who makes the clips; the server turns "who
makes the clips" into a profile (``media_policy.new_story_profile``) and
picks a format that fits, checked with the zero-call oracle
(``timing.plan_floor_preview``, through ``format_fit``):

- a native-speech story that names no format starts on the confrontation one;
- a format no plan of this story's clips fits (the v1 formats, a hook of
  1.5-3.5 s, on a native-speech story) is refused in one plain sentence and
  nothing is created;
- "me" is the manual profile (no generated voice, no narrator, the images on
  the quality links); "the app" is the native-speech profile on its cheapest
  speaking link, its price in the offer;
- the offer lists the formats that fit each choice and language, and why a
  hidden one is hidden;
- the form's default view says none of the words the human did not know.

Essential tests only (DEC-234). Stdlib + pytest (DEC-012) for the store and
the text pin; the route tests need fastapi and skip without it.
"""

from __future__ import annotations

import copy
import os
import pathlib
import re

import pytest

from clipping.aistory import defaults, media_policy, templates
from clipping.aistory.store import StoryStore
from test_stories_api import _settings, api  # noqa: F401 -- the stories API fixture, used as it is

NOW = "2026-10-05T22:00:00+00:00"
ROOT = pathlib.Path(__file__).resolve().parents[1]
WIZARD = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "NewStoryWizard.jsx"
REFUSAL = "This format cannot fit the clips this story makes. Let the app choose one."
V2_FORMATS = ["serial_60s_v2", "serial_90s_v2", "narrated_drama_60s_v2", "confrontation_50s_v2"]
QUALITY_SETTINGS = {"FAL_KEY": "test-fal-key"}


@pytest.fixture
def store(tmp_path):
    return StoryStore(str(tmp_path / "stories"), on_log=lambda line: None)


def _story_folders(store):
    root = pathlib.Path(store.root)
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


# ------------------------------------------------------------ the refusal (A4.1)

@pytest.mark.parametrize("template_id", ["serial_60s_v1", "serial_90s_v1"])
@pytest.mark.parametrize("profile", [defaults.manual_speech_generation_profile(),
                                     media_policy.native_speech_profile()])
def test_a_native_speech_story_on_a_v1_format_is_refused_and_never_created(store, template_id, profile):
    with pytest.raises(ValueError) as caught:
        store.create(language="fr", generation_profile=profile, episode_template_id=template_id, now=NOW)
    assert str(caught.value) == REFUSAL
    assert _story_folders(store) == []


def test_any_pick_the_oracle_says_cannot_fit_is_refused(store, monkeypatch):
    # A confrontation squeezed to a 30 s window: no plan of 6/8 s clips fits it, in French or English.
    tight = copy.deepcopy(templates.load_episode_template("confrontation_50s_v2"))
    tight["window_s"] = [20, 30]
    real = templates.load_episode_template
    monkeypatch.setattr(templates, "load_episode_template",
                        lambda tid: copy.deepcopy(tight) if tid == "confrontation_50s_v2" else real(tid))
    for language in ("fr", "en"):
        with pytest.raises(ValueError, match=re.escape(REFUSAL)):
            store.create(language=language, generation_profile=defaults.manual_speech_generation_profile(),
                         episode_template_id="confrontation_50s_v2", now=NOW)
    assert _story_folders(store) == []
    # Named nothing, the app picks a format that does fit instead.
    story = store.create(language="fr", generation_profile=defaults.manual_speech_generation_profile(), now=NOW)
    assert story["episode_template_id"] in V2_FORMATS and story["episode_template_id"] != "confrontation_50s_v2"


def test_a_story_that_does_not_speak_in_its_clips_keeps_every_shipped_format(store):
    # The TTS stories re-time their scenes to the window: no oracle, as before.
    story = store.create(language="fr", generation_profile=defaults.quality_generation_profile(),
                         episode_template_id="serial_60s_v1", now=NOW)
    assert story["episode_template_id"] == "serial_60s_v1"
    assert store.create(language="fr", now=NOW)["episode_template_id"] == "serial_60s_v1"


def test_a_fitting_v2_pick_is_kept_and_no_pick_is_the_confrontation(store):
    for profile in (defaults.manual_speech_generation_profile(), media_policy.native_speech_profile()):
        assert store.create(language="en", generation_profile=profile, now=NOW)[
            "episode_template_id"] == "confrontation_50s_v2"
        assert store.create(language="en", generation_profile=profile, style_template_id="fruit_drama",
                            episode_template_id="narrated_drama_60s_v2", now=NOW)[
            "episode_template_id"] == "narrated_drama_60s_v2"


# ------------------------------------------------------------ who makes the clips (A4.2)

def test_me_is_the_manual_profile_and_the_app_is_native_speech_on_its_cheapest_link():
    me = media_policy.new_story_profile({}, clips="me")
    assert me == defaults.manual_speech_generation_profile()
    assert "images" not in me  # the images on the quality links
    app = media_policy.new_story_profile({}, clips="app")
    assert app == dict(media_policy.native_speech_profile(), speech_model="lite")
    assert media_policy.speech_link({"generation_profile": app}) == "gemini/veo-3.1-lite"
    # No choice named: as before (the keys decide).
    assert media_policy.new_story_profile({}) is None
    assert media_policy.new_story_profile(QUALITY_SETTINGS) == defaults.manual_speech_generation_profile()


def test_the_offer_prices_both_choices_and_lists_the_formats_that_fit():
    offer = media_policy.new_story_offer(QUALITY_SETTINGS)
    me, app = offer["clip_makers"]["me"], offer["clip_makers"]["app"]
    assert me["profile"] == media_policy.new_story_profile({}, clips="me")
    assert app["profile"] == media_policy.new_story_profile({}, clips="app")
    manual = media_policy.native_speech_estimate(
        {"FAL_KEY": "test-fal-key"}, story={"generation_profile": me["profile"]})
    assert me["episode_usd"] == manual["episode_usd"] < 1.0
    assert app["episode_usd"] == media_policy.native_speech_estimate(QUALITY_SETTINGS, model="lite")["episode_usd"]
    assert app["episode_usd"] > me["episode_usd"]
    for maker in ("me", "app"):
        for language in ("fr", "en"):
            assert offer["formats_that_fit"][maker][language] == V2_FORMATS
            hidden = offer["formats_hidden"][maker][language]
            assert sorted(hidden) == ["serial_60s_v1", "serial_90s_v1"]
            assert hidden["serial_60s_v1"] == "its opening needs at least 6 s of clips, more than the 3.5 s it has."


def test_the_oracle_s_answer_is_the_store_s(store):
    from clipping.aistory import format_fit

    profile = defaults.manual_speech_generation_profile()
    fit, hidden = format_fit.formats_that_fit(profile, language="fr")
    assert fit == V2_FORMATS and set(hidden) == {"serial_60s_v1", "serial_90s_v1"}
    for template_id in hidden:
        assert format_fit.format_refusal(profile, template_id, language="fr") is not None
    assert format_fit.formats_that_fit(defaults.quality_generation_profile(), language="fr") == (
        list(defaults.EPISODE_TEMPLATE_IDS), {})


# ------------------------------------------------------------ the route

def test_the_route_creates_from_who_makes_the_clips_and_refuses_an_impossible_format(api):
    _settings(api, QUALITY_SETTINGS)
    me = api.client.post("/api/stories", json={"language": "fr", "seed_text": "Two lemons argue.", "clips": "me"})
    assert me.status_code == 201, me.text
    story = me.json()
    assert story["generation_profile"]["budget_profile"] == "native_speech_manual"
    assert story["generation_profile"]["voices"] == "none" and "images" not in story["generation_profile"]
    assert story["narrator"] == {"enabled": False, "voice": None}
    assert story["episode_template_id"] == "confrontation_50s_v2"
    app = api.client.post("/api/stories", json={"language": "en", "clips": "app"}).json()
    assert app["generation_profile"]["budget_profile"] == "native_speech"
    assert app["generation_profile"]["speech_model"] == "lite" and app["generation_profile"]["voices"] == "none"
    assert app["episode_template_id"] == "confrontation_50s_v2"
    before = sorted(os.listdir(api.outputs / "stories")) if (api.outputs / "stories").is_dir() else []
    refused = api.client.post("/api/stories", json={"language": "fr", "clips": "me",
                                                    "episode_template_id": "serial_60s_v1"})
    assert refused.status_code == 400 and refused.json()["detail"] == REFUSAL
    assert (sorted(os.listdir(api.outputs / "stories")) if (api.outputs / "stories").is_dir() else []) == before
    offer = api.client.get("/api/stories/new-profile").json()
    assert offer["formats_that_fit"]["me"]["fr"] == V2_FORMATS
    assert offer["clip_makers"]["app"]["profile"]["speech_model"] == "lite"


# ------------------------------------------------------------ the screen (S1)

def _form_markup(src):
    body = src.split("function CreateStoryForm() {", 1)[1]
    return body[body.index("\n  return (\n"):]


def test_the_form_asks_four_things_in_plain_words_and_folds_the_rest_under_advanced():
    src = WIZARD.read_text(encoding="utf-8")
    markup = _form_markup(src)
    for label in (">Your idea<", ">Language<", ">The look<", ">Who makes the clips<"):
        assert label in markup, label
    assert "Create the story" in markup
    assert "Me, on Flow or Higgsfield" in src and "the app writes the prompts and checks the clips." in src
    assert "of app cost per episode." in src and "'The app'" in src and "per episode`" in src
    # One collapsed fold, opened by the human only.
    assert markup.count("<details") == 1 and "<summary>Advanced</summary>" in markup
    assert "const [showAdvanced, setShowAdvanced] = useState(false)" in src
    fold = markup[markup.index("<details"):markup.index("</details>")]
    for advanced in ('htmlFor="new-story-episode-format"', 'id="new-story-frame"', 'htmlFor="new-story-universe"',
                     "setMode('agent')", 'value="native_speech_manual"', 'id="new-story-image-preference"',
                     'id="new-story-sheet-mode"', 'id="new-story-body-rule"', 'id="new-story-prompt-style"',
                     "Narrator"):
        assert advanced in fold, advanced
    # The Advanced format select lists only what the server says fits, and says why one is hidden.
    assert "offer.formats_that_fit" in src and "offer.formats_hidden" in src
    # The default view: none of the words the human did not know.
    default_view = markup.replace(fold, "") + src[src.index("const CLIP_CHOICES"):].split("\n]\n", 1)[0]
    for word in ("tier", "route", "pipeline", "profile", "native speech", "v2"):
        assert word not in default_view.lower(), word
