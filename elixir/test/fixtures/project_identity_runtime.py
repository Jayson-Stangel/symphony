#!/usr/bin/env python3
"""Container fixture keyed by actual resource names, with one shared store."""
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['IDENTITY_RUNTIME'])
root.mkdir(exist_ok=True)
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
def path(name):
    return root / (name + '.json')
def read(name):
    if not path(name).exists():
        sys.exit(1)
    return json.loads(path(name).read_text())
def save(name, data):
    path(name).write_text(json.dumps(data))
def option(flag):
    return args[args.index(flag) + 1]
cmd = args[0]
if cmd == 'inspect':
    data = read(args[-1])
    if '--format' in args:
        fmt = option('--format')
        if 'State.Running' in fmt: print(str(data['running']).lower())
        elif 'State.Restarting' in fmt: print('false')
        elif 'State.Pid' in fmt: print('42')
        elif 'Mounts' in fmt:
            if '.Source' in fmt: print(data['workflow'] + ' /etc/symphony/WORKFLOW.md false')
            else: print(data['storage'] + '-workspaces /workspaces\n' + data['storage'] + '-state /var/lib/symphony\n' + os.environ['SYMPHONY_RUNNER_NAMESPACE'].join(['symphony-', '-codex-auth']) + ' /root/.codex')
        elif '{{.Image}}' in fmt: print(data['image'])
        elif 'com.symphony.' in fmt:
            key = fmt.split('com.symphony.')[1].split('"')[0]
            print(data.get(key, '<no value>'))
        elif 'Entrypoint' in fmt: print('/usr/local/bin/symphony-runner')
        elif 'PortBindings' in fmt: print('127.0.0.1 ' + data['port'])
elif cmd == 'image':
    if 'com.symphony.agent' in ' '.join(args): print('claude' if 'claude' in args[-1] else 'codex')
    elif 'Config.User' in ' '.join(args): print('11001:11001')
    else: print('fixture-claude-image' if 'claude' in args[-1] else 'fixture-codex-image')
elif cmd == 'volume':
    if '--format' in args: print(args[-1].removesuffix('-claude-auth'))
elif cmd == 'run':
    if '--detach' in args:
        name = option('--name')
        if path(name).exists(): sys.exit(1)
        data = {'running': True, 'image': args[-1], 'credentials': option('--env-file')}
        for index, item in enumerate(args):
            if item == '--label':
                key, value = args[index + 1].removeprefix('com.symphony.').split('=', 1)
                data[key] = value
            if item == '--mount': data['workflow'] = args[index + 1].split('source=')[1].split(',target=')[0]
            if item == '--volume' and args[index + 1].endswith('/etc/symphony/WORKFLOW.md:ro'): data['workflow'] = args[index + 1].split(':/etc/symphony/WORKFLOW.md:ro')[0]
        save(name, data)
    elif 'check' in args:
        print(json.dumps({'ready': True, 'profile': 'a' * 64}))
elif cmd in ['stop', 'start']:
    data = read(args[-1]); data['running'] = cmd == 'start'; save(args[-1], data)
elif cmd == 'rm':
    path(args[-1]).unlink()
elif cmd == 'rename':
    source, target = args[1:]
    read(source)
    if path(target).exists(): sys.exit(1)
    path(source).rename(path(target))
    fault = root / 'fail-rename-once'
    if fault.exists():
        fault.unlink(); sys.exit(97)
# Other image build/tag, ps, auth and ownership preparation calls succeed.
