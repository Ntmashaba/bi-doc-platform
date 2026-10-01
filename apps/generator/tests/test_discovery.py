"""Folder discovery for `bidoc batch` and the desktop review: one set of rules, the same inputs everywhere (B08 follow-up)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "packages" / "engines" / "tests"))
from fixtures import adf_factory, pbi_model  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from bidoc_generator import cli, discovery  # noqa: E402
from bidoc_generator.batch import Options, Runner  # noqa: E402
from bidoc_generator.desktop.app import create_app  # noqa: E402
from bidoc_generator.extract import python_tool  # noqa: E402
from bidoc_generator.history import History  # noqa: E402

FAKE = python_tool(str(HERE / "fake_pbi_tools.py"))
PORT, SECRET = 8798, "discovery-secret"
MUTATE = {"X-Bidoc-Session": SECRET, "X-Requested-With": "bidoc"}
DOCTOR = {"platform": "Test", "checks": [], "inputs": {"pbix": {"available": True, "reason": None}}}
DONE = ("completed", "local_only", "failed", "cancelled", "interrupted")


def pbix(path: Path, content: bytes = b"PK fake pbix") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content + path.name.encode())            # distinct bytes, so distinct documents
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "Documentation"
        self.model = pbi_model(self.tmp / "model")
        os.environ["FAKE_PBI_MODEL"] = str(self.model)
        os.environ["FAKE_PBI_MODE"] = "ok"
        self.h3, self.h4 = self.tmp / "h3 Reports", self.tmp / "h4 Reports"          # spaces, as on Windows

    def runner(self):
        r = Runner(History(self.tmp / "home"), tool_command=FAKE)
        self.addCleanup(r.shutdown)
        return r

    def labels(self, scan):
        return [i["label"] for i in scan.items]

    def discover(self, *paths, **kw):
        return discovery.discover([str(p) for p in paths], **kw)


class Discovering(Base):
    def test_two_folders_with_nested_pbix_files_give_one_input_per_file(self):
        pbix(self.h3 / "Finance" / "Budget.pbix")
        pbix(self.h3 / "Sales.pbix")
        pbix(self.h4 / "Ops" / "Q1" / "Plant.pbix")
        pbix(self.h4 / "Ops" / "Q1" / "Plant.abf")
        (self.h4 / "Ops" / "model.bim").write_text("{}")
        scan = self.discover(self.h3, self.h4)
        self.assertEqual(self.labels(scan), ["h3 Reports/Finance/Budget.pbix", "h3 Reports/Sales.pbix",
                                              "h4 Reports/Ops/model.bim", "h4 Reports/Ops/Q1/Plant.abf",
                                              "h4 Reports/Ops/Q1/Plant.pbix"])
        self.assertEqual({i["kind"] for i in scan.items}, {"pbix", "abf", "bim"})
        self.assertEqual((scan.usable, scan.selections), (5, 2))

    def test_batch_runs_one_document_per_discovered_file(self):
        for p in (self.h3 / "Finance" / "Budget.pbix", self.h3 / "Sales.pbix", self.h4 / "Ops" / "Q1" / "Plant.pbix"):
            pbix(p)
        r = self.runner()
        batch = r.submit([str(self.h3), str(self.h4)], Options(output_dir=str(self.out)))
        self.assertTrue(r.wait(60))
        items = r.history.batch(batch)["items"]
        self.assertEqual([i["label"] for i in items], ["h3 Reports/Finance/Budget.pbix", "h3 Reports/Sales.pbix",
                                                        "h4 Reports/Ops/Q1/Plant.pbix"])
        self.assertTrue(all(i["state"] in ("completed", "local_only") for i in items), [i["state"] for i in items])
        self.assertEqual(len({i["item_id"] for i in items}), 3)

    def test_overlapping_selections_do_not_duplicate(self):
        inner = pbix(self.h3 / "Sub" / "Inner.pbix")
        pbix(self.h3 / "Top.pbix")
        scan = self.discover(self.h3, inner, self.h3 / "Sub", self.h3)                 # a folder, a file in it, the same twice
        self.assertEqual(self.labels(scan), ["h3 Reports/Sub/Inner.pbix", "h3 Reports/Top.pbix"])
        self.assertEqual((scan.usable, scan.selections), (2, 4))
        first = self.discover(inner, self.h3)                                          # an explicit file keeps its own label
        self.assertEqual(self.labels(first), ["Inner.pbix", "h3 Reports/Top.pbix"])      # selections keep their order

    def test_a_recognised_project_stays_one_input_and_is_not_searched(self):
        project = self.h3 / "Sales"
        (project / "Sales.SemanticModel").mkdir(parents=True)
        (project / "Sales.pbip").write_text("{}")
        shutil.copy(self.model, project / "Sales.SemanticModel" / "model.bim")        # a model file inside the project
        pbix(project / "Sales.SemanticModel" / "stray.pbix")                           # nor is anything else in it
        adf = adf_factory(self.h3 / "factory")                                         # a Data Factory Git folder
        pbix(self.h3 / "pipeline" / "InAFolderNamedLikeAFactoryPart.pbix")             # a name alone does not make a factory
        pbix(self.h3 / "Loose.pbix")
        scan = self.discover(self.h3)
        self.assertEqual({i["label"]: i["kind"] for i in scan.items},
                         {"h3 Reports/Loose.pbix": "pbix", "h3 Reports/Sales": "pbip", "h3 Reports/factory": "adf_git",
                          "h3 Reports/pipeline/InAFolderNamedLikeAFactoryPart.pbix": "pbix"})
        self.assertEqual(len(scan.items), 4)
        self.assertTrue(adf.is_dir())

    def test_stray_json_and_other_files_are_not_inputs(self):
        pbix(self.h3 / "Real.pbix")
        (self.h3 / "notes.json").write_text('{"resources": [], "$schema": "deploymentTemplate"}')
        (self.h3 / "readme.txt").write_text("hello")
        pbix(self.h3 / "._Real.pbix")                                                  # macOS resource fork
        self.assertEqual(self.labels(self.discover(self.h3)), ["h3 Reports/Real.pbix"])
        self.assertEqual(self.discover(self.h3 / "notes.json").items[0]["kind"], "adf_arm")      # explicit: as before

    def test_explicit_files_behave_as_before(self):
        a = pbix(self.tmp / "Alone.pbix")
        missing = self.tmp / "gone.pbix"
        scan = self.discover(a, missing)
        self.assertEqual([i["label"] for i in scan.items], ["Alone.pbix", "gone.pbix"])
        self.assertEqual(scan.items[1]["errors"][0]["message"], "not found")

    def test_output_folder_and_development_folders_are_left_out(self):
        pbix(self.h3 / "Keep.pbix")
        pbix(self.h3 / "Documentation" / "Generated.pbix")           # the output folder, inside the scanned one
        for name in (".git", ".venv", "node_modules"):
            pbix(self.h3 / name / "Hidden.pbix")
        scan = self.discover(self.h3, exclude=[self.h3 / "Documentation"])
        self.assertEqual(self.labels(scan), ["h3 Reports/Keep.pbix"])

    def test_the_generators_own_folder_is_left_out(self):
        runner = self.runner()
        pbix(self.h3 / "Keep.pbix")
        pbix(runner.history.home / "workspaces" / "item" / "Extract.pbix")
        scan = runner.discover([str(self.tmp)], str(self.out))
        self.assertEqual([i["label"] for i in scan.items if not i.get("errors")],
                         [Path(self.tmp.name, "h3 Reports", "Keep.pbix").as_posix(),
                          Path(self.tmp.name, "model", "model.bim").as_posix()])

    def test_links_and_junctions_are_not_followed(self):
        elsewhere = self.tmp / "elsewhere"
        pbix(elsewhere / "Outside.pbix")
        pbix(self.h3 / "Inside.pbix")
        (self.h3 / "linked").mkdir()
        real = discovery._is_link
        with mock.patch.object(discovery, "_is_link", side_effect=lambda e: e.name == "linked" or real(e)):
            scan = self.discover(self.h3)
        self.assertEqual(self.labels(scan), ["h3 Reports/Inside.pbix"])
        self.assertTrue(any("Not followed" in w and "linked" in w for w in scan.warnings))
        try:                                                    # and a real symbolic link, where this platform allows one
            os.symlink(elsewhere, self.h3 / "symlinked", target_is_directory=True)
        except (OSError, NotImplementedError):
            return
        scan = self.discover(self.h3)
        self.assertEqual(self.labels(scan), ["h3 Reports/Inside.pbix"])
        self.assertTrue(any("symlinked" in w for w in scan.warnings))

    def test_an_unreadable_subfolder_does_not_stop_the_readable_ones(self):
        pbix(self.h3 / "A" / "Good.pbix")
        pbix(self.h3 / "Locked" / "Never.pbix")
        pbix(self.h3 / "Z" / "Also.pbix")
        real = os.scandir

        def scandir(path):
            if Path(path).name == "Locked":
                raise PermissionError(13, "Permission denied")
            return real(path)
        with mock.patch.object(discovery.os, "scandir", scandir):
            scan = self.discover(self.h3)
        usable = [i["label"] for i in scan.items if not i.get("errors")]
        failed = [i for i in scan.items if i.get("errors")]
        self.assertEqual(usable, ["h3 Reports/A/Good.pbix", "h3 Reports/Z/Also.pbix"])
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["label"], "h3 Reports/Locked")
        self.assertIn("cannot read folder", failed[0]["errors"][0]["message"])

    def test_an_empty_scan_is_a_failed_item_not_success(self):
        (self.h3 / "Empty").mkdir(parents=True)
        (self.h3 / "notes.txt").write_text("nothing here")
        scan = self.discover(self.h3)
        self.assertEqual((scan.usable, len(scan.items)), (0, 1))
        self.assertIn("no supported inputs found", scan.items[0]["errors"][0]["message"])

    def test_scan_bounds_are_reported(self):
        for i in range(5):
            pbix(self.h3 / f"R{i}.pbix")
        scan = self.discover(self.h3, max_items=3)
        self.assertEqual(scan.usable, 3)
        self.assertTrue(any("scan stopped" in i["errors"][0]["message"] for i in scan.items if i.get("errors")))

    def test_same_file_names_in_different_folders_are_told_apart_and_flagged(self):
        pbix(self.h3 / "A" / "Sales.pbix")
        pbix(self.h3 / "B" / "Sales.pbix")
        scan = self.discover(self.h3)
        self.assertEqual(self.labels(scan), ["h3 Reports/A/Sales.pbix", "h3 Reports/B/Sales.pbix"])
        self.assertTrue(any("share a file name" in w for w in scan.warnings))

    def test_selections_keep_their_order_and_a_folders_finds_are_sorted(self):
        a, b = pbix(self.tmp / "Zed.pbix"), pbix(self.tmp / "Alpha.pbix")
        pbix(self.h3 / "b.pbix")
        pbix(self.h3 / "A.pbix")
        scan = self.discover(a, self.h3, b)
        self.assertEqual(self.labels(scan), ["Zed.pbix", "h3 Reports/A.pbix", "h3 Reports/b.pbix", "Alpha.pbix"])

    def test_results_are_sorted_the_same_way_every_time(self):
        for name in ("b.pbix", "A.pbix", "c.pbix", "a2.pbix"):
            pbix(self.h3 / name)
        first = self.labels(self.discover(self.h3))
        self.assertEqual(first, sorted(first, key=str.casefold))
        self.assertEqual(first, self.labels(self.discover(self.h3)))


class RunningAndRetrying(Base):
    def test_one_corrupt_pbix_fails_alone_and_can_be_retried(self):
        pbix(self.h3 / "Good.pbix")
        bad = self.h3 / "Bad.pbix"
        bad.write_bytes(b"CORRUPT")
        pbix(self.h3 / "Fine.pbix")
        r = self.runner()
        batch = r.submit([str(self.h3)], Options(output_dir=str(self.out)))
        self.assertTrue(r.wait(60))
        items = r.history.batch(batch)["items"]
        self.assertEqual(len(items), 3)
        states = {i["label"]: i["state"] for i in items}
        self.assertEqual(states["h3 Reports/Bad.pbix"], "failed")
        self.assertEqual(states["h3 Reports/Good.pbix"], "completed")
        self.assertEqual(states["h3 Reports/Fine.pbix"], "completed")
        pbix(bad)                                                   # fix the file, then retry that item only
        r.retry(next(i["item_id"] for i in items if i["state"] == "failed"))
        self.assertTrue(r.wait(60))
        self.assertEqual({i["label"]: i["state"] for i in r.history.batch(batch)["items"]}["h3 Reports/Bad.pbix"], "completed")

    def test_an_empty_folder_item_is_searched_again_on_retry(self):
        self.h3.mkdir()
        r = self.runner()
        batch = r.submit([str(self.h3)], Options(output_dir=str(self.out)))
        self.assertTrue(r.wait(60))
        item = r.history.batch(batch)["items"][0]
        self.assertEqual(item["state"], "failed")
        pbix(self.h3 / "Arrived.pbix")
        r.retry(item["item_id"])
        self.assertTrue(r.wait(60))
        self.assertIn(r.history.batch(batch)["items"][0]["state"], ("completed", "local_only"))

    def test_a_folder_that_now_holds_several_inputs_is_not_silently_narrowed_on_retry(self):
        self.h3.mkdir()
        r = self.runner()
        batch = r.submit([str(self.h3)], Options(output_dir=str(self.out)))
        self.assertTrue(r.wait(60))
        item = r.history.batch(batch)["items"][0]
        pbix(self.h3 / "One.pbix")
        pbix(self.h3 / "Two.pbix")
        r.retry(item["item_id"])
        self.assertTrue(r.wait(60))
        after = r.history.batch(batch)["items"][0]
        self.assertEqual(after["state"], "failed")
        self.assertIn("now holds 2 inputs", after["errors"][0]["message"])


class SameInputsEverywhere(Base):
    def setUp(self):
        super().setUp()
        pbix(self.h3 / "Finance" / "Budget.pbix")
        pbix(self.h4 / "Sales.pbix")
        adf_factory(self.h4 / "Factory")
        self.sources = [str(self.h3), str(self.h4)]

    def cli_items(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"BIDOC_HOME": str(self.tmp / "cli-home")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["batch", *self.sources, "--output-dir", str(self.out), "--json"])
        return code, json.loads(out.getvalue())

    def desktop(self):
        runner = self.runner()
        client = TestClient(create_app(runner, session_secret=SECRET, port=PORT, doctor=lambda: DOCTOR),
                            base_url=f"http://127.0.0.1:{PORT}")
        return runner, client

    def test_cli_and_desktop_discover_the_same_inputs(self):
        _, client = self.desktop()
        reviewed = client.post("/api/review", json={"inputs": self.sources, "output_dir": str(self.out)}, headers=MUTATE).json()
        _, batch = self.cli_items()
        # The CLI resolves the paths it is given (on Windows that expands 8.3 short names such as RUNNER~1), the desktop
        # keeps them as typed; they must still be the same files, so compare locations, not spellings.
        key = lambda items: [(i["label"], os.path.normcase(os.path.realpath(i["source"])), i.get("kind")) for i in items]  # noqa: E731
        self.assertEqual(key(reviewed["items"]), key(batch["items"]))
        self.assertEqual(len(batch["items"]), 3)
        self.assertEqual(batch["discovery"]["found"], 3)
        self.assertEqual(reviewed["summary"]["found"], 3)

    def test_a_differently_spelled_path_finds_the_same_files(self):
        link = self.tmp / "alias"
        try:
            os.symlink(self.tmp, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            return                                                                      # no symlinks here; the Windows short-name case covers it
        _, client = self.desktop()
        via_alias = client.post("/api/review", json={"inputs": [str(link / "h3 Reports")], "output_dir": str(self.out)},
                                headers=MUTATE).json()
        direct = client.post("/api/review", json={"inputs": [str(self.h3)], "output_dir": str(self.out)}, headers=MUTATE).json()
        locations = lambda r: [(i["label"], os.path.realpath(i["source"])) for i in r["items"]]    # noqa: E731
        self.assertEqual(locations(via_alias), locations(direct))
        self.assertEqual(via_alias["summary"]["found"], 1)

    def test_the_cli_reports_an_empty_folder_as_a_failure(self):
        (self.tmp / "nothing").mkdir()
        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"BIDOC_HOME": str(self.tmp / "cli-home")}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["batch", str(self.tmp / "nothing"), "--output-dir", str(self.out)])
        self.assertNotEqual(code, 0)
        self.assertIn("no supported inputs found", out.getvalue())
        self.assertIn("Found 0 input(s)", err.getvalue())

    def wait(self, client, batch_id):
        for _ in range(400):
            b = client.get(f"/api/batches/{batch_id}").json()
            if all(i["state"] in DONE for i in b["items"]):
                return b
            time.sleep(0.05)
        self.fail("batch did not finish")

    def test_submitting_a_review_queues_exactly_the_reviewed_list(self):
        runner, client = self.desktop()
        review = client.post("/api/review", json={"inputs": self.sources, "output_dir": str(self.out)}, headers=MUTATE).json()
        pbix(self.h3 / "Late.pbix")                                 # appears after the review
        body = {"inputs": self.sources, "output_dir": str(self.out), "review_id": review["review_id"]}
        batch = client.post("/api/batches", json=body, headers=MUTATE).json()
        done = self.wait(client, batch["batch_id"])
        self.assertEqual([i["label"] for i in done["items"]], [i["label"] for i in review["items"]])
        self.assertNotIn("Late", " ".join(i["label"] for i in done["items"]))
        fresh = client.post("/api/batches", json={"inputs": self.sources, "output_dir": str(self.out)}, headers=MUTATE).json()
        self.assertIn("h3 Reports/Late.pbix", [i["label"] for i in self.wait(client, fresh["batch_id"])["items"]])

    def test_a_stale_or_changed_review_is_refused(self):
        _, client = self.desktop()
        review = client.post("/api/review", json={"inputs": self.sources, "output_dir": str(self.out)}, headers=MUTATE).json()
        body = {"inputs": self.sources, "output_dir": str(self.tmp / "elsewhere"), "review_id": review["review_id"]}
        self.assertEqual(client.post("/api/batches", json=body, headers=MUTATE).status_code, 409)
        body = {"inputs": self.sources, "output_dir": str(self.out), "review_id": "unknown"}
        self.assertEqual(client.post("/api/batches", json=body, headers=MUTATE).status_code, 409)

    def test_the_review_leaves_the_output_folder_out(self):
        pbix(self.out / "Generated.pbix")
        _, client = self.desktop()
        review = client.post("/api/review", json={"inputs": [str(self.tmp)], "output_dir": str(self.out)}, headers=MUTATE).json()
        self.assertFalse(any("Documentation" in i["label"] for i in review["items"]))


if __name__ == "__main__":
    unittest.main()
