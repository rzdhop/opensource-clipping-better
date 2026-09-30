"""The dashboard never shows a sign-in screen on a server that needs no token
(DEC-173; the human, 2026-09-29: no auth on the app, ever).

The sidebar's "Sign out" used to render on every server and set the app to
signed out, which renders ``Login`` -- one click on an open server (the
sidebar shows from 768 px up) produced exactly the sign-in screen the human
ruled out. Now the button renders only while a token is stored (a deployment
that set ``API_TOKEN``), and signing out asks the server again: an open
server answers "in", so ``Login`` is never reached there.

Text contracts over ``web/dashboard/src/App.jsx``, like the other dashboard
tests. Stdlib + pytest (DEC-012).
"""

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src" / "App.jsx"


def _app():
    return APP.read_text(encoding="utf-8")


def _sign_out_function(text):
    match = re.search(r"const signOut = \(\) => \{(.*?)\n  \}", text, re.S)
    assert match, "App.jsx defines signOut"
    return match.group(1)


def test_the_sign_out_button_renders_only_while_a_token_is_stored():
    text = _app()
    button = text.index("Sign out")
    guard = text.rfind("{getToken() && (", 0, button)
    assert guard != -1, "the Sign out button must be wrapped in {getToken() && (...)}"
    # The guard is the button's own: no other JSX block closes between them.
    assert text.count(")}", guard, button) == 0


def test_signing_out_asks_the_server_again_and_never_forces_the_login_screen():
    body = _sign_out_function(_app())
    assert "clearToken()" in body
    assert "setAuth('checking')" in body
    assert "setAuth('out')" not in body


def test_login_is_rendered_only_after_the_server_refused():
    text = _app()
    # 'out' is set only from the server's answer to checkToken (or its failure).
    outs = [m.start() for m in re.finditer(r"setAuth\('out'\)|setAuth\(ok \? 'in' : 'out'\)", text)]
    check = text.index("checkToken(getToken())")
    effect_end = text.index("}, [auth])", check)
    assert outs and all(check < pos < effect_end for pos in outs)
