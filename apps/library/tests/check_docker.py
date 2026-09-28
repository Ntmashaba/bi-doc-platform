"""A03 container check: the image runs as non-root, refuses an unacknowledged local bind,
keeps the Host check, and keeps published documents across restart and re-creation.

    python apps/library/tests/check_docker.py IMAGE        (needs Docker and the platform packages)
"""
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "engines" / "tests"))
from fixtures import adf_factory  # noqa: E402

from bidoc_engines.generate import GenerateRequest, generate  # noqa: E402

ACK = ["-e", "LOCAL_CONTAINER_BIND=published-on-host-loopback-only"]


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def step(name):
    print(f"  ok {name}", flush=True)


def request(port, path, *, method="GET", body=None, headers=None, host=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method=method,
                                 headers={"Host": host or f"127.0.0.1:{port}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def wait_ready(port, name):
    for _ in range(120):
        try:
            if request(port, "/api/v1/health/ready")[0] == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    print(docker("logs", name, check=False).stdout + docker("logs", name, check=False).stderr)
    raise SystemExit("library did not become ready")


def main(image):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    tag = uuid.uuid4().hex[:8]
    name, volume = f"bidoc-check-{tag}", f"bidoc-check-{tag}"
    try:
        refused = docker("run", "--rm", image, check=False)
        assert refused.returncode == 2 and "LOCAL_CONTAINER_BIND" in refused.stderr, refused
        step("local mode refuses the container interface without the acknowledgement")

        def start():
            docker("run", "-d", "--name", name, "-v", f"{volume}:/data", "-p", f"127.0.0.1:{port}:8765",
                   "-e", f"PORT=8765", "-e", f"ALLOWED_HOSTS=127.0.0.1:{port}", *ACK, image)
            wait_ready(port, name)
        start()
        assert docker("exec", name, "id", "-u").stdout.strip() == "10001"
        step("runs as a non-root user")

        status, _, _ = request(port, "/api/v1/documents", host="attacker.example")
        assert status == 403, status
        step("Host check still applies inside the container")

        secret = docker("exec", name, "cat", "/data/session-secret").stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            r = generate(GenerateRequest(engine="adf", source_path=str(adf_factory(Path(tmp) / "f")),
                                         source_kind="adf_git", output_dir=str(Path(tmp) / "out"),
                                         title="Container factory", environment="Production", profile="shared"))
            data = Path(r.artifact_path).read_bytes()
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"f.html\"\r\n"
                f"Content-Type: text/html\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        status, out, _ = request(port, "/api/v1/imports", method="POST", body=body, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}", "Idempotency-Key": "docker-check",
            "X-Bidoc-Session": secret, "X-Requested-With": "bidoc"})
        assert status == 201, (status, out)
        out = json.loads(out)
        path = f"/api/v1/documents/{out['document_id']}/revisions/{out['revision_id']}/download"
        digest = hashlib.sha256(request(port, path)[1]).hexdigest()
        step("publishes a document")

        docker("restart", name)
        wait_ready(port, name)
        assert hashlib.sha256(request(port, path)[1]).hexdigest() == digest
        step("document survives a restart")

        docker("rm", "-f", name)
        start()
        items = json.loads(request(port, "/api/v1/documents")[1])["items"]
        assert [d["title"] for d in items] == ["Container factory"], items
        assert hashlib.sha256(request(port, path)[1]).hexdigest() == digest
        assert docker("exec", name, "cat", "/data/session-secret").stdout.strip() == secret
        hits = json.loads(request(port, "/api/v1/search?q=container")[1])["items"]
        assert hits and hits[0]["document_id"] == out["document_id"], hits
        step("document, session secret and search survive re-creating the container (A03)")

        health = docker("inspect", "--format", "{{.State.Health.Status}}", name).stdout.strip()
        for _ in range(30):
            if health != "starting":
                break
            time.sleep(1)
            health = docker("inspect", "--format", "{{.State.Health.Status}}", name).stdout.strip()
        assert health == "healthy", health
        step("image health check reports healthy")
        return 0
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", "-f", volume, check=False)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "bidoc-library"))
