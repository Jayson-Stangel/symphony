#!/usr/bin/env python3
"""Local Mac ARM manager-only lifecycle using existing original images.

Requires real frozen B/C packages and an already owned loopback registry.
Never builds, saves, loads, publishes, qualifies images, or requests a model.
Only native account login/status calls are fixture stubs. All container,
listener, filesystem, update and rollback operations are actual product calls.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

spec = importlib.util.spec_from_file_location("stock_fixture", Path(__file__).with_name("test-zero-rebuild-stock.py"))
stock = importlib.util.module_from_spec(spec); spec.loader.exec_module(stock)
LABEL = "com.symphony.zero-rebuild-owner"
APPROVED_DOCKER_HOST = "unix:///Users/jayson/.docker/run/docker.sock"


def require_local_daemon(value):
    if value != APPROVED_DOCKER_HOST:
        raise ValueError("only the explicitly approved local Docker socket is authorized")


def volume_absent(receipt):
    return receipt.returncode == 1 and "no such volume" in receipt.stderr.lower()


def acquire_owned_volume(docker, name, token, pending, storage=None):
    receipt = docker("volume", "inspect", name, expected=None)
    assert volume_absent(receipt), "preexisting or uncertain volume preserved"
    pending.add(name)  # Before a create can complete with a lost receipt.
    labels = ["--label", LABEL + "=" + token]
    if storage is not None: labels += ["--label", "com.symphony.storage=" + storage]
    docker("volume", "create", *labels, name)
    value = json.loads(docker("volume", "inspect", name).stdout)[0]
    assert value["Name"] == name and value["Labels"][LABEL] == token
    if storage is not None: assert value["Labels"]["com.symphony.storage"] == storage


def cleanup_owned_volume(docker, name, token, storage=None):
    receipt = docker("volume", "inspect", name, expected=None)
    if volume_absent(receipt):return
    assert receipt.returncode == 0, "volume inspection uncertain"
    value = json.loads(receipt.stdout)[0]
    assert value["Name"] == name and value["Labels"][LABEL] == token, "foreign volume preserved"
    if storage is not None: assert value["Labels"].get("com.symphony.storage") == storage, "foreign storage preserved"
    docker("volume", "rm", name)
    assert volume_absent(docker("volume", "inspect", name, expected=None)), "owned volume absence uncertain"


def assert_container_image(value, qualified_uri, validated_id):
    """The launch argument may be an immutable URI or its prevalidated runtime ID."""
    assert value["Image"] == validated_id, "container resolved to a different image"
    assert value["Config"]["Image"] in (qualified_uri, validated_id), "foreign image launch reference"
    assert value["Config"]["Labels"]["com.symphony.image"] == validated_id, "container image label differs"


def assert_owned_listener(value, name, provider, uri, validated_id, captured_id=None):
    assert value["Name"] == "/" + name, "foreign container name"
    labels = value["Config"]["Labels"]
    assert labels["com.symphony.storage"] == name, "foreign storage"
    assert labels["com.symphony.mode"] == "listener", "foreign mode"
    assert labels["com.symphony.agent"] == provider, "foreign provider"
    assert captured_id is None or value["Id"] == captured_id, "foreign replacement"
    assert_container_image(value, uri, validated_id)


def seed_project_transport(config_home, identifier):
    """Seed only a fresh product-init file; this value is not a credential."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", identifier): raise ValueError("invalid owned project id")
    path = Path(config_home) / "projects" / (identifier + ".env")
    if path.is_symlink() or not path.is_file(): raise ValueError("expected owned initialized project env")
    text = path.read_text()
    assignments = [line for line in text.splitlines() if line and not line.startswith("#")]
    if assignments != ["LINEAR_API_KEY=", "GH_TOKEN="]:
        raise ValueError("preserve nonempty or unexpected project credentials")
    path.write_text(text.replace("GH_TOKEN=\n", "GH_TOKEN=credential-free-local-memory-placeholder\n"))
    path.chmod(0o600)
    return path


