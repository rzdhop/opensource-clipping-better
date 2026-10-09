"""showrunner stands alone: nothing in it imports the Clips app (``clipping``), the web API or the deleted
rzdhop-story server. Stdlib only."""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = ("import clipping", "from clipping", "import web", "from web", "import mcp_server", "from mcp_server")


def test_nothing_in_showrunner_imports_clipping_web_or_the_old_server():
    offenders = []
    for dirpath, _, filenames in os.walk(ROOT):
        for f in filenames:
            if f.endswith(".py"):
                path = os.path.join(dirpath, f)
                with open(path, encoding="utf-8") as fh:
                    for k, line in enumerate(fh, 1):
                        if line.lstrip().startswith(FORBIDDEN):
                            offenders.append(f"{os.path.relpath(path, ROOT)}:{k}")
    assert offenders == []
