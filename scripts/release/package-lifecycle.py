#!/usr/bin/env python3
"""Native Linux test controller for two actual packaged revisions in clean Ubuntu.

Only native login entrypoints/status are stubbed. Real Docker, candidate images,
listeners, HTTP, mounts, persistence and installed updater transitions are tested.
No host credentials, model requests, source/runtime mounts or public publication.
"""
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid


def acquire_container(run, *args):
    # Pull progress belongs to stderr; only Docker's authoritative stdout ID
    # grants ownership. Never infer ownership from a name after failed create.
    created = run(["docker", "run", *args], separate_stderr=True)
    if not re.fullmatch(r"[0-9a-f]{64}\n?", created.stdout):
        raise AssertionError(("invalid detached container ID", created.stdout))
    return created.stdout.strip()


def remove_local_image(run, image):
    def present():
        observed = run(["docker", "image", "inspect", image], expected=None)
        if observed.returncode == 0:
            return True
        assert observed.returncode == 1 and "No such image" in observed.stdout, ("cannot verify image absence", image, observed.stdout)
        return False
    if present():
        run(["docker", "image", "rm", image])
    assert not present(), ("local image remains", image)


def inspect_resource(arguments, absent_messages):
    observed = subprocess.run(["docker", *arguments], text=True, capture_output=True, timeout=15)
    if observed.returncode == 0:
        return observed
    diagnostic = observed.stdout + observed.stderr
    assert observed.returncode == 1 and any(message in diagnostic.lower() for message in absent_messages), ("cannot verify resource absence", arguments, diagnostic)
    return None


def cleanup_volume(volume):
    if inspect_resource(["volume", "inspect", volume], ("no such volume",)) is None:
        return volume
    deletion = subprocess.run(["docker", "volume", "rm", volume], text=True, capture_output=True, timeout=30)
    assert deletion.returncode == 0, ("volume removal failed", volume, deletion.stdout, deletion.stderr)
    assert inspect_resource(["volume", "inspect", volume], ("no such volume",)) is None, "volume remains: " + volume
    return volume


