"""A36: the cookie-free desktop publishing sequence through a real TLS ingress.

    python apps/library/tests/check_ingress.py        (needs Docker and openssl)

nginx terminates TLS in front of the library (gateway mode), the way a client ingress
would: every browser route needs sign-in (HTTP basic here) and nginx asserts the
identity; /api/v1/publishing/* bypasses sign-in, and nginx strips any identity headers
there. The generator's own commands then connect, publish, publish a new version against
the ETag, retry, and read results back over https, trusting only the test CA. Escalation
and revocation cases must fail.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402

NGINX = "nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10"
LIB_PORT, TLS_PORT = 18765, 18443
BASE = f"https://localhost:{TLS_PORT}"
CONF = """
events {}
http {
  client_max_body_size 30m;
  server {
    listen 127.0.0.1:%(tls)d ssl;
    server_name localhost;
    ssl_certificate /etc/nginx/tls/cert.pem;
    ssl_certificate_key /etc/nginx/tls/key.pem;
    # Token-only API: no sign-in, and no identity may be asserted by the client.
    location /api/v1/publishing/ {
      proxy_pass http://127.0.0.1:%(lib)d;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-User "";
      proxy_set_header X-Forwarded-Roles "";
      proxy_set_header Cookie "";
    }
    # Everything else: sign-in at the ingress, which asserts who the user is.
    location / {
      auth_basic "library";
      auth_basic_user_file /etc/nginx/tls/htpasswd;
      proxy_pass http://127.0.0.1:%(lib)d;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-Proto https;
      proxy_set_header X-Forwarded-User $remote_user;
      proxy_set_header X-Forwarded-Roles "publisher";
    }
  }
}
"""

results = []


def step(name, ok=True):
    results.append((name, ok))
    print(("  ok " if ok else "  FAIL ") + name, flush=True)
    if not ok:
        raise SystemExit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def wait_port(port, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit(f"port {port} never opened")


def main():
    tmp = Path(tempfile.mkdtemp())
    procs, container = [], "bidoc-ingress-" + str(os.getpid())
    try:
        tls = tmp / "tls"
        tls.mkdir()
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=localhost",
             "-addext", "subjectAltName=DNS:localhost", "-keyout", str(tls / "key.pem"), "-out", str(tls / "cert.pem")])
        hashed = run(["openssl", "passwd", "-apr1", "pw-ops"]).stdout.strip()
        (tls / "htpasswd").write_text(f"ops:{hashed}\neve:{hashed}\n")
        (tls / "nginx.conf").write_text(CONF % {"tls": TLS_PORT, "lib": LIB_PORT})
        for f in tls.iterdir():
            f.chmod(0o644)

        env = dict(os.environ, AUTH_MODE="gateway", GATEWAY_TRUSTED_PROXIES="127.0.0.1/32", BIND_HOST="127.0.0.1",
                   PORT=str(LIB_PORT), LOCAL_DATA_DIR=str(tmp / "data"), LOG_LEVEL="warning")
        procs.append(subprocess.Popen([sys.executable, "-m", "bidoc_library"], env=env))
        wait_port(LIB_PORT)
        run(["docker", "run", "-d", "--rm", "--name", container, "--network", "host",
             "-v", f"{tls}:/etc/nginx/tls:ro", "-v", f"{tls / 'nginx.conf'}:/etc/nginx/nginx.conf:ro", NGINX])
        wait_port(TLS_PORT)

        ctx = ssl.create_default_context(cafile=str(tls / "cert.pem"))
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                             urllib.request.ProxyHandler({}))

        def call(method, path, *, user=None, token=None, body=None, headers=None):
            h = {"Accept": "application/json", **(headers or {})}
            if user:
                import base64
                h["Authorization"] = "Basic " + base64.b64encode(f"{user}:pw-ops".encode()).decode()
            if token:
                h["Authorization"] = f"Bearer {token}"
            data = None
            if body is not None:
                data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
            req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
            try:
                with opener.open(req, timeout=30) as r:
                    return r.status, json.loads(r.read() or b"null")
            except urllib.error.HTTPError as e:
                return e.code, None

        # A publisher signs in through the ingress and issues a token (browser identity, CSRF header).
        status, issued = call("POST", "/api/v1/publish-tokens", user="ops", body={"label": "ci", "expires_in_days": 1},
                              headers={"X-Requested-With": "bidoc"})
        step("publisher issues a token through the ingress", status == 201)
        token = issued["token"]
        status, _ = call("POST", "/api/v1/publish-tokens", body={"label": "x", "expires_in_days": 1},
                         headers={"X-Requested-With": "bidoc"})
        step("browser routes need sign-in at the ingress", status == 401)

        # The generator: connect and publish over https with only the test CA trusted.
        gen_env = dict(os.environ, BIDOC_HOME=str(tmp / "gen"), SSL_CERT_FILE=str(tls / "cert.pem"),
                       NO_PROXY="localhost,127.0.0.1", no_proxy="localhost,127.0.0.1")
        bidoc = [sys.executable, "-c", "import sys; from bidoc_generator.cli import main; sys.exit(main())"]
        out = run(bidoc + ["connect", BASE, "--token-stdin", "--json"], input=token + "\n", env=gen_env)
        step("bidoc connect over https", json.loads(out.stdout)["subject"] == "ops")
        factory = adf_factory(tmp / "factory")
        first = generate(GenerateRequest(engine="adf", source_path=str(factory), source_kind="adf_git",
                                         output_dir=str(tmp / "o1"), environment="Production"))
        out = json.loads(run(bidoc + ["publish", first.artifact_path, "--json"], env=gen_env).stdout)[0]
        step("publish a new document (cookie-free, readback)", out["status"] == "published" and out["new_document"])
        again = json.loads(run(bidoc + ["publish", first.artifact_path, "--json"], env=gen_env).stdout)[0]
        step("retry returns the original outcome", again["status"] == "duplicate"
             and again["revision_id"] == out["revision_id"])
        time.sleep(1.1)
        second = generate(GenerateRequest(engine="adf", source_path=str(factory), source_kind="adf_git",
                                          output_dir=str(tmp / "o2"), environment="Production"))
        v2 = json.loads(run(bidoc + ["publish", second.artifact_path, "--json"], env=gen_env).stdout)[0]
        step("new version against the current ETag", v2["status"] == "published" and not v2["new_document"]
             and v2["document_id"] == out["document_id"] and v2["revision_id"] != out["revision_id"])
        status, revs = call("GET", f"/api/v1/documents/{out['document_id']}/revisions", user="ops")
        step("readers see both versions through the ingress", status == 200 and len(revs["items"]) == 2)

        # Escalation: the token reaches nothing outside /api/v1/publishing.
        for method, path, body in (("GET", "/api/v1/documents", None),
                                   ("POST", "/api/v1/publish-tokens", {"label": "x", "expires_in_days": 1}),
                                   ("POST", f"/api/v1/documents/{out['document_id']}/archive", None)):
            status, _ = call(method, path, token=token, body=body, headers={"X-Requested-With": "bidoc"})
            step(f"token refused on {method} {path.split('/api/v1')[1]}", status == 401)
        status, _ = call("GET", "/api/v1/publishing/capabilities",
                         headers={"X-Forwarded-User": "ops", "X-Forwarded-Roles": "admin"})
        step("forged identity headers do not open the publishing API", status == 401)
        status, _ = call("GET", "/api/v1/publishing/capabilities", token=token + "x")
        step("a tampered token is refused", status == 401)

        # Revocation takes effect on the next request.
        status, listed = call("GET", "/api/v1/publish-tokens", user="ops")
        token_id = listed["items"][0]["token_id"]
        status, _ = call("DELETE", f"/api/v1/publish-tokens/{token_id}", user="ops", headers={"X-Requested-With": "bidoc"})
        step("publisher revokes the token", status == 204)
        failed = subprocess.run(bidoc + ["publish", second.artifact_path, "--json"], env=gen_env, capture_output=True,
                                text=True)
        step("a revoked token cannot publish", failed.returncode != 0
             and json.loads(failed.stdout)[0]["error"] == "CREDENTIAL_REJECTED")
        return 0
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        for p in procs:
            p.terminate()
            p.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
