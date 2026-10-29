"""Verify real fixture failures/skips cannot become successful preflight receipts."""
import importlib.util
from pathlib import Path
import tempfile
import os
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_tests', Path(__file__).parents[1] / 'release_tests.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class ReleasePreflightTest(unittest.TestCase):
    def execute(self, kind, filename, body):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / filename).write_text('import unittest\nclass Fixture(unittest.TestCase):\n' + body)
            return RELEASE.run(kind, directory)

    def test_real_failure_and_skip_refuse_qualification(self):
        self.assertFalse(self.execute('native', 'test_process.py',
                                     '    def test_failed(self): self.fail("actual fixture failure")\n'))
        self.assertFalse(self.execute('native', 'test_process.py',
                                     '    @unittest.skip("missing native utility")\n    def test_skipped(self): pass\n'))

    def test_unknown_suite_remains_native(self):
        self.assertTrue(self.execute('native', 'test_new_process.py', '    def test_process(self): self.assertTrue(True)\n'))
        with self.assertRaisesRegex(ValueError, 'no release qualification'):
            self.execute('portable', 'test_new_process.py', '    def test_process(self): self.assertTrue(True)\n')

    def test_portable_metadata_suite_runs(self):
        self.assertTrue(self.execute('portable', 'test_artifacts.py', '    def test_policy(self): self.assertTrue(True)\n'))


