"""The subtitle look in the dashboard (plan 23 stage B5): text contracts (CI
has no node). The style step keeps its font, highlight and mode controls and
gains size, position, outline and box controls (editable after the style
lock, which only freezes the template-level controls); the font becomes a
select of the shipped families; the episode page's Preview pane gets a
"Subtitles" panel with an approximate CSS preview of one line and a "Render
again" button wired to the existing re-render; ``api.js`` gets
``patchSubtitleStyle``. The values the pages send are the schema's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"
EDITOR = STORY / "SubtitleStyleEditor.jsx"
HELPERS = STORY / "subtitleStyle.js"
STYLE_STEP = STORY / "steps" / "StyleStep.jsx"
PREVIEW = STORY / "episode" / "PreviewPane.jsx"
API = SRC / "api.js"
CSS = SRC / "index.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_font_list_is_the_shipped_families_in_the_fonts_index_order():
    families = [entry["family"] for entry in json.loads(
        (ROOT / "assets" / "fonts" / "fonts_index.json").read_text(encoding="utf-8"))["fonts"]]
    src = _read(HELPERS)
    match = re.search(r"export const SUBTITLE_FONT_FAMILIES = \[(.*?)\]", src)
    assert match, "SUBTITLE_FONT_FAMILIES not found"
    assert re.findall(r"'([^']+)'", match.group(1)) == families


def test_the_ranges_are_the_schemas():
    from clipping.aistory import schemas

    src = _read(HELPERS)
    for name, (low, high) in (("SIZE_PCT", schemas.SUBTITLE_SIZE_PCT), ("POSITION_PCT", schemas.SUBTITLE_POSITION_PCT),
                              ("OUTLINE_PX", schemas.SUBTITLE_OUTLINE_PX),
                              ("BOX_OPACITY_PCT", schemas.SUBTITLE_BOX_OPACITY_PCT)):
        assert f"export const {name} = {{ min: {low}, max: {high} }}" in src, name
    # what the render draws with no override (render/subtitles.py)
    from clipping.aistory.render import subtitles

    assert f"word_pop: {{ size: {subtitles.WORD_POP_FONT_SIZE}, position: 77.5, outline: {subtitles.WORD_POP_OUTLINE_PX}" in src
    assert f"two_line: {{ size: {subtitles.TWO_LINE_FONT_SIZE}, position: 82, outline: {subtitles.TWO_LINE_OUTLINE_PX}" in src


def test_the_object_the_pages_send_has_the_schemas_keys_and_nothing_else():
    from clipping.aistory import schemas

    src = _read(HELPERS)
    body = src[src.index("export function styleFromForm"):src.index("function rgba")]
    sent = set(re.findall(r"style\.([a-z_]+) =", body))
    assert sent == set(schemas.SUBTITLE_STYLE_SCHEMA["properties"])
    assert "style.box = { colour: form.boxColour, opacity_pct: Number(form.boxOpacity) }" in body
    assert "return Object.keys(style).length ? style : null" in body  # an untouched form clears the look


def test_the_editor_has_every_control_a_preview_and_the_save_and_clear_actions():
    src = _read(EDITOR)
    for control in ('id="subtitle-font-family"', 'id="subtitle-size"', 'id="subtitle-position"',
                    'id="subtitle-text-colour"', 'id="subtitle-highlight-colour"', 'id="subtitle-outline-px"',
                    'id="subtitle-outline-colour"', 'aria-label="Box behind the text"', 'id="subtitle-box-colour"',
                    'id="subtitle-box-opacity"'):
        assert control in src, control
    assert "SUBTITLE_FONT_FAMILIES.map" in src and "<select" in src
    assert "previewCss(form, mode)" in src and 'aria-label="Approximate subtitle preview"' in src
    assert "await patchSubtitleStyle(storyId, body)" in src and "save(null)" in src and "Save subtitles" in src
    # a box replaces the outline: its controls are disabled with it, and the editor says so
    assert src.count("disabled={disabled || form.boxOn}") == 2 and "Replaces the outline" in src
    # the style lock's freeze never disables it
    assert "locked" not in src


def test_the_preview_is_a_css_line_with_the_outline_as_a_stroke_and_the_box_as_a_background():
    src = _read(HELPERS)
    assert "WebkitTextStroke" in src and "css.background = rgba(form.boxColour" in src
    assert "css.bottom = `${100 - position}%`" in src and "css.top = `${position}%`" in src


def test_the_style_step_keeps_its_controls_and_gains_the_look_after_the_lock():
    src = _read(STYLE_STEP)
    for kept in ('id="style-font-family"', 'aria-label="Highlight colour"', 'id="style-subtitle-mode"'):
        assert kept in src, kept
    # the font is a select of the shipped families (a template's own unshipped family stays pickable)
    assert re.search(r'<select\s+id="style-font-family"', src)
    assert "SUBTITLE_FONT_FAMILIES.map" in src and "(template font)" in src
    # the new controls: not disabled by the lock
    assert "<SubtitleStyleEditor" in src and "showFont={false}" in src
    block = src[src.index("<SubtitleStyleEditor"):]
    block = block[:block.index("/>")]
    assert "locked" not in block and "story={story}" in block and "styleLock={styleLock}" in block


def test_the_preview_pane_has_a_subtitles_panel_with_render_again_on_the_existing_rerender():
    src = _read(PREVIEW)
    assert "function SubtitlesPanel" in src and '<CardHeader title="Subtitles" />' in src
    assert "<SubtitleStyleEditor" in src and "<SubtitlesPanel storyId={storyId}" in src
    panel = src[src.index("function SubtitlesPanel"):src.index("// ------------------------------------------------------- changes / re-render")]
    assert "runStoryStep(storyId, 'rerender', { ep })" in panel and "'Render again'" in panel
    assert "disabled={busy || running || !hasRender || dirty}" in panel
    # the panel sits right under the rendered video
    assert src.index("<RenderMedia episode") < src.index("<SubtitlesPanel storyId") < src.index("<ChangesSinceRender")


def test_the_api_client_patches_the_subtitle_style_route_with_the_object_or_null():
    src = _read(API)
    body = src[src.index("export async function patchSubtitleStyle"):]
    body = body[:body.index("\n}\n")]
    assert "`/stories/${storyId}/subtitle-style`" in body and "method: 'PATCH'" in body
    assert "body: JSON.stringify(style)" in body


def test_the_panel_has_its_own_styles():
    src = _read(CSS)
    assert ".story-subtitle-style-grid" in src and ".story-subtitle-preview-frame" in src