def auth_fixture(arguments, policy, cwd):
    """Exact product auth argv only; no account code or provider requests run."""
    scoped = policy["projects"].get(str(Path(cwd).resolve()))
    auth_like = any("auth.mjs" in x for x in arguments) or "codex" in arguments or "login" in arguments
    if not auth_like: return None
    if scoped is None: raise ValueError("auth outside owned fixture project")
    storage = scoped["storage"]
    profile = scoped["profile"]
    if not isinstance(profile, str) or not re.fullmatch(r"[a-f0-9]{64}", profile): raise ValueError("malformed fixture profile")
    for image in policy["images"]["codex"]:
        for action, flags in ((["login", "status"], []), (["login", "--device-auth"], ["--interactive", "--tty"])):
            expected = ["run", "--rm", *flags, "--volume", policy["codex_volume"] + ":/root/.codex", "--entrypoint", "codex", image, *action]
            if arguments == expected: return "FIXTURE credential-free Codex login/status only\n"
    for image in policy["images"]["claude"]:
        for action, flags in (("login", ["--interactive", "--tty"]), ("check", [])):
            expected = ["run", "--rm", *flags, "--name", storage + "-claude-auth",
                "--label", "com.symphony.storage=" + storage, "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m",
                "--volume", storage + "-claude-auth:/home/claude", "--entrypoint", "/usr/local/bin/node",
                image, "/opt/symphony/claude/auth.mjs", action]
            if arguments == expected:
                return json.dumps({"ready": True, "profile": profile}) + "\n" if action == "check" else "FIXTURE credential-free Claude login only\n"
    raise ValueError("foreign or malformed auth invocation refused")


