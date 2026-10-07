"""Large inputs for trying size limits by hand: fetch or build them into samples/downloads/large/.

    python scripts/large_samples.py                 list them, with what each is expected to do
    python scripts/large_samples.py contoso-10m     fetch or build the named ones
    python scripts/large_samples.py --all

They are described in samples/large-manifest.json and are not part of the acceptance corpus
(samples/manifest.json): continuous integration does not download them, and nothing is committed.

* A download is streamed to disk, refused above its recorded size and checked against its SHA-256. A file wanted
  from inside a .7z archive is extracted alone, into a scratch folder, and checked against its own SHA-256 before it
  is moved into place, so a member with a path that leads elsewhere cannot write there.
  Unpacking needs py7zr, which the platform itself does not: python -m pip install py7zr
* A synthetic model is built here from two numbers in the manifest and checked the same way, so everyone works
  with the same bytes.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "samples" / "large-manifest.json"
CACHE = ROOT / "samples" / "downloads" / "large"
CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verified(path: Path, size: int, sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == size and sha256_of(path) == sha256


def download(item: dict, target: Path) -> None:
    """Stream item['url'] to `target`; never keep more than the recorded size, never keep a wrong file."""
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(item["url"], headers={"User-Agent": "bi-doc-platform-large-samples"})
    digest, written = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as out:
            for block in iter(lambda: response.read(CHUNK), b""):
                written += len(block)
                if written > item["bytes"]:
                    raise ValueError(f"{item['file']} is larger than the {item['bytes']} bytes recorded for it")
                digest.update(block)
                out.write(block)
                if written % (50 * CHUNK) < len(block):
                    print(f"  {written / CHUNK:6.0f} of {item['bytes'] / CHUNK:.0f} MiB", flush=True)
        if written != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise ValueError(f"Checksum mismatch: {item['file']} (the upstream file may have been replaced)")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


def extract_member(archive: Path, member: str, target: Path, size: int, sha256: str) -> None:
    """Extract exactly `member` from a .7z archive and keep it only if it is the recorded file."""
    try:
        import py7zr
    except ImportError:
        raise SystemExit(f"Cannot unpack {archive.name}: install py7zr (python -m pip install py7zr), then run "
                         "this again. The download is kept.") from None
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".extract-") as scratch:
        with py7zr.SevenZipFile(archive) as opened:
            if member not in opened.getnames():
                raise ValueError(f"{archive.name} has no member named {member!r}")
            opened.extract(path=scratch, targets=[member])
        found = Path(scratch) / Path(member).name
        if found.is_symlink() or not verified(found, size, sha256):
            raise ValueError(f"Checksum mismatch: {member} inside {archive.name}")
        os.replace(found, target)


def synthetic_model(measures: int, terms: int) -> bytes:
    """One table and `measures` measures, each a sum of `terms` constants: a large document from no real data."""
    tail = " + ".join(str(n) for n in range(terms))
    doc = {"compatibilityLevel": 1500, "model": {"tables": [{
        "name": "T",
        "columns": [{"name": "A", "dataType": "int64", "sourceColumn": "A"}],
        "measures": [{"name": f"M{i}", "expression": f"SUM(T[A]) + 0 * {tail}"} for i in range(measures)],
        "partitions": [{"name": "T", "source": {"type": "m", "expression": 'let S = Sql.Database("srv", "db") in S'}}],
    }]}}
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def prepare(item: dict) -> Path:
    """The ready input for one manifest entry, fetched or built if it is not already there and correct."""
    target = CACHE / item["input"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if item["kind"] == "synthetic":
        if not verified(target, item["bytes"], item["sha256"]):
            raw = synthetic_model(item["measures"], item["terms"])
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError(f"{item['id']}: the generated model does not match the manifest "
                                 f"({len(raw)} bytes, sha256 {hashlib.sha256(raw).hexdigest()})")
            target.write_bytes(raw)
        return target
    if verified(target, item["member_bytes"], item["member_sha256"]):
        return target
    archive = CACHE / item["file"]
    if not verified(archive, item["bytes"], item["sha256"]):
        print(f"  downloading {item['file']} ({item['bytes'] / CHUNK:.0f} MiB)", flush=True)
        download(item, archive)
    print(f"  unpacking {item['member']} ({item['member_bytes'] / CHUNK:.0f} MiB)", flush=True)
    extract_member(archive, item["member"], target, item["member_bytes"], item["member_sha256"])
    archive.unlink()                      # the archive is only a container; the checked input is what is kept
    return target


def main(argv=None) -> int:
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description="Fetch or build the large samples in samples/large-manifest.json.")
    parser.add_argument("ids", nargs="*", help="manifest ids; none lists what is available")
    parser.add_argument("--all", action="store_true", help="every sample in the manifest")
    args = parser.parse_args(argv)
    known = {item["id"]: item for item in items}
    unknown = [name for name in args.ids if name not in known]
    if unknown:
        parser.error(f"unknown sample {', '.join(unknown)}; choose from {', '.join(known)}")
    chosen = items if args.all else [known[name] for name in args.ids]
    if not chosen:
        for item in items:
            size = item.get("member_bytes") or item["bytes"]
            print(f"{item['id']:16} {size / CHUNK:6.0f} MiB  {item['kind']:9}  {item['expect']}")
        print("\nFetch or build with: python scripts/large_samples.py ID [ID ...]   (or --all)")
        return 0
    for item in chosen:
        print(item["id"], flush=True)
        path = prepare(item)
        shown = path.relative_to(ROOT)
        print(f"  ready: {shown}\n  expect: {item['expect']}\n"
              f"  try: bidoc generate {item['generate']} --source \"{shown}\" --output-dir samples/output/large")
    return 0


if __name__ == "__main__":
    sys.exit(main())
