#!/usr/bin/env python3
"""Start the dedicated VM; use the normal Actions service and job cleanup.

No registration, queue, watcher, grants, custom completion state or recovery loop.
Run only after separately approved installation/activation in the dedicated VM.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time

REPOSITORY = 'https://github.com/Jayson-Stangel/symphony'
NAME = 'symphony-macos14'
VM = 'symphony-ci-macos14'
RUNNER = Path('/Users/admin/.local/share/symphony-ci-runner-25a')
PYTHON = Path('/Users/admin/.local/share/symphony-ci-tools-25a/python/bin/python3')
PERSISTENT_HOME = Path('/Users/jayson/Library/Application Support/Symphony Mac CI Persistent')


def require(value, message):
    if not value: raise RuntimeError(message)


def command(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, capture_output=True,
                          timeout=kwargs.pop('timeout', 30), **kwargs).stdout


def service(root=RUNNER):
    # Normal runner configuration produced by config.sh / svc.sh install.
    config = json.loads((root / '.runner').read_text(encoding='utf-8-sig'))
    require(config.get('gitHubUrl', '').rstrip('/') == REPOSITORY and config.get('agentName') == NAME,
            'runner must be registered only to the Symphony repository')
    plist = Path((root / '.service').read_text().strip())
    require(plist.resolve() == plist and plist.parent == Path.home() / 'Library/LaunchAgents',
            'runner service is not this guest user native LaunchAgent')
    data = plistlib.loads(plist.read_bytes()); label = data['Label']
    require(re.fullmatch(r'actions\.runner\.[A-Za-z0-9_.-]+', label)
            and data['ProgramArguments'] == [str(root / 'runsvc.sh')]
            and data['WorkingDirectory'] == str(root), 'native runner service differs')
    return f'gui/{os.getuid()}/{label}', plist


def require_guest():
    require(Path.home() == Path('/Users/admin') and command(['uname', '-sm']).strip() == b'Darwin arm64'
            and command(['sw_vers', '-productVersion']).startswith(b'14.'),
            'requires dedicated macOS14 ARM64 guest; no host service/power effects')


def guest_start(root=RUNNER):
    require_guest()
    mounts = command(['/sbin/mount']).lower()
    require(b'virtiofs' not in mounts and b'my shared files' not in mounts, 'host filesystem share is not allowed')
    require(sys.version_info >= (3, 11), 'guest requires Python3.11+')
    expected_env = {
        'TMPDIR=' + str(root / '_work/_temp'),
        'ACTIONS_RUNNER_HOOK_JOB_COMPLETED=' + str(root.parent / 'persistent-mac-job-completed.sh'),
    }
    require(expected_env <= set((root / '.env').read_text().splitlines()), 'native runner cleanup environment differs')
    target, plist = service(root)
    disabled = command(['launchctl', 'print-disabled', f'gui/{os.getuid()}']).decode()
    label = target.rsplit('/', 1)[1]
    require(not re.search('"' + re.escape(label) + r'"\s*=>\s*true', disabled),
            'runner service disabled after unsafe cleanup; operator repair required')
    # Inspect native state, never kickstart -k an accepting service. A loaded
    # LaunchAgent may have exited; loaded does not mean running.
    current = subprocess.run(['launchctl', 'print', target], capture_output=True, timeout=10)
    if current.returncode == 0 and re.search(rb'\bpid = [1-9][0-9]*', current.stdout):
        print('Using existing native runner service; active work preserved.')
        return
    if current.returncode:
        # Unlike svc.sh start (load -w), bootstrap does not re-enable a service
        # disabled by the cleanup hook. Do not reset native disabled state.
        command(['launchctl', 'bootstrap', f'gui/{os.getuid()}', plist])
    else:
        command(['launchctl', 'kickstart', '-p', target])  # no -k: preserve any concurrent active instance
    state = command(['launchctl', 'print', target])
    require(re.search(rb'\bpid = [1-9][0-9]*', state), 'native service did not start; inspect launchctl state')
    print('Started native runner service.')


def stop_service(root=RUNNER):
    require_guest()  # Never stop host services or halt the host on identity uncertainty.
    errors = []
    try:
        target, _ = service(root)
        # Disable native automatic startup first. Bootout unloads the accepting
        # service/process group; killing Listener alone lets RunnerService respawn.
        for argv in (['launchctl', 'disable', target], ['launchctl', 'bootout', target]):
            try: command(argv)
            except Exception: errors.append(' '.join(argv[:2]))
    except Exception:
        errors.append('native service identity')
    if errors:
        print('Native service stop unverified; halt the dedicated guest.', file=sys.stderr)
        command(['sudo', '-n', '/sbin/shutdown', '-h', 'now'])
    # Bootout may terminate this hook with its parent service. Later host checks
    # inspect normal launchctl/runner state, never a custom cleanup receipt.


def clean_job(env=os.environ, root=RUNNER):
    require_guest()
    try:
        service(root)
        require(env.get('GITHUB_REPOSITORY') == 'Jayson-Stangel/symphony', 'foreign repository job')
        work = root / '_work'
        require(env.get('TMPDIR') == str(work / '_temp'), 'job temp must use native runner temp')
        workspace = Path(env['GITHUB_WORKSPACE'])
        require(work.resolve() == work and workspace.resolve() == workspace
                and workspace.parent.parent == work and workspace.name == 'symphony',
                'workspace is outside the dedicated runner job directory')
        # Real DMG fixture images live in workspace or native RUNNER_TEMP/TMPDIR.
        # Do not touch other disks. Detach before removing their backing files.
        images = plistlib.loads(command(['hdiutil', 'info', '-plist'])).get('images', [])
        for image in images:
            path = Path(image.get('image-path', '/nonexistent'))
            if not path.is_relative_to(work): continue
            require(path.resolve() == path, 'DMG backing path is redirected')
            for entity in image.get('system-entities', []):
                if not entity.get('mount-point'): continue
                device = entity.get('dev-entry', '')
                require(re.fullmatch(r'/dev/disk[0-9]+(?:s[0-9]+)*', device), 'DMG device is uncertain')
                command(['hdiutil', 'detach', device])
        if workspace.exists(): shutil.rmtree(workspace)
        require(not workspace.exists(), 'job workspace cleanup failed')
        # Checkout post actions run before this hook. Runner FinalizeJob then
        # cleans tracked orphan processes; CompleteJobAsync cleans native temp
        # AFTER pending log uploads. Do not delete current runner temp here.
    except Exception:
        print('Unsafe job cleanup: stopping native runner service.', file=sys.stderr)
        stop_service(root)
        raise


def start_vm(tart, mac, home=PERSISTENT_HOME, clock=time.monotonic, pause=time.sleep):
    require(home == PERSISTENT_HOME and home.resolve() == home and home.stat().st_uid == os.getuid(),
            'requires the separate owned persistent Tart home')
    vm = home / 'vms' / VM
    require(vm.resolve() == vm and vm.is_dir() and vm.stat().st_uid == os.getuid(), 'dedicated VM path/owner differs')
    require(re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', mac), 'explicit reviewed VM MAC required')
    env = dict(os.environ, TART_HOME=str(home), TART_NO_AUTO_PRUNE='1')
    state = json.loads(command([tart, 'get', VM, '--format', 'json'], env=env))
    require(state.get('MACAddress') == mac and type(state.get('Running')) is bool, 'dedicated VM identity differs')
    if not state['Running']:
        with (home / 'vm.log').open('ab') as log:
            subprocess.Popen([str(tart), 'run', VM, '--no-graphics', '--no-audio',
                '--no-usb-accessories', '--no-clipboard'], env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    # A short boot readiness wait, not an Actions watcher. The one standard
    # guest service receives queued jobs without any host command between jobs.
    end = clock() + 120
    while clock() < end:
        try:
            command([tart, 'exec', VM, '/usr/bin/true'], env=env, timeout=min(5, end-clock()))
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pause(min(2, max(0, end-clock())))
    require(clock() < end, 'VM boot unavailable; inspect native Tart logs')
    # A guest/service refusal is not a retryable boot probe or permission to
    # re-enable a disabled runner. Start/check exactly once after exec is ready.
    command([tart, 'exec', VM, str(PYTHON), str(RUNNER.parent / 'persistent_mac_runner.py'),
             'guest-start'], env=env, timeout=min(30, end-clock()))
    print('Dedicated persistent VM and native runner service are up.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    start = sub.add_parser('start'); start.add_argument('--tart', type=Path, required=True)
    start.add_argument('--mac-address', required=True)
    sub.add_parser('guest-start'); sub.add_parser('guest-clean')
    args = parser.parse_args()
    try:
        if args.mode == 'start': start_vm(args.tart, args.mac_address)
        elif args.mode == 'guest-start': guest_start()
        else: clean_job()
        return 0
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print('Persistent runner operation refused; inspect native logs/state.', file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
