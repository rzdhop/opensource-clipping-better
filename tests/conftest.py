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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _forget_model_swaps():
    """A model swap is remembered per key for the life of the process
    (DEC-077). Tests share one process, so none may inherit another's swap."""
    from clipping.providers import llm

    llm.reset_model_fallbacks()
    yield
    llm.reset_model_fallbacks()
