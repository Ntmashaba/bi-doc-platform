"""Run the pre-extracted reports in pbi-tools/pbix-samples through the PBIX
batch's load, link and render path.

Usage:
    git clone https://github.com/pbi-tools/pbix-samples SAMPLES
    git -C SAMPLES checkout 84a442248b7259de7f5dbac0e25f0c33e3c9fd6a
    python tests/samples/check_pbix_samples.py SAMPLES

Covers every pbi-tools model layout seen in practice (folder with table.json,
early <T>.json with .xml measures and dataSources/, legacy mashup sources) and
legacy report layouts. Fails if a report does not load or render, if a source is
Unknown, or if fewer reports than expected are found.
"""
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pbidocgen.linker import link  # noqa: E402
from pbidocgen.pbix_batch import load_extracted  # noqa: E402
from pbidocgen.renderer import build_payload, render_html  # noqa: E402

EXPECTED = 29


def main(root):
    folders = sorted(p for p in Path(root, "powerbi-desktop-samples").glob("*/*") if (p / "Model").is_dir())
    failures, sources = [], Counter()
    with tempfile.TemporaryDirectory() as tmp:
        for folder in folders:
            work = Path(tmp) / folder.name
            shutil.copytree(folder, work)  # the loader writes an assembled model beside the extract
            try:
                model, report = load_extracted(work, work.with_suffix(".pbix"), True)
                payload = build_payload(model, report, link(model, report) if model else None, folder.name)
                render_html(payload, Path(tmp) / (folder.name + ".html"))
                rows = payload["primarySources"]["rows"]
                sources.update(r["sourceType"] for r in rows)
                if not model["tables"]:
                    failures.append(f"{folder.name}: no tables")
                if any(r["sourceType"] == "Unknown" for r in rows):
                    failures.append(f"{folder.name}: Unknown source")
                print(f"ok   {folder.name}: {len(model['tables'])} tables, {len(rows)} source rows")
            except Exception as exc:  # report every failure, not just the first
                failures.append(f"{folder.name}: {type(exc).__name__}: {exc}")
                print(f"FAIL {folder.name}: {exc}")
    print(dict(sources))
    if len(folders) < EXPECTED:
        failures.append(f"only {len(folders)} extracted reports found; expected {EXPECTED}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"All {len(folders)} extracted reports loaded and rendered.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pbix-samples-src")
