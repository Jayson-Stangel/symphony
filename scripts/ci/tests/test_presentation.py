"""Real YAML parsing and Git comparisons prove display-only omissions."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_ci import ROOT, classifier
from test_components import historical_selector_workflow

WORKFLOW = 'name: Old workflow\non:\n  pull_request:\njobs:\n  check:\n    name: Old job\n    runs-on: ubuntu-24.04\n    steps:\n      - name: Old step\n        run: echo true\n'
ACTION = 'name: Old action\ndescription: Stable\nruns:\n  using: composite\n  steps:\n    - name: Old step\n      shell: bash\n      run: echo true\n'


SELECTOR_REVERSALS = {'make-all': [('          test "$RESULT" = success\n'
               '          case "$CODE" in true|false) ;; *) exit 1 ;; esac\n',
               '          test "$RESULT" = success\n'
               '          test "$MAC_RUNNER_RESULT" = success\n'
               '          case "$CODE" in true|false) ;; *) exit 1 ;; esac\n'),
              ('          if [ "$NPM" = true ] || [ "$CONTRACTS" = true ]; then\n'
               '            test "$MAC_RUNNER_RESULT" = success\n'
               '            test "$PROCESS_RESULT" = success\n',
               '          if [ "$NPM" = true ] || [ "$CONTRACTS" = true ]; then\n'
               '            test "$PROCESS_RESULT" = success\n'),
              ('          else\n'
               '            test "$MAC_RUNNER_RESULT" = skipped\n'
               '            test "$PROCESS_RESULT" = skipped\n',
               '          else\n            test "$PROCESS_RESULT" = skipped\n'),
              ('    needs: changes\n'
               "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' && "
               "(needs.changes.outputs.npm == 'true' || needs.changes.outputs.contracts == 'true') "
               '}}\n'
               '    runs-on: ubuntu-24.04\n',
               '    needs: changes\n'
               "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' }}\n"
               '    runs-on: ubuntu-24.04\n')],
 'package-checks': [('    needs: [changes, qualification, host-policy, mac-runner]\n'
                     "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' && "
                     "needs.qualification.result == 'success' && needs.host-policy.result == "
                     "'success' && (((needs.changes.outputs.package_macos == 'true' || "
                     "needs.changes.outputs.fixtures == 'true') && needs.mac-runner.result == "
                     "'success') || (needs.changes.outputs.package_macos == 'false' && "
                     "needs.changes.outputs.fixtures == 'false' && needs.mac-runner.result == "
                     "'skipped')) && needs.changes.outputs.native == 'true' && "
                     "needs.changes.outputs.ready == 'true' }}\n"
                     '    strategy:\n',
                     '    needs: [changes, qualification, host-policy, mac-runner]\n'
                     "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' && "
                     "needs.qualification.result == 'success' && needs.host-policy.result == "
                     "'success' && needs.mac-runner.result == 'success' && "
                     "needs.changes.outputs.native == 'true' && needs.changes.outputs.ready == "
                     "'true' }}\n"
                     '    strategy:\n'),
                    ('          test "$RESULT" = success\n'
                     '          for flag in "$CODE" "$PORTABLE_REQUIRED" "$MACOS" "$LINUX" '
                     '"$FIXTURES" "$NATIVE_REQUIRED" "$READY"; do\n',
                     '          test "$RESULT" = success\n'
                     '          test "$MAC_RUNNER_RESULT" = success\n'
                     '          for flag in "$CODE" "$PORTABLE_REQUIRED" "$MACOS" "$LINUX" '
                     '"$FIXTURES" "$NATIVE_REQUIRED" "$READY"; do\n'),
                    ('          test "$PLATFORM" = "$expected_platform"\n'
                     '          if [ "$macos" = true ] && [ "$READY" = true ]; then\n'
                     '            test "$MAC_RUNNER_RESULT" = success\n'
                     '          else\n'
                     '            test "$MAC_RUNNER_RESULT" = skipped\n'
                     '          fi\n'
                     '          if [ "$CODE" = false ]; then\n',
                     '          test "$PLATFORM" = "$expected_platform"\n'
                     '          if [ "$CODE" = false ]; then\n'),
                    ('    needs: [changes, qualification, host-policy]\n'
                     "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' && "
                     "needs.qualification.result == 'success' && needs.host-policy.result == "
                     "'success' && needs.changes.outputs.native == 'true' && "
                     "needs.changes.outputs.ready == 'true' && "
                     "(needs.changes.outputs.package_macos == 'true' || "
                     "needs.changes.outputs.fixtures == 'true') }}\n"
                     '    runs-on: ubuntu-24.04\n',
                     '    needs: [changes, qualification, host-policy]\n'
                     "    if: ${{ always() && !cancelled() && needs.changes.result == 'success' && "
                     "needs.qualification.result == 'success' && (needs.host-policy.result == "
                     "'success' || (needs.changes.outputs.native == 'false' && "
                     "needs.host-policy.result == 'skipped')) }}\n"
                     '    runs-on: ubuntu-24.04\n')]}


class PresentationOnly(unittest.TestCase):
    def test_added_deleted_audited_workflows_parse_existing_side_and_remain_behavioral(self):
        path = '.github/workflows/macos-local-signing-import.yml'
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            git('init', '-q'); git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
            target = Path(folder) / path
            target.parent.mkdir(parents=True)
            recipe = Path(folder) / 'runner/Dockerfile'
            recipe.parent.mkdir()
            recipe.write_text('FROM example/base@sha256:' + 'a' * 64 +
                              ' AS browser-tools\nCOPY runner/browser/ /browser/\n')
            git('add', '--all'); git('commit', '-qm', 'baseline')
            base = git('rev-parse', 'HEAD')
            target.write_text(WORKFLOW)
            git('add', '--all'); git('commit', '-qm', 'add audited workflow')
            added = git('rev-parse', 'HEAD')
            target.unlink()
            git('add', '--all'); git('commit', '-qm', 'delete audited workflow')
            deleted = git('rev-parse', 'HEAD')
            target.write_text('malformed: [')
            git('add', '--all'); git('commit', '-qm', 'malformed addition')
            malformed = git('rev-parse', 'HEAD')
            original = os.getcwd(); os.chdir(folder)
            try:
                for before, after in ((base, added), (added, deleted)):
                    event = dict(before=before, after=after)
                    paths = classifier.changed_paths(event, 'push')
                    route, _, _, presentation, fallback = classifier.event_routing(paths, event, 'push')
                    self.assertEqual({key for key in classifier.COMPONENTS if route[key]}, {'portable'})
                    self.assertFalse(presentation or fallback)
                    self.assertFalse(classifier.browser_required(paths, event, 'push'))
                event = dict(before=deleted, after=malformed)
                route, _, _, _, fallback = classifier.event_routing([path], event, 'push')
                self.assertTrue(all(route[key] for key in classifier.COMPONENTS))
                self.assertTrue(fallback)
                self.assertTrue(classifier.browser_required([path], event, 'push'))
            finally:
                os.chdir(original)

    def compare(self, path, before, after):
        result = subprocess.run(['ruby', str(ROOT/'scripts/ci/presentation_only.rb')],
                                input=json.dumps(dict(path=path, before=before, after=after)), text=True, capture_output=True)
        return result.returncode == 0 and json.loads(result.stdout)['presentation_only']

    def test_finite_selector_policy_rejects_unknown_jobs_globals_and_payloads(self):
        # Frozen pre-selector controls remain the reviewed finite baseline.
        for name in ('make-all', 'package-checks'):
            path = '.github/workflows/' + name + '.yml'
            after = historical_selector_workflow(name)
            before = after
            for current, original in SELECTOR_REVERSALS[name]:
                self.assertIn(current, before)
                before = before.replace(current, original, 1)
            def policy(value):
                result = subprocess.run(['ruby', str(ROOT / 'scripts/ci/presentation_only.rb')],
                    input=json.dumps(dict(path=path, before=before, after=value)), text=True, capture_output=True)
                return result.returncode == 0 and json.loads(result.stdout).get('mac_selector_policy_only') is True
            self.assertTrue(policy(after), name)
            # Persistent routing is new behavior, outside the historical audit.
            self.assertFalse(policy((ROOT / path).read_text()), name)
            for altered in (after.replace('permissions:', 'unknown-global: true\npermissions:', 1),
                    after.replace('  pull_request:', '  unknown_event:', 1),
                    after + '\n  unknown-job:\n    runs-on: ubuntu-24.04\n    steps: [{run: "true"}]\n',
                    after.replace('timeout-minutes: 8', 'timeout-minutes: 9', 1),
                    after.replace('run: /usr/bin/python3 -B scripts/ci/elixir_coverage.py execute', 'run: echo unreviewed', 1)
                        if name == 'make-all' else after.replace('scripts/package/', 'scripts/unknown-package/', 1)):
                self.assertNotEqual(altered, after)
                self.assertFalse(policy(altered), (name, altered))

    def test_names_only_across_workflows_and_composite_actions(self):
        for path in ('make-all', 'package-checks', 'change-classification', 'runner-integration', 'pr-description-lint', 'host-policy'):
            self.assertTrue(self.compare('.github/workflows/'+path+'.yml', WORKFLOW, WORKFLOW.replace('Old', 'New')), path)
        self.assertTrue(self.compare('.github/actions/build-runner/action.yml', ACTION, ACTION.replace('Old', 'New')))

    def test_semantic_differences_and_ambiguous_yaml_never_grant_omission(self):
        changed = WORKFLOW.replace('Old', 'New')
        for after in (changed.replace('echo true', 'echo false'), changed.replace('ubuntu-24.04', 'macos-14'),
                      changed.replace('run: echo true', "if: false\n        run: echo true"),
                      changed.replace('on:', 'true:'), changed.replace('echo true', 'true'),
                      changed.replace('run: echo true', 'run: "true"'),
                      changed.replace('on:', 'permissions: write-all\non:'),
                      changed.replace('    runs-on:', '    needs: other\n    runs-on:'),
                      changed.replace('    steps:', '    strategy: {matrix: {runner: [macos-14]}}\n    steps:'),
                      changed.replace('        run: echo true', '        uses: actions/checkout@other'),
                      changed.replace('jobs:', 'jobs: {duplicate: true}\njobs:'),
                      changed.replace('run: echo true', 'run: &command echo true'),
                      changed+'invalid: *command\n', changed+'---\nname: other\n', 'malformed: ['):
            # Anchor declarations have no semantic effect until referenced; that
            # spelling is treated conservatively too by the parser below.
            self.assertFalse(self.compare('.github/workflows/make-all.yml', WORKFLOW, after), after)
        for filename in ('runner-release.yml', 'native-manager-helpers.yml', 'current-manager-package.yml'):
            self.assertFalse(self.compare('.github/workflows/'+filename, WORKFLOW, changed))
        reusable = WORKFLOW.replace('    runs-on: ubuntu-24.04', '    uses: ./.github/workflows/host-policy.yml')
        self.assertFalse(self.compare('.github/workflows/runner-integration.yml', reusable, reusable.replace('Old', 'New')))

    def test_real_history_attributes_each_consumer_and_names_only_omissions(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git','-C',folder,*args], stderr=subprocess.PIPE).decode().strip()
            def commit(files):
                for path, content in files.items():
                    target=Path(folder)/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
                git('add','--all');git('commit','-qm','fixture')
                return git('rev-parse','HEAD')
            git('init','-q');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
            base=commit({'.github/workflows/package-checks.yml':WORKFLOW, 'runner/Dockerfile':
                'FROM example/base@sha256:'+'a'*64+' AS browser-tools\nCOPY runner/browser/package.json /opt/browser/\n'})
            named=commit({'.github/workflows/package-checks.yml':WORKFLOW.replace('Old','New')})
            original=os.getcwd();os.chdir(folder)
            try:
                event=dict(before=base,after=named)
                route,triggers,active,presentation,fallback=classifier.event_routing(classifier.changed_paths(event,'push'),event,'push')
                self.assertFalse(any(route[key] for key in classifier.COMPONENTS))
                self.assertEqual(presentation,['.github/workflows/package-checks.yml']);self.assertEqual(active,[])
                event_file=Path(folder)/'event.json';event_file.write_text(json.dumps(event))
                output=Path(folder)/'outputs';summary=Path(folder)/'summary'
                result=subprocess.run(['python3',str(ROOT/'scripts/ci/classify_changes.py')],
                    env=dict(os.environ,GITHUB_EVENT_NAME='push',GITHUB_EVENT_PATH=str(event_file),
                             GITHUB_OUTPUT=str(output),GITHUB_STEP_SUMMARY=str(summary)),capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                outputs=dict(line.split('=',1) for line in output.read_text().splitlines())
                for key in ('code','browser','native',*classifier.COMPONENTS):self.assertEqual(outputs[key],'false',key)
                self.assertEqual(outputs['policy'],'true')
                self.assertIn('no relevant changes',summary.read_text())
                self.assertIn('Verified presentation-only files',summary.read_text())
                for generated in (event_file,output,summary):generated.unlink()
                prose=commit({'README.md':'human prose'})
                event_file.write_text(json.dumps(dict(before=named,after=prose)))
                output.write_text('')
                result=subprocess.run(['python3',str(ROOT/'scripts/ci/classify_changes.py')],
                    env=dict(os.environ,GITHUB_EVENT_NAME='push',GITHUB_EVENT_PATH=str(event_file),
                             GITHUB_OUTPUT=str(output)),capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                outputs=dict(line.split('=',1) for line in output.read_text().splitlines())
                self.assertEqual(outputs['policy'],'false')
                self.assertEqual(outputs['code'],'false')
                event_file.unlink();output.unlink()
                mixed=commit({'scripts/package/linux/Package.resolved':'SDK change','elixir/lib/app.ex':'source change'})
                event=dict(before=base,after=mixed)
                route,triggers,active,presentation,fallback=classifier.event_routing(classifier.changed_paths(event,'push'),event,'push')
                self.assertEqual({item['path'] for item in triggers['application']},{'elixir/lib/app.ex'})
                self.assertEqual({item['path'] for item in triggers['package_linux']},{'scripts/package/linux/Package.resolved'})
                self.assertEqual(triggers['package_macos'],[])
                self.assertIn('SDK',triggers['package_linux'][0]['rule'])
                self.assertEqual(presentation,['.github/workflows/package-checks.yml'])
                shared=commit({'scripts/ci/classify_changes.py':'changed shared control'})
                event=dict(before=mixed,after=shared)
                route,triggers,*_=classifier.event_routing(classifier.changed_paths(event,'push'),event,'push')
                self.assertEqual({key for key in classifier.COMPONENTS if route[key]}, {'portable'})
                self.assertEqual(triggers['portable'], [{'path': 'scripts/ci/classify_changes.py',
                    'rule': 'known CI selection policy; unconditional policy regressions and portable workflow contracts'}])
                event=dict(before=base,after=named)
                paths=classifier.changed_paths(event,'push')
                with patch.object(classifier.subprocess,'run',side_effect=OSError('no parser')):
                    route,triggers,_,presentation,fallback=classifier.event_routing(paths,event,'push')
                    self.assertTrue(route['package_macos']);self.assertFalse(presentation);self.assertTrue(fallback)
                event=dict(before='f'*40,after=named)
                route,_,_,_,fallback=classifier.event_routing([],event,'push')
                self.assertTrue(all(route[key] for key in classifier.COMPONENTS));self.assertTrue(fallback)
            finally:os.chdir(original)
