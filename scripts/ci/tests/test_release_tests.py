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
    def guard(self, filename, name, values):
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
            env = dict(os.environ, GITHUB_SHA='a' * 40, GITHUB_STEP_SUMMARY=str(Path(directory) / 'summary'), **values)
            return subprocess.run(['bash', '-e', '-c', script], cwd=directory, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode

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
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(LOCAL_RESULT='success', LOCAL_REVISION='a'*40, CALLER_REVISION='', CACHE_PROBE='true', DIAGNOSTIC_ARTIFACT_RUN='123')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name,
            dict(LOCAL_RESULT='skipped', LOCAL_REVISION='', CALLER_REVISION='b'*40)), 0)

    def test_invalid_production_controller_mode_refuses_before_selector_fanout(self):
        mode = dict(PREPARE='true', EVENT='workflow_dispatch', REF='refs/heads/main',
                    ENGINE_ONLY='false', CONTROLLER_ONLY='true')
        self.assertNotEqual(self.guard('burrito-release.yml', 'Require manual main preparation', mode), 0)
        self.assertEqual(self.guard('burrito-release.yml', 'Require manual main preparation',
                                   dict(mode, PREPARE='false')), 0)

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
        good = dict(HAS_BUILDS='true', PREFLIGHT='success', PACKAGES='success', RUNNERS='success', METADATA='success', BUNDLES='success', LIFECYCLE='success', PREVIOUS_REVISION='b'*40)
        self.assertEqual(self.guard('runner-release.yml', name, good), 0)
        self.assertEqual(self.guard('runner-release.yml', name, dict(good, HAS_BUILDS='false', RUNNERS='skipped')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, HAS_BUILDS='false')), 0)
        self.assertEqual(self.guard('runner-release.yml', name, dict(good, PREVIOUS_REVISION='', LIFECYCLE='skipped')), 0)
        self.assertNotEqual(self.guard('runner-release.yml', name, dict(good, PREVIOUS_REVISION='', LIFECYCLE='success')), 0)
        for key in good:
            if key == 'PREVIOUS_REVISION':
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
                'publication-preflight':['component-plan','candidate-metadata','candidate-qualification'],
                'candidate-metadata': ['candidate'], 'packaged-release-platform': ['publication-preflight'],
                'packaged-release': ['packaged-release-platform'],
                'packaged-candidate': ['candidate-metadata', 'package-inputs'],
                'packaged-candidate-metadata': ['packaged-candidate'],
                'ubuntu-package-lifecycle': ['packaged-candidate-metadata'],
                'lifecycle-diagnostic': ['qualification'], 'cache-probe': ['qualification']},
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
