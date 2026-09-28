"""Generate local and shared artifacts for every template in Microsoft's
Azure-DataFactory repository, validate them, and check shared output holds no SQL.

    git clone --filter=blob:none --sparse https://github.com/Azure/Azure-DataFactory REPO
    git -C REPO sparse-checkout set templates
    git -C REPO checkout ce4c9cab41e5e4fa66f4bddd042cab649c36a4ca
    python packages/engines/tests/check_adf_templates.py REPO OUTPUT_DIR
"""
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

from bidoc_contracts import validate_artifact
from bidoc_engines.adf import VIEW_IDS
from bidoc_engines.generate import GenerateRequest, generate

EXPECTED = 95
SQL = re.compile(r"(?i)\b(select\s[^<]{0,80}\sfrom|insert\s+into|delete\s+from|truncate\s+table)\b")


def main(repo, out_dir):
    templates = sorted(p for p in Path(repo, "templates").iterdir() if p.is_dir())
    failures, sizes, local_sql = [], [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for template in templates:
            work = Path(tmp) / template.name
            shutil.copytree(template, work)
            for profile in ("local", "shared"):
                r = generate(GenerateRequest(engine="adf", source_path=str(work), source_kind="adf_arm",
                                             output_dir=out_dir, profile=profile))
                if r.status != "completed":
                    failures.append(f"{template.name} {profile}: {r.status} {r.errors}")
                    continue
                data = Path(r.artifact_path).read_bytes()
                validate_artifact(data, view_ids=VIEW_IDS)
                sizes.append(len(data))
                found = SQL.search(data.decode("utf-8"))
                if profile == "shared" and found:
                    failures.append(f"{template.name}: SQL in shared artifact: {found.group(0)[:60]!r}")
                local_sql += bool(profile == "local" and found)
    if sizes:
        print(f"{len(sizes)} artifacts; HTML median {statistics.median(sizes):,.0f} B, max {max(sizes):,} B; "
              f"local artifacts with SQL kept: {local_sql}")
    if len(templates) < EXPECTED:
        failures.append(f"only {len(templates)} templates found; expected {EXPECTED}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"All {len(templates)} templates: valid local and shared artifacts; no SQL in shared output.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "adf-template-output")
