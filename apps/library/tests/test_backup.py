"""Backup and restore into a clean deployment (A14, A28) on every backend."""
import json
import shutil
import sys
import unittest
from pathlib import Path

from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_derived import MUTATE, SECRET, DerivedTest  # noqa: E402

from bidoc_contracts import Limits  # noqa: E402
from bidoc_library import backup as b  # noqa: E402
from bidoc_library.api import create_app  # noqa: E402
from bidoc_library.config import Settings  # noqa: E402


class BackupRestore(DerivedTest):
    def restored_store(self):
        return None                                          # LocalStore in a new data folder

    def build(self):
        adf, pbi = self.publish_both()
        body = {"source_document_id": adf["document_id"], "source_revision_id": adf["revision_id"],
                "source_object_id": "adf:pipeline:PL_Load", "target_document_id": pbi["document_id"],
                "target_revision_id": pbi["revision_id"], "target_object_id": "pbi:table:Sales",
                "expected_catalogue_sequence": self.c.get("/api/v1/search-index").json()["generation"],
                "kind": "related_to", "reason": "Checked with the data team."}
        r = self.c.post("/api/v1/relationships/manual", json=body, headers=MUTATE)
        if r.status_code != 201:                              # object IDs differ: link the documents instead
            body.pop("target_object_id")
            r = self.c.post("/api/v1/relationships/manual", json=body, headers=MUTATE)
        self.assertEqual(r.status_code, 201, r.text)
        self.rid = r.json()["relationship_id"]
        doc = f"/api/v1/documents/{adf['document_id']}"
        etag = self.c.get(f"{doc}/metadata").headers["ETag"]
        r = self.c.patch(f"{doc}/metadata", json={"owner": "Data team", "reason": "Ownership"},
                         headers={**MUTATE, "If-Match": etag})
        self.assertEqual(r.status_code, 200, r.text)
        self.first_generation = self.rel(pbi["document_id"])["generation"]["generation_id"]
        self.publish(self.artifact("adf", self.factory, "adf_git"), etag=self.etag(adf["document_id"]))
        return adf, pbi

    def observe(self, c, adf, pbi):
        """Everything a reader can see: catalogue, history, bytes, metadata, relationships, audit."""
        out = {"docs": c.get("/api/v1/documents").json()}
        for d in (adf, pbi):
            base = f"/api/v1/documents/{d['document_id']}"
            revs = c.get(f"{base}/revisions").json()["items"]
            out[d["document_id"]] = {
                "doc": c.get(base).json(), "revisions": revs,
                "bytes": [c.get(f"{base}/revisions/{r['revision_id']}/download").content for r in revs],
                "metadata": c.get(f"{base}/metadata/history").json(),
                "current": c.get(f"{base}/relationships").json(),
                "by_revision": c.get(f"{base}/relationships", params={"revision_id": d["revision_id"]}).json(),
                "pinned": c.get(f"{base}/relationships", params={"generation_id": self.first_generation}).json()}
        out["audit"] = c.get(f"/api/v1/relationships/manual/{self.rid}/audit").json()
        out["search"] = c.get("/api/v1/search-index").json()
        return json.loads(json.dumps(out, default=lambda v: v.hex()).replace('"request_id"', '"_"'),
                          object_hook=lambda o: {k: v for k, v in o.items() if k != "_"})

    def test_restore_into_clean_deployment(self):                         # A14, A28
        adf, pbi = self.build()
        before = self.observe(self.c, adf, pbi)
        self.assertEqual(before[pbi["document_id"]]["pinned"]["generation"]["state"], "pinned")
        self.assertTrue(before[pbi["document_id"]]["current"]["manual"])
        dest = self.tmp / "backup"
        report = b.backup(self.app.state.store, dest)
        self.assertEqual(report["catalogue_sequence"], before["search"]["generation"])
        self.assertEqual(report["counts"]["committed_revisions"], 3)
        index = b.verify(dest)
        self.assertEqual(index["format"], "bidoc-backup/1")

        settings = Settings(local_data_dir=self.tmp / "restored")
        checked = b.restore(dest, settings=settings, limits=Limits(), store=self.restored_store())
        self.assertEqual((checked["documents"], checked["revisions"]), (2, 3))
        store = self.restored_store_opened or None
        app = create_app(settings, store=store, session_secret=SECRET)
        after = self.observe(TestClient(app, base_url="http://127.0.0.1:8765"), adf, pbi)
        for key in ("current", "by_revision", "pinned"):                     # the pinned generation keeps its ID
            for d in (adf, pbi):
                self.assertEqual(after[d["document_id"]][key], before[d["document_id"]][key])
        self.assertEqual(after, before)

    def test_damaged_or_misused_backups_are_refused(self):
        self.build()
        dest = self.tmp / "backup"
        b.backup(self.app.state.store, dest)
        with self.assertRaisesRegex(b.BackupError, "not empty"):
            b.backup(self.app.state.store, dest)
        victim = next(f for f in json.loads((dest / "backup.json").read_text())["files"]
                      if "artifacts/" in f["path"])
        path = dest / victim["path"]
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(b.BackupError, "checksum"):
            b.verify(dest)
        path.write_bytes(original)
        (dest / "stray.txt").write_text("x")
        with self.assertRaisesRegex(b.BackupError, "unexpected files"):
            b.verify(dest)
        (dest / "stray.txt").unlink()
        target = self.restored_store()
        if target is None:
            occupied = self.tmp / "occupied"
            shutil.copytree(self.tmp / "data", occupied)
            with self.assertRaisesRegex(b.BackupError, "not empty"):
                b.restore(dest, settings=Settings(local_data_dir=occupied), limits=Limits())
        else:
            target.blobs.create("x", b"x")
            with self.assertRaisesRegex(b.BackupError, "not empty"):
                b.restore(dest, settings=Settings(), limits=Limits(), store=target)
        with self.assertRaisesRegex(b.BackupError, "backup; the configured backend"):
            wrong = None if target is not None else self.other_backend()
            b.restore(dest, settings=Settings(local_data_dir=self.tmp / "w"), limits=Limits(), store=wrong)

    def other_backend(self):
        from bidoc_library.azure import AzureStore, MemoryBlobs, MemoryTables
        return AzureStore(MemoryTables(), MemoryBlobs())

    restored_store_opened = None


