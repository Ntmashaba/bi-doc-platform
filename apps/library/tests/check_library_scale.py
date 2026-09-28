"""A41-style measurement: publish real documents into one library and measure costs.

    python apps/library/tests/check_library_scale.py PBIX_SAMPLES ADF_TEMPLATES
(the same pinned checkouts used by packages/engines/tests/check_*.py)
"""
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

from bidoc_engines.generate import GenerateRequest, generate
from bidoc_library.derived import Derived
from bidoc_library.search import search
from bidoc_library.store import LocalStore


def main(pbix_samples, adf_repo):
    inputs = [("power_bi", "extracted", p) for p in sorted(Path(pbix_samples, "powerbi-desktop-samples").glob("*/*"))
              if (p / "Model").is_dir()]
    inputs += [("adf", "adf_arm", p) for p in sorted(Path(adf_repo, "templates").iterdir()) if p.is_dir()]
    with tempfile.TemporaryDirectory() as tmp:
        store, publish_times = LocalStore(Path(tmp) / "library"), []
        derived = Derived(store)
        for i, (engine, kind, src) in enumerate(inputs):
            work = Path(tmp) / "src" / f"{i}-{src.name}"
            shutil.copytree(src, work)
            r = generate(GenerateRequest(engine=engine, source_path=str(work), source_kind=kind,
                                         output_dir=str(Path(tmp) / "out"), environment="Production"))
            data = Path(r.artifact_path).read_bytes()
            t = time.monotonic()
            store.publish(data, subject="scale", idempotency_key=f"k{i}")
            publish_times.append(time.monotonic() - t)
        t = time.monotonic()
        derived.refresh()
        rebuild = time.monotonic() - t
        index = derived.search_index()
        size = len(json.dumps(index, separators=(",", ":")).encode())
        t = time.monotonic()
        for q in ("sales", "customer revenue", "copy data", "date", "pipeline trigger"):
            search(index["documents"], q)
        search_ms = (time.monotonic() - t) / 5 * 1000
        rels = derived.relationships(index["documents"][0]["document_id"])
    print(json.dumps({"documents": len(inputs), "publish_seconds_median": round(statistics.median(publish_times), 3),
                      "publish_seconds_max": round(max(publish_times), 3), "full_rebuild_seconds": round(rebuild, 3),
                      "search_index_bytes": size, "bytes_per_document": size // len(inputs),
                      "python_search_ms_per_query": round(search_ms, 1),
                      "relationship_generation": rels["generation"]["state"]}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
