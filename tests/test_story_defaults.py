"""The story defaults agree everywhere a new story is described (spec 9.3:
"same defaults as the API"; DEC-114).

Three places today: the constants of ``clipping/aistory/defaults.py``, the
API's ``GenerationProfileModel`` (``web/api/models.py``, read as text so this
runs without pydantic), and the ``--ai-story new`` parser. The dashboard's
new-story form joins as the fourth place in the stage that adds it (phase 1,
stage 11). And in every place the language has **no** default: a story names
``fr`` or ``en`` or is refused.

Stdlib + pytest only (DEC-012). The CLI is imported inside the tests, so
against the parent commit each test fails on its own.
"""

from __future__ import annotations

import argparse
import ast
import importlib
import pathlib
import re

import pytest

from clipping.aistory import defaults, schemas

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "web" / "api" / "models.py"

# (profile field, CLI dest, default constant, closed list)
PROFILE = (
    ("tier", "tier", defaults.DEFAULT_TIER, defaults.TIERS),
    ("route", "route", defaults.DEFAULT_ROUTE, defaults.ROUTES),
    ("consistency_mode", "consistency_mode", defaults.DEFAULT_CONSISTENCY_MODE, defaults.CONSISTENCY_MODES),
    ("budget_profile", "budget_profile", defaults.DEFAULT_BUDGET_PROFILE, defaults.BUDGET_PROFILES),
)


def _class_body(text, name) -> str:
    body = text[text.index(f"class {name}("):]
    return body[: body.index("\nclass ")]


def _new_parser():
    cli = importlib.import_module("clipping.aistory.cli")
    parser = cli.build_parser()
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return parser, commands.choices["new"]


def _option(parser, flag):
    return next(a for a in parser._actions if flag in a.option_strings)


def test_the_story_defaults_agree_in_all_three_places():
    # 1. the constants, each inside its closed list, and the fresh profile built from them
    for _field, _dest, default, choices in PROFILE:
        assert default in choices and type(default) is type(choices[0])
    assert defaults.default_generation_profile() == {field: default for field, _d, default, _c in PROFILE}

    # 2. the request model: the four literal lines, each literal the constant
    body = _class_body(MODELS.read_text(encoding="utf-8"), "GenerationProfileModel")
    tier = re.search(r"^    tier: int = (.+)$", body, re.M)
    assert tier and ast.literal_eval(tier.group(1)) == defaults.DEFAULT_TIER
    for field, _dest, default, choices in PROFILE[1:]:
        line = re.search(rf"^    {field}: Literal\[(.+)\] = (.+)$", body, re.M)
        assert line, field
        assert ast.literal_eval(f"({line.group(1)},)") == choices, field
        assert ast.literal_eval(line.group(2)) == default, field

    # 3. the CLI: the parsed defaults and the closed lists of `new`
    parser, new = _new_parser()
    args = parser.parse_args(["new", "--lang", "fr"])
    for _field, dest, default, choices in PROFILE:
        assert getattr(args, dest) == default, dest
        action = next(a for a in new._actions if a.dest == dest)
        assert action.default == default and tuple(action.choices) == choices, dest


def test_the_react_new_story_form_agrees_too():
    """The fourth place (phase 1, stage 11): NewStoryWizard.jsx's
    ``DEFAULT_GENERATION_PROFILE`` literal, and its language control's
    initial state (no default)."""
    wizard = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "NewStoryWizard.jsx"
    src = wizard.read_text(encoding="utf-8")

    match = re.search(r"const DEFAULT_GENERATION_PROFILE = \{([^}]*)\}", src)
    assert match, "DEFAULT_GENERATION_PROFILE literal not found in NewStoryWizard.jsx"
    body = match.group(1)

    tier = re.search(r"tier:\s*(\d+)", body)
    assert tier and int(tier.group(1)) == defaults.DEFAULT_TIER
    for field, _dest, default, _choices in PROFILE[1:]:
        line = re.search(rf"{field}:\s*'([a-z_]+)'", body)
        assert line, field
        assert line.group(1) == default, field

    # The language control starts at null (or an equally empty falsy value),
    # never a picked language -- the same "no silent default" rule as the
    # API and the CLI.
    lang_state = re.search(r"const \[language, setLanguage\] = useState\((.*?)\)", src)
    assert lang_state, "language useState(...) not found in NewStoryWizard.jsx"
    assert lang_state.group(1).strip() in ("null", "''", '""'), lang_state.group(1)


def test_the_language_has_no_default_in_any_place():
    # 1. no default-language constant at all
    assert [name for name in vars(defaults) if "LANG" in name.upper()] == []

    # 2. the create model's language field has no "="
    body = _class_body(MODELS.read_text(encoding="utf-8"), "StoryCreateRequest")
    line = re.search(r"^    language: (.+)$", body, re.M)
    assert line and "=" not in line.group(1)
    assert ast.literal_eval(f"({line.group(1)[len('Literal['):-1]},)") == schemas.LANGUAGES

    # 3. the CLI option is required, with no default, over the same languages
    _parser, new = _new_parser()
    lang = _option(new, "--lang")
    assert lang.required is True and lang.default is None
    assert tuple(lang.choices) == schemas.LANGUAGES


