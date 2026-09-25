"""Two modes, one app (DEC-094).

Every dashboard page lives under `/clips` or `/story`; Settings is shared by
both modes at `/settings`; the paths the product shipped with (`/`, `/new`,
`/job/<id>`) still open the right page through a client-side redirect, because
they are what bookmarks and the PC helper's printed link carry. The last mode
used is remembered, and the switch stays reachable on a phone, where the
sidebar is hidden.

Text tests: they read the sources, so they run in CI without node.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "dashboard" / "src"
APP = SRC / "App.jsx"
CSS = SRC / "index.css"

MODE_ROOTS = ("/clips", "/story")
SHARED = ("/settings",)

# `to="/x"`, `to={`/x/${id}`}`, `navigate('/x')`, `navigate(`/x/${id}`)`
LINK = re.compile(r"""(?:\bto=|\bnavigate\()\s*[{(]?\s*[`'"](/[^`'"]*)""")
ROUTE = re.compile(r'<Route\s+path="([^"]+)"')


def jsx_files():
    return sorted(SRC.rglob("*.jsx"))


def test_every_link_target_lives_under_a_mode_or_is_shared():
    offenders = []
    for path in jsx_files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in LINK.finditer(line):
                target = match.group(1)
                if target.startswith(MODE_ROOTS) or target in SHARED:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {target}")
    assert offenders == [], "links outside /clips, /story or /settings:\n" + "\n".join(offenders)


def test_the_router_declares_both_modes_the_shared_page_and_the_old_paths():
    declared = set(ROUTE.findall(APP.read_text(encoding="utf-8")))
    for path in ("/", "/clips", "/clips/new", "/clips/job/:jobId", "/settings",
                 "/story", "/story/*", "/new", "/job/:jobId", "*"):
        assert path in declared, path


def test_the_old_paths_redirect_instead_of_rendering():
    app = APP.read_text(encoding="utf-8")
    assert re.search(r'path="/new"\s+element=\{<Navigate to="/clips/new" replace', app)
    assert "to={`/clips/job/${" in app, "/job/:jobId must keep the id when it redirects"
    assert "readMode()" in app, "/ must open the last mode used"


def test_the_last_mode_used_is_remembered():
    switch = (SRC / "components" / "ModeSwitch.jsx").read_text(encoding="utf-8")
    assert "localStorage" in switch
    assert "'rzc_mode'" in switch
    app = APP.read_text(encoding="utf-8")
    assert "rememberMode(" in app, "App writes the mode whenever the location changes"


def test_the_mode_switch_is_reachable_on_a_phone():
    """Under 768 px the sidebar is display: none (pre-existing), so the switch
    also lives in a top bar that only appears there."""
    css = CSS.read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 768px)"):]
    assert re.search(r"\.sidebar\s*\{\s*display:\s*none", mobile)
    assert re.search(r"\.mobile-topbar\s*\{\s*display:\s*none", css.split("@media (max-width: 768px)")[0])
    assert re.search(r"\.mobile-topbar\s*\{[^}]*display:\s*flex", mobile)
    app = APP.read_text(encoding="utf-8")
    assert app.count("<ModeSwitch") == 2, "once in the sidebar, once in the mobile top bar"


def test_the_pc_helper_prints_the_new_deep_link():
    src = (ROOT / "tools" / "rzclips-fetch.py").read_text(encoding="utf-8")
    assert "/clips/job/" in src
    assert "}/job/{" not in src
