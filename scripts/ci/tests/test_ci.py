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
            self.assertIn('case "$CODE" in true|false)',text)
            self.assertNotIn('\n    paths:',text)
            if name == 'package-checks':
                # Package work moved into concurrent jobs. The named required
                # aggregate executes only status checks; all heavy jobs are gated.
                for heavy in ('portable', 'native', 'host-package'):
                    block = re.split(r'(?m)^  [a-zA-Z0-9_-]+:', text.split('  ' + heavy + ':\n', 1)[1], maxsplit=1)[0]
                    self.assertIn("if: needs.changes.outputs.code == 'true'", block)
                continue
            steps=text.split('    steps:\n',1)[1]
            # Every application/setup/test step is gated by the shared decision.
            for step in steps.split('      - ')[2:-1]:
                self.assertIn("if: needs.changes.outputs.code != 'false'",step)

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
