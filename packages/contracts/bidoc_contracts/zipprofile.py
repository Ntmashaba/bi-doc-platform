"""ZIP profile of envelope v1 (handoff section 5, required in R2).

One root `document.html` (holding the manifest) plus optional files under `assets/`.
The manifest's `assets` array lists every asset with its SHA-256; each must verify, and
nothing unlisted may be present. Rejected without extracting anything to disk:

- paths that are absolute, contain backslashes, drive letters, `.` or `..` segments, or
  empty segments;
- duplicate or case-colliding entries;
- symlinks, encrypted entries and nested archives;
- anything outside `document.html` and `assets/`;
- more entries, compressed bytes or expanded bytes than the configured limits. Sizes are
  enforced while reading, not trusted from the archive's own headers.
"""
from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass

from .validate import ContractError, Limits, validate_artifact

ZIP_MAGIC = b"PK\x03\x04"
_NESTED = re.compile(r"\.(zip|jar|war|7z|rar|gz|tgz|bz2|xz|tar|cab|zst)$", re.I)
_ARCHIVE_MAGIC = (ZIP_MAGIC, b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"BZh", b"\xfd7zXZ")


@dataclass
class ZipArtifact:
    manifest: dict
    document: bytes                     # document.html bytes
    assets: dict                        # path -> bytes


def is_zip(data: bytes) -> bool:
    return data[:4] == ZIP_MAGIC


def _bad(code, message):
    return ContractError(code, message)


def _check_path(name: str) -> str:
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\0" in name:
        raise _bad("ZIP_UNSAFE_PATH", f"entry {name!r} is not a relative POSIX path")
    parts = name.rstrip("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise _bad("ZIP_UNSAFE_PATH", f"entry {name!r} contains an empty, '.' or '..' segment")
    return name


def validate_zip(data: bytes, *, limits: Limits = Limits(), view_ids=None) -> ZipArtifact:
    if len(data) > limits.zip_bytes:
        raise _bad("ARTIFACT_TOO_LARGE", f"The ZIP exceeds the {limits.zip_bytes}-byte limit.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise _bad("ZIP_INVALID", f"The file is not a readable ZIP archive: {exc}") from None
    if len(infos) > limits.zip_entries:
        raise _bad("ZIP_TOO_MANY_ENTRIES", f"The ZIP has more than {limits.zip_entries} entries.")
    seen, folded, files = set(), set(), {}
    for info in infos:
        name = _check_path(info.filename)
        if name in seen:
            raise _bad("ZIP_DUPLICATE_ENTRY", f"entry {name!r} appears more than once")
        if name.casefold() in folded:
            raise _bad("ZIP_DUPLICATE_ENTRY", f"entry {name!r} collides with another entry by letter case")
        seen.add(name)
        folded.add(name.casefold())
        if info.flag_bits & 0x1:
            raise _bad("ZIP_ENCRYPTED", f"entry {name!r} is encrypted")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise _bad("ZIP_SYMLINK", f"entry {name!r} is a symbolic link")
        if info.is_dir():
            if name.rstrip("/") != "assets" and not name.startswith("assets/"):
                raise _bad("ZIP_UNEXPECTED_ENTRY", f"folder {name!r} is not allowed; use assets/")
            continue
        if name != "document.html" and not name.startswith("assets/"):
            raise _bad("ZIP_UNEXPECTED_ENTRY", f"entry {name!r} is not allowed: only document.html and assets/")
        if _NESTED.search(name):
            raise _bad("ZIP_NESTED_ARCHIVE", f"entry {name!r} is an archive; nested archives are not allowed")
        files[name] = info
    if "document.html" not in files:
        raise _bad("ZIP_NO_DOCUMENT", "The ZIP has no root document.html.")

    budget = limits.zip_expanded_bytes
    contents = {}
    for name, info in files.items():
        cap = limits.html_bytes if name == "document.html" else budget
        with archive.open(info) as fh:                         # sizes enforced while reading
            chunk = fh.read(min(cap, budget) + 1)
        if len(chunk) > cap:
            raise _bad("ARTIFACT_TOO_LARGE", f"{name} exceeds the {cap}-byte limit")
        budget -= len(chunk)
        if budget < 0:
            raise _bad("ARTIFACT_TOO_LARGE", f"The ZIP expands beyond {limits.zip_expanded_bytes} bytes.")
        if name != "document.html" and chunk.startswith(_ARCHIVE_MAGIC):
            raise _bad("ZIP_NESTED_ARCHIVE", f"entry {name!r} is an archive; nested archives are not allowed")
        contents[name] = chunk

    manifest = validate_artifact(contents["document.html"], limits=limits, view_ids=view_ids, allow_assets=True)
    listed = {a["path"]: a["sha256"] for a in manifest.get("assets", [])}
    if len(listed) != len(manifest.get("assets", [])):
        raise _bad("ASSET_MISMATCH", "The manifest lists an asset path twice.")
    present = {n for n in contents if n != "document.html"}
    if set(listed) != present:
        raise ContractError("ASSET_MISMATCH", "The manifest's assets and the ZIP's files differ.",
                            [{"path": "/assets", "message": f"listed but missing: {p}"} for p in sorted(set(listed) - present)]
                            + [{"path": "/assets", "message": f"present but not listed: {p}"} for p in sorted(present - set(listed))])
    for path, digest in listed.items():
        if hashlib.sha256(contents[path]).hexdigest() != digest:
            raise ContractError("ASSET_HASH_MISMATCH", f"{path} does not match its SHA-256.")
    return ZipArtifact(manifest, contents["document.html"], {p: contents[p] for p in present})
