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


def _studio_modules():
    return [n for n in sys.modules if n == "clipping.studio" or n.startswith("clipping.studio.")]


@pytest.fixture
def render_stack_stubbed(monkeypatch):
    """Mock modules for the render layer's heavy imports; restored afterwards.

    clipping.studio is imported fresh against the mocks and discarded after, in
    both directions: a real one imported earlier is not reused (it would hold
    the real cv2), and a mocked one does not outlive the test (it would hand
    MagicMocks to whatever imports clipping.studio next).
    """
    from unittest import mock

    import clipping

    for name in _RENDER_STACK:
        monkeypatch.setitem(sys.modules, name, mock.MagicMock(name=name))
    saved = {name: sys.modules.pop(name) for name in _studio_modules()}
    had_attr = hasattr(clipping, "studio")
    old_attr = getattr(clipping, "studio", None)
    if had_attr:
        delattr(clipping, "studio")
    yield
    for name in _studio_modules():
        del sys.modules[name]
    sys.modules.update(saved)
    if had_attr:
        clipping.studio = old_attr
    elif hasattr(clipping, "studio"):
        delattr(clipping, "studio")


@pytest.fixture(autouse=True)
def _forget_model_swaps():
    """A model swap is remembered per key for the life of the process
    (DEC-087). Tests share one process, so none may inherit another's swap."""
    from clipping.providers import llm

    llm.reset_model_fallbacks()
    yield
    llm.reset_model_fallbacks()
