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
                    '.github/workflows/runner-integration.yml',
                    '.github/workflows/change-classification.yml',
                    'scripts/ci/unknown-browser-control.py',
                ]
                irrelevant = [
                    'elixir/lib/app.ex', 'docs/guide.md', 'scripts/ci/tests/test_browser_routing.py',
                    'scripts/ci/classify_changes.py', 'scripts/ci/presentation_only.rb',
                    'scripts/release/package-lifecycle.py',
                ]
                for path in irrelevant + relevant:
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
        text = (ROOT / '.github/workflows/runner-integration.yml').read_text().split('  chromium:\n', 1)[1].split('  portable:\n', 1)[0]
        steps = text.split('      - ')[1:]
        guard = steps[0].split('        run: |\n')[1]
        guard = '\n'.join(line[10:] for line in guard.splitlines() if line.strip())
        # Browser qualification remains gated for both decisions, including checkout/action.
        for step in steps[1:6]:
            self.assertIn("if: github.event_name != 'workflow_dispatch' && needs.changes.outputs.code == 'true' && needs.changes.outputs.browser == 'true'", step)
        self.assertIn("needs.changes.outputs.browser == 'false'", steps[6])
        self.assertIn('intentionally omitted', steps[6])
        self.assertIn("needs.changes.outputs.code == 'false'", steps[7])
        self.assertIn('intentionally omitted, not executed', steps[7])
        for result in ('success', 'failure', 'cancelled', 'skipped', ''):
            for code in ('true', 'false', '', 'invalid'):
                for browser in ('true', 'false', '', 'invalid'):
                    actual = subprocess.run(['bash', '-e', '-c', guard], env=dict(os.environ, RESULT=result, CODE=code, BROWSER=browser, READY='true'))
                    valid = code in ('true', 'false') and browser in ('true', 'false') and (code == 'true' or browser == 'false')
                    self.assertEqual(actual.returncode == 0, result == 'success' and valid)
        self.assertIn('smoke-image.sh symphony-browser-codex codex', text)
        self.assertIn('smoke-image.sh symphony-browser-claude claude', text)
        # Candidate consumers continue to run actual-image qualification.
        release = (ROOT / '.github/workflows/runner-release.yml').read_text()
        smoke = (ROOT / 'scripts/release/runner-smoke.sh').read_text()
        self.assertNotIn('outputs.browser', release)
        self.assertIn('smoke-image.sh', smoke)

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
            outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(outputs, dict(code='true', policy='true', browser='true', ready='true', native='true', native_platform='all',
                                          **{key: 'true' for key in classifier.COMPONENTS}))
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
        for name,job in [('make-all','make-all'),('package-checks','package-qualification'),('runner-integration','chromium')]:
            text=(ROOT/'.github/workflows'/(name+'.yml')).read_text()
            self.assertIn('  '+job+':',text)
            wrapper_guard = ('if: ${{ !inputs.sign_macos && (always()) }}'
                             if name == 'runner-integration' else 'if: ${{ always() }}')
            self.assertIn(wrapper_guard,text)
            self.assertIn('test "$RESULT" = success',text)
            self.assertNotIn('\n    paths:',text)
        text=(ROOT/'.github/workflows/package-checks.yml').read_text()
        self.assertNotIn('  native:\n',text)
        self.assertIn('Run native process and updater fixtures in the helper allocation',text)
        self.assertNotIn('macos-15-intel',text)
        self.assertIn('matrix: ${{ fromJSON(needs.host-policy.outputs.matrix) }}',text)
        self.assertIn('workflow_dispatch:',text)
        self.assertIn('previous_revision:',text)

    def test_actual_wrapper_guard_from_empty_workspace(self):
        text=(ROOT/'.github/workflows/make-all.yml').read_text()
        self.assertNotIn('    defaults:',text)
        wrapper=re.search(r'(?ms)^  make-all:\n.*?(?=^  [a-z][a-z0-9-]*:\n|\Z)',text).group(0)
        guard=wrapper.split('        run: |\n',1)[1].split('      - ',1)[0]
        guard='\n'.join(line[10:] for line in guard.splitlines() if line.strip())
        with tempfile.TemporaryDirectory() as folder:
            for result,code,expected in [('success','false',0),('success','true',0),('failure','false',1),('cancelled','false',1),('skipped','false',1),('success','',1)]:
                command=subprocess.run(['bash','-e','-c',guard],cwd=folder,env=dict(os.environ,RESULT=result,CODE=code,POLICY='true',READY='true',NPM='false',READINESS='false',CONFIG='false',CONTRACTS='false',PROCESS_RESULT='skipped',MAC_RUNNER_RESULT='success',CLAUDE='false',ELIXIR_RESULT='success' if code=='true' else 'skipped',CLAUDE_RESULT='skipped'))
                self.assertEqual(command.returncode==0,expected==0,(result,code))
            for policy in ('', 'invalid', 'false'):
                refused=subprocess.run(['bash','-e','-c',guard],cwd=folder,
                    env=dict(os.environ,RESULT='success',CODE='true',POLICY=policy,READY='true',NPM='false',READINESS='false',CONFIG='false',CONTRACTS='false',PROCESS_RESULT='skipped',MAC_RUNNER_RESULT='success',CLAUDE='false',ELIXIR_RESULT='success',CLAUDE_RESULT='skipped'),capture_output=True)
                self.assertNotEqual(refused.returncode,0)
            summary=Path(folder)/'summary'
            command='echo "Documentation-only event; behavioral checks intentionally omitted, not executed." >> "$GITHUB_STEP_SUMMARY"'
            subprocess.run(['bash','-e','-c',command],cwd=folder,env=dict(os.environ,GITHUB_STEP_SUMMARY=str(summary)),check=True)
            self.assertIn('not executed',summary.read_text())
            # A real failing behavioral command must remain failing: wrappers do
            # not use continue-on-error or a trailing blanket green step.
            failure=subprocess.run(['bash','-e','-c',guard+'\nfalse\necho unreachable'],cwd=folder,env=dict(os.environ,RESULT='success',CODE='true',POLICY='true',READY='true',NPM='false',READINESS='false',CONFIG='false',CONTRACTS='false',PROCESS_RESULT='skipped',MAC_RUNNER_RESULT='success'),capture_output=True,text=True)
            self.assertNotEqual(failure.returncode,0)
            self.assertNotIn('unreachable',failure.stdout)

    def test_metadata_requires_no_toolchain(self):
        text=(ROOT/'.github/workflows/pr-description-lint.yml').read_text()
        for forbidden in ('mise','mix ', 'deps.get','install:','npm','pip '):
            self.assertNotIn(forbidden,text)
        self.assertIn('check_pr_body.py --event',text)


