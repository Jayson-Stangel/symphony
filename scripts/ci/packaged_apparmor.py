#!/usr/bin/env python3
"""Native hosted Linux acceptance of the shipped source-free AppArmor command.

No direct parser load prepares the host. Cleanup removes only this invocation's
verified new policy/receipt/boot guard. This does not prove reboot, authentication,
model delivery or subsequent protected execution.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
TRUSTED_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'
PROFILE_RE = re.compile(r'symphony-codex-candidate-[a-f0-9]{16}')


def require(value, message):
    if not value:
        raise ValueError('packaged AppArmor: ' + message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked(args, *, allowed=(0,), env=None, strip=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True,
                            timeout=120, env=env)
    require(result.returncode in allowed, 'command failed: ' + str(args[0]))
    return result.stdout.strip() if strip else result.stdout


def tool(name):
    value = shutil.which(name, path=TRUSTED_PATH)
    require(value is not None, 'missing host prerequisite ' + name)
    return value


def privilege(*args, allowed=(0,), strip=True):
    return checked([tool('sudo'), '-n', *args], allowed=allowed, strip=strip)


def native_context():
    require(platform.system() == 'Linux' and platform.machine() == 'x86_64',
            'native Linux x86-64 is required')
    require(os.geteuid() != 0, 'run acceptance as the ordinary CI user')
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'only an isolated GitHub-hosted runner is supported')
    require(os.environ.get('PACKAGED_HOST_PREPARATION') == 'true' and
            os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch' and
            os.environ.get('GITHUB_REPOSITORY') == 'Jayson-Stangel/symphony' and
            os.environ.get('GITHUB_ACTOR') == 'Jayson-Stangel' and
            os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'Jayson-Stangel',
            'explicit owner manual packaged host preparation consent is required')
    revision = os.environ.get('GITHUB_SHA', '')
    require(re.fullmatch('[a-f0-9]{40}', revision), 'exact workflow source is required')
    return revision


def policy_identity():
    policy = ROOT / 'scripts/codex-apparmor.profile'
    names = re.findall(r'^profile (' + PROFILE_RE.pattern + r') ', policy.read_text(), re.M)
    require(len(names) == 1, 'shipped versioned policy identity is ambiguous')
    return names[0], digest(policy)


def selected_archive(packages, manifest, revision, assembled=False):
    sys.path.insert(0, str(ROOT / 'scripts/release'))
    from manager_envelope import load_release
    import artifacts
    artifacts.validate(json.loads(manifest.read_text()), require_bundle=not assembled)
    value = load_release(manifest)
    require(value['source_revision'] == revision, 'package source differs from workflow')
    entry = value['bundles']['linux_x86_64']
    name = entry['filename']
    require(Path(name).name == name, 'archive filename is unsafe')
    archive = packages / name
    require(archive.is_file() and not archive.is_symlink(), 'native archive is missing or linked')
    checksum = digest(archive)
    if not assembled:
        require(entry.get('sha256') == checksum, 'native archive checksum differs')
    return archive, checksum


def extract_bundle(archive, destination):
    require(not destination.exists() and not destination.is_symlink(), 'extraction destination exists')
    destination.mkdir(mode=0o700)
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        require(len(members) <= 4096 and sum(member.size for member in members) <= 512 * 1024**2,
                'native archive exceeds acceptance bounds')
        names = set()
        for member in members:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and '..' not in path.parts and
                    (member.isdir() or member.isfile()) and not member.mode & 0o7022,
                    'unsafe native archive entry')
            name = str(path)
            require(name not in names, 'duplicate native archive entry')
            names.add(name)
        source.extractall(destination, members=members, filter='data')
    require(not (destination / '.git').exists(), 'source checkout is not a package')


def host_identity():
    docker = tool('docker')
    if os.environ.get('DOCKER_CONTEXT'):
        endpoint = checked([docker, 'context', 'inspect', os.environ['DOCKER_CONTEXT'],
                            '--format', '{{.Endpoints.docker.Host}}'])
    else:
        endpoint = os.environ.get('DOCKER_HOST') or checked(
            [docker, 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
    require(endpoint == 'unix:///var/run/docker.sock', 'selected daemon is not the local rootful host')
    hostname = checked([tool('hostname')])
    require(checked([docker, 'info', '--format', '{{.Name}}']) == hostname and
            checked([docker, 'info', '--format', '{{.OSType}}']) == 'linux', 'daemon host differs')
    identifier = checked([docker, 'info', '--format', '{{.ID}}'])
    require(re.fullmatch('[A-Za-z0-9:-]+', identifier), 'invalid daemon identity')
    security = checked([docker, 'info', '--format', '{{range .SecurityOptions}}{{println .}}{{end}}'])
    require('name=apparmor' in security.splitlines() and 'name=rootless' not in security.splitlines(),
            'native rootful AppArmor acceptance cannot be omitted')
    return hostname, identifier


def paths(profile):
    return {'policy': Path('/etc/apparmor.d') / profile,
            'ownership': Path('/etc/apparmor.d') / ('.' + profile + '.symphony'),
            'boot_guard': Path('/etc/systemd/system/docker.service.d') / ('50-' + profile + '.conf')}


def expected_contents(profile, checksum, parser):
    return {'policy': (ROOT / 'scripts/codex-apparmor.profile').read_text(),
            'ownership': f'symphony-apparmor-v1\n{profile}\n{checksum}\n',
            'boot_guard': ('[Unit]\nRequires=apparmor.service\nAfter=apparmor.service\n[Service]\n'
                           f'ExecStartPre={parser} --replace --skip-cache /etc/apparmor.d/{profile}\n')}


def exists(path):
    result = subprocess.run([tool('sudo'), '-n', tool('test'), '-e', str(path)],
                            capture_output=True, timeout=20)
    require(result.returncode in (0, 1), 'host presence inspection failed')
    return result.returncode == 0


def profiles():
    return privilege(tool('cat'), '/sys/kernel/security/apparmor/profiles').splitlines()


def save(path, receipt):
    require(not path.is_symlink(), 'linked evidence destination')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def prepare(packages, manifest, evidence, assembled):
    revision = native_context()
    require(not evidence.exists() and not evidence.is_symlink(), 'evidence already exists')
    archive, checksum = selected_archive(packages, manifest, revision, assembled)
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    work = Path(tempfile.mkdtemp(prefix='symphony-packaged-apparmor-', dir=temporary))
    bundle = work / 'bundle'
    extract_bundle(archive, bundle)
    helper = bundle / 'libexec/symphony-host'
    launcher = bundle / 'scripts/symphony'
    checked([helper, 'verify-files', bundle])
    require(checked([helper, 'build-revision']) == revision and
            checked([helper, 'host-platform']) == 'linux_x86_64', 'native helper source/platform differs')
    environment = {'PATH': TRUSTED_PATH, 'HOME': str(work),
                   'DOCKER_HOST': 'unix:///var/run/docker.sock'}
    version = json.loads(checked([launcher, '--version'], env=environment))
    require(version['source_revision'] == revision, 'real packaged launcher source differs')
    profile, policy_sha = policy_identity()
    require(digest(bundle / 'scripts/codex-apparmor.profile') == policy_sha, 'packaged policy differs')
    hostname, daemon = host_identity()
    parser = tool('apparmor_parser')
    destinations = paths(profile)
    for path in destinations.values():
        require(not exists(path) and not path.is_symlink(), 'owned persistence path already exists')
    require(not any(row.startswith(profile + ' (') for row in profiles()), 'versioned profile already loaded')
    enabled = checked([tool('systemctl'), 'is-enabled', 'apparmor.service'], allowed=(0, 1))
    require(enabled in ('enabled', 'disabled'), 'unsupported original AppArmor service state')
    receipt = {'schema': 1, 'source_revision': revision, 'package_sha256': checksum,
               'package_filename': archive.name, 'helper_sha256': digest(helper),
               'profile': profile, 'policy_sha256': policy_sha, 'parser': parser,
               'host': hostname, 'kernel': platform.release(), 'daemon_id': daemon,
               'service_enabled_before': enabled, 'work': str(work), 'prepared': False,
               'profile_absent_before': True, 'scope': 'native packaged preparation only; reboot/auth/protected execution unverified'}
    receipt['dropdir_existed_before'] = exists(Path('/etc/systemd/system/docker.service.d'))
    marker = {'source_revision': revision, 'package_sha256': checksum,
              'evidence': str(evidence.resolve())}
    save(work / 'ownership.json', marker)
    receipt['work_marker_sha256'] = digest(work / 'ownership.json')
    save(evidence, receipt)  # Own partial effects before the actual administrator command.
    command = [tool('env'), '-i', 'PATH=' + TRUSTED_PATH, 'HOME=' + str(work),
               'DOCKER_HOST=unix:///var/run/docker.sock', launcher,
               'runner', 'apparmor', '--apply', '--daemon-id', daemon]
    checked([launcher, 'runner', 'apparmor'], env=environment)
    for phase in ('fresh', 'repeat'):
        result = subprocess.run([tool('sudo'), '-n', *[str(arg) for arg in command]],
                                capture_output=True, text=True, timeout=120)
        # This env-clean product command has no account/config/model input. Its
        # own daemon diagnostics redact endpoints; preserve actual failures.
        with evidence.with_suffix('.commands.log').open('a') as log:
            log.write(json.dumps({'phase': phase, 'exit_code': result.returncode,
                                  'stdout': result.stdout, 'stderr': result.stderr}) + '\n')
        require(result.returncode == 0, 'actual packaged administrator command failed (' + phase + ')')
    require(profile + ' (enforce)' in profiles(), 'prepared policy is not enforced')
    for name, path in destinations.items():
        require(privilege(tool('cat'), path, strip=False) == expected_contents(profile, policy_sha, parser)[name],
                'installed policy/receipt/boot guard bytes differ')
    require(digest(archive) == checksum, 'package changed during preparation')
    receipt.update(prepared=True, repeated=True, boot_guard=True, archive_unchanged=True)
    save(evidence, receipt)
    return receipt


def verify(packages, manifest, evidence, assembled=False, cleanup_required=True):
    revision = native_context()
    archive, checksum = selected_archive(packages, manifest, revision, assembled=assembled)
    receipt = json.loads(evidence.read_text())
    profile, policy_sha = policy_identity()
    require(receipt['source_revision'] == revision and receipt['package_sha256'] == checksum and
            receipt['package_filename'] == archive.name and receipt['profile'] == profile and
            receipt['policy_sha256'] == policy_sha and (not cleanup_required or receipt.get('cleanup') ==
            'verified owned profile/persistence removal; Docker not restarted') and all(receipt[key] is True for key in
            ('profile_absent_before', 'prepared', 'repeated', 'boot_guard', 'archive_unchanged')),
            'missing or mismatched native packaged preparation evidence')
    return receipt


def cleanup(evidence):
    revision = native_context()
    if not evidence.exists():
        return {'cleanup': 'no owned preparation effects'}
    receipt = json.loads(evidence.read_text())
    profile, policy_sha = policy_identity()
    require(receipt['source_revision'] == revision and receipt['profile'] == profile and
            receipt['policy_sha256'] == policy_sha and receipt['parser'] == tool('apparmor_parser'),
            'cleanup identity differs')
    require(receipt.get('service_enabled_before') in ('enabled', 'disabled') and
            type(receipt.get('dropdir_existed_before')) is bool and
            receipt.get('profile_absent_before') is True,
            'cleanup original state is malformed')
    work = Path(receipt['work'])
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    require(work.parent == temporary and work.name.startswith('symphony-packaged-apparmor-') and
            work.resolve() == work and not work.is_symlink() and
            digest(work / 'ownership.json') == receipt['work_marker_sha256'], 'owned extraction identity differs')
    expected = expected_contents(profile, policy_sha, receipt['parser'])
    destinations = paths(profile)
    for name, path in destinations.items():
        if exists(path):
            require(not path.is_symlink() and
                    privilege(tool('stat'), '-c', '%u %a %F', path) == '0 644 regular file' and
                    privilege(tool('cat'), path, strip=False) == expected[name],
                    'owned file changed; cleanup refused')
    if any(row.startswith(profile + ' (') for row in profiles()):
        require(exists(destinations['policy']), 'loaded profile lacks owned policy bytes')
        privilege(receipt['parser'], '--remove', destinations['policy'])
        require(not any(row.startswith(profile + ' (') for row in profiles()), 'owned profile remains loaded')
    for path in destinations.values():
        if exists(path):
            privilege(tool('rm'), '--', path)
    privilege(tool('systemctl'), 'daemon-reload')
    if receipt['service_enabled_before'] == 'disabled':
        privilege(tool('systemctl'), 'disable', 'apparmor.service')
    require(checked([tool('systemctl'), 'is-enabled', 'apparmor.service'], allowed=(0, 1)) ==
            receipt['service_enabled_before'], 'service enablement restoration unverified')
    if not receipt['dropdir_existed_before']:
        privilege(tool('rmdir'), '/etc/systemd/system/docker.service.d')
    require(not any(exists(path) or path.is_symlink() for path in destinations.values()) and
            not any(row.startswith(profile + ' (') for row in profiles()),
            'owned policy or persistence removal is unverified')
    shutil.rmtree(work)
    receipt['cleanup'] = 'verified owned profile/persistence removal; Docker not restarted'
    save(evidence, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'verify', 'cleanup'))
    parser.add_argument('--packages', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--assembled', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'cleanup':
            result = cleanup(args.evidence)
        else:
            require(args.packages is not None and args.manifest is not None, 'package inputs required')
            result = (prepare(args.packages, args.manifest, args.evidence, args.assembled) if args.mode == 'prepare'
                      else verify(args.packages, args.manifest, args.evidence, args.assembled))
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(str(error) if isinstance(error, ValueError) else 'packaged AppArmor acceptance refused; inspect owned local evidence', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
