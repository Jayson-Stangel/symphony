#!/usr/bin/env python3
"""Seal native records and verify their immutable GitHub artifact origins.

The small original record is downloaded and compared, not trusted from a local
receipt. The large image payload must still exist with its original digest;
actual loading/qualification belongs to the selected lifecycle consumer.
"""
import argparse
from datetime import datetime, timezone, timedelta
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


_BINARY_FLAGS = None


def api_command(path, binary=False):
    global _BINARY_FLAGS
    if binary and _BINARY_FLAGS is None:
        help_text = subprocess.check_output(["gh", "api", "--help"], stderr=subprocess.PIPE, text=True, timeout=10)
        _BINARY_FLAGS = ["--allow-escape-sequences"] if "--allow-escape-sequences" in help_text else []
    return ["gh", "api", *(_BINARY_FLAGS or [] if binary else []), f"repos/{REPOSITORY}/{path}"]


def api(path, binary=False):
    try:
        data = subprocess.check_output(api_command(path, binary), stderr=subprocess.PIPE, timeout=60)
    except subprocess.SubprocessError:
        raise ValueError("native origin service unavailable; reuse refused") from None
    return data if binary else json.loads(data)


def require(condition, message):
    artifacts.require(condition, message)


def instant(value):
    # GitHub log timestamps can have seven fractional digits; Python3.9 accepts six.
    require(isinstance(value, str), "missing origin timestamp")
    value = re.sub(r"(\.\d{6})\d+(?=Z|[+-])", r"\1", value)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "origin timestamp has no timezone")
    return parsed


def available(artifact, expected_name, expected_digest, run_id, controller, now):
    require(artifact.get("name") == expected_name, "artifact name differs")
    require(artifact.get("digest") == expected_digest, "artifact digest differs")
    require(artifact.get("expired") is False and type(artifact.get("size_in_bytes")) is int
            and artifact["size_in_bytes"] > 0, "artifact payload is absent or expired")
    expiry = instant(artifact["expires_at"])
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


BUILD_STEP = "Build exact native candidate with reusable dependency layers"
NATIVE_STEP = "Build exact runner and test a digest pull from an isolated registry"
SEAL_STEP = "Seal original native record against immutable uploaded payload"
CLEANUP_STEP = "Remove only the qualification registry owned by this job"
SEAL_FORMAT_ERROR = "component provenance: missing immutable artifact digest;"
MAX_JOB_LOG_BYTES = 8 * 1024 * 1024


def recovery_eligibility(job, raw_log):
    """Narrowly recover one known downstream seal-format failure, never a failed smoke."""
    require(job.get("status") == "completed" and job.get("conclusion") == "failure", "original job is not the recoverable sealing failure")
    require(type(job.get("id")) is int and job["id"] > 0, "missing original native job ID")
    steps = job.get("steps", [])
    failures = [step for step in steps if step.get("conclusion") == "failure"]
    require(len(failures) == 1 and failures[0].get("name") == SEAL_STEP, "failure was not exclusively downstream sealing")
    require(not any(step.get("conclusion") in ("cancelled", "timed_out", "action_required") for step in steps), "cancelled native work cannot be recovered")
    def successful(name):
        found = [step for step in steps if step.get("name") == name]
        require(len(found) == 1 and found[0].get("status") == "completed" and found[0].get("conclusion") == "success",
                "original required native stage did not pass: " + name)
        return found[0]
    build = successful(BUILD_STEP)
    native = successful(NATIVE_STEP)
    require(build["number"] < native["number"], "native qualification did not follow its actual build")
    cleanup = successful(CLEANUP_STEP)
    failed = failures[0]
    uploads = [step for step in steps if (step.get("name", "").startswith("Run actions/upload-artifact@") or step.get("name") == "Retain runner payload") and
               native["number"] < step.get("number", 0) < failed["number"] and
               step.get("status") == "completed" and step.get("conclusion") == "success"]
    require(len(uploads) == 1 and cleanup["number"] > failed["number"], "native payload upload or cleanup ordering differs")
    require(isinstance(raw_log, bytes) and len(raw_log) <= MAX_JOB_LOG_BYTES, "original native log is unavailable or oversized")
    start = instant(failed["started_at"])
    end = instant(failed["completed_at"]) + timedelta(seconds=1)
    known_error = False
    for line in raw_log.decode("utf-8-sig").splitlines():
        timestamp, _, message = line.partition(" ")
        if SEAL_FORMAT_ERROR not in message:
            continue
        when = instant(timestamp)
        known_error = known_error or start <= when <= end
    require(known_error, "failed sealing was not the known immutable-digest format error")
    return {"job_id": job["id"], "original_job_conclusion": "failure", "build_step_number": build["number"], "qualification_step_number": native["number"],
            "payload_step_number": uploads[0]["number"], "failure_step_number": failed["number"],
            "cleanup_step_number": cleanup["number"], "failure_reason": "bare-upload-artifact-sha256",
            "log_sha256": hashlib.sha256(raw_log).hexdigest()}