class CodexConfigContractsTest(unittest.TestCase):
    def test_missing_contract_file_refuses_instead_of_empty_discovery(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(RELEASE, 'ROOT', Path(directory)), \
                patch.object(RELEASE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'missing Codex config contract'):
                RELEASE.codex_config_contracts()
        command.assert_not_called()

    def test_old_python_refuses_without_starting_or_skipping_contracts(self):
        with patch.object(RELEASE.sys, 'version_info', (3, 10)), patch.object(RELEASE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'Python 3.11'):
                RELEASE.codex_config_contracts()
        command.assert_not_called()

    def test_portable_and_fixed_selection_share_contracts_without_native_builds(self):
        for kind in ('portable', 'codex-config'):
            with self.subTest(kind=kind), patch.object(RELEASE.sys, 'argv', ['release_tests.py', kind]), \
                    patch.object(RELEASE, 'codex_config_contracts') as contracts, \
                    patch.object(RELEASE.subprocess, 'run'), patch.object(RELEASE, 'run', return_value=True) as fixtures:
                with self.assertRaises(SystemExit) as result:
                    RELEASE.main()
                self.assertEqual(result.exception.code, 0)
                contracts.assert_called_once_with()
                if kind == 'codex-config':
                    fixtures.assert_not_called()
                else:
                    fixtures.assert_called_once_with('portable', RELEASE.ROOT / 'scripts/release/tests')

    def test_contract_failure_stops_manual_portable_before_release_qualification(self):
        with patch.object(RELEASE.sys, 'argv', ['release_tests.py', 'portable']), \
                patch.object(RELEASE, 'codex_config_contracts', side_effect=subprocess.CalledProcessError(1, 'contract')), \
                patch.object(RELEASE, 'run') as fixtures:
            with self.assertRaises(subprocess.CalledProcessError): RELEASE.main()
        fixtures.assert_not_called()

    def test_required_application_step_uses_the_same_fixed_contract_selection(self):
        text = (RELEASE.ROOT / '.github/workflows/make-all.yml').read_text()
        block = text.split('      - name: Codex file and mount policy regressions\n', 1)[1].split('      - name:', 1)[0]
        self.assertIn("needs.changes.outputs.codex_config == 'true'", block)
        self.assertIn('python3 scripts/ci/release_tests.py codex-config', block)
        self.assertIn("if: needs.changes.outputs.readiness == 'true'\n        run: python3 runner/test_codex_readiness.py", text)
        portable = (RELEASE.ROOT / '.github/workflows/release-preflight.yml').read_text()
        self.assertIn('python3 scripts/ci/release_tests.py portable', portable)


class WorkflowGuardTest(unittest.TestCase):
    def guard(self, filename, name, values, outputs=False):
        root = Path(__file__).resolve().parents[3]
        text = (root / '.github/workflows' / filename).read_text()
        section = text.split('      - name: ' + name + '\n', 1)[1]
        script = section.split('        run: |\n', 1)[1].split('\n      - ', 1)[0]
        # Execute the actual workflow shell, including failure/cancellation checks.
        lines = []
        for line in script.splitlines():
            if line.strip() and not line.startswith('          '):
                break
            lines.append(line[10:])
        script = '\n'.join(lines)
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, GITHUB_SHA='a' * 40, GITHUB_STEP_SUMMARY=str(Path(directory) / 'summary'),
                       GITHUB_OUTPUT=str(Path(directory) / 'outputs'), **values)
            result = subprocess.run(['bash', '-e', '-c', script], cwd=directory, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if outputs:
                path = Path(env['GITHUB_OUTPUT'])
                values = dict(line.split('=', 1) for line in path.read_text().splitlines()) if path.exists() else {}
                return result.returncode, values
            return result.returncode

    def test_source_output_never_accepts_failed_missing_or_wrong_head(self):
        name = 'Require exact successful local or caller-owned preflight'
        for result in ('failure', 'cancelled', 'skipped', ''):
            self.assertNotEqual(self.guard('runner-release.yml', name,
                dict(LOCAL_RESULT=result, LOCAL_REVISION='a'*40, CALLER_REVISION='')), 0)
        self.assertEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='success', LOCAL_REVISION='a'*40, CALLER_REVISION='')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='success', LOCAL_REVISION='b'*40, CALLER_REVISION='')), 0)
        self.assertEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='skipped', LOCAL_REVISION='', CALLER_REVISION='a'*40)), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(LOCAL_RESULT='success', LOCAL_REVISION='a'*40, CALLER_REVISION='', PREVIOUS_REVISION='invalid')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='skipped', LOCAL_REVISION='', CALLER_REVISION='b'*40)), 0)

    def test_invalid_production_controller_mode_refuses_before_selector_fanout(self):
        mode = dict(PREPARE='true', EVENT='workflow_dispatch', REF='refs/heads/main',
                    ENGINE_ONLY='false', CONTROLLER_ONLY='true')
        self.assertNotEqual(self.guard('burrito-release.yml', 'Require manual main preparation', mode), 0)
        self.assertEqual(self.guard('burrito-release.yml', 'Require manual main preparation',
                                   dict(mode, PREPARE='false')), 0)

    def test_final_only_mode_omits_only_unconsumed_candidate_archives(self):
        good = dict(LOCAL_RESULT='success', LOCAL_REVISION='a'*40, CALLER_REVISION='',
                    PREPARE_RELEASE='true', COMPOSE_PACKAGES='true', PREVIOUS_ARTIFACT_RUN='',
                    LEGACY_ARTIFACT_RUN='', MANAGER_ONLY_UPDATE='false')
        name = 'Require exact successful local or caller-owned preflight'
        self.assertEqual(self.guard('runner-release.yml', name, good, outputs=True),
                         (0, {'final_only': 'true'}))
        for changed in ({'PREPARE_RELEASE': 'false'}, {'PREVIOUS_ARTIFACT_RUN': '123'},
                        {'LEGACY_ARTIFACT_RUN': '123', 'PREVIOUS_ARTIFACT_RUN': '124', 'MANAGER_ONLY_UPDATE': 'true'},
                        {'MANAGER_ONLY_UPDATE': 'true', 'PREVIOUS_ARTIFACT_RUN': '123'}):
            self.assertEqual(self.guard('runner-release.yml', name, dict(good, **changed), outputs=True),
                             (0, {'final_only': 'false'}))
        for changed in ({'COMPOSE_PACKAGES': 'false'}, {'LOCAL_RESULT': 'failure'}, {'PREVIOUS_REVISION': 'b'*40},
                        {'MANAGER_ONLY_UPDATE': 'true'}, {'LEGACY_ARTIFACT_RUN': '123'},
                        {'MANAGER_ONLY_UPDATE': 'true', 'LEGACY_ARTIFACT_RUN': '123', 'PREVIOUS_ARTIFACT_RUN': '123'}):
            code, output = self.guard('runner-release.yml', name, dict(good, **changed), outputs=True)
            self.assertNotEqual(code, 0)
            self.assertEqual(output, {})

    def test_final_package_failure_cannot_be_hidden_by_intentionally_skipped_candidates(self):
        good = dict(PREFLIGHT='success', PACKAGES='success', HAS_BUILDS='false', RUNNERS='skipped',
                    METADATA='success', BUNDLES='skipped', LIFECYCLE='skipped', PREVIOUS_REVISION='',
                    PREVIOUS_ARTIFACT_RUN='', FINAL_ONLY='true', FINAL_PACKAGES='success', PREPARE_RELEASE='true')
        name = 'Require all requested candidate work without accepting omitted failures'
        self.assertEqual(self.guard('runner-release.yml', name, good), 0)
        for changed in ({'FINAL_PACKAGES': 'failure'}, {'FINAL_PACKAGES': 'cancelled'},
                        {'FINAL_PACKAGES': 'skipped'}, {'BUNDLES': 'success'}, {'FINAL_ONLY': ''},
                        {'LIFECYCLE': 'success'}, {'METADATA': 'failure'}, {'PACKAGES': 'skipped'}):
            self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, **changed)), 0)
        lifecycle = dict(good, FINAL_ONLY='false', BUNDLES='success', PREVIOUS_ARTIFACT_RUN='123',
                         LIFECYCLE='success')
        self.assertEqual(self.guard('runner-release.yml', name, lifecycle), 0)
        for state in ('skipped', 'failure', 'cancelled', ''):
            self.assertNotEqual(self.guard('runner-release.yml', name, dict(lifecycle, LIFECYCLE=state)), 0)
        for state in ('skipped', 'failure', 'cancelled', ''):
            self.assertNotEqual(self.guard('runner-release.yml', name, dict(lifecycle, FINAL_PACKAGES=state)), 0)
        before = 'Require qualified component inputs before preparing final metadata'
        self.assertEqual(self.guard('runner-release.yml', before, dict(HAS_BUILDS='false', RUNNERS='skipped')), 0)
        for result in ('failure', 'cancelled', 'skipped', ''):
            self.assertNotEqual(self.guard('runner-release.yml', before, dict(HAS_BUILDS='true', RUNNERS=result)), 0)

    def test_controller_cache_diagnostic_confines_source_provider_and_all_other_modes(self):
        good=dict(LOCAL_RESULT='success',LOCAL_REVISION='a'*40,CALLER_REVISION='',CONTROLLER_COMPARISON='true',
                  CONTROLLER_REVISION='a'*40,GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_REF='refs/heads/dev',
                  GITHUB_REPOSITORY='Jayson-Stangel/symphony',GITHUB_ACTOR='Jayson-Stangel',GITHUB_TRIGGERING_ACTOR='Jayson-Stangel',
                  GITHUB_RUN_ATTEMPT='1',PREPARE_RELEASE='false',
                  COMPOSE_PACKAGES='false',MANAGER_ONLY_UPDATE='false',PACKAGED_PREPARATION='false',RETAIN_DIAGNOSTIC='true')
        name='Require exact successful local or caller-owned preflight'
        self.assertEqual(self.guard('runner-release.yml',name,good),0)
        for field,value in [('CONTROLLER_REVISION','b'*40),('GITHUB_REF','refs/heads/main'),('GITHUB_EVENT_NAME','push'),
                            ('GITHUB_ACTOR','foreign'),('GITHUB_TRIGGERING_ACTOR','foreign'),('GITHUB_RUN_ATTEMPT','2'),
                            ('CACHE_OVERLAP','true'),('COMPOSE_PACKAGES','true'),
                            ('PREPARE_RELEASE','true'),('PREVIOUS_ARTIFACT_RUN','123'),('LEGACY_ARTIFACT_RUN','123'),('BOOTSTRAP_PREVIOUS','proof.json'),
                            ('RESUME_NATIVE','123'),('BASELINE_RUN','123'),('COMPONENT_PLAN','artifact'),
                            ('DIAGNOSTIC_ARTIFACT_RUN','123'),('TRANSFER_REPLAY','123'),('TRANSFER_TIMING','123'),
                            ('TRANSFER_DIAGNOSTIC','123'),('PACKAGED_PREPARATION','true'),('RETAIN_DIAGNOSTIC','false')]:
            with self.subTest(field=field):self.assertNotEqual(self.guard('runner-release.yml',name,dict(good,**{field:value})),0)
        root=Path(__file__).resolve().parents[3]
        workflow=(root/'.github/workflows/runner-release.yml').read_text()
        diagnostic=workflow.split('  cache-controller-comparison:\n')[1].split('  packaged-candidate:',1)[0]
        self.assertIn('runs-on: ubuntu-24.04-arm',diagnostic)
        self.assertIn('strategy: [serial, overlap]',diagnostic)
        self.assertIn("'claude','arm64'",diagnostic)
        self.assertIn('Linux/aarch64',diagnostic)
        self.assertNotIn('include:',diagnostic)
        self.assertIn('timeout-minutes: 60',diagnostic)
        self.assertIn('scripts/release/build-candidate-runners.sh',diagnostic)
        self.assertIn('cache-only solve performed uncached build operations',diagnostic)
        for key in ('component-plan','candidate','candidate-metadata','package-inputs','candidate-qualification'):
            block=workflow.split('  '+key+':\n',1)[1].split('    steps:',1)[0]
            self.assertIn("inputs.cache_controller_revision == ''",block)

    def test_controller_cache_receipt_budget_and_uncached_work_fail_closed(self):
        import json
        root=Path(__file__).resolve().parents[3]
        workflow=(root/'.github/workflows/runner-release.yml').read_text()
        budget=workflow.split("          PYBUDGET\n",1)[0].rsplit("          python3 - <<'PYBUDGET'\n",1)[1]
        budget='\n'.join(line[10:] for line in budget.splitlines())
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder);receipts=directory/'controller-cache-overlap';receipts.mkdir()
            report=receipts/'measurement.json';report.write_text('{}')
            env=dict(os.environ,RUNNER_TEMP=folder,STRATEGY='overlap',GITHUB_OUTPUT=str(directory/'output'))
            result=subprocess.run(['python3','-c',budget],env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((directory/'output').read_text(),'ready=true\n')
            (directory/'output').unlink()
            with report.open('wb') as output:output.truncate(32*1024**2+1)
            result=subprocess.run(['python3','-c',budget],env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((directory/'output').exists())
            report.unlink();report.symlink_to(directory/'output')
            (directory/'output').write_text('{}')
            result=subprocess.run(['python3','-c',budget],env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            # Exercise the actual workflow's local-hit assertion, not a mirror.
            assertion=workflow.split('                  operations=',1)[1].split('          archives=',1)[0]
            assertion='operations='+assertion.replace('\n                  ','\n').rstrip()
            import re
            for cached,passes in ((True,True),(False,False)):
                raw=directory/'raw.jsonl';raw.write_text(json.dumps({'vertexes':[{'digest':'run','name':'[claude 2/3] RUN npm install'}]}))
                scope=dict(re=re,json=json,raw=raw,kind='export',timings={'export':{'intervals':[{'vertex_id':'run','cached':cached}]}})
                if passes:exec(assertion,scope)
                else:
                    with self.assertRaisesRegex(AssertionError,'uncached build operations'):exec(assertion,scope)
    def test_pending_bootstrap_requires_fresh_ubuntu_terminal_proof(self):
        values = dict(PREFLIGHT='success', PACKAGES='success', HAS_BUILDS='false', RUNNERS='skipped',
                      METADATA='success', BUNDLES='success', FINAL_ONLY='false', FINAL_PACKAGES='skipped',
                      PREPARE_RELEASE='false', LIFECYCLE='success', PREVIOUS_REVISION='', PREVIOUS_ARTIFACT_RUN='40588897389',
                      BOOTSTRAP_REQUESTED='true', BOOTSTRAP_PROOF='true')
        name = 'Require all requested candidate work without accepting omitted failures'
        self.assertEqual(self.guard('runner-release.yml', name, values), 0)
        for result in ('', 'false', 'failure', 'skipped'):
            self.assertNotEqual(self.guard('runner-release.yml', name, dict(values, BOOTSTRAP_PROOF=result)), 0)
        for result in ('failure', 'cancelled', 'skipped'):
            self.assertNotEqual(self.guard('runner-release.yml', name, dict(values, LIFECYCLE=result)), 0)

    def test_documentation_aggregate_requires_intentional_skips(self):
        name = 'Require every requested native and package-input qualification'
        docs = dict(NATIVE_REQUIRED='false', PORTABLE='success', PACKAGE='skipped')
        self.assertEqual(self.guard('package-checks.yml', name, docs), 0)
        for key in ('PORTABLE', 'PACKAGE'):
            self.assertNotEqual(self.guard('package-checks.yml', name, dict(docs, **{key: 'failure'})), 0)
        code = dict(NATIVE_REQUIRED='true', PORTABLE='success', PACKAGE='success')
        self.assertEqual(self.guard('package-checks.yml', name, code), 0)
        for key in code:
            if key != 'NATIVE_REQUIRED':
                self.assertNotEqual(self.guard('package-checks.yml', name, dict(code, **{key: 'skipped'})), 0)

    def test_terminal_requires_every_requested_native_package_and_image_result(self):
        name = 'Require all requested candidate work without accepting omitted failures'
        good = dict(HAS_BUILDS='true', PREFLIGHT='success', PACKAGES='success', RUNNERS='success', METADATA='success', BUNDLES='success', LIFECYCLE='success', PREVIOUS_REVISION='b'*40, FINAL_ONLY='false', FINAL_PACKAGES='skipped', PREPARE_RELEASE='false')
        self.assertEqual(self.guard('runner-release.yml', name, good), 0)
        self.assertEqual(self.guard('runner-release.yml', name, dict(good, HAS_BUILDS='false', RUNNERS='skipped')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, HAS_BUILDS='false')), 0)
        self.assertEqual(self.guard('runner-release.yml', name, dict(good, PREVIOUS_REVISION='', LIFECYCLE='skipped')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, PREVIOUS_REVISION='', LIFECYCLE='success')), 0)
        for key in good:
            if key in ('PREVIOUS_REVISION', 'FINAL_ONLY', 'FINAL_PACKAGES', 'PREPARE_RELEASE'):
                continue
            for result in ('failure', 'cancelled', 'skipped', ''):
                self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, **{key: result})), 0)


