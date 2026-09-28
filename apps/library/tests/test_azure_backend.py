"""B10: the Azure layout under concurrency and crashes (handoff 17.2), and proof that the
in-memory emulation behaves like Azure Storage (checked against Azurite when configured)."""
import os
import random
import shutil
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_contracts import validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.azure import AzureBlobs, AzureStore, AzureTables, Conflict, MemoryBlobs, MemoryTables  # noqa: E402
from bidoc_library.errors import LibraryError  # noqa: E402
from bidoc_library.store import SimulatedCrash  # noqa: E402

AZURITE = os.environ.get("BIDOC_AZURE_TEST_CONNECTION_STRING")


class Semantics:
    """What the store relies on from Table and Blob storage."""

    def make(self):
        return MemoryTables(), MemoryBlobs()

    def setUp(self):
        self.t, self.b = self.make()

    def test_create_is_create_if_absent(self):
        self.t.transact("p", [("create", {"RowKey": "a", "v": 1})])
        with self.assertRaises(Conflict):
            self.t.transact("p", [("create", {"RowKey": "a", "v": 2})])
        self.assertEqual(self.t.get("p", "a")["v"], 1)

    def test_replace_needs_the_current_etag(self):
        self.t.transact("p", [("create", {"RowKey": "a", "v": 1})])
        first = self.t.get("p", "a")
        self.t.transact("p", [("replace", {"RowKey": "a", "v": 2}, first["etag"])])
        with self.assertRaises(Conflict):
            self.t.transact("p", [("replace", {"RowKey": "a", "v": 3}, first["etag"])])
        self.assertEqual(self.t.get("p", "a")["v"], 2)

    def test_a_partition_transaction_is_all_or_nothing(self):
        self.t.transact("p", [("create", {"RowKey": "a", "v": 1})])
        stale = self.t.get("p", "a")["etag"]
        self.t.transact("p", [("replace", {"RowKey": "a", "v": 2}, stale)])
        with self.assertRaises(Conflict):
            self.t.transact("p", [("create", {"RowKey": "b", "v": 1}),
                                  ("replace", {"RowKey": "a", "v": 3}, stale)])
        self.assertIsNone(self.t.get("p", "b"))
        self.assertEqual(self.t.get("p", "a")["v"], 2)

    def test_query_by_prefix_is_ordered_and_partitioned(self):
        self.t.transact("p", [("create", {"RowKey": k}) for k in ("x:2", "x:1", "y:1")])
        self.t.transact("q", [("create", {"RowKey": "x:0"})])
        self.assertEqual([r["RowKey"] for r in self.t.query("p", "x:")], ["x:1", "x:2"])

    def test_blob_create_never_overwrites(self):
        self.assertTrue(self.b.create("a/b/document.html", b"one"))
        self.assertFalse(self.b.create("a/b/document.html", b"two"))
        self.assertEqual(self.b.read("a/b/document.html"), b"one")
        self.assertEqual(self.b.list("a/"), ["a/b/document.html"])


class MemorySemantics(Semantics, unittest.TestCase):
    pass


if AZURITE:
    class AzuriteSemantics(Semantics, unittest.TestCase):
        def make(self):
            name = "s" + uuid.uuid4().hex[:20]
            self.addCleanup(self._drop, name)
            return AzureTables.from_connection_string(AZURITE, name), AzureBlobs.from_connection_string(AZURITE, name)

        @staticmethod
        def _drop(name):
            from azure.data.tables import TableServiceClient
            from azure.storage.blob import BlobServiceClient
            TableServiceClient.from_connection_string(AZURITE).delete_table(name)
            BlobServiceClient.from_connection_string(AZURITE).delete_container(name)


