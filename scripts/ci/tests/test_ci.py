import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]

def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/ci' / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

classifier = module('classify_changes')
validator = module('check_pr_body')


class EventReplay(unittest.TestCase):
    def test_real_git_events(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            def commit(path, content):
                p = Path(folder) / path; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q');git('config', 'user.name', 'Fixture');git('config', 'user.email', 'fixture@example.invalid')
            base = commit('README.md', 'before')
            first = commit('README.md', 'docs one')
            second = commit('docs/example.md', 'docs two')
            original = os.getcwd();os.chdir(folder)
            try:
                cases = []
                for target in ('dev', 'main'):
                    for head in (first, second):
                        event = {'pull_request': {'base': {'sha': base, 'ref': target}, 'head': {'sha': head}}}
                        cases.append((f'PR {target} {head[:7]}', 'pull_request', event, True))
                cases.append(('merge push', 'push', {'before': base, 'after': second}, True))
                code = commit('runner/entrypoint.sh', '#!/bin/sh\nexit 0')
                cases.append(('mixed cumulative PR', 'pull_request', {'pull_request': {'base': {'sha': base}, 'head': {'sha': code}}}, False))
                runtime = commit('elixir/WORKFLOW.md', 'runtime instruction')
                cases.append(('runtime Markdown', 'push', {'before': code, 'after': runtime}, False))
                skill = commit('.codex/skills/push/SKILL.md', 'agent input')
                cases.append(('packaged skill', 'push', {'before': runtime, 'after': skill}, False))
                git('mv', 'README.md', 'new-runtime.sh');git('commit', '-qm', 'rename')
                rename = git('rev-parse','HEAD')
                cases.append(('rename docs to code', 'push', {'before': skill, 'after': rename}, False))
                git('rm', 'new-runtime.sh');git('commit', '-qm', 'delete')
                deletion = git('rev-parse','HEAD')
                cases.append(('delete code', 'push', {'before': rename, 'after': deletion}, False))
                git('rm', 'docs/example.md');git('commit','-qm','delete docs')
                docs_delete = git('rev-parse','HEAD')
                cases.append(('delete docs', 'push', {'before': deletion, 'after': docs_delete}, True))
                git('checkout','-qb','stale-base',base)
                stale = commit('other-runtime.py', 'behavior')
                cases.append(('stale base includes feature code', 'pull_request', {'pull_request': {'base': {'sha': stale}, 'head': {'sha': code}}}, False))
                cases.append(('missing history', 'push', {'before': 'f'*40, 'after': second}, False))
                cases.append(('new branch', 'push', {'before': '0'*40, 'after': second}, False))
                for label, name, event, expected in cases:
                    with self.subTest(label=label):
                        start=time.monotonic();docs,reason,paths=classifier.classify(event,name)
                        self.assertEqual(docs,expected,(reason,paths))
                        print(json.dumps({'event':label,'docs_only':docs,'elapsed_seconds':round(time.monotonic()-start,6),'paths':paths,
                            'behavioral_steps':'intentionally omitted' if docs else 'required (not executed by this classifier replay)'}))
            finally:
                os.chdir(original)

    def test_unknown_and_operational_paths(self):
        for path in ('examples/run.sh','docs/tool.py','WORKFLOW.md','elixir/WORKFLOW.md','.agents/skills/a.md','.codex/skills/a.md','unknown.md','../README.md'):
            self.assertFalse(classifier.documentation(path),path)
        for path in ('README.md','AGENTS.md','docs/example.md','elixir/docs/logging.md'):
            self.assertTrue(classifier.documentation(path),path)

    def test_failed_discovery_is_code(self):
        for event,name in [({},'push'),({},'workflow_dispatch'),({'before':'x','after':'y'},'push')]:
            self.assertFalse(classifier.classify(event,name)[0])


class BrowserRouting(unittest.TestCase):
    def test_real_event_ranges(self):
        with tempfile.TemporaryDirectory() as folder:
            def git(*args):
                return subprocess.check_output(['git', '-C', folder, *args], stderr=subprocess.PIPE).decode().strip()
            recipe = ('FROM example/base@sha256:' + 'a' * 64 + ' AS browser-tools\n'
                      'COPY runner/browser/package*.json /browser/\nCOPY runner/browser/smoke.cjs /browser/\nRUN npm ci\n')
            def commit(path, content='fixture'):
                p = Path(folder) / path
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
                git('add', '--all'); git('commit', '-qm', 'fixture')
                return git('rev-parse', 'HEAD')
            git('init', '-q'); git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            commit('runner/Dockerfile', recipe)
            base = commit('README.md')
            original = os.getcwd(); os.chdir(folder)
            try:
                previous = base
                relevant = [
                    'runner/browser/package-lock.json', 'runner/browser/smoke.cjs',
                    'runner/Dockerfile', '.dockerignore', 'runner/Dockerfile.dockerignore',
                    '.github/actions/build-runner/action.yml',
                    'scripts/release/runner-cache-policy.sh', 'runner/node-smoke.sh',
                    '.github/workflows/browser-smoke.yml',
                    '.github/workflows/change-classification.yml',
                    'scripts/ci/classify_changes.py', 'scripts/ci/tests/test_browser_scope.py',
                ]
                for path in ['elixir/lib/app.ex', 'docs/guide.md'] + relevant:
                    content = recipe.replace('RUN npm ci', 'RUN npm ci --ignore-scripts') if path == 'runner/Dockerfile' else 'fixture'
                    head = commit(path, content)
                    event = {'before': previous, 'after': head}
                    paths = classifier.changed_paths(event, 'push')
                    self.assertEqual(classifier.browser_required(paths, event, 'push'), path in relevant, path)
                    previous = head
                # Full mixed PR retains browser changes behind a later app commit.
                head = commit('elixir/lib/later.ex')
                for target in ('dev', 'main'):
                    event = {'pull_request': {'base': {'sha': base, 'ref': target}, 'head': {'sha': head}}}
                    self.assertTrue(classifier.browser_required(classifier.classify(event, 'pull_request')[2], event, 'pull_request'))
                # Exact push excludes earlier browser commits.
                event = {'before': previous, 'after': head}
                self.assertFalse(classifier.browser_required(classifier.changed_paths(event, 'push'), event, 'push'))
                git('mv', 'runner/browser/smoke.cjs', 'unrelated.cjs'); git('commit', '-qm', 'rename')
                renamed = git('rev-parse', 'HEAD')
                event = {'before': head, 'after': renamed}
                self.assertTrue(classifier.browser_required(classifier.changed_paths(event, 'push'), event, 'push'))
                git('rm', 'runner/browser/package-lock.json'); git('commit', '-qm', 'delete')
                deleted = git('rev-parse', 'HEAD')
                event = {'before': renamed, 'after': deleted}
                self.assertTrue(classifier.browser_required(classifier.changed_paths(event, 'push'), event, 'push'))
                for before in ('f'*40, '0'*40, 'invalid', deleted):
                    event = {'before': before, 'after': deleted}
                    self.assertTrue(classifier.browser_required(classifier.classify(event, 'push')[2], event, 'push'))
                git('checkout', '-qb', 'stale', base)
                stale = commit('other.ex')
                event = {'pull_request': {'base': {'sha': stale}, 'head': {'sha': head}}}
                self.assertTrue(classifier.browser_required(classifier.classify(event, 'pull_request')[2], event, 'pull_request'))
            finally:
                os.chdir(original)

    def test_actual_browser_wrapper(self):
        text = (ROOT / '.github/workflows/browser-smoke.yml').read_text()
        steps = text.split('      - ')[1:]
        guard = steps[0].split('        run: |\n')[1]
        guard = '\n'.join(line[10:] for line in guard.splitlines() if line.strip())
        # Browser qualification remains gated for both decisions, including checkout/action.
        for step in steps[1:4]:
            self.assertIn("if: needs.changes.outputs.code == 'true' && needs.changes.outputs.browser == 'true'", step)
        self.assertIn("needs.changes.outputs.browser == 'false'", steps[4])
        self.assertIn('intentionally omitted', steps[4])
        self.assertIn("needs.changes.outputs.code == 'false'", steps[5])
        self.assertIn('intentionally omitted, not executed', steps[5])
        for result in ('success', 'failure', 'cancelled', 'skipped', ''):
            for code in ('true', 'false', '', 'invalid'):
                for browser in ('true', 'false', '', 'invalid'):
                    actual = subprocess.run(['bash', '-e', '-c', guard], env=dict(os.environ, RESULT=result, CODE=code, BROWSER=browser))
                    valid = code in ('true', 'false') and browser in ('true', 'false') and (code == 'true' or browser == 'false')
                    self.assertEqual(actual.returncode == 0, result == 'success' and valid)
        self.assertIn('--user 11001:11001 --entrypoint node', text)
        self.assertIn('--entrypoint node symphony-browser-test', text)
        # Candidate consumers continue to run actual-image qualification.
        release = (ROOT / '.github/workflows/runner-release.yml').read_text()
        smoke = (ROOT / 'scripts/release/runner-smoke.sh').read_text()
        self.assertNotIn('outputs.browser', release)
        self.assertIn('smoke.cjs', smoke)

    def test_cli_publishes_both_decisions(self):
        workflow = (ROOT / '.github/workflows/change-classification.yml').read_text()
        self.assertIn('value: ${{ jobs.classify.outputs.browser }}', workflow)
        self.assertIn('browser: ${{ steps.changes.outputs.browser }}', workflow)
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / 'event'; event.write_text('{}')
            output = Path(folder) / 'output'
            summary = Path(folder) / 'summary'
            subprocess.run(['python3', str(ROOT / 'scripts/ci/classify_changes.py')],
                env=dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME='push',
                         GITHUB_OUTPUT=str(output), GITHUB_STEP_SUMMARY=str(summary)),
                capture_output=True, check=True)
            self.assertEqual(output.read_text(), 'code=true\nbrowser=true\n')
            self.assertIn('Browser qualification required: **true**', summary.read_text())

    def test_malformed_event_fails_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / 'event'; event.write_text('{')
            output = Path(folder) / 'output'
            result = subprocess.run(['python3', str(ROOT / 'scripts/ci/classify_changes.py')],
                env=dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME='push', GITHUB_OUTPUT=str(output)),
                capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())


