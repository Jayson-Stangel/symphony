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

def mounts(data):
    result = [dict(item) for item in data.get('mounts', [])]
    for item in result:
        if item['Destination'] == '/etc/symphony/WORKFLOW.md':
            item.update(Type=data.get('workflow_type', 'bind'), Source=data['workflow'],
                        Destination=data.get('workflow_destination', '/etc/symphony/WORKFLOW.md'),
                        RW=data.get('workflow_rw', False))
    return result

def full_inspect(data):
    # Reconstruct from observed launch arguments, preserving later test mutations.
    # Missing old labels/env/mounts stay missing; never infer v2 authority.
    labels = {key: data[key.removeprefix('com.symphony.')]
              for key in data.get('label_keys', []) if key.removeprefix('com.symphony.') in data}
    return {'Image': data['image'], 'Config': {'Env': data.get('env', []), 'Labels': labels},
            'Mounts': mounts(data)}
cmd = args[0]
if cmd == 'info':
    print('name=seccomp,profile=builtin')
elif cmd == 'inspect':
    hide = root / 'hide-inspect-once'
    if '--format' not in args and hide.exists() and hide.read_text() == args[-1]:
        hide.unlink(); sys.exit(1)
    data = read(args[-1])
    if '--format' in args:
        fmt = option('--format')
        if 'State.Running' in fmt: print(str(data['running']).lower())
        elif 'State.Restarting' in fmt: print('false')
        elif 'State.Pid' in fmt: print('42')
        elif 'Mounts' in fmt:
            if '{{json .Mounts}}' in fmt:
                print(json.dumps(mounts(data)))
            elif '.Source' in fmt: print(data['workflow'] + ' /etc/symphony/WORKFLOW.md false')
            else: print('\n'.join(item.get('Name', '') + ' ' + item['Destination'] for item in mounts(data)))
        elif '{{.Image}}' in fmt: print(data['image'])
        elif 'com.symphony.' in fmt:
            key = fmt.split('com.symphony.')[1].split('"')[0]
            print(data.get(key, '<no value>'))
        elif 'Entrypoint' in fmt: print('/usr/local/bin/symphony-runner')
        elif 'PortBindings' in fmt: print('127.0.0.1 ' + data['port'])
    else:
        print(json.dumps([full_inspect(data)]))
elif cmd == 'image':
    if 'com.symphony.agent' in ' '.join(args): print('claude' if 'claude' in args[-1] else 'codex')
    elif 'Config.User' in ' '.join(args): print('11001:11001')
    else: print('fixture-claude-image' if 'claude' in args[-1] else 'sha256:' + 'a' * 64)
elif cmd == 'volume':
    if '--format' in args: print(args[-1].removesuffix('-claude-auth'))
elif cmd == 'run':
    if '--detach' in args:
        name = option('--name')
        if path(name).exists(): sys.exit(1)
        data = {'running': True, 'image': args[-1], 'credentials': option('--env-file'), 'env': [], 'mounts': [], 'label_keys': []}
        for index, item in enumerate(args):
            if item == '--label':
                raw_key, value = args[index + 1].split('=', 1)
                data['label_keys'].append(raw_key)
                data[raw_key.removeprefix('com.symphony.')] = value
            elif item == '--env':
                data['env'].append(args[index + 1])
            elif item == '--mount':
                fields = dict(part.split('=', 1) if '=' in part else (part, True)
                              for part in args[index + 1].split(','))
                mount = {'Type': fields['type'], 'Source': fields['source'],
                         'Destination': fields['target'], 'RW': 'readonly' not in fields}
                data['mounts'].append(mount)
                if mount['Destination'] == '/etc/symphony/WORKFLOW.md': data['workflow'] = mount['Source']
            elif item == '--volume':
                parts = args[index + 1].split(':')
                if len(parts) < 2: continue
                mount = {'Type': 'bind' if parts[0].startswith('/') else 'volume', 'Source': parts[0],
                         'Destination': parts[1], 'RW': parts[2:] != ['ro']}
                if mount['Type'] == 'volume': mount['Name'] = parts[0]
                data['mounts'].append(mount)
                if mount['Destination'] == '/etc/symphony/WORKFLOW.md': data['workflow'] = mount['Source']
        save(name, data)
    elif args[-1:] == ['--codex-config-snapshot'] and option('--entrypoint') == '/opt/symphony/elixir/bin/symphony':
        print(json.dumps({'schema': 1, 'mode': 'codex-config-snapshot', 'profile': 'trust-ro-v2',
                          'config_base64': '', 'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                          'source': {'present': False}}))
    elif '--entrypoint' in args and option('--entrypoint') == '/usr/bin/stat':
        assert args[-3:] == ['-c', '%u', '/run/symphony-config.toml']
        fields = dict(part.split('=', 1) if '=' in part else (part, True) for part in option('--mount').split(','))
        assert fields['target'] == '/run/symphony-config.toml' and 'readonly' in fields
        print(pathlib.Path(fields['source']).stat().st_uid)
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
