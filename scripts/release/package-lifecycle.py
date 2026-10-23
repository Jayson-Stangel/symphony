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


def package_selections(packages, current_only, revision, manager_update_only=False):
    current = json.loads((packages / "release.json").read_text())
    if current_only:
        assert current["source_revision"] == revision, "current-manager integration requires this exact manager source"
        return [(packages, current)]
    previous = json.loads((packages / "previous/release.json").read_text())
    assert current["source_revision"] != previous["source_revision"], "two distinct actual source revisions required"
    selected = [(packages / "previous", previous), (packages, current)]
    if manager_update_only and (packages / 'legacy/release.json').exists():
        legacy = json.loads((packages / 'legacy/release.json').read_text())
        assert legacy['schema_version'] == previous['schema_version'] == 1 and current['schema_version'] == 2, 'legacy chain requires actual schema1 old/bridge and schema2 current packages'
        assert len({legacy['source_revision'], previous['source_revision'], current['source_revision']}) == 3, 'three distinct actual manager sources required'
        selected.insert(0, (packages / 'legacy', legacy))
    return selected


def verify_loaded_component(config, record, provider, independent):
    labels = config.get("Config", {}).get("Labels") or {}
    assert (config.get("Architecture") == "amd64" and record.get("platform") == "linux/amd64" and
            labels.get("com.symphony.agent") == provider and labels.get("com.symphony.architecture") == "amd64" and
            labels.get("org.opencontainers.image.revision") == record["source_revision"]), "loaded original image source/platform/provider differs"
    if independent:
        assert config.get("Id") == record.get("image_config"), "loaded original image config differs"
        assert labels.get("com.symphony.protocol") == str(record.get("protocol")) == "1", "loaded original runner protocol differs"
        assert labels.get("com.symphony.inputs.sha256") == record.get("input_identity"), "loaded original input identity differs"


def load_original_archive(run, folder, provider):
    archive = folder / f"{provider}-x86_64.docker.tar.gz"
    sidecar = folder / f"{provider}-x86_64.sha256"
    assert archive.is_file() and not archive.is_symlink() and sidecar.is_file() and not sidecar.is_symlink()
    fields = sidecar.read_text().strip().split()
    assert len(fields) == 2 and re.fullmatch('[a-f0-9]{64}', fields[0]) and fields[1] in (archive.name, './'+archive.name), 'original archive checksum identity differs'
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''): digest.update(block)
    assert digest.hexdigest() == fields[0], 'original archive checksum differs'
    with archive.open('rb') as source:
        run(['docker', 'load'], stdin=source)


def stage_separate_previous(run, images, provider, previous, folder_name="previous"):
    """Load an original prior subject before current tags; retain its exact identity."""
    folder = images / folder_name
    if not folder.exists(): return None
    assert folder.is_dir() and not folder.is_symlink(), 'previous subjects must use an owned directory'
    load_original_archive(run, folder, provider)
    record = previous['runners'][provider]['x86_64']
    # The adapter selects the original current role. New schema2 records retain
    # their actual archive_tag; historical90 current records predate that field.
    original = record.get('archive_tag', f'localhost:5000/symphony-{provider}:candidate')
    assert original in (f'localhost:5000/symphony-{provider}:candidate', f'localhost:5000/symphony-{provider}:previous')
    observed = json.loads(run(['docker', 'image', 'inspect', original]).stdout)[0]
    verify_loaded_component(observed, record, provider, previous['schema_version'] == 2)
    alias = f'localhost:5000/symphony-{provider}:previous'
    run(['docker', 'tag', original, alias])
    checked = json.loads(run(['docker', 'image', 'inspect', alias]).stdout)[0]
    assert checked['Id'] == observed['Id'], 'local previous-role alias changes original config'
    return observed['Id']


