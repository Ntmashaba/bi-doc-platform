"""Access modes (handoff sections 8 and 17.1). There is never an unauthenticated fallback.

local    Loopback only. Host must be a loopback name (blocks DNS rebinding); a
         cross-site Origin is refused; mutations need the per-session secret
         (X-Bidoc-Session) and the anti-CSRF header. One local owner holds every role.
gateway  Identity is trusted only from configured proxy addresses; anything else is
         401. Browser mutations need the anti-CSRF header, which a cross-site form
         or simple request cannot send.
entra    Azure Container Apps / App Service built-in authentication (Easy Auth) with
         Microsoft Entra ID. The platform signs the user in and passes the validated
         claims as X-MS-CLIENT-PRINCIPAL; it is trusted only from the configured proxy
         addresses and only for the configured tenant. App roles map to viewer,
         publisher and admin (ENTRA_ROLE_MAP). No principal means 401, never anonymous.
"""
from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import LibraryError

ROLES = {"viewer": {"read"}, "publisher": {"read", "publish"}, "admin": {"read", "publish", "admin"}}
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "bidoc"
SESSION_HEADER = "X-Bidoc-Session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ENTRA_PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"
TID_CLAIM = "http://schemas.microsoft.com/identity/claims/tenantid"
OID_CLAIM = "http://schemas.microsoft.com/identity/claims/objectidentifier"


@dataclass(frozen=True)
class Principal:
    subject: str
    roles: frozenset

    def can(self, permission: str) -> bool:
        return any(permission in ROLES.get(r, ()) for r in self.roles)


def unauthorized(message="authentication required") -> LibraryError:
    return LibraryError("UNAUTHENTICATED", message, 401)


def forbidden(message="insufficient role") -> LibraryError:
    return LibraryError("FORBIDDEN", message, 403)


def load_session_secret(data_dir: Path) -> str:
    """Per-installation secret for local mutations, readable only by the owner."""
    path = Path(data_dir) / "session-secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(32)
    fd = path.open("x", encoding="utf-8")
    try:
        fd.write(value)
    finally:
        fd.close()
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


class AccessPolicy:
    def __init__(self, settings, session_secret: str | None = None):
        self.settings = settings
        self.mode = settings.auth_mode
        self.session_secret = session_secret
        self.proxies = [ipaddress.ip_network(p, strict=False) for p in settings.gateway_trusted_proxies]
        self.role_map = dict(settings.entra_role_map)
        port = settings.port
        self.local_hosts = {f"{h}:{port}" for h in ("127.0.0.1", "localhost", "[::1]")} | \
            {"127.0.0.1", "localhost", "[::1]"} | set(settings.extra_allowed_hosts)

    def authenticate(self, *, method: str, host: str | None, origin: str | None, client_ip: str | None,
                     headers) -> Principal:
        mutating = method.upper() not in SAFE_METHODS
        if self.mode == "local":
            if (host or "").lower() not in self.local_hosts:
                raise forbidden("requests must address the loopback host")
            if origin and origin.lower() not in {f"http://{h}" for h in self.local_hosts}:
                raise forbidden("cross-origin requests are not allowed")
            if mutating:
                supplied = headers.get(SESSION_HEADER, "")
                if not self.session_secret or not hmac.compare_digest(supplied, self.session_secret):
                    raise unauthorized("local changes need the session secret (X-Bidoc-Session)")
                self._csrf(headers)
            return Principal("local-owner", frozenset({"viewer", "publisher", "admin"}))
        try:
            ip = ipaddress.ip_address(client_ip or "")
        except ValueError:
            raise unauthorized("request did not come through the configured gateway") from None
        if not any(ip in net for net in self.proxies):
            raise unauthorized("request did not come through the configured gateway")
        if self.mode == "entra":
            principal = self._entra(headers)
            if mutating:
                self._csrf(headers)
            return principal
        subject = (headers.get(self.settings.gateway_subject_header) or "").strip()
        if not subject:
            raise unauthorized("the gateway did not supply an identity")
        roles = frozenset(r.strip().lower() for r in (headers.get(self.settings.gateway_roles_header) or "").split(",")
                          if r.strip()) or frozenset(self.settings.gateway_default_roles)
        if mutating:
            self._csrf(headers)
        return Principal(subject[:200], roles & set(ROLES))

    def _entra(self, headers) -> Principal:
        raw = headers.get(ENTRA_PRINCIPAL_HEADER) or ""
        if not raw:
            raise unauthorized("sign-in required (no authenticated principal from the platform)")
        try:
            data = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4), validate=False))
            claims = [(str(c["typ"]), str(c["val"])) for c in data.get("claims", [])]
        except (ValueError, TypeError, KeyError, AttributeError):
            raise unauthorized("the platform principal could not be read") from None
        if (data.get("auth_typ") or "").lower() not in ("aad", "azureactivedirectory"):
            raise unauthorized("only Microsoft Entra ID sign-in is accepted")
        values = {}
        for typ, val in claims:
            values.setdefault(typ, []).append(val)
        first = lambda *names: next((values[n][0] for n in names if values.get(n)), "")  # noqa: E731
        if first("tid", TID_CLAIM).lower() != self.settings.entra_tenant_id.lower():
            raise unauthorized("this sign-in belongs to a different tenant")
        subject = first("oid", OID_CLAIM)
        if not subject:
            raise unauthorized("the sign-in has no object ID")
        role_typ = data.get("role_typ") or "roles"
        granted = {self.role_map[r] for r in values.get(role_typ, []) + values.get("roles", []) if r in self.role_map}
        roles = frozenset(granted or self.settings.entra_default_roles)
        return Principal(subject[:200], roles & set(ROLES))

    @staticmethod
    def _csrf(headers):
        if headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise forbidden(f"state-changing requests need the {CSRF_HEADER}: {CSRF_VALUE} header")