class RetainedDiagnosticContractTest(unittest.TestCase):
    def test_retention_crosses_reusable_workflow_boundary_explicitly(self):
        root = Path(__file__).resolve().parents[3]
        runner = (root / '.github/workflows/runner-release.yml').read_text()
        packages = (root / '.github/workflows/package-checks.yml').read_text()
        burrito = (root / '.github/workflows/burrito-release.yml').read_text()
        for text in (runner, packages):
            call = text.split('  workflow_call:', 1)[1].split('  pull_request:', 1)[0].split('  workflow_dispatch:', 1)[0]
            self.assertIn('      retain_diagnostic:', call)
            self.assertIn("inputs.retain_diagnostic && github.run_id || 'latest'", text)
        nested = runner.split('  package-inputs:', 1)[1].split('  qualification:', 1)[0]
        self.assertIn('retain_diagnostic: ${{ inputs.retain_diagnostic }}', nested)
        self.assertIn('retain_diagnostic: ${{ inputs.controller_only || inputs.retain_diagnostic }}', burrito)
        self.assertIn('previous_revision: ${{ inputs.previous_revision }}', burrito)
        self.assertIn('      previous_revision:', runner.split('  workflow_call:', 1)[1].split('  workflow_dispatch:', 1)[0])
        self.assertNotIn('inputs.controller_only', packages)
        integration = (root / '.github/workflows/runner-integration.yml').read_text()
        selected_runners = integration.split('  runners:\n', 1)[1].split('  manager-inputs:\n', 1)[0]
        self.assertIn("github.event_name == 'workflow_dispatch'", selected_runners)
        self.assertIn('retain_diagnostic: true', selected_runners)
        self.assertIn('compose_packages: false', selected_runners)
        self.assertIn('publish: false', selected_runners)


