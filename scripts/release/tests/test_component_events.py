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
            subprocess.run(['git','init','--quiet',str(root)],check=True)
            subprocess.run(['git','-C',str(root),'add','.'],check=True)
            subprocess.run(['git','-C',str(root),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','--quiet','-m','Freeze actual manager source'],check=True)
            revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
            bound='v0.0.5-manager.'+revision+'.manifest.'+'d'*64
            for policy,tag,expected in (('1\n',bound,True),('1\n','v0.0.5',False),
                                        ('1\n',bound.replace(revision,'e'*40),False),
                                        ('1\n',bound[:-1],False),('2\n','v0.0.5',True),
                                        ('2\n','v0.0.4',False),('3\n','v0.0.5',False)):
                (root/'scripts/release/release-schema').write_text(policy)
                result=subprocess.run(['bash','-e','-c',script],cwd=root,capture_output=True,
                    env=dict(os.environ,GITHUB_REF_TYPE='tag',GITHUB_REF_NAME=tag))
                self.assertEqual(result.returncode==0,expected,(policy,tag,result.stderr))
        caller=(ROOT/'.github/workflows/burrito-release.yml').read_text()
        self.assertIn('manifest_schema: ${{ needs.qualification.outputs.release_schema }}',caller)

    def test_git_object_authority_consumers_checkout_complete_history(self):
        import re
        jobs={'component-plan.yml':['plan'],
              'runner-release.yml':['component-plan','candidate-metadata','packaged-release-platform','packaged-candidate'],
              'runner-integration.yml':['manager-inputs','listener-and-cleanup'],
              'current-manager-package.yml':['package']}
        for filename,names in jobs.items():
            workflow=(ROOT/'.github/workflows'/filename).read_text()
            for name in names:
                with self.subTest(workflow=filename,job=name):
                    block=re.search(r'(?ms)^  '+re.escape(name)+r':\n.*?(?=^  [a-z][a-z-]+:|\Z)',workflow)
                    self.assertIsNotNone(block)
                    self.assertRegex(block.group(),r'uses: actions/checkout@[^\n]+\n        with:\n          fetch-depth: 0')

    def test_browser_wrapper_requires_explicit_classification_for_qualification_or_omission(self):
        workflow = (ROOT / '.github/workflows/runner-integration.yml').read_text().split('  chromium:\n', 1)[1].split('  portable:\n', 1)[0]
        block = workflow.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        guard = '\n'.join(line[10:] for line in block.splitlines())
        # Browser routing no longer depends on a reference-component plan. Both
        # docs omission and unchanged-browser omission need an explicit decision.
        self.assertNotIn('needs.plan', workflow)
        with tempfile.TemporaryDirectory() as directory:
            for code, browser, valid in (
                ('false', 'false', True), ('true', 'false', True), ('true', 'true', True),
                ('false', 'true', False), ('false', '', False), ('false', None, False),
                ('true', '', False), ('true', None, False), ('true', 'reuse', False),
                ('true', 'build', False), ('true', 'unknown', False), ('', 'false', False),
            ):
                for classification in ('success', 'failure', 'cancelled', 'skipped', ''):
                    with self.subTest(code=code, browser=browser, classification=classification):
                        env = dict(os.environ, RESULT=classification, CODE=code)
                        if browser is None:
                            env.pop('BROWSER', None)
                        else:
                            env['BROWSER'] = browser
                        result = subprocess.run(['bash', '-e', '-c', guard], cwd=directory,
                                                env=env, capture_output=True, text=True)
                        self.assertEqual(result.returncode == 0, valid and classification == 'success')

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
                    RUNNERS='skipped', INPUTS='success', PACKAGE='success', REFERENCE_NODE_REQUEST='false', REFERENCE_NODE='skipped')
        with tempfile.TemporaryDirectory() as directory:
            def accepted(values):
                return subprocess.run(['bash', '-e', '-c', script], cwd=directory,
                                      env=dict(os.environ, **values)).returncode == 0
            self.assertTrue(accepted(good))
            self.assertTrue(accepted(dict(good, HAS_BUILDS='true', RUNNERS='success')))
            requested = dict(good, REFERENCE_NODE_REQUEST='true', REFERENCE_NODE='success')
            self.assertTrue(accepted(requested))
            for state in ('failure', 'cancelled', 'skipped', ''):
                self.assertFalse(accepted(dict(requested, REFERENCE_NODE=state)))
            self.assertFalse(accepted(dict(good, REFERENCE_NODE='success')))
            self.assertFalse(accepted(dict(good, REFERENCE_NODE_REQUEST='')))
            for key in ('RESULT', 'PORTABLE', 'HELPERS', 'PLAN', 'INPUTS', 'PACKAGE'):
                for state in ('failure', 'cancelled', 'skipped', ''):
                    self.assertFalse(accepted(dict(good, **{key: state})), (key, state))
            for state in ('success', 'failure', 'cancelled', ''):
                self.assertFalse(accepted(dict(good, RUNNERS=state)))
            self.assertFalse(accepted(dict(good, HAS_BUILDS='')))
            docs = dict(RESULT='success', CODE='false', PORTABLE='skipped', HELPERS='skipped', PLAN='skipped', RUNNERS='skipped',
                        INPUTS='skipped', PACKAGE='skipped', HAS_BUILDS='', REFERENCE_NODE_REQUEST='false', REFERENCE_NODE='skipped')
            self.assertTrue(accepted(docs))
            self.assertFalse(accepted(dict(docs, PACKAGE='failure')))


if __name__ == '__main__':
    unittest.main()
