"""A01 on real data: publish the 29 real reports from pbi-tools/pbix-samples (shared
profile) into a library through the HTTP API, then for every report search one of its
measures and open that measure's viewer target and exact search section.

    python apps/library/tests/check_real_library.py SAMPLES
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

from starlette.testclient import TestClient

from bidoc_engines.generate import GenerateRequest, generate
from bidoc_library.api import create_app
from bidoc_library.config import Settings

SECRET = "check-real-library"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}
EXPECTED = 29


def main(samples):
    folders = sorted(p for p in Path(samples, "powerbi-desktop-samples").glob("*/*") if (p / "Model").is_dir())
    failures, checked = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        app = create_app(Settings(local_data_dir=tmp / "data"), session_secret=SECRET)
        c = TestClient(app, base_url="http://127.0.0.1:8765")
        published = {}
        started = time.perf_counter()
        for i, folder in enumerate(folders):
            work = tmp / "src" / folder.name
            shutil.copytree(folder, work)
            r = generate(GenerateRequest(engine="power_bi", source_path=str(work), source_kind="extracted",
                                         output_dir=str(tmp / "out"), profile="shared", environment="Production"))
            if r.status != "completed":
                failures.append(f"{folder.name}: generate {r.errors}")
                continue
            up = c.post("/api/v1/imports", headers={**MUTATE, "Idempotency-Key": f"real-{i}"},
                        files={"file": ("doc.html", Path(r.artifact_path).read_bytes(), "text/html")})
            if up.status_code != 201:
                failures.append(f"{folder.name}: import {up.status_code} {up.text[:300]}")
                continue
            published[folder.name] = up.json()
        print(f"published {len(published)} real reports in {time.perf_counter() - started:.1f} s")

        for name, out in published.items():
            doc = out["document_id"]
            objects = c.get(f"/api/v1/documents/{doc}/objects", params={"limit": 1000}).json()
            measures = [o for o in objects["items"] if o["kind"] == "measure" and o["view"]]
            if not measures:
                continue
            m = measures[0]
            hits = c.get("/api/v1/search", params={"q": m["label"], "limit": 200}).json()["items"]
            mine = [h for h in hits if h["document_id"] == doc]
            if not mine:
                failures.append(f"{name}: searching measure {m['label']!r} does not find its document")
                continue
            if m["view"]["view_id"] != "pbi.measure":
                failures.append(f"{name}: measure {m['label']!r} has no measure viewer target")
                continue
            if not any(h["section_id"] == m["section_id"] for h in mine):
                failures.append(f"{name}: searching {m['label']!r} does not offer the measure's own section")
                continue
            view = c.get(f"/api/v1/documents/{doc}/revisions/{out['revision_id']}/view")
            if view.status_code != 200 or f'"{m["view"]["view_id"]}"'.encode() not in view.content:
                failures.append(f"{name}: viewer for {m['label']!r} does not register {m['view']['view_id']}")
                continue
            checked += 1
    if len(folders) < EXPECTED:
        failures.append(f"only {len(folders)} extracted reports found; expected {EXPECTED}")
    if checked < 20:
        failures.append(f"only {checked} reports had a searchable, openable measure")
    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print(f"A01: {len(published)} real reports published; {checked} found by a measure name and opened at it.")


if __name__ == "__main__":
    main(sys.argv[1])
