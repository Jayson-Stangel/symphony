"""Private HTTPS Git and empty Linear peers for credential-free package tests."""

import http.server
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
from urllib.parse import urlsplit


class WorkflowFixture:
    def __init__(self, directory, docker, name, image):
        self.directory = directory
        self.docker = docker
        self.name = name
        self.image = image
        self.container_id = None
        self.network_id = None

    @property
    def environment(self):
        return {"PROOF_WORKFLOW_NETWORK": self.name, "PROOF_WORKFLOW_CA": str(self.directory / "cert.pem")}

    def publish(self, checkout, owner, basename):
        assert re.fullmatch(r"[a-z0-9-]+", owner) and re.fullmatch(r"[a-z0-9-]+", basename)
        bare = self.directory / "git" / owner / (basename + ".git")
        bare.parent.mkdir(parents=True, exist_ok=True)
        env = {"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        subprocess.run(["git", "clone", "--quiet", "--bare", str(checkout), str(bare)], check=True, env=env)
        return subprocess.check_output(["git", "-C", str(bare), "rev-parse", "HEAD"], text=True, env=env).strip()

    def certificates(self):
        def openssl(*args):
            subprocess.run(["openssl", *args], cwd=self.directory, check=True, capture_output=True)

        openssl("req", "-x509", "-nodes", "-newkey", "rsa:2048", "-days", "2",
                "-subj", "/CN=Symphony test CA", "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", "ca.key", "-out", "cert.pem")
        openssl("req", "-nodes", "-newkey", "rsa:2048", "-subj", "/CN=github.com",
                "-keyout", "key.pem", "-out", "request.pem")
        (self.directory / "extensions").write_text(
            "subjectAltName=DNS:github.com,DNS:api.linear.app\nbasicConstraints=critical,CA:FALSE\nextendedKeyUsage=serverAuth\n")
        openssl("x509", "-req", "-in", "request.pem", "-CA", "cert.pem", "-CAkey", "ca.key",
                "-CAcreateserial", "-days", "2", "-extfile", "extensions", "-out", "server.pem")
        (self.directory / "cert.pem").chmod(0o644)

    def start(self):
        self.certificates()
        self.network_id = self.docker("network", "create", self.name).strip()
        self.container_id = self.docker(
            "run", "--detach", "--name", self.name, "--network", self.network_id,
            "--network-alias", "github.com", "--network-alias", "api.linear.app",
            "--read-only", "--entrypoint", "/usr/bin/python3",
            "--mount", f"type=bind,source={self.directory},target=/fixture,readonly",
            "--mount", f"type=bind,source={Path(__file__).resolve()},target=/fixture-server.py,readonly",
            self.image, "/fixture-server.py", "/fixture",
        ).strip()

    def stop(self):
        if self.container_id:
            self.docker("stop", "--time", "5", self.container_id)
            self.docker("rm", self.container_id)
        if self.network_id:
            self.docker("network", "rm", self.network_id)


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.handle_fixture()

    def do_POST(self):
        self.handle_fixture()

    def handle_fixture(self):
        host = self.headers.get("Host")
        target = urlsplit(self.path)
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if host == "github.com" and re.fullmatch(r"/[a-z0-9-]+/[a-z0-9-]+\.git/(info/refs|git-upload-pack)", target.path):
            self.git_response(target, body)
        elif host == "api.linear.app" and target.path == "/graphql" and self.command == "POST":
            self.linear_response(json.loads(body))
        else:
            self.send_error(404)

    def git_response(self, target, body):
        env = {
            "PATH": "/usr/bin:/bin",
            "GIT_PROJECT_ROOT": str(self.server.fixture_root / "git"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(self.server.fixture_root / "git" / target.path.lstrip("/").split(".git/", 1)[0]) + ".git",
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": target.path,
            "QUERY_STRING": target.query,
            "REQUEST_METHOD": self.command,
            "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(body)),
            "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
        }
        result = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True, timeout=10, check=True)
        headers, payload = result.stdout.split(b"\r\n\r\n", 1)
        fields = dict(line.decode().split(": ", 1) for line in headers.split(b"\r\n"))
        self.send_response(int(fields.pop("Status", "200").split()[0]))
        for key, value in fields.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def linear_response(self, request):
        query = request.get("query", "")
        slug = request.get("variables", {}).get("projectSlug")
        if "query SymphonyLinearProject" in query and isinstance(slug, str) and slug.startswith("fixture-"):
            data = {"projects": {"nodes": [{"id": "synthetic-" + slug, "slugId": slug}]}}
        elif "query SymphonyLinearViewer" in query:
            data = {"viewer": {"id": "fixture-viewer"}}
        elif "query SymphonyLinearPoll" in query or "query SymphonyLinearIssuesById" in query:
            data = {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        else:
            self.send_error(404)
            return
        payload = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    fixture_root = Path(sys.argv[1])
    server = http.server.ThreadingHTTPServer(("", 443), FixtureHandler)
    server.fixture_root = fixture_root
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(fixture_root / "server.pem", fixture_root / "key.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
