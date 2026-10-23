"""Execute event guards with failed, missing and cancelled planner decisions."""
import os
import hashlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class ComponentEvents(unittest.TestCase):
    def test_production_policy_and_tag_bind_manager_version_independently_of_engine(self):
        workflow=(ROOT/'.github/workflows/release-preflight.yml').read_text()
        section=workflow.split('      - name: Validate selected release version before native or image work\n',1)[1]
        body=section.split('        run: |\n',1)[1].split('      - ',1)[0]
        script='\n'.join(line[10:] for line in body.splitlines())
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'elixir').mkdir();(root/'scripts/release').mkdir(parents=True)
            (root/'elixir/mix.exs').write_text('    version: "0.0.4",\n')
            (root/'scripts/release/manager-version').write_text('0.0.5\n')
            (root/'scripts/release/manager_version.py').write_text((ROOT/'scripts/release/manager_version.py').read_text())
            for policy,tag,expected in (('1\n','v0.0.5',True),('2\n','v0.0.5',True),('2\n','v0.0.4',False),('3\n','v0.0.5',False)):
                (root/'scripts/release/release-schema').write_text(policy)
                result=subprocess.run(['bash','-e','-c',script],cwd=root,capture_output=True,
                    env=dict(os.environ,GITHUB_REF_TYPE='tag',GITHUB_REF_NAME=tag))
                self.assertEqual(result.returncode==0,expected,(policy,tag,result.stderr))
        caller=(ROOT/'.github/workflows/burrito-release.yml').read_text()
        self.assertIn('manifest_schema: ${{ needs.qualification.outputs.release_schema }}',caller)

    def test_browser_wrapper_requires_complete_plan_except_explicit_docs(self):
        workflow = (ROOT / '.github/workflows/browser-smoke.yml').read_text()
        block = workflow.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        guard = '\n'.join(line[10:] for line in block.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            for code, plan, browser, success in (
                ('false', 'skipped', '', True), ('true', 'success', 'reuse', True),
                ('true', 'success', 'build', True), ('false', 'success', 'reuse', False),
                ('true', 'failure', 'reuse', False), ('true', 'cancelled', 'reuse', False),
                ('true', 'skipped', 'reuse', False), ('true', 'success', '', False),
                ('true', 'success', 'unknown', False), ('', 'skipped', '', False),
            ):
                result = subprocess.run(['bash', '-e', '-c', guard], cwd=directory,
                                        env=dict(os.environ, RESULT='success', CODE=code, PLAN=plan, BROWSER=browser))
                self.assertEqual(result.returncode == 0, success, (code, plan, browser))

    def test_manual_baseline_is_rejected_before_discovery_on_ordinary_events(self):
        workflow = (ROOT / '.github/workflows/component-plan.yml').read_text()
        block = workflow.split('        run: |\n', 1)[1].split('          python3 scripts/release/runner_reuse.py', 1)[0]
        script = '\n'.join(line[10:] for line in block.splitlines())
        for event, run, accepted in (('pull_request', '', True), ('push', '', True),
                                     ('pull_request', '123', False), ('push', '123', False),
                                     ('workflow_dispatch', '123', True), ('workflow_dispatch', 'not-a-run', False)):
            with self.subTest(event=event, run=run):
                result = subprocess.run(['bash', '-e', '-c', script], env=dict(os.environ, GITHUB_EVENT_NAME=event, BASELINE_RUN=run))
                self.assertEqual(result.returncode == 0, accepted)

    def test_helper_artifact_is_checked_before_executable_permission(self):
        workflow = (ROOT / '.github/workflows/current-manager-package.yml').read_text()
        section = workflow.split('      - name: Verify the original exact-source helper checksum\n', 1)[1]
        body = section.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        script = '\n'.join(line[10:] for line in body.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            helper = Path(directory) / 'symphony-host'
            payload = b'actual selected helper bytes'
            checksum = Path(directory) / 'symphony-host.sha256'
            checksum.write_text(hashlib.sha256(payload).hexdigest() + '  symphony-host\n')
            helper.write_bytes(payload)
            helper.chmod(0o644)
            self.assertEqual(subprocess.run(['bash', '-e', '-c', script], cwd=directory,
                                           capture_output=True).returncode, 0)
            self.assertTrue(helper.stat().st_mode & 0o111)
            helper.write_bytes(b'changed after original qualification')
            helper.chmod(0o644)
            self.assertNotEqual(subprocess.run(['bash', '-e', '-c', script], cwd=directory,
                                              capture_output=True).returncode, 0)
            self.assertFalse(helper.stat().st_mode & 0o111)
        guard_section = workflow.split('      - name: Require the current source\'s qualified native helpers\n', 1)[1]
        guard = guard_section.split('        run: ', 1)[1].split('\n', 1)[0]
        for revision, expected in (('a' * 40, True), ('b' * 40, False), ('', False)):
            result = subprocess.run(['bash', '-e', '-c', guard], env=dict(os.environ,
                                    HELPER_REVISION=revision, GITHUB_SHA='a' * 40))
            self.assertEqual(result.returncode == 0, expected)

    def test_current_manager_wrapper_rejects_missing_failed_or_cancelled_evidence(self):
        workflow = (ROOT / '.github/workflows/runner-integration.yml').read_text()
        section = workflow.split('      - name: Require complete current-manager and component decisions\n', 1)[1]
        body = section.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        script = '\n'.join(line[10:] for line in body.splitlines())
        good = dict(RESULT='success', CODE='true', PORTABLE='success', HELPERS='success', PLAN='success', HAS_BUILDS='false',
                    RUNNERS='skipped', INPUTS='success', PACKAGE='success')
        with tempfile.TemporaryDirectory() as directory:
            def accepted(values):
                return subprocess.run(['bash', '-e', '-c', script], cwd=directory,
                                      env=dict(os.environ, **values)).returncode == 0
            self.assertTrue(accepted(good))
            self.assertTrue(accepted(dict(good, HAS_BUILDS='true', RUNNERS='success')))
            for key in ('RESULT', 'PORTABLE', 'HELPERS', 'PLAN', 'INPUTS', 'PACKAGE'):
                for state in ('failure', 'cancelled', 'skipped', ''):
                    self.assertFalse(accepted(dict(good, **{key: state})), (key, state))
            for state in ('success', 'failure', 'cancelled', ''):
                self.assertFalse(accepted(dict(good, RUNNERS=state)))
            self.assertFalse(accepted(dict(good, HAS_BUILDS='')))
            docs = dict(RESULT='success', CODE='false', PORTABLE='skipped', HELPERS='skipped', PLAN='skipped', RUNNERS='skipped',
                        INPUTS='skipped', PACKAGE='skipped', HAS_BUILDS='')
            self.assertTrue(accepted(docs))
            self.assertFalse(accepted(dict(docs, PACKAGE='failure')))


if __name__ == '__main__':
    unittest.main()
