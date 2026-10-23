#!/usr/bin/env python3
"""Record observed tooling and OCI identity after digest pull + smoke."""
import argparse
import json
import re
import subprocess
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=120).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--registry-state", help="owned qualification registry; derive true config from original manifest")
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--inputs", help="exact source input snapshot used by this build")
    parser.add_argument("--source-root", default=".")
    parser.add_argument("--archive-tag", help="actual original archive tag, including current/previous role")
    args = parser.parse_args()
    if args.archive_tag is not None and not re.fullmatch(re.escape(f"localhost:5000/symphony-{args.provider}") + r":(?:candidate|previous)", args.archive_tag):
        parser.error("archive tag differs from the original provider/current-prior namespace")
    config = json.loads(docker("image", "inspect", args.image))[0]
    if args.registry_state:
        import qualified_graph
        original, config_id, raw_config = qualified_graph.qualified_config(args.image, args.registry_state, config)
        if original != args.image:
            parser.error("record must select its original immutable qualified reference")
        labels = raw_config["config"]["Labels"]
        if raw_config.get("architecture") != config["Architecture"] or raw_config.get("os") != "linux":
            parser.error("qualified config platform differs from inspection")
    else:
        config_id = config["Id"]
        labels = config["Config"]["Labels"]
    expected = "amd64" if args.architecture == "x86_64" else "arm64"
    if (config["Architecture"] != expected or labels.get("org.opencontainers.image.revision") != args.revision
            or labels.get("com.symphony.architecture") != expected or labels.get("com.symphony.agent") != args.provider):
        parser.error("image provider/architecture/revision differs")
    tools = {}
    for tool in ("git", "node", "playwright"):
        tools[tool] = docker("run", "--rm", "--network", "none", "--entrypoint", tool, args.image, "--version")
    if args.provider == "codex":
        tools["codex"] = docker("run", "--rm", "--network", "none", "--entrypoint", "codex", args.image, "--version")
    else:
        tools["claude_sdk"] = docker("run", "--rm", "--network", "none", "--entrypoint", "/usr/local/bin/node", args.image,
                                    "-p", "require('/opt/symphony/claude/node_modules/@anthropic-ai/claude-agent-sdk/package.json').version")
        tools["claude"] = docker("run", "--rm", "--network", "none", "--entrypoint", "/usr/local/bin/node", args.image,
                                 "--input-type=module", "-e",
                                 "import {nativeExecutable} from '/opt/symphony/claude/runtime.mjs'; "
                                 "import {execFileSync} from 'node:child_process'; "
                                 "process.stdout.write(execFileSync(nativeExecutable(), ['--version'], {encoding:'utf8', timeout:15000}));")
    result = {"provider": args.provider, "architecture": args.architecture,
              "platform": f"linux/{expected}", "source_revision": args.revision,
              "image": args.image, "image_config": config_id,
              "archive_tag": args.archive_tag or f"localhost:5000/symphony-{args.provider}:candidate",
              "engine_version": labels.get("org.opencontainers.image.version"), "tool_versions": tools}
    if args.inputs:
        import runner_inputs
        snapshot = json.loads(Path(args.inputs).read_text())
        source = subprocess.check_output(["git", "-C", args.source_root, "rev-parse", "HEAD"], text=True).strip()
        if source != args.revision:
            parser.error("recorded runner source is not the selected committed checkout")
        options = {key: value for key, value in snapshot["inputs"]["build_args"].items() if key != "TARGETARCH"}
        current = runner_inputs.identity(args.source_root, args.provider, args.architecture, options)
        if current != snapshot or labels.get("com.symphony.inputs.sha256") != snapshot["input_identity"]:
            parser.error("image input identity differs from the exact source/build snapshot")
        if labels.get("com.symphony.protocol") != "1":
            parser.error("unsupported runner protocol")
        tools["elixir"] = docker("run", "--rm", "--network", "none", "--entrypoint", "elixir", args.image, "--version")
        packages = docker("run", "--rm", "--network", "none", "--entrypoint", "/usr/bin/dpkg-query", args.image,
                          "-W", "-f=${binary:Package}\t${Version}\n")
        inventory = dict(line.split("\t", 1) for line in packages.splitlines())
        if not inventory or any(not key or not value for key, value in inventory.items()):
            parser.error("original resolved OS dependency inventory is incomplete")
        result.update(input_identity=snapshot["input_identity"], protocol=1,
                      browser_input_identity=runner_inputs.project(args.source_root, "browser-tools", args.architecture, snapshot["inputs"]["build_args"])["input_identity"],
                      dependencies={"resolution_policy": "retain-original-qualified-resolution",
                                    "base_images": sorted({stage["base"] for stage in snapshot["inputs"]["recipe"].values()
                                                           if "@sha256:" in stage["base"]}),
                                    "os_packages": inventory, "build_options": snapshot["inputs"]["build_args"]})
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