def cleanup_negative_image(run, reference, token):
    """Remove only a diagnostic image carrying this run's unique ownership label."""
    def absent(receipt, selected):
        return receipt.returncode == 1 and receipt.stdout.strip() in ('', '[]') and (receipt.stderr or '').strip() in (
            'Error response from daemon: No such image: ' + selected, 'Error: No such image: ' + selected)
    receipt = run(['docker', 'image', 'inspect', reference], expected=None, separate_stderr=True)
    if receipt.returncode:
        assert absent(receipt, reference), ('negative image absence uncertain', reference, receipt.stdout, receipt.stderr)
        return
    images = json.loads(receipt.stdout)
    assert len(images) == 1, 'ambiguous negative image receipt'
    image = images[0]
    identifier = image.get('Id', '')
    assert re.fullmatch(r'sha256:[a-f0-9]{64}', identifier), 'invalid negative image config identity'
    assert image.get('Config', {}).get('Labels', {}).get('com.symphony.lifecycle-negative') == token, 'refused foreign negative image'
    run(['docker', 'image', 'rm', identifier])
    for selected in (reference, identifier):
        checked = run(['docker', 'image', 'inspect', selected], expected=None, separate_stderr=True)
        assert absent(checked, selected), ('negative image remains or absence uncertain', selected, checked.stdout, checked.stderr)


