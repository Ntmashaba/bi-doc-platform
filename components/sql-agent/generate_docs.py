"""Generate documentation from a folder of scripted SQL Server Agent jobs.

Thin wrapper mirroring the other engine components' generate_docs.py:
loads the input, runs the analysis, writes the requested outputs.

    python generate_docs.py <file.sql|folder>                      # JSON to stdout
    python generate_docs.py <file.sql|folder> -o payload.json      # JSON to file
    python generate_docs.py <file.sql|folder> --html docs.html --also json,word,csv,agent
    python generate_docs.py <file.sql|folder> --coverage-only
"""

from __future__ import annotations

import sys

from sqlagentdocgen.cli import main

if __name__ == "__main__":
    sys.exit(main())
