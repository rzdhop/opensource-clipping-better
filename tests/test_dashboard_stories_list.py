"""The stories list page (dashboard overhaul stage 2, DEC-254), read as text.

No JS test runner (DEC-012): these pin what the page must keep doing --
the same two API calls and the delete behind the kit's danger dialog, the
card fields of ``GET /api/stories`` it reads, and the cover fetched with the
auth header (``fetchStoryCoverUrl``), never put in an ``<img src>`` as an API
path, which cannot carry the header.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
PAGE = SRC / "pages" / "story" / "StoriesList.jsx"
API_JS = SRC / "api.js"
MENU = SRC / "ui" / "Menu.jsx"
FORMAT = SRC / "lib" / "format.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_the_page_keeps_its_api_calls_and_the_delete_behind_the_danger_dialog():
    src = _read(PAGE)
    imported = re.search(r"import \{([^}]*)\} from '\.\./\.\./api'", src)
    assert imported, "StoriesList does not import from ../../api"
    for name in ("fetchStories", "deleteStory", "fetchStyles", "fetchStoryCoverUrl"):
        assert name in imported.group(1), name
    handler = src.split("const handleDelete = async", 1)[1].split("\n  }\n", 1)[0]
    assert "await confirm({" in handler and "tone: 'danger'" in handler
    assert "title: `Delete \"${title || 'Untitled story'}\"?`" in handler
    assert "message: 'This cannot be undone.'" in handler
    assert "if (!confirmed) return" in handler
    assert handler.index("if (!confirmed) return") < handler.index("await deleteStory(storyId)")


def test_the_page_reads_the_card_fields_of_the_list_payload():
    src = _read(PAGE)
    for field in ("story.cover", "story.progress", "story.episodes", "story.style_label", "story.pipeline",
                  "progress.steps_done", "progress.steps_total", "progress.next", "latest.state"):
        assert field in src, field
    # Every step id and episode state the backend sends has a label.
    for step in ("concepts", "bible", "style", "cast", "places", "season", "knowledge"):
        assert re.search(rf"^\s*{step}: '", src, re.MULTILINE), step
    for state in ("draft", "written", "planned", "assets", "rendered"):
        assert re.search(rf"^\s*{state}: '", src, re.MULTILINE), state


def test_the_cover_is_fetched_with_the_auth_header_never_used_as_an_img_src():
    src = _read(PAGE)
    assert "fetchStoryCoverUrl(story.cover)" in src
    assert "URL.revokeObjectURL" in src
    assert "src={story.cover}" not in src
    api = _read(API_JS)
    body = api.split("export async function fetchStoryCoverUrl(", 1)[1].split("\n}\n", 1)[0]
    assert "await request(cover)" in body
    # Only a story media path is fetched.
    assert r"/^\/stories\/[0-9a-f]{12}\/media\//" in body


def test_the_card_states_keep_the_empty_state_text_and_the_new_story_action():
    src = _read(PAGE)
    assert 'title="No stories yet"' in src
    assert "'Start a persistent story workspace — a world, a cast and a style lock that ' +" in src
    assert src.count('<Button as={Link} to="/story/new" variant="primary" icon={Sparkles}>New story</Button>') == 2
    assert "<Skeleton" in src


def test_the_overflow_menu_is_a_labelled_menu_button():
    src = _read(MENU)
    assert 'aria-haspopup="menu"' in src and "aria-expanded={open}" in src
    assert 'role="menu"' in src and 'role="menuitem"' in src
    assert "event.key === 'Escape'" in src
    # Focus goes back to the trigger before the action runs (a dialog then returns it there).
    choose = src.split("const choose = (item) => {", 1)[1].split("\n  }\n", 1)[0]
    assert choose.index("close()") < choose.index("item.onSelect()")


def test_the_relative_time_helper_is_exported():
    src = _read(FORMAT)
    assert "export function formatRelativeTime(value, now = Date.now())" in src
    for literal in ("'just now'", "min ago", "h ago", "d ago"):
        assert literal in src, literal