class ReusedPreflightSchedulingTest(unittest.TestCase):
    def test_fanout_joins_override_implicit_ancestor_skip_and_require_success(self):
        # A real caller-owned preflight leaves the local portable job skipped.
        # Actions' implicit success() can suppress its descendants even after
        # qualification succeeds. Every fanout/join needs an explicit status
        # function and direct successful artifact dependencies instead.
        root = Path(__file__).resolve().parents[3]
        import re
        expected = {
            'runner-release.yml': {
                'package-inputs': ['qualification'], 'component-plan': ['qualification'], 'candidate': ['qualification','component-plan'],
                'publication-preflight':['component-plan','candidate-metadata','package-inputs','qualification'],
                'candidate-metadata': ['candidate'], 'packaged-release-platform': ['publication-preflight'],
                'packaged-release': ['packaged-release-platform'],
                'packaged-candidate': ['candidate-metadata', 'package-inputs'],
                'packaged-candidate-metadata': ['packaged-candidate'],
                'ubuntu-package-lifecycle': ['packaged-candidate-metadata'],
                'lifecycle-diagnostic': ['qualification'], 'cache-controller-comparison': ['qualification']},
            'runner-integration.yml': {'manager-package': ['manager-inputs','helpers']},
            'current-manager-package.yml': {'package':['host-policy'],'metadata':['package']},
            'native-manager-helpers.yml': {'helper':['host-policy'],'qualification':['helper']},
            'package-checks.yml': {
                'native-host-package': ['changes', 'qualification', 'host-policy']}}
        for filename, jobs in expected.items():
            text = (root / '.github/workflows' / filename).read_text()
            for name, needed in jobs.items():
                with self.subTest(workflow=filename, job=name):
                    block = re.search(r'^  '+name+r':\n(?:(?!^  [a-z]).*\n)*', text, re.M).group(0)
                    condition = re.search(r'^    if: (.*)$', block, re.M).group(1)
                    self.assertIn('always()', condition)
                    self.assertIn('!cancelled()', condition)
                    for predecessor in needed:
                        self.assertIn('needs.'+predecessor+".result == 'success'", condition)