def cleanup_container(name, controller_ids, storages, evidence):
    observed = inspect_resource(["inspect", name], ("no such object", "no such container"))
    if observed is None:
        return name  # A planned backup may never have been created.
    config = json.loads(observed.stdout)[0]
    if name in controller_ids:
        safe = config["Id"] == controller_ids[name]
    else:
        safe = (config["Config"].get("Labels") or {}).get("com.symphony.storage") in storages
    if not safe:
        raise AssertionError("refused foreign resource at " + name)
    if name not in controller_ids:
        with (evidence / (name + ".log")).open("w") as output:
            subprocess.run(["docker", "logs", config["Id"]], stdout=output, stderr=subprocess.STDOUT, timeout=15)
    deletion = subprocess.run(["docker", "rm", "-f", config["Id"]], text=True, capture_output=True, timeout=30)
    assert deletion.returncode == 0, "container removal failed: " + name
    assert inspect_resource(["inspect", config["Id"]], ("no such object", "no such container")) is None, "container remains: " + name
    return name


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: package-lifecycle.py PACKAGES RUNNER_ARCHIVES EVIDENCE")
    if os.uname().sysname != "Linux" or os.uname().machine != "x86_64":
        raise SystemExit("actual native Linux x86-64 is required")
    packages, images, evidence = (Path(p).resolve() for p in sys.argv[1:])
    evidence.mkdir(parents=True, exist_ok=True)
    root = evidence / "isolated"; root.mkdir(mode=0o755)
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        raise SystemExit("run the controller as an ordinary CI user")
    namespace = "package-" + uuid.uuid4().hex[:12]
    prefix = "symphony-" + namespace
    client, registry = prefix + "-client", prefix + "-registry"
    owned = set()
    controller_ids = {}
    storages = set()
    owned_volumes = set()
    log = (evidence / "commands.log").open("w")
    result = {"source_revision": os.environ.get("GITHUB_SHA"), "os_arch": os.uname().sysname + "/" + os.uname().machine,
              "nativeAuth": "STUB login entrypoints and Codex login status ONLY; no account/model requests",
              "package_source_revision": os.environ.get("PACKAGE_SOURCE_REVISION", os.environ.get("GITHUB_SHA")),
              "diagnostic_artifact_run": os.environ.get("DIAGNOSTIC_ARTIFACT_RUN"),
              "checks": [], "passed": False}

    def run(args, expected=0, **kwargs):
        args = [str(a) for a in args]
        separate_stderr = kwargs.pop("separate_stderr", False)
        p = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT, timeout=kwargs.pop("timeout", 300), **kwargs)
        line = "$ " + " ".join(args) + "\n" + p.stdout + (p.stderr or "")
        print(line, flush=True); log.write(line); log.flush()
        if expected is not None and p.returncode != expected:
            raise AssertionError((args, p.returncode, p.stdout))
        return p

    def docker(*args, **kwargs):
        return run(["docker", *args], **kwargs).stdout.strip()

    def record(name):
        result["checks"].append(name)
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    def inspect(name, field):
        return docker("inspect", "--format", field, name)

    def free_port():
        with socket.socket() as candidate:
            candidate.bind(("127.0.0.1", 0))
            return candidate.getsockname()[1]

    def state(port):
        deadline = time.monotonic() + 45
        last = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/state", timeout=2) as response:
                    last = json.load(response)
                if "error" not in last and last.get("counts") == {"running": 0, "retrying": 0, "blocked": 0}:
                    (evidence / f"state-{port}.json").write_text(json.dumps(last, indent=2) + "\n")
                    return last
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        raise AssertionError(("real isolated listener unavailable", last))

    def mount_snapshot(name):
        return sorted(json.loads(inspect(name, "{{json .Mounts}}")), key=lambda m: json.dumps(m, sort_keys=True))

    def preserved(name):
        # Read actual retained volumes without starting a stopped listener or
        # changing its state. The throwaway utility process uses the real image.
        return docker("run", "--rm", "--network", "none", "--read-only", "--volumes-from", name + ":ro",
                      "--entrypoint", "sh", inspect(name, "{{.Image}}"), "-ceu",
                      "sha256sum /workspaces/proof/edited /workspaces/proof/untracked /var/lib/symphony/proof-state; git -C /workspaces/proof rev-parse HEAD; GIT_OPTIONAL_LOCKS=0 git -C /workspaces/proof status --porcelain")

    def negative_archive(source_archive, manifest, mode):
        directory = root / ("negative-" + mode); directory.mkdir()
        # These are our own already-checksummed candidate archives, not downloads
        # accepted at the product boundary. The negative payload is deliberate.
        with tarfile.open(source_archive, "r:gz") as source:
            source.extractall(directory, filter="data")
        inner = json.loads((directory / "release.json").read_text())
        outer = json.loads(json.dumps(manifest))
        if mode == "platform":
            inner["package_platform"] = "macos"
        (directory / "release.json").write_text(json.dumps(inner, indent=2) + "\n")
        (directory / "files.sha256").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  ./" + str(p.relative_to(directory)) + "\n"
            for p in sorted(directory.rglob("*")) if p.is_file() and p.name != "files.sha256"))
        archive = root / (mode + ".tar.gz")
        with tarfile.open(archive, "w:gz") as output:
            for path in sorted(directory.iterdir()):
                output.add(path, arcname="./" + path.name)
        outer["bundles"]["linux_x86_64"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        external = root / (mode + ".json"); external.write_text(json.dumps(outer))
        return external, archive

    try:
        current = json.loads((packages / "release.json").read_text())
        previous = json.loads((packages / "previous/release.json").read_text())
        assert current["source_revision"] != previous["source_revision"], "two distinct actual source revisions required"
        assert current["source_revision"] == result["package_source_revision"], "candidate must match exact selected package source"
        assert result["diagnostic_artifact_run"] or result["package_source_revision"] == result["source_revision"], "acceptance candidate must match exact workflow head"
        source_archives = []
        for folder, manifest in ((packages / "previous", previous), (packages, current)):
            bundle = manifest["bundles"]["linux_x86_64"]
            archive = folder / bundle["filename"]
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == bundle["sha256"]
            source_archives.append(archive)
        result["packages"] = [{"source_revision": m["source_revision"], **m["bundles"]["linux_x86_64"]} for m in (previous, current)]
        controller_ids[registry] = acquire_container(run, "-d", "--name", registry, "-e", "REGISTRY_STORAGE_DELETE_ENABLED=true", "-p", "127.0.0.1:5000:5000", "registry:3")
        owned.add(registry)
        for provider in ("codex", "claude"):
            archive = images / f"{provider}-x86_64.docker.tar.gz"
            expected = (images / f"{provider}-x86_64.sha256").read_text().split()[0]
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
            with archive.open("rb") as source:
                # Docker load handles the gzip archive; no shell pipeline status ambiguity.
                run(["docker", "load"], stdin=source)
            for tag, manifest in (("candidate", current), ("previous", previous)):
                image = f"localhost:5000/symphony-{provider}:{tag}"
                docker("push", image)
                digest = docker("image", "inspect", "--format", "{{index .RepoDigests 0}}", image)
                assert digest == manifest["runners"][provider]["x86_64"]["image"], ("image digest changed", image, digest)
                assert inspect(image, '{{index .Config.Labels "org.opencontainers.image.revision"}}') == manifest["source_revision"]
        record("two real source-bound package and runner revision checksums")

        # The client sees only candidate downloads and isolated data, with the
        # same absolute path used for Docker bind mounts on the native host.
        for source in source_archives + [packages / "previous/release.json", packages / "release.json"]:
            target = root / ("old-" + source.name if source.parent.name == "previous" else "new-" + source.name)
            shutil.copyfile(source, target)
        first_manifest, second_manifest = root / "old-release.json", root / "new-release.json"
        first_archive = root / ("old-" + previous["bundles"]["linux_x86_64"]["filename"])
        second_archive = root / ("new-" + current["bundles"]["linux_x86_64"]["filename"])
        home = root / "home"; home.mkdir(mode=0o700)
        tools = root / "tools"; tools.mkdir()
        for name in ("git", "python", "python3", "node", "mise", "swift", "swiftc", "elixir", "erl", "shasum", "codex", "claude"):
            tool = tools / name; tool.write_text("#!/bin/sh\necho 'unexpected host dependency: " + name + "' >&2\nexit 98\n"); tool.chmod(0o755)
        wrapper = root / "docker-peer"
        wrapper.write_text('''#!/bin/bash
set -euo pipefail
printf '%q ' "$@" >> "$PROOF_ROOT/docker-calls.log"; printf '\\n' >> "$PROOF_ROOT/docker-calls.log"
# Only account entrypoints are stubbed; no account credentials are created.
if [[ "$*" == *"--entrypoint codex "* ]] && { [[ "$*" == *" login status" ]] || [[ "$*" == *" login --device-auth" ]]; }; then
  echo 'STUB Codex native login entrypoint/status; no credentials or inference'; exit 0
fi
if [[ "$*" == *"/opt/symphony/claude/auth.mjs login" ]]; then
  echo 'STUB Claude native login entrypoint; no credentials or inference'; exit 0
fi
/usr/bin/docker "$@"
if [ -f "$PROOF_ROOT/fault" ]; then
  mode=$(cat "$PROOF_ROOT/fault")
  if [ "$mode" = create ] && [ "$1" = run ] && [[ "$*" == *"--detach"* ]]; then
    rm "$PROOF_ROOT/fault"; echo 'FAULT after actual Docker create' >&2; exit 97
  fi
fi
'''); wrapper.chmod(0o755)
        socket_gid = os.stat("/var/run/docker.sock").st_gid
        controller_ids[client] = acquire_container(run, "-d", "--name", client, "--network", "host", "--volume", "/var/run/docker.sock:/var/run/docker.sock",
               "--volume", f"{root}:{root}", "ubuntu:26.04", "sleep", "infinity")
        owned.add(client)
        docker("exec", client, "bash", "-euc", "apt-get update && apt-get install -y --no-install-recommends docker.io curl ca-certificates; cat /etc/os-release; uname -sm", timeout=300)
        environment = {
            "HOME": str(home), "PATH": f"{tools}:/usr/bin:/bin", "SYMPHONY_HOME": "", "GH_TOKEN": "", "GITHUB_TOKEN": "",
            "SYMPHONY_RUNNER_NAMESPACE": namespace, "SYMPHONY_APP_HOME": str(root / "app"),
            "SYMPHONY_CONFIG_HOME": str(root / "config"), "SYMPHONY_BIN_DIR": str(root / "bin"),
            "SYMPHONY_SKILLS_DIR": str(root / "skills"), "SYMPHONY_CONTAINER_RUNTIME": str(wrapper), "PROOF_ROOT": str(root),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "XDG_CONFIG_HOME": str(home / ".config")}

        def client_run(args, cwd=root, expected=0, overrides=None):
            env = dict(environment, **(overrides or {}))
            options = ["docker", "exec", "--user", f"{uid}:{socket_gid}", "--workdir", str(cwd)]
            for key, value in env.items(): options += ["--env", key + "=" + value]
            return run([*options, client, *args], expected=expected)

        download = root / "download"; download.mkdir()
        client_run(["tar", "-xzf", str(first_archive), "-C", str(download)])
        client_run([str(download / "install.sh")]); client_run([str(download / "install.sh")])
        command = root / "bin/symphony"
        client_run([str(command), "--version"])
        assert json.loads(client_run([str(command), "doctor", "--json"]).stdout)["ready"] is True
        record("ordinary-user clean Ubuntu26.04 source-free repeat install, version and doctor with poisoned optional tools")
        repositories = [root / "one/service", root / "two/service"]
        identifiers, names, ports = [], [], []
        for number, repo in enumerate(repositories):
            repo.mkdir(parents=True)
            # Preparing the target checkout is a controller responsibility,
            # separate from installing/running the product in the clean client.
            # Use real Git metadata, then poison Git for every product command.
            git_env = {"PATH": os.environ["PATH"], "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
            run(["git", "init", "--quiet", str(repo)], env=git_env)
            run(["git", "-C", str(repo), "remote", "add", "origin", f"https://example.invalid/{number}/service.git"], env=git_env)
            tracked = repo / "tracked-project-canary"; tracked.write_text("committed project canary\n")
            run(["git", "-C", str(repo), "add", tracked.name], env=git_env)
            run(["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "--quiet", "-m", "fixture"], env=git_env)
            tracked.write_text(tracked.read_text() + "dirty project change\n")
            (repo / "untracked-project-canary").write_text("untracked project evidence\n")
            client_run([str(command), "init", "--linear-project", "disposable-package-proof", "--local-skill", "commit"], cwd=repo)
            # No real tracker is ever started: replace the generated template
            # before launching the disposable fixture.
            override = repo / ".codex/skills/symphony-commit/SKILL.md"
            override.write_text(override.read_text() + "\nIntentional local override canary " + str(number) + "\n")
            (repo / "WORKFLOW.md").write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\npolling:\n  interval_ms: 1000\n---\nEmpty isolated candidate fixture; zero inference.\n")
            # Ask the actual packaged launcher for its canonical resource id.
            script = 'source "$1" --help >/dev/null; project_id "$2"'
            identity = client_run(["bash", "-euc", script, "fixture", str(command), str(repo)]).stdout.strip()
            identifiers.append(identity); name = prefix + "-" + identity; names.append(name); owned.update((name, name + "-before-update")); storages.add(name)
            credentials = root / "config/projects" / (identity + ".env")
            credentials.write_text("GH_TOKEN=credential-free-placeholder-" + str(number) + "\n# credential-canary\n")
            credentials.chmod(0o600)
            client_run([str(command), "auth", "codex", "login"], cwd=repo)
            client_run([str(command), "auth", "claude", "login"], cwd=repo)
            owned_volumes.update((prefix + "-codex-auth", name + "-claude-auth", name + "-workspaces", name + "-state"))
            port = free_port(); ports.append(port)
            client_run([str(command), "run", "--port", str(port)], cwd=repo)
            state(port)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                dashboard = response.read().decode()
            assert "Symphony" in dashboard and "<html" in dashboard.lower(), dashboard[:200]
            (evidence / f"dashboard-{number}.html").write_text(dashboard)
        assert identifiers[0] != identifiers[1]
        assert inspect(names[0], '{{index .Config.Labels "com.symphony.storage"}}') != inspect(names[1], '{{index .Config.Labels "com.symphony.storage"}}')
        name, repo, port = names[0], repositories[0], ports[0]
        utility_image = previous["runners"]["codex"]["x86_64"]["image"]
        auth_volumes = [prefix + "-codex-auth", *[n + "-claude-auth" for n in names]]
        for volume in auth_volumes:
            docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "sh", "--volume", volume + ":/proof", utility_image,
                   "-ceu", "printf synthetic-noncredential-native-volume-canary > /proof/package-proof-canary")
        def auth_canaries():
            return {volume: docker("run", "--rm", "--network", "none", "--read-only", "--entrypoint", "sh", "--volume", volume + ":/proof:ro", utility_image,
                                   "-ceu", "sha256sum /proof/package-proof-canary") for volume in auth_volumes}
        auth_before = auth_canaries()
        docker("exec", name, "sh", "-ceu", "mkdir /workspaces/proof; git -C /workspaces/proof init -q; git -C /workspaces/proof config user.name Fixture; git -C /workspaces/proof config user.email fixture@example.invalid; printf committed > /workspaces/proof/edited; git -C /workspaces/proof add edited; git -C /workspaces/proof commit -qm fixture; printf dirty >> /workspaces/proof/edited; printf untracked > /workspaces/proof/untracked; printf state-canary > /var/lib/symphony/proof-state")
        before = preserved(name)
        mount_before = mount_snapshot(name)
        assert {m["Destination"] for m in mount_before} >= {"/workspaces", "/var/lib/symphony", "/etc/symphony/WORKFLOW.md", "/root/.codex"}
        assert all(m["Type"] == "volume" for m in mount_before if m["Destination"] in ("/workspaces", "/var/lib/symphony"))
        canaries = [root / "config/projects" / (i + ".env") for i in identifiers]
        canaries += [r / "WORKFLOW.md" for r in repositories]
        canaries += [r / filename for r in repositories for filename in ("tracked-project-canary", "untracked-project-canary")]
        canaries += [r / ".codex/skills/symphony-commit/SKILL.md" for r in repositories]
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in canaries}
        def unchanged():
            assert preserved(name) == before
            assert mount_snapshot(name) == mount_before
            assert auth_canaries() == auth_before
            assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in canaries} == hashes
        metadata = [root / "config/runners" / (i + ".env") for i in identifiers]
        second_metadata = metadata[1].read_bytes(); second_image = inspect(names[1], "{{.Image}}")
        client_run([str(command), "runner", "stop"], cwd=repo)
        assert inspect(name, "{{.State.Running}}") == "false"
        client_run([str(command), "run", "--port", str(port)], cwd=repo); state(port); unchanged()
        record("two same-basename real memory listeners, attributable dashboard/state, persistent mounts, dirty/untracked/state canaries and stop/restart")
        update_args = ["--candidate-manifest", str(second_manifest), "--candidate-bundle", str(second_archive)]
        client_run([str(command), "runner", "stop"], cwd=repo)
        stopped_id = inspect(name, "{{.Id}}")
        assert inspect(name, "{{.State.Running}}") == "false"
        client_run([str(command), "update", *update_args], cwd=repo)
        assert inspect(name, "{{.State.Running}}") == "false" and inspect(name, "{{.Id}}") == stopped_id
        assert json.loads(client_run([str(command), "--version"]).stdout)["source_revision"] == current["source_revision"]
        assert inspect(names[1], "{{.Image}}") == second_image and metadata[1].read_bytes() == second_metadata
        client_run([str(command), "update", "--rollback-release"], cwd=repo)
        assert json.loads(client_run([str(command), "--version"]).stdout)["source_revision"] == previous["source_revision"]
        assert inspect(name, "{{.State.Running}}") == "false" and inspect(name, "{{.Id}}") == stopped_id
        unchanged()
        client_run([str(command), "run", "--port", str(port)], cwd=repo); state(port)
        record("two real package revisions: manager-only activation and updater-controlled rollback retain stopped runner until explicit run, preserving both selected images")
        # Remove only the unselected new candidate image from this disposable
        # daemon/registry and prove a real immutable-digest pull failure. Restore
        # from the same checksummed archive before positive transition proof.
        image = current["runners"]["codex"]["x86_64"]["image"]
        remove_local_image(run, "localhost:5000/symphony-codex:candidate")
        remove_local_image(run, image)
        digest = image.split("@", 1)[1]
        request = urllib.request.Request("http://127.0.0.1:5000/v2/symphony-codex/manifests/" + digest, method="DELETE")
        with urllib.request.urlopen(request, timeout=5) as response: assert response.status == 202
        failed = client_run([str(command), "update", *update_args, "--agent", "codex"], cwd=repo, expected=1)
        assert "cannot pull released codex image" in failed.stdout
        assert not (root / "config/runners" / (identifiers[0] + ".transition")).exists()
        assert json.loads(client_run([str(command), "--version"]).stdout)["source_revision"] == previous["source_revision"]
        unchanged()
        with (images / "codex-x86_64.docker.tar.gz").open("rb") as source:
            run(["docker", "load"], stdin=source)
        docker("push", "localhost:5000/symphony-codex:candidate")
        assert docker("image", "inspect", "--format", "{{index .RepoDigests 0}}", "localhost:5000/symphony-codex:candidate") == image
        record("actual missing image/digest pull refuses before journal or manager activation; checksummed image restored for retry")
        (root / "fault").write_text("create")
        client_run([str(command), "update", *update_args, "--agent", "codex"], cwd=repo, expected=97)
        assert (root / "config/runners" / (identifiers[0] + ".transition")).is_dir()
        client_run([str(command), "update", "--agent", "codex"], cwd=repo)
        state(port); unchanged()
        assert inspect(name, "{{.Image}}") == inspect(current["runners"]["codex"]["x86_64"]["image"], "{{.Id}}")
        assert inspect(names[1], "{{.Image}}") == second_image and metadata[1].read_bytes() == second_metadata
        record("real selected-runner update after post-create failure, pinned retry and unchanged second-project selected image")
        # Explicit --agent transitions preserve the updater's existing policy:
        # a stopped source may become a running selected candidate; rollback
        # resumes the retained previous runner. Manager-only updates above do
        # not start it. Prove both supported paths without changing policy.
        client_run([str(command), "runner", "stop"], cwd=repo)
        assert inspect(name, "{{.State.Running}}") == "false"
        (root / "fault").write_text("create")
        client_run([str(command), "update", "--candidate-manifest", str(first_manifest), "--candidate-bundle", str(first_archive), "--agent", "codex"], cwd=repo, expected=97)
        client_run([str(command), "update", "--rollback-runner"], cwd=repo)
        state(port); unchanged()
        assert inspect(name, "{{.Image}}") == inspect(current["runners"]["codex"]["x86_64"]["image"], "{{.Id}}")
        assert not (root / "config/runners" / (identifiers[0] + ".transition")).exists()
        record("stopped-source explicit selected transition and updater-controlled pending rollback resume healthy previous image, preserving native-volume/local-override/dirty/untracked/workflow/credential/state evidence")
        before_version = client_run([str(command), "--version"]).stdout
        corrupt = json.loads(second_manifest.read_text()); corrupt["bundles"]["linux_x86_64"]["sha256"] = "0" * 64
        corrupted_manifest = root / "corrupt.json"; corrupted_manifest.write_text(json.dumps(corrupt))
        failed = client_run([str(command), "update", "--candidate-manifest", str(corrupted_manifest), "--candidate-bundle", str(second_archive)], cwd=repo, expected=1)
        assert "checksum mismatch" in failed.stdout
        bad_platform_manifest, bad_platform_archive = negative_archive(second_archive, current, "platform")
        failed = client_run([str(command), "update", "--candidate-manifest", str(bad_platform_manifest), "--candidate-bundle", str(bad_platform_archive)], cwd=repo, expected=1)
        assert "platform" in failed.stdout
        failed = client_run([str(command), "doctor", "--json"], expected=1, overrides={"SYMPHONY_CONTAINER_RUNTIME": "/missing/docker"})
        assert json.loads(failed.stdout)["ready"] is False
        conflict = root / "conflicting-command"; conflict.mkdir(); (conflict / "symphony").write_text("unrelated")
        failed = client_run([str(download / "install.sh"), "--bin-dir", str(conflict), "--app-dir", str(root / "conflict-app")], expected=1)
        assert "refusing" in failed.stdout and (conflict / "symphony").read_text() == "unrelated"
        assert client_run([str(command), "--version"]).stdout == before_version
        unchanged(); state(port)
        record("wrong checksum/platform, actual missing registry image, missing Docker engine and conflicting unmanaged path refuse without data/manager loss")
        calls = (root / "docker-calls.log").read_text()
        assert " build " not in " " + calls
        assert "unexpected host dependency" not in (evidence / "commands.log").read_text()
        result["passed"] = True
        record("credential-free candidate acceptance complete; authenticated login/inference intentionally unperformed")
    finally:
        cleanup_errors = []
        removed = []
        try:
            for name in list(owned):
                try:
                    absent = cleanup_container(name, controller_ids, storages, evidence)
                    if absent:
                        removed.append(absent)
                except Exception as error:
                    cleanup_errors.append(str(error))
            for volume in owned_volumes:
                try:
                    removed.append(cleanup_volume(volume))
                except Exception as error:
                    cleanup_errors.append(str(error))
            if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15).returncode != 0:
                cleanup_errors.append("Docker unavailable; resource absence cannot be verified")
        except Exception as error:
            cleanup_errors.append("cleanup verification failed: " + str(error))
        result["cleanup"] = {"verified_absent": removed, "errors": cleanup_errors}
        if cleanup_errors:
            result["passed"] = False
        (evidence / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        log.close()
        if cleanup_errors:
            raise AssertionError(cleanup_errors)



if __name__ == "__main__":
    main()
