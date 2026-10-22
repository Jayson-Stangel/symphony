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
    schema = value.get("schema_version")
    require(type(schema) is int and schema in (1, 2), "unsupported release manifest schema")
    if schema == 2:
        require(value.get("compatibility") == {"runner_protocol": 1} and
                type(value["compatibility"].get("runner_protocol")) is int,
                "unsupported manager/runner compatibility")
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
            if schema == 1:
                require(runner.get("source_revision") == value["source_revision"], "runner revision differs")
            else:
                require(REVISION.fullmatch(runner.get("source_revision", "")), "invalid runner source revision")
                validate_component(runner, provider, architecture)
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
    if "bundles" in value:
        bundles = value["bundles"]
        require(isinstance(bundles, dict) and set(bundles) == {"macos", "linux_x86_64"}, "invalid platform bundles")
        require(bundles["macos"] == bundle, "legacy macOS bundle differs")
        linux = bundles["linux_x86_64"]
        require(isinstance(linux, dict), "Linux bundle must be an object")
        require(linux.get("filename") == f"symphony-{value['version']}-linux_x86_64.tar.gz", "unexpected Linux bundle filename")
        if require_bundle:
            require(re.fullmatch(r"[0-9a-f]{64}", linux.get("sha256", "")), "missing Linux bundle checksum")
    if "package_platform" in value:
        require(value["package_platform"] in ("macos", "linux_x86_64"), "invalid package platform")
        require(value["package_platform"] != "linux_x86_64" or "bundles" in value, "Linux package has no platform bundles")
    return value