def recovery_payload_binding(job, recovery, artifact):
    selected = [step for step in job.get("steps", []) if step.get("number") == recovery["payload_step_number"]]
    require(len(selected) == 1, "original payload upload stage is missing")
    upload = selected[0]
    created = instant(artifact["created_at"])
    start = instant(upload["started_at"])
    end = instant(upload["completed_at"]) + timedelta(seconds=1)
    require(start <= created <= end, "payload belongs to another native job attempt")


def run_identity(run_id, controller, fetch, current_context=None, candidate_run=None):
    run = fetch(f"actions/runs/{run_id}")
    current = current_context if current_context and run_id == current_context["run_id"] else None
    require(run.get("id") == run_id, "qualification run identity differs")
    require(run.get("repository", {}).get("full_name") == REPOSITORY, "foreign qualification repository")
    if current:
        require(controller == current["controller_revision"] and run.get("event") == current["event"],
                "current-run qualification context differs")
        require(run.get("path") in (".github/workflows/runner-release.yml", ".github/workflows/burrito-release.yml",
                                     ".github/workflows/runner-integration.yml"), "unqualified current-run workflow")
        if current["event"] != "pull_request":
            require(run.get("head_repository", {}).get("full_name") == REPOSITORY, "foreign qualification repository")
        if run.get("head_sha") != controller:
            require(current["event"] == "pull_request", "current controller differs from run source")
            commit = fetch(f"commits/{controller}")
            require(commit.get("sha") == controller and len(commit.get("parents", [])) == 2 and run.get("head_sha") in
                    [parent.get("sha") for parent in commit.get("parents", [])],
                    "current PR merge is not bound to this run head")
    else:
        require(run.get("head_sha") == controller, "qualification run identity differs")
        require(run.get("head_repository", {}).get("full_name") == REPOSITORY, "foreign qualification repository")
        workflows = (".github/workflows/runner-release.yml", ".github/workflows/burrito-release.yml")
        # Only an explicitly selected manual original can authorize integration
        # evidence. Ordinary historical discovery retains its existing policy.
        if type(candidate_run) is int and candidate_run == run_id and run.get("event") == "workflow_dispatch":
            workflows += (".github/workflows/runner-integration.yml",)
        require(run.get("event") in ("workflow_dispatch", "push") and run.get("path") in workflows,
                "unqualified native workflow origin")
    return run


