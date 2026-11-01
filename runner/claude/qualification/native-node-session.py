#!/usr/bin/env python3
"""Plan, or explicitly run, one native Node turn via the production Claude helper.

--live sends model requests and requires separate operator authorization for the
exact candidate, disposable native profile and bounded subscription usage.
This driver does not enforce a dollar cap or a native built-in-tool allowlist.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

WORKSPACE = Path('/workspaces/native-node-qualification')
FIXTURE = '/qualification/claude/qualification/native-node-fixture.py'
PROMPT = (
    'Run exactly this command once with Bash:\npython3 ' + FIXTURE + ' --run\n'
    'The local fixture installs, '
    'loads, deletes and rebuilds the supplied/private native dependency. '
    'Do not edit files, inspect HOME/auth, use network, run Git, delegate, '
    'repair errors, repeat the command or invoke any other tool. '
    'Report only PASS or FAIL from the command result.')


def frames():
    common = {'v': 1, 'session_id': 'native-node-qualification'}
    return [
        {**common, 'type': 'start', 'request_id': 'start', 'workspace': str(WORKSPACE),
         'config': {'model': 'sonnet', 'max_turns': 1, 'max_agentic_turns': 3,
                    'turn_timeout_ms': 120000}, 'tools': [], 'skills': {},
         'instructions': 'Only execute the local native Node qualification prompt.'},
        {**common, 'type': 'turn', 'request_id': 'native-node', 'prompt': PROMPT},
        {**common, 'type': 'stop', 'request_id': 'stop'},
    ]


def live():
    if os.getuid() != 11001 or Path.home() != Path('/home/claude') or Path.cwd() != WORKSPACE:
        raise RuntimeError('wrong disposable container context')
    if not (WORKSPACE / '.native-node-qualification.json').is_file() or (WORKSPACE / 'projects').exists():
        raise RuntimeError('fixture must be freshly prepared')
    # A failed/auth-refused attempt also consumes this prepared workspace.
    with (WORKSPACE / '.native-node-session-attempt').open('x') as marker:
        marker.write('one separately authorized attempt\n')
    state = json.loads((WORKSPACE / '.native-node-qualification.json').read_text())
    expected_canaries = [Path.home() / '.npmrc',
                         Path.home() / '.claude/symphony-native-node-canary']
    if state['canaries'] != [str(path) for path in expected_canaries]:
        raise RuntimeError('unexpected fixture state')
    # The unchanged helper CLI performs nativeIdentity/profileFromStatus before
    # serve; no additional auth process or alternate readiness path is needed.
    start, turn, stop = frames()
    child = subprocess.Popen(['node', '/opt/symphony/claude/helper.mjs'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)

    def send(frame):
        child.stdin.write((json.dumps(frame) + '\n').encode())
        child.stdin.flush()

    sent_turn = completed = False
    tool_starts = 0
    buffer = b''
    deadline = time.monotonic() + 140
    try:
        send(start)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                if not selector.select(timeout=1):
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('helper output unavailable')
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if len(line) > 1048576:
                        raise RuntimeError('helper frame too large')
                    event = json.loads(line)
                    if event.get('type') == 'ready':
                        if event.get('identity', {}).get('category') != 'subscription':
                            raise RuntimeError('unqualified native profile')
                        if sent_turn:
                            raise RuntimeError('unexpected second ready')
                        sent_turn = True
                        send(turn)
                    elif event.get('type') in ('error', 'tool_call'):
                        raise RuntimeError('helper or unexpected tracker error')
                    elif event.get('type') == 'progress' and event.get('status') == 'tool_started':
                        tool_starts += 1
                        if event.get('tool') != 'command' or tool_starts > 1:
                            raise RuntimeError('unexpected native tool observed')
                    elif event.get('type') == 'turn_result':
                        if not sent_turn or event.get('outcome') != 'completed' or not tool_starts:
                            raise RuntimeError('native turn failed')
                        result = json.loads((WORKSPACE / 'native-node-result.json').read_text())
                        expected = {'fixture': 'native-node', 'uid': 11001,
                                    'arch': os.uname().machine,
                                    'supplied_and_private': 'compile/load/delete/rebuild/load passed',
                                    'synthetic_auth_config': 'unchanged'}
                        if result != expected:
                            raise RuntimeError('fixture proof missing')
                        header = Path('/usr/local/include/node/node.h')
                        if hashlib.sha256(header.read_bytes()).hexdigest() != state['header_sha256']:
                            raise RuntimeError('image header changed')
                        for path in expected_canaries:
                            if path.read_text() != '# synthetic-native-node-qualification\n':
                                raise RuntimeError('synthetic canary changed')
                        completed = True
                        break
                if completed:
                    break
                if len(buffer) > 1048576:
                    raise RuntimeError('helper frame too large')
        if not completed:
            raise RuntimeError('qualification deadline')
    finally:
        try:
            send(stop)
        except (BrokenPipeError, OSError):
            pass
        child.stdin.close()
        # Require the production acknowledgement after native/scratch cleanup,
        # not merely exit0. Outer container teardown still bounds failure paths.
        acknowledged = False
        cleanup_deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while not acknowledged and time.monotonic() < cleanup_deadline:
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if len(line) > 1048576:
                        raise RuntimeError('helper frame too large')
                    event = json.loads(line)
                    acknowledged = (event.get('type') == 'stopped' and
                                    event.get('session_id') == stop['session_id'] and
                                    event.get('request_id') == stop['request_id'])
                    if acknowledged:
                        break
                if acknowledged:
                    break
                if not selector.select(timeout=0.1):
                    continue
                chunk = os.read(child.stdout.fileno(), 65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 1048576:
                    raise RuntimeError('helper frame too large')
        exit_code = child.wait(timeout=1)
        if exit_code != 0 or not acknowledged:
            print(json.dumps({'failure_phase': 'helper_stop', 'helper_exit_code': exit_code,
                              'stop_acknowledged': acknowledged}))
            raise RuntimeError('helper stop failed')
    print(json.dumps({'proof': 'actual-production-helper-sdk-turn', 'sessions': 1,
                      'user_turns': 1, 'observed_tool_starts': tool_starts,
                      'fixture': 'passed', 'billing': 'requires account-side evidence'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--plan', action='store_true', help='print exact frames; no model calls')
    mode.add_argument('--live', action='store_true', help='send one separately authorized SDK turn')
    args = parser.parse_args()
    if args.plan:
        for frame in frames():
            print(json.dumps(frame))
    else:
        try:
            live()
        except Exception:
            # Do not persist arbitrary native/provider errors or model text.
            print('Native Node SDK qualification failed; no retry', flush=True)
            raise SystemExit(1)
