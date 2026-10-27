"""Source identity refusal fixtures; these do not qualify runner admission."""
import copy
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("mac_runner_preflight", ROOT / "scripts/ci/mac_runner_preflight.py")
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)
HEAD, SOURCE, BASE = "a" * 40, "b" * 40, "c" * 40
REPO = preflight.REPOSITORY
WORKFLOW = ".github/workflows/make-all.yml"


class PreparationIdentity(unittest.TestCase):
    def setUp(self):
        root = "repos/" + REPO
        self.documents = {
            root: {"full_name": REPO, "owner": {"type": "User"}, "visibility": "public", "permissions": {"admin": True}},
            root + "/actions/runs/12/attempts/2": {
                "id": 12, "run_attempt": 2, "repository": {"full_name": REPO},
                "event": "pull_request", "path": WORKFLOW, "head_sha": HEAD,
                "status": "queued", "pull_requests": [{"number": 34}],
                "actor": {"login": "Jayson-Stangel"}, "triggering_actor": {"login": "Jayson-Stangel"},
            },
            root + "/pulls/34": {
                "number": 34, "state": "open", "merge_commit_sha": SOURCE, "user": {"login": "Jayson-Stangel"},
                "head": {"ref": "codex/feature", "sha": HEAD, "repo": {"full_name": REPO}},
                "base": {"sha": BASE, "ref": "dev", "repo": {"full_name": REPO}},
            },
            root + "/commits/" + SOURCE: {"sha": SOURCE, "parents": [{"sha": BASE}, {"sha": HEAD}]},
            root + "/git/ref/heads/main": {"object": {"sha": BASE}},
            root + "/git/ref/heads/dev": {"object": {"sha": HEAD}},
        }

    def inspect(self, documents=None, **kw):
        args = dict(run_id=12, attempt=2, pr_number=34, head=HEAD, source=SOURCE, workflow=WORKFLOW,
                    read=(documents or self.documents).__getitem__)
        args.update(kw)
        return preflight.inspect_run(**args)

    def test_matching_identity_is_still_blocked_and_not_authority(self):
        report = self.inspect()
        self.assertTrue(report["identity_checked"])
        self.assertFalse(report["execution_ready"])
        self.assertFalse(report["workflow_controller_revision_verified"])
        self.assertEqual(report["required_check"], "Application checks")
        self.assertTrue(any("Personal repository" in reason for reason in report["blockers"]))
        self.assertTrue(any("cleanup" in reason for reason in report["blockers"]))

    def test_identity_failures_refuse_instead_of_using_old_success(self):
        root = "repos/" + REPO
        cases = [
            (root, ("permissions", "admin"), False),
            (root + "/actions/runs/12/attempts/2", ("run_attempt",), 1),
            (root + "/actions/runs/12/attempts/2", ("event",), "pull_request_target"),
            (root + "/actions/runs/12/attempts/2", ("head_sha",), "d" * 40),
            (root + "/actions/runs/12/attempts/2", ("status",), "completed"),
            (root + "/actions/runs/12/attempts/2", ("status",), "cancelled"),
            (root + "/actions/runs/12/attempts/2", ("pull_requests",), []),
            (root + "/actions/runs/12/attempts/2", ("path",), ".github/workflows/burrito-release.yml"),
            (root + "/pulls/34", ("head", "repo", "full_name"), "untrusted/fork"),
            (root + "/pulls/34", ("head", "sha"), "d" * 40),
            (root + "/pulls/34", ("merge_commit_sha",), "d" * 40),
            (root + "/pulls/34", ("base", "ref"), "main"),
            (root + "/commits/" + SOURCE, ("parents",), [{"sha": HEAD}]),
            (root + "/commits/" + SOURCE, ("parents",), [{"sha": "d" * 40}, {"sha": HEAD}]),
        ]
        for document, keys, value in cases:
            with self.subTest(document=document, keys=keys, value=value):
                changed = copy.deepcopy(self.documents)
                target = changed[document]
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaises(preflight.Refusal):
                    self.inspect(changed)

    def test_missing_or_malformed_metadata_fails_closed(self):
        for endpoint in self.documents:
            if "/git/ref/heads/" in endpoint:
                continue
            for value in (None, [], {}, "invalid"):
                with self.subTest(endpoint=endpoint, value=value):
                    changed = copy.deepcopy(self.documents)
                    changed[endpoint] = value
                    with self.assertRaises(preflight.Refusal):
                        self.inspect(changed)

    def test_no_unapproved_workflow_or_abbreviated_source(self):
        for kwargs in ({"workflow": ".github/workflows/release-native.yml"}, {"head": "abc1234"},
                       {"attempt": 0}, {"run_id": True}, {"source": "A" * 40}):
            with self.subTest(kwargs=kwargs), self.assertRaises(preflight.Refusal):
                self.inspect(**kwargs)

    def test_even_organization_metadata_does_not_establish_admission(self):
        self.documents["repos/" + REPO]["owner"]["type"] = "Organization"
        report = self.inspect()
        self.assertFalse(report["execution_ready"])
        self.assertTrue(any("admission" in reason for reason in report["blockers"]))

    def test_github_transport_only_gets_and_does_not_echo_provider_errors(self):
        with patch.object(preflight.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "secret diagnostic")
            with self.assertRaises(preflight.Refusal) as error:
                preflight.github_read("repos/" + REPO)
            self.assertNotIn("secret", str(error.exception))
            self.assertEqual(run.call_args.args[0][:7], ["gh", "api", "--method", "GET", "--hostname", "github.com", "repos/" + REPO])
            self.assertEqual(run.call_args.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