class CommandLine(DerivedTest):
    def test_backup_verify_restore_check(self):
        import contextlib
        import io
        import os
        from unittest import mock
        from bidoc_library.__main__ import main
        self.publish_both()

        def run(*args, data):
            out = io.StringIO()
            with mock.patch.dict(os.environ, {"LOCAL_DATA_DIR": str(data)}), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = main(list(args))
            return code, (json.loads(out.getvalue()) if out.getvalue() else None)

        code, made = run("backup", str(self.tmp / "bk"), data=self.tmp / "data")
        self.assertEqual((code, made["backend"], made["counts"]["committed_revisions"]), (0, "local", 2))
        self.assertEqual(run("verify", str(self.tmp / "bk"), data=self.tmp / "data")[0], 0)
        code, restored = run("restore", str(self.tmp / "bk"), data=self.tmp / "new")
        self.assertEqual((code, restored["documents"], restored["revisions"]), (0, 2, 2))
        self.assertEqual(run("check", data=self.tmp / "new")[1]["catalogue_sequence"], made["catalogue_sequence"])
        self.assertEqual(run("restore", str(self.tmp / "bk"), data=self.tmp / "new")[0], 1)     # not empty
        self.assertEqual(run("bogus", data=self.tmp / "new")[0], 2)


class AzureVariant:
    def restored_store(self):
        store = self.make_store()
        self.restored_store_opened = store
        return store


from backends import AzureMemoryStore, AZURE, AzuriteStore  # noqa: E402

AzureMemoryBackupRestore = type("AzureMemoryBackupRestore", (AzureVariant, AzureMemoryStore, BackupRestore), {})
if AZURE:
    AzuriteBackupRestore = type("AzuriteBackupRestore", (AzureVariant, AzuriteStore, BackupRestore), {})


if __name__ == "__main__":
    unittest.main()