def docker_peer(policy_path):
    policy = json.loads(Path(policy_path).read_text())
    arguments = sys.argv[1:]
    with (Path(policy["root"]) / "docker-calls.log").open("a") as log:
        log.write(json.dumps(arguments) + "\n")
    if not arguments or arguments[0] in ("build", "buildx", "save", "load", "commit", "login", "push"):
        raise ValueError("forbidden image operation")
    output = auth_fixture(arguments, policy, os.getcwd())
    if output is not None:
        print(output, end=""); return
    os.execv(policy["docker"], [policy["docker"], *arguments])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before-manifest", "before-bundle", "after-manifest", "after-bundle", "registry-state", "evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--docker-host", required=True)
    args = parser.parse_args()
    require_local_daemon(args.docker_host)
    assert os.uname().sysname == "Darwin" and os.uname().machine == "arm64", "actual native Mac ARM required"
    root = args.evidence.resolve(); root.mkdir(mode=0o700)
    token = uuid.uuid4().hex
    namespace = "reuse-local-" + token[:16]
    prefix = "symphony-" + namespace
    docker = shutil.which("docker"); assert docker
    home = root / "home"; home.mkdir(mode=0o700)
    docker_config = root / "docker-config"; docker_config.mkdir(mode=0o700)
    (docker_config / "config.json").write_text("{}\n")
    # Deliberately do not inherit host accounts, tokens, config, project paths,
    # Docker contexts, Git credential helpers, or SYMPHONY_* overrides.
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C",
           "DOCKER_HOST": args.docker_host, "DOCKER_CONFIG": str(docker_config),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    log = (root / "commands.log").open("w")
    result = {"passed": False, "scope": "native Mac ARM B-to-C manager-only; stock handoff tested separately",
              "owner": token, "image_build_export_requalification": False, "checks": [], "cleanup": []}
    containers, volumes, planned_containers = {}, set(), {}
    volume_storage = {}
    completed = False

    def run(arguments, expected=0, cwd=None, timeout=120, product=False, input_text=None):
        selected_env = dict(env, **product_env) if product else env
        receipt = subprocess.run([str(v) for v in arguments], cwd=cwd, env=selected_env,
            text=True, capture_output=True, timeout=timeout, input=input_text)
        log.write("$ " + " ".join(map(str, arguments)) + "\n" + receipt.stdout + receipt.stderr + "\n"); log.flush()
        if expected is not None:
            assert receipt.returncode == expected, (arguments, receipt.returncode, receipt.stdout, receipt.stderr)
        return receipt

    def d(*arguments, **kwargs):
        return run([docker, *arguments], **kwargs)

    def inspect(name):
        return json.loads(d("inspect", name).stdout)[0]

    def check(name):
        result["checks"].append(name)
        (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    def absent(kind, name):
        receipt = d(kind, "inspect", name, expected=None)
        assert receipt.returncode == 1 and "no such" in receipt.stderr.lower(), (name, receipt.stdout, receipt.stderr)

    def create_volume(name, storage=None):
        volume_storage[name] = storage
        acquire_owned_volume(d, name, token, volumes, storage)

    def state(port):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%s/api/v1/state" % port, timeout=2) as response:
                    value = json.load(response)
                if value.get("counts") == {"running": 0, "retrying": 0, "blocked": 0}:
                    (root / ("state-%s.json" % port)).write_text(json.dumps(value, indent=2) + "\n")
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.2)
        raise AssertionError("real memory listener did not become ready")

    def free_port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0)); return listener.getsockname()[1]

    def product(*arguments, cwd=None, expected=0):
        return run([command, *arguments], product=True, cwd=cwd, expected=expected)

    def identity():
        return json.loads(product("--version").stdout)["source_revision"]

    def utility(image, *arguments):
        return d("run", "--rm", "--label", LABEL + "=" + token, "--network", "none", "--read-only",
                 "--entrypoint", "sh", *arguments, image, "-ceu",
                 "sha256sum /proof/edited /proof/untracked /state/proof-state; git -c safe.directory=/proof -C /proof rev-parse HEAD; GIT_OPTIONAL_LOCKS=0 git -c safe.directory=/proof -C /proof status --porcelain").stdout

    try:
        observed = json.loads(d("info", "--format", "{{json .}}").stdout)
        assert observed["OSType"] == "linux" and observed["Architecture"] in ("aarch64", "arm64")
        registry = json.loads(args.registry_state.read_text())
        registered = inspect(registry["container_id"])
        assert registered["Id"] == registry["container_id"] and registered["Name"] == "/" + registry["name"]
        assert registered["Config"]["Labels"]["com.symphony.native-test-owner"] == registry["owner"]
        assert registered["State"]["Running"] is True
        assert registered["HostConfig"]["PortBindings"]["5000/tcp"] == [{"HostIp":"127.0.0.1","HostPort":str(registry["host_port"])}]
        packages = []
        for role, manifest_path, bundle_path in (("before", args.before_manifest, args.before_bundle),
                                                 ("after", args.after_manifest, args.after_bundle)):
            outer = json.loads(manifest_path.read_text())
            assert stock.digest(bundle_path) == outer["bundle"]["sha256"]
            folder = root / role; folder.mkdir(); stock.extract(bundle_path, folder)
            helper = folder / "libexec/symphony-host"
            assert run(["/usr/bin/lipo", "-archs", helper]).stdout.strip() == "arm64"
            run([helper, "validate-manager", folder / "release.json"])
            core = json.loads((folder / "manager-core.json").read_text())
            revision = run([helper, "build-revision"]).stdout.strip()
            assert revision == core["manager_source_revision"]
            packages.append((outer, core, folder, revision))
        before, after = packages
        assert before[3] != after[3]
        assert before[1]["original_components"] == after[1]["original_components"]
        assert before[1]["distribution"] == after[1]["distribution"]
        images = {}
        for provider in ("codex", "claude"):
            original = before[1]["original_components"][provider]["arm64"]
            image = before[1]["distribution"][provider]["arm64"]
            assert re.fullmatch(r"(?:localhost|127\.0\.0\.1):[0-9]+/[^@]+@sha256:[a-f0-9]{64}", image)
            assert image.startswith("localhost:%s/" % registry["host_port"])
            config = json.loads(d("image", "inspect", image).stdout)[0]
            labels = config["Config"]["Labels"]
            assert config["Architecture"] == "arm64"
            # Product validator distinguishes original config identity from a
            # manifest-store runtime Id. It still refuses transport888: only
            # the original qualified OCI digest may take the manifest branch.
            run([before[2] / "libexec/symphony-host", "validate-runtime-image", before[2] / "release.json",
                 provider, "arm64", image], input_text=json.dumps([config]))
            assert labels["org.opencontainers.image.revision"] == original["source_revision"]
            assert labels["com.symphony.inputs.sha256"] == original["input_identity"]
            assert labels["com.symphony.agent"] == provider and labels["com.symphony.protocol"] == str(original["protocol"]) == "1"
            assert image in config["RepoDigests"]
            images[provider] = image
        result["manager_sources"] = [before[3], after[3]]
        result["original_components"] = before[1]["original_components"]
        check("both actual packages and original ARM configs/digests/source/input/protocol verified")
        wrapper = root / "docker-peer"
        policy_path = root / "auth-fixture.json"
        policy = {"root": str(root), "docker": docker, "images": {}, "projects": {}, "codex_volume": prefix + "-codex-auth"}
        for provider, image in images.items():
            policy["images"][provider] = [image, inspect(image)["Id"]]
        policy_path.write_text(json.dumps(policy))
        wrapper.write_text("#!" + sys.executable + "\nimport importlib.util\ns=importlib.util.spec_from_file_location('fixture', " + repr(str(Path(__file__).resolve())) + ")\nm=importlib.util.module_from_spec(s);s.loader.exec_module(m)\nm.docker_peer(" + repr(str(policy_path)) + ")\n")
        wrapper.chmod(0o755)
        result["auth_proof"] = "exact credential-free login/check fixture; no native provider auth acceptance"
        product_env = {"SYMPHONY_HOME": "", "SYMPHONY_APP_HOME": str(root / "app"),
            "SYMPHONY_CONFIG_HOME": str(root / "config"), "SYMPHONY_BIN_DIR": str(root / "bin"),
            "SYMPHONY_SKILLS_DIR": str(root / "skills"), "SYMPHONY_RUNNER_NAMESPACE": namespace,
            "SYMPHONY_CONTAINER_RUNTIME": str(wrapper), "PROOF_ROOT": str(root), "ACTUAL_DOCKER": docker}
        run([before[2] / "install.sh"], product=True)
        command = root / "bin/symphony"
        assert identity() == before[3]
        projects = []
        create_volume(prefix + "-codex-auth")
        for provider in ("codex", "claude"):
            repo = root / provider / "service"; repo.mkdir(parents=True)
            run(["/usr/bin/git", "init", "-q", repo])
            run(["/usr/bin/git", "-C", repo, "remote", "add", "origin", "https://example.invalid/" + provider + "/service.git"])
            tracked = repo / "tracked"; tracked.write_text("committed\n")
            run(["/usr/bin/git", "-C", repo, "add", "tracked"])
            run(["/usr/bin/git", "-C", repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"])
            tracked.write_text("committed\ndirty\n"); (repo / "untracked").write_text("untracked\n")
            product("init", "--linear-project", "local-memory-fixture", cwd=repo)
            (repo / "WORKFLOW.md").write_text("---\ntracker:\n  kind: memory\nworkspace:\n  root: /workspaces\npolling:\n  interval_ms: 1000\n---\nEmpty local fixture; zero inference.\n")
            identifier = run(["/bin/bash", "-euc", 'source "$1" --help >/dev/null; project_id "$2"', "fixture", command, repo], product=True).stdout.strip()
            name = run(["/bin/bash", "-euc", 'source "$1" --help >/dev/null; runner_storage_id "$2"', "fixture", command, repo], product=True).stdout.strip()
            assert name == prefix + "-" + identifier
            seed_project_transport(root / "config", identifier)
            policy["projects"][str(repo.resolve())] = {"storage": name, "profile": hashlib.sha256((token + name).encode()).hexdigest()}
            policy_path.write_text(json.dumps(policy))
            absent("container", name)
            for suffix in ("workspaces", "state", "claude-auth"):
                create_volume(name + "-" + suffix, name)
            for agent in ("codex", "claude"):
                product("auth", agent, "login", cwd=repo)
            port = free_port()
            planned_containers[name] = provider
            product("run", "--agent", provider, "--port", str(port), cwd=repo)
            config = inspect(name)
            assert_owned_listener(config, name, provider, images[provider], policy["images"][provider][1])
            containers[name] = config["Id"]
            assert_container_image(config, images[provider], policy["images"][provider][1])
            state(port)
            d("exec", name, "sh", "-ceu", "mkdir /workspaces/proof; git -C /workspaces/proof init -q; git -C /workspaces/proof config user.name Fixture; git -C /workspaces/proof config user.email fixture@example.invalid; printf committed > /workspaces/proof/edited; git -C /workspaces/proof add edited; git -C /workspaces/proof commit -qm fixture; printf dirty >> /workspaces/proof/edited; printf untracked > /workspaces/proof/untracked; printf state-canary > /var/lib/symphony/proof-state")
            projects.append((repo, name, port, provider))
        canaries = {p: stock.digest(p) for repo, _, _, _ in projects for p in (repo / "tracked", repo / "untracked", repo / "WORKFLOW.md")}
        canaries.update({p: stock.digest(p) for p in (root / "config/projects").glob("*.env")})
        assert len(list((root / "config/projects").glob("*.env"))) == 2
        result["transport_auth"] = "owned project env noncredential placeholder; empty Memory tracker, zero issues; no host credentials"
        metadata = {p: p.read_bytes() for p in (root / "config/runners").glob("*.env")}
        assert len(metadata) == 2
        auth_volumes = sorted(name for name in volumes if name.endswith("-auth"))
        for volume in auth_volumes:
            d("run", "--rm", "--label", LABEL + "=" + token, "--network", "none", "--read-only", "--entrypoint", "sh",
              "--volume", volume + ":/proof", images["codex"], "-ceu", "printf synthetic-noncredential-canary > /proof/reuse-canary")
        def auth_snapshot():
            return {volume:d("run", "--rm", "--label", LABEL + "=" + token, "--network", "none", "--read-only", "--entrypoint", "sh",
                "--volume", volume + ":/proof:ro", images["codex"], "-ceu", "sha256sum /proof/reuse-canary").stdout for volume in auth_volumes}
        auth_before = auth_snapshot()
        def snapshot():
            output = {}
            for _, name, _, provider in projects:
                config = inspect(name)
                assert_container_image(config, images[provider], policy["images"][provider][1])
                output[name] = {"id": config["Id"], "image": config["Image"], "running": config["State"]["Running"],
                    "mounts": sorted(config["Mounts"], key=lambda value: value["Destination"]),
                    "data": utility(images[provider], "--volume", name + "-workspaces:/proof:ro", "--volume", name + "-state:/state:ro")}
            return output
        product("runner", "stop", cwd=projects[0][0])
        baseline = snapshot()
        assert baseline[projects[0][1]]["running"] is False and baseline[projects[1][1]]["running"] is True
        def unchanged():
            assert snapshot() == baseline
            assert {p: stock.digest(p) for p in canaries} == canaries
            assert {p: p.read_bytes() for p in metadata} == metadata
            assert auth_snapshot() == auth_before
            assert not list((root / "config/runners").glob("*.transition*"))
        check("both real provider listeners, owned persistent volumes and dirty Git/state canaries ready")
        selection = ("--candidate-manifest", args.after_manifest.resolve(), "--candidate-bundle", args.after_bundle.resolve())
        product("update", *selection, cwd=projects[0][0]); assert identity() == after[3]; unchanged()
        product("update", "--rollback-release", cwd=projects[0][0]); assert identity() == before[3]; unchanged()
        invalid = json.loads(args.after_manifest.read_text()); invalid["bundle"]["sha256"] = "0" * 64
        bad = root / "corrupt-release.json"; bad.write_text(json.dumps(invalid))
        receipt = product("update", "--candidate-manifest", bad, "--candidate-bundle", args.after_bundle.resolve(), cwd=projects[0][0], expected=None)
        assert receipt.returncode != 0
        assert identity() == before[3]; unchanged()
        product("update", *selection, cwd=projects[0][0]); assert identity() == after[3]; unchanged()
        check("actual B-to-C activation, rollback, checksum refusal and retry retain both original runners/states/canaries")
        for repo, name, port, provider in projects:
            product("runner", "stop", cwd=repo)
            stopped = inspect(name)
            assert stopped["State"]["Running"] is False
            assert_owned_listener(stopped, name, provider, images[provider], policy["images"][provider][1], containers[name])
            # Product deliberately recreates a stopped listener. Retain the
            # planned exact name/storage/image ownership if acquisition loses
            # its receipt; capture the replacement ID immediately on success.
            del containers[name]
            product("run", "--agent", provider, "--port", str(port), cwd=repo)
            restarted = inspect(name)
            assert_owned_listener(restarted, name, provider, images[provider], policy["images"][provider][1])
            containers[name] = restarted["Id"]
            state(port)
            current = snapshot()[name]
            assert current["image"] == baseline[name]["image"]
            assert current["mounts"] == baseline[name]["mounts"]
            assert current["data"] == baseline[name]["data"]
            assert {p: stock.digest(p) for p in canaries} == canaries
            assert auth_snapshot() == auth_before
            product("runner", "stop", cwd=repo)
        completed = True
    finally:
        # Remove only exact captured product IDs, plus our labelled temporary
        # utilities if a command timed out before --rm completed.
        try:
            for name in planned_containers:
                receipt = d("container", "inspect", name, expected=None)
                if receipt.returncode:
                    assert receipt.returncode == 1 and "no such container" in receipt.stderr.lower()
                    continue
                value = json.loads(receipt.stdout)[0]
                provider = planned_containers[name]
                assert_owned_listener(value, name, provider, images[provider], policy["images"][provider][1], containers.get(name))
                d("rm", "-f", value["Id"]); absent("container", value["Id"])
                result["cleanup"].append(value["Id"])
            for identifier in d("ps", "-aq", "--filter", "label=" + LABEL + "=" + token).stdout.split():
                value = inspect(identifier); assert value["Config"]["Labels"][LABEL] == token
                d("rm", "-f", identifier); absent("container", identifier); result["cleanup"].append(identifier)
            for name in volumes:
                cleanup_owned_volume(d, name, token, volume_storage[name]); result["cleanup"].append(name)
            result["passed"] = completed
        finally:
            (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            log.close()


if __name__ == "__main__":
    main()
