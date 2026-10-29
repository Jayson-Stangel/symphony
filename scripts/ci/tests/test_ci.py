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


class NativeOCICompatibilityDiagnostic(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('GITHUB_ACTIONS') == 'true'
                         and os.environ.get('RUNNER_OS') == 'Linux'
                         and os.environ.get('ImageOS') == 'ubuntu24',
                         'temporary native OCI data probe requires actual GitHub Ubuntu 24')
    def test_native_oci_compatibility_and_reproducible_identity_data_only(self):
        """ONE unmerged diagnostic, no suite reuse or full application execution."""
        import hashlib
        import platform
        import shutil
        import signal
        import stat
        import uuid

        started = time.monotonic()
        operation_deadline = started + 125
        cleanup_deadline = started + 172
        limits = {'whole_case_seconds': 180, 'alarm_seconds': 175,
                  'operation_seconds': 125, 'task_disk_bytes': 10 * 1024 ** 3,
                  'stdout_bytes': 64 * 1024, 'pulls': 2, 'fresh_builds': 2,
                  'containers': 2}
        profile = 'elixir-coverage-oci-preflight-v1'
        owner = 'jay716-native-oci-' + uuid.uuid4().hex
        bases = ['docker.io/library/node@sha256:4e9cb555d708e0829c9d93e5eeae9dfab0617b832ca436a690680e0fca735ef5',
                 'docker.io/hexpm/elixir@sha256:df1f6fe1ffb340380b5089bdf4eed8fcef2305e5909e272b4587fb7864b7ab10']
        result = {'label': 'JAY716_NATIVE_OCI_DIAGNOSTIC_V1', 'diagnostic_only': True,
                  'eligible_evidence': False, 'limits': limits, 'profile': profile,
                  'events': [], 'images': [], 'containers': [], 'cleanup': [], 'pinned_bases': bases,
                  'public_bases_and_bounded_cache_retained': True,
                  'shared_prune': False, 'result': 'STOP_BEFORE_EFFECTS'}
        prefix = []
        tags = []
        containers = []
        root_disk = None
        initial_free = None

        class Stop(Exception):
            pass

        def require(value, reason):
            if not value:
                raise Stop(reason)

        def alarm(_signum, _frame):
            raise Stop('whole-case-wall-alarm')

        def disk_guard():
            if root_disk is not None:
                growth = initial_free - shutil.disk_usage(root_disk).free
                result['observed_disk_growth_bytes'] = max(0, growth)
                require(growth <= limits['task_disk_bytes'], 'task-disk-bound')

        def command(phase, args, *, stdin=None, timeout=30, cleanup=False):
            end = cleanup_deadline if cleanup else operation_deadline
            remaining = end - time.monotonic()
            require(remaining > 0, 'bounded-' + ('cleanup' if cleanup else 'operation') + '-deadline')
            tick = time.monotonic()
            output = folder / (phase + '.log')
            # Docker output is bounded to local private logs; no raw pull/build
            # errors, headers or credentials are emitted in the JSON receipt.
            with output.open('wb') as stream:
                process = subprocess.Popen(prefix + args, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT, env=environment)
                try:
                    if stdin:
                        process.stdin.write(stdin); process.stdin.close()
                    deadline = time.monotonic() + min(timeout, remaining)
                    while process.poll() is None:
                        require(time.monotonic() < deadline, 'subcommand-timeout-' + phase)
                        require(output.stat().st_size <= 2 * 1024 ** 2, 'subcommand-output-bound')
                        if not cleanup:
                            disk_guard()
                        time.sleep(0.1)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait(timeout=2)
            raw = output.read_bytes()
            require(len(raw) <= 2 * 1024 ** 2, 'subcommand-output-bound')
            result['events'].append({'phase': phase, 'seconds': round(time.monotonic() - tick, 3),
                                     'exit_code': process.returncode, 'bytes': len(raw),
                                     'sha256': hashlib.sha256(raw).hexdigest()})
            return process.returncode, raw

        def successful(phase, args, **kwargs):
            code, raw = command(phase, args, **kwargs)
            require(code == 0, 'first-command-failure-' + phase)
            return raw

        def inspect_image(phase, tag):
            data = json.loads(successful(phase, ['image', 'inspect', tag]))[0]
            require(data['Architecture'] == 'amd64' and data['Os'] == 'linux', 'image-native-platform-mismatch')
            return {key: data.get(key) for key in ('Id', 'Architecture', 'Os', 'Created', 'Config', 'RootFS', 'Size')}

        def run_args(name, image, entrypoint):
            containers.append(name)
            args = ['run', '--pull', 'never', '--name', name, '--label', 'com.symphony.task=' + owner,
                    '--platform', 'linux/amd64', '--user', '1001:1001', '--read-only', '--network', 'none',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--cpus', '2',
                    '--memory', '2g', '--pids-limit', '256', '--ipc', 'private', '--cgroupns', 'private',
                    '--workdir', '/home/ci', '--env', 'HOME=/home/ci', '--env', 'TMPDIR=/tmp',
                    '--env', 'MIX_HOME=/cache/mix', '--env', 'HEX_HOME=/cache/hex',
                    '--env', 'PATH=/usr/local/bin:/usr/bin:/bin', '--env', 'LANG=C.UTF-8',
                    '--env', 'LC_ALL=C.UTF-8', '--env', 'TZ=UTC']
            for path, size, mode in [('/home/ci', '16m', '0700'), ('/tmp', '32m', '1777'), ('/cache', '32m', '0700')]:
                args += ['--tmpfs', path + ':rw,nosuid,nodev,exec,size=' + size + ',mode=' + mode + ',uid=1001,gid=1001']
            return args + ['--entrypoint', entrypoint, '-i', image, '-']

        previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, alarm)
        signal.setitimer(signal.ITIMER_REAL, 175)
        try:
            with tempfile.TemporaryDirectory(prefix='jay716-native-oci-') as directory:
                folder = Path(directory).resolve();folder.chmod(0o700)
                home = folder / 'client-home';home.mkdir(mode=0o700)
                config = folder / 'docker-config';config.mkdir(mode=0o700)
                (config / 'config.json').write_text('{}\n');(config / 'config.json').chmod(0o600)
                docker = shutil.which('docker')
                require(docker and platform.machine() == 'x86_64', 'native-host-or-docker-unavailable')
                release = Path('/etc/os-release').read_text()
                require(re.search(r'^ID=ubuntu$', release, re.M) and re.search(r'^VERSION_ID="24\.04"$', release, re.M),
                        'actual-host-image-not-ubuntu24')
                socket = Path('/var/run/docker.sock').resolve(strict=True)
                require(stat.S_ISSOCK(socket.stat().st_mode), 'verified-local-unix-socket-unavailable')
                prefix = [docker, '--config', str(config), '--host', 'unix://' + str(socket)]
                environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
                               'DOCKER_CONFIG': str(config), 'DOCKER_HOST': 'unix://' + str(socket),
                               'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
                result['image_labels'] = {name: (os.environ.get(name, '') if re.fullmatch(r'[A-Za-z0-9_.-]{0,80}', os.environ.get(name, '')) else 'invalid')
                                          for name in ('ImageOS', 'ImageVersion')}
                info = json.loads(successful('daemon-before', ['info', '--format', '{{json .}}'], timeout=10))
                require(info['OSType'] == 'linux' and info['Architecture'] in ('x86_64', 'amd64'), 'daemon-not-native-linux-amd64')
                result['daemon'] = {key: info.get(key) for key in ('ID', 'ServerVersion', 'OSType', 'Architecture', 'DockerRootDir')}
                root_disk = Path(info['DockerRootDir'])
                initial_free = shutil.disk_usage(root_disk).free
                require(initial_free >= 12 * 1024 ** 3, 'insufficient-task-disk-headroom')
                builder = json.loads(successful('builder-before', ['buildx', 'inspect', 'default', '--format', '{{json .}}'], timeout=10))
                require(builder.get('Driver') == 'docker' and builder.get('Nodes')
                        and all(n.get('Status') == 'running' for n in builder['Nodes']), 'no-running-integrated-docker-builder')
                result['builder'] = {'driver': builder['Driver'], 'nodes': [{k: n.get(k) for k in ('Name', 'Endpoint', 'Status', 'Buildkit')} for n in builder['Nodes']]}
                try:
                    for n, base in enumerate(bases):
                        successful('pull-' + str(n), ['pull', '--platform', 'linux/amd64', base], timeout=35)
                    context = folder / 'context';context.mkdir(mode=0o700)
                    dockerfile = ('ARG SOURCE_DATE_EPOCH=0\nFROM ' + bases[1] + ' AS otp\nFROM ' + bases[0] + '\n'
                                  'COPY --from=otp /usr/local/ /usr/local/\nCOPY passwd /etc/passwd\nCOPY group /etc/group\n'
                                  'USER 1001:1001\nWORKDIR /home/ci\n'
                                  'ENV HOME=/home/ci TMPDIR=/tmp MIX_HOME=/cache/mix HEX_HOME=/cache/hex LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=UTC\n'
                                  'LABEL com.symphony.ci.profile="' + profile + '"\n')
                    inputs = {'Dockerfile': dockerfile,
                              'passwd': 'root:x:0:0:root:/root:/usr/sbin/nologin\nci:x:1001:1001:CI:/home/ci:/usr/sbin/nologin\nnobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n',
                              'group': 'root:x:0:\nci:x:1001:\nnogroup:x:65534:\n'}
                    for name, value in inputs.items():
                        path = context / name;path.write_text(value);path.chmod(0o644);os.utime(path, (0, 0))
                    os.utime(context, (0, 0))
                    result['recipe'] = {'source_date_epoch': 0, 'provenance': False, 'no_cache': True,
                                        'file_sha256': {name: hashlib.sha256(value.encode()).hexdigest() for name, value in inputs.items()},
                                        'file_modes': '0644', 'file_mtimes': 0, 'contains_application_source': False,
                                        'dynamic_ownership_in_image_config': False}
                    for n in range(2):
                        tag = owner + ':' + str(n);tags.append(tag)
                        successful('fresh-build-' + str(n), ['buildx', 'build', '--builder', 'default', '--platform', 'linux/amd64',
                                   '--network', 'none', '--pull=false', '--no-cache', '--provenance=false',
                                   '--build-arg', 'SOURCE_DATE_EPOCH=0', '--load', '--progress', 'plain', '--tag', tag, str(context)], timeout=25)
                        result['images'].append(inspect_image('image-' + str(n), tag))
                    first, second = result['images']
                    require(re.fullmatch(r'1970-01-01T00:00:00(?:\.0+)?Z', first.get('Created') or '')
                            and re.fullmatch(r'1970-01-01T00:00:00(?:\.0+)?Z', second.get('Created') or ''),
                            'source-date-epoch-not-applied-STOP')
                    result['identical_actual_image_fields'] = {key: first[key] == second[key] for key in ('Id', 'Created', 'Config', 'RootFS')}
                    require(all(result['identical_actual_image_fields'].values()), 'actual-image-config-rootfs-identity-differs-STOP')
                    require(first['Config']['User'] == '1001:1001' and first['Config']['Labels'].get('com.symphony.ci.profile') == profile,
                            'fixed-image-profile-differs')
                    probe = r'''import json,os,platform,pwd,shutil,signal,socket,subprocess,sys,threading
from pathlib import Path
r={'scope':'native linux amd64 OCI primitives only; no application or qualification reuse','versions':{},'failed_tool':None}
def run(tool,args):
 r['failed_tool']=tool
 p=subprocess.run([tool]+args,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
 if p.returncode:raise RuntimeError(tool+' exit'+str(p.returncode)+' '+(p.stdout+p.stderr)[-2048:])
 r['versions'][tool]=(p.stdout+p.stderr).strip()[:512];r['failed_tool']=None
try:
 assert os.getuid()==os.getgid()==1001 and pwd.getpwuid(1001).pw_name=='ci' and os.getcwd()=='/home/ci' and platform.machine()=='x86_64'
 assert sys.version_info>=(3,11)
 for tool,args in [('node',['--version']),('elixir',['--short-version']),('mix',['--version']),('python3',['--version']),('make',['--version']),('git',['--version'])]:run(tool,args)
 assert r['versions']['node']=='v24.18.0' and r['versions']['elixir']=='1.19.5'
 v=list(Path('/usr/local/lib/erlang/releases').glob('*/OTP_VERSION'));assert len(v)==1 and v[0].read_text().strip()=='28.5'
 r['otp_full']='28.5';r['libraries']=[]
 paths=list(Path('/usr/local/lib/erlang').rglob('*.so'))+list(Path('/usr/local/lib/erlang').glob('erts-*/bin/*'))
 for p in paths:
  if not p.is_file():continue
  with p.open('rb') as f:elf=f.read(4)==b'\x7fELF'
  if not elf:continue
  v=subprocess.run(['ldd',str(p)],capture_output=True,text=True,timeout=5);out=v.stdout+v.stderr
  r['libraries'].append({'path':str(p),'missing':'not found' in out,'status':v.returncode})
  assert 'not found' not in out and (v.returncode==0 or 'not a dynamic executable' in out or 'statically linked' in out)
 for d in ['/home/ci','/tmp','/cache']:
  p=Path(d)/'private-probe';p.write_text('private');assert p.read_text()=='private';p.unlink()
 p=Path('/tmp/private-exec');p.write_text('#!/bin/sh\nprintf private-exec\n');p.chmod(0o700);assert subprocess.check_output([str(p)])==b'private-exec';p.unlink()
 p=subprocess.Popen(['sleep','30'],start_new_session=True);os.killpg(p.pid,signal.SIGTERM);assert p.wait(timeout=3)==-signal.SIGTERM
 s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1)
 def serve():
  c,_=s.accept();assert c.recv(4)==b'ping';c.sendall(b'pong');c.close()
 t=threading.Thread(target=serve);t.start();c=socket.create_connection(s.getsockname(),timeout=3);c.sendall(b'ping');assert c.recv(4)==b'pong';c.close();t.join(3);assert not t.is_alive();s.close()
 r['result']='PASS_NATIVE_COMPATIBILITY_PRIMITIVES_ONLY'
except Exception as e:r['result']='STOP_FIRST_PRIMITIVE_FAILURE';r['category']=type(e).__name__;r['reason']=str(e)[:2304]
print(json.dumps(r,sort_keys=True))
'''
                    code, raw = command('combined-primitives', run_args(owner + '-combined', tags[0], 'python3'), stdin=probe.encode(), timeout=30)
                    require(code == 0 and len(raw) < 16384, 'primitive-executor-failed')
                    result['primitives'] = json.loads(raw)
                    if result['primitives'].get('failed_tool') == 'mix':
                        # Explicit single baseline only after the named Mix failure;
                        # both commands use non-TTY/devnull stdin and same isolation.
                        baseline = b"set +e\nprintf '' | timeout 20 mix --version </dev/null 2>&1\ns=$?\nprintf '\\nSOURCE_BASE_MIX_EXIT %s\\n' \"$s\"\n"
                        code, raw = command('source-mix-baseline', run_args(owner + '-source', bases[1], '/bin/sh')[:-1] + ['-s'], stdin=baseline, timeout=25)
                        result['source_mix_baseline'] = {'executor_exit': code, 'output_sha256': hashlib.sha256(raw).hexdigest(),
                                                         'exit': int(re.search(rb'SOURCE_BASE_MIX_EXIT (\d+)', raw)[1]) if re.search(rb'SOURCE_BASE_MIX_EXIT (\d+)', raw) else None,
                                                         'tail': raw[-2304:].decode(errors='replace')}
                    require(result['primitives'].get('result') == 'PASS_NATIVE_COMPATIBILITY_PRIMITIVES_ONLY', 'actual-native-primitive-failed-STOP')
                    result['result'] = 'PASS_NATIVE_COMPATIBILITY_AND_TWO_FRESH_IDENTICAL_IMAGES_ONLY'
                except Exception as error:
                    result['result'] = 'STOP_DATA_NOT_QUALIFICATION';result['reason'] = str(error)[:200]
                finally:
                    # Only exact names/tags created by this case; no base/cache prune.
                    for name in containers:
                        try:
                            code, raw = command('cleanup-inspect-' + name[-8:], ['inspect', name], cleanup=True, timeout=4)
                            if code == 0:
                                value = json.loads(raw)[0]
                                require(value['Config']['Labels'].get('com.symphony.task') == owner, 'foreign-container-preserved')
                                result['containers'].append({'id': value['Id'], 'name': name, 'user': value['Config']['User'],
                                                            'read_only': value['HostConfig']['ReadonlyRootfs'], 'network': value['HostConfig']['NetworkMode'],
                                                            'bind_mounts': value['Mounts'], 'tmpfs': value['HostConfig']['Tmpfs'],
                                                            'cap_drop': value['HostConfig']['CapDrop'], 'security_options': value['HostConfig']['SecurityOpt']})
                                require(not value['Mounts'] and value['Config']['User'] == '1001:1001'
                                        and value['HostConfig']['ReadonlyRootfs']
                                        and value['HostConfig']['NetworkMode'] == 'none'
                                        and 'ALL' in value['HostConfig']['CapDrop']
                                        and any(v.startswith('no-new-privileges') for v in value['HostConfig']['SecurityOpt']),
                                        'actual-container-isolation-differs')
                                exact_id = value['Id']
                                if value['State']['Running']:
                                    successful('cleanup-stop-' + name[-8:], ['stop', '--time', '1', exact_id],
                                               cleanup=True, timeout=3)
                                successful('cleanup-remove-' + name[-8:], ['rm', exact_id], cleanup=True, timeout=4)
                                result['cleanup'].append({'container': name, 'removed': True})
                        except Exception:result['cleanup'].append({'container': name, 'removed': False})
                    for n, tag in enumerate(tags):
                        try:
                            code, raw = command('cleanup-image-inspect-' + str(n), ['image', 'inspect', tag], cleanup=True, timeout=4)
                            if code == 0:
                                value = json.loads(raw)[0]
                                require(tag in value.get('RepoTags', []) and value['Config']['Labels'].get('com.symphony.ci.profile') == profile, 'foreign-image-preserved')
                                successful('cleanup-tag-' + str(n), ['image', 'rm', tag], cleanup=True, timeout=4)
                                result['cleanup'].append({'tag': tag, 'removed': True})
                        except Exception:result['cleanup'].append({'tag': tag, 'removed': False})
                    try:
                        remaining = successful('owned-containers-after', ['ps', '-a', '--filter',
                            'label=com.symphony.task=' + owner, '--format', '{{.ID}}'], cleanup=True, timeout=3)
                        remaining_tags = successful('owned-tags-after', ['image', 'ls', '--format',
                            '{{.Repository}}:{{.Tag}}'], cleanup=True, timeout=3).decode().splitlines()
                        require(not remaining.strip() and not any(tag in remaining_tags for tag in tags),
                                'owned-cleanup-presence-remains')
                        result['cleanup_verified_absent'] = True
                    except Exception:
                        result['cleanup_verified_absent'] = False
                    disk_guard()
        except Exception as error:
            result['result'] = 'STOP_DATA_NOT_QUALIFICATION';result['reason'] = str(error)[:200]
        finally:
            try:
                result['elapsed_seconds'] = round(time.monotonic() - started, 3)
                encoded = json.dumps(result, sort_keys=True, separators=(',', ':'))
                self.assertLessEqual(len(encoded.encode()), 64 * 1024 - 128)
                print('JAY716_NATIVE_OCI_DIAGNOSTIC_JSON=' + encoded, flush=True)
                self.assertTrue(all(row['removed'] for row in result['cleanup'])
                                and (not (tags or containers) or result.get('cleanup_verified_absent') is True),
                                'owned cleanup unverified; no retry')
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    unittest.main()
