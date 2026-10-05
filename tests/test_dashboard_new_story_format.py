"""The new-story form's "Episode format" (plan 20 stage 1, the fruit-drama
pack).

- ``pages/story/episodeTemplates.js`` holds the one list of episode formats
  (the new-story form and the episode page's "Episode length" both read it):
  each entry's id is a shipped template, its label names the template's own
  window, its ``pipeline`` is the one the template is shaped for, and it has
  a one-line ``help``.
- ``NewStoryWizard.jsx`` renders an "Episode format" select over that list,
  pre-filled from the chosen style's suggestion
  (``episode_defaults.episode_template_id`` of ``GET /api/stories/styles``)
  when it fits the pipeline, else the pipeline's default
  (``defaults.episode_template_for``), shows the format's help line, and
  sends ``episode_template_id`` -- the user's pick, else the style's
  suggestion, else null (the server's default).

Text contracts over the sources (DEC-012: stdlib + pytest only, no JS
runner), in the style of tests/test_dashboard_switch_pipeline.py; the
createFields key itself is checked against ``StoryCreateRequest`` by
tests/test_story_payload_contract.py.
"""

from __future__ import annotations

import pathlib
import re

from clipping.aistory import defaults, templates

ROOT = pathlib.Path(__file__).resolve().parents[1]
STORY_SRC = ROOT / "web" / "dashboard" / "src" / "pages" / "story"
FORMATS = STORY_SRC / "episodeTemplates.js"
WIZARD = STORY_SRC / "NewStoryWizard.jsx"


def _read(path):
    return path.read_text(encoding="utf-8")


def _formats() -> list:
    src = _read(FORMATS)
    match = re.search(r"export const EPISODE_TEMPLATES = \[(.*?)\]\n", src, re.DOTALL)
    assert match, "EPISODE_TEMPLATES not found in episodeTemplates.js"
    entries = re.findall(r"\{ id: '([a-z0-9_]+)', label: '([^']+)', pipeline: '([a-z0-9]*)',\s*help: '([^']+)' \}",
                         match.group(1))
    assert len(entries) == len(defaults.EPISODE_TEMPLATE_IDS)  # non-vacuity: every entry was read
    return [dict(zip(("id", "label", "pipeline", "help"), entry)) for entry in entries]


def test_each_format_is_a_shipped_template_labelled_with_its_window_and_pipeline():
    formats = _formats()
    assert [f["id"] for f in formats] == list(defaults.EPISODE_TEMPLATE_IDS)
    for fmt in formats:
        tpl = templates.load_episode_template(fmt["id"])
        lo, hi = tpl["window_s"]
        assert f"({lo:g}–{hi:g})" in fmt["label"], fmt
        # A v2 format is shaped for beat shots (max_shot_s), a legacy one is not.
        assert fmt["pipeline"] == ("v2" if "max_shot_s" in tpl else ""), fmt
        assert fmt["help"].endswith(".") and len(fmt["help"]) <= 80, fmt


def test_the_narrated_drama_says_what_it_is_in_one_line():
    narrated = next(f for f in _formats() if f["id"] == defaults.EPISODE_TEMPLATE_ID_NARRATED)
    assert narrated["help"] == "Narrated drama: one dramatic narrator, 2–4 character lines."
    tpl = templates.load_episode_template(narrated["id"])
    assert tpl["character_lines"] == [2, 4]


def test_the_pipeline_default_and_the_style_suggestion_mirror_the_backend():
    src = _read(FORMATS)
    assert (f"return pipeline === 'v2' ? '{defaults.EPISODE_TEMPLATE_ID_V2}' : '{defaults.EPISODE_TEMPLATE_ID}'"
            in src)
    body = src.split("export function styleSuggestedTemplate(style, pipeline) {", 1)[1].split("\n}\n", 1)[0]
    assert "style.episode_defaults.episode_template_id" in body
    # Only a suggestion shaped for the story's pipeline is taken.
    assert "format.pipeline === (pipeline || '')" in body


def test_the_wizard_renders_the_episode_format_select_and_sends_the_choice():
    src = _read(WIZARD)
    # Re-pinned on purpose (plan 22 stage 3): the wizard also imports the native-speech profile's suggestion
    # (tests/test_story_confrontation_template.py).
    assert ("import {\n  EPISODE_TEMPLATES, pipelineDefaultTemplate, profileSuggestedTemplate, styleSuggestedTemplate,\n"
            "} from './episodeTemplates'") in src
    assert '<label className="form-label" htmlFor="new-story-episode-format">Episode format</label>' in src
    select = src.split('id="new-story-episode-format"', 1)[1].split("</select>", 1)[0]
    # Re-pinned on purpose (plan 28 stage A4/S1): under Advanced, the select lists only the formats the server's
    # oracle says fit (offer.formats_that_fit), after "Let the app choose"; a hidden one is named with its reason.
    assert "value={formatChoice}" in select
    assert "onChange={(e) => setEpisodeTemplateChoice(e.target.value)}" in select
    assert '<option value="">Let the app choose (recommended)</option>' in select
    assert "formatOptions.map((tpl) => <option key={tpl.id} value={tpl.id}>{tpl.label}</option>)" in select
    assert "const formatOptions = EPISODE_TEMPLATES.filter((tpl) => fitIds.includes(tpl.id))" in src
    assert "const formatChoice = fitIds.includes(episodeTemplateChoice) ? episodeTemplateChoice : ''" in src
    assert "Not offered: {tpl.label}, {hiddenReasons[tpl.id]}" in src
    assert "{episodeFormat ? episodeFormat.help : ''}" in src
    # A voiced story keeps the style's suggestion; a story whose characters speak in their own clips sends none
    # (the server picks one that fits). Re-pinned on purpose (plan 28 stage A4).
    assert "const suggestedTemplate = nativeSpeech ? null : styleSuggestedTemplate(chosenStyle, pipeline)" in src
    # Sent: the pick or the suggestion; else null, so the server picks.
    create = re.search(r"const createFields = \{(.*?)\n      \}", src, re.DOTALL).group(1)
    assert "        episode_template_id: formatChoice || suggestedTemplate || null," in create
