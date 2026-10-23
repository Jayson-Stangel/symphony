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
            tools=root/'tools';tools.mkdir()
            gh=tools/'gh'
            gh.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"status":"404"}\'\nexit 1\n')
            gh.chmod(0o755)
            bound='v0.0.5-manager.'+revision+'.manifest.'+'d'*64
            for policy,tag,expected in (('1\n',bound,True),('1\n','v0.0.5',False),
                                        ('1\n',bound.replace(revision,'e'*40),False),
                                        ('1\n',bound[:-1],False),('2\n','v0.0.5',True),
                                        ('2\n','v0.0.4',False),('3\n','v0.0.5',False)):
                (root/'scripts/release/release-schema').write_text(policy)
                result=subprocess.run(['bash','-e','-c',script],cwd=root,capture_output=True,
                    env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],GITHUB_REF_TYPE='tag',GITHUB_REF_NAME=tag))
                self.assertEqual(result.returncode==0,expected,(policy,tag,result.stderr))
            (root/'scripts/release/release-schema').write_text('2\n')
            gh.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"tag_name":"v0.0.5","draft":false}\'\n')
            completed=subprocess.run(['bash','-e','-c',script],cwd=root,capture_output=True,
                env=dict(os.environ,PATH=str(tools)+os.pathsep+os.environ['PATH'],GITHUB_REF_TYPE='tag',GITHUB_REF_NAME='v0.0.5'))
            self.assertNotEqual(completed.returncode,0)
            self.assertIn(b'completed release',completed.stderr)
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


class CommittedReleaseMode(unittest.TestCase):
    """Run actual caller shell and planner; only external native receipts are synthetic."""
    def test_normal_caller_builds_changes_and_preserves_verified_originals(self):
        import copy
        import json
        import sys
        sys.path.insert(0, str(ROOT / 'scripts/release'))
        from runner_inputs import identity
        from manager_version import schema, read

        self.assertEqual(schema(ROOT), 2)
        self.assertNotEqual(read(ROOT), '0.0.4', 'new manager releases must preserve the completed v0.0.4 identity')
        caller = (ROOT / '.github/workflows/burrito-release.yml').read_text()
        self.assertIn('manifest_schema: ${{ needs.qualification.outputs.release_schema }}', caller)
        self.assertIn('uses: ./.github/workflows/release-preflight.yml', caller)
        preflight = (ROOT / '.github/workflows/release-preflight.yml').read_text()
        self.assertIn('schema=$(python3 scripts/release/manager_version.py --schema)', preflight)
        producer = (ROOT / '.github/workflows/runner-release.yml').read_text()
        body = producer.split('      - name: Resolve the selected component plan\n', 1)[1]
        body = body.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        script = '\n'.join(line[10:] for line in body.splitlines())
        templates = json.loads((ROOT / 'scripts/release/tests/fixtures/manager-original-68.json').read_text())['records']
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            # This copy is an isolated real Git checkout, not a planner-only root.
            checkout = directory / 'checkout'
            subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(checkout)], check=True)
            revision = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
            tools = directory / 'tools'; tools.mkdir()
            driver = directory / 'synthetic-origin-driver.py'
            driver.write_text('''import json, os, sys
sys.path.insert(0, os.environ['RELEASE_MODULES'])
import runner_reuse
records=json.load(open(os.environ['SYNTHETIC_RECORDS']))
runner_reuse.discover=lambda **kwargs: records
runner_reuse.verify_origin=lambda record: {'scope':'SYNTHETIC native receipt control', 'image':record['image']}
sys.argv=sys.argv[1:]
runner_reuse.main()
''')
            shim = tools / 'python3'
            shim.write_text('#!/bin/bash\nif [ "$1" = scripts/release/runner_reuse.py ]; then\n  exec "$REAL_PYTHON" "$SYNTHETIC_DRIVER" "$@"\nfi\nexec "$REAL_PYTHON" "$@"\n')
            shim.chmod(0o755)
            # Read the actual preflight receipt command, with committed policy,
            # instead of supplying a test-only schema to the producer.
            receipt = preflight.split('      - name: Emit exact-source qualification receipt\n', 1)[1]
            receipt = receipt.split('        run: |\n', 1)[1].split('      - ', 1)[0]
            receipt_script = '\n'.join(line[10:] for line in receipt.splitlines())
            receipt_output = directory / 'receipt'
            subprocess.run(['bash', '-e', '-c', receipt_script], cwd=ROOT, check=True,
                           env=dict(os.environ, GITHUB_SHA=revision, GITHUB_OUTPUT=str(receipt_output)))
            selected_schema = dict(line.split('=', 1) for line in receipt_output.read_text().splitlines())['schema']
            for mode in ('unchanged', 'changed', 'incompatible-finite'):
                records = copy.deepcopy(templates)
                if mode != 'incompatible-finite':
                    for record in records:
                        p, a = record['provider'], record['architecture']
                        record['input_identity'] = identity(checkout, p, a)['input_identity']
                        if mode == 'changed' and p == 'claude':
                            record['input_identity'] = 'f' * 64
                records_path = directory / 'records.json'
                records_path.write_text(json.dumps(records))
                result = subprocess.run(['bash', '-e', '-c', script], cwd=checkout, capture_output=True, text=True,
                    env=dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'], REAL_PYTHON=sys.executable,
                             SYNTHETIC_DRIVER=str(driver), SYNTHETIC_RECORDS=str(records_path),
                             RELEASE_MODULES=str(ROOT / 'scripts/release'), GITHUB_SHA=revision,
                             GITHUB_OUTPUT=str(directory / 'producer-output'), BASELINE_RUN='', PREVIOUS_REVISION='',
                             MANIFEST_SCHEMA='1' if mode == 'incompatible-finite' else selected_schema,
                             SELECTED_PLAN_ARTIFACT='', PREVIOUS_ARTIFACT_RUN='', LEGACY_ARTIFACT_RUN='',
                             MANAGER_ONLY_UPDATE='false'))
                if mode == 'incompatible-finite':
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('no reuse decision was authorized', result.stderr)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                plan = json.loads((checkout / 'component-plan.json').read_text())
                self.assertEqual(len(plan['build_matrix']['include']), 0 if mode == 'unchanged' else 2)
                self.assertEqual(len(plan['reuse_matrix']['include']), 4 if mode == 'unchanged' else 2)
                for entry in plan['reuse_matrix']['include']:
                    expected = next(r for r in records if r['provider'] == entry['provider'] and r['architecture'] == entry['architecture'])
                    self.assertEqual(entry['record'], expected)
                    self.assertEqual(entry['source_revision'], expected['source_revision'])
                    self.assertEqual(entry['origin']['image'], expected['image'])
                (checkout / 'component-plan.json').unlink()


if __name__ == '__main__':
    unittest.main()