class Stress(unittest.TestCase):
    """Several replicas publish competing revisions while some crash at random points."""

    REPLICAS, ROUNDS = 4, 6

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.tables, self.blobs = MemoryTables(), MemoryBlobs()
        self.factory = adf_factory(self.tmp / "factory")
        self.n = 0

    def artifact(self):
        self.n += 1
        r = generate(GenerateRequest(engine="adf", source_path=str(self.factory), source_kind="adf_git",
                                     output_dir=str(self.tmp / f"out{self.n}")))
        return Path(r.artifact_path).read_bytes()

    def test_competing_replicas_with_crashes_keep_the_catalogue_consistent(self):
        rng = random.Random(1234)
        base = AzureStore(self.tables, self.blobs)
        first = base.publish(self.artifact(), subject="setup", idempotency_key="first")
        doc_id = first["document_id"]
        outcomes = []
        for round_no in range(self.ROUNDS):
            etag = base.get_document(doc_id)["etag"]
            candidates = [self.artifact() for _ in range(self.REPLICAS)]
            faults = [rng.choice([(), (), ("after_prepare",), ("before_commit",), ("after_commit",)])
                      for _ in range(self.REPLICAS)]
            results = [None] * self.REPLICAS

            def attempt(i):
                store = AzureStore(self.tables, self.blobs, faults=faults[i])   # a replica
                try:
                    results[i] = ("ok", store.publish(candidates[i], subject=f"r{i}",
                                                      idempotency_key=f"{round_no}-{i}", expected_etag=etag))
                except SimulatedCrash as exc:
                    results[i] = ("crash", str(exc))
                except LibraryError as exc:
                    results[i] = (exc.code, None)
            threads = [threading.Thread(target=attempt, args=(i,)) for i in range(self.REPLICAS)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # A restart reconciles every crashed import; retries with the same key report the outcome.
            recovered = AzureStore(self.tables, self.blobs, cleanup_grace_seconds=0)
            for i, (kind, _) in enumerate(results):
                if kind == "crash":
                    try:
                        results[i] = ("ok", recovered.publish(candidates[i], subject=f"r{i}",
                                                              idempotency_key=f"{round_no}-{i}", expected_etag=etag))
                    except LibraryError as exc:
                        results[i] = (exc.code, None)
            winners = [r[1] for r in results if r[0] == "ok"]
            self.assertEqual(len(winners), 1, results)                  # exactly one revision per ETag
            self.assertTrue(all(r[0] in ("ok", "REVISION_CONFLICT") for r in results), results)
            self.assertEqual(recovered.get_document(doc_id)["current_revision_id"], winners[0]["revision_id"])
            outcomes.append(winners[0])
        self.check_invariants(AzureStore(self.tables, self.blobs), doc_id, 1 + self.ROUNDS, outcomes)

    def check_invariants(self, store, doc_id, expected_revisions, winners):
        seq = store.catalogue_sequence()
        events = self.tables.query("documents", "event:")
        self.assertEqual([e["sequence"] for e in events], list(range(1, seq + 1)))   # no gaps, no doubles
        history = store.list_revisions(doc_id)["items"]
        self.assertEqual(len(history), expected_revisions)
        self.assertEqual([h["revision_id"] for h in history][:len(winners)],
                         [w["revision_id"] for w in reversed(winners)])
        for h in history:                                      # every committed revision is readable
            validate_artifact(store.read_artifact(doc_id, h["revision_id"]))
        committed = {e["revision_id"] for e in self.tables.query("documents", "evrev:")}
        for imp in self.tables.query("imports", "imp:"):       # no import claims an uncommitted success
            if imp["state"] == "committed":
                self.assertIn(imp["revision_id"], committed)
            self.assertNotIn(imp["state"], ("prepared", "validating"))


if AZURITE:
    class AzuriteStress(Stress):
        """The same race and crash schedule through the real SDKs against Azurite."""

        ROUNDS = 3

        def setUp(self):
            super().setUp()
            name = "x" + uuid.uuid4().hex[:20]
            self.addCleanup(AzuriteSemantics._drop, name)
            self.tables = AzureTables.from_connection_string(AZURITE, name)
            self.blobs = AzureBlobs.from_connection_string(AZURITE, name)


if __name__ == "__main__":
    unittest.main()
