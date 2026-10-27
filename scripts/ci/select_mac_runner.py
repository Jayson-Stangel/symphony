#!/usr/bin/env python3
"""Resolve source-bound routing; external admission/isolation approval is separate.

The repository variable is never a security boundary against an admitted hostile
workflow. Operators must establish the approved service freeze before installing
it. This module never registers runners, starts jobs or grants CI success.
"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from mac_runner_preflight import Refusal, REPOSITORY, SHA, github_read, inspect_run, supported_pr_route

TARGETS = {
    ".github/workflows/make-all.yml": {"platform-contracts"},
    ".github/workflows/package-checks.yml": {"native-host-package"},
    ".github/workflows/native-manager-helpers.yml": {"helper"},
    ".github/workflows/current-manager-package.yml": {"package"},
    ".github/workflows/release-native.yml": {"native"},
    ".github/workflows/runner-release.yml": {"packaged-candidate", "packaged-release-platform"},
    ".github/workflows/burrito-release.yml": {"build", "smoke", "controller-diagnostics"},
}
CONTROLLERS = {
    ".github/workflows/make-all.yml", ".github/workflows/package-checks.yml",
    ".github/workflows/runner-integration.yml", ".github/workflows/runner-release.yml",
    ".github/workflows/burrito-release.yml",
}
FIELDS = {
    "schema", "repository", "event_name", "controller_workflow", "workflow_ref",
    "workflow_sha", "run_id", "run_attempt", "pr_number", "pr_head", "base_sha",
    "source_sha", "actor", "triggering_actor", "pr_author", "expires_at", "targets",
}
TARGET_FIELDS = {"consumer_workflow", "job_key", "runner_label"}
LABEL = re.compile(r"symphony-backup-mac-[a-f0-9]{32}\Z")
OWNER = "Jayson-Stangel"


def verify_live(permit, read=github_read, expected_pr_base_ref=None, expected_pr_head_ref=None):
    if permit["event_name"] == "pull_request":
        report = inspect_run(permit["run_id"], permit["run_attempt"], permit["pr_number"],
                             permit["pr_head"], permit["source_sha"], permit["controller_workflow"],
                             read=read, require_admin=False)
        if (report.get("identity_checked") is not True or report.get("base_sha") != permit["base_sha"]
                or any(report.get(key) != OWNER for key in ("run_actor", "triggering_actor", "pr_author"))):
            raise Refusal("live PR source identity is not verified")
        if ((expected_pr_base_ref is not None and report.get("pr_base_ref") != expected_pr_base_ref)
                or (expected_pr_head_ref is not None and report.get("pr_head_ref") != expected_pr_head_ref)):
            raise Refusal("live PR branch route differs from the reviewed event")
        return
    root = "repos/" + REPOSITORY
    run = read(f"{root}/actions/runs/{permit['run_id']}/attempts/{permit['run_attempt']}")
    if (run.get("id") != permit["run_id"] or run.get("run_attempt") != permit["run_attempt"]
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("event") != permit["event_name"] or run.get("path") != permit["controller_workflow"]
            or run.get("head_sha") != permit["source_sha"]
            or run.get("status") not in {"queued", "in_progress", "waiting", "pending"}
            or run.get("actor", {}).get("login") != OWNER
            or run.get("triggering_actor", {}).get("login") != OWNER):
        raise Refusal("live non-PR run/source/actor identity differs")
    if read(f"{root}/commits/{permit['source_sha']}").get("sha") != permit["source_sha"]:
        raise Refusal("live source commit differs")


def select_runner(raw, context, event, consumer, job_key, now=None, verify=verify_live):
    """Return only this consumer's label; all unselected jobs stay hosted."""
    if consumer not in TARGETS or job_key not in TARGETS[consumer]:
        raise Refusal("unsupported credential-free selector target")
    if not raw:
        return "macos-14"
    if len(raw) > 16384:
        raise Refusal("routing permit is oversized")
    try:
        permit = json.loads(raw)
    except ValueError:
        raise Refusal("routing permit is not valid JSON") from None
    if not isinstance(permit, dict) or type(permit.get("run_id")) is not int:
        raise Refusal("routing permit has no integer run identity")
    if str(permit["run_id"]) != context.get("GITHUB_RUN_ID"):
        return "macos-14"
    if set(permit) != FIELDS or type(permit["schema"]) is not int or permit["schema"] != 1:
        raise Refusal("routing permit has an unsupported schema")
    if any(type(permit[key]) is not int or permit[key] <= 0 for key in ("run_id", "run_attempt")):
        raise Refusal("routing permit identities must be positive integers")
    if not isinstance(permit["controller_workflow"], str) or permit["controller_workflow"] not in CONTROLLERS:
        raise Refusal("routing permit controller is unsupported")
    if any(not isinstance(permit[key], str) or not SHA.fullmatch(permit[key]) for key in ("source_sha", "workflow_sha")):
        raise Refusal("routing permit revisions must be full lowercase SHAs")
    targets = permit["targets"]
    if not isinstance(targets, list) or not 1 <= len(targets) <= 8:
        raise Refusal("routing permit must name one to eight exact job targets")
    pairs, labels = set(), set()
    selected = None
    for target in targets:
        if not isinstance(target, dict) or set(target) != TARGET_FIELDS:
            raise Refusal("routing target has an unsupported schema")
        workflow, key, label = target["consumer_workflow"], target["job_key"], target["runner_label"]
        if not isinstance(workflow, str) or not isinstance(key, str) or workflow not in TARGETS or key not in TARGETS[workflow]:
            raise Refusal("routing target is not a retained credential-free Mac job")
        if not isinstance(label, str) or not LABEL.fullmatch(label):
            raise Refusal("routing target label must have the dedicated random label format")
        if (workflow, key) in pairs or label in labels:
            raise Refusal("routing targets and labels must be distinct")
        pairs.add((workflow, key)); labels.add(label)
        if (workflow, key) == (consumer, job_key):
            selected = label
    try:
        expiry = datetime.datetime.strptime(permit["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        raise Refusal("routing permit expiry must be an exact UTC timestamp") from None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not now < expiry <= now + datetime.timedelta(hours=2):
        raise Refusal("routing permit expired or exceeds the two hour routing window")
    try:
        expected = {
            "repository": REPOSITORY, "event_name": context["GITHUB_EVENT_NAME"],
            "workflow_ref": context["GITHUB_WORKFLOW_REF"], "workflow_sha": context["GITHUB_WORKFLOW_SHA"],
            "source_sha": context["GITHUB_SHA"], "actor": OWNER, "triggering_actor": OWNER,
        }
        if context["GITHUB_REPOSITORY"] != REPOSITORY or context["GITHUB_ACTOR"] != OWNER or context["GITHUB_TRIGGERING_ACTOR"] != OWNER:
            raise Refusal("only the owner-authored and owner-triggered run can use backup routing")
        if str(permit["run_attempt"]) != context["GITHUB_RUN_ATTEMPT"] or any(permit[key] != value for key, value in expected.items()):
            raise Refusal("routing permit differs from the exact controller/source/run context")
        if permit["workflow_ref"] != f"{REPOSITORY}/{permit['controller_workflow']}@{context['GITHUB_REF']}":
            raise Refusal("controller workflow/ref differs")
        if permit["event_name"] == "pull_request":
            pr = event["pull_request"]
            if (type(permit["pr_number"]) is not int or permit["pr_number"] <= 0
                    or permit["pr_number"] != event["number"] or permit["pr_author"] != OWNER
                    or pr["user"]["login"] != OWNER
                    or permit["pr_head"] != pr["head"]["sha"] or permit["base_sha"] != pr["base"]["sha"]
                    or not SHA.fullmatch(permit["pr_head"]) or not SHA.fullmatch(permit["base_sha"])
                    or pr["head"]["repo"]["full_name"] != REPOSITORY
                    or pr["base"]["repo"]["full_name"] != REPOSITORY or not supported_pr_route(pr)
                    or context["GITHUB_REF"] != f"refs/pull/{permit['pr_number']}/merge"):
                raise Refusal("routing permit requires the exact owner feature-to-dev or dev-to-main CI source")
        elif permit["event_name"] in {"push", "workflow_dispatch"}:
            if not context["GITHUB_REF"].startswith("refs/heads/"):
                raise Refusal("backup routing does not admit tag publication events")
            if permit["pr_number"] is not None or any(permit[key] is not None for key in ("pr_head", "base_sha", "pr_author")):
                raise Refusal("non-PR routing must not claim PR provenance")
        else:
            raise Refusal("backup routing event is unsupported")
    except (KeyError, TypeError):
        raise Refusal("selector event/context is incomplete") from None
    # Validate all matching-run permits, including consumers omitted by classification.
    if permit["event_name"] == "pull_request":
        verify(permit, expected_pr_base_ref=event["pull_request"]["base"]["ref"],
               expected_pr_head_ref=event["pull_request"]["head"].get("ref"))
    else:
        verify(permit)
    return selected or "macos-14"


def main():
    try:
        raw = os.environ.get("BACKUP_MAC_PERMIT", "")
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()) if raw else {}
        runner = select_runner(raw, os.environ, event, os.environ["SELECT_WORKFLOW_PATH"], os.environ["SELECT_JOB_KEY"])
    except (Refusal, KeyError, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
        message = str(error) if isinstance(error, Refusal) else "selector metadata is unavailable or invalid"
        print("Backup Mac routing refused: " + message, file=sys.stderr)
        return 1
    print("runner=" + runner)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
