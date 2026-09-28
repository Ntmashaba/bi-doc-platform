"""The generator's desktop app (handoff sections 4 and 17.1).

A FastAPI app on 127.0.0.1 serves the UI and a small JSON API over the batch runner.
pywebview shows it in a native window and adds file pickers. Protections, as for the
library's local mode:

- Host must be the loopback address and port (blocks DNS rebinding); a foreign Origin
  is refused.
- Every change needs the per-launch session secret (X-Bidoc-Session, injected into the
  page) and the anti-CSRF header.
- Generated documents are only served by item ID from the history, under a sandbox CSP
  (no same-origin, no network), and the pywebview window that previews them has no host
  bridge.
"""
from __future__ import annotations

import hmac
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..batch import Options, Runner, classify
from ..doctor import diagnose, home
from ..history import History

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"desktop.js": "text/javascript", "desktop.css": "text/css"}
SHELL_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
             "frame-src 'self'; form-action 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")
VIEW_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'self'")
SAFE = {"GET", "HEAD"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class BatchIn(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=500)
    output_dir: str = Field(min_length=1, max_length=4096)
    profile: Literal["local", "shared"] = "local"
    include_query_code: bool = False
    environment: str = Field("", max_length=200)
    business_area: str = Field("", max_length=200)
    owner: str = Field("", max_length=200)


class ReviewIn(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=500)


def create_app(runner: Runner, *, session_secret: str, port: int, doctor=None) -> FastAPI:
    app = FastAPI(title="bidoc desktop", docs_url=None, redoc_url=None, openapi_url=None)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    history: History = runner.history
    report = doctor or (lambda: diagnose(runner.pbi_tools))

    @app.exception_handler(ApiError)
    async def api_error(request, exc: ApiError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "").lower() not in hosts:
            return JSONResponse({"error": {"code": "FORBIDDEN", "message": "requests must address the loopback host"}},
                                status_code=403)
        origin = request.headers.get("origin")
        if origin and origin.lower() not in origins:
            return JSONResponse({"error": {"code": "FORBIDDEN", "message": "cross-origin requests are not allowed"}},
                                status_code=403)
        if request.method not in SAFE:
            supplied = request.headers.get("x-bidoc-session", "")
            if not hmac.compare_digest(supplied, session_secret) or request.headers.get("x-requested-with") != "bidoc":
                return JSONResponse({"error": {"code": "UNAUTHENTICATED",
                                               "message": "changes need the session secret"}}, status_code=401)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/", include_in_schema=False)
    def shell():
        page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__SESSION__", session_secret)
        return HTMLResponse(page, headers={"Content-Security-Policy": SHELL_CSP, "X-Frame-Options": "DENY"})

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str):
        if name not in STATIC_FILES:
            raise ApiError(404, "NOT_FOUND", "not found")
        return FileResponse(STATIC / name, media_type=STATIC_FILES[name])

    @app.get("/api/doctor")
    def doctor_route():
        return report()

    @app.get("/api/state")
    def state():
        return {"active": runner.active(), "interrupted_on_start": history.interrupted}

    @app.post("/api/review")
    def review(body: ReviewIn):
        """Batch review: what each input is, before anything runs."""
        pbix = report()["inputs"]["pbix"]
        items = []
        for path in body.inputs:
            it = classify(path)
            if it.get("kind") == "pbix" and not pbix["available"]:
                it["warnings"] = [f"PBIX generation is unavailable: {pbix['reason']}"]
            items.append(it)
        return {"items": items}

    @app.get("/api/batches")
    def batches(limit: int = 20):
        return history.batches(max(1, min(limit, 100)))

    @app.post("/api/batches", status_code=201)
    def create_batch(body: BatchIn):
        out = Path(body.output_dir)
        if not out.is_absolute():
            raise ApiError(400, "INVALID_REQUEST", "choose an absolute output folder")
        opts = Options(output_dir=str(out), profile=body.profile,
                       query_code="included" if body.include_query_code else "withheld",
                       environment=body.environment, business_area=body.business_area, owner=body.owner)
        return history.batch(runner.submit(body.inputs, opts))

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str):
        try:
            return history.batch(batch_id)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", "batch not found") from None

    @app.post("/api/batches/{batch_id}/cancel")
    def cancel_batch(batch_id: str):
        get_batch(batch_id)
        return {"cancelled": runner.cancel_batch(batch_id)}

    def _item(item_id):
        try:
            return history.item(item_id)
        except KeyError:
            raise ApiError(404, "NOT_FOUND", "item not found") from None

    @app.post("/api/items/{item_id}/cancel")
    def cancel_item(item_id: str):
        _item(item_id)
        if not runner.cancel(item_id):
            raise ApiError(409, "ALREADY_FINISHED", "this item has already finished")
        return _item(item_id)

    @app.post("/api/items/{item_id}/retry")
    def retry_item(item_id: str):
        _item(item_id)
        try:
            return runner.retry(item_id)
        except ValueError as exc:
            raise ApiError(409, "NOT_RETRYABLE", str(exc)) from None

    @app.get("/api/items/{item_id}/view", include_in_schema=False)
    def view(item_id: str):
        """A generated document, only by item ID, sandboxed without same-origin or network."""
        item = _item(item_id)
        path = item["artifact_path"]
        if item["state"] not in ("completed", "local_only") or not path or not Path(path).is_file():
            raise ApiError(404, "NOT_FOUND", "no document for this item")
        return Response(Path(path).read_bytes(), media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": VIEW_CSP, "Cross-Origin-Resource-Policy": "same-origin"})

    return app


class DesktopApi:
    """Exposed to the main window only (pywebview js_api): native pickers and the preview window."""

    def __init__(self, url: str):
        self.url = url
        self.window = None

    def pick_files(self):
        import webview  # noqa: PLC0415
        chosen = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True, file_types=(
            "Power BI and Data Factory (*.pbix;*.pbip;*.bim;*.json)", "All files (*.*)"))
        return list(chosen or [])

    def pick_folder(self):
        import webview  # noqa: PLC0415
        chosen = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        return list(chosen or [])

    def open_preview(self, item_id: str):
        import webview  # noqa: PLC0415
        # A separate window with no js_api: previews never reach the host bridge.
        webview.create_window("Documentation preview", f"{self.url}/api/items/{item_id}/view", width=1280, height=860)
        return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(*, pbi_tools=None, window=True, port=0) -> int:
    import uvicorn  # noqa: PLC0415
    report = diagnose(pbi_tools)
    pbix = report["inputs"]["pbix"]
    runner = Runner(History(home()), pbi_tools=report["pbi_tools"],
                    pbix_ready=None if pbix["available"] else pbix["reason"])
    port = port or _free_port()
    secret = secrets.token_urlsafe(32)
    app = create_app(runner, session_secret=secret, port=port, doctor=lambda: diagnose(pbi_tools))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           server_header=False))
    url = f"http://127.0.0.1:{port}"
    if not window:
        print(f"bidoc desktop on {url} (loopback only). Press Ctrl+C to stop.")
        try:
            server.run()
        finally:
            runner.shutdown()
        return 0
    try:
        import webview  # noqa: PLC0415
    except ImportError:
        print("The desktop window needs pywebview (pip install pywebview). Use --no-window to serve the app "
              "in your browser instead.")
        return 3
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    api = DesktopApi(url)
    main = webview.create_window("BI Documentation Generator", url, js_api=api, width=1200, height=820,
                                 min_size=(900, 600))
    api.window = main

    def closing():
        # Desktop processing is not a background service: offer to wait, or cancel and close.
        if runner.active():
            if not main.create_confirmation_dialog(
                    "Generation is still running", "Cancel the running work and close? Choose Cancel to keep "
                                                   "working; unfinished items can be retried later."):
                return False
            runner.shutdown(cancel=True)
        return True

    main.events.closing += closing
    webview.start()
    runner.shutdown()
    server.should_exit = True
    thread.join(timeout=5)
    return 0
