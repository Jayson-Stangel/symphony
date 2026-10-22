#!/usr/bin/env python3
"""Record observed tooling and OCI identity after digest pull + smoke."""
import argparse
import json
import subprocess
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    config = json.loads(docker("image", "inspect", args.image))[0]
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
              "image": args.image, "tool_versions": tools}
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
