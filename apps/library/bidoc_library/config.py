"""Typed, validated library configuration from the environment (handoff section 11).

Secrets never come from repository files. Unknown or unsafe combinations fail at
start-up rather than falling back to something more permissive.
"""
from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path

MIB = 1024 * 1024


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    data_backend: str = "local"
    local_data_dir: Path = Path("bidoc-data")
    auth_mode: str = "local"                       # local | gateway (entra arrives in B13)
    bind_host: str = "127.0.0.1"
    port: int = 8765
    gateway_trusted_proxies: tuple = ()            # proxy addresses allowed to assert identity
    gateway_subject_header: str = "X-Forwarded-User"
    gateway_roles_header: str = "X-Forwarded-Roles"
    gateway_default_roles: tuple = ("viewer",)     # roles when the gateway sends none
    max_html_bytes: int = 25 * MIB
    max_manifest_bytes: int = 16 * MIB
    log_level: str = "info"
    extra_allowed_hosts: tuple = field(default_factory=tuple)
    # Local mode inside a container must listen on the container interface. That is only
    # acceptable when the host publishes the port on its own loopback (-p 127.0.0.1:8765:8765);
    # the operator states this explicitly. The Host check and session secret still apply.
    local_container_bind: bool = False

    def validate(self) -> "Settings":
        if self.data_backend != "local":
            raise ConfigError("DATA_BACKEND must be 'local': the Azure storage adapter exists (B10) but the "
                              "library serves from it only after B10b")
        if self.auth_mode == "entra":
            raise ConfigError("AUTH_MODE=entra is not available until B13; use local or gateway")
        if self.auth_mode not in ("local", "gateway"):
            raise ConfigError("AUTH_MODE must be local or gateway")
        if self.auth_mode == "local":
            if not ipaddress.ip_address(self.bind_host).is_loopback and not self.local_container_bind:
                raise ConfigError("AUTH_MODE=local only binds to a loopback address; use gateway mode to share. "
                                  f"In a container published only on the host loopback, set "
                                  f"{CONTAINER_BIND_ENV}={CONTAINER_BIND_ACK}")
        if self.auth_mode == "gateway":
            if not self.gateway_trusted_proxies:
                raise ConfigError("AUTH_MODE=gateway needs GATEWAY_TRUSTED_PROXIES (the ingress addresses)")
            for proxy in self.gateway_trusted_proxies:
                ipaddress.ip_network(proxy, strict=False)
        if not 1 <= self.port <= 65535:
            raise ConfigError("PORT must be 1-65535")
        if self.max_html_bytes < 1 or self.max_manifest_bytes < 1:
            raise ConfigError("upload limits must be positive")
        return self


CONTAINER_BIND_ENV = "LOCAL_CONTAINER_BIND"
CONTAINER_BIND_ACK = "published-on-host-loopback-only"


def _container_ack(value) -> bool:
    if value in (None, ""):
        return False
    if value != CONTAINER_BIND_ACK:
        raise ConfigError(f"{CONTAINER_BIND_ENV} must be exactly {CONTAINER_BIND_ACK!r} when set")
    return True


def _list(value: str) -> tuple:
    return tuple(x.strip() for x in (value or "").split(",") if x.strip())


def from_env(env=None) -> Settings:
    env = os.environ if env is None else env
    try:
        return Settings(
            data_backend=env.get("DATA_BACKEND", "local"),
            local_data_dir=Path(env.get("LOCAL_DATA_DIR", "bidoc-data")),
            auth_mode=env.get("AUTH_MODE", "local"),
            bind_host=env.get("BIND_HOST", "127.0.0.1"),
            port=int(env.get("PORT", "8765")),
            gateway_trusted_proxies=_list(env.get("GATEWAY_TRUSTED_PROXIES", "")),
            gateway_subject_header=env.get("GATEWAY_SUBJECT_HEADER", "X-Forwarded-User"),
            gateway_roles_header=env.get("GATEWAY_ROLES_HEADER", "X-Forwarded-Roles"),
            gateway_default_roles=_list(env.get("GATEWAY_DEFAULT_ROLES", "viewer")),
            max_html_bytes=int(env.get("MAX_HTML_BYTES", str(25 * MIB))),
            max_manifest_bytes=int(env.get("MAX_MANIFEST_BYTES", str(16 * MIB))),
            log_level=env.get("LOG_LEVEL", "info"),
            extra_allowed_hosts=_list(env.get("ALLOWED_HOSTS", "")),
            local_container_bind=_container_ack(env.get(CONTAINER_BIND_ENV)),
        ).validate()
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