class RuntimeInventoryDiagnostic(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true'
                         and os.environ.get('RUNNER_OS') == 'Linux'
                         and os.environ.get('ImageOS') == 'ubuntu24',
                         'temporary data probe requires actual GitHub Ubuntu 24 runner')
    def test_public_runtime_inventory_diagnostic_only(self):
        """Temporary unmerged DATA probe; never eligibility or qualification."""
        import hashlib
        import shutil
        import signal
        import stat
        import sys

        started = time.monotonic()
        deadline = started + 110
        limits = {'seconds': 115, 'read_bytes': 8 * 1024 ** 3 - 1024 ** 2,
                  'hashed_files': 100_000, 'json_bytes': 1024 ** 2 - 128}
        result = {'label': 'JAY716_PUBLIC_RUNTIME_DIAGNOSTIC_V1',
                  'diagnostic_only': True, 'eligible_evidence': False,
                  'limits': limits, 'image': {}, 'runner': {}, 'roots': [],
                  'resolutions': [], 'inventory': [], 'inventory_truncated': False,
                  'bytes_read': 0, 'hashed_files': 0, 'first_budget_path': None}
        inventory_size = 0
        manifest = None
        pending = None

        class Budget(Exception):
            pass

        def check(path):
            nonlocal pending
            pending = str(path)
            if time.monotonic() >= deadline:
                raise Budget('time')
            if result['hashed_files'] >= limits['hashed_files']:
                raise Budget('file-count')
            if result['bytes_read'] >= limits['read_bytes']:
                raise Budget('read-bytes')

        def alarm(_signum, _frame):
            raise Budget('wall-clock-alarm')

        def record(row):
            nonlocal inventory_size
            encoded = json.dumps(row, sort_keys=True, separators=(',', ':'))
            if manifest is not None:
                manifest.update(encoded.encode() + b'\n')
            if inventory_size + len(encoded.encode()) <= 640_000:
                result['inventory'].append(row)
                inventory_size += len(encoded.encode())
            else:
                result['inventory_truncated'] = True

        allowed = [Path(p) for p in ('/usr/bin', '/usr/local/bin', '/usr/sbin',
                    '/usr/lib', '/usr/local/lib', '/lib', '/lib64',
                    '/usr/share/git-core', '/usr/share/zoneinfo',
                    '/usr/share/ca-certificates', '/etc/profile.d',
                    '/etc/ssl', '/etc/ld.so.conf.d')]
        exact = {Path(p) for p in ('/etc/os-release', '/etc/profile',
                 '/etc/nsswitch.conf', '/etc/ld.so.cache', '/etc/ld.so.conf',
                 '/etc/ld.so.preload', '/etc/resolv.conf', '/etc/hosts',
                 '/etc/gai.conf', '/etc/host.conf', '/etc/environment',
                 '/etc/python3.12/sitecustomize.py', '/etc/ssl/openssl.cnf')}

        def inside(path):
            return path in exact or any(path == p or path.is_relative_to(p)
                                        for p in allowed)

        def file_bytes(path, info):
            check(path)
            if info.st_size > limits['read_bytes'] - result['bytes_read']:
                raise Budget('read-bytes-next-file')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
                         | os.O_NONBLOCK)
            value = hashlib.sha256()
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino, opened.st_mode) != (info.st_dev, info.st_ino, info.st_mode):
                    return {'error': 'changed-before-read'}
                if not stat.S_ISREG(opened.st_mode):
                    return {'error': 'not-regular'}
                result['hashed_files'] += 1
                while True:
                    check(path)
                    left = limits['read_bytes'] - result['bytes_read']
                    chunk = os.read(fd, min(1024 * 1024, left))
                    if not chunk:
                        break
                    result['bytes_read'] += len(chunk)
                    value.update(chunk)
                after = os.fstat(fd)
                original = (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                            info.st_uid, info.st_gid, info.st_mtime_ns, info.st_ctime_ns)
                final = (after.st_dev, after.st_ino, after.st_size, after.st_mode,
                         after.st_uid, after.st_gid, after.st_mtime_ns, after.st_ctime_ns)
                return {'sha256': value.hexdigest(), 'size': info.st_size,
                        'changed_while_reading': original != final}
            finally:
                os.close(fd)

        # Global visited destinations prevent aliases from recursively rehashing
        # shared /bin↔/usr/bin and library/Git symlink targets.
        visited = set()

        def visit(path, summary, active):
            check(path)
            row = {'path': str(path)}
            try:
                info = path.lstat()
                row.update(mode=stat.S_IMODE(info.st_mode), uid=info.st_uid,
                           gid=info.st_gid)
                if stat.S_ISLNK(info.st_mode):
                    row.update(kind='symlink', target=os.readlink(path))
                    record(row)
                    try:
                        target = path.resolve(strict=False)
                    except RuntimeError:
                        record({'path': str(path), 'error': 'symlink-cycle'})
                        return
                    if not inside(target):
                        record({'path': str(path), 'error': 'unfollowed-public-root-escape',
                                'resolved': str(target)})
                    elif target in active:
                        record({'path': str(path), 'error': 'symlink-cycle'})
                    else:
                        visit(target, summary, active | {path})
                    return
                actual = path.absolute()
                if actual in active:
                    record({'path': str(path), 'error': 'directory-cycle'})
                    return
                if actual in visited:
                    summary['aliases_skipped'] += 1
                    return
                visited.add(actual)
                if stat.S_ISDIR(info.st_mode):
                    row['kind'] = 'directory'
                    summary['directories'] += 1
                    record(row)
                    # scandir streaming avoids an unbounded directory listing.
                    with os.scandir(path) as children:
                        for child in children:
                            visit(Path(child.path), summary, active | {actual})
                elif stat.S_ISREG(info.st_mode):
                    if path.name in {'.credentials', '.credentials_rsaparams', '.env',
                                     '.netrc', '.npmrc', '.pypirc', 'auth.json',
                                     'id_rsa', 'id_ed25519'}:
                        record({'path': str(path), 'kind': 'excluded-nonpublic-name'})
                        return
                    row.update(kind='file', **file_bytes(path, info))
                    summary['files'] += 1
                    record(row)
                else:
                    record({'path': str(path), 'kind': 'unread-special-file'})
            except FileNotFoundError:
                record({'path': str(path), 'kind': 'absent'})
            except OSError as error:
                record({'path': str(path), 'error': type(error).__name__})

        previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, limits['seconds'])
        try:
            # Only public worker executable/status ancestry; never environ/cmdline.
            pid = os.getppid()
            for _ in range(12):
                check("/proc/worker-ancestry")
                try:
                    proc = Path('/proc') / str(pid)
                    exe = (proc / 'exe').resolve(strict=True)
                    if exe.name == 'Runner.Worker' and exe.parent.name == 'bin':
                        worker = exe.parent.parent
                        result['runner'] = {'worker_executable': str(exe),
                                            'public_roots': [str(worker / x) for x in ('bin', 'externals')]}
                        allowed.extend([worker / 'bin', worker / 'externals'])
                        break
                    data = (proc / 'status').read_text()
                    match = re.search(r'^PPid:\s+(\d+)$', data, re.M)
                    if not match or int(match[1]) <= 0:
                        break
                    pid = int(match[1])
                except (OSError, RuntimeError):
                    break

            # Only these two documented public image labels; never env dumps.
            for name in ('ImageOS', 'ImageVersion'):
                value = os.environ.get(name, '')
                result['image'][name] = value if re.fullmatch(r'[A-Za-z0-9_.-]{0,80}', value) else 'invalid-public-label'
            os_release = Path('/etc/os-release').read_text()
            if not re.search(r'^ID=ubuntu$', os_release, re.M) or not re.search(r'^VERSION_ID="24\.04"$', os_release, re.M):
                result['refusal'] = 'actual os-release is outside Ubuntu24 profile'
                return
            # Finite audited command lookup, in parent, canonical-profile and
            # actual reduced child contexts. Do not execute any resolved file.
            names = ('erl', 'elixir', 'mix', 'node', 'python3', 'bash', 'sh', 'env',
                     'git', 'cat', 'timeout', 'kill', 'sleep', 'ps', 'id', 'uname',
                     'readlink', 'dirname', 'basename', 'mkdir', 'chmod', 'cp',
                     'mv', 'rm', 'ln', 'mktemp', 'sed', 'awk', 'grep', 'tr',
                     'cut', 'sort', 'head', 'tail', 'stat', 'flock', 'sha256sum',
                     'shasum', 'ssh', 'ssh-keygen', 'docker', 'curl', 'gh')
            parent_path = os.environ.get('PATH', '')
            tool_dirs = []
            tool_roots = []
            for name in ('elixir', 'erl', 'node', 'python3'):
                found = shutil.which(name)
                if found:
                    try:
                        selected = Path(found).resolve(strict=True)
                        if (inside(selected) or selected.is_relative_to('/opt/hostedtoolcache')
                                or selected.is_relative_to('/home/runner/.local/share/mise/installs')):
                            if not inside(selected):
                                allowed.append(selected.parent.parent)
                            if name != 'python3':
                                tool_dirs.append(str(selected.parent))
                            if str(selected.parent.parent) not in ('/usr', '/usr/local', '/'):
                                tool_roots.append(str(selected.parent.parent))
                    except (OSError, RuntimeError):
                        pass
            contexts = {'parent': parent_path,
                        'canonical': ':'.join(dict.fromkeys(tool_dirs + ['/usr/bin', '/bin'])),
                        'child': '/usr/local/bin:/usr/bin:/bin',
                        'apparmor': '/usr/sbin:/usr/bin:/sbin:/bin'}
            for label, search in contexts.items():
                directories = list(dict.fromkeys(search.split(':')))
                for name in names:
                    check(name)
                    candidates = []
                    for folder in directories[:40]:
                        path = Path(folder) / name
                        if not Path(folder).is_absolute() or not inside(path):
                            candidates.append({'path': str(path), 'unfollowed': 'outside-public-roots'})
                            continue
                        present = os.path.lexists(path)
                        item = {'path': str(path), 'present': present}
                        if present:
                            item['executable'] = os.access(path, os.X_OK)
                            try:
                                item['resolved'] = str(path.resolve(strict=False))
                            except RuntimeError:
                                item['error'] = 'symlink-cycle'
                        candidates.append(item)
                    result['resolutions'].append({'context': label, 'name': name,
                                                  'candidates': candidates})
            result['python_startup'] = {
                'executable': str(Path(sys.executable).resolve()),
                'version': list(sys.version_info[:3]),
                'public_search_paths': [p for p in sys.path if p and Path(p).is_absolute()
                                        and inside(Path(p))],
                'nonpublic_search_paths_not_read': sum(1 for p in sys.path
                    if not p or not Path(p).is_absolute() or not inside(Path(p)))}
            result['tool_roots_present'] = list(dict.fromkeys(tool_roots))
            # Prioritize small implicit inputs before potentially huge roots.
            roots = list(map(str, sorted(exact))) + [
                '/etc/profile.d', '/etc/ld.so.conf.d', '/usr/lib/python3.12',
                '/usr/lib/python3/dist-packages', '/usr/local/lib/python3.12/dist-packages',
                '/usr/local/lib/python3.12/site-packages',
                *result['python_startup']['public_search_paths'], '/usr/lib/git-core',
                '/usr/share/git-core', '/usr/share/zoneinfo', '/usr/lib/locale',
                '/etc/ssl/certs', '/usr/share/ca-certificates',
                '/usr/bin', '/usr/local/bin', '/usr/sbin', '/usr/lib/x86_64-linux-gnu',
                *tool_roots, *result['runner'].get('public_roots', [])]
            result['root_plan'] = list(dict.fromkeys(roots))
            for value in result['root_plan']:
                check(value)
                root = Path(value)
                summary = {'path': value, 'files': 0, 'directories': 0,
                           'aliases_skipped': 0, 'bytes': 0, 'complete': False}
                before = result['bytes_read']
                tick = time.monotonic()
                result['roots'].append(summary)
                manifest = hashlib.sha256()
                try:
                    visit(root, summary, set())
                    summary['complete'] = True
                finally:
                    summary['observed_manifest_sha256'] = manifest.hexdigest()
                    summary['seconds'] = round(time.monotonic() - tick, 6)
                    summary['bytes'] = result['bytes_read'] - before
        except Budget as error:
            result['budget_stop'] = str(error)
            result['first_budget_path'] = pending
        finally:
            # Reserve at most the remaining119s wall budget for serialization/log IO.
            def output_alarm(_signum, _frame):
                raise TimeoutError('diagnostic output exceeded total wall bound')
            signal.signal(signal.SIGALRM, output_alarm)
            signal.setitimer(signal.ITIMER_REAL, max(0.001, 119 - (time.monotonic() - started)))
            result['elapsed_seconds'] = round(time.monotonic() - started, 6)
            result['scope'] = 'read-only public DATA, never validation reuse; missing runtimes are absent, not installed'
            encoded = json.dumps(result, sort_keys=True, indent=1)
            # Fail visibly if a future edit invalidates the explicit log bound.
            if len(encoded.encode()) > limits['json_bytes']:
                result['inventory'] = []
                result['inventory_truncated'] = True
                result['output_bound_refusal'] = True
                encoded = json.dumps(result, sort_keys=True, indent=1)
            if len(encoded.encode()) > limits['json_bytes']:
                result['resolutions'] = []
                result['output_bound_refusal'] = True
                encoded = json.dumps(result, sort_keys=True, indent=1)
            self.assertLessEqual(len(encoded.encode()), limits['json_bytes'])
            try:
                print('JAY716_PUBLIC_RUNTIME_DIAGNOSTIC_JSON_BEGIN', flush=True)
                print(encoded, flush=True)
                print('JAY716_PUBLIC_RUNTIME_DIAGNOSTIC_JSON_END', flush=True)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    unittest.main()
