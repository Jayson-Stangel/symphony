#!/usr/bin/env python3
"""Restore existing ARM archives into one owned local registry without builds.

Every transported config/tag is inspected before ANY load. Existing references
are refused, even if matching, because they have no acquisition receipt here.
The exact original archive is loaded unchanged; extra transported images are
recorded, not qualified. Only original selected OCI digests may be restored.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

REGISTRY_LABEL = "com.symphony.native-test-owner"


def require(condition, message):
    if not condition:raise ValueError(message)


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):value.update(block)
    return value.hexdigest()


def archive_inventory(path, provider):
    small = {}
    size = 0
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name.removeprefix("./")
            if name == "manifest.json" or re.fullmatch(r"(?:[a-f0-9]{64}\.json|blobs/sha256/[a-f0-9]{64})", name):
                if not member.isfile() or member.size > 4 * 1024 * 1024:continue
                require(name not in small, "duplicate archive metadata")
                size += member.size; require(size <= 64 * 1024 * 1024, "archive metadata bound exceeded")
                small[name] = archive.extractfile(member).read()
    manifest = json.loads(small["manifest.json"])
    require(isinstance(manifest, list) and 0 < len(manifest) <= 8, "invalid transported image manifest")
    output = []
    tags = set()
    for item in manifest:
        raw = small[item["Config"]]
        config_sha = hashlib.sha256(raw).hexdigest()
        require(item["Config"] in (config_sha + ".json", "blobs/sha256/" + config_sha), "transported config SHA differs")
        value = json.loads(raw); labels = value.get("config", {}).get("Labels", {})
        require(value.get("architecture") == "arm64" and value.get("os") == "linux", "foreign transported image platform")
        require(labels.get("com.symphony.agent") == provider and labels.get("com.symphony.architecture") == "arm64", "foreign transported provider/CPU")
        require(re.fullmatch("[a-f0-9]{40}", labels.get("org.opencontainers.image.revision", "")), "missing transported source")
        image_tags = item.get("RepoTags")
        require(isinstance(image_tags, list) and 0 < len(image_tags) <= 4, "untagged or excessive transported image")
        for tag in image_tags:
            require(tag in (f"localhost:5000/symphony-{provider}:candidate", f"localhost:5000/symphony-{provider}:previous"), "foreign transported tag")
            require(tag not in tags, "duplicate transported tag");tags.add(tag)
        output.append({"config": "sha256:" + config_sha, "tags": image_tags, "labels": labels,
                       "transport_only": True})
    return output


def absent(receipt):
    return receipt.returncode == 1 and receipt.stdout.strip() in ("", "[]") and "no such image" in receipt.stderr.lower()


def preflight(inventory, docker):
    # This entire loop completes for every archive before caller may load one.
    for item in inventory:
        for reference in [item["config"], *item["tags"]]:
            receipt = docker("image", "inspect", reference, expected=None)
            require(absent(receipt), "existing or uncertain image reference; load refused: " + reference)


def cleanup(state, docker):
    allowed = dict(state.get("owned_tags", {}))
    for tag, identifier in allowed.items():
        receipt = docker("image", "inspect", tag, expected=None)
        if absent(receipt):continue
        require(receipt.returncode == 0, "image ownership inspection uncertain")
        require(json.loads(receipt.stdout)[0]["Id"] == identifier, "foreign replacement tag preserved: " + tag)
        docker("image", "rm", tag)
    for identifier in state.get("owned_configs", []):
        receipt = docker("image", "inspect", identifier, expected=None)
        if absent(receipt):continue
        require(receipt.returncode == 0, "config ownership inspection uncertain")
        value = json.loads(receipt.stdout)[0]
        require(value["Id"] == identifier, "foreign config identity")
        # Do not remove a user reference or a config now in use elsewhere.
        require(not value.get("RepoTags") and all(ref in allowed for ref in value.get("RepoDigests", [])),
                "new foreign image references preserved; cleanup refused")
        docker("image", "rm", identifier)
    for reference in [*allowed, *state.get("owned_configs", [])]:
        require(absent(docker("image", "inspect", reference, expected=None)),
                "owned reference remains or final absence uncertain: " + reference)
    state["cleanup"] = "removed-owned-references"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("restore", "cleanup"))
    parser.add_argument("--records", type=Path)
    parser.add_argument("--archive", action="append", default=[], help="PROVIDER=existing archive path")
    parser.add_argument("--registry-state", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--docker-host", required=True)
    parser.add_argument("--docker-config", type=Path, required=True)
    args = parser.parse_args()
    require((args.docker_config / "config.json").read_text().strip() == "{}", "empty isolated Docker config required")
    env = {"PATH": os.environ["PATH"], "HOME": str(args.docker_config.parent),
           "DOCKER_HOST": args.docker_host, "DOCKER_CONFIG": str(args.docker_config)}
    log_path = args.state.with_suffix(".commands.log")
    with log_path.open("a") as log:
        def docker(*values, expected=0, stdin=None):
            # No build/save/export/login/public-registry operation is available.
            require(values[0] in ("image", "inspect", "load", "tag", "push", "pull"), "forbidden image command")
            if values[0] in ("push", "pull"):
                require(re.fullmatch(r"localhost:[0-9]+/symphony-(codex|claude)(?::candidate|@sha256:[a-f0-9]{64})", values[1]), "only selected local registry route allowed")
            command = ["docker", *values]
            receipt = subprocess.run(command, stdin=stdin, env=env, text=True, capture_output=True, timeout=900)
            log.write("$ " + " ".join(command) + "\n" + receipt.stdout + receipt.stderr + "\n");log.flush()
            if expected is not None:require(receipt.returncode == expected, "Docker command failed: " + " ".join(values))
            return receipt
        if args.operation == "cleanup":
            state = json.loads(args.state.read_text())
            cleanup(state, docker); args.state.write_text(json.dumps(state, indent=2) + "\n"); return
        require(not args.state.exists(), "refusing existing restore state")
        records_value = json.loads(args.records.read_text())
        records = {r["provider"]:r for r in records_value["records"] if r["architecture"] == "arm64"}
        archives = dict(value.split("=", 1) for value in args.archive)
        require(set(archives) == set(records) == {"codex", "claude"}, "exact two original ARM providers required")
        registry = json.loads(args.registry_state.read_text())
        observed = json.loads(docker("inspect", registry["container_id"]).stdout)[0]
        require(observed["Id"] == registry["container_id"] and observed["Name"] == "/" + registry["name"] and
                observed["Config"]["Labels"].get(REGISTRY_LABEL) == registry["owner"] and observed["State"]["Running"], "owned registry receipt differs")
        port = registry["host_port"]
        require(type(port) is int and 0 < port <= 65535, "invalid owned registry port")
        bindings = observed["HostConfig"]["PortBindings"]["5000/tcp"]
        require(bindings == [{"HostIp": "127.0.0.1", "HostPort": str(port)}], "registry is not the selected loopback listener")
        inventories = {}
        for provider, filename in archives.items():
            path = Path(filename)
            require(sha256(path) == records[provider]["qualification"]["archive_sha256"], "original archive checksum differs")
            inventories[provider] = archive_inventory(path, provider)
            selected = [item for item in inventories[provider] if item["config"] == records[provider]["image_config"]]
            require(len(selected) == 1 and records[provider]["archive_tag"] in selected[0]["tags"], "original selected config/tag absent")
            labels = selected[0]["labels"]
            require(labels["org.opencontainers.image.revision"] == records[provider]["source_revision"] and
                    labels.get("com.symphony.inputs.sha256") == records[provider]["input_identity"] and
                    labels.get("com.symphony.protocol") == str(records[provider]["protocol"]) == "1", "original selected labels differ")
        inventory = [item for group in inventories.values() for item in group]
        aliases = {provider:(f"localhost:{port}/symphony-{provider}:candidate",
                            f"localhost:{port}/symphony-{provider}@" + record["image"].split("@", 1)[1])
                   for provider, record in records.items()}
        preflight(inventory, docker)
        for references in aliases.values():
            for reference in references:
                require(absent(docker("image", "inspect", reference, expected=None)), "preexisting distribution alias preserved")
        state = {"archives": archives, "inventory": inventories, "preexisting_references": [],
                 "planned_configs": sorted({item["config"] for item in inventory}),
                 "owned_tags": {}, "owned_configs": [],
                 "distribution": {}, "cleanup": "pending", "restored": False,
                 "scope": "original archive restore only; extra transported configs not qualified"}
        args.state.write_text(json.dumps(state, indent=2) + "\n")
        try:
            # Recheck every tag immediately before first mutation as well.
            preflight(inventory, docker)
            for references in aliases.values():
                for reference in references:
                    require(absent(docker("image", "inspect", reference, expected=None)), "preexisting distribution alias preserved")
            for provider, filename in archives.items():
                # A previous provider load may take minutes. Recheck this
                # provider immediately before granting any acquisition intent.
                preflight(inventories[provider], docker)
                for reference in aliases[provider]:
                    require(absent(docker("image", "inspect", reference, expected=None)), "preexisting distribution alias preserved")
                for item in inventories[provider]:
                    state["owned_configs"] = sorted(set(state["owned_configs"]) | {item["config"]})
                    state["owned_tags"].update({tag:item["config"] for tag in item["tags"]})
                args.state.write_text(json.dumps(state, indent=2) + "\n")
                with Path(filename).open("rb") as stream:docker("load", stdin=stream)
                for item in inventories[provider]:
                    for tag in item["tags"]:
                        value = json.loads(docker("image", "inspect", tag).stdout)[0]
                        require(value["Id"] == item["config"], "loaded transported tag/config differs")
            for provider, record in records.items():
                alias_tag, alias = aliases[provider]
                for reference in (alias_tag, alias):
                    require(absent(docker("image", "inspect", reference, expected=None)), "preexisting distribution alias preserved")
                state["owned_tags"][alias_tag] = record["image_config"]
                state["owned_tags"][alias] = record["image_config"]
                args.state.write_text(json.dumps(state, indent=2) + "\n")
                docker("tag", record["archive_tag"], alias_tag)
                docker("push", alias_tag)
                value = json.loads(docker("image", "inspect", alias_tag).stdout)[0]
                require(alias in value.get("RepoDigests", []) and value["Id"] == record["image_config"], "local push changed original OCI digest/config")
                docker("pull", alias)
                require(json.loads(docker("image", "inspect", alias).stdout)[0]["Id"] == record["image_config"], "original digest pull changed config")
                state["distribution"][provider] = {"arm64": alias}
            state["restored"] = True
        except BaseException:
            cleanup(state, docker)
            raise
        finally:
            args.state.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":main()
