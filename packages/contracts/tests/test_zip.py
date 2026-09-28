"""ZIP profile (handoff 5; A10): one document.html, verified assets, and every unsafe archive rejected."""
import hashlib
import io
import json
import stat
import sys
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bidoc_contracts import (PLACEHOLDER, ContractError, Limits, embed_manifest, locate_manifest,  # noqa: E402
                             validate_artifact, validate_zip)

HTML = (HERE / "fixtures" / "valid-power-bi.html").read_bytes()
LOGO = b"\x89PNG\r\n\x1a\nfake image"


def with_assets(html: bytes, assets: dict) -> bytes:
    loc = locate_manifest(html)
    manifest = json.loads(loc.body)
    manifest["assets"] = [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in assets.items()]
    return embed_manifest(html[:loc.start] + PLACEHOLDER + html[loc.end:], manifest)


def build(entries, *, symlink=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            if name == symlink:
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, data)
            else:
                z.writestr(name, data)
    return buf.getvalue()


def code(data, **kw):
    try:
        validate_zip(data, **kw)
    except ContractError as exc:
        return exc.code
    return None


class ZipProfile(unittest.TestCase):
    def setUp(self):
        self.doc = with_assets(HTML, {"assets/logo.png": LOGO})

    def test_valid_zip_with_verified_asset(self):
        z = validate_zip(build([("document.html", self.doc), ("assets/logo.png", LOGO)]))
        self.assertEqual(z.assets, {"assets/logo.png": LOGO})
        self.assertEqual(z.manifest["assets"][0]["path"], "assets/logo.png")
        self.assertIsNone(code(build([("document.html", HTML)])))           # assets are optional

    def test_assets_are_only_for_the_zip_profile(self):
        with self.assertRaises(ContractError) as ctx:
            validate_artifact(self.doc)
        self.assertEqual(ctx.exception.code, "CONTRACT_VIOLATION")

    def test_unsafe_archives_are_rejected(self):
        ok = [("document.html", self.doc), ("assets/logo.png", LOGO)]
        cases = {
            "ZIP_UNSAFE_PATH": [build(ok + [("../evil.txt", b"x")]), build(ok + [("/etc/passwd", b"x")]),
                                build(ok + [("assets/../../x", b"x")]), build(ok + [("assets\\x.png", b"x")]),
                                build(ok + [("C:/x.png", b"x")]), build(ok + [("assets//x.png", b"x")])],
            "ZIP_DUPLICATE_ENTRY": [build(ok + [("assets/logo.png", LOGO)]), build(ok + [("assets/LOGO.png", LOGO)])],
            "ZIP_SYMLINK": [build(ok + [("assets/link", b"/etc/passwd")], symlink="assets/link")],
            "ZIP_NESTED_ARCHIVE": [build(ok + [("assets/inner.zip", build(ok))]),
                                   build(ok + [("assets/inner.bin", build(ok))])],
            "ZIP_UNEXPECTED_ENTRY": [build(ok + [("other.html", b"x")]), build(ok + [("scripts/", b"")])],
            "ZIP_NO_DOCUMENT": [build([("assets/logo.png", LOGO)])],
            "ZIP_INVALID": [b"PK\x03\x04not really a zip"],
            "ASSET_MISMATCH": [build([("document.html", self.doc)]),
                               build(ok + [("assets/extra.css", b"x")])],
            "ASSET_HASH_MISMATCH": [build([("document.html", self.doc), ("assets/logo.png", LOGO + b"!")])],
        }
        for expected, archives in cases.items():
            for i, data in enumerate(archives):
                with self.subTest(expected=expected, case=i):
                    self.assertEqual(code(data), expected)

    def test_encrypted_entries_are_rejected(self):
        data = bytearray(build([("document.html", self.doc), ("assets/logo.png", LOGO)]))
        # Set the "encrypted" flag bit on every local and central header.
        for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            start = 0
            while (i := data.find(sig, start)) != -1:
                data[i + off] |= 0x01
                start = i + 4
        self.assertEqual(code(bytes(data)), "ZIP_ENCRYPTED")

    def test_limits_are_enforced_while_reading(self):
        big = [("document.html", self.doc), ("assets/logo.png", LOGO)]
        self.assertEqual(code(build(big), limits=Limits(zip_entries=1)), "ZIP_TOO_MANY_ENTRIES")
        self.assertEqual(code(build(big), limits=Limits(zip_bytes=100)), "ARTIFACT_TOO_LARGE")
        bomb = build([("document.html", self.doc), ("assets/logo.png", LOGO)])
        self.assertEqual(code(bomb, limits=Limits(zip_expanded_bytes=len(self.doc) + 5)), "ARTIFACT_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
