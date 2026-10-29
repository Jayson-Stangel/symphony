"""One reviewed full-ExUnit execution profile, never a generic CI cache key.

The profile deliberately expires when relevant validation source changes. Fresh
qualification remains the fallback; publishing a new reviewed profile is separate
from observing two matching commit IDs.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time

from validation_inputs import UnknownInput, command, digest, linux_platform, tree_identity, tracked_source, untracked_inputs

PROFILE_PATH = Path(__file__).with_name('elixir-coverage-profile.json')
CONTROL_PATHS = (
    '.github/workflows/make-all.yml', '.github/workflows/change-classification.yml',
    'scripts/ci/classify_changes.py', 'scripts/ci/release_tests.py',
    'scripts/ci/presentation_only.rb', 'scripts/ci/validation_inputs.py',
    'scripts/ci/elixir_coverage_profile.py', 'scripts/ci/elixir-coverage-profile.json',
    'scripts/ci/validation_evidence.py', 'scripts/ci/elixir_coverage.py',
)
GENERATED = ('elixir/deps', 'elixir/_build', 'elixir/bin/symphony',
             'elixir/cover', 'runner/claude/node_modules')
EXPLICIT_SOURCE = {'.github/pull_request_template.md', 'scripts/codex-apparmor.profile',
                   'scripts/codex-seccomp.json'}


def classifier():
    spec = importlib.util.spec_from_file_location('coverage_consumer_selection', Path(__file__).with_name('classify_changes.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_exclusion():
    policy = classifier()
    def excluded(path):
        # Exact existing presentation assets have no fixed-suite reader. New
        # media remains conservative; do not use a directory/extension shortcut.
        if path in {'.github/media/symphony-demo.mp4', '.github/media/elixir-screenshot.png'}:
            return True
        if path in EXPLICIT_SOURCE:
            return False
        # These are bound separately, including the entire actual workflow.
        if path.startswith(('scripts/ci/', '.github/workflows/', '.github/actions/')):
            return True
        return 'application' not in policy.consumer_selection(path)[0]
    return excluded


def source_identity(root, revision):
    untracked_inputs(root, GENERATED)
    identity, rows = tracked_source(root, revision, source_exclusion())
    if any(row[1] == '120000' for row in rows):
        raise UnknownInput('source symlinks are outside this audited full-coverage profile')
    return identity, rows


def audited_code(rows):
    # The reviewed code pin is independent of a particular VM's UID/mode state;
    # actual permissions/ownership remain part of the execution fingerprint.
    return digest([row[:3] for row in rows])


def control_path(path):
    return path.startswith(('scripts/ci/', '.github/workflows/', '.github/actions/'))


def controls(root, revision):
    # An added unknown CI control must not inherit an older full-coverage proof.
    # The finite loader list above is distinct from the complete control identity.
    value, rows = tracked_source(root, revision, lambda path: not control_path(path))
    return dict(identity=value, files={row[0]: row[2] for row in rows})


def executable_path(parent):
    """No ambient CLI directories, shims, provider CLIs or user shell startup."""
    selected = {}
    for name in ('erl', 'elixir', 'mix', 'node'):
        found = shutil.which(name, path=parent.get('PATH'))
        if not found:
            raise UnknownInput('required public tool is unavailable')
        actual = Path(found).resolve(strict=True)
        if not actual.is_file():
            raise UnknownInput('required public executable is not a file')
        selected[name] = actual
    if selected['mix'].parent != selected['elixir'].parent:
        raise UnknownInput('Mix and Elixir must use the same actual distribution')
    # mise's shim is an external resolver, not the actual selected runtime.
    if any('/shims/' in str(path) for path in selected.values()):
        raise UnknownInput('actual tool paths must precede mutable mise shims')
    directories = list(dict.fromkeys(str(selected[name].parent) for name in ('elixir', 'erl', 'node')))
    return ':'.join([*directories, '/usr/bin', '/bin']), selected


def public_system_roots():
    # The fixed suite invokes shell/Git/Python helpers. Bind the public system
    # executable/library substrate rather than guessing only their entrypoints.
    required = (
        '/usr/bin', '/usr/local/bin', '/usr/sbin', '/usr/lib/x86_64-linux-gnu', '/usr/lib/python3.12',
        '/usr/lib/git-core', '/usr/share/git-core', '/usr/share/zoneinfo',
        '/usr/lib/locale', '/etc/ssl/certs', '/usr/share/ca-certificates',
        '/etc/ssl/openssl.cnf', '/etc/nsswitch.conf', '/etc/ld.so.cache',
        '/etc/profile', '/etc/profile.d', '/etc/resolv.conf',
    )
    optional = ('/usr/lib/python3/dist-packages', '/usr/local/lib/python3.12/dist-packages')
    if any(not Path(path).exists() for path in required):
        raise UnknownInput('audited public system substrate is unavailable')
    return [Path(path) for path in (*required, *optional) if Path(path).exists()]


def external_settings(environment):
    for path in ('/opt/symphony', '/etc/symphony', '/etc/codex', '/var/lib/symphony'):
        if os.path.lexists(path):
            raise UnknownInput('foreign installed configuration is not in the profile')
    lock_root = Path('/tmp') / ('symphony-runner-locks-' + str(os.getuid()))
    if os.path.lexists(lock_root):
        raise UnknownInput('pre-existing global fixture lock state is not audited')
    # Current fixed tests resolve localhost only; other hosts are mocked. Ignore
    # VM hostname labels, but bind every actual localhost mapping, not its order.
    localhost = []
    for line in Path('/etc/hosts').read_text().splitlines():
        fields = line.split('#', 1)[0].split()
        if len(fields) > 1 and any(value in {'localhost', 'ip6-localhost', 'ip6-loopback'} for value in fields[1:]):
            localhost.append(fields)
    if not any(row[0] == '127.0.0.1' and 'localhost' in row[1:] for row in localhost):
        raise UnknownInput('audited localhost resolution is unavailable')
    current = Path('/proc/self/attr/current').read_text().strip()
    if current != 'unconfined':
        raise UnknownInput('unknown process security profile')
    kernel = {}
    for path in ('/proc/sys/kernel/unprivileged_userns_clone',
                 '/proc/sys/kernel/apparmor_restrict_unprivileged_userns',
                 '/proc/sys/kernel/pid_max', '/proc/sys/fs/protected_symlinks',
                 '/proc/sys/fs/protected_hardlinks'):
        value = Path(path)
        kernel[path] = value.read_text().strip() if value.exists() else None
    # Cgroup execution/resource policy, without treating per-VM labels as code.
    rows = Path('/proc/self/cgroup').read_text().splitlines()
    if len(rows) != 1 or not rows[0].startswith('0::'):
        raise UnknownInput('only the actual cgroup-v2 execution profile is audited')
    relative = rows[0][3:]
    if '..' in Path(relative).parts:
        raise UnknownInput('unsafe cgroup membership')
    group = Path('/sys/fs/cgroup') / relative.lstrip('/')
    limits = []
    while True:
        if not group.is_dir():
            raise UnknownInput('actual cgroup membership is unavailable')
        values = {}
        for name in ('cpu.max', 'cpu.weight', 'memory.max', 'memory.swap.max', 'pids.max', 'cpuset.cpus.effective', 'cpuset.mems.effective'):
            path = group / name
            values[name] = path.read_text().strip() if path.exists() else None
        limits.append(values)
        if group == Path('/sys/fs/cgroup'):
            break
        group = group.parent
    cpu = []
    for block in Path('/proc/cpuinfo').read_text().strip().split('\n\n'):
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition(':')
            if separator and key.strip() in {'vendor_id', 'cpu family', 'model', 'model name',
                    'stepping', 'microcode', 'flags', 'bugs', 'cache size', 'address sizes'}:
                fields[key.strip()] = value.strip()
        if not fields.get('flags') or not fields.get('model name'):
            raise UnknownInput('actual CPU runtime capabilities are unavailable')
        cpu.append(fields)
    return dict(localhost=sorted(localhost), security_profile=current, kernel=kernel,
                cgroup_policy=limits, cpu=cpu, clock_ticks=os.sysconf('SC_CLK_TCK'),
                processors=os.sysconf('SC_NPROCESSORS_ONLN'))


def public_environment(environment, parent):
    fixed = dict(HEX_OFFLINE='1', MIX_ENV='test', ERL_FLAGS='+S 2:2', LANG='C.UTF-8',
                 LC_ALL='C.UTF-8', TZ='UTC', CI='true', GIT_CONFIG_NOSYSTEM='1',
                 GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_COUNT='0',
                 PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    paths = {'PATH', 'HOME', 'TMPDIR', 'HEX_HOME', 'MIX_HOME', 'MIX_ARCHIVES', 'MIX_REBAR3'}
    if (type(environment) is not dict or set(environment) != set(fixed) | paths
            or any(type(value) is not str for value in environment.values())
            or any(environment.get(key) != value for key, value in fixed.items())):
        raise UnknownInput('execution environment is outside the finite public profile')
    if not all(environment[key].startswith('/') and '\n' not in environment[key]
               and '\r' not in environment[key] for key in paths):
        raise UnknownInput('execution paths are outside the public profile')
    temporary = parent.get('RUNNER_TEMP', '')
    if type(temporary) is not str or not Path(temporary).is_absolute():
        raise UnknownInput('canonical job-private state is unavailable')
    state = Path(temporary).resolve() / 'symphony-coverage-v1'
    if any(environment[key] != str(state / leaf)
           for key, leaf in (('HOME', 'home'), ('TMPDIR', 'tmp'), ('HEX_HOME', 'hex'))):
        raise UnknownInput('execution private paths differ from the audited job profile')
    return environment


def fingerprint(root, revision, environment, parent, *, budget_seconds=30):
    """Only public byte identities; no credential stores or ambient env dump."""
    started = time.monotonic()
    # Reject accidental dict(os.environ) before any probes or serialization.
    public_environment(environment, parent)
    root = Path(root).resolve()
    profile = json.loads(PROFILE_PATH.read_text())
    source, rows = source_identity(root, revision)
    if profile.get('version') != 1 or audited_code(rows) != profile.get('audited_source_sha256'):
        raise UnknownInput('changed or unaudited full-coverage source profile')
    platform = linux_platform(parent)
    if environment.get('MIX_ENV') != 'test' or environment.get('ERL_FLAGS') != '+S 2:2':
        raise UnknownInput('unknown coverage execution settings')
    path, executables = executable_path(environment)
    if environment['PATH'] != path:
        raise UnknownInput('coverage executable search path is not canonical')
    tool_roots = [executables[name].parent.parent for name in ('erl', 'elixir', 'node')]
    mix_home = Path(environment['MIX_HOME']).resolve()
    archives = Path(environment.get('MIX_ARCHIVES', str(mix_home / 'archives'))).resolve()
    if mix_home != tool_roots[1] / '.mix':
        raise UnknownInput('Mix public executable state is outside the actual Elixir distribution')
    if Path(sys.executable).resolve() != Path('/usr/bin/python3.12').resolve():
        raise UnknownInput('only the actual system Python3.12 runtime is audited')
    if archives != mix_home / 'archives':
        raise UnknownInput('unreviewed Mix archive search path')
    if any(path.name not in {'archives', 'elixir'} for path in mix_home.iterdir()):
        raise UnknownInput('unknown global Mix executable/configuration state')
    # The exact reviewed tool versions bound the audited runtime interfaces;
    # actual full distribution/library/cache bytes are still compared below.
    versions = dict(node=command([str(executables['node']), '--version'], env=environment).decode().strip(),
                    elixir=command([str(executables['elixir']), '--short-version'], env=environment).decode().strip())
    otp = next((tool_roots[0] / 'releases').glob('*/OTP_VERSION'), None)
    if otp is None:
        raise UnknownInput('resolved full OTP version is unavailable')
    versions['otp'] = otp.read_text().strip()
    if versions != profile.get('tool_versions'):
        raise UnknownInput('changed or unaudited runtime version')
    roots = [root / 'elixir/deps', root / 'elixir/_build/test',
             root / 'runner/claude/node_modules', *tool_roots, mix_home, *public_system_roots()]
    # Symlinks may lead into tracked priv/source or standard public system roots;
    # every followed destination is hashed, and source bytes are independently bound.
    allowed = [root, *roots, Path('/usr'), Path('/lib'), Path('/lib64'), Path('/opt'), Path('/etc/ssl/certs')]
    identities = []
    for item in dict.fromkeys(roots):
        identity, _ = tree_identity(item, allowed_roots=allowed, deadline=started + budget_seconds)
        identities.append(identity)
    value = dict(schema_version=1, family='full-exunit-coverage', profile_version=profile['version'],
                 source=source, controllers=controls(root, revision), environment=environment,
                 platform=platform, external=external_settings(environment), public_inputs=identities,
                 command=['mix', 'test', '--cover', '--seed', '0', '--max-failures', '1'])
    if time.monotonic() - started > budget_seconds:
        raise UnknownInput('complete public profile exceeded its budget')
    return dict(identity=value, sha256=digest(value), elapsed_seconds=round(time.monotonic() - started, 3),
                files=sum(item['files'] for item in identities), bytes=sum(item['bytes'] for item in identities))
