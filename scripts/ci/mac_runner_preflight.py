#!/usr/bin/env python3
"""Inspect a proposed backup Mac CI run without registering or starting a runner.

This preparation tool deliberately never reports execution ready. Live isolation
and enforceable job admission remain prerequisites, rather than claims fixtures
or repository-level runner administration can establish.
"""
import argparse
import json
import re
import subprocess

REPOSITORY = "Jayson-Stangel/symphony"
WORKFLOWS = {
    ".github/workflows/make-all.yml": {
        "job": "Fast process contracts (macos-14)",
        "required_check": "Application checks",
    },
    ".github/workflows/runner-integration.yml": {
        "job": "Explicit credential-free integration consumers",
        "required_check": "Browser qualification",
    },
    ".github/workflows/package-checks.yml": {
        "job": "Native helpers and fixtures (macos)",
        "required_check": "Package qualification",
    },
}
SHA = re.compile(r"[0-9a-f]{40}\Z")


class Refusal(ValueError):
    pass


def github_read(path, *, timeout=30):
    """Use the existing authenticated CLI for bounded GETs; never echo errors."""
    result = subprocess.run(
        ["gh", "api", "--method", "GET", "--hostname", "github.com", path],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode:
        raise Refusal("GitHub read failed; inspect authentication/permissions privately")
    try:
        return json.loads(result.stdout)
    except ValueError:
        raise Refusal("GitHub returned invalid JSON") from None


def supported_pr_route(pr):
    """Feature CI targets dev; promotion CI is only same-repo dev into main.

    This route check grants no merge, production promotion or rollout authority.
    Repository/owner/source identity checks remain required separately.
    """
    return pr.get("base", {}).get("ref") == "dev" or (
        pr.get("base", {}).get("ref") == "main" and pr.get("head", {}).get("ref") == "dev"
    )


def _inspect_run(run_id, attempt, pr_number, head, source, workflow, read, require_admin):
    """Verify live source/run identity and report missing execution boundaries.

    A successful identity read is not job admission, approval, CI qualification,
    or evidence that this machine is an isolated runner host.
    """
    if any(type(value) is not int or value <= 0 for value in (run_id, attempt, pr_number)):
        raise Refusal("run, attempt and PR must be positive integers")
    if not all(isinstance(value, str) and SHA.fullmatch(value) for value in (head, source)):
        raise Refusal("head and tested source must be full lowercase commit SHAs")
    if workflow not in WORKFLOWS:
        raise Refusal("only ordinary application or package CI is in this preparation scope")
    root = "repos/" + REPOSITORY
    repo = read(root)
    run = read(f"{root}/actions/runs/{run_id}/attempts/{attempt}")
    pr = read(f"{root}/pulls/{pr_number}")
    commit = read(f"{root}/commits/{source}")
    checks = [
        (repo.get("full_name") == REPOSITORY, "repository identity differs"),
        (not require_admin or repo.get("permissions", {}).get("admin") is True, "runner administration permission is unverified"),
        (run.get("id") == run_id and run.get("run_attempt") == attempt, "run or attempt differs"),
        (run.get("repository", {}).get("full_name") == REPOSITORY, "run repository differs"),
        (run.get("event") == "pull_request", "only ordinary PR runs are supported by this preflight"),
        (run.get("path") == workflow, "workflow path differs"),
        (run.get("head_sha") == head, "run head differs from the reviewed PR head"),
        (run.get("status") in {"queued", "in_progress", "waiting", "pending"}, "run is not pending; a completed result cannot be reused"),
        (pr.get("number") == pr_number and pr.get("state") == "open", "PR is not the selected open PR"),
        (pr.get("head", {}).get("repo", {}).get("full_name") == REPOSITORY, "fork source is refused"),
        (pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY, "PR base repository differs"),
        (supported_pr_route(pr), "PR must target dev, or be the same-repository dev-to-main promotion CI"),
        (pr.get("user", {}).get("login") == "Jayson-Stangel", "PR author differs from the approved owner"),
        (run.get("actor", {}).get("login") == "Jayson-Stangel", "run actor differs from the approved owner"),
        (run.get("triggering_actor", {}).get("login") == "Jayson-Stangel", "run triggering actor differs from the approved owner"),
        (pr.get("head", {}).get("sha") == head, "PR head advanced or differs"),
        (pr.get("merge_commit_sha") == source, "current tested merge source differs"),
        (any(item.get("number") == pr_number for item in run.get("pull_requests", [])), "run is not associated with the selected PR"),
        (commit.get("sha") == source, "tested source commit differs"),
        ([item.get("sha") for item in commit.get("parents", [])] == [pr.get("base", {}).get("sha"), head], "tested source does not bind the current base and PR head"),
    ]
    if pr.get("base", {}).get("ref") == "main":
        # A cached PR/merge snapshot must not admit stale main or dev refs.
        main_ref = read(f"{root}/git/ref/heads/main")
        dev_ref = read(f"{root}/git/ref/heads/dev")
        checks.extend([
            (main_ref.get("object", {}).get("sha") == pr.get("base", {}).get("sha"), "promotion main base advanced or differs"),
            (dev_ref.get("object", {}).get("sha") == head, "promotion dev head advanced or differs"),
        ])
    failures = [message for valid, message in checks if not valid]
    if failures:
        raise Refusal("; ".join(failures))
    owner_type = repo.get("owner", {}).get("type")
    blockers = [
        "No exact isolated host, account, mounts, network policy or toolchain has been approved and verified.",
        "No enforceable pre-execution admission boundary has been demonstrated for the exact workflow and job.",
        "Existing hosted jobs cannot be retargeted by editing a workflow and rerunning its old revision.",
        "Real backup Mac process and native helper jobs, original artifact provenance and cleanup are unproved.",
    ]
    if owner_type == "User":
        blockers.insert(1, "Personal repository runners have no organization runner group workflow restriction.")
    return {
        "schema": 1,
        "purpose": "read-only preparation; never execution authorization",
        "repository": REPOSITORY,
        "repository_owner_type": owner_type,
        "repository_visibility": repo.get("visibility"),
        "runner_admin_permission": repo.get("permissions", {}).get("admin") is True,
        "run_id": run_id,
        "run_attempt": attempt,
        "pr_number": pr_number,
        "pr_head": head,
        "base_sha": pr["base"]["sha"],
        "pr_base_ref": pr["base"].get("ref"),
        "pr_head_ref": pr["head"].get("ref"),
        "expected_tested_merge_sha": source,
        "workflow_path": workflow,
        "job_display": WORKFLOWS[workflow]["job"],
        "required_check": WORKFLOWS[workflow]["required_check"],
        "pending_run_status": run["status"],
        "run_actor": run.get("actor", {}).get("login"),
        "triggering_actor": run.get("triggering_actor", {}).get("login"),
        "pr_author": pr.get("user", {}).get("login"),
        "identity_checked": True,
        "execution_ready": False,
        "workflow_controller_revision_verified": False,
        "blockers": blockers,
    }


def inspect_run(run_id, attempt, pr_number, head, source, workflow, read=github_read, require_admin=True):
    try:
        return _inspect_run(run_id, attempt, pr_number, head, source, workflow, read, require_admin)
    except (AttributeError, KeyError, TypeError):
        raise Refusal("GitHub metadata is incomplete or has an unexpected shape") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--tested-source", required=True)
    parser.add_argument("--workflow", choices=WORKFLOWS, required=True)
    args = parser.parse_args()
    try:
        report = inspect_run(args.run_id, args.attempt, args.pr, args.head, args.tested_source, args.workflow)
    except (Refusal, OSError, subprocess.TimeoutExpired) as error:
        message = str(error) if isinstance(error, Refusal) else "GitHub metadata could not be read within the bounded request"
        print(json.dumps({"execution_ready": False, "identity_checked": False, "error": message}, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2  # Honest blocked preparation, including when all identity reads agree.


if __name__ == "__main__":
    raise SystemExit(main())
