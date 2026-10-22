#!/usr/bin/env python3
"""Build-time release identity; never owns installed runner or transition state."""
import argparse
import hashlib
import json
import re
from pathlib import Path

ARCHITECTURES = {"arm64": "linux/arm64", "x86_64": "linux/amd64"}
PROVIDERS = ("codex", "claude")
DIGEST_IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(value, require_bundle=True):
    require(isinstance(value, dict), "release manifest must be an object")
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1, "unsupported release manifest schema")
    require(VERSION.fullmatch(value.get("version", "")), "invalid release version")
    require(REVISION.fullmatch(value.get("source_revision", "")), "invalid source revision")
    require(value.get("channel") in ("candidate", "stable"), "invalid release channel")
    require(value.get("host_architectures") == list(ARCHITECTURES), "unsupported host architectures")
    runners = value.get("runners", {})
    require(isinstance(runners, dict), "runners must be an object")
    require(set(runners) == set(PROVIDERS), "both runner providers are required")
    for provider in PROVIDERS:
        require(isinstance(runners[provider], dict), "provider runners must be an object")
        require(set(runners[provider]) == set(ARCHITECTURES), "both runner architectures are required")
        for architecture, platform in ARCHITECTURES.items():
            runner = runners[provider][architecture]
            require(isinstance(runner, dict), "runner must be an object")
            require(runner.get("platform") == platform, "runner platform differs")
            require(runner.get("source_revision") == value["source_revision"], "runner revision differs")
            require(DIGEST_IMAGE.fullmatch(runner.get("image", "")), "runner image must use an immutable digest")
            tools = runner.get("tool_versions", {})
            require(isinstance(tools, dict), "tool versions must be an object")
            for tool in (provider, "git", "node", "playwright"):
                require(isinstance(tools.get(tool), str) and bool(tools[tool].strip()), f"missing {tool} version")
    bundle = value.get("bundle", {})
    require(isinstance(bundle, dict), "bundle must be an object")
    require(bundle.get("filename") == f"symphony-{value['version']}-macos.tar.gz", "unexpected bundle filename")
    if require_bundle:
        require(re.fullmatch(r"[0-9a-f]{64}", bundle.get("sha256", "")), "missing bundle checksum")
    return value


def assemble(args):
    runners = {provider: {} for provider in PROVIDERS}
    for filename in args.records:
        record = json.loads(Path(filename).read_text())
        provider, architecture = record.pop("provider"), record.pop("architecture")
        require(provider in runners and architecture in ARCHITECTURES, "unsupported runner record")
        require(architecture not in runners[provider], "duplicate runner record")
        runners[provider][architecture] = record
    result = {
        "schema_version": 1,
        "version": args.version,
        "source_revision": args.revision,
        "channel": args.channel,
        "host_architectures": list(ARCHITECTURES),
        "runners": runners,
        "bundle": {"filename": f"symphony-{args.version}-macos.tar.gz"},
    }
    if args.bundle:
        require(Path(args.bundle).name == result["bundle"]["filename"], "bundle filename differs")
        result["bundle"]["sha256"] = hashlib.sha256(Path(args.bundle).read_bytes()).hexdigest()
    validate(result, require_bundle=bool(args.bundle))
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate")
    check.add_argument("manifest")
    check.add_argument("--internal", action="store_true", help="allow bundled metadata without its own checksum")
    build = commands.add_parser("assemble")
    build.add_argument("--version", required=True)
    build.add_argument("--revision", required=True)
    build.add_argument("--channel", choices=("candidate", "stable"), required=True)
    build.add_argument("--bundle")
    build.add_argument("--output", required=True)
    build.add_argument("records", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate(json.loads(Path(args.manifest).read_text()), require_bundle=not args.internal)
        else:
            assemble(args)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"release metadata: {error}\n")


if __name__ == "__main__":
    main()
