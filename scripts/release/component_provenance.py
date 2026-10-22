#!/usr/bin/env python3
"""Seal native records and verify their immutable GitHub artifact origins.

The small original record is downloaded and compared, not trusted from a local
receipt. The large image payload must still exist with its original digest;
actual loading/qualification belongs to the selected lifecycle consumer.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
import re
from pathlib import Path
import subprocess
import zipfile

import artifacts

REPOSITORY = "Jayson-Stangel/symphony"
MAX_RECORD_BYTES = 1024 * 1024


def api(path, binary=False):
    try:
        data = subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/{path}"], stderr=subprocess.PIPE)
    except subprocess.SubprocessError:
        raise ValueError("native origin service unavailable; reuse refused") from None
    return data if binary else json.loads(data)


def require(condition, message):
    artifacts.require(condition, message)


def available(artifact, expected_name, expected_digest, run_id, controller, now):
    require(artifact.get("name") == expected_name, "artifact name differs")
    require(artifact.get("digest") == expected_digest, "artifact digest differs")
    require(artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
            and artifact["size_in_bytes"] > 0, "artifact payload is absent or expired")
    expiry = datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
    require(expiry > now, "artifact payload has expired")
    origin = artifact.get("workflow_run", {})
    require(origin.get("id") == run_id and origin.get("head_sha") == controller, "foreign artifact origin")


def trusted_lineage(revision, fetch=api):
    """Accept only ancestors of live fork integration/production refs."""
    require(artifacts.REVISION.fullmatch(revision or ""), "invalid source/controller revision")
    for branch in ("dev", "main"):
        comparison = fetch(f"compare/{revision}...{branch}")
        if comparison.get("status") in ("ahead", "identical") and comparison.get("merge_base_commit", {}).get("sha") == revision:
            return
    raise ValueError("source/controller is outside verified fork dev/main history")


def verify_origin(record, fetch=api, now=None, candidate_run=None):
    provider, architecture = record["provider"], record["architecture"]
    artifacts.validate_component(record, provider, architecture)
    proof = record["qualification"]
    run_id, attempt = proof["run_id"], proof["run_attempt"]
    run = fetch(f"actions/runs/{run_id}")
    require(run.get("id") == run_id and run.get("head_sha") == proof["controller_revision"], "qualification run identity differs")
    require(run.get("repository", {}).get("full_name") == REPOSITORY and
            run.get("head_repository", {}).get("full_name") == REPOSITORY, "foreign qualification repository")
    require(run.get("event") in ("workflow_dispatch", "push") and
            run.get("path") in (".github/workflows/runner-release.yml", ".github/workflows/burrito-release.yml"),
            "unqualified native workflow origin")
    if candidate_run is not None:
        require(type(candidate_run) is int and candidate_run == run_id and run.get("event") == "workflow_dispatch",
                "explicit candidate origin differs from the selected manual run")
    else:
        for revision in set((proof["controller_revision"], record["source_revision"])):
            trusted_lineage(revision, fetch)
    jobs = fetch(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    require(jobs.get("total_count", 0) <= 100, "incomplete native job discovery")
    selected = [job for job in jobs.get("jobs", []) if job.get("name") == proof["job_name"]]
    require(len(selected) == 1 and selected[0].get("status") == "completed"
            and selected[0].get("conclusion") == "success", "original native qualification did not pass")
    native_label = "ubuntu-24.04-arm" if architecture == "arm64" else "ubuntu-24.04"
    require(native_label in selected[0].get("labels", []), "original qualification was not on the selected native platform")
    now = now or datetime.now(timezone.utc)
    payload = fetch(f"actions/artifacts/{proof['artifact_id']}")
    available(payload, proof["artifact_name"], proof["artifact_digest"], run_id, proof["controller_revision"], now)
    inventory = fetch(f"actions/runs/{run_id}/artifacts?per_page=100")
    require(inventory.get("total_count", 0) <= 100, "incomplete origin artifact discovery")
    record_name = f"runner-records-{provider}-{architecture}"
    candidates = [item for item in inventory.get("artifacts", []) if item.get("name") == record_name]
    require(len(candidates) == 1, "original small qualification record is missing or ambiguous")
    metadata = candidates[0]
    require(type(metadata.get("size_in_bytes")) is int and metadata["size_in_bytes"] <= MAX_RECORD_BYTES,
            "qualification record artifact is too large")
    available(metadata, record_name, metadata.get("digest"), run_id, proof["controller_revision"], now)
    require(isinstance(metadata.get("digest"), str) and metadata["digest"].startswith("sha256:"), "missing record artifact digest")
    data = fetch(f"actions/artifacts/{metadata['id']}/zip", binary=True)
    require(len(data) <= MAX_RECORD_BYTES and "sha256:" + hashlib.sha256(data).hexdigest() == metadata["digest"],
            "original record artifact checksum differs")
    name = proof.get("record_filename", proof["archive_filename"].removesuffix(".docker.tar.gz") + ".json")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            require(sum(item.file_size for item in archive.infolist()) <= MAX_RECORD_BYTES,
                    "qualification record expands beyond its limit")
            require(archive.namelist().count(name) == 1, "original native record is missing or duplicated")
            original = json.loads(archive.read(name))
    except (zipfile.BadZipFile, RuntimeError):
        raise ValueError("corrupt or unsupported original record archive; reuse refused") from None
    require(original == record, "record differs from the original native qualification")
    return {"run_id": run_id, "artifact_id": payload["id"], "artifact_digest": payload["digest"],
            "record_artifact_id": metadata["id"], "record_artifact_digest": metadata["digest"],
            "run_attempt": attempt, "controller_revision": proof["controller_revision"],
            "job_name": proof["job_name"], "archive_filename": proof["archive_filename"],
            "archive_sha256": proof["archive_sha256"], "record_filename": name}


def artifact_digest(value):
    """upload-artifact emits bare SHA256; service metadata includes the prefix."""
    require(isinstance(value, str) and re.fullmatch(r"(?:sha256:)?[a-f0-9]{64}", value),
            "invalid immutable upload artifact digest")
    return value if value.startswith("sha256:") else "sha256:" + value


def seal(args):
    record_path = Path(args.record)
    record = json.loads(record_path.read_text())
    provider, architecture = record["provider"], record["architecture"]
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "native origin repository differs")
    checksum = hashlib.sha256()
    with open(args.archive, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"])
    job_inventory = api(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    require(job_inventory.get("total_count", 0) <= 100, "incomplete sealing job inventory")
    allowed_jobs = (f"runner-{provider}-{architecture}", f"runners / runner-{provider}-{architecture}")
    jobs = [job for job in job_inventory.get("jobs", []) if job.get("name") in allowed_jobs]
    require(len(jobs) == 1 and args.job_name in allowed_jobs, "native sealing job is missing or ambiguous")
    native_label = "ubuntu-24.04-arm" if architecture == "arm64" else "ubuntu-24.04"
    require(native_label in jobs[0].get("labels", []), "sealing job has wrong native platform")
    proof = {"repository": REPOSITORY, "run_id": int(os.environ["GITHUB_RUN_ID"]),
             "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
             "controller_revision": os.environ["GITHUB_SHA"], "job_name": jobs[0]["name"],
             "artifact_id": args.artifact_id, "artifact_name": f"runner-candidate-{provider}-{architecture}",
             "artifact_digest": artifact_digest(args.artifact_digest), "archive_filename": args.archive_name,
             "archive_sha256": checksum.hexdigest(), "record_filename": args.record_name,
             **{key: record[key] for key in ("image", "source_revision", "input_identity")}}
    record["qualification"] = proof
    artifacts.validate_component(record, provider, architecture)
    record_path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("record")
    build = commands.add_parser("seal")
    build.add_argument("--record", required=True)
    build.add_argument("--archive", required=True)
    build.add_argument("--archive-name", required=True)
    build.add_argument("--record-name", required=True)
    build.add_argument("--artifact-id", required=True, type=int)
    build.add_argument("--artifact-digest", required=True)
    build.add_argument("--job-name", required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            print(json.dumps(verify_origin(json.loads(Path(args.record).read_text())), sort_keys=True))
        else:
            seal(args)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        # Provider stderr is captured above and never exposed (URLs can carry credentials).
        detail = str(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(1, f"component provenance: {detail}; original evidence could not be verified\n")


if __name__ == "__main__":
    main()
