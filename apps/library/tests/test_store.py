"""Local catalogue and crash-safe publication (handoff B04; A02, A04, A05, A06, A10, A14, A34)."""
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import ADF_MARKERS, adf_factory, pbi_model  # noqa: E402

from bidoc_contracts import PLACEHOLDER, Limits, embed_manifest, locate_manifest, validate_artifact  # noqa: E402
from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402
from bidoc_library.errors import LibraryError  # noqa: E402
from bidoc_library.migrations import MIGRATIONS  # noqa: E402
from bidoc_library.store import LocalStore, SimulatedCrash  # noqa: E402


def rewrite(artifact: bytes, **changes) -> bytes:
    """A validly re-hashed copy of an artifact with manifest fields changed."""
    loc = locate_manifest(artifact)
    manifest = json.loads(loc.body)
    for key, value in changes.items():
        parts = key.split("__")
        node = manifest
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    page = artifact[:loc.start] + PLACEHOLDER + artifact[loc.end:]
    return embed_manifest(page, manifest)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.factory = adf_factory(self.tmp / "factory")
        self.store = LocalStore(self.tmp / "data")
        self.keys = iter(range(10_000))

    def generate(self, source=None, **kw) -> bytes:
        r = generate(GenerateRequest(engine="adf", source_path=str(source or self.factory), source_kind="adf_git",
                                     output_dir=str(self.tmp / "out"), **kw))
        self.assertEqual(r.status, "completed", r.errors)
        return Path(r.artifact_path).read_bytes()

    def publish(self, data, store=None, key=None, **kw):
        return (store or self.store).publish(data, subject="alice", idempotency_key=key or f"k{next(self.keys)}", **kw)

    def assertError(self, code, fn, *args, **kw):
        with self.assertRaises(LibraryError) as ctx:
            fn(*args, **kw)
        self.assertEqual(ctx.exception.code, code, ctx.exception.message)
        return ctx.exception


class Publication(StoreTest):
    def test_new_document_is_committed_readable_and_reprojected(self):
        local = self.generate()                                     # local artifact: SQL included
        self.assertIn(ADF_MARKERS["sql_literal"].encode(), local)
        out = self.publish(local)
        self.assertEqual((out["state"], out["duplicate"], out["catalogue_sequence"]), ("committed", False, 1))
        self.assertEqual(out["indexing_state"], "pending")
        stored = self.store.read_artifact(out["document_id"], out["revision_id"])
        manifest = validate_artifact(stored)
        self.assertEqual(manifest["projection"]["profile"], "shared")      # the importer never trusts labels
        self.assertEqual(manifest["projection"]["options"], {"query_code": "withheld"})
        for marker in ADF_MARKERS.values():
            self.assertNotIn(marker.encode(), stored)
        self.assertEqual(manifest["revision_id"], out["revision_id"])
        doc = self.store.get_document(out["document_id"])
        self.assertEqual((doc["current_revision_id"], doc["document_type"]), (out["revision_id"], "adf"))
        self.assertEqual(self.store.list_documents()["items"][0]["document_id"], out["document_id"])

    def test_query_code_can_be_included_but_never_restored(self):
        included = self.store.read_artifact(**{k: v for k, v in self.publish(self.generate(), query_code="included").items()
                                               if k in ("document_id", "revision_id")})
        self.assertIn(ADF_MARKERS["sql_literal"].encode(), included)
        self.assertNotIn(ADF_MARKERS["inline_password"].encode(), included)
        other = adf_factory(self.tmp / "other")
        shared = self.generate(other, profile="shared")
        out = self.publish(shared, query_code="included")
        stored = validate_artifact(self.store.read_artifact(out["document_id"], out["revision_id"]))
        self.assertEqual(stored["projection"]["options"], {"query_code": "withheld"})

    def test_power_bi_artifacts_publish_too(self):
        model = pbi_model(self.tmp / "pbi")
        r = generate(GenerateRequest(engine="power_bi", source_path=str(model), source_kind="bim",
                                     output_dir=str(self.tmp / "out")))
        out = self.publish(Path(r.artifact_path).read_bytes())
        self.assertEqual(self.store.get_document(out["document_id"])["document_type"], "power_bi")

    def test_new_revision_needs_current_etag_and_keeps_history(self):   # A02
        first = self.publish(self.generate())
        doc = self.store.get_document(first["document_id"])
        second_bytes = self.generate()
        self.assertError("PRECONDITION_REQUIRED", self.publish, second_bytes)
        self.assertError("REVISION_CONFLICT", self.publish, second_bytes, expected_etag="stale")
        second = self.publish(second_bytes, expected_etag=doc["etag"])
        self.assertEqual(second["document_id"], first["document_id"])
        history = self.store.list_revisions(first["document_id"])["items"]
        self.assertEqual([h["revision_id"] for h in history], [second["revision_id"], first["revision_id"]])
        for h in history:
            validate_artifact(self.store.read_artifact(first["document_id"], h["revision_id"]))
        self.assertEqual(self.store.get_document(first["document_id"])["current_revision_id"], second["revision_id"])

    def test_duplicates_and_idempotency(self):                           # A04
        data = self.generate()
        first = self.publish(data, key="same")
        self.assertEqual(self.publish(data, key="same"), first)             # lost response: same outcome
        again = self.publish(data)                                          # identical bytes, new key
        self.assertTrue(again["duplicate"])
        self.assertEqual((again["revision_id"], again["catalogue_sequence"]),
                         (first["revision_id"], first["catalogue_sequence"]))
        self.assertEqual(self.store.catalogue_sequence(), 1)
        self.assertEqual(len(self.store.list_revisions(first["document_id"])["items"]), 1)
        self.assertError("IDEMPOTENCY_KEY_REUSED", self.publish, self.generate(), key="same")

    def test_same_revision_different_bytes_conflicts(self):
        data = self.generate()
        out = self.publish(data)
        forged = rewrite(data, description="changed")
        self.assertError("REVISION_BYTES_CONFLICT", self.publish, forged,
                         expected_etag=self.store.get_document(out["document_id"])["etag"])

    def test_concurrent_updates_one_wins(self):                          # A05
        first = self.publish(self.generate())
        etag = self.store.get_document(first["document_id"])["etag"]
        candidates = [self.generate(), self.generate()]
        results = []

        def attempt(data, key):
            # one store shared by both threads, as in the library process (single instance)
            try:
                results.append(("ok", self.store.publish(
                    data, subject="bob", idempotency_key=key, expected_etag=etag)["revision_id"]))
            except LibraryError as exc:
                results.append((exc.code, None))
            except Exception as exc:  # noqa: BLE001 - surface anything unexpected in the assertion
                results.append((repr(exc), None))

        threads = [threading.Thread(target=attempt, args=(d, f"c{i}")) for i, d in enumerate(candidates)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(r[0] for r in results), ["REVISION_CONFLICT", "ok"])
        winner = next(r[1] for r in results if r[0] == "ok")
        self.assertEqual(self.store.get_document(first["document_id"])["current_revision_id"], winner)
        self.assertEqual(len(self.store.list_revisions(first["document_id"])["items"]), 2)