def validate_component(runner, provider, architecture):
    require(provider in PROVIDERS and architecture in ARCHITECTURES, "unknown native component target")
    require(runner.get("platform") == ARCHITECTURES[architecture], "native component platform differs")
    require(REVISION.fullmatch(runner.get("source_revision", "")), "invalid original runner revision")
    require(DIGEST_IMAGE.fullmatch(runner.get("image", "")), "invalid original immutable image")
    if "browser_input_identity" in runner:
        require(re.fullmatch(r"[a-f0-9]{64}", runner["browser_input_identity"]), "invalid original browser identity")
    require(re.fullmatch(r"[a-f0-9]{64}", runner.get("input_identity", "")), "missing runner input identity")
    require(type(runner.get("protocol")) is int and runner["protocol"] == 1, "incompatible runner protocol")
    require(isinstance(runner.get("tool_versions", {}).get("elixir"), str) and
            bool(runner["tool_versions"]["elixir"].strip()), "missing original engine/toolchain version")
    dependencies = runner.get("dependencies")
    require(isinstance(dependencies, dict) and dependencies.get("resolution_policy") == "retain-original-qualified-resolution",
            "missing original dependency resolution policy")
    bases = dependencies.get("base_images")
    require(isinstance(bases, list) and bool(bases) and all(isinstance(base, str) and
            re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[a-f0-9]{64}", base) for base in bases),
            "missing pinned original base images")
    packages = dependencies.get("os_packages")
    require(isinstance(packages, dict) and bool(packages) and all(isinstance(name, str) and name and
            isinstance(version, str) and version for name, version in packages.items()),
            "missing original resolved OS packages")
    options = dependencies.get("build_options")
    require(isinstance(options, dict) and all(isinstance(key, str) and isinstance(value, str) for key, value in options.items())
            and options.get("TARGETARCH") == ARCHITECTURES[architecture].split("/")[1],
            "missing or incompatible original effective build options")
    proof = runner.get("qualification")
    require(isinstance(proof, dict), "missing original native qualification")
    require(proof.get("repository") == "Jayson-Stangel/symphony", "foreign native qualification")
    for key in ("run_id", "run_attempt", "artifact_id"):
        require(type(proof.get(key)) is int and proof[key] > 0, "invalid native qualification " + key)
    require(REVISION.fullmatch(proof.get("controller_revision", "")), "missing qualification controller revision")
    for key in ("source_revision", "input_identity", "image"):
        require(proof.get(key) == runner.get(key), "native qualification identity differs: " + key)
    require(proof.get("job_name") in (f"runner-{provider}-{architecture}", f"runners / runner-{provider}-{architecture}"), "native qualification target differs")
    require(proof.get("artifact_name") == f"runner-candidate-{provider}-{architecture}", "native artifact target differs")
    require(re.fullmatch(r"sha256:[a-f0-9]{64}", proof.get("artifact_digest", "")), "missing immutable artifact digest")
    require(proof.get("archive_filename") in (f"{provider}-{architecture}.docker.tar.gz",
                                              f"previous/{provider}-{architecture}.docker.tar.gz"),
            "native archive target differs")
    require(proof.get("record_filename", f"{provider}-{architecture}.json") in
            (f"{provider}-{architecture}.json", f"previous/{provider}-{architecture}.json"),
            "native record target differs")
    require(re.fullmatch(r"[a-f0-9]{64}", proof.get("archive_sha256", "")), "missing native archive checksum")
    if "record_origin" in proof:
        origin = proof["record_origin"]
        require(isinstance(origin, dict) and set(origin) == {"run_id", "run_attempt", "controller_revision", "job_name"}, "invalid small-record origin")
        require(all(type(origin.get(key)) is int and origin[key] > 0 for key in ("run_id", "run_attempt")), "invalid small-record run identity")
        require(REVISION.fullmatch(origin.get("controller_revision", "")), "invalid small-record controller")
        require(origin.get("job_name") in (f"recover-{provider}-{architecture}", f"native-record-recovery / recover-{provider}-{architecture}",
                                          f"runner-{provider}-{architecture}", f"runners / runner-{provider}-{architecture}"), "unknown small-record native job")
    if "payload_origin" in proof:
        origin = proof["payload_origin"]
        require(isinstance(origin, dict) and set(origin) == {"run_id", "controller_revision"} and
                type(origin.get("run_id")) is int and origin["run_id"] == proof["run_id"] and
                origin.get("controller_revision") == proof["controller_revision"], "invalid original payload origin")
    if "native_step_recovery" in proof:
        recovery = proof["native_step_recovery"]
        expected = {"job_id", "original_job_conclusion", "build_step_number", "qualification_step_number", "payload_step_number",
                    "failure_step_number", "cleanup_step_number", "failure_reason", "log_sha256"}
        require(isinstance(recovery, dict) and set(recovery) == expected and "record_origin" in proof and "payload_origin" in proof,
                "missing independent recovery origins")
        require(all(type(recovery.get(key)) is int and recovery[key] > 0 for key in
                    ("job_id", "build_step_number", "qualification_step_number", "payload_step_number", "failure_step_number", "cleanup_step_number")),
                "invalid successful-stage recovery identity")
        require(recovery.get("original_job_conclusion") == "failure" and recovery.get("failure_reason") == "bare-upload-artifact-sha256" and
                re.fullmatch(r"[a-f0-9]{64}", recovery.get("log_sha256", "")), "unknown native recovery failure")



def assemble(args, verifier=None):
    runners = {provider: {} for provider in PROVIDERS}
    for filename in args.records:
        record = json.loads(Path(filename).read_text())
        if getattr(args, "schema_version", 1) == 2:
            from component_provenance import verify_origin
            (verifier or verify_origin)(record)
        provider, architecture = record.pop("provider"), record.pop("architecture")
        require(provider in runners and architecture in ARCHITECTURES, "unsupported runner record")
        require(architecture not in runners[provider], "duplicate runner record")
        runners[provider][architecture] = record
    result = {
        "schema_version": getattr(args, "schema_version", 1),
        "version": args.version,
        "source_revision": args.revision,
        "channel": args.channel,
        "host_architectures": list(ARCHITECTURES),
        "runners": runners,
        "bundle": {"filename": f"symphony-{args.version}-macos.tar.gz"},
    }
    if result["schema_version"] == 2:
        result["compatibility"] = {"runner_protocol": 1}

    if getattr(args, "platform_bundles", False) or getattr(args, "linux_bundle", None):
        result["bundles"] = {"macos": result["bundle"],
                             "linux_x86_64": {"filename": f"symphony-{args.version}-linux_x86_64.tar.gz"}}
    if getattr(args, "linux_bundle", None):
        linux = Path(args.linux_bundle)
        require(linux.name == result["bundles"]["linux_x86_64"]["filename"], "Linux bundle filename differs")
        result["bundles"]["linux_x86_64"]["sha256"] = hashlib.sha256(linux.read_bytes()).hexdigest()
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
    build.add_argument("--schema-version", choices=(1, 2), type=int, default=1)
    build.add_argument("--version", required=True)
    build.add_argument("--revision", required=True)
    build.add_argument("--channel", choices=("candidate", "stable"), required=True)
    build.add_argument("--bundle")
    build.add_argument("--linux-bundle")
    build.add_argument("--platform-bundles", action="store_true", help="include Linux x86-64 and legacy macOS bundle slots")
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
