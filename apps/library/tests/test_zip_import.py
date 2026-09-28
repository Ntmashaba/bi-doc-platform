"""ZIP profile imports through the API (handoff 5; A10)."""
import hashlib
import io
import json
import unittest
import zipfile

from bidoc_contracts import PLACEHOLDER, embed_manifest, locate_manifest, validate_artifact
from test_api import MUTATE, ApiTest

LOGO = b"\x89PNG\r\n\x1a\nlogo"


def zipped(html: bytes, assets: dict, extra=()) -> bytes:
    loc = locate_manifest(html)
    manifest = json.loads(loc.body)
    manifest["assets"] = [{"path": p, "sha256": hashlib.sha256(d).hexdigest()} for p, d in assets.items()]
    doc = embed_manifest(html[:loc.start] + PLACEHOLDER + html[loc.end:], manifest)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("document.html", doc)
        for p, d in list(assets.items()) + list(extra):
            z.writestr(p, d)
    return buf.getvalue()


class ZipImport(ApiTest):
    def test_zip_is_validated_then_re_rendered_without_its_assets(self):
        data = zipped(self.artifact(), {"assets/logo.png": LOGO})
        pre = self.client.post("/api/v1/imports/preview", files={"file": ("d.zip", data, "application/zip")},
                               headers=MUTATE).json()
        self.assertEqual(pre["input"], "zip")
        self.assertTrue(any("asset file(s)" in w for w in pre["coverage_warnings"]))
        out = self.upload(data)
        self.assertEqual(out.status_code, 201, out.text)
        o = out.json()
        stored = self.client.get(f"/api/v1/documents/{o['document_id']}/revisions/{o['revision_id']}/download").content
        manifest = validate_artifact(stored)                                 # self-contained HTML again
        self.assertNotIn("assets", manifest)
        self.assertNotIn(LOGO, stored)

    def test_unsafe_zip_leaves_nothing_behind(self):                         # A10
        bad = zipped(self.artifact(), {"assets/logo.png": LOGO}, extra=[("../escape.txt", b"x")])
        r = self.upload(bad)
        body = self.assertApiError(r, 422, "CONTRACT_INVALID")
        self.assertEqual(body["details"]["contract_code"], "ZIP_UNSAFE_PATH")
        self.assertEqual(self.client.get("/api/v1/documents").json()["items"], [])
        wrong = zipped(self.artifact(), {"assets/logo.png": LOGO})
        tampered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(wrong)) as src, zipfile.ZipFile(tampered, "w") as dst:
            for info in src.infolist():
                dst.writestr(info.filename, src.read(info) + (b"!" if info.filename.startswith("assets/") else b""))
        body = self.assertApiError(self.upload(tampered.getvalue()), 422, "CONTRACT_INVALID")
        self.assertEqual(body["details"]["contract_code"], "ASSET_HASH_MISMATCH")


from backends import add_variants  # noqa: E402

add_variants(globals(), (ZipImport,))

if __name__ == "__main__":
    unittest.main()
