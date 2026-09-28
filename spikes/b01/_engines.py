"""Locate the pinned engine checkouts (siblings of this repo, or PBI_DOC_GEN / ADF_DOC_GEN)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PBI = Path(os.environ.get("PBI_DOC_GEN", ROOT / "pbi-doc-gen"))
ADF = Path(os.environ.get("ADF_DOC_GEN", ROOT / "adf-doc-gen"))
for p in (PBI, ADF):
    if not p.is_dir():
        sys.exit(f"engine checkout not found: {p}")
    sys.path.insert(0, str(p))
