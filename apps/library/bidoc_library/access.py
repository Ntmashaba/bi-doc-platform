"""Access modes (handoff sections 8 and 17.1). There is never an unauthenticated fallback.

local    Loopback only. Host must be a loopback name (blocks DNS rebinding); a
         cross-site Origin is refused; mutations need the per-session secret
         (X-Bidoc-Session) and the anti-CSRF header. One local owner holds every role.
gateway  Identity is trusted only from configured proxy addresses; anything else is
         401. Browser mutations need the anti-CSRF header, which a cross-site form
         or simple request cannot send.
"""
from __future__ import annotations

import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import LibraryError

ROLES = {"viewer": {"read"}, "publisher": {"read", "publish"}, "admin": {"read", "publish", "admin"}}
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "bidoc"
SESSION_HEADER = "X-Bidoc-Session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


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
        # gateway
        try:
            ip = ipaddress.ip_address(client_ip or "")
        except ValueError:
            raise unauthorized("request did not come through the configured gateway") from None
        if not any(ip in net for net in self.proxies):
            raise unauthorized("request did not come through the configured gateway")
        subject = (headers.get(self.settings.gateway_subject_header) or "").strip()
        if not subject:
            raise unauthorized("the gateway did not supply an identity")
        roles = frozenset(r.strip().lower() for r in (headers.get(self.settings.gateway_roles_header) or "").split(",")
                          if r.strip()) or frozenset(self.settings.gateway_default_roles)
        if mutating:
            self._csrf(headers)
        return Principal(subject[:200], roles & set(ROLES))

    @staticmethod
    def _csrf(headers):
        if headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise forbidden(f"state-changing requests need the {CSRF_HEADER}: {CSRF_VALUE} header")
