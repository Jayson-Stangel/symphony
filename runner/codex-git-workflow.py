"""Model-free Git regression through the native Codex app-server execution API.

Runs only in a disposable qualification container, with no accounts or project
mounts. This proves local-remote Git effects, not GitHub or inference acceptance.
"""
import json
import os
from pathlib import Path
import select
import subprocess
import tempfile
import time


def read_response(stream, buffer, request_id, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        while b'\n' in buffer:
            line, _, remainder = buffer.partition(b'\n')
            buffer[:] = remainder
            message = json.loads(line)
            if message.get('id') == request_id:
                if 'error' in message:
                    raise RuntimeError('app-server refused qualification interface')
                return message['result']
        ready, _, _ = select.select([stream], [], [], max(0, deadline-time.monotonic()))
        if not ready:
            break
        data = os.read(stream.fileno(), 65536)
        if not data:
            raise RuntimeError('app-server closed before Git qualification response')
        buffer.extend(data)
    raise RuntimeError('app-server Git qualification timed out')


def qualify():
    root = Path('/workspaces/git-qualification')
    root.mkdir()
    home = Path('/var/lib/symphony/git-qualification-home')
    home.mkdir()
    env = {**os.environ, 'HOME': str(home), 'CODEX_HOME': str(home),
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
    with tempfile.TemporaryFile() as stderr:
        child = subprocess.Popen(['codex', 'app-server'], cwd=root, env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=stderr, bufsize=0)
        try:
            sequence = 0
            buffer = bytearray()

            def request(method, params):
                nonlocal sequence
                sequence += 1
                payload = {'id': sequence, 'method': method, 'params': params}
                child.stdin.write((json.dumps(payload) + '\n').encode())
                child.stdin.flush()
                return read_response(child.stdout, buffer, sequence)

            request('initialize', {'clientInfo': {'name': 'symphony-git-qualification', 'version': '1'},
                                   'capabilities': {'experimentalApi': True}})
            child.stdin.write((json.dumps({'method': 'initialized', 'params': {}}) + '\n').encode())
            child.stdin.flush()

            def execute(cwd, script, git_write):
                roots = [str(cwd)] + ([str(cwd / '.git')] if git_write else [])
                return request('command/exec', {'command': ['bash', '-ceu', script], 'cwd': str(cwd),
                                                'timeoutMs': 30000,
                                                'sandboxPolicy': {'type': 'workspaceWrite',
                                                                  'writableRoots': roots,
                                                                  'networkAccess': False}})

            seed = execute(root, '''git init -q --bare seed.git
git init -q seed
cd seed
git config user.name Fixture
git config user.email fixture@example.invalid
printf baseline > value
git add value
git commit -qm baseline
git push -q ../seed.git HEAD:refs/heads/main
cd ..
git clone -q --branch main seed.git task
''', True)
            if seed['exitCode'] != 0:
                raise RuntimeError('protected Git bootstrap failed')
            task = root / 'task'
            old = execute(task, 'git config user.name Proof', False)
            if old['exitCode'] == 0 or 'Read-only file system' not in old['stderr']:
                raise RuntimeError('old policy did not reproduce protected Git failure')
            print(json.dumps({'old_policy': 'git_metadata_read_only', 'exit_code': old['exitCode']}), flush=True)
            candidate = execute(task, '''git config user.name Proof
git config user.email proof@example.invalid
git clone -q --bare ../seed.git remote.git
git remote set-url origin "$PWD/remote.git"
git fetch -q origin
git switch -qc codex/git-qualification
printf candidate > value
test "$(cat value)" = candidate
git add value
git commit -qm candidate
git push -q origin HEAD:refs/heads/codex/git-qualification
git fetch -q origin codex/git-qualification
test "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)"
test "$(git --git-dir=remote.git show codex/git-qualification:value)" = candidate
printf dirty > value
printf preserved > untracked
git fetch -q origin
test "$(cat value)" = dirty
test "$(cat untracked)" = preserved
printf git-workflow-complete
''', True)
            if candidate['exitCode'] != 0 or candidate['stdout'] != 'git-workflow-complete':
                raise RuntimeError('protected app-server Git workflow failed')
            print(json.dumps({'candidate_policy': 'owned_git_writable', 'result': candidate['stdout'],
                              'model_requests': 0, 'remote': 'disposable_local_bare'}), flush=True)
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == '__main__':
    qualify()
