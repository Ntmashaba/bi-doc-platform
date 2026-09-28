"""HTTP API v1 (handoff section 7) over the local store.

Every error body is {"error": {"code", "message", "request_id", "details"}}; paths,
stack traces and credentials are never exposed. Protected routes return 401 for a
missing or invalid identity and 403 for an insufficient role.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import timedelta
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, File, Form, Header, Path, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from importlib import resources as _resources
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from bidoc_contracts import SCHEMA_VERSION, Limits

from . import __version__
from .access import AccessPolicy, Principal, forbidden, load_session_secret
from .config import Settings
from .derived import Derived
from .errors import LibraryError
from .jobs import Jobs
from .manual import ManualLinks
from .publishing import MAX_INSTALLER_BYTES, Publishing, require_transport
from .search import search as run_search
from .store import LocalStore

log = logging.getLogger("bidoc_library")
PREFIX = "/api/v1"
MULTIPART_OVERHEAD = 1024 * 1024
VIEW_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'self'")
DOWNLOAD_CSP = "sandbox; default-src 'none'"
SHELL_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
             "frame-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")
STATIC_FILES = {"app.js": "text/javascript", "search.js": "text/javascript", "protocol.js": "text/javascript",
                "styles.css": "text/css"}


# ---- response models (documented in OpenAPI) --------------------------------------

class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


class Classification(BaseModel):
    business_area: str
    environment: str
    owner: str


class Publication(BaseModel):
    asset_id: str
    environment_key: str
    scope_key: str


class Document(BaseModel):
    document_id: str
    document_type: Literal["power_bi", "adf"]
    classification: Classification
    title: str
    description: str
    tags: list[str]
    current_revision_id: str
    archived: bool
    created_at: str
    updated_at: str
    etag: str
    publication: Publication


class MetadataValues(BaseModel):
    title: str
    description: str
    tags: list[str]
    business_area: str
    owner: str


class OverrideProvenance(BaseModel):
    value: Any
    subject: Optional[str]
    reason: str
    set_at: str
    catalogue_sequence: int


class DocumentMetadata(BaseModel):
    document_id: str
    revision_id: str
    effective: MetadataValues
    revision: MetadataValues = Field(description="What the current revision's immutable artifact says")
    overrides: dict[str, OverrideProvenance]
    immutable: dict[str, str] = Field(description="Stream fields; never editable through metadata")
    etag: str
    catalogue_sequence: int


class MetadataPatch(BaseModel):
    model_config = {"extra": "allow"}   # unknown and immutable fields get a precise error from the store
    reason: str = Field(min_length=1, max_length=1000)


class MetadataChange(BaseModel):
    catalogue_sequence: int
    subject: Optional[str]
    reason: str
    changes: dict[str, Any]
    before: MetadataValues
    after: MetadataValues
    occurred_at: str


LEGACY_METADATA_HELP = ("JSON object with any of title, description, tags, business_area, environment, owner; "
                        "used only when the file is legacy engine HTML without a manifest")


class ImportPreview(BaseModel):
    input: Literal["envelope", "zip", "legacy"]
    document_type: Literal["power_bi", "adf"]
    document_id: Optional[str] = Field(description="null for legacy input: a new stream identity is assigned")
    outcome: Literal["new_document", "new_version", "duplicate"]
    duplicate_of: Optional[dict[str, str]]
    title: str
    description: str
    tags: list[str]
    classification: Classification
    generator: dict[str, str]
    source: dict[str, str]
    native_schema: str
    query_code: Literal["withheld", "included"]
    omissions: list[dict[str, str]]
    coverage_warnings: list[str]
    object_count: int
    section_count: int


class DocumentPage(BaseModel):
    items: list[Document]
    next_cursor: Optional[str]


class Revision(BaseModel):
    document_id: str
    revision_id: str
    title: str
    schema_version: int
    generated_at: str
    published_at: str
    publisher_subject: Optional[str]
    artifact_sha256: str
    content_sha256: str
    size_bytes: int
    section_count: int
    catalogue_sequence: int


class RevisionPage(BaseModel):
    items: list[Revision]
    next_cursor: Optional[str]


class ImportOutcome(BaseModel):
    import_id: str
    document_id: Optional[str]
    revision_id: Optional[str]
    status: str
    duplicate: bool
    catalogue_sequence: Optional[int]
    indexing_state: Optional[Literal["ready", "pending", "failed"]]


class ImportStatus(BaseModel):
    import_id: str
    state: Literal["validating", "prepared", "committed", "conflicted", "failed"]
    document_id: Optional[str]
    revision_id: Optional[str]
    duplicate: bool
    catalogue_sequence: Optional[int]
    indexing_state: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    created_at: str
    completed_at: Optional[str]


class Processing(BaseModel):
    engine: str
    input_types: list[str]
    available: bool
    reason: Optional[str]


class SearchSection(BaseModel):
    id: str
    title: str
    text: str


class SearchDocument(BaseModel):
    document_id: str
    revision_id: str
    document_type: Literal["power_bi", "adf"]
    classification: Classification
    title: str
    tags: list[str]
    sections: list[SearchSection]


class SearchIndex(BaseModel):
    generation: int
    state: Literal["ready", "updating", "stale"]
    documents: list[SearchDocument]


class SearchHit(BaseModel):
    document_id: str
    revision_id: str
    title: str
    document_type: str
    section_id: Optional[str]
    section_title: Optional[str]
    snippet: str


class SearchResults(BaseModel):
    generation: int
    state: str
    total: int = Field(description="all matches; items holds the first `limit`")
    items: list[SearchHit]


class ObjectView(BaseModel):
    target_id: str
    view_id: str
    args: dict


class DocumentObject(BaseModel):
    object_id: str
    kind: str
    label: str
    section_id: str
    parent_object_id: Optional[str]
    dynamic: bool
    opaque: bool
    coverage: str
    view: Optional[ObjectView]


class SectionTarget(BaseModel):
    section_id: str
    view: ObjectView


class ObjectPage(BaseModel):
    document_id: str
    revision_id: str
    items: list[DocumentObject]
    sections: list[SectionTarget]
    next_cursor: Optional[str]


class ManualLinkIn(BaseModel):
    source_document_id: uuid.UUID
    source_revision_id: uuid.UUID
    source_object_id: Optional[str] = None
    target_document_id: uuid.UUID
    target_revision_id: uuid.UUID
    target_object_id: Optional[str] = None
    expected_catalogue_sequence: int
    kind: Literal["related_to", "produces", "consumes", "deletes"]
    reason: str


class ManualLinkPatch(BaseModel):
    kind: Optional[Literal["related_to", "produces", "consumes", "deletes"]] = None
    reason: Optional[str] = None
    target_document_id: Optional[uuid.UUID] = None
    target_revision_id: Optional[uuid.UUID] = None
    target_object_id: Optional[str] = None
    expected_catalogue_sequence: Optional[int] = None


class ManualLink(BaseModel):
    relationship_id: str
    source_document_id: str
    source_revision_id: str
    source_object_id: Optional[str]
    target_document_id: str
    target_revision_id: str
    target_object_id: Optional[str]
    kind: str
    reason: str
    creator_subject: Optional[str]
    created_at: str
    updated_at: str
    etag: str
    status: str
    version: int



class JobOut(BaseModel):
    job_id: str
    input_type: str
    filename: str
    source_bytes: int
    document_id: Optional[str]
    requested_by: str
    state: Literal["queued", "leased", "running", "publishing", "cancel_requested", "succeeded", "failed",
                   "cancelled"]
    stage: Optional[str]
    attempt: int
    max_attempts: int
    worker_id: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    error: Optional[dict]
    output_document_id: Optional[str]
    output_revision_id: Optional[str]
    cancellation_requested: bool
    cancel_outcome: Optional[str]


class JobList(BaseModel):
    items: list[JobOut]


class WorkerRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    input_types: Optional[list[Literal["pbix", "pbip_zip"]]] = None


class WorkerOut(BaseModel):
    worker_id: str
    label: str
    enrolled_by: str
    enrolled_at: str
    allowed_input_types: list[str]
    input_types: list[str]
    engine_version: Optional[str]
    extractor_version: Optional[str]
    readiness: str
    readiness_detail: Optional[str]
    last_heartbeat_at: Optional[str]
    revoked_at: Optional[str]
    ready: bool


class EnrolledWorker(WorkerOut):
    token: str


class WorkerList(BaseModel):
    items: list[WorkerOut]


class Heartbeat(BaseModel):
    engine_version: str = Field(max_length=100)
    extractor_version: Optional[str] = Field(None, max_length=100)
    input_types: list[Literal["pbix", "pbip_zip"]]
    readiness: Literal["ready", "not_ready"]
    readiness_detail: Optional[str] = Field(None, max_length=500)


class LeaseBody(BaseModel):
    lease_token: str = Field(min_length=1, max_length=100)


class ProgressBody(LeaseBody):
    stage: Literal["downloading", "extracting", "analysing", "rendering", "uploading"]


class CompleteBody(LeaseBody):
    attempt_id: str = Field(min_length=1, max_length=64)
    staged_result_id: str = Field(min_length=1, max_length=64)


class FailBody(LeaseBody):
    error_code: str = Field(min_length=1, max_length=60)
    message: str = Field("", max_length=2000)
    retryable: bool = False


class ClaimedJob(JobOut):
    attempt_id: str


class Claim(BaseModel):
    job: ClaimedJob
    lease_token: str
    lease_expires_at: str
    input_download_url: str


class Renewal(BaseModel):
    lease_expires_at: str
    cancellation_requested: bool


class Staged(BaseModel):
    staged_result_id: str
    document_id: str
    revision_id: str


class Outcome(BaseModel):
    state: str
    document_id: Optional[str]
    revision_id: Optional[str]
    catalogue_sequence: Optional[int]
    cancel_outcome: Optional[str]

class Capabilities(BaseModel):
    version: str
    manifest_versions: list[int]
    limits: dict
    access_mode: str
    can_publish: bool
    processing: list[Processing]
    worker_status: Optional[str]
    installer_available: bool
    search_available: bool
    storage_backend: Literal["local", "azure"]
    worker_last_heartbeat_at: Optional[str] = Field(None, description="latest heartbeat of any enrolled worker")
    search_mode: Literal["client", "server"] = Field(
        description="client: download /search-index and search in the browser; server: the index is too large, "
                    "so load /search-index?sections=false for the catalogue and query /search")
    can_manage_relationships: bool
    can_administer: bool
    relationship_schema_version: str


class TokenRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100, description="e.g. the machine the generator runs on")
    expires_in_days: int = Field(ge=1, le=30)


class TokenInfo(BaseModel):
    token_id: str
    subject: str
    label: str
    scopes: list[str]
    created_at: str
    expires_at: str
    revoked_at: Optional[str]
    revoked_by: Optional[str]
    last_used_at: Optional[str]
    state: Literal["active", "expired", "revoked"]


class IssuedToken(TokenInfo):
    token: str = Field(description="Shown once. Store it in the generator; the library keeps only a hash")


class TokenList(BaseModel):
    items: list[TokenInfo]


class SubjectRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)


class PublishingCapabilities(BaseModel):
    version: str
    manifest_versions: list[int]
    limits: dict
    query_code_options: list[str]
    subject: str


class PublishingDocument(BaseModel):
    document_id: str
    document_type: str
    title: str
    current_revision_id: str
    etag: str
    publication: dict


class PublishingResult(BaseModel):
    import_id: str
    document_id: str
    revision_id: str
    current_revision_id: str
    title: str
    artifact_sha256: Optional[str]
    catalogue_sequence: Optional[int]
    duplicate: bool


class Release(BaseModel):
    version: str
    platform: str
    filename: str
    sha256: str
    size_bytes: int
    release_notes: str
    prerequisites: list[str]
    signed: bool
    label: str
    created_at: str
    approved: bool
    approved_at: Optional[str]
    download_url: Optional[str]


class ReleaseList(BaseModel):
    items: list[Release]


def _release_out(r: dict) -> dict:
    approved = bool(r.get("approved_at"))
    return {**{k: r[k] for k in ("version", "platform", "filename", "sha256", "size_bytes", "release_notes",
                                 "prerequisites", "signed", "label", "created_at")},
            "approved": approved, "approved_at": r.get("approved_at"),
            "download_url": f"{PREFIX}/releases/{r['version']}/download" if approved else None}


ERRORS = {code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 413, 422, 428)}


def _etag_header(value: str) -> str:
    return f'"{value}"'


def _if_match(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if value == "*" or value.startswith("W/"):
        raise LibraryError("INVALID_REQUEST", "If-Match must be one strong ETag from a previous response")
    return value.strip('"')


def _filename(title: str, revision_id: str) -> str:
    return (re.sub(r"[^A-Za-z0-9._ -]+", "_", title).strip(" .")[:80] or "document") + f"--{revision_id[:8]}.html"


def open_store(settings: Settings, limits: Limits):
    """The configured backend: LocalStore (SQLite + files) or AzureStore (Table + Blob)."""
    if settings.data_backend != "azure":
        return LocalStore(settings.local_data_dir, limits=limits)
    from .azure import AzureBlobs, AzureStore, AzureTables  # noqa: PLC0415 - optional dependency
    if settings.azure_connection_string:
        tables = AzureTables.from_connection_string(settings.azure_connection_string, settings.azure_table)
        blobs = AzureBlobs.from_connection_string(settings.azure_connection_string, settings.azure_container)
    else:
        from azure.identity import DefaultAzureCredential  # noqa: PLC0415
        credential = DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id or None)
        tables = AzureTables.from_identity(settings.azure_table_endpoint, settings.azure_table, credential)
        blobs = AzureBlobs.from_identity(settings.azure_blob_endpoint, settings.azure_container, credential)
    return AzureStore(tables, blobs, limits=limits)


def create_app(settings: Settings, store=None, session_secret: str | None = None) -> FastAPI:
    settings.validate()
    limits = Limits(html_bytes=settings.max_html_bytes, manifest_bytes=settings.max_manifest_bytes,
                    zip_bytes=settings.max_zip_bytes)
    upload_cap = max(limits.html_bytes, limits.zip_bytes)
    store = store or open_store(settings, limits)
    if settings.auth_mode == "local" and session_secret is None:
        session_secret = load_session_secret(settings.local_data_dir)
    policy = AccessPolicy(settings, session_secret)
    derived = Derived(store)
    manual = ManualLinks(store, derived)
    publishing = Publishing(store)
    jobs = Jobs(store, max_source_bytes=settings.max_source_bytes,
                retention=timedelta(hours=settings.source_retention_hours))

    def refresh_derived() -> str:
        """Bring search/relationships up to date after a committed change; never fails the change."""
        try:
            return "ready" if derived.refresh()["state"] == "ready" else "pending"
        except Exception:  # noqa: BLE001 - the change is committed; reads will retry and label state
            log.exception("derived state rebuild failed")
            return "failed"
    app = FastAPI(title="BI Documentation Platform library", version=__version__,
                  description="Shared Power BI and Data Factory documentation library, API v1.",
                  openapi_url=f"{PREFIX}/openapi.json", docs_url=None, redoc_url=None)
    app.state.store, app.state.settings, app.state.policy = store, settings, policy
    app.state.jobs = jobs
    try:
        jobs.cleanup()                        # also on start; the daily job does not depend on traffic
    except Exception:  # noqa: BLE001 - never block startup on housekeeping
        log.exception("job cleanup at startup failed")

    # ---- errors and cross-cutting headers ---------------------------------------

    def error(request: Request, status: int, code: str, message: str, details=None, headers=None):
        # Set the request ID here too: unhandled errors are answered outside the middleware.
        headers = {**(headers or {}), "X-Request-ID": request.state.request_id}
        return JSONResponse(status_code=status, headers=headers,
                            content={"error": {"code": code, "message": message,
                                               "request_id": request.state.request_id, "details": details or {}}})

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid.uuid4())
        caps = {f"{PREFIX}/jobs": settings.max_source_bytes}
        if request.method == "POST" and (request.url.path in caps or re.fullmatch(
                rf"{PREFIX}/worker/jobs/[^/]+/results", request.url.path)):
            length = request.headers.get("content-length")
            cap = caps.get(request.url.path, limits.html_bytes)
            if length is None:
                return error(request, 411, "LENGTH_REQUIRED", "uploads must declare Content-Length")
            if not length.isdigit() or int(length) > cap + MULTIPART_OVERHEAD:
                return error(request, 413, "PAYLOAD_TOO_LARGE", f"uploads are limited to {cap} bytes")
        if request.method == "POST" and request.url.path in (f"{PREFIX}/imports", f"{PREFIX}/publishing/imports"):
            length = request.headers.get("content-length")
            if length is None:
                return error(request, 411, "LENGTH_REQUIRED", "uploads must declare Content-Length")
            if not length.isdigit() or int(length) > upload_cap + MULTIPART_OVERHEAD:
                return error(request, 413, "PAYLOAD_TOO_LARGE",
                             f"uploads are limited to {limits.html_bytes} bytes of HTML or "
                             f"{limits.zip_bytes} bytes of ZIP")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(LibraryError)
    async def library_error(request: Request, exc: LibraryError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return error(request, exc.status, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        issues = [{"location": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "")}
                  for e in exc.errors()][:20]
        return error(request, 400, "INVALID_REQUEST", "the request is malformed", {"issues": issues})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return error(request, exc.status_code, code, str(exc.detail) if exc.status_code != 404 else "not found")

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error (request %s)", request.state.request_id)
        return error(request, 500, "INTERNAL_ERROR", "the library could not complete the request")

    # ---- identity ---------------------------------------------------------------

    def principal(request: Request) -> Principal:
        return policy.authenticate(method=request.method, host=request.headers.get("host"),
                                   origin=request.headers.get("origin"),
                                   client_ip=request.client.host if request.client else None,
                                   headers=request.headers)

    def reader(p: Principal = Depends(principal)) -> Principal:
        if not p.can("read"):
            raise forbidden("viewer role required")
        return p

    def publisher(p: Principal = Depends(principal)) -> Principal:
        if not p.can("publish"):
            raise forbidden("publisher role required")
        return p

    def admin(p: Principal = Depends(principal)) -> Principal:
        if not p.can("admin"):
            raise forbidden("administrator role required")
        return p

    # ---- library shell (static, no inline script) ----------------------------------

    static = _resources.files("bidoc_library").joinpath("static")

    @app.get("/", include_in_schema=False)
    def shell(p: Principal = Depends(reader)):
        page = static.joinpath("index.html").read_text(encoding="utf-8")
        # Local mode: the page carries the per-installation secret its own requests need. Other
        # sites cannot read it (no CORS, Host checked) and it never appears in any document.
        meta = (f'<meta name="bidoc-session" content="{session_secret}">'
                if settings.auth_mode == "local" and session_secret else "")
        return Response(page.replace("<!--BIDOC_SESSION-->", meta), media_type="text/html; charset=utf-8",
                        headers={"Content-Security-Policy": SHELL_CSP, "X-Frame-Options": "DENY"})

    @app.get("/static/{name}", include_in_schema=False)
    def static_file(name: str, p: Principal = Depends(reader)):
        if name not in STATIC_FILES:
            raise LibraryError("NOT_FOUND", "not found", 404)
        return Response(static.joinpath(name).read_bytes(), media_type=STATIC_FILES[name],
                        headers={"Cache-Control": "no-cache"})

    # ---- health and capabilities --------------------------------------------------

    @app.get(f"{PREFIX}/health/live", tags=["health"])
    def live():
        return {"status": "live"}

    @app.get(f"{PREFIX}/health/ready", tags=["health"], responses={503: {"model": ErrorResponse}})
    def ready(request: Request):
        try:
            store.catalogue_sequence()
        except Exception:  # noqa: BLE001 - readiness reports, it never raises
            return error(request, 503, "NOT_READY", "catalogue storage is unavailable")
        return {"status": "ready"}

    @app.get(f"{PREFIX}/capabilities", response_model=Capabilities, tags=["library"], responses=ERRORS)
    def capabilities(p: Principal = Depends(reader)):
        return {"version": __version__, "manifest_versions": [SCHEMA_VERSION],
                "limits": {"html_bytes": limits.html_bytes, "zip_bytes": limits.zip_bytes, "manifest_bytes": limits.manifest_bytes,
                           "section_text_bytes": limits.section_text_bytes, "source_bytes": settings.max_source_bytes},
                "storage_backend": getattr(store, "backend", "local"),
                "worker_last_heartbeat_at": jobs.last_heartbeat(),
                "access_mode": settings.auth_mode, "can_publish": p.can("publish"),
                "processing": jobs.processing()[0], "worker_status": jobs.processing()[1], "installer_available": bool(publishing.releases()), "search_available": True,
                "search_mode": search_mode(),
                "can_manage_relationships": p.can("publish"), "can_administer": p.can("admin"),
                "relationship_schema_version": "rel-rules/1"}

    # ---- documents --------------------------------------------------------------

    @app.get(f"{PREFIX}/documents", response_model=DocumentPage, tags=["documents"], responses=ERRORS)
    def list_documents(q: Optional[str] = Query(None, max_length=200),
                       document_type: Optional[Literal["power_bi", "adf"]] = None,
                       business_area: Optional[str] = None, environment: Optional[str] = None,
                       owner: Optional[str] = None, tag: Optional[str] = None, archived: bool = False,
                       cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                       p: Principal = Depends(reader)):
        if archived and not p.can("publish"):
            raise forbidden("only publishers can list archived documents")
        return store.list_documents(q=q, document_type=document_type, business_area=business_area,
                                    environment=environment, owner=owner, tag=tag, archived=archived,
                                    cursor=cursor, limit=limit)

    @app.get(f"{PREFIX}/documents/{{document_id}}", response_model=Document, tags=["documents"], responses=ERRORS)
    def get_document(document_id: uuid.UUID, response: Response, p: Principal = Depends(reader)):
        doc = store.get_document(str(document_id))
        if doc["archived"] and not p.can("publish"):
            raise LibraryError("NOT_FOUND", "document not found", 404)
        response.headers["ETag"] = _etag_header(doc["etag"])
        return doc

    @app.get(f"{PREFIX}/documents/{{document_id}}/metadata", response_model=DocumentMetadata, tags=["documents"],
             responses=ERRORS)
    def get_metadata(document_id: uuid.UUID, response: Response, p: Principal = Depends(reader)):
        get_document(document_id, response, p)          # same visibility rules as the document
        meta = store.get_metadata(str(document_id))
        response.headers["ETag"] = _etag_header(meta["etag"])
        return meta

    @app.patch(f"{PREFIX}/documents/{{document_id}}/metadata", response_model=DocumentMetadata,
               tags=["documents"], responses=ERRORS)
    def patch_metadata(document_id: uuid.UUID, body: MetadataPatch,
                       if_match: Optional[str] = Header(None, alias="If-Match"),
                       p: Principal = Depends(publisher)):
        """Catalogue overrides for title, description, tags, business_area and owner (null removes one).

        Stored artifacts are never modified; environment and scope are immutable stream fields.
        """
        changes = {k: v for k, v in (body.model_extra or {}).items()}
        meta = store.set_metadata(str(document_id), changes, body.reason, _if_match(if_match), p.subject)
        refresh_derived()
        return JSONResponse(meta, headers={"ETag": _etag_header(meta["etag"])})

    @app.get(f"{PREFIX}/documents/{{document_id}}/metadata/history", response_model=list[MetadataChange],
             tags=["documents"], responses=ERRORS)
    def metadata_history(document_id: uuid.UUID, p: Principal = Depends(publisher)):
        return store.metadata_history(str(document_id))

    @app.get(f"{PREFIX}/documents/{{document_id}}/revisions", response_model=RevisionPage, tags=["documents"],
             responses=ERRORS)
    def list_revisions(document_id: uuid.UUID, cursor: Optional[str] = None,
                       limit: int = Query(50, ge=1, le=200), p: Principal = Depends(reader)):
        get_document(document_id, Response(), p)
        return store.list_revisions(str(document_id), cursor=cursor, limit=limit)

    def _artifact(document_id, revision_id, p):
        doc = get_document(document_id, Response(), p)
        return doc, store.read_artifact(str(document_id), str(revision_id))

    @app.get(f"{PREFIX}/documents/{{document_id}}/revisions/{{revision_id}}/download", tags=["documents"],
             responses={200: {"content": {"text/html": {}}}, **ERRORS})
    def download(document_id: uuid.UUID, revision_id: uuid.UUID, p: Principal = Depends(reader)):
        doc, data = _artifact(document_id, revision_id, p)
        return Response(data, media_type="text/html; charset=utf-8", headers={
            "Content-Disposition": f'attachment; filename="{_filename(doc["title"], str(revision_id))}"',
            "Content-Security-Policy": DOWNLOAD_CSP})

    @app.get(f"{PREFIX}/documents/{{document_id}}/revisions/{{revision_id}}/view", tags=["documents"],
             responses={200: {"content": {"text/html": {}}}, **ERRORS})
    def view(document_id: uuid.UUID, revision_id: uuid.UUID, p: Principal = Depends(reader)):
        # Stored artifacts are regenerated by the trusted renderer; the response is still
        # sandboxed (opaque origin, no network, no forms, no top navigation). B06 adds the shell.
        _, data = _artifact(document_id, revision_id, p)
        return Response(data, media_type="text/html; charset=utf-8", headers={
            "Content-Security-Policy": VIEW_CSP, "Cross-Origin-Resource-Policy": "same-origin"})

    def _archive(action, document_id, if_match, p):
        doc = action(str(document_id), _if_match(if_match), p.subject)
        refresh_derived()
        return JSONResponse(doc, headers={"ETag": _etag_header(doc["etag"])})

    @app.post(f"{PREFIX}/documents/{{document_id}}/archive", response_model=Document, tags=["documents"],
              responses=ERRORS)
    def archive(document_id: uuid.UUID, if_match: Optional[str] = Header(None, alias="If-Match"),
                p: Principal = Depends(publisher)):
        return _archive(store.archive, document_id, if_match, p)

    @app.post(f"{PREFIX}/documents/{{document_id}}/restore", response_model=Document, tags=["documents"],
              responses=ERRORS)
    def restore(document_id: uuid.UUID, if_match: Optional[str] = Header(None, alias="If-Match"),
                p: Principal = Depends(publisher)):
        return _archive(store.restore, document_id, if_match, p)

    # ---- imports ----------------------------------------------------------------

    @app.post(f"{PREFIX}/imports", response_model=ImportOutcome, status_code=201, tags=["imports"],
              responses={200: {"model": ImportOutcome, "description": "Duplicate of an earlier import"},
                         411: {"model": ErrorResponse}, **ERRORS})
    async def create_import(file: UploadFile = File(..., description="Envelope-v1 HTML artifact, or known legacy "
                                                                     "engine HTML to convert"),
                            target_document_id: Optional[uuid.UUID] = Form(None),
                            query_code: Literal["withheld", "included"] = Form("withheld"),
                            legacy_metadata: Optional[str] = Form(None, max_length=10000,
                                                                  description=LEGACY_METADATA_HELP),
                            idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1,
                                                          max_length=200),
                            if_match: Optional[str] = Header(None, alias="If-Match"),
                            p: Principal = Depends(publisher)):
        return await run_import(file, p.subject, idempotency_key, if_match, target_document_id, query_code,
                                legacy_metadata)

    async def run_import(file, subject, idempotency_key, if_match, target_document_id, query_code, legacy_metadata):
        """One import path for browser and direct publishing: the same validation and commit."""
        data = await file.read(upload_cap + 1)
        cap = limits.zip_bytes if data[:4] == b"PK\x03\x04" else limits.html_bytes
        if len(data) > cap:
            raise LibraryError("PAYLOAD_TOO_LARGE", f"the upload exceeds the {cap}-byte limit", 413)
        outcome = await run_in_threadpool(
            store.publish, data, subject=subject, idempotency_key=idempotency_key,
            expected_etag=_if_match(if_match), target_document_id=str(target_document_id) if target_document_id else None,
            query_code=query_code, legacy_metadata=legacy_metadata)
        indexing = await run_in_threadpool(refresh_derived)
        body = {"import_id": outcome["import_id"], "document_id": outcome["document_id"],
                "revision_id": outcome["revision_id"], "status": "completed", "duplicate": outcome["duplicate"],
                "catalogue_sequence": outcome["catalogue_sequence"], "indexing_state": indexing}
        return JSONResponse(body, status_code=200 if outcome["duplicate"] else 201)

    @app.post(f"{PREFIX}/imports/preview", response_model=ImportPreview, tags=["imports"],
              responses={411: {"model": ErrorResponse}, **ERRORS})
    async def preview_import(file: UploadFile = File(...),
                             query_code: Literal["withheld", "included"] = Form("withheld"),
                             legacy_metadata: Optional[str] = Form(None, max_length=10000,
                                                                   description=LEGACY_METADATA_HELP),
                             p: Principal = Depends(publisher)):
        """Detected identity, title, outcome and projection omissions; nothing is stored."""
        data = await file.read(upload_cap + 1)
        cap = limits.zip_bytes if data[:4] == b"PK\x03\x04" else limits.html_bytes
        if len(data) > cap:
            raise LibraryError("PAYLOAD_TOO_LARGE", f"the upload exceeds the {cap}-byte limit", 413)
        return await run_in_threadpool(store.preview, data, legacy_metadata=legacy_metadata, query_code=query_code)

    @app.get(f"{PREFIX}/imports/{{import_id}}", response_model=ImportStatus, tags=["imports"], responses=ERRORS)
    def get_import(import_id: uuid.UUID, p: Principal = Depends(publisher)):
        return store.get_import(str(import_id))

    # ---- search -----------------------------------------------------------------

    @app.get(f"{PREFIX}/search-index", response_model=SearchIndex, tags=["search"],
             responses={304: {"description": "Not modified"}, **ERRORS})
    def search_index(request: Request, sections: bool = True, p: Principal = Depends(reader)):
        """The whole search index, or with `sections=false` only the catalogue fields (server search mode)."""
        index = derived.search_index()
        etag = _etag_header(f"search-{index['generation']}-{index['state']}-{int(sections)}")
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        if not sections:
            index = {**index, "documents": [{**d, "sections": []} for d in index["documents"]]}
        return JSONResponse(index, headers={"ETag": etag, "Cache-Control": "private, no-cache"})

    index_size = {"key": None, "bytes": 0}

    def search_mode() -> str:
        index = derived.search_index()
        key = (index["generation"], index["state"])
        if index_size["key"] != key:
            index_size.update(key=key, bytes=len(json.dumps(index, separators=(",", ":")).encode()))
        return "server" if index_size["bytes"] > settings.client_search_index_bytes else "client"

    @app.get(f"{PREFIX}/search", response_model=SearchResults, tags=["search"], responses=ERRORS)
    def search(q: Optional[str] = Query(None, max_length=500),
               document_type: Optional[Literal["power_bi", "adf"]] = None, business_area: Optional[str] = None,
               environment: Optional[str] = None, owner: Optional[str] = None, tag: Optional[str] = None,
               limit: int = Query(50, ge=1, le=500), p: Principal = Depends(reader)):
        """The same matching and ranking as the browser search (search semantics v1)."""
        index = derived.search_index()
        items = run_search(index["documents"], q, document_type=document_type, business_area=business_area,
                           environment=environment, owner=owner, tag=tag)
        return {"generation": index["generation"], "state": index["state"], "total": len(items),
                "items": items[:limit]}

    # ---- objects and relationships ------------------------------------------------

    def _revision(document_id, revision_id, p):
        doc = get_document(document_id, Response(), p)
        rev = str(revision_id) if revision_id else doc["current_revision_id"]
        store.read_artifact(str(document_id), rev)              # committed and intact, or 404
        return rev

    @app.get(f"{PREFIX}/documents/{{document_id}}/objects", response_model=ObjectPage, tags=["relationships"],
             responses=ERRORS)
    def objects(document_id: uuid.UUID, revision_id: Optional[uuid.UUID] = None, cursor: Optional[int] = None,
                limit: int = Query(200, ge=1, le=1000), p: Principal = Depends(reader)):
        rev = _revision(document_id, revision_id, p)
        items = derived.objects(str(document_id), rev)
        start = cursor or 0
        return {"document_id": str(document_id), "revision_id": rev, "items": items[start:start + limit],
                "sections": derived.section_targets(str(document_id), rev),
                "next_cursor": str(start + limit) if start + limit < len(items) else None}

    @app.get(f"{PREFIX}/documents/{{document_id}}/relationships", tags=["relationships"], responses=ERRORS)
    def relationships(document_id: uuid.UUID, revision_id: Optional[uuid.UUID] = None,
                      generation_id: Optional[uuid.UUID] = None, object_id: Optional[str] = Query(None, max_length=512),
                      p: Principal = Depends(reader)):
        _revision(document_id, revision_id, p)
        return derived.relationships(str(document_id), revision_id=str(revision_id) if revision_id else None,
                                     generation_id=str(generation_id) if generation_id else None,
                                     object_id=object_id, can_see_archived=p.can("publish"))

    def _link_response(record, status=200):
        return JSONResponse(record, status_code=status, headers={"ETag": _etag_header(record["etag"])})

    @app.post(f"{PREFIX}/relationships/manual", response_model=ManualLink, status_code=201, tags=["relationships"],
              responses=ERRORS)
    def create_manual(body: ManualLinkIn, p: Principal = Depends(publisher)):
        data = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in body.model_dump().items()}
        record = manual.create(data, p.subject)
        refresh_derived()
        return _link_response(record, 201)

    @app.get(f"{PREFIX}/relationships/manual/{{relationship_id}}", response_model=ManualLink, tags=["relationships"],
             responses=ERRORS)
    def get_manual(relationship_id: uuid.UUID, p: Principal = Depends(reader)):
        return _link_response(manual.get(str(relationship_id)))

    @app.patch(f"{PREFIX}/relationships/manual/{{relationship_id}}", response_model=ManualLink,
               tags=["relationships"], responses=ERRORS)
    def patch_manual(relationship_id: uuid.UUID, body: ManualLinkPatch,
                     if_match: Optional[str] = Header(None, alias="If-Match"), p: Principal = Depends(publisher)):
        changes = {k: (str(v) if isinstance(v, uuid.UUID) else v)
                   for k, v in body.model_dump(exclude_unset=True).items()}
        record = manual.update(str(relationship_id), _if_match(if_match), changes, p.subject)
        refresh_derived()
        return _link_response(record)

    @app.delete(f"{PREFIX}/relationships/manual/{{relationship_id}}", status_code=204, tags=["relationships"],
                responses=ERRORS)
    def delete_manual(relationship_id: uuid.UUID, if_match: Optional[str] = Header(None, alias="If-Match"),
                      p: Principal = Depends(publisher)):
        manual.delete(str(relationship_id), _if_match(if_match), p.subject)
        refresh_derived()
        return Response(status_code=204)

    @app.get(f"{PREFIX}/relationships/manual/{{relationship_id}}/audit", tags=["relationships"], responses=ERRORS)
    def manual_audit(relationship_id: uuid.UUID, p: Principal = Depends(publisher)):
        return {"items": manual.audit(str(relationship_id))}

    # ---- publishing tokens (browser identity; B11) -------------------------------------

    @app.post(f"{PREFIX}/publish-tokens", status_code=201, response_model=IssuedToken, tags=["publishing"],
              responses=ERRORS)
    def issue_token(body: TokenRequest, p: Principal = Depends(publisher)):
        """A publishing token for the desktop generator. The secret is returned once and never stored."""
        return publishing.issue(p.subject, body.label, body.expires_in_days)

    @app.get(f"{PREFIX}/publish-tokens", response_model=TokenList, tags=["publishing"], responses=ERRORS)
    def list_tokens(all: bool = False, p: Principal = Depends(publisher)):
        if all and not p.can("admin"):
            raise forbidden("only administrators can list every token")
        return {"items": publishing.list(p.subject, everyone=all)}

    @app.delete(f"{PREFIX}/publish-tokens/{{token_id}}", status_code=204, tags=["publishing"], responses=ERRORS)
    def revoke_token(token_id: str = Path(..., pattern=r"^[0-9a-f]{16}$"), p: Principal = Depends(publisher)):
        publishing.revoke(token_id, p)
        return Response(status_code=204)

    @app.post(f"{PREFIX}/publish-tokens/revoke-subject", tags=["publishing"], responses=ERRORS)
    def revoke_subject(body: SubjectRequest, p: Principal = Depends(admin)):
        """Revoke all of a subject's tokens, e.g. when their publisher role is removed."""
        return {"revoked": publishing.revoke_subject(body.subject, p)}

    # ---- direct publishing namespace: publishing tokens only (handoff 17.5) ------------

    def token_principal(request: Request) -> Principal:
        require_transport(request, settings, policy.proxies)
        if settings.auth_mode == "local" and (request.headers.get("host") or "").lower() not in policy.local_hosts:
            raise forbidden("requests must address the loopback host")
        return publishing.authenticate(request.headers.get("authorization"))

    def own_import(import_id, p):
        imp = store.get_import(import_id)
        if imp["subject"] != p.subject:
            raise LibraryError("NOT_FOUND", "import not found", 404)
        return imp

    @app.get(f"{PREFIX}/publishing/capabilities", response_model=PublishingCapabilities, tags=["publishing"],
             responses=ERRORS)
    def publishing_capabilities(p: Principal = Depends(token_principal)):
        return {"version": __version__, "manifest_versions": [1], "limits": {"html_bytes": limits.html_bytes},
                "query_code_options": ["withheld", "included"], "subject": p.subject}

    @app.get(f"{PREFIX}/publishing/documents/{{document_id}}", response_model=PublishingDocument,
             tags=["publishing"], responses=ERRORS)
    def publishing_document(document_id: uuid.UUID, response: Response, p: Principal = Depends(token_principal)):
        """The current ETag of one explicitly requested document, to publish a new version of it."""
        doc = store.get_document(str(document_id))
        if doc["archived"]:
            raise LibraryError("NOT_FOUND", "document not found", 404)
        response.headers["ETag"] = _etag_header(doc["etag"])
        return {"document_id": doc["document_id"], "document_type": doc["document_type"], "title": doc["title"],
                "current_revision_id": doc["current_revision_id"], "etag": doc["etag"],
                "publication": doc["publication"]}

    @app.post(f"{PREFIX}/publishing/imports", response_model=ImportOutcome, status_code=201, tags=["publishing"],
              responses={200: {"model": ImportOutcome, "description": "Duplicate of an earlier import"},
                         411: {"model": ErrorResponse}, **ERRORS})
    async def publishing_import(file: UploadFile = File(..., description="Envelope-v1 HTML artifact"),
                                target_document_id: Optional[uuid.UUID] = Form(None),
                                query_code: Literal["withheld", "included"] = Form("withheld"),
                                idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1,
                                                              max_length=200),
                                if_match: Optional[str] = Header(None, alias="If-Match"),
                                p: Principal = Depends(token_principal)):
        return await run_import(file, p.subject, idempotency_key, if_match, target_document_id, query_code, None)

    @app.get(f"{PREFIX}/publishing/imports/{{import_id}}", response_model=ImportStatus, tags=["publishing"],
             responses=ERRORS)
    def publishing_import_status(import_id: uuid.UUID, p: Principal = Depends(token_principal)):
        return own_import(str(import_id), p)

    @app.get(f"{PREFIX}/publishing/results/{{import_id}}", response_model=PublishingResult, tags=["publishing"],
             responses=ERRORS)
    def publishing_result(import_id: uuid.UUID, p: Principal = Depends(token_principal)):
        """Safe readback of an own committed import: what the library now holds."""
        imp = own_import(str(import_id), p)
        if imp["state"] != "committed":
            raise LibraryError("IMPORT_INCOMPLETE", "the import has not committed", 409, {"state": imp["state"]})
        doc = store.get_document(imp["document_id"])
        rev = next((r for r in store.list_revisions(imp["document_id"], limit=200)["items"]
                    if r["revision_id"] == imp["revision_id"]), None)
        return {"import_id": imp["import_id"], "document_id": imp["document_id"], "revision_id": imp["revision_id"],
                "current_revision_id": doc["current_revision_id"], "title": doc["title"],
                "artifact_sha256": rev["artifact_sha256"] if rev else None,
                "catalogue_sequence": imp["catalogue_sequence"], "duplicate": imp["duplicate"]}

    # ---- installer releases (B11) ---------------------------------------------------------

    @app.get(f"{PREFIX}/releases", response_model=ReleaseList, tags=["releases"], responses=ERRORS)
    def list_releases(p: Principal = Depends(reader)):
        """Approved releases; administrators also see ones awaiting approval."""
        return {"items": [_release_out(r) for r in publishing.releases(include_unapproved=p.can("admin"))]}

    @app.post(f"{PREFIX}/releases", status_code=201, response_model=Release, tags=["releases"],
              responses={411: {"model": ErrorResponse}, **ERRORS})
    async def add_release(file: UploadFile = File(...), version: str = Form(...), sha256: str = Form(...),
                          release_notes: str = Form(""), prerequisites: str = Form("[]"),
                          label: str = Form("development build (unsigned)"), signed: bool = Form(False),
                          platform: Literal["windows-x64"] = Form("windows-x64"), p: Principal = Depends(admin)):
        try:
            prereq = json.loads(prerequisites)
        except ValueError:
            raise LibraryError("INVALID_REQUEST", "prerequisites must be a JSON list") from None
        data = await file.read(MAX_INSTALLER_BYTES + 1)
        record = await run_in_threadpool(
            publishing.add_release, version=version, platform=platform, filename=file.filename or "",
            data=data, sha256=sha256, release_notes=release_notes, prerequisites=prereq, label=label,
            signed=signed, subject=p.subject)
        return _release_out(record)

    @app.post(f"{PREFIX}/releases/{{version}}/approve", response_model=Release, tags=["releases"], responses=ERRORS)
    def approve_release(version: str, p: Principal = Depends(admin)):
        return _release_out(publishing.approve(version, p.subject))

    @app.get(f"{PREFIX}/releases/latest", response_model=Release, tags=["releases"], responses=ERRORS)
    def latest_release(platform: Literal["windows-x64"] = "windows-x64", p: Principal = Depends(reader)):
        return _release_out(publishing.latest(platform))

    @app.get(f"{PREFIX}/releases/{{version}}/download", tags=["releases"], responses=ERRORS)
    def release_download(version: str, p: Principal = Depends(reader)):
        record, data = publishing.download(version)
        return Response(data, media_type="application/vnd.microsoft.portable-executable", headers={
            "Content-Disposition": f'attachment; filename="{record["filename"]}"',
            "Content-Security-Policy": DOWNLOAD_CSP, "X-Checksum-SHA256": record["sha256"]})

    # ---- processing jobs (R3, B14): browser side ---------------------------------------------

    @app.post(f"{PREFIX}/jobs", status_code=202, response_model=JobOut, tags=["jobs"], responses=ERRORS)
    async def create_job(file: UploadFile = File(..., description="A PBIX file, or a ZIP of a PBIP project"),
                         input_type: Literal["pbix", "pbip_zip"] = Form(...),
                         document_id: Optional[uuid.UUID] = Form(None, description="publish as a new version of this"),
                         p: Principal = Depends(publisher)):
        """Queue a source for a processing worker; 409 WORKER_UNAVAILABLE when none is ready."""
        return await run_in_threadpool(jobs.submit, file.file, filename=file.filename or "", input_type=input_type,
                                       document_id=str(document_id) if document_id else None, principal=p)

    @app.get(f"{PREFIX}/jobs", response_model=JobList, tags=["jobs"], responses=ERRORS)
    def list_jobs(p: Principal = Depends(publisher)):
        return {"items": jobs.list(p)}

    @app.get(f"{PREFIX}/jobs/{{job_id}}", response_model=JobOut, tags=["jobs"], responses=ERRORS)
    def get_job(job_id: uuid.UUID, p: Principal = Depends(publisher)):
        return jobs.get(str(job_id), p)

    @app.post(f"{PREFIX}/jobs/{{job_id}}/cancel", status_code=202, response_model=JobOut, tags=["jobs"],
              responses=ERRORS)
    def cancel_job(job_id: uuid.UUID, p: Principal = Depends(publisher)):
        return jobs.cancel(str(job_id), p)

    @app.post(f"{PREFIX}/jobs/{{job_id}}/retry", status_code=202, response_model=JobOut, tags=["jobs"],
              responses=ERRORS)
    def retry_job(job_id: uuid.UUID, p: Principal = Depends(publisher)):
        return jobs.retry(str(job_id), p)

    @app.post(f"{PREFIX}/workers", status_code=201, response_model=EnrolledWorker, tags=["workers"], responses=ERRORS)
    def enroll_worker(body: WorkerRequest, p: Principal = Depends(admin)):
        """Enroll a processing worker. The token is shown once."""
        return jobs.enroll(body.label, body.input_types, p)

    @app.get(f"{PREFIX}/workers", response_model=WorkerList, tags=["workers"], responses=ERRORS)
    def list_workers(p: Principal = Depends(admin)):
        return {"items": jobs.workers()}

    @app.delete(f"{PREFIX}/workers/{{worker_id}}", status_code=204, tags=["workers"], responses=ERRORS)
    def revoke_worker(worker_id: str, p: Principal = Depends(admin)):
        jobs.revoke_worker(worker_id, p)
        return Response(status_code=204)

    # ---- worker namespace: worker tokens only (handoff 10) --------------------------------

    def worker_principal(request: Request) -> dict:
        require_transport(request, settings, policy.proxies)
        if settings.auth_mode == "local" and (request.headers.get("host") or "").lower() not in policy.local_hosts:
            raise forbidden("requests must address the loopback host")
        return jobs.authenticate(request.headers.get("authorization"))

    W = f"{PREFIX}/worker"

    @app.post(f"{W}/heartbeat", status_code=204, tags=["worker"], responses=ERRORS)
    def worker_heartbeat(body: Heartbeat, w: dict = Depends(worker_principal)):
        jobs.heartbeat(w, engine_version=body.engine_version, extractor_version=body.extractor_version,
                       input_types=body.input_types, readiness=body.readiness, readiness_detail=body.readiness_detail)
        return Response(status_code=204)

    @app.post(f"{W}/claim", tags=["worker"], response_model=Claim, responses={204: {"description": "No job"},
                                                                              **ERRORS})
    def worker_claim(w: dict = Depends(worker_principal)):
        got = jobs.claim(w)
        if got is None:
            return Response(status_code=204)
        job, token, expires = got
        return {"job": {**jobs.public(job), "attempt_id": job["attempt_id"]}, "lease_token": token,
                "lease_expires_at": expires, "input_download_url": f"{W}/jobs/{job['job_id']}/source"}

    @app.get(f"{W}/jobs/{{job_id}}/source", tags=["worker"], responses=ERRORS)
    def worker_source(job_id: uuid.UUID, lease_token: str = Header(..., alias="X-Lease-Token"),
                      w: dict = Depends(worker_principal)):
        job, chunks = jobs.source(str(job_id), w, lease_token)
        return StreamingResponse(chunks, media_type="application/octet-stream",
                                 headers={"Content-Length": str(job["source_bytes"])})

    @app.post(f"{W}/jobs/{{job_id}}/renew", response_model=Renewal, tags=["worker"], responses=ERRORS)
    def worker_renew(job_id: uuid.UUID, body: LeaseBody, w: dict = Depends(worker_principal)):
        return jobs.renew(str(job_id), w, body.lease_token)

    @app.post(f"{W}/jobs/{{job_id}}/progress", status_code=204, tags=["worker"], responses=ERRORS)
    def worker_progress(job_id: uuid.UUID, body: ProgressBody, w: dict = Depends(worker_principal)):
        jobs.progress(str(job_id), w, body.lease_token, body.stage)
        return Response(status_code=204)

    @app.post(f"{W}/jobs/{{job_id}}/results", status_code=201, response_model=Staged, tags=["worker"],
              responses=ERRORS)
    async def worker_results(job_id: uuid.UUID, file: UploadFile = File(...), lease_token: str = Form(...),
                             attempt_id: str = Form(...), w: dict = Depends(worker_principal)):
        """Stage an immutable candidate for this attempt; nothing is published yet."""
        data = await file.read(limits.html_bytes + 1)
        if len(data) > limits.html_bytes:
            raise LibraryError("PAYLOAD_TOO_LARGE", f"the result exceeds the {limits.html_bytes}-byte limit", 413)
        return await run_in_threadpool(jobs.stage_result, str(job_id), w, lease_token, attempt_id, data)

    @app.post(f"{W}/jobs/{{job_id}}/complete", response_model=Outcome, tags=["worker"], responses=ERRORS)
    def worker_complete(job_id: uuid.UUID, body: CompleteBody, w: dict = Depends(worker_principal)):
        """The server publishes the staged result as the requesting user, under the current lease."""
        out = jobs.complete(str(job_id), w, body.lease_token, body.attempt_id, body.staged_result_id)
        if out["state"] == "succeeded":
            refresh_derived()
        return out

    @app.post(f"{W}/jobs/{{job_id}}/fail", response_model=JobOut, tags=["worker"], responses=ERRORS)
    def worker_fail(job_id: uuid.UUID, body: FailBody, w: dict = Depends(worker_principal)):
        return jobs.fail(str(job_id), w, body.lease_token, code=body.error_code, message=body.message,
                         retryable=body.retryable)

    return app
