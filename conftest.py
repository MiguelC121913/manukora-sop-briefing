"""Ensures the repo root is on sys.path so tests can `import src.<module>`
regardless of how pytest is invoked (bare `pytest`, `python -m pytest`, or
from a different working directory).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
