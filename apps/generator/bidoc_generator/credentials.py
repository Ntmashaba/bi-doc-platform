"""Where the generator keeps a library publishing token (handoff 4, 8).

Windows: Windows Credential Manager (generic credential, current user), through the
documented CredWriteW/CredReadW/CredDeleteW API. Other systems (development and CI):
a file in the generator home readable only by its owner. Tokens are never written to
config.json, generated documents, history or logs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .doctor import home

TARGET_PREFIX = "bidoc:publish:"


def _target(library_url: str) -> str:
    return TARGET_PREFIX + library_url.rstrip("/").lower()


# ---- Windows Credential Manager -----------------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE = 1, 2
    ERROR_NOT_FOUND = 1168

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _advapi.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    _advapi.CredWriteW.restype = wintypes.BOOL
    _advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    _advapi.CredReadW.restype = wintypes.BOOL
    _advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    _advapi.CredDeleteW.restype = wintypes.BOOL
    _advapi.CredFree.argtypes = [ctypes.c_void_p]

    def _save(target: str, secret: str) -> None:
        blob = secret.encode("utf-16-le")
        buf = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        cred = _CREDENTIALW(Type=CRED_TYPE_GENERIC, TargetName=target, CredentialBlobSize=len(blob),
                            CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)),
                            Persist=CRED_PERSIST_LOCAL_MACHINE, UserName="bidoc",
                            Comment="BI Documentation Generator publishing token")
        if not _advapi.CredWriteW(ctypes.byref(cred), 0):
            raise OSError(ctypes.get_last_error(), "could not save to Windows Credential Manager")

    def _load(target: str):
        pcred = ctypes.POINTER(_CREDENTIALW)()
        if not _advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
            if ctypes.get_last_error() == ERROR_NOT_FOUND:
                return None
            raise OSError(ctypes.get_last_error(), "could not read Windows Credential Manager")
        try:
            c = pcred.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            _advapi.CredFree(pcred)

    def _delete(target: str) -> None:
        if not _advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0) and ctypes.get_last_error() != ERROR_NOT_FOUND:
            raise OSError(ctypes.get_last_error(), "could not delete from Windows Credential Manager")

    STORE = "Windows Credential Manager"

else:
    def _file() -> Path:
        return home() / "credentials.json"

    def _read_all() -> dict:
        try:
            return json.loads(_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_all(data: dict) -> None:
        path = _file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)

    def _save(target: str, secret: str) -> None:
        data = _read_all()
        data[target] = secret
        _write_all(data)

    def _load(target: str):
        return _read_all().get(target)

    def _delete(target: str) -> None:
        data = _read_all()
        if data.pop(target, None) is not None:
            _write_all(data)

    STORE = "a file readable only by you (generator home)"


def save_token(library_url: str, token: str) -> None:
    _save(_target(library_url), token)


def load_token(library_url: str):
    return _load(_target(library_url))


def delete_token(library_url: str) -> None:
    _delete(_target(library_url))
