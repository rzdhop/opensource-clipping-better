"""Pytest bootstrap.

Adds the repo root to sys.path so `import clipping...` works without an
editable install, and exposes the fixtures directory.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# The render layer imports cv2, mediapipe, numpy, PIL, requests and yt_dlp at
# module level. None of them is installed in the pytest-only CI environment
# (DEC-012), so a test that needs to *load* a studio module -- not render with
# it -- replaces them with mocks for its own duration.
_RENDER_STACK = (
    "cv2", "mediapipe", "mediapipe.tasks", "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision", "numpy", "PIL", "PIL.Image",
    "PIL.ImageDraw", "PIL.ImageFont", "PIL.ImageFilter", "requests", "yt_dlp",
)


import pytest  # noqa: E402  (kept below the path bootstrap above)


@pytest.fixture
def render_stack_stubbed(monkeypatch):
    """Mock modules for the render layer's heavy imports; restored afterwards."""
    from unittest import mock

    for name in _RENDER_STACK:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock(name=name))
    yield
