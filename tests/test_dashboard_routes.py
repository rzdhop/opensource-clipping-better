"""The dashboard's routes: Clips only.

Every dashboard page lives under `/clips`, with Settings at `/settings`; the
paths the product shipped with (`/`, `/new`, `/job/<id>`) still open the
right page through a client-side redirect, because they are what bookmarks
and the PC helper's printed link carry. The old AI Story pages (`/story...`)
are gone, and an unknown path lands on the job list. The navigation stays
reachable on a phone, where the sidebar is hidden.

Text tests: they read the sources, so they run in CI without node.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
APP = SRC / "App.jsx"
CSS = SRC / "index.css"

ROOTS = ("/clips",)
SHARED = ("/settings",)

# `to="/x"`, `to={`/x/${id}`}`, `navigate('/x')`, `navigate(`/x/${id}`)`
LINK = re.compile(r"""(?:\bto=|\bnavigate\()\s*[{(]?\s*[`'"](/[^`'"]*)""")
ROUTE = re.compile(r'<Route\s+path="([^"]+)"')


def jsx_files():
    return sorted(SRC.rglob("*.jsx"))


def test_every_link_target_lives_under_clips_or_is_shared():
    offenders = []
    for path in jsx_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in LINK.finditer(line):
                target = match.group(1).split("#", 1)[0].split("?", 1)[0]
                if target.startswith(ROOTS) or target in SHARED:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {target}")
    assert offenders == [], "links outside /clips or /settings:\n" + "\n".join(offenders)


def test_the_router_declares_the_clips_pages_the_settings_and_the_old_paths():
    declared = set(ROUTE.findall(APP.read_text(encoding="utf-8")))
    for path in ("/", "/clips", "/clips/new", "/clips/job/:jobId", "/settings",
                 "/new", "/job/:jobId", "*"):
        assert path in declared, path
    assert not [path for path in declared if path.startswith("/story")], declared


def test_the_old_paths_redirect_instead_of_rendering():
    app = APP.read_text(encoding="utf-8")
    assert re.search(r'path="/new"\s+element=\{<Navigate to="/clips/new" replace', app)
    assert "to={`/clips/job/${" in app, "/job/:jobId must keep the id when it redirects"
    assert re.search(r'path="/"\s+element=\{<Navigate to="/clips" replace', app)
    assert re.search(r'path="\*"\s+element=\{<Navigate to="/clips" replace', app)


def test_no_mode_switch_is_left():
    app = APP.read_text(encoding="utf-8")
    assert "ModeSwitch" not in app
    assert "readMode" not in app and "rememberMode" not in app
    assert not (SRC / "components" / "ModeSwitch.jsx").exists()
    assert not (SRC / "pages" / "story").exists()


def test_the_navigation_is_reachable_on_a_phone():
    """Under 768 px the sidebar is display: none, so a top bar that only
    appears there carries the menu button that opens it as a drawer."""
    css = CSS.read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 768px)"):]
    assert re.search(r"\.sidebar\s*\{\s*display:\s*none", mobile)
    assert re.search(r"\.mobile-topbar\s*\{\s*display:\s*none", css.split("@media (max-width: 768px)")[0])
    assert re.search(r"\.mobile-topbar\s*\{[^}]*display:\s*flex", mobile)
    app = APP.read_text(encoding="utf-8")
    assert 'className="mobile-topbar"' in app
    assert 'aria-controls="app-sidebar"' in app


def test_the_pc_helper_prints_the_new_deep_link():
    src = (ROOT / "tools" / "rzclips-fetch.py").read_text(encoding="utf-8")
    assert "/clips/job/" in src
    assert "}/job/{" not in src