def test_the_clip_defaults_agree_in_the_step_the_api_the_cli_and_the_dashboard():
    """Phase 6 stage 11: ``animate`` is on and ``fill_failed_with_motion``
    off unless sent -- in the steps' own readers, in the API's params (a
    field left unsent reaches the step as nothing), in the CLI (a flag that
    turns each away from its default, never a value), and in what the
    dashboard sends today (the assets run sends ``animate: true``; the render
    sends no fill)."""
    from clipping.aistory.steps import assets, render

    # 1. the steps
    assert assets.animate_param({}) is True and assets.animate_param({"animate": None}) is True
    assert render.FILL_PARAM not in render.read_params({})

    # 2. the API's params: optional, no default of their own
    text = MODELS.read_text(encoding="utf-8")
    assert re.search(r"^    animate: Optional\[bool\] = None$", _class_body(text, "AssetsStepParams"), re.M)
    assert re.search(r"^    fill_failed_with_motion: Optional\[bool\] = None$", _class_body(text, "RenderStepParams"),
                     re.M)

    # 3. the CLI: --no-animate (assets) and --fill-failed-with-motion (step render, render) start off
    cli = importlib.import_module("clipping.aistory.cli")
    parser = cli.build_parser()
    commands = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    step, render_cmd = commands.choices["step"], commands.choices["render"]
    for command, flag in ((step, "--no-animate"), (step, "--fill-failed-with-motion"),
                          (render_cmd, "--fill-failed-with-motion")):
        action = _option(command, flag)
        assert action.default is False and action.const is True, flag
    args = parser.parse_args(["step", "0123456789ab", "assets", "--ep", "1"])
    assert assets.ANIMATE_PARAM not in cli._phase4_params(args, "assets")
    args = parser.parse_args(["step", "0123456789ab", "render", "--ep", "1"])
    assert render.FILL_PARAM not in cli._phase4_params(args, "render")

    # 4. the dashboard (phase 6 stage 12): the assets run's "Animate" checkbox
    # starts ticked (assets.animate_param's own default) and the render
    # step's "Fill failed shots with motion" checkbox starts unticked
    # (render.FILL_PARAM's own default) -- both controls are now
    # user-editable, so the pin is on each checkbox's initial state, not a
    # literal `true` in the params object (which now forwards the state
    # variable).
    pane = ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "episode"
    # The assets header moved to storyboard/AssetsCards.jsx (dashboard overhaul stage 4, DEC-256).
    storyboard_src = (pane / "storyboard" / "AssetsCards.jsx").read_text(encoding="utf-8")
    assert re.search(r"const \[animate, setAnimate\] = useState\(true\)", storyboard_src)
    assert re.search(r"const assetsParams = \{[^}]*\banimate\b[^}]*\}", storyboard_src)

    preview_src = (pane / "PreviewPane.jsx").read_text(encoding="utf-8")
    assert re.search(r"const \[fillFailedWithMotion, setFillFailedWithMotion\] = useState\(false\)", preview_src)
    assert re.search(r"const renderParams = \{[^}]*\bfill_failed_with_motion\b[^}]*\}", preview_src)


