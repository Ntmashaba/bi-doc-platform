"""Shared test path setup.

In CI the real adf-doc-gen package is installed from components/adf (see
requirements.txt); the /tmp fallback only exists for offline sandboxes where
the sibling component cannot be pip-installed.
"""

import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1]
if str(COMPONENT) not in sys.path:
    sys.path.insert(0, str(COMPONENT))

try:
    import adfdocgen  # noqa: F401
except ImportError:  # pragma: no cover - offline sandbox only
    vendored = Path("/tmp/sqlagent/libs")
    if vendored.exists():
        sys.path.insert(0, str(vendored))
