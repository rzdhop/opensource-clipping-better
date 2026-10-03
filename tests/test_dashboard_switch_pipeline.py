"""The Visual tier card's "Regenerate on v2" button (the human, 2026-10-02:
"make a Regen button for episodes when: 'This story cannot move to the v2
(quality) pipeline: episode 1 already has a script ...'").

- ``api.js``: an ``ApiError`` carries the refusal's ``code`` and its whole
  ``detail`` object (``PATCH``'s structured 409 names the episodes);
  ``switchPipeline(storyId, body)`` is ``POST /stories/{id}/switch-pipeline``
  and sends what ``StorySwitchPipelineRequest`` declares.
- ``GenerationProfileCard``: a save refused with 409 and
  ``workflow.PIPELINE_SWITCH_HAS_SCRIPTS`` shows the sentence and a
  "Regenerate episode N on v2" button whose confirm says what is archived,
  what is kept and what runs next; a refused save puts the selects back on
  the server's values and refreshes the story (they kept showing values the
  server never saved); the hint says what the steps really do.

Text contracts over the sources (DEC-012: stdlib + pytest only, no JS
runner), in the style of tests/test_dashboard_phase7_editing.py.
"""

from __future__ import annotations

import ast
import pathlib
import re

from clipping.aistory import workflow

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
API_JS = SRC / "api.js"
WIZARD = SRC / "pages" / "story" / "NewStoryWizard.jsx"
MODELS = ROOT / "web" / "api" / "models.py"


def _read(path):
    return path.read_text(encoding="utf-8")


def _class_fields(name):
    tree = ast.parse(_read(MODELS))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)}
    raise AssertionError(name)


def _function(src, name):
    """The source of ``function <name>(`` up to the next top-level function."""
    start = src.index(f"function {name}(")
    following = re.search(r"\n(?:export default |export async |export )?(?:async )?function \w+\(", src[start + 1:])
    return src[start:start + 1 + following.start()] if following else src[start:]


# ------------------------------------------------------------------ api.js

def test_an_api_error_carries_the_refusals_code_and_its_detail():
    src = _read(API_JS)
    parse = _function(src, "parseDetail")
    assert "detail.code" in parse
    error = src[src.index("export class ApiError"):]
    error = error[:error.index("\n}\n")]
    assert "this.code = code" in error and "this.detail = detail" in error
    build = _function(src, "apiError")
    assert "code" in build and "detail" in build
    # A plain string detail is still exactly its message (every existing caller).
    assert "if (detail != null) return { message: String(detail)" in parse


def test_switch_pipeline_posts_to_its_route():
    src = _read(API_JS)
    body = _function(src, "switchPipeline")
    assert body.startswith("function switchPipeline(storyId, body)")
    assert "`/stories/${storyId}/switch-pipeline`" in body and "method: 'POST'" in body
    assert "throw await apiError(res," in body and "JSON.stringify(body)" in body


def test_the_card_sends_what_the_switch_request_declares():
    src = _read(WIZARD)
    sites = re.findall(r"switchPipeline\(\s*storyId,\s*\{([^}]*)\}\s*\)", src)
    assert sites, "no switchPipeline(storyId, {...}) call in NewStoryWizard.jsx"
    for site in sites:
        keys = set(re.findall(r"([a-z_]+)\s*:", site))
        assert keys == _class_fields("StorySwitchPipelineRequest"), keys
        assert "regenerate_episodes: true" in site


# ------------------------------------------------------------------ the card

def test_the_card_offers_to_regenerate_the_episodes_the_refusal_names():
    src = _read(WIZARD)
    card = _function(src, "GenerationProfileCard")
    constant = re.search(r"const PIPELINE_SWITCH_HAS_SCRIPTS = '([a-z_]+)'", src)
    assert constant and constant.group(1) == workflow.PIPELINE_SWITCH_HAS_SCRIPTS
    assert "err.status === 409" in card and "err.code === PIPELINE_SWITCH_HAS_SCRIPTS" in card
    assert "err.detail.episodes" in card
    # The button names the episodes: "Regenerate episode 1 on v2", "Regenerate episodes 1–3 on v2".
    label = _function(src, "episodesLabel")
    assert "`episode ${" in label and "–" in label
    assert "Regenerate ${episodesLabel(" in src
    # Asked first, with what goes, what stays and what runs next.
    # The kit's confirm dialog (useConfirm, DEC-253) carries regenerateConfirm's text as its message.
    assert re.search(r"await confirm\(\{[\s\S]{0,300}?message: regenerateConfirm\(switchOffer\.episodes, switchOffer\.patch\)", card)
    confirm = _function(src, "regenerateConfirm")
    for words in ("script, storyboard, images, clips and render", "cast, places, props, season and music",
                  "dossier and look", "knowledge base", "estimate first"):
        assert words in confirm, words
    # Done: the story is fetched again, and what happened is said (the queued step or why it could not start).
    regenerate = card[card.index("const regenerate = async"):]
    regenerate = regenerate[:regenerate.index("\n  }\n")]
    assert "onChange()" in regenerate
    summary = _function(src, "switchedSummary")
    assert "next_step" in summary and "refused" in summary and "discarded" in summary


def test_a_refused_save_shows_the_servers_values_again_and_refreshes():
    card = _function(_read(WIZARD), "GenerationProfileCard")
    save = card[card.index("const save = async"):]
    save = save[:save.index("\n  }\n")]
    failed = save[save.index("catch (err)"):]
    for setter in ("setTier(profile.tier)", "setRoute(profile.route)", "setBudgetProfile(profile.budget_profile)"):
        assert setter in failed, setter
    assert "onChange()" in failed
    # And whenever the story is fetched again, the selects follow what the server holds.
    assert re.search(r"\}, \[profile\.tier, profile\.route, profile\.budget_profile\]\)", card)


def test_the_hint_says_what_the_steps_really_do():
    card = _function(_read(WIZARD), "GenerationProfileCard")
    hint = card[card.index("canSwitchToV2\n"):card.index("Animate every shot")]
    assert "Places & props" in hint and "drawn before" in hint
    assert "redraws the sheets (its estimate shows the cost)" not in hint
