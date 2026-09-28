"""Worker mode: process hosted jobs with the same engines (handoff 10; R3, B14).

    bidoc worker connect URL [--token-stdin]     store the worker token (from an administrator)
    bidoc worker run [--once] [--pbi-tools PATH] heartbeat, claim and process jobs, one at a time
    bidoc worker disconnect

The worker only connects outbound, to /api/v1/worker/* with its own token. It never calls
the generic publishing endpoints: it stages a candidate and asks the server to complete,
and the server publishes under the current lease.

Each job runs in its own workspace, deleted afterwards. The lease is renewed every 30 s;
if the library reports cancellation, or the lease is lost, the extraction process tree
is killed and the job abandoned (a lost lease is never reported as a failure, because
another worker may own the job now). Only an administrator-configured pbi-tools is run;
nothing is discovered by scanning the disk.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from . import __version__
from .publisher import PublishError, _NoRedirect, normalize_url

HEARTBEAT_SECONDS = 30
RENEW_SECONDS = 30
POLL_SECONDS = 5
TIMEOUT = 60
MAX_ZIP_ENTRIES = 20000
MAX_ZIP_EXPANDED = 4 * 1024 ** 3
CREDENTIAL_KIND = "worker"


class LeaseLost(Exception):
    pass


class WorkerClient:
    def __init__(self, url: str, token: str):
        self.base = normalize_url(url) + "/api/v1/worker"
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler())

    def __repr__(self):
        return f"WorkerClient({self.base!r})"

    def call(self, method, path, *, body=None, headers=None, raw=False, stream_to=None):
        data, h = None, {"Authorization": f"Bearer {self._token}", "Accept": "application/json", **(headers or {})}
        if isinstance(body, (dict, list)):
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif body is not None:
            data = body
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self._opener.open(req, timeout=TIMEOUT) as resp:
                if stream_to is not None:
                    shutil.copyfileobj(resp, stream_to, 1 << 20)
                    return resp.status, None
                payload = resp.read()
                return resp.status, (payload if raw else json.loads(payload) if payload else None)
        except urllib.error.HTTPError as exc:
            try:
                err = json.loads(exc.read() or b"{}").get("error", {})
            except ValueError:
                err = {}
            if exc.code == 401:
                raise PublishError("CREDENTIAL_REJECTED", "the library did not accept the worker token", 401) from None
            raise PublishError(err.get("code") or f"HTTP_{exc.code}", err.get("message") or exc.reason, exc.code) \
                from None
        except PublishError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise PublishError("LIBRARY_UNREACHABLE", f"could not reach the library: {getattr(exc, 'reason', exc)}") \
                from None

    def multipart(self, path, fields: dict, file_name: str, file_bytes: bytes):
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                 for k, v in fields.items()]
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                     f"Content-Type: text/html\r\n\r\n".encode() + file_bytes + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call("POST", path, body=b"".join(parts),
                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


# ---- safe project ZIP extraction ---------------------------------------------------------

def extract_project_zip(archive: Path, root: Path) -> Path:
    """Extract a PBIP project ZIP inside `root`, keeping its folder layout; returns the .pbip.

    Refuses absolute or traversing paths, backslashes, drive letters, symlinks, encrypted
    entries, too many entries and oversized content (checked while writing)."""
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ValueError("the project ZIP has too many entries")
        seen = set()
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            raw = info.orig_filename                     # before Windows turns "\\" into "/"
            if not name or name.startswith("/") or "\\" in name or "\\" in raw or ":" in name.split("/")[0] \
                    or any(p in ("..", ".") for p in parts):
                raise ValueError(f"unsafe path in the project ZIP: {name!r}")
            if info.flag_bits & 0x1:
                raise ValueError("encrypted entries are not accepted")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"symbolic links are not accepted: {name!r}")
            key = name.rstrip("/").lower()
            if key in seen:
                raise ValueError(f"duplicate entry in the project ZIP: {name!r}")
            seen.add(key)
            target = root.joinpath(*parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                while chunk := src.read(1 << 20):
                    total += len(chunk)
                    if total > MAX_ZIP_EXPANDED:
                        raise ValueError("the project ZIP expands beyond the size limit")
                    out.write(chunk)
    projects = sorted(root.rglob("*.pbip"))
    if len(projects) != 1:
        raise ValueError(f"the ZIP must contain exactly one .pbip project (found {len(projects)})")
    return projects[0]


# ---- the worker loop ---------------------------------------------------------------------

class Worker:
    def __init__(self, client: WorkerClient, *, pbi_tools=None, tool_command=None, workspace_root=None,
                 extract_timeout=900, log=print):
        self.client, self.pbi_tools, self.tool_command = client, pbi_tools, tool_command
        self.workspace_root = Path(workspace_root or tempfile.gettempdir()) / "bidoc-worker"
        self.extract_timeout, self.log = extract_timeout, log
        self.stop = threading.Event()

    def readiness(self) -> dict:
        from bidoc_engines import power_bi  # noqa: PLC0415

        from .extract import check_tool  # noqa: PLC0415
        types, detail, extractor = ["pbip_zip"], None, None
        if self.tool_command:
            types.append("pbix")
            extractor = "configured command"
        else:
            try:
                extractor = check_tool(self.pbi_tools)
                types.append("pbix")
            except Exception as exc:  # noqa: BLE001 - reported to the library, never fatal
                detail = f"PBIX not available: {exc}"
        return {"engine_version": f"bidoc {__version__}; {power_bi.ENGINE} {power_bi.ENGINE_VERSION}",
                "extractor_version": str(extractor)[:100] if extractor else None, "input_types": types,
                "readiness": "ready", "readiness_detail": detail}

    def heartbeat(self):
        self.client.call("POST", "/heartbeat", body=self.readiness())

    def _heartbeats(self):
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                self.heartbeat()
            except PublishError as exc:
                self.log(f"heartbeat failed: {exc}")

    def run(self, *, once=False) -> int:
        """Process jobs until stopped; with once=True, at most one job. Returns jobs processed."""
        self.heartbeat()
        beat = threading.Thread(target=self._heartbeats, daemon=True)
        beat.start()
        done = 0
        try:
            while not self.stop.is_set():
                status, claim = self.client.call("POST", "/claim")
                if status == 200:
                    self.process(claim)
                    done += 1
                    if once:
                        break
                elif once:
                    break
                else:
                    self.stop.wait(POLL_SECONDS)
        finally:
            self.stop.set()
        return done

    def process(self, claim: dict) -> str:
        job, lease = claim["job"], claim["lease_token"]
        job_id = job["job_id"]
        workspace = self.workspace_root / job_id
        cancel, lost = threading.Event(), threading.Event()
        renewer_stop = threading.Event()

        def renew_loop():
            while not renewer_stop.wait(RENEW_SECONDS):
                try:
                    _, out = self.client.call("POST", f"/jobs/{job_id}/renew", body={"lease_token": lease})
                    if out.get("cancellation_requested"):
                        cancel.set()
                except PublishError as exc:
                    if exc.status == 409:
                        lost.set()
                        cancel.set()
                        return

        def progress(stage):
            if lost.is_set():
                raise LeaseLost()
            try:
                self.client.call("POST", f"/jobs/{job_id}/progress", body={"lease_token": lease, "stage": stage})
            except PublishError as exc:
                if exc.status == 409:
                    lost.set()
                    raise LeaseLost() from None

        def fail(code, message, retryable):
            if lost.is_set():
                return "abandoned"
            try:
                _, out = self.client.call("POST", f"/jobs/{job_id}/fail", body={
                    "lease_token": lease, "error_code": code, "message": str(message)[:1500], "retryable": retryable})
                return out["state"]
            except PublishError as exc:
                return "abandoned" if exc.status == 409 else f"unreported ({exc.code})"

        renewer = threading.Thread(target=renew_loop, daemon=True)
        renewer.start()
        self.log(f"job {job_id}: {job['input_type']} {job['filename']} (attempt {job['attempt']})")
        try:
            shutil.rmtree(workspace, ignore_errors=True)
            workspace.mkdir(parents=True)
            progress("downloading")
            source = workspace / ("source.pbix" if job["input_type"] == "pbix" else "source.zip")
            with source.open("wb") as out:
                self.client.call("GET", claim["input_download_url"][len("/api/v1/worker"):],
                                 headers={"X-Lease-Token": lease}, stream_to=out)
            result = self._generate(job, source, workspace, progress, cancel)
            if cancel.is_set():
                raise _Cancelled()
            progress("uploading")
            data = Path(result.artifact_path).read_bytes()
            _, staged = self.client.multipart(f"/jobs/{job_id}/results",
                                              {"lease_token": lease, "attempt_id": job["attempt_id"]},
                                              "document.html", data)
            _, outcome = self.client.call("POST", f"/jobs/{job_id}/complete", body={
                "lease_token": lease, "attempt_id": job["attempt_id"],
                "staged_result_id": staged["staged_result_id"]})
            self.log(f"job {job_id}: {outcome['state']} (document {outcome['document_id']}, "
                     f"revision {outcome['revision_id']})")
            return outcome["state"]
        except (LeaseLost, _Cancelled) as exc:
            if lost.is_set():
                self.log(f"job {job_id}: lease lost; abandoned")
                return "abandoned"
            state = fail("CANCELLED", "cancelled", False) if isinstance(exc, _Cancelled) else "abandoned"
            self.log(f"job {job_id}: {state}")
            return state
        except _JobError as exc:
            state = fail(exc.code, exc.message, exc.retryable)
            self.log(f"job {job_id}: {exc.code}: {exc.message} -> {state}")
            return state
        except PublishError as exc:
            if exc.status == 409:
                self.log(f"job {job_id}: {exc.code}; abandoned")
                return "abandoned"
            state = fail("LIBRARY_ERROR", f"{exc.code}: {exc}", exc.status is None or exc.status >= 500)
            self.log(f"job {job_id}: {exc} -> {state}")
            return state
        except Exception as exc:  # noqa: BLE001 - report, never crash the worker
            state = fail("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}", False)
            self.log(f"job {job_id}: internal error {exc} -> {state}")
            return state
        finally:
            renewer_stop.set()
            renewer.join(timeout=5)
            shutil.rmtree(workspace, ignore_errors=True)       # A17: the workspace never outlives the job

    def _generate(self, job, source: Path, workspace: Path, progress, cancel):
        from bidoc_engines.generate import GenerateRequest, generate  # noqa: PLC0415

        from .extract import ExtractionCancelled, ExtractionError, check_tool, extract_pbix  # noqa: PLC0415
        extracted, kind, path = None, None, source
        try:
            if job["input_type"] == "pbix":
                progress("extracting")
                extracted = extract_pbix(source, workspace / "extract",
                                         None if self.tool_command else check_tool(self.pbi_tools),
                                         timeout=self.extract_timeout, cancellation=cancel, command=self.tool_command)
                kind = "pbix"
            else:
                progress("extracting")
                try:
                    path = extract_project_zip(source, workspace / "project")
                except (ValueError, zipfile.BadZipFile) as exc:
                    raise _JobError("INVALID_INPUT", str(exc), False) from None
                kind = "pbip"
        except ExtractionCancelled:
            raise _Cancelled() from None
        except ExtractionError as exc:
            raise _JobError(exc.code, str(exc), exc.code == "EXTRACTION_TIMEOUT") from None
        progress("analysing")
        request = GenerateRequest(engine="power_bi", source_path=str(path), source_kind=kind,
                                  output_dir=str(workspace / "out"), profile="shared", query_code="withheld",
                                  document_id=job.get("document_id"), mapping_dir=str(workspace / "identity"),
                                  extracted_path=str(extracted) if extracted else None)

        def engine_progress(stage, message=""):
            if stage == "rendering":
                progress("rendering")
        result = generate(request, engine_progress, cancel)
        if result.status == "cancelled":
            raise _Cancelled()
        if result.status != "completed":
            messages = "; ".join(e.get("message", "") for e in result.errors)[:1000] or result.status
            raise _JobError((result.errors[0].get("code") if result.errors else None) or "GENERATION_FAILED",
                            messages, False)
        return result


class _Cancelled(Exception):
    pass


class _JobError(Exception):
    def __init__(self, code, message, retryable):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
