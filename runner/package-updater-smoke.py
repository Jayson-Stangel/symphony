#!/usr/bin/env python3
"""Opt-in actual installed-bundle proof; no credentials, accounts, or inference.

Usage: python3 runner/package-updater-smoke.py MANIFEST1 BUNDLE1 MANIFEST2 BUNDLE2

Both candidate bundles must already contain the packaged updater. They may be
two candidate versions of the same committed source. Docker/image operations,
the native macOS helper, real listener/HTTP controls and persistent mounts are
actual. ONLY Codex login-status is stubbed; an empty Memory tracker has no work.
The test developer's Python instruments Docker/curl, never supplies an installed
manager dependency. Source access is denied with sandbox-exec when available.
Owned disposable resources are removed; local evidence is retained.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid


if len(sys.argv) != 5 or sys.platform != "darwin":
    raise SystemExit(__doc__)
inputs = [Path(value).resolve() for value in sys.argv[1:]]
manifests = [json.loads(inputs[index].read_text()) for index in (0, 2)]
if manifests[0]["runners"]["codex"]["arm64"]["image"] == manifests[1]["runners"]["codex"]["arm64"]["image"]:
    raise SystemExit("update proof requires distinct selected Codex manifest digests")
for index in (0, 1):
    if hashlib.sha256(inputs[index * 2 + 1].read_bytes()).hexdigest() != manifests[index]["bundle"]["sha256"]:
        raise SystemExit("candidate archive checksum differs")
checkout = Path(__file__).resolve().parent.parent
proof = Path(tempfile.mkdtemp(prefix="symphony-package-updater-proof-")).resolve()
namespace = "package-proof-" + uuid.uuid4().hex[:10]
prefix = "symphony-" + namespace
real_docker = shutil.which("docker")
real_curl = shutil.which("curl")
if not real_docker or not real_curl:
    raise SystemExit("Docker and curl are required")
app = proof / "app"
config = proof / "config"
tools = proof / "tools"
tools.mkdir()
docker_calls = proof / "docker.jsonl"
auth_volume = prefix + "-codex-auth"
owned_containers = []
owned_storage = []
environment = dict(os.environ, HOME=str(proof / "home"), SYMPHONY_APP_HOME=str(app),
                   SYMPHONY_CONFIG_HOME=str(config), SYMPHONY_BIN_DIR=str(proof / "bin"),
                   SYMPHONY_SKILLS_DIR=str(proof / "skills"), SYMPHONY_RUNNER_NAMESPACE=namespace,
                   SYMPHONY_CODEX_AUTH_VOLUME=auth_volume, PROOF_ROOT=str(proof),
                   PROOF_DOCKER=real_docker, PROOF_CURL=real_curl, PROOF_APP=str(app))
for name in ("SYMPHONY_HOME", "SYMPHONY_RUNNER_IMAGE", "SYMPHONY_RUNNER_HOME", "GH_TOKEN", "GITHUB_TOKEN", "LINEAR_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    environment.pop(name, None)
endpoint = subprocess.check_output([real_docker, "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], text=True).strip()
environment["DOCKER_HOST"] = endpoint
environment["DOCKER_CONFIG"] = str(proof / "docker-config")
Path(environment["DOCKER_CONFIG"]).mkdir()
Path(environment["HOME"]).mkdir()
for name in ("git", "python3", "node", "codex", "claude", "mise", "erl", "elixir", "swiftc", "xcrun"):
    path = tools / name
    path.write_text("#!/bin/sh\necho 'unexpected host dependency: " + name + "' >&2\nexit 98\n")
    path.chmod(0o755)
peer = tools / "docker-peer"
peer.write_text("#!" + sys.executable + r'''
import json,os,pathlib,subprocess,sys
root=pathlib.Path(os.environ['PROOF_ROOT']);args=sys.argv[1:]
with (root/'docker.jsonl').open('a') as log: log.write(json.dumps(args)+'\n')
if args[0]=='run' and '--entrypoint' in args and args[args.index('--entrypoint')+1]=='codex' and args[-2:]==['login','status']:
    print('STUB Codex native login status: no account credentials or inference');sys.exit(0)
result=subprocess.run([os.environ['PROOF_DOCKER'],*args])
fault=root/'docker-fault'
if result.returncode==0 and fault.exists():
    mode=fault.read_text().strip()
    if (mode=='create' and args[0]=='run' and '--detach' in args) or (mode=='backup-remove' and args[0]=='rm' and args[-1].endswith('-before-update')):
        fault.unlink();print('FAULT AFTER actual Docker side effect: '+mode,file=sys.stderr);sys.exit(97)
sys.exit(result.returncode)
''')
peer.chmod(0o755)
curl = tools / "curl"
curl.write_text("#!" + sys.executable + r'''
import json,os,pathlib,subprocess,sys
root=pathlib.Path(os.environ['PROOF_ROOT']);args=sys.argv[1:]
result=subprocess.run([os.environ['PROOF_CURL'],*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
with (root/'http.jsonl').open('a') as log:log.write(json.dumps({'args':args,'exit':result.returncode,'body':result.stdout.decode(errors='replace')})+'\n')
fault=root/'activation-fault'
if result.returncode==0 and fault.exists() and args[-1].endswith('/api/v1/agent/resume'):
    fault.unlink();os.chmod(os.environ['PROOF_APP'],0o500)
    print('FAULT after actual resume: deny manager activation writes',file=sys.stderr)
sys.stdout.buffer.write(result.stdout);sys.stderr.buffer.write(result.stderr);sys.exit(result.returncode)
''')
curl.chmod(0o755)
environment["SYMPHONY_CONTAINER_RUNTIME"] = str(peer)
environment["PATH"] = str(tools) + ":/usr/bin:/bin:/usr/sbin:/sbin"
command = proof / "bin/symphony"
sandbox = shutil.which("sandbox-exec")
sandbox_prefix = [sandbox, "-p", '(version 1)(allow default)(deny file-read* (subpath ' + json.dumps(str(checkout)) + '))'] if sandbox else []


def run(args, expected=0, **kwargs):
    result = subprocess.run([str(value) for value in args], text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    print("$", " ".join(map(str, args)), "\n", result.stdout, flush=True)
    if expected is not None:
        assert result.returncode == expected, (result.returncode, result.stdout)
    return result


def docker(*args):
    return run([real_docker, *args], env=environment).stdout.strip()


def launch(*args, project=0, expected=0, extra=None):
    directory = projects[project] if projects[project].exists() else proof
    return run([*sandbox_prefix, command, *args], expected, cwd=directory, env=environment | (extra or {})).stdout


def free_port():
    with socket.socket() as connection:
        connection.bind(("127.0.0.1", 0))
        return connection.getsockname()[1]


def state(port):
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/state", timeout=2) as response:
                value = json.load(response)
            if "error" not in value:
                assert value["counts"] == {"running": 0, "retrying": 0, "blocked": 0}
                return value
        except OSError:
            time.sleep(.2)
    raise AssertionError("installed real listener did not become healthy")


def current_version():
    return json.loads(launch("--version"))["version"]


def update_second(*args, expected=0):
    return launch("update", "--candidate-manifest", inputs[2], "--candidate-bundle", inputs[3], *args, expected=expected)


projects = [proof / "one/service", proof / "two/service"]
ports = [free_port(), free_port()]
identities = []
try:
    extracted = proof / "download"
    extracted.mkdir()
    run(["tar", "-xzf", inputs[1], "-C", extracted])
    run([extracted / "install.sh"], env=environment)
    shutil.rmtree(extracted)
    assert current_version() == manifests[0]["version"]
    launch("doctor", "--json")
    if sandbox:
        denied = run([*sandbox_prefix, "/bin/cat", checkout / "scripts/symphony"], expected=None, env=environment)
        assert denied.returncode != 0, "source checkout access was not denied"
    docker("volume", "create", auth_volume)
    for index, project in enumerate(projects):
        project.mkdir(parents=True)
        run(["git", "init", "-q", project])
        run(["git", "-C", project, "remote", "add", "origin", f"https://example.invalid/proof-{index}/service.git"])
        launch("init", "--linear-project", "unused-dispatch-disabled", project=index)
        (project / "WORKFLOW.md").write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\npolling:\n  interval_ms: 1000\nobservability:\n  dashboard_enabled: true\n---\nEmpty isolated package proof. No issues or inference.\n")
        metadata = json.loads(launch("project", "doctor", "--json", project=index))
        identity = metadata.get("project_id") or metadata.get("identity")
        if not identity:
            candidates = set((config / "projects").glob("*.env")) - {config / "projects" / (value + ".env") for value in identities}
            assert len(candidates) == 1, metadata
            identity = candidates.pop().stem
        identities.append(identity)
        credentials = config / "projects" / (identity + ".env")
        credentials.write_text("GH_TOKEN=credential-free-placeholder-" + str(index) + "\n")
        name = prefix + "-" + identity
        owned_containers.extend([name, name + "-before-update"])
        owned_storage.append(name)
        launch("run", "--port", str(ports[index]), project=index)
        snapshot = state(ports[index])
        (proof / f"initial-state-{index}.json").write_text(json.dumps(snapshot, indent=2))
        html = urllib.request.urlopen(f"http://127.0.0.1:{ports[index]}/", timeout=5).read().decode()
        assert "Symphony" in html
        (proof / f"dashboard-{index}.html").write_text(html)
        mounts = json.loads(docker("inspect", "--format", "{{json .Mounts}}", name))
        assert {value["Destination"] for value in mounts} == {"/etc/symphony/WORKFLOW.md", "/root/.codex", "/workspaces", "/var/lib/symphony"}
        assert all(value.get("Type") == "volume" or value["Source"] == str(project / "WORKFLOW.md") for value in mounts)
        (proof / f"mounts-{index}.json").write_text(json.dumps(mounts, indent=2))
    assert identities[0] != identities[1]
    names = [prefix + "-" + identity for identity in identities]
    docker("exec", names[0], "sh", "-ceu", "mkdir /workspaces/package-canary; git -C /workspaces/package-canary init -q; git -C /workspaces/package-canary config user.name Fixture; git -C /workspaces/package-canary config user.email fixture@example.invalid; printf tracked >/workspaces/package-canary/tracked; git -C /workspaces/package-canary add tracked; git -C /workspaces/package-canary commit -qm canary; printf dirty >>/workspaces/package-canary/tracked; printf evidence >/workspaces/package-canary/untracked; printf retained >/var/lib/symphony/package-state")
    def preservation():
        return docker("exec", names[0], "sh", "-ceu", "sha256sum /workspaces/package-canary/tracked /workspaces/package-canary/untracked /var/lib/symphony/package-state; git -C /workspaces/package-canary rev-parse HEAD; git -C /workspaces/package-canary status --porcelain")
    canary = preservation()
    original_credentials = {path: path.read_bytes() for path in (config / "projects").glob("*.env")}
    workflows = {path: (path / "WORKFLOW.md").read_bytes() for path in projects}
    launch("runner", "stop")
    launch("run", "--port", str(ports[0])); state(ports[0])
    assert preservation() == canary
    corrupt = proof / "corrupt.tar.gz"
    corrupt.write_bytes(b"interrupted download")
    failure = launch("update", "--candidate-manifest", inputs[2], "--candidate-bundle", corrupt, expected=1)
    assert "checksum mismatch" in failure and current_version() == manifests[0]["version"]
    assert state(ports[0]) and preservation() == canary
    (proof / "docker-fault").write_text("create")
    update_second("--agent", "codex", expected=97)
    assert current_version() == manifests[0]["version"]
    alternate = proof / "alternate-config"
    alternate.mkdir()
    refusal = launch("update", project=1, expected=1, extra={"SYMPHONY_CONFIG_HOME": str(alternate)})
    assert "unfinished packaged update" in refusal
    launch("runner", "stop")
    launch("update", "--agent", "codex")
    assert current_version() == manifests[1]["version"]
    assert state(ports[0]) and preservation() == canary
    launch("runner", "stop", project=1)
    launch("run", "--port", str(ports[1]), project=1); state(ports[1])
    assert docker("inspect", "--format", "{{.Image}}", names[1]) != docker("inspect", "--format", "{{.Image}}", names[0])
    launch("update", "--rollback-release", "--agent", "codex")
    assert current_version() == manifests[0]["version"]
    assert state(ports[0]) and preservation() == canary
    # Fail manager activation after actual candidate admission. The old manager
    # and qualified prior container must still support updater-controlled rollback.
    (proof / "activation-fault").touch()
    failure = update_second("--agent", "codex", expected=None)
    assert "FAULT after actual resume" in failure
    assert current_version() == manifests[0]["version"]
    assert docker("inspect", "--format", "{{.State.Running}}", names[0] + "-before-update") == "false"
    app.chmod(0o700)
    launch("update", "--rollback-runner")
    assert current_version() == manifests[0]["version"]
    assert state(ports[0]) and preservation() == canary
    update_second("--agent", "codex")
    assert current_version() == manifests[1]["version"]
    assert state(ports[0]) and preservation() == canary
    assert all(path.read_bytes() == contents for path, contents in original_credentials.items())
    assert all((path / "WORKFLOW.md").read_bytes() == contents for path, contents in workflows.items())
    assert not (app / "pending-transition").exists()
    evidence = {"environment": "macOS native arm64 developer host, isolated installed candidate bundles",
                "namespace": namespace, "versions": [{key: value[key] for key in ("version", "source_revision", "runners")} for value in manifests],
                "sourceAccessDenied": bool(sandbox), "nativeLogin": "STUB login-status ONLY; no credentials",
                "modelRequests": 0, "tracker": "Memory, zero issues", "cleanMacOS": False,
                "actualIntelMac": False, "root": str(proof), "ports": ports,
                "observations": "actual archive install, UI HTML/API, retained mounts/state/Git, stop/restart, failed checksum, incomplete create/retry, two-project pin, completed rollback, activation-failure rollback"}
    (proof / "evidence.json").write_text(json.dumps(evidence, indent=2))
    print("PASS actual packaged update lifecycle; evidence " + str(proof / "evidence.json"), flush=True)
finally:
    if app.exists(): app.chmod(0o700)
    for name in owned_containers:
        with (proof / (name + ".log")).open("w") as output:
            subprocess.run([real_docker, "logs", name], env=environment, stdout=output, stderr=subprocess.STDOUT)
        owner = subprocess.run([real_docker, "inspect", "--format", '{{index .Config.Labels "com.symphony.storage"}}', name], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if owner.returncode == 0 and owner.stdout.strip() in owned_storage:
            subprocess.run([real_docker, "rm", "-f", name], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    volumes = subprocess.check_output([real_docker, "volume", "ls", "--format", "{{.Name}}"], env=environment, text=True).splitlines()
    for volume in volumes:
        if volume.startswith(prefix + "-"):
            subprocess.run([real_docker, "volume", "rm", volume], env=environment, stdout=subprocess.DEVNULL)
    print("Owned disposable containers/volumes removed. Evidence retained: " + str(proof), flush=True)
