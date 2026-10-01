"""Direct publishing to a library (handoff 8 and 17.5; B11).

- The library URL must be https://, or http:// on a loopback address.
- Redirects are never followed, so the token is never forwarded to another host.
- Each publication has a stable Idempotency-Key (artifact digest plus precondition), so a
  retry after a lost response returns the original outcome instead of publishing twice.
- Connection failures are retried a few times with the same key; library errors are not.
- The token appears only in the Authorization header. It is never printed or logged.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

TIMEOUT = 60
RETRIES = 3


class PublishError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.status = code, status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublishError("REDIRECT_REFUSED", f"the library answered with a redirect ({code}); check the URL. "
                                               "Redirects are not followed, so the token is never forwarded.", code)


def normalize_url(url: str) -> str:
    """The library base URL, or PublishError when it is not safe to send a token to."""
    parts = urllib.parse.urlsplit((url or "").strip())
    if parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.query or parts.fragment:
        raise PublishError("INVALID_URL", "give the library address, e.g. https://docs.example.com")
    if parts.scheme == "http":
        host = parts.hostname
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise PublishError("INSECURE_URL", "publishing tokens are only sent over https:// (http:// only to this "
                                               "computer)")
    path = parts.path.rstrip("/")
    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))


@dataclass
class Result:
    status: str                     # published | duplicate
    document_id: str
    revision_id: str
    import_id: str
    title: str
    catalogue_sequence: int | None
    new_document: bool


class LibraryClient:
    def __init__(self, url: str, token: str):
        self.base = normalize_url(url) + "/api/v1/publishing"
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def __repr__(self):                                    # never show the token
        return f"LibraryClient({self.base!r})"

    def _request(self, method, path, *, body=None, headers=None, retry=False):
        attempts = RETRIES if retry else 1
        for attempt in range(attempts):
            req = urllib.request.Request(self.base + path, data=body, method=method, headers={
                "Authorization": f"Bearer {self._token}", "Accept": "application/json", **(headers or {})})
            try:
                with self._opener.open(req, timeout=TIMEOUT) as resp:
                    return resp.status, resp.headers, json.loads(resp.read() or b"null")
            except urllib.error.HTTPError as exc:
                try:
                    err = json.loads(exc.read() or b"{}").get("error", {})
                except ValueError:
                    err = {}
                if exc.code == 404 and method == "GET" and path.startswith("/documents/"):
                    return 404, exc.headers, None
                code = err.get("code") or f"HTTP_{exc.code}"
                if exc.code == 401:
                    raise PublishError("CREDENTIAL_REJECTED", "the library did not accept the publishing token "
                                                              "(unknown, expired or revoked). Connect again with a "
                                                              "new token.", 401) from None
                raise PublishError(code, err.get("message") or exc.reason, exc.code) from None
            except PublishError:
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt + 1 == attempts:
                    raise PublishError("LIBRARY_UNREACHABLE", f"could not reach the library: "
                                                              f"{getattr(exc, 'reason', exc)}") from None
                time.sleep(2 ** attempt)

    def capabilities(self) -> dict:
        return self._request("GET", "/capabilities", retry=True)[2]

    def document(self, document_id: str):
        status, headers, body = self._request("GET", f"/documents/{document_id}", retry=True)
        return None if status == 404 else {**body, "etag_header": headers.get("ETag")}   # case-insensitive

    def publish(self, artifact: bytes, *, filename: str = "document.html", query_code: str = "withheld") -> Result:
        from dataclasses import replace  # noqa: PLC0415

        from bidoc_contracts import ContractError, Limits, validate_artifact  # noqa: PLC0415
        limits = Limits()
        try:                                       # the target library's own limit wins over the default
            advertised = self.capabilities().get("limits", {}).get("html_bytes")
            if isinstance(advertised, int) and advertised > 0:
                limits = replace(limits, html_bytes=advertised)
        except PublishError:
            pass                                   # the upload below reports an unreachable or rejecting library
        try:
            manifest = validate_artifact(artifact, limits=limits)
        except ContractError as exc:
            if exc.code == "ARTIFACT_TOO_LARGE":
                raise PublishError("ARTIFACT_TOO_LARGE", f"not published: the document is {len(artifact) / 1048576:.1f} MiB "
                                   f"and this library accepts at most {limits.html_bytes / 1048576:.1f} MiB. The generated "
                                   "file is unchanged and usable locally; ask the library administrator to raise the "
                                   "limit.") from None
            raise PublishError("CONTRACT_INVALID", f"this file cannot be published: {exc}") from None
        doc = self.document(manifest["document_id"])
        etag = doc["etag_header"] if doc else None
        key = "bidoc-" + hashlib.sha256(artifact + b"\0" + (etag or "").encode() + query_code.encode()).hexdigest()[:40]
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="query_code"\r\n\r\n{query_code}\r\n'.encode(),
                 f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{_safe(filename)}"\r\n'
                 f"Content-Type: text/html\r\n\r\n".encode() + artifact + b"\r\n",
                 f"--{boundary}--\r\n".encode()]
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Idempotency-Key": key}
        if etag:
            headers["If-Match"] = etag
        status, _, out = self._request("POST", "/imports", body=b"".join(parts), headers=headers, retry=True)
        result = self._request("GET", f"/results/{out['import_id']}", retry=True)[2]
        return Result("duplicate" if out["duplicate"] else "published", result["document_id"], result["revision_id"],
                      out["import_id"], result["title"], result["catalogue_sequence"], doc is None)


def _safe(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in "._- ")[:100] or "document.html"