class Identity(StoreTest):
    def test_stream_and_type_are_immutable(self):
        data = self.generate()
        out = self.publish(data)
        etag = self.store.get_document(out["document_id"])["etag"]
        moved = rewrite(self.generate(), publication__environment_key="test")
        self.assertError("STREAM_IDENTITY_CONFLICT", self.publish, moved, expected_etag=etag)
        twin = rewrite(self.generate(), document_id="00000000-0000-4000-8000-000000000001")
        self.assertError("STREAM_IDENTITY_CONFLICT", self.publish, twin)
        retyped = rewrite(self.generate(), document_type="power_bi")
        self.assertIn(self.assertError("CONTRACT_INVALID", self.publish, retyped, expected_etag=etag).status, (422,))

    def test_invalid_and_unsupported_artifacts(self):                    # A10 (HTML profile)
        data = self.generate()
        self.assertError("CONTRACT_INVALID", self.publish, data.replace(b"Loads daily sales.", b"Loads daily sales!"))
        self.assertError("UNSUPPORTED_SAFE_PROJECTION", self.publish,
                         rewrite(data, projection__native_schema="adf-doc-gen/9"))
        small = LocalStore(self.tmp / "small", limits=Limits(html_bytes=1000))
        self.assertEqual(self.assertError("PAYLOAD_TOO_LARGE", self.publish, data, store=small).status, 413)
        self.assertEqual(self.store.list_documents()["items"], [])
        self.assertFalse(any((self.tmp / "data" / "artifacts").rglob("*.html")))


