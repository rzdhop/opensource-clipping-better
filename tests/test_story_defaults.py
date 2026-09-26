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
