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