class Metadata(unittest.TestCase):
    def setUp(self):
        self.template=(ROOT/'.github/pull_request_template.md').read_text()
        self.body='#### Context\n\nA reason.\n\n#### TL;DR\n\n*A change.*\n\n#### Summary\n\n- Detail\n\n#### Alternatives\n\n- Keep old behavior\n\n#### Test Plan\n\n- [x] Checks passed\n'

    def test_valid(self):
        self.assertEqual(validator.lint(self.template,self.body),[])

    def test_rejects_bad_metadata(self):
        for body in ('',self.body.replace('#### Context','#### Missing'),self.body.replace('- [x] Checks passed','untested'),self.body+'<!-- placeholder -->',self.body.replace('A reason.','')):
            self.assertTrue(validator.lint(self.template,body))


class WorkflowContracts(unittest.TestCase):
    def test_required_wrappers_and_no_workflow_path_filters(self):
        for name,job in [('make-all','make-all'),('package-checks','macos-package'),('runner-integration','listener-and-cleanup'),('browser-smoke','chromium')]:
            text=(ROOT/'.github/workflows'/(name+'.yml')).read_text()
            self.assertIn('  '+job+':',text)
            self.assertIn('if: ${{ always() }}',text)
            self.assertIn('test "$RESULT" = success',text)
            self.assertIn('case "$CODE" in',text)
            self.assertNotIn('\n    paths:',text)
            if name == 'package-checks':
                # Package work moved into concurrent jobs. The named required
                # aggregate executes only status checks; all heavy jobs are gated.
                for heavy in ('portable', 'native', 'native-host-package'):
                    block = re.split(r'(?m)^  [a-zA-Z0-9_-]+:', text.split('  ' + heavy + ':\n', 1)[1], maxsplit=1)[0]
                    self.assertIn("needs.changes.outputs.code == 'true'", block)
                continue
            if name == 'runner-integration':
                self.assertIn("if: needs.plan.outputs.has_builds == 'true'", text)
                steps=text.split('  listener-and-cleanup:\n',1)[1].split('    steps:\n',1)[1]
            else:
                steps=text.split('    steps:\n',1)[1]
            # Every application/setup/test step is gated by the shared decision.
            for step in steps.split('      - ')[2:-1]:
                if name == 'browser-smoke':
                    self.assertIn("if: needs.changes.outputs.code == 'true' && needs.changes.outputs.browser ==", step)
                elif name == 'runner-integration':
                    self.assertIn("needs.changes.outputs.code == 'true'", step)
                else:
                    self.assertIn("if: needs.changes.outputs.code != 'false'",step)

    def test_package_matrix_and_actual_required_wrapper(self):
        text=(ROOT/'.github/workflows/package-checks.yml').read_text()
        def job(name):
            start=text.index('  '+name+':\n')
            remaining=text[start:]
            boundary=re.search(r'\n  [a-z][a-z0-9-]*:',remaining)
            return remaining[:boundary.start()] if boundary else remaining
        for name in ('native-host-package',):
            block=job(name)
            self.assertIn('    needs: [changes, qualification, host-policy]\n',block)
            self.assertIn("needs.changes.outputs.code == 'true'",block)
        self.assertIn('matrix: ${{ fromJSON(needs.host-policy.outputs.matrix) }}',job('native-host-package'))
        self.assertNotIn('macos-15-intel', text)
        self.assertIn('workflow_dispatch:',text)
        self.assertIn('previous_revision:',text)
        wrapper=job('macos-package')
        self.assertIn('needs: [changes, qualification, native, native-host-package]',wrapper)
        self.assertIn('if: ${{ always() }}',wrapper)
        self.assertIn('behavioral checks intentionally omitted, not executed.',wrapper)
        # Run both actual literal scripts, preserving bash -e semantics. A
        # classification failure cannot be hidden by skipped native jobs, and
        # a code decision requires every native result to be successful.
        scripts=[]
        for body in wrapper.split('        run: |\n')[1:]:
            lines=[]
            for line in body.splitlines():
                if line and not line.startswith('          '): break
                lines.append(line[10:])
            scripts.append('\n'.join(lines))
        self.assertEqual(len(scripts),2)
        statuses=('success','failure','cancelled','skipped','')
        with tempfile.TemporaryDirectory() as folder:
            for result,code in (('success','true'),('success','false'),('failure','false'),('cancelled','true'),('skipped','false'),('success',''),('success','invalid')):
                for mac in statuses:
                    for linux in statuses:
                        env=dict(os.environ,RESULT=result,CODE=code,PORTABLE='success',NATIVE=linux,PACKAGE=mac,GITHUB_STEP_SUMMARY=str(Path(folder)/'summary'))
                        command=scripts[0]+'\n'+scripts[1]
                        actual=subprocess.run(['bash','-e','-c',command],cwd=folder,env=env,capture_output=True,text=True)
                        expected=result=='success' and code in ('true','false') and (mac==linux==('skipped' if code=='false' else 'success'))
                        self.assertEqual(actual.returncode==0,expected,(result,code,mac,linux,actual.stderr))

    def test_actual_wrapper_guard_from_empty_workspace(self):
        text=(ROOT/'.github/workflows/make-all.yml').read_text()
        self.assertNotIn('    defaults:',text)
        guard=text.split('        run: |\n',1)[1].split('      - ',1)[0]
        guard='\n'.join(line[10:] for line in guard.splitlines() if line.strip())
        with tempfile.TemporaryDirectory() as folder:
            for result,code,expected in [('success','false',0),('success','true',0),('failure','false',1),('cancelled','false',1),('skipped','false',1),('success','',1)]:
                command=subprocess.run(['bash','-e','-c',guard],cwd=folder,env=dict(os.environ,RESULT=result,CODE=code))
                self.assertEqual(command.returncode==0,expected==0,(result,code))
            summary=Path(folder)/'summary'
            command='echo "Documentation-only event; behavioral checks intentionally omitted, not executed." >> "$GITHUB_STEP_SUMMARY"'
            subprocess.run(['bash','-e','-c',command],cwd=folder,env=dict(os.environ,GITHUB_STEP_SUMMARY=str(summary)),check=True)
            self.assertIn('not executed',summary.read_text())
            # A real failing behavioral command must remain failing: wrappers do
            # not use continue-on-error or a trailing blanket green step.
            failure=subprocess.run(['bash','-e','-c',guard+'\nfalse\necho unreachable'],cwd=folder,env=dict(os.environ,RESULT='success',CODE='true'),capture_output=True,text=True)
            self.assertNotEqual(failure.returncode,0)
            self.assertNotIn('unreachable',failure.stdout)

    def test_metadata_requires_no_toolchain(self):
        text=(ROOT/'.github/workflows/pr-description-lint.yml').read_text()
        for forbidden in ('mise','mix ', 'deps.get','install:','npm','pip '):
            self.assertNotIn(forbidden,text)
        self.assertIn('check_pr_body.py --event',text)


if __name__ == '__main__':
    unittest.main()