class EngineOnlyCoverageTest(unittest.TestCase):
    def test_engine_only_retains_native_controller_regressions_after_preflight(self):
        root = Path(__file__).resolve().parents[3]
        text = (root / '.github/workflows/burrito-release.yml').read_text()
        block = text.split('  build:\n', 1)[1].split('\n  controller-diagnostics:', 1)[0]
        self.assertIn('needs: [qualification, host-policy, mac-runner-build]', block)
        self.assertIn('if: inputs.engine_only', block)
        self.assertIn('python3 scripts/ci/release_tests.py native', block)
        self.assertNotIn('  engine-fixtures:', text)
        selector = text.split('  mac-runner-controller-diagnostics:\n', 1)[1]
        self.assertIn('needs: production-source', selector)
        self.assertIn("needs.production-source.result == 'success'", selector)
        self.assertIn('fetch-depth: 0', block)
        self.assertFalse((root / '.github/workflows/release-native.yml').exists())
        self.assertNotIn('test_engine_smoke.py', RELEASE.PORTABLE)
        self.assertIn('test_engine_smoke.py', [path.name for path in (root / 'scripts/release/tests').glob('test_*.py')])


class PriorHelperSchedulingTest(unittest.TestCase):
    def test_prior_capabilities_and_helpers_precede_image_artifact_joins(self):
        root = Path(__file__).resolve().parents[3]
        runner = (root / '.github/workflows/runner-release.yml').read_text()
        packages = (root / '.github/workflows/package-checks.yml').read_text()
        call = packages.split('  workflow_call:', 1)[1].split('    outputs:', 1)[0]
        self.assertIn('      previous_revision:', call)
        self.assertIn('previous_revision: ${{ inputs.previous_revision }}', runner.split('  package-inputs:',1)[1])
        qualification = runner.split('  qualification:',1)[1].split('  candidate:',1)[0]
        self.assertIn('verify-prior-source.py "$PREVIOUS_REVISION"', qualification)
        early = packages.split('      - name: Build prior helper early',1)[1].split('      - name: Retain actual exact-source',1)[0]
        self.assertIn('"$helper" --native', early)
        self.assertIn('build-linux-host-helper.sh', early)
        self.assertIn('prior-native-host-helper-${{ matrix.platform }}-${{ inputs.previous_revision }}-${{ github.run_id }}-${{ github.run_attempt }}', early)
        late = runner.split('      - name: Build source-bound native candidate archives',1)[1].split('      - uses:',1)[0]
        self.assertIn('qualified-prior-helper/symphony-host', late)
        self.assertNotIn('build-host-helper.sh', late)
        self.assertNotIn('build-linux-host-helper.sh', late)


