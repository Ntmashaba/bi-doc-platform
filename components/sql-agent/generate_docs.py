"""Generate the native analysis payload for a folder of job scripts.

Thin wrapper mirroring the other engine components' generate_docs.py:
loads the input, runs the analysis, writes the JSON payload.

    python generate_docs.py <file.sql|folder> -o payload.json
    python generate_docs.py <file.sql|folder> --coverage-only
"""

from __future__ import annotations

import sys

from sqlagentdocgen.cli import main

if __name__ == "__main__":
    sys.exit(main())