def test_new_story_is_v2_quality_when_keys_present(monkeypatch, tmp_path, capsys):
    """Phase 7 stage 2a (the human's answer: "tier 2 + the quality preset
    when the keys are present"); re-pinned at stage 2c (DEC-235, "fal only"):
    a story created without a generation profile -- through the API route's
    helper or ``--ai-story new`` without any profile flag -- is a v2 story
    on the quality preset when Settings (or the CLI's environment) hold
    FAL_KEY alone; else today's default. An explicit profile is honoured as
    sent, and ``store.create``'s own default never moves."""
    from clipping.aistory import media_policy
    from clipping.aistory import store as story_store

    # Re-pinned on purpose (phase 7 follow-up, stage E; the human's choice of 2026-10-02: every clip
    # brings its own ambience): the preset is tier 3, its clips' sound kept as ambience under the lines.
    quality = {"tier": 3, "route": "api", "consistency_mode": "references", "budget_profile": "quality",
               "pipeline": "v2"}
    assert defaults.quality_generation_profile() == quality
    # Re-pinned on purpose (plan 22 stage 5: the manual mode is the default): with the keys, a new story
    # starts on the quality preset's v2 profile on the native_speech_manual budget profile (your own clips).
    manual = dict(quality, budget_profile="native_speech_manual")
    assert defaults.manual_speech_generation_profile() == manual
    for name in ("FAL_KEY", "GEMINI_PAID_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    # 1. the API route's helper, on the Settings values
    assert media_policy.new_story_profile({"FAL_KEY": "fk", "GEMINI_PAID_API_KEY": "pk"}) == manual
    assert media_policy.new_story_profile({"FAL_KEY": "fk"}) == manual  # re-pinned (DEC-235): FAL_KEY alone suffices
    assert media_policy.new_story_profile({"GEMINI_PAID_API_KEY": "pk"}) is None
    assert media_policy.new_story_profile({}) is None  # the no-key case, unchanged

    # 2. the CLI, on its own environment
    cli = importlib.import_module("clipping.aistory.cli")
    outputs = tmp_path / "outputs"
    stories = story_store.StoryStore(outputs, on_log=lambda *a: None)

    def created(*flags):
        assert cli.main(["new", "--lang", "fr", "--outputs-dir", str(outputs), *flags]) == 0
        newest = max(stories.list(), key=lambda entry: entry["created_at"] + entry["story_id"])
        return stories.get(newest["story_id"])["generation_profile"]

    assert created() == defaults.default_generation_profile()
    monkeypatch.setenv("FAL_KEY", "fk")
    assert created() == manual  # re-pinned (DEC-235): FAL_KEY alone is enough
    assert created("--tier", "1") == defaults.default_generation_profile()

    # 3. the store's own default is today's
    assert stories.create(language="fr", now="2026-10-01T10:00:00+00:00")["generation_profile"] == \
        defaults.default_generation_profile()


def test_a_v2_story_is_created_on_the_v2_episode_template(tmp_path):
    """Phase 7 stage 4 (DEC-227): ``store.create`` -- the API route's and the
    CLI's way in -- puts a v2 story (pipeline v2, the quality preset) on
    serial_60s_v2; any other profile, and no profile at all, keeps
    serial_60s_v1 (``defaults.EPISODE_TEMPLATE_ID``). The per-story 1080p
    switch is an optional profile key, validated like the others."""
    from clipping.aistory import store as story_store

    stories = story_store.StoryStore(tmp_path / "outputs", on_log=lambda *a: None)
    now = "2026-10-01T10:00:00+00:00"
    v2 = stories.create(language="fr", generation_profile=defaults.quality_generation_profile(), now=now)
    legacy = stories.create(language="fr", generation_profile={"budget_profile": "quality"}, now=now)
    default = stories.create(language="en", now=now)

    assert v2["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2 == "serial_60s_v2"
    assert legacy["episode_template_id"] == default["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID
    assert "video_resolution" not in v2["generation_profile"]
    hd = stories.create(language="fr", generation_profile=dict(defaults.quality_generation_profile(),
                                                                video_resolution="1080p"), now=now)
    assert hd["generation_profile"]["video_resolution"] == "1080p" and schemas.story_bible_errors(hd) == []
    try:
        stories.create(language="fr", generation_profile={"video_resolution": "4k"}, now=now)
    except ValueError as exc:
        assert "video_resolution" in str(exc)
    else:
        raise AssertionError("an unknown video_resolution must be refused")


def test_episode_template_for_reads_the_story_s_own_choice_first_then_the_pipeline(tmp_path):
    """Plan 20 stage 1: the story's own ``episode_template_id`` (sent on
    creation) when it names a shipped template, else the pipeline's default
    as before -- an unknown value never reaches the story (``store.create``
    refuses it: ValueError, the API's 400)."""
    from clipping.aistory import store as story_store

    v2 = defaults.quality_generation_profile()
    assert defaults.episode_template_for(None) == defaults.EPISODE_TEMPLATE_ID == "serial_60s_v1"
    assert defaults.episode_template_for(v2) == defaults.EPISODE_TEMPLATE_ID_V2
    assert defaults.episode_template_for(v2, None) == defaults.EPISODE_TEMPLATE_ID_V2
    assert defaults.episode_template_for(v2, "narrated_drama_60s_v2") == "narrated_drama_60s_v2"
    assert defaults.episode_template_for({}, "serial_90s_v2") == "serial_90s_v2"
    assert defaults.episode_template_for(v2, "serial_45s_v1") == defaults.EPISODE_TEMPLATE_ID_V2
    assert defaults.episode_template_for({}, "serial_45s_v1") == defaults.EPISODE_TEMPLATE_ID
    assert defaults.EPISODE_TEMPLATE_ID_NARRATED == "narrated_drama_60s_v2"
    assert defaults.EPISODE_TEMPLATE_ID_90_V2 == "serial_90s_v2"

    stories = story_store.StoryStore(tmp_path / "outputs", on_log=lambda *a: None)
    now = "2026-10-04T10:00:00+00:00"
    narrated = stories.create(language="fr", style_template_id="fruit_drama", generation_profile=v2,
                              episode_template_id="narrated_drama_60s_v2", now=now)
    assert narrated["episode_template_id"] == "narrated_drama_60s_v2"
    assert stories.get(narrated["story_id"]) == narrated and schemas.story_bible_errors(narrated) == []
    # The style alone never picks the format: the server takes what is sent.
    styled = stories.create(language="fr", style_template_id="fruit_drama", generation_profile=v2, now=now)
    assert styled["episode_template_id"] == defaults.EPISODE_TEMPLATE_ID_V2
    with pytest.raises(ValueError, match="serial_45s_v1"):
        stories.create(language="fr", episode_template_id="serial_45s_v1", now=now)