class NativeHelperInvocationTest(unittest.TestCase):
    def test_actual_current_mac_helper_step_passes_native_to_the_builder(self):
        root = Path(__file__).resolve().parents[3]
        text = (root / '.github/workflows/package-checks.yml').read_text()
        block = text.split('      - name: Build actual native macOS ARM64 helper\n', 1)[1]
        body = block.split('        run: |\n', 1)[1].split('      - ', 1)[0]
        script = '\n'.join(line[10:] for line in body.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            bash = folder / 'bash'
            bash.write_text('#!/usr/bin/env python3\nimport json,os,pathlib,sys\npathlib.Path(os.environ["CALLS"]).write_text(json.dumps(sys.argv[1:]))\nassert sys.argv[3:] == ["--native"]\npathlib.Path(sys.argv[2]).write_text("native invocation fixture")\n')
            bash.chmod(0o755)
            env = dict(os.environ, PATH=str(folder)+os.pathsep+os.environ['PATH'], RUNNER_TEMP=str(folder), CALLS=str(folder/'calls'))
            subprocess.run(['/bin/bash', '-e', '-c', script], cwd=root, env=env, check=True)
            import json
            self.assertEqual(json.loads((folder/'calls').read_text()), ['scripts/release/build-host-helper.sh', str(folder/'host-helper/symphony-host'), '--native'])


if __name__ == '__main__':
    unittest.main()


class ComponentsOnlyManualTest(unittest.TestCase):
    def test_explicit_false_cannot_be_overridden_by_manual_caller_event(self):
        text=(Path(__file__).resolve().parents[3]/'.github/workflows/runner-release.yml').read_text()
        dispatch=text.split('  workflow_dispatch:',1)[1].split('permissions:',1)[0]
        self.assertIn('      compose_packages:',dispatch)
        self.assertNotIn("inputs.compose_packages || github.event_name",text)
        import re
        for name in ('package-inputs','packaged-candidate','candidate-qualification'):
            block=re.search(r'^  '+name+r':\n(?:(?!^  [a-z]).*\n)*',text,re.M).group()
            self.assertIn('inputs.compose_packages',re.search(r'^    if: (.*)$',block,re.M).group(1))


class FinalPackageGraphTest(unittest.TestCase):
    def test_final_cohort_join_is_acyclic_and_route_jobs_follow_real_consumers(self):
        import re
        text = (RELEASE.ROOT/'.github/workflows/runner-release.yml').read_text().split('jobs:\n', 1)[1]
        graph = {}
        blocks = dict((m.group(1), m.group(2)) for m in re.finditer(
            r'^  ([a-z][a-z0-9-]*):\n((?:(?!^  [a-z]).*\n)*)', text, re.M))
        for name, block in blocks.items():
            match = re.search(r'^    needs: (.+)$', block, re.M)
            graph[name] = [] if not match else [v.strip() for v in match[1].strip('[]').split(',')]
        checked, active = set(), set()
        def visit(name):
            self.assertIn(name, graph)
            self.assertNotIn(name, active, 'workflow dependency cycle')
            if name in checked:
                return
            active.add(name)
            for dependency in graph[name]:
                visit(dependency)
            active.remove(name); checked.add(name)
        for name in graph:
            visit(name)
        self.assertIn('packaged-release', graph['candidate-qualification'])
        self.assertNotIn('candidate-qualification', graph['publication-preflight'])
        self.assertIn("needs.qualification.outputs.final_only == 'false'", blocks['mac-runner-packaged-candidate'])
        self.assertEqual(graph['mac-runner-packaged-release-platform'], ['publication-preflight'])
        self.assertIn("needs.publication-preflight.result == 'success'", blocks['mac-runner-packaged-release-platform'])
        self.assertIn("verify_packages(Path('dist'), os.environ['GITHUB_SHA'])", blocks['packaged-release'])
