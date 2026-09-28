"""The browser search (static/search.js, compiled from TypeScript) returns exactly what
the Python reference returns, over generated documents and queries; and the shell is
served with its security headers."""
import json
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from bidoc_library.search import search

STATIC = Path(__file__).resolve().parents[1] / "bidoc_library" / "static"
WORDS = ["Sales", "sales", "Revenue", "pipeline", "Copy", "daily", "Déjà", "vu", "FactSales", "dbo", "Orders",
         "trigger", "measure", "Total", "margin", "MAX(id)", "lake", "raw", "2024", "Überblick", "straße"]


def corpus(seed=7, n=40):
    rng = random.Random(seed)
    docs = []
    for i in range(n):
        words = lambda k: " ".join(rng.choice(WORDS) for _ in range(k))  # noqa: E731
        docs.append({
            "document_id": f"{i:08d}-0000-4000-8000-000000000000", "revision_id": f"r{i}",
            "document_type": rng.choice(["power_bi", "adf"]),
            "classification": {"business_area": rng.choice(["", "Finance", "Sales"]),
                               "environment": rng.choice(["Production", "Test"]), "owner": rng.choice(["", "BI"])},
            "title": words(rng.randint(1, 4)), "tags": sorted({rng.choice(WORDS) for _ in range(rng.randint(0, 3))}),
            "sections": [{"id": f"s{i}-{j}", "title": words(rng.randint(1, 3)), "text": words(rng.randint(0, 60))}
                         for j in range(rng.randint(0, 6))]})
    return docs


QUERIES = [(None, {}), ("", {}), ("sales", {}), ("SALES revenue", {}), ("copy daily", {"document_type": "adf"}),
           ("déjà", {}), ("überblick", {}), ("max(id)", {}), ("pipeline", {"environment": "Test"}),
           ("total margin 2024", {"business_area": "Finance"}), ("  lake   raw ", {}), ("nothing-here", {}),
           (None, {"tag": "Sales"}), ("dbo", {"owner": "BI", "document_type": "power_bi"})]


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the browser-search parity test")
class SearchParity(unittest.TestCase):
    def test_same_results_as_python(self):
        docs = corpus()
        expected = [search(docs, q, **f) for q, f in QUERIES]
        script = (f"import {{ search }} from {json.dumps((STATIC / 'search.js').as_uri())};\n"
                  "const input = JSON.parse(await new Promise(r => { let s=''; process.stdin.on('data', d => s += d);"
                  " process.stdin.on('end', () => r(s)); }));\n"
                  "process.stdout.write(JSON.stringify(input.queries.map(([q, f]) => search(input.docs, q, f))));\n")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.mjs"
            path.write_text(script, encoding="utf-8")
            out = subprocess.run(["node", str(path)], input=json.dumps({"docs": docs, "queries": QUERIES}),
                                 capture_output=True, text=True, check=True)
        actual = json.loads(out.stdout)
        for (q, f), e, a in zip(QUERIES, expected, actual):
            self.assertEqual(a, e, f"query {q!r} {f}")
        self.assertTrue(any(expected), "the corpus should produce hits")


class Shell(unittest.TestCase):
    def test_shell_headers_and_static_whitelist(self):
        from starlette.testclient import TestClient

        from bidoc_library.api import create_app
        from bidoc_library.config import Settings
        with tempfile.TemporaryDirectory() as d:
            client = TestClient(create_app(Settings(local_data_dir=Path(d)), session_secret="sekret"),
                                base_url="http://127.0.0.1:8765")
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            csp = page.headers["Content-Security-Policy"]
            for rule in ("script-src 'self'", "frame-ancestors 'none'", "object-src 'none'"):
                self.assertIn(rule, csp)
            self.assertNotIn("unsafe-inline", csp)
            self.assertIn('<meta name="bidoc-session" content="sekret">', page.text)
            self.assertNotIn("<script>", page.text)                        # no inline script at all
            self.assertEqual(client.get("/static/app.js").headers["content-type"].split(";")[0], "text/javascript")
            self.assertEqual(client.get("/static/../api.py").status_code, 404)
            self.assertEqual(client.get("/static/index.html").status_code, 404)
            self.assertEqual(client.get("/", headers={"Host": "evil.example"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