class CrashRecovery(StoreTest):                                          # A06, A34
    def crash(self, point, data, key="k-crash", **kw):
        store = LocalStore(self.tmp / "data", faults={point})
        with self.assertRaises(SimulatedCrash):
            store.publish(data, subject="alice", idempotency_key=key, **kw)
        return LocalStore(self.tmp / "data", cleanup_grace_seconds=0)   # restart after the grace period

    def test_recent_interrupted_work_is_left_alone(self):
        data = self.generate()
        store = LocalStore(self.tmp / "data", faults={"after_artifact_write"})
        with self.assertRaises(SimulatedCrash):
            store.publish(data, subject="alice", idempotency_key="young")
        LocalStore(self.tmp / "data")                                    # second process, default grace
        self.assertTrue(any((self.tmp / "data" / "artifacts").rglob("document.html")))
        self.assertEqual(self.assertError("IMPORT_INCOMPLETE", self.publish, data, key="young").status, 409)

    def test_crash_after_artifact_write(self):
        data = self.generate()
        store = self.crash("after_artifact_write", data)
        self.assertEqual(store.list_documents()["items"], [])
        self.assertFalse(any((self.tmp / "data" / "artifacts").rglob("*.html")))   # orphan removed
        self.assertError("IMPORT_INTERRUPTED", self.publish, data, store=store, key="k-crash")
        self.assertEqual(self.publish(data, store=store)["state"], "committed")

    def test_crash_after_prepare_or_inside_commit_recovers_on_restart(self):
        for point in ("after_prepare", "before_commit"):
            with self.subTest(point=point):
                source = adf_factory(self.tmp / point)
                data = self.generate(source)
                store = self.crash(point, data, key=point)
                out = self.publish(data, store=store, key=point)          # retry recovers the outcome
                self.assertEqual(out["state"], "committed")
                self.assertEqual(store.get_document(out["document_id"])["current_revision_id"], out["revision_id"])

    def test_crash_after_commit_does_not_hide_the_publication(self):
        data = self.generate()
        store = self.crash("after_commit", data)
        docs = store.list_documents()["items"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(self.publish(data, store=store, key="k-crash")["revision_id"], docs[0]["current_revision_id"])

    def test_intervening_commit_is_never_overwritten(self):
        first = self.publish(self.generate())
        etag = self.store.get_document(first["document_id"])["etag"]
        late, winner = self.generate(), self.generate()
        crashing = LocalStore(self.tmp / "data", faults={"after_prepare"})
        with self.assertRaises(SimulatedCrash):
            crashing.publish(late, subject="alice", idempotency_key="late", expected_etag=etag)
        won = self.publish(winner, expected_etag=etag)                    # same store object: no reconcile
        store = LocalStore(self.tmp / "data")                             # restart reconciles "late"
        self.assertEqual(store.get_document(first["document_id"])["current_revision_id"], won["revision_id"])
        self.assertError("REVISION_CONFLICT", self.publish, late, store=store, key="late", expected_etag=etag)
        history = [h["revision_id"] for h in store.list_revisions(first["document_id"])["items"]]
        self.assertEqual(history, [won["revision_id"], first["revision_id"]])
        late_rev = validate_artifact(late)["revision_id"]
        self.assertError("NOT_FOUND", store.read_artifact, first["document_id"], late_rev)
        self.assertError("REVISION_CONFLICT", self.publish, late, store=store)        # byte duplicate of a conflict


class CatalogueOperations(StoreTest):
    def test_archive_restore_and_filters(self):
        a = self.publish(self.generate(tags=("sales",), owner="Data team", environment="Production"))
        b_source = adf_factory(self.tmp / "b")
        b = self.publish(self.generate(b_source, title="Another factory"))
        doc = self.store.get_document(a["document_id"])
        self.assertEqual(self.store.list_documents(tag="sales")["items"][0]["document_id"], a["document_id"])
        self.assertEqual(len(self.store.list_documents(owner="Data team")["items"]), 1)
        self.assertEqual(self.store.list_documents(q="another")["items"][0]["document_id"], b["document_id"])
        self.assertError("PRECONDITION_REQUIRED", self.store.archive, a["document_id"], None, "alice")
        archived = self.store.archive(a["document_id"], doc["etag"], "alice")
        self.assertNotIn(a["document_id"], [d["document_id"] for d in self.store.list_documents()["items"]])
        self.assertEqual(self.store.list_documents(archived=True)["items"][0]["document_id"], a["document_id"])
        self.assertError("REVISION_CONFLICT", self.store.restore, a["document_id"], doc["etag"], "alice")
        self.store.restore(a["document_id"], archived["etag"], "alice")
        self.assertEqual(len(self.store.list_documents()["items"]), 2)
        self.assertEqual(self.store.catalogue_sequence(), 4)

    def test_pagination(self):
        for i in range(5):
            self.publish(self.generate(adf_factory(self.tmp / f"f{i}"), title=f"Factory {i}"))
        page = self.store.list_documents(limit=2)
        seen = [d["title"] for d in page["items"]]
        while page["next_cursor"]:
            page = self.store.list_documents(limit=2, cursor=page["next_cursor"])
            seen += [d["title"] for d in page["items"]]
        self.assertEqual(seen, [f"Factory {i}" for i in range(5)])
        self.assertError("INVALID_REQUEST", self.store.list_documents, cursor="%%%")

    def test_backup_and_restore(self):                                   # A14
        out = self.publish(self.generate())
        report = self.store.backup(self.tmp / "backup")
        self.assertEqual(report, {"catalogue_sequence": 1, "artifacts": 1})
        restored = LocalStore(self.tmp / "backup")
        self.assertEqual(restored.get_document(out["document_id"]), self.store.get_document(out["document_id"]))
        self.assertEqual(restored.read_artifact(out["document_id"], out["revision_id"]),
                         self.store.read_artifact(out["document_id"], out["revision_id"]))

    def test_corrupted_artifact_is_detected(self):
        out = self.publish(self.generate())
        path = next((self.tmp / "data" / "artifacts").rglob("document.html"))
        path.write_bytes(path.read_bytes() + b" ")
        self.assertEqual(self.assertError("ARTIFACT_CORRUPT", self.store.read_artifact, out["document_id"],
                                          out["revision_id"]).status, 500)

    def test_newer_schema_is_refused(self):
        conn = sqlite3.connect(self.tmp / "data" / "catalogue.sqlite3")
        conn.execute("INSERT INTO schema_migrations VALUES (?, 'future', 'now')", (MIGRATIONS[-1][0] + 1,))
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            LocalStore(self.tmp / "data")


if __name__ == "__main__":
    unittest.main()
