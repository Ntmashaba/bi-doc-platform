"""Batch generation: discover inputs, run them one at a time, keep per-item outcomes.

Item states: queued → validating → (extracting, PBIX only) → analysing → rendering →
completed | local_only | failed | cancelled. Successful items survive other items'
failures. A failed, cancelled or interrupted item can be retried. PBIX extraction runs
one at a time (the runner is sequential), in the item's own workspace.
"""
from __future__ import annotations

import json
import os
import threading
import time
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from .extract import ExtractionCancelled, ExtractionError, check_tool, extract_pbix
from .history import History, now

ADF_FOLDERS = {"pipeline", "dataset", "linkedService", "dataflow", "trigger", "factory"}


@dataclass
class Options:
    output_dir: str
    profile: str = "local"
    query_code: str = "withheld"
    environment: str = ""
    business_area: str = ""
    owner: str = ""
    extract_timeout: float = 900
    pbix_backend: dict | None = None    # set once per batch that has PBIX items: which extractor, and any fallback reason


def _is_arm(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace")
        return '"resources"' in head and ("deploymentTemplate" in head or '"$schema"' in head)
    except OSError:
        return False


def classify(path) -> dict:
    """Engine and input kind for one path, or an error explaining why it is not a complete input."""
    p = Path(path)
    item = {"source": str(p), "label": p.name or str(p)}
    if not p.exists():
        return {**item, "errors": [{"code": "INVALID_INPUT", "message": "not found"}]}
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix in (".pbix", ".abf"):
            return {**item, "engine": "power_bi", "kind": suffix[1:]}
        if suffix in (".bim", ".xmla"):
            # .xmla: an SSMS "Script Database as > CREATE To" script, which holds the same document as a model.bim
            # (JSON at compatibility level 1200 and later, XML at 1100 and 1103; a .bim follows the same split).
            # Only a file that is selected: folder searches skip .xmla, most of which are processing scripts.
            return {**item, "engine": "power_bi", "kind": "bim"}
        if suffix == ".pbip":
            model = [d for d in p.parent.glob(f"{p.stem}.SemanticModel") if d.is_dir()]
            if model:
                return {**item, "source": str(p.parent), "label": p.stem, "engine": "power_bi", "kind": "pbip"}
            return {**item, "errors": [{"code": "INVALID_INPUT", "message":
                    "a .pbip file is only a pointer; select the whole project folder with its .SemanticModel"}]}
        if suffix == ".json" and _is_arm(p):
            return {**item, "engine": "adf", "kind": "adf_arm"}
        if suffix == ".json":
            return {**item, "engine": "adf", "kind": "adf_resources"}
        return {**item, "errors": [{"code": "INVALID_INPUT", "message": f"unsupported file type {suffix or '(none)'}"}]}
    found = classify_folder(p)
    if found is not None:
        return found
    return {**item, "errors": [{"code": "INVALID_INPUT", "message":
            "not a recognised Power BI project, model, report, pbi-tools extract or Data Factory folder"}]}


def classify_folder(path, children=None, on_list=None, blocked=None) -> dict | None:
    """The input a folder is: a recognised project folder, or an error item when it cannot be read or is a broken project.
    None when it is not itself an input (it may hold inputs: see `discovery.discover`).

    `children` is the folder's listing when the caller already has it. `on_list` is called before every further folder listing
    this makes (checking a Data Factory part for JSON), so a caller can count them. `blocked(path)` says a path must not be
    looked at (a link, or a folder that is left out): such a child never counts towards recognising a project."""
    p = Path(path)
    item = {"source": str(p), "label": p.name or str(p)}
    if children is None:
        try:
            if on_list:
                on_list()
            children = {c.name for c in p.iterdir()}
        except OSError as exc:
            return {**item, "errors": [{"code": "INVALID_INPUT", "message": f"cannot read folder {p}: {exc.strerror or exc}"}]}

    def ok(*parts) -> bool:                                   # every step down from the folder is allowed
        return not blocked or not any(blocked(p.joinpath(*parts[:n + 1])) for n in range(len(parts)))

    children = {c for c in children if ok(c)}
    if any(c.endswith(".SemanticModel") for c in children):
        return {**item, "engine": "power_bi", "kind": "pbip"}
    if any(c.endswith(".pbip") for c in children):
        return {**item, "errors": [{"code": "INVALID_INPUT", "message":
                "this project folder has a .pbip pointer but no .SemanticModel folder"}]}
    if ok("Model") and (p / "Model").is_dir() and (
            (ok("Report") and (p / "Report").is_dir()) or (ok("Model", "database.json") and (p / "Model" / "database.json").is_file())):
        return {**item, "engine": "power_bi", "kind": "extracted"}
    if p.name.endswith(".SemanticModel") or (ok("definition", "model.tmdl") and (p / "definition" / "model.tmdl").is_file()) \
            or (ok("model.tmdl") and (p / "model.tmdl").is_file()):
        return {**item, "engine": "power_bi", "kind": "tmdl"}
    if p.name.endswith(".Report") or (ok("definition", "report.json") and (p / "definition" / "report.json").is_file()):
        return {**item, "engine": "power_bi", "kind": "pbir"}
    if any(_holds_json(p / name, on_list, blocked) for name in sorted(children & ADF_FOLDERS)):
        return {**item, "engine": "adf", "kind": "adf_git"}
    return None


def _holds_json(folder: Path, on_list=None, blocked=None) -> bool:
    """A Data Factory Git folder's `pipeline/`, `dataset/`... hold the JSON definitions; a folder that merely has one of
    those names (a reports folder with a `pipeline` subfolder) is not a factory. A part that is a link, or JSON that is a
    link, does not count."""
    if (blocked and blocked(folder)) or not folder.is_dir():
        return False
    if on_list:
        on_list()
    try:
        with os.scandir(folder) as entries:                      # stops at the first JSON file, however large the folder
            return any(e.name.lower().endswith(".json") and not (blocked and blocked(folder / e.name)) for e in entries)
    except OSError:
        return False


class Runner:
    """Runs queued items on one background thread; the UI and CLI read state from History."""

    def __init__(self, history: History, *, pbi_tools=None, pbix_ready=None, tool_command=None, backend=None):
        self.history = history
        self.backend = backend                    # the backend.Selection behind pbi_tools/pbix_ready, when built from one
        self.pbi_tools = pbi_tools
        self.pbix_ready = pbix_ready              # None = ready; otherwise the reason PBIX is unavailable
        self.tool_command = tool_command          # tests: run a script instead of pbi-tools.exe
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._cancel: dict[str, threading.Event] = {}
        self._thread = None
        self.idle = threading.Event()
        self.idle.set()

    # ---- control ----------------------------------------------------------------

    def discover(self, paths, output_dir=None):
        """The inputs `paths` stand for (folders searched), as `submit` would queue them. Excludes the output folder and
        this generator's own folder."""
        from .discovery import discover  # noqa: PLC0415 - discovery imports classify from this module
        return discover(paths, exclude=[output_dir, self.history.home])

    def submit(self, paths, options: Options) -> str:
        """Queue every input in `paths`; a folder that is not itself a project is searched (see discovery.py)."""
        return self.submit_items(self.discover(paths, options.output_dir).items, options)

    def submit_items(self, items, options: Options) -> str:
        """Queue already-discovered items, e.g. the list a person reviewed."""
        if self.backend is not None and any(i.get("kind") == "pbix" for i in items):
            # Recorded once for the batch, not once per file, so the CLI, the history and the desktop app can all say
            # which extractor is used and why.
            options = dataclasses.replace(options, pbix_backend=self.backend.as_dict())
        batch_id = self.history.create_batch(options.output_dir, options.__dict__, items)
        self._kick()
        return batch_id

    def retry(self, item_id) -> dict:
        item = self.history.requeue(item_id)
        self._kick()
        return item

    def cancel(self, item_id) -> bool:
        """Cancel a queued or running item. Returns False when it had already finished."""
        with self._lock:
            ev = self._cancel.get(item_id)
            if ev is not None:
                ev.set()
                return True
        if self.history.cancel_queued(item_id):
            return True
        with self._lock:                                   # claimed in between: cancel the run
            ev = self._cancel.get(item_id)
            if ev is not None:
                ev.set()
                return True
        return False

    def cancel_batch(self, batch_id) -> int:
        return sum(self.cancel(i["item_id"]) for i in self.history.batch(batch_id)["items"]
                   if i["state"] not in ("completed", "local_only", "failed", "cancelled", "interrupted"))

    def active(self) -> bool:
        return not self.idle.is_set()

    def shutdown(self, *, cancel=True, timeout=30) -> None:
        if cancel:
            with self._lock:
                for ev in self._cancel.values():
                    ev.set()
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wait(self, timeout=None) -> bool:
        """Block until nothing is queued or running (CLI and tests)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self.idle.wait(0.05) and not self.history.queued():
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False

    def _kick(self):
        self.idle.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, name="bidoc-batch", daemon=True)
            self._thread.start()
        self._wake.set()

    # ---- work -------------------------------------------------------------------

    def _loop(self):
        while not self._stop.is_set():
            queued = self.history.queued()
            if not queued:
                self.idle.set()
                self._wake.wait(0.5)
                self._wake.clear()
                if not self.history.queued():
                    continue
                self.idle.clear()
                continue
            self.idle.clear()
            self._run(queued[0])

    def _run(self, item):
        item_id = item["item_id"]
        cancel = threading.Event()
        with self._lock:
            self._cancel[item_id] = cancel
        if not self.history.claim(item_id, item["attempt"] + 1):     # cancelled while waiting
            with self._lock:
                self._cancel.pop(item_id, None)
            return
        batch = self.history.batch(item["batch_id"])
        opts = Options(**batch["options"])
        started = time.monotonic()

        def finish(state, **fields):
            self.history.update(item_id, state=state, finished_at=now(),
                                elapsed_seconds=round(time.monotonic() - started, 3), **fields)

        try:
            from bidoc_engines.generate import GenerateRequest, generate  # noqa: PLC0415
            if not item["engine"]:                  # retry of an input that was not usable before
                # a folder that held nothing (or could not be read) is searched again; one that now holds several inputs
                # cannot become one item, so say so instead of silently picking one
                scan = self.discover([item["source"]], opts.output_dir)
                usable = [i for i in scan.items if not i.get("errors")]
                if len(usable) > 1:
                    return finish("failed", errors=[{"code": "INVALID_INPUT", "message":
                                  f"{item['source']} now holds {len(usable)} inputs; queue the folder again as a new batch"}])
                found = usable[0] if usable else scan.items[0]
                if found.get("errors"):
                    return finish("failed", errors=found["errors"])
                self.history.update(item_id, engine=found["engine"], kind=found["kind"], source=found["source"])
                item = {**item, **{k: found[k] for k in ("engine", "kind", "source")}}
            extracted = None
            if item["kind"] in ("pbix", "abf"):
                if item["kind"] == "pbix" and self.pbix_ready is not None:
                    return finish("failed", errors=[{"code": "PREREQUISITE_MISSING", "message": self.pbix_ready}])
                self.history.update(item_id, state="extracting")
                workspace = self.history.workspace(item_id)
                tool = check_tool("pbixray") if item["kind"] == "abf" else (None if self.tool_command else check_tool(self.pbi_tools))
                extracted = extract_pbix(item["source"], workspace, tool,
                                         timeout=opts.extract_timeout, cancellation=cancel,
                                         command=self.tool_command)
            request = GenerateRequest(
                engine=item["engine"], source_path=item["source"], source_kind=item["kind"],
                output_dir=opts.output_dir, profile=opts.profile, query_code=opts.query_code,
                environment=opts.environment, business_area=opts.business_area, owner=opts.owner,
                extracted_path=str(extracted) if extracted else None,
                mapping_dir=str(self.history.home / "identity"))

            def progress(stage, message=""):
                if stage in ("analysing", "rendering"):
                    self.history.update(item_id, state=stage)

            result = generate(request, progress, cancel)
            if result.status == "cancelled":
                return finish("cancelled")
            fields = dict(warnings=result.warnings, errors=result.errors, artifact_path=result.artifact_path,
                          document_id=result.document_id, revision_id=result.revision_id)
            finish({"completed": "completed", "local_only": "local_only"}.get(result.status, "failed"), **fields)
            if result.status in ("completed", "local_only"):
                self.history.release_workspace(item_id)
        except ExtractionCancelled:
            finish("cancelled")
        except ExtractionError as exc:
            finish("failed", errors=[{"code": exc.code, "message": str(exc)}])
        except Exception as exc:  # noqa: BLE001 - one item's bug must not stop the batch
            finish("failed", errors=[{"code": "INTERNAL_ERROR", "message": f"{type(exc).__name__}: {exc}"}])
        finally:
            with self._lock:
                self._cancel.pop(item_id, None)


def summary(batch: dict) -> dict:
    counts = {}
    for it in batch["items"]:
        counts[it["state"]] = counts.get(it["state"], 0) + 1
    return counts


def to_json(obj) -> str:
    return json.dumps(obj, indent=1)
