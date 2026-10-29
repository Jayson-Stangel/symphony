"""One read-only full-ExUnit world, using the existing local Docker executor.

No registry publication, image transfer artifact, host home/socket mount, daemon
settings, provider credential or privileged container. Every phase owns one
container and removes it before the next; only declared job-private binds persist.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from validation_inputs import UnknownInput, digest, file_identity

DIRECTORY = Path(__file__).parent
RECIPE = DIRECTORY / 'elixir-coverage.Dockerfile'
PUBLIC = {name: DIRECTORY / ('elixir-coverage.' + name) for name in ('passwd', 'group')}
GENERATED = ('elixir/deps', 'elixir/_build/dev', 'elixir/_build/test',
             'elixir/bin', 'elixir/cover', 'elixir/log', 'runner/claude/node_modules')
PRIVATE = {'home': '/home/ci', 'tmp': '/tmp', 'mix': '/cache/mix',
           'hex': '/cache/hex', 'npm': '/cache/npm', 'native_fetch': '/cache/native-fetch'}
SOURCE = '/home/runner/work/symphony/symphony'
FAMILY = 'full-exunit-oci-v2'


def require(value, message):
    if not value:
        raise UnknownInput(message)


def client_environment():
    home = Path(os.environ['RUNNER_TEMP']) / 'symphony-coverage-docker-client-v2'
    home.mkdir(mode=0o700, exist_ok=True)
    require(not home.is_symlink() and home.stat().st_uid == os.getuid()
            and not home.stat().st_mode & 0o077, 'private Docker client directory is unsafe')
    config = home / 'config.json'
    expected = dict(cliPluginsExtraDirs=['/usr/libexec/docker/cli-plugins'])
    if not config.exists():
        fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output: json.dump(expected, output)
    require(not config.is_symlink() and json.loads(config.read_text()) == expected,
            'Docker client config differs from finite public profile')
    return dict(PATH='/usr/bin:/bin', HOME=str(home), DOCKER_CONFIG=str(home),
                DOCKER_HOST='unix:///var/run/docker.sock')


def docker(args, *, timeout=30, capture=True, allow_absent=False):
    result = subprocess.run(['/usr/bin/docker', *args], env=client_environment(),
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None, timeout=timeout)
    if allow_absent and result.returncode == 1 and (b'No such object:' in (result.stderr or b'')
            or (args[:2] == ['image','inspect'] and b'No such image:' in (result.stderr or b''))):
        return None
    if result.returncode and capture and args[:2] == ['start', '--attach']:
        # Only the credential-free execution phase, never service/client errors.
        require(len(result.stdout) + len(result.stderr) <= 4 * 1024 * 1024, 'OCI phase output exceeded bound')
        sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr)
        sys.stdout.buffer.flush(); sys.stderr.buffer.flush()
    require(result.returncode == 0, 'owned OCI operation failed')
    if capture:
        require(len(result.stdout) <= 4 * 1024 * 1024, 'OCI metadata exceeded bound')
        return result.stdout


def one(args):
    rows = json.loads(docker(args))
    require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict, 'ambiguous OCI object')
    return rows[0]


def image_identity(value):
    require(value.get('Os') == 'linux' and value.get('Architecture') == 'amd64'
            and re.fullmatch(r'sha256:[0-9a-f]{64}', value.get('Id', ''))
            and type(value.get('Config')) is dict and type(value.get('RootFS')) is dict,
            'unknown execution image identity')
    # Actual consumed image identity, not a substituted expected/local digest.
    return {key: value[key] for key in ('Id', 'Created', 'Config', 'RootFS', 'Architecture', 'Os')}


def initialize(root, state):
    require(os.getuid() == os.getgid() == 1001, 'OCI profile requires actual hosted UID/GID1001')
    require(not state.exists() and not state.is_symlink(), 'OCI state already exists')
    state.mkdir(mode=0o700)
    public = state / 'public'; public.mkdir(mode=0o700)
    for name, source in PUBLIC.items():
        target = public / name
        target.write_bytes(source.read_bytes()); target.chmod(0o644)
    for name in PRIVATE:
        (state / name).mkdir(mode=0o700)
    for relative in GENERATED:
        path = root / relative
        require(not path.is_symlink(), 'generated source bind is symlinked')
        path.mkdir(parents=True, exist_ok=True)
        require(path.stat().st_uid == 1001 and not any(path.iterdir()), 'untrusted pre-existing generated input')
    context = state / 'image-context'; context.mkdir(mode=0o700)
    shutil.copyfile(RECIPE, context / 'Dockerfile')
    owner = 'symphony-coverage-' + uuid.uuid4().hex
    tag = owner + ':runtime'
    intent = state / 'oci-intent.json'
    fd = os.open(intent, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as target:
        json.dump(dict(owner=owner, tag=tag, build_complete=False, recipe_sha256=hashlib.sha256(RECIPE.read_bytes()).hexdigest()), target)
    docker(['buildx', 'build', '--builder', 'default', '--platform', 'linux/amd64',
            '--pull=false', '--network', 'none', '--no-cache', '--provenance=false',
            '--build-arg', 'SOURCE_DATE_EPOCH=0', '--load', '--tag', tag, str(context)], timeout=900, capture=False)
    intent_value = json.loads(intent.read_text()); intent_value['build_complete'] = True
    intent.write_text(json.dumps(intent_value))
    value = image_identity(one(['image', 'inspect', tag]))
    record = dict(owner=owner, tag=tag, image=value,
                  recipe_sha256=hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
                  public={name: file_identity(state / 'public' / name) for name in PUBLIC})
    path = state / 'oci.json'; path.write_text(json.dumps(record)); path.chmod(0o600)
    return record


def record(state):
    path = state / 'oci.json'
    require(path.is_file() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and not path.stat().st_mode & 0o077, 'owned OCI record unavailable')
    value = json.loads(path.read_text())
    require(re.fullmatch('symphony-coverage-[0-9a-f]{32}', value.get('owner', '')), 'OCI owner identity invalid')
    require(image_identity(one(['image', 'inspect', value['image']['Id']])) == value['image'], 'OCI image changed')
    return value


def environment(mix='test', *, network=False):
    value = dict(HOME='/home/ci', TMPDIR='/tmp', MIX_HOME='/cache/mix', MIX_ARCHIVES='/cache/mix/archives',
        HEX_HOME='/cache/hex', ELIXIR_MAKE_CACHE_DIR='/cache/native-fetch', MIX_ENV=mix, LANG='C.UTF-8', LC_ALL='C.UTF-8', TZ='UTC', CI='true',
        PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_COUNT='0',
        PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    if network:
        value['NPM_CONFIG_CACHE'] = '/cache/npm'
    else:
        value['HEX_OFFLINE'] = '1'
    return value


def arguments(root, state, value, phase, command, *, mix='test', network=False):
    name = value['owner'] + '-' + phase + '-' + uuid.uuid4().hex[:8]
    args = ['create', '--name', name, '--label', 'com.symphony.ci.owner=' + value['owner'],
        '--read-only', '--user', '1001:1001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--network', 'bridge' if network else 'none', '--hostname', 'symphony-ci',
        '--mount', f'type=bind,src={root},dst={SOURCE},readonly', '--workdir', SOURCE + '/elixir']
    for relative in GENERATED:
        args += ['--mount', f'type=bind,src={root / relative},dst={SOURCE}/{relative}']
    for name_, destination in PRIVATE.items():
        args += ['--mount', f'type=bind,src={state / name_},dst={destination}']
    for name_, path in PUBLIC.items():
        args += ['--mount', f"type=bind,src={state / 'public' / name_},dst=/etc/{name_},readonly"]
    for key, val in sorted(environment(mix, network=network).items()):
        args += ['--env', key + '=' + val]
    # No CPU/memory limits: preserve actual hosted full-suite concurrency.
    args += ['--entrypoint', command[0], value['image']['Id'], *command[1:]]
    return name, args


def run(root, state, phase, command, *, mix='test', network=False, capture=False):
    value = record(state)
    name, args = arguments(root, state, value, phase, command, mix=mix, network=network)
    started = time.monotonic()
    identity = None
    try:
        identity = docker(args).decode().strip()
        require(re.fullmatch('[0-9a-f]{64}', identity), 'OCI create identity unavailable')
        actual = one(['inspect', identity])
        policy = admit(actual, root, state, value, network=network)
        policy_file = state / (phase + '-policy.json')
        policy_file.write_text(json.dumps(policy, sort_keys=True)); policy_file.chmod(0o600)
        result = docker(['start', '--attach', identity], timeout=1500, capture=capture)
        final = one(['inspect', identity])
        require(final['State']['Running'] is False and final['State']['ExitCode'] == 0,
                'OCI qualification phase failed')
        return result if capture else round(time.monotonic() - started, 3)
    finally:
        cleanup_phase(identity or name, value)


def admit(actual, root, state, value, *, network):
    config, host = actual['Config'], actual['HostConfig']
    require(config['Labels'].get('com.symphony.ci.owner') == value['owner']
            and actual['Image'] == value['image']['Id'] and config['User'] == '1001:1001'
            and host['ReadonlyRootfs'] is True and host['Privileged'] is False
            and host['NetworkMode'] == ('bridge' if network else 'none')
            and host['CapDrop'] == ['ALL'] and not host.get('CapAdd')
            and host['SecurityOpt'] in (['no-new-privileges'], ['no-new-privileges:true'])
            and not host.get('Devices') and not host.get('VolumesFrom')
            and not host.get('NanoCpus') and not host.get('Memory'), 'OCI phase isolation policy differs')
    expected = {SOURCE: (str(root), False)}
    expected.update({SOURCE + '/' + path: (str(root / path), True) for path in GENERATED})
    expected.update({path: (str(state / name), True) for name, path in PRIVATE.items()})
    expected.update({'/etc/' + name: (str(state / 'public' / name), False) for name in PUBLIC})
    env = dict(item.split('=', 1) for item in config['Env'])
    expected_env = dict(item.split('=', 1) for item in value['image']['Config']['Env'])
    expected_env.update(environment(env.get('MIX_ENV'), network=network))
    require(env == expected_env and len(env) == len(config['Env'])
            and config['WorkingDir'] == SOURCE + '/elixir', 'OCI phase public environment differs')
    mounts = actual.get('Mounts')
    require(type(mounts) is list and len(mounts) == len(expected)
            and {m['Destination']: (m.get('Source'), m.get('RW')) for m in mounts} == expected
            and all(m.get('Type') == 'bind' for m in mounts), 'OCI phase mount allowlist differs')
    return dict(user=config['User'], network=host['NetworkMode'], read_only=host['ReadonlyRootfs'],
                cap_drop=host['CapDrop'], security=host['SecurityOpt'], mounts=expected,
                cpu=host.get('NanoCpus'), memory=host.get('Memory'), environment=env)


def cleanup_phase(identity, value):
    raw = docker(['inspect', identity], allow_absent=True)
    if raw is None:
        # Absence is a positive service read, never an arbitrary inspection error.
        return
    actual = json.loads(raw)[0]
    require(actual['Config']['Labels'].get('com.symphony.ci.owner') == value['owner']
            and actual['Image'] == value['image']['Id'], 'cleanup object is not owned')
    exact = actual['Id']
    if actual['State']['Running']:
        docker(['stop', '--time', '10', exact], timeout=20)
        actual = one(['inspect', exact])
        require(actual['State']['Running'] is False, 'owned phase failed bounded shutdown')
    docker(['rm', exact])
    require(docker(['inspect', exact], allow_absent=True) is None, 'owned phase remains after cleanup')


PREFLIGHT = r'''
import json,os,pwd,resource,socket,subprocess,signal,tempfile,platform
from pathlib import Path
assert os.getuid()==os.getgid()==1001 and pwd.getpwuid(1001).pw_name=='ci'
assert json.loads(subprocess.check_output(['node','-e','console.log(JSON.stringify(require("os").userInfo()))']))['username']=='ci'
s=Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in s and int(next(x.split(':')[1] for x in s.splitlines() if x.startswith('CapEff:')),16)==0
assert not Path('/var/run/docker.sock').exists() and sorted(os.listdir('/sys/class/net'))==['lo']
mounts=[]
for line in Path('/proc/self/mountinfo').read_text().splitlines():
 p=line.split();i=p.index('-');dest=p[4]
 if dest in ['/', '/etc/passwd','/etc/group','/home/ci','/tmp','/cache/mix','/cache/hex','/cache/npm','/cache/native-fetch'] or dest.startswith('/home/runner/work/symphony/symphony'):
  mounts.append([dest,sorted(p[5].split(',')),p[i+1]])
assert any(p=='/' and 'ro' in flags for p,flags,fs in mounts)
assert any(p=='/home/runner/work/symphony/symphony' and 'ro' in flags for p,flags,fs in mounts)
log=Path('/home/runner/work/symphony/symphony/elixir/log')/'symphony-preflight-output'
log.write_bytes(b'owned log output capability');log.unlink()
for parent in ['/home/ci','/tmp','/cache/mix','/cache/hex','/cache/native-fetch']:
 f=Path(parent)/'symphony-preflight';f.write_text('#!/bin/sh\nexit 0\n');f.chmod(0o700);subprocess.run([str(f)],check=True);f.unlink()
p=subprocess.Popen(['sleep','30'],start_new_session=True)
try:
 assert os.getpgid(p.pid)==p.pid;os.killpg(p.pid,signal.SIGTERM);p.wait(timeout=5)
finally:
 if p.poll() is None:p.kill();p.wait()
a=socket.socket();a.bind(('127.0.0.1',0));a.listen(1)
b=socket.create_connection(a.getsockname());c,_=a.accept();b.sendall(b'x');assert c.recv(1)==b'x';b.close();c.close();a.close()
assert subprocess.check_output(['node','--version']).decode().strip()=='v24.18.0'
assert subprocess.check_output(['elixir','--short-version']).decode().strip()=='1.19.5'
assert 'Mix 1.19.5' in subprocess.check_output(['mix','--version']).decode()
limits={}
for name in ['cpu.max','memory.max','memory.swap.max','pids.max','cpuset.cpus.effective']:
 q=Path('/sys/fs/cgroup')/name;limits[name]=q.read_text().strip() if q.exists() else None
cpu=[]
for block in Path('/proc/cpuinfo').read_text().split('\n\n'):
 fields={k.strip():v.strip() for l in block.splitlines() if ':' in l for k,v in [l.split(':',1)] if k.strip() in ['vendor_id','model name','flags','microcode']}
 if fields:cpu.append(fields)
print(json.dumps(dict(kernel=list(platform.uname())[:3],cpu=cpu,processors=os.cpu_count(),cgroup=limits,
 rlimits={str(k):list(resource.getrlimit(k)) for k in [resource.RLIMIT_NOFILE,resource.RLIMIT_NPROC,resource.RLIMIT_STACK]},
 mounts=sorted(mounts),status={k:l.split(':',1)[1].strip() for l in s.splitlines() if ':' in l for k in [l.split(':',1)[0]] if k in ['CapEff','NoNewPrivs','Seccomp','Seccomp_filters']},
 network_files={n:Path('/etc/'+n).read_text() for n in ['hosts','resolv.conf','hostname']}),sort_keys=True))
'''


def preflight(root, state):
    raw = run(root, state, 'preflight', ['python3', '-I', '-c', PREFLIGHT], capture=True)
    value = json.loads(raw)
    daemon = json.loads(docker(['version', '--format', '{{json .Server}}']))
    engine = json.loads(docker(['info', '--format', '{{json .}}']))
    require(type(engine) is dict, 'Docker execution settings unavailable')
    engine = {key:engine[key] for key in ('Driver','CgroupDriver','CgroupVersion','SecurityOptions',
              'KernelVersion','OperatingSystem','Architecture','OSType','DefaultRuntime','Runtimes')}
    runtime = dict(container=value, docker=daemon, engine=engine, image=record(state)['image'],
                   recipe_sha256=hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
                   public={name: file_identity(state / 'public' / name) for name in PUBLIC},
                   environment=environment(), generated=list(GENERATED), private=PRIVATE)
    path = state / 'runtime.json'; path.write_text(json.dumps(runtime)); path.chmod(0o600)
    return runtime


def clean_tmp(state):
    path = state / 'tmp'
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid(), 'private scratch is not owned')
    shutil.rmtree(path); path.mkdir(mode=0o700)



def require_empty_log(root):
    path=root/'elixir/log'
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid==os.getuid()
            and path.resolve().is_relative_to(root.resolve()) and not any(path.iterdir()),
            'application log output is not an exact owned empty initial input')


def native_fetch_path(state):
    path=state/'native_fetch'
    require(state.is_dir() and not state.is_symlink() and state.stat().st_uid==os.getuid()
            and not state.stat().st_mode & 0o077 and path.is_dir() and not path.is_symlink()
            and path.stat().st_uid==os.getuid() and not path.stat().st_mode & 0o077,
            'native fetch scratch is not exact owned private state')
    return path


def clean_native_fetch(state):
    path=native_fetch_path(state)
    children=list(path.iterdir())
    require(all(child.name=='lazy_html-nif-2.16-x86_64-linux-gnu-0.1.10.tar.gz'
                and child.is_file() and not child.is_symlink() and child.stat().st_uid==os.getuid()
                for child in children), 'unknown native fetch scratch; refuse deletion')
    for child in children:child.unlink()
    path.rmdir();path.mkdir(mode=0o700)


def require_empty_native_fetch(state):
    require(not any(native_fetch_path(state).iterdir()), 'native fetch scratch is not empty before offline qualification')


def cleanup(state):
    intent = state / 'oci-intent.json'
    if not intent.exists():
        return  # No Docker mutation is permitted before exclusive intent.
    require(intent.is_file() and not intent.is_symlink() and intent.stat().st_uid == os.getuid()
            and not intent.stat().st_mode & 0o077, 'OCI cleanup intent is unsafe')
    value = json.loads(intent.read_text())
    require(re.fullmatch('symphony-coverage-[0-9a-f]{32}', value.get('owner', ''))
            and value.get('tag') == value['owner'] + ':runtime'
            and value.get('recipe_sha256') == hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
            'OCI cleanup ownership differs')
    raw = docker(['image', 'inspect', value['tag']], allow_absent=True)
    if raw is None:
        require(value.get('build_complete') is True, 'build completion is unknown; tag absence is not verified cleanup')
        return
    rows = json.loads(raw)
    require(type(rows) is list and len(rows) == 1, 'ambiguous owned image')
    image = image_identity(rows[0])
    require(image['Config'].get('Labels', {}).get('com.symphony.ci.profile') == 'elixir-coverage-oci-preflight-v1',
            'owned tag profile differs')
    if (state / 'oci.json').exists():
        require(record(state)['image'] == image, 'owned OCI tag changed')
    # Remove the exclusive tag only, never a shared image ID or build cache.
    docker(['image', 'rm', value['tag']])
    require(docker(['image', 'inspect', value['tag']], allow_absent=True) is None,
            'owned tag removal was not verified')
    require(value.get('build_complete') is True, 'build completion is unknown; do not claim full cleanup')
