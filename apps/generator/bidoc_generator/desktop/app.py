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

from ..batch import Options, Runner
from ..backend import for_runner
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
    review_id: Optional[str] = Field(None, max_length=100)    # queue exactly the list that review showed


class LibraryIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    token: str = Field(min_length=1, max_length=200)


class PublishIn(BaseModel):
    include_query_code: bool = False


class ReviewIn(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=500)
    output_dir: Optional[str] = Field(None, max_length=4096)  # left out of the search, like `bidoc batch` does


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
        return {"active": runner.active(), "interrupted_on_start": history.interrupted,
                "pbix_backend": runner.backend.as_dict() if runner.backend is not None else None}

    reviews: dict[str, dict] = {}              # review_id -> the list a person was shown; the last few only
    reviews_lock = threading.Lock()

    @app.post("/api/review")
    def review(body: ReviewIn):
        """Batch review: what each input is, before anything runs. Folders are searched exactly as `bidoc batch` does,
        and submitting with the returned `review_id` queues this very list, not a fresh scan."""
        pbix = report()["inputs"]["pbix"]
        scan = runner.discover(body.inputs, body.output_dir)
        for it in scan.items:
            if it.get("kind") == "pbix" and not pbix["available"]:
                it["warnings"] = [f"PBIX generation is unavailable: {pbix['reason']}"]
        review_id = secrets.token_urlsafe(12)
        with reviews_lock:
            reviews[review_id] = {"items": scan.items, "output_dir": body.output_dir, "inputs": list(body.inputs)}
            while len(reviews) > 8:
                reviews.pop(next(iter(reviews)))
        return {"review_id": review_id, "items": scan.items, "warnings": scan.warnings, "summary": scan.as_dict()}

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
        if body.review_id:
            with reviews_lock:
                reviewed = reviews.get(body.review_id)
            if reviewed is None:
                raise ApiError(409, "REVIEW_EXPIRED", "That review is no longer available; review the inputs again.")
            if reviewed["output_dir"] != body.output_dir:
                raise ApiError(409, "REVIEW_CHANGED", "The output folder changed since the review; review the inputs again.")
            if reviewed["inputs"] != list(body.inputs):
                raise ApiError(409, "REVIEW_CHANGED", "The inputs changed since the review; review them again.")
            return history.batch(runner.submit_items(reviewed["items"], opts))
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

    # ---- library connection and direct publishing (B11) -----------------------------

    def _library():
        from ..credentials import load_token  # noqa: PLC0415
        from ..doctor import config  # noqa: PLC0415
        url = config().get("library_url")
        return url, (load_token(url) if url else None)

    @app.get("/api/library")
    def library_status():
        from ..credentials import STORE  # noqa: PLC0415
        from ..publisher import LibraryClient, PublishError  # noqa: PLC0415
        url, token = _library()
        if not url or not token:
            return {"state": "not_connected", "url": url, "token_store": STORE}
        try:
            caps = LibraryClient(url, token).capabilities()
        except PublishError as exc:
            state = "credential_rejected" if exc.code == "CREDENTIAL_REJECTED" else "unreachable"
            return {"state": state, "url": url, "message": str(exc), "token_store": STORE}
        return {"state": "connected", "url": url, "subject": caps["subject"], "token_store": STORE}

    @app.post("/api/library")
    def library_connect(body: LibraryIn):
        from ..credentials import save_token  # noqa: PLC0415
        from ..doctor import config  # noqa: PLC0415
        from ..publisher import LibraryClient, PublishError, normalize_url  # noqa: PLC0415
        try:
            url = normalize_url(body.url)
            LibraryClient(url, body.token.strip()).capabilities()
        except PublishError as exc:
            raise ApiError(400 if exc.code in ("INVALID_URL", "INSECURE_URL", "CREDENTIAL_REJECTED") else 502,
                           exc.code, str(exc)) from None
        save_token(url, body.token.strip())
        settings = config()
        settings["library_url"] = url
        home().mkdir(parents=True, exist_ok=True)
        import json  # noqa: PLC0415
        (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        return library_status()

    @app.delete("/api/library")
    def library_disconnect():
        import json  # noqa: PLC0415

        from ..credentials import delete_token  # noqa: PLC0415
        from ..doctor import config  # noqa: PLC0415
        settings = config()
        url = settings.pop("library_url", None)
        if url:
            delete_token(url)
            (home() / "config.json").write_text(json.dumps(settings, indent=1), encoding="utf-8")
        return {"state": "not_connected", "url": None}

    @app.post("/api/items/{item_id}/publish")
    def publish_item(item_id: str, body: PublishIn):
        from ..publisher import LibraryClient, PublishError  # noqa: PLC0415
        item = _item(item_id)
        if item["state"] != "completed" or not item["artifact_path"]:
            raise ApiError(409, "NOT_PUBLISHABLE", "only completed documents with a publication manifest can be "
                                                   "published")
        url, token = _library()
        if not url or not token:
            raise ApiError(409, "NOT_CONNECTED", "connect to a library first")
        try:
            r = LibraryClient(url, token).publish(Path(item["artifact_path"]).read_bytes(),
                                                  filename=Path(item["artifact_path"]).name,
                                                  query_code="included" if body.include_query_code else "withheld")
        except PublishError as exc:
            raise ApiError(409 if exc.status in (409, 428, 422, 401) else 502, exc.code, str(exc)) from None
        return {**r.__dict__, "library_url": url}

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
            "BI models and factories (*.pbix;*.abf;*.pbip;*.bim;*.xmla;*.json)", "All files (*.*)"))
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


def build_runner(pbi_tools=None) -> Runner:
    """The batch runner for the desktop app: PBIX backend and readiness from the shared policy (backend.for_runner).
    `pbi_tools` is only an explicit --pbi-tools; a saved or PATH pbi-tools is found by the policy."""
    tool, pbix_ready, selection = for_runner(pbi_tools)
    return Runner(History(home()), pbi_tools=tool, pbix_ready=pbix_ready, backend=selection)


def run(*, pbi_tools=None, window=True, port=0) -> int:
    import uvicorn  # noqa: PLC0415
    runner = build_runner(pbi_tools)
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
