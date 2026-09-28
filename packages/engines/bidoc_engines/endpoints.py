"""Endpoint fields and `endpoint-norm/1` (docs/contracts/identity-and-bindings-v1.md).

`endpoint` keeps values as the engine found them (split into fields, nothing folded).
`normalized_endpoint` is a comparison form: DNS hosts and Azure storage account and
container names lower-cased, SQL Server's default port made explicit; SQL names and
paths keep their case and encoding.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

NORMALIZATION_VERSION = "endpoint-norm/1"
FIELDS = ("system", "server", "instance", "port", "database", "schema", "object",
          "storage_account", "container", "path", "url")
SQL_SERVER_SYSTEMS = {"SQL Server", "Azure SQL Database", "Azure SQL Managed Instance", "Azure Synapse SQL",
                      "Azure Synapse / SQL"}
_STORAGE_HOST = re.compile(r"^([a-z0-9]+)\.(?:blob|dfs|file|queue|table)\.core\.windows\.net$", re.I)


def empty() -> dict:
    return dict.fromkeys(FIELDS)


def split_server(server):
    """'tcp:host\\inst,1444' -> ('host', 'inst', 1444); values as written."""
    if not server:
        return None, None, None
    s = re.sub(r"(?i)^tcp:", "", str(server).strip())
    port = None
    m = re.search(r"[,:](\d{1,5})$", s)
    if m and "://" not in s:
        port, s = int(m.group(1)), s[:m.start()]
    host, _, instance = s.partition("\\")
    return host or None, instance or None, port


def endpoint(**values) -> dict:
    ep = empty()
    for k, v in values.items():
        if k not in ep:
            raise KeyError(k)
        ep[k] = v if v not in ("", []) else None
    if ep["port"] is not None and not (isinstance(ep["port"], int) and 1 <= ep["port"] <= 65535):
        ep["port"] = None
    return ep


def storage_account_of(url_or_host):
    if not url_or_host:
        return None
    host = urlparse(url_or_host if "://" in url_or_host else "https://" + url_or_host).hostname or ""
    m = _STORAGE_HOST.match(host)
    return m.group(1) if m else None


def normalize(ep: dict) -> dict:
    n = dict(ep)
    if n.get("server") and not re.match(r"^[a-z][a-z0-9+.-]*://", n["server"], re.I):
        n["server"] = n["server"].lower().rstrip(".")
    if n.get("instance"):
        n["instance"] = n["instance"].upper()           # SQL Server instance names are case-insensitive
    if n.get("system") in SQL_SERVER_SYSTEMS and n.get("server") and not n.get("instance") and n.get("port") is None:
        n["port"] = 1433
    for k in ("storage_account", "container"):
        if n.get(k):
            n[k] = n[k].lower()
    if n.get("url"):
        u = urlparse(n["url"])
        n["url"] = u._replace(scheme=u.scheme.lower(), netloc=u.netloc.lower()).geturl()
    return n
