"""Storage operations per library action on the Azure layout (input to the B10 cost worksheet).

    python apps/library/tests/measure_azure_ops.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.azure import AzureStore, MemoryBlobs, MemoryTables  # noqa: E402


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        tables, blobs = MemoryTables(), MemoryBlobs()
        store = AzureStore(tables, blobs)

        def art(i):
            r = generate(GenerateRequest(engine="adf", source_path=str(adf_factory(tmp / f"f{i}")),
                                         source_kind="adf_git", output_dir=str(tmp / "out")))
            return Path(r.artifact_path).read_bytes()

        def measure(label, fn):
            t0, b0 = tables.calls, blobs.calls
            out = fn()
            print(f"{label:44} {tables.calls - t0:4} table  {blobs.calls - b0:3} blob")
            return out

        for i in range(49):
            store.publish(art(i), subject="s", idempotency_key=f"seed{i}")
        data = art(99)
        out = measure("publish a new document", lambda: store.publish(data, subject="s", idempotency_key="n"))
        measure("retry the same key (lost response)", lambda: store.publish(data, subject="s", idempotency_key="n"))
        measure("publish identical bytes (duplicate)", lambda: store.publish(data, subject="s", idempotency_key="d"))
        etag = store.get_document(out["document_id"])["etag"]
        new = art(99)
        measure("publish a new revision", lambda: store.publish(new, subject="s", idempotency_key="r",
                                                                expected_etag=etag))
        measure("get a document", lambda: store.get_document(out["document_id"]))
        measure("list documents (50 in catalogue)", lambda: store.list_documents())
        measure("list revisions", lambda: store.list_revisions(out["document_id"]))
        rev = store.get_document(out["document_id"])["current_revision_id"]
        measure("read an artifact", lambda: store.read_artifact(out["document_id"], rev))
        etag = store.get_document(out["document_id"])["etag"]
        measure("archive", lambda: store.archive(out["document_id"], etag, "s"))
        measure("startup reconcile (52 imports)", lambda: store.reconcile(startup=True))
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