def main():
    arguments = sys.argv[1:]
    current_only = bool(arguments and arguments[0] == "--current-manager-only")
    manager_update_only = bool(arguments and arguments[0] == "--manager-only-update")
    if current_only or manager_update_only:
        arguments = arguments[1:]
    if len(arguments) != 3:
        raise SystemExit("Usage: package-lifecycle.py [--current-manager-only | --manager-only-update] PACKAGES RUNNER_ARCHIVES EVIDENCE")
    if os.uname().sysname != "Linux" or os.uname().machine != "x86_64":
        raise SystemExit("actual native Linux x86-64 is required")
    packages, images, evidence = (Path(p).resolve() for p in arguments)
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
    negative_images = {}
    distribution_aliases = set()
    storages = set()
    owned_volumes = set()
    log = (evidence / "commands.log").open("w")
    result = {"source_revision": os.environ.get("GITHUB_SHA"), "os_arch": os.uname().sysname + "/" + os.uname().machine,
              "nativeAuth": "STUB login entrypoints and Codex login status ONLY; no account/model requests",
              "package_source_revision": os.environ.get("PACKAGE_SOURCE_REVISION", os.environ.get("GITHUB_SHA")),
              "diagnostic_artifact_run": os.environ.get("DIAGNOSTIC_ARTIFACT_RUN"),
              "mode": "current-manager-only" if current_only else "manager-only-update" if manager_update_only else "two-revision-lifecycle",
              "qualified_runner_components": [], "checks": [], "passed": False}

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
        selections = package_selections(packages, current_only, result["source_revision"], manager_update_only)
        current = selections[-1][1]
        initial = selections[0][1]
        previous = selections[-2][1] if len(selections) > 1 else initial
        legacy_chain = len(selections) == 3
        if legacy_chain:
            result['mode'] = 'legacy-bridge-manager-only-update'
        if manager_update_only:
            assert previous['runners'] == current['runners'], 'manager-only update requires the exact unchanged original component records'
        assert current["source_revision"] == result["package_source_revision"], "candidate must match exact selected package source"
        assert result["diagnostic_artifact_run"] or result["package_source_revision"] == result["source_revision"], "acceptance candidate must match exact workflow head"
        source_archives = []
        for folder, manifest in selections:
            bundle = manifest["bundles"]["linux_x86_64"]
            archive = folder / bundle["filename"]
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == bundle["sha256"]
            source_archives.append(archive)
        result["packages"] = [{"source_revision": manifest["source_revision"], **manifest["bundles"]["linux_x86_64"]}
                              for _, manifest in selections]
        controller_ids[registry] = acquire_container(run, "-d", "--name", registry, "-e", "REGISTRY_STORAGE_DELETE_ENABLED=true", "-p", "127.0.0.1:5000:5000", "registry:3")
        owned.add(registry)
        for provider in ("codex", "claude"):
            # A separately verified old current-role subject may use the same
            # :candidate tag. Alias it locally before loading the new current.
            previous_config = stage_separate_previous(run, images, provider, initial, "legacy" if legacy_chain else "previous") if not current_only else None
            load_original_archive(run, images, provider)
            selected = [("candidate", current)] if current_only else [("candidate", current), ("previous", initial)]
            for legacy_tag, manifest in selected:
                qualified = manifest["runners"][provider]["x86_64"]
                image = (f"localhost:5000/symphony-{provider}:previous" if legacy_tag == 'previous' and previous_config is not None
                         else qualified.get("archive_tag", f"localhost:5000/symphony-{provider}:{legacy_tag}"))
                assert image in (f"localhost:5000/symphony-{provider}:candidate", f"localhost:5000/symphony-{provider}:previous"), "foreign original archive tag"
                original = qualified["image"]
                assert re.fullmatch(f"localhost:5000/symphony-{provider}@sha256:[a-f0-9]{{64}}", original), "original digest must use the isolated candidate registry route"
                loaded = json.loads(docker("image", "inspect", image))[0]
                verify_loaded_component(loaded, qualified, provider, manifest["schema_version"] == 2)
                if legacy_tag == 'previous' and previous_config is not None:
                    assert loaded['Id'] == previous_config, 'current archive replaced the selected old subject'
                if legacy_chain and legacy_tag == 'previous':
                    assert (loaded.get('Config', {}).get('Labels') or {}).get('com.symphony.protocol') in (None, ''), 'legacy fixture must retain the actual unlabeled old image'
                docker("push", image)
                observed = json.loads(docker("image", "inspect", image))[0]
                assert original in observed.get("RepoDigests", []), ("original image digest changed", image, original)
                docker("pull", original)
                pulled = json.loads(docker("image", "inspect", original))[0]
                verify_loaded_component(pulled, qualified, provider, manifest["schema_version"] == 2)
                alias = manifest.get("distribution", {}).get(provider, {}).get("x86_64")
                if alias is not None:
                    assert manifest["schema_version"] == 2
                    expected_alias = f"localhost:5000/symphony-distribution-{provider}@" + original.split("@", 1)[1]
                    assert alias == expected_alias, "diagnostic distribution must preserve original digest in the isolated repository"
                    alias_tag = f"localhost:5000/symphony-distribution-{provider}:" + legacy_tag
                    distribution_aliases.update((alias_tag, alias))
                    docker("tag", original, alias_tag)
                    docker("push", alias_tag)
                    assert alias in json.loads(docker("image", "inspect", alias_tag))[0].get("RepoDigests", []), "alias changed original OCI digest"
                    # Remove the alias references before the actual registry
                    # pull. Shared original layers/config remain valid; they
                    # are not fresh anonymous network-download evidence.
                    remove_local_image(run, alias_tag)
                    remove_local_image(run, alias)
                    docker("pull", alias)
                    distributed = json.loads(docker("image", "inspect", alias))[0]
                    verify_loaded_component(distributed, qualified, provider, True)
                    assert distributed["Id"] == pulled["Id"], "alias changed original config"
                result["qualified_runner_components"].append({"provider": provider, "architecture": "x86_64",
                    "manager_source_revision": manifest["source_revision"], "runner_source_revision": qualified["source_revision"],
                    "image": original, "distribution_image": alias, "observed_image_config": pulled["Id"],
                    "input_identity": qualified.get("input_identity"), "protocol": qualified.get("protocol")})
        record("actual source-bound manager package and original runner checksums" if current_only else "two real source-bound package and runner revision checksums")

        # The client sees only candidate downloads and isolated data, with the
        # same absolute path used for Docker bind mounts on the native host.
        for index, (folder, manifest) in enumerate(selections):
            prefix = 'new-' if index == len(selections)-1 else 'legacy-' if legacy_chain and index == 0 else 'old-'
            for source in (source_archives[index], folder / 'release.json'):
                shutil.copyfile(source, root / (prefix + source.name))
        first_prefix = 'legacy-' if legacy_chain else 'new-' if current_only else 'old-'
        first_manifest = root / (first_prefix + 'release.json')
        second_manifest = root / "new-release.json"
        first_archive = root / (first_prefix + initial["bundles"]["linux_x86_64"]["filename"])
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
            selected_agent = "claude" if (current_only or manager_update_only) and number == 1 else "codex"
            client_run([str(command), "run", "--agent", selected_agent, "--port", str(port)], cwd=repo)
            state(port)
            assert inspect(name, "{{.Image}}") == inspect(initial["runners"][selected_agent]["x86_64"]["image"], "{{.Id}}")
            selected_reference = initial.get("distribution", {}).get(selected_agent, {}).get("x86_64", initial["runners"][selected_agent]["x86_64"]["image"])
            assert inspect(name, "{{.Config.Image}}") == selected_reference, "installed manager did not select the manifest distribution reference"
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
                dashboard = response.read().decode()
            assert "Symphony" in dashboard and "<html" in dashboard.lower(), dashboard[:200]
            (evidence / f"dashboard-{number}.html").write_text(dashboard)
        assert identifiers[0] != identifiers[1]
        assert inspect(names[0], '{{index .Config.Labels "com.symphony.storage"}}') != inspect(names[1], '{{index .Config.Labels "com.symphony.storage"}}')
        name, repo, port = names[0], repositories[0], ports[0]
        utility_image = initial["runners"]["codex"]["x86_64"]["image"]
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
        if legacy_chain:
            bridge_manifest = root/'old-release.json'
            bridge_archive = root/('old-'+previous['bundles']['linux_x86_64']['filename'])
            original_ids = {selected: (inspect(selected, '{{.Id}}'), inspect(selected, '{{.Image}}')) for selected in names}
            original_metadata = [path.read_bytes() for path in metadata]
            client_run([str(command), 'update', '--candidate-manifest', str(bridge_manifest), '--candidate-bundle', str(bridge_archive)], cwd=repo)
            assert json.loads(client_run([str(command), '--version']).stdout)['source_revision'] == previous['source_revision']
            assert {selected: (inspect(selected, '{{.Id}}'), inspect(selected, '{{.Image}}')) for selected in names} == original_ids
            assert [path.read_bytes() for path in metadata] == original_metadata
            unchanged()
            client_run([str(command), 'runner', 'stop'], cwd=repo)
            client_run([str(command), 'run', '--port', str(port)], cwd=repo); state(port); unchanged()
            assert inspect(name, '{{.Image}}') == original_ids[name][1]
            assert inspect(names[1], '{{.Id}}') == original_ids[names[1]][0] and metadata[1].read_bytes() == second_metadata
            record('actual old schema1 manager to strict schema1 bridge retains both original unlabeled provider images and persistent project canaries')
        update_args = ["--candidate-manifest", str(second_manifest), "--candidate-bundle", str(second_archive)]
        if current["schema_version"] == 2:
            # This deliberately incompatible image is a negative fixture only:
            # no Dockerfile build, export, qualified record or future baseline.
            fixture = prefix + "-protocol-negative"
            original_image = inspect(name, "{{.Image}}")
            controller_ids[fixture] = acquire_container(run, "-d", "--name", fixture, "--network", "none",
                                                       "--entrypoint", "sleep", original_image, "infinity")
            owned.add(fixture)
            negative_tag = "symphony-negative:" + namespace
            negative_images[negative_tag] = namespace
            committed = run(["docker", "commit", "--change", "LABEL com.symphony.protocol=2", "--change",
                             "LABEL com.symphony.lifecycle-negative=" + namespace, controller_ids[fixture], negative_tag],
                            separate_stderr=True)
            negative_config = committed.stdout.strip()
            assert re.fullmatch(r"sha256:[a-f0-9]{64}", negative_config) and negative_config != original_image
            assert inspect(negative_config, '{{index .Config.Labels "com.symphony.protocol"}}') == "2"
            assert inspect(negative_config, '{{index .Config.Labels "com.symphony.lifecycle-negative"}}') == namespace
            original_metadata = metadata[0].read_bytes()
            substituted, count = re.subn(rb"(?m)^SYMPHONY_RUNNER_IMAGE_ID=sha256:[a-f0-9]{64}$",
                                        b"SYMPHONY_RUNNER_IMAGE_ID=" + negative_config.encode(), original_metadata)
            assert count == 1, 'owned runner metadata has no unique config identity'
            activation_links = {path: os.readlink(root / 'app' / path) if (root / 'app' / path).is_symlink() else None
                                for path in ('current', 'previous')}
            containers_before = {selected: (inspect(selected, "{{.Id}}"), inspect(selected, "{{.State.Running}}")) for selected in names}
            assert not list((root / "config/runners").glob("*.transition*")), "unexpected pending transition before negative fixture"
            try:
                metadata[0].write_bytes(substituted)
                failed = client_run([str(command), "update", *update_args], cwd=repo, expected=1)
                assert 'retained runner protocol is incompatible with this manager' in failed.stdout
                assert {path: os.readlink(root / 'app' / path) if (root / 'app' / path).is_symlink() else None
                        for path in ('current', 'previous')} == activation_links
                assert {selected: (inspect(selected, "{{.Id}}"), inspect(selected, "{{.State.Running}}")) for selected in names} == containers_before
                assert not list((root / "config/runners").glob("*.transition*")), "incompatible runner created a project transition"
                assert metadata[0].read_bytes() == substituted and metadata[1].read_bytes() == second_metadata
                assert inspect(names[1], "{{.Image}}") == second_image
                unchanged()
                result["incompatible_retained_fixture"] = {"original_config": original_image, "negative_config": negative_config,
                    "protocol": 2, "qualified": False, "manager_activation_refused": True,
                    "scope": "current package only" if current_only else "actual prior-to-current package update"}
                record("actual incompatible retained config refuses before manager activation or project transition; both projects and canaries preserved")
            finally:
                metadata[0].write_bytes(original_metadata)
        if current_only:
            assert json.loads(client_run([str(command), "--version"]).stdout)["source_revision"] == current["source_revision"]
            assert inspect(names[1], "{{.Image}}") == second_image and metadata[1].read_bytes() == second_metadata
            for selected_repo, selected_name in zip(repositories, names):
                client_run([str(command), "runner", "stop"], cwd=selected_repo)
                assert inspect(selected_name, "{{.State.Running}}") == "false"
            unchanged()
            result["passed"] = True
            record("current exact-source installed manager exercises verified original runners; two-source update/failure/rollback not requested in this mode")
            return
        if manager_update_only:
            runner_snapshot = {selected: (inspect(selected, "{{.Id}}"), inspect(selected, "{{.Image}}")) for selected in names}
            metadata_snapshot = [path.read_bytes() for path in metadata]
            client_run([str(command), "runner", "stop"], cwd=repo)
            assert inspect(name, "{{.State.Running}}") == "false"
            def retained():
                assert {selected: (inspect(selected, "{{.Id}}"), inspect(selected, "{{.Image}}")) for selected in names} == runner_snapshot
                assert [path.read_bytes() for path in metadata] == metadata_snapshot
                assert inspect(name, "{{.State.Running}}") == "false"
                assert inspect(names[1], "{{.State.Running}}") == "true"
                assert not list((root / "config/runners").glob("*.transition*"))
                unchanged()
            client_run([str(command), "update", *update_args], cwd=repo)
            assert json.loads(client_run([str(command), "--version"]).stdout)['source_revision'] == current['source_revision']
            retained()
            client_run([str(command), "update", "--rollback-release"], cwd=repo)
            assert json.loads(client_run([str(command), "--version"]).stdout)['source_revision'] == previous['source_revision']
            retained()
            corrupted = json.loads(second_manifest.read_text())
            corrupted['bundles']['linux_x86_64']['sha256'] = '0'*64
            invalid = root/'manager-only-corrupt-manifest.json'; invalid.write_text(json.dumps(corrupted))
            failed = client_run([str(command), 'update', '--candidate-manifest', str(invalid), '--candidate-bundle', str(second_archive)], cwd=repo, expected=1)
            assert 'checksum mismatch' in failed.stdout
            assert json.loads(client_run([str(command), '--version']).stdout)['source_revision'] == previous['source_revision']
            retained()
            client_run([str(command), 'update', *update_args], cwd=repo)
            assert json.loads(client_run([str(command), '--version']).stdout)['source_revision'] == current['source_revision']
            retained()
            client_run([str(command), 'run', '--port', str(port)], cwd=repo); state(port); unchanged()
            assert inspect(name, '{{.Image}}') == runner_snapshot[name][1]
            assert inspect(names[1], '{{.Id}}') == runner_snapshot[names[1]][0] and metadata[1].read_bytes() == second_metadata
            for selected_repo, selected_name in zip(repositories, names):
                client_run([str(command), 'runner', 'stop'], cwd=selected_repo)
                assert inspect(selected_name, '{{.State.Running}}') == 'false'
            unchanged()
            result['passed'] = True
            record('actual old90 to strict schema1 bridge to schema2 manager, rollback to bridge, checksum refusal, retry and restart preserve both unlabeled legacy projects and all canaries' if legacy_chain else 'two actual manager sources with exact unchanged runners: activation, rollback, checksum refusal, retry and restart preserve both projects and all canaries; legacy three-source chain not requested')
            return
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
            for reference in distribution_aliases:
                try:
                    remove_local_image(run, reference)
                    removed.append(reference)
                except Exception as error:
                    cleanup_errors.append(str(error))
            for reference, token in negative_images.items():
                try:
                    cleanup_negative_image(run, reference, token)
                    removed.append(reference)
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
