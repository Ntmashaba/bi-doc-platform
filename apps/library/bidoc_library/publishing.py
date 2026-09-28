"""Direct publishing credentials and installer releases (handoff 7, 8, 17.5; B11).

Publishing tokens
- `bidocpt_<id>_<secret>`: 256 random bits, shown once. Only a SHA-256 of the whole
  token is stored (the secret is high-entropy, so a fast hash is appropriate).
- Scope `publish` only. Tokens authenticate the /api/v1/publishing/* namespace and
  nothing else: they cannot issue tokens, archive, edit metadata or manage relationships.
- 1–30 days. Revocation takes effect on the next request. An administrator can revoke
  every token of a subject, which is the central policy for publisher-role removal when
  the library cannot query roles itself (gateway mode; see docs/publishing.md).
- Failures are one generic 401, so a caller cannot tell unknown, wrong, expired or revoked
  tokens apart.

Releases
- An administrator uploads an installer with its version, SHA-256 (checked against the
  bytes), notes and prerequisites. It is served only after a separate approval, and the
  bytes are re-checked on every download.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from .access import Principal, forbidden, unauthorized
from .errors import LibraryError, conflict, not_found

TOKEN_RE = re.compile(r"^bidocpt_([0-9a-f]{16})_([A-Za-z0-9_-]{43})$")
VERSION_RE = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,5})$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.exe$")
PLATFORMS = ("windows-x64",)
MAX_INSTALLER_BYTES = 500 * 1024 * 1024
LAST_USED_RESOLUTION = timedelta(hours=1)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _public(t: dict) -> dict:
    now = datetime.now(timezone.utc)
    state = "revoked" if t.get("revoked_at") else "expired" if _parse(t["expires_at"]) <= now else "active"
    return {k: t.get(k) for k in ("token_id", "subject", "label", "scopes", "created_at", "expires_at",
                                  "revoked_at", "revoked_by", "last_used_at")} | {"state": state}


class Publishing:
    def __init__(self, store, clock=lambda: datetime.now(timezone.utc)):
        self.store, self.clock = store, clock

    # ---- tokens ----------------------------------------------------------------

    def issue(self, subject: str, label: str, expires_in_days: int) -> dict:
        label = (label or "").strip()
        if not 1 <= len(label) <= 100:
            raise LibraryError("INVALID_REQUEST", "a label of 1-100 characters is required")
        if not isinstance(expires_in_days, int) or not 1 <= expires_in_days <= 30:
            raise LibraryError("INVALID_REQUEST", "expires_in_days must be 1 to 30")
        token_id, secret = secrets.token_hex(8), secrets.token_urlsafe(32)
        token = f"bidocpt_{token_id}_{secret}"
        now = self.clock()
        record = {"token_id": token_id, "subject": subject, "label": label, "token_hash": _hash(token),
                  "scopes": ["publish"], "created_at": _iso(now),
                  "expires_at": _iso(now + timedelta(days=expires_in_days)),
                  "revoked_at": None, "revoked_by": None, "last_used_at": None}
        self.store.token_put(record)
        self.store.token_audit_add(token_id, "issue", subject)
        return {**_public(record), "token": token}

    def list(self, subject: str, *, everyone=False) -> list[dict]:
        return [_public(t) for t in self.store.token_list() if everyone or t["subject"] == subject]

    def revoke(self, token_id: str, principal: Principal) -> None:
        t = self.store.token_get(token_id)
        is_admin = principal.can("admin")
        if t is None or (t["subject"] != principal.subject and not is_admin):
            raise not_found("publishing token")
        if not t.get("revoked_at"):
            t["revoked_at"], t["revoked_by"] = _iso(self.clock()), principal.subject
            self.store.token_update(t)
        self.store.token_audit_add(token_id, "revoke" if t["subject"] == principal.subject else "admin-revoke",
                                   principal.subject)

    def revoke_subject(self, subject: str, admin: Principal) -> int:
        """Revoke every active token of a subject (e.g. when their publisher role is removed)."""
        n = 0
        for t in self.store.token_list():
            if t["subject"] == subject and not t.get("revoked_at"):
                t["revoked_at"], t["revoked_by"] = _iso(self.clock()), admin.subject
                self.store.token_update(t)
                self.store.token_audit_add(t["token_id"], "admin-revoke-subject", admin.subject)
                n += 1
        return n

    def authenticate(self, authorization: str | None) -> Principal:
        """The token's subject as a publisher, or one generic 401."""
        denied = unauthorized("a valid publishing token is required")
        scheme, _, token = (authorization or "").partition(" ")
        m = TOKEN_RE.match(token.strip()) if scheme.lower() == "bearer" else None
        if not m:
            raise denied
        t = self.store.token_get(m.group(1))
        now = self.clock()
        if t is None or not hmac.compare_digest(t["token_hash"], _hash(token.strip())) \
                or t.get("revoked_at") or _parse(t["expires_at"]) <= now or "publish" not in t["scopes"]:
            raise denied
        if not t.get("last_used_at") or now - _parse(t["last_used_at"]) >= LAST_USED_RESOLUTION:
            t["last_used_at"] = _iso(now)
            self.store.token_update(t)
        return Principal(t["subject"], frozenset({"publisher"}))

    # ---- releases ----------------------------------------------------------------

    @staticmethod
    def _version_key(version: str):
        return tuple(int(x) for x in version.split("."))

    def add_release(self, *, version, platform, filename, data: bytes, sha256, release_notes, prerequisites,
                    label, signed: bool, subject) -> dict:
        if not VERSION_RE.match(version or ""):
            raise LibraryError("INVALID_REQUEST", "version must look like 1.2.3")
        if platform not in PLATFORMS:
            raise LibraryError("INVALID_REQUEST", f"platform must be one of {', '.join(PLATFORMS)}")
        if not FILENAME_RE.match(filename or ""):
            raise LibraryError("INVALID_REQUEST", "the installer must be a .exe with a plain file name")
        if not data or len(data) > MAX_INSTALLER_BYTES:
            raise LibraryError("PAYLOAD_TOO_LARGE", "installers are limited to 500 MB", 413)
        actual = hashlib.sha256(data).hexdigest()
        if (sha256 or "").lower() != actual:
            raise LibraryError("CHECKSUM_MISMATCH", "the SHA-256 does not match the uploaded installer", 422,
                               {"actual": actual})
        if not isinstance(prerequisites, list) or not all(isinstance(p, str) and len(p) <= 200 for p in prerequisites):
            raise LibraryError("INVALID_REQUEST", "prerequisites must be a list of short texts")
        record = {"version": version, "platform": platform, "filename": filename, "sha256": actual,
                  "size_bytes": len(data), "release_notes": (release_notes or "")[:20000],
                  "prerequisites": prerequisites[:20], "signed": bool(signed), "label": (label or "")[:100],
                  "created_at": _iso(self.clock()), "created_by": subject, "approved_at": None, "approved_by": None}
        if not self.store.release_put(record, data):
            raise conflict("RELEASE_EXISTS", "this version was already uploaded; releases are immutable")
        return record

    def approve(self, version: str, subject: str) -> dict:
        r = self.store.release_get(version)
        if r is None:
            raise not_found("release")
        if not r.get("approved_at"):
            r["approved_at"], r["approved_by"] = _iso(self.clock()), subject
            self.store.release_update(r)
        return r

    def releases(self, *, include_unapproved=False) -> list[dict]:
        items = [r for r in self.store.release_list() if include_unapproved or r.get("approved_at")]
        return sorted(items, key=lambda r: self._version_key(r["version"]), reverse=True)

    def latest(self, platform: str) -> dict:
        approved = [r for r in self.releases() if r["platform"] == platform]
        if not approved:
            raise LibraryError("NO_APPROVED_RELEASE", "no approved installer release is available yet", 404)
        return approved[0]

    def download(self, version: str) -> tuple[dict, bytes]:
        r = self.store.release_get(version) if VERSION_RE.match(version or "") else None
        if r is None or not r.get("approved_at"):
            raise LibraryError("NO_APPROVED_RELEASE", "that version is not an approved release", 404)
        data = self.store.release_read(r)
        if hashlib.sha256(data).hexdigest() != r["sha256"]:
            raise LibraryError("ARTIFACT_CORRUPT", "the installer failed its integrity check", 500)
        return r, data


def require_transport(request, settings, trusted) -> None:
    """Credentials only over HTTPS, except on loopback (handoff 17.5)."""
    import ipaddress  # noqa: PLC0415
    client = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        ip = None
    if request.url.scheme == "https" or (ip is not None and ip.is_loopback and settings.auth_mode == "local"):
        return
    if ip is not None and any(ip in net for net in trusted) and \
            request.headers.get("x-forwarded-proto", "").lower() == "https":
        return
    raise forbidden("publishing requires HTTPS")