def selected_job(run_id, attempt, name, architecture, fetch):
    jobs = fetch(f"actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    require(jobs.get("total_count", 0) <= 100, "incomplete native job discovery")
    selected = [job for job in jobs.get("jobs", []) if job.get("name") == name]
    require(len(selected) == 1, "original native qualification is missing or ambiguous")
    native_label = "ubuntu-24.04-arm" if architecture == "arm64" else "ubuntu-24.04"
    require(native_label in selected[0].get("labels", []), "original qualification was not on the selected native platform")
    return selected[0]


def verify_origin(record, fetch=api, now=None, candidate_run=None, current_context=None):
    provider, architecture = record["provider"], record["architecture"]
    artifacts.validate_component(record, provider, architecture)
    proof = record["qualification"]
    run_id, attempt = proof["run_id"], proof["run_attempt"]
    controller = proof["controller_revision"]
    origin = proof.get("record_origin", {})
    record_run = origin.get("run_id", run_id)
    record_attempt = origin.get("run_attempt", attempt)
    record_controller = origin.get("controller_revision", controller)
    record_job = origin.get("job_name", proof["job_name"])
    native_run = run_identity(run_id, controller, fetch, current_context, candidate_run=candidate_run)
    metadata_run = run_identity(record_run, record_controller, fetch, current_context, candidate_run=candidate_run)
    payload_origin = proof.get("payload_origin", {})
    payload_run = payload_origin.get("run_id", run_id)
    payload_controller = payload_origin.get("controller_revision", controller)
    require(payload_run == run_id and payload_controller == controller, "payload did not originate in the qualified native run")
    if current_context is not None:
        require(record_run == current_context["run_id"] and run_id == record_run and
                record_controller == current_context["controller_revision"], "current-run verifier cannot authorize a foreign run")
        if record["source_revision"] != controller:
            trusted_lineage(record["source_revision"], fetch)
    elif candidate_run is not None:
        require(type(candidate_run) is int and candidate_run == record_run and metadata_run.get("event") == "workflow_dispatch",
                "explicit candidate origin differs from the selected manual run")
        if record_controller != controller:
            ancestry = fetch(f"compare/{controller}...{record_controller}")
            require(ancestry.get("status") in ("ahead", "identical") and ancestry.get("merge_base_commit", {}).get("sha") == controller,
                    "original native controller is outside selected recovery lineage")
        if record["source_revision"] != controller:
            ancestry = fetch(f"compare/{record['source_revision']}...{controller}")
            require(ancestry.get("status") in ("ahead", "identical") and ancestry.get("merge_base_commit", {}).get("sha") == record["source_revision"],
                    "original source is outside selected native controller lineage")
    else:
        for revision in set((controller, record_controller, record["source_revision"])):
            trusted_lineage(revision, fetch)
    selected = selected_job(run_id, attempt, proof["job_name"], architecture, fetch)
    recovery = proof.get("native_step_recovery")
    if recovery is not None:
        actual = recovery_eligibility(selected, fetch(f"actions/jobs/{selected['id']}/logs", binary=True))
        require(recovery == actual, "original successful-stage evidence differs")
        require(bool(origin) and bool(payload_origin), "recovery requires independent record and payload origins")
    else:
        require(selected.get("status") == "completed" and selected.get("conclusion") == "success", "original native qualification did not pass")
    metadata_job = selected_job(record_run, record_attempt, record_job, architecture, fetch)
    require(metadata_job.get("status") == "completed" and metadata_job.get("conclusion") == "success", "small-record recovery job did not pass")
    now = now or datetime.now(timezone.utc)
    payload = fetch(f"actions/artifacts/{proof['artifact_id']}")
    available(payload, proof["artifact_name"], proof["artifact_digest"], payload_run, native_run["head_sha"], now)
    if recovery:
        recovery_payload_binding(selected, recovery, payload)
    inventory = fetch(f"actions/runs/{record_run}/artifacts?per_page=100")
    require(inventory.get("total_count", 0) <= 100, "incomplete origin artifact discovery")
    record_name = f"runner-records-{provider}-{architecture}"
    candidates = [item for item in inventory.get("artifacts", []) if item.get("name") == record_name]
    require(len(candidates) == 1, "original small qualification record is missing or ambiguous")
    metadata = candidates[0]
    require(type(metadata.get("size_in_bytes")) is int and metadata["size_in_bytes"] <= MAX_RECORD_BYTES,
            "qualification record artifact is too large")
    available(metadata, record_name, metadata.get("digest"), record_run, metadata_run["head_sha"], now)
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
            "record_run_id": record_run, "record_run_attempt": record_attempt,
            "record_controller_revision": record_controller, "record_job_name": record_job,
            "native_qualification_scope": "successful-native-step-before-seal-failure" if recovery else "successful-native-job",
            "original_job_conclusion": selected["conclusion"],
            "record_artifact_id": metadata["id"], "record_artifact_digest": metadata["digest"],
            "run_attempt": attempt, "controller_revision": proof["controller_revision"],
            "job_name": proof["job_name"], "archive_filename": proof["archive_filename"],
            "archive_sha256": proof["archive_sha256"], "record_filename": name}


def verify_current_origin(record, fetch=api, root="."):
    """Same-run test composition only; ordinary discovery never calls this helper."""
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "current-run repository differs")
    event = os.environ.get("GITHUB_EVENT_NAME")
    require(event in ("pull_request", "push", "workflow_dispatch"), "unsupported current-run event")
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    require(revision == os.environ.get("GITHUB_SHA"), "current manager checkout differs from workflow source")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    record_run = record["qualification"].get("record_origin", {}).get("run_id", record["qualification"]["run_id"])
    if record_run != run_id:
        return verify_origin(record, fetch=fetch)
    return verify_origin(record, fetch=fetch, current_context={"run_id": run_id,
                         "controller_revision": revision, "event": event})


def selection_verifier(baseline_run=None, root=".", fetch=api):
    """Caller-authorized manual baseline plus current-run or protected-history records."""
    if baseline_run is not None:
        require(type(baseline_run) is int and baseline_run > 0 and
                os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and
                os.environ.get("GITHUB_REPOSITORY") == REPOSITORY,
                "baseline selection requires an explicit manual fork diagnostic")
    manual=None
    if baseline_run is not None:
        from component_selection import manual_verifier
        manual=manual_verifier(baseline_run,fetch=fetch)
    def verify(record):
        if manual is not None:
            record_run=record['qualification'].get('record_origin',{}).get('run_id',record['qualification']['run_id'])
            if str(record_run)!=os.environ.get('GITHUB_RUN_ID'):
                return manual(record)
        return verify_current_origin(record, fetch=fetch, root=root)
    return verify


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
    allowed_jobs = artifacts.runner_job_names(provider, architecture)
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
