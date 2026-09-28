"""Generate local and shared artifacts for the 29 real reports pre-extracted in
pbi-tools/pbix-samples, validate every artifact, and check the shared ones hold no
query code while DAX and source locations survive.

    git clone https://github.com/pbi-tools/pbix-samples SAMPLES
    git -C SAMPLES checkout 84a442248b7259de7f5dbac0e25f0c33e3c9fd6a
    python packages/engines/tests/check_real_pbix_samples.py SAMPLES OUTPUT_DIR
"""
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

from bidoc_contracts import validate_artifact
from bidoc_engines.generate import GenerateRequest, generate
from bidoc_engines.power_bi import VIEW_IDS

EXPECTED = 29
CODE = [re.compile(p) for p in (r"let\\n\s+Source\s*=", r"Sql\.Database\(", r"Excel\.Workbook\(",
                                 r'Binary\.FromText\(\\?"[A-Za-z0-9+/=]{8}', r"Web\.Contents\(", r"(?i)select [^<]{0,80} from ")]


def main(samples, out_dir):
    folders = sorted(p for p in Path(samples, "powerbi-desktop-samples").glob("*/*") if (p / "Model").is_dir())
    failures, sizes, kept_dax = [], {"local": [], "shared": []}, 0
    with tempfile.TemporaryDirectory() as tmp:
        for folder in folders:
            work = Path(tmp) / folder.name
            shutil.copytree(folder, work)
            for profile in ("local", "shared"):
                r = generate(GenerateRequest(engine="power_bi", source_path=str(work), source_kind="extracted",
                                             output_dir=out_dir, profile=profile))
                if r.status != "completed":
                    failures.append(f"{folder.name} {profile}: {r.errors}")
                    continue
                data = Path(r.artifact_path).read_bytes()
                manifest = validate_artifact(data, view_ids=VIEW_IDS)
                sizes[profile].append((len(data), sum(len(s["text"].encode()) for s in manifest["sections"])))
                if profile == "shared":
                    text = data.decode("utf-8")
                    leaks = [p.pattern for p in CODE if p.search(text)]
                    if leaks:
                        failures.append(f"{folder.name}: query code in shared artifact: {leaks}")
                    kept_dax += sum(1 for t in manifest["native_payload"]["data"].get("tableSources", [])
                                    if t.get("query") and t["query"] != "[query code withheld]")
    for profile, s in sizes.items():
        if s:
            print(f"{profile}: {len(s)} artifacts; HTML median {statistics.median(x for x, _ in s):,.0f} B, "
                  f"max {max(x for x, _ in s):,} B; search text median {statistics.median(y for _, y in s):,.0f} B, "
                  f"max {max(y for _, y in s):,} B")
    print(f"DAX calculated-table queries kept in shared artifacts: {kept_dax}")
    if len(folders) < EXPECTED:
        failures.append(f"only {len(folders)} extracted reports found; expected {EXPECTED}")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"All {len(folders)} reports: valid local and shared artifacts; no query code in shared output.")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "real-pbix-output")
