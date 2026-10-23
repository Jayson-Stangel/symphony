#!/usr/bin/env python3
"""Exercise actual native runtime identity checks and the production shell call without Docker."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--helper', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = p.parse_args()
    core = json.loads((args.manifest.parent / 'manager-core.json').read_text())
    count = 0
    with tempfile.TemporaryDirectory(prefix='runtime-image-identity-') as folder:
        root = Path(folder)
        def check(receipt, provider, arch, accepted, reference=None):
            nonlocal count
            selected = reference or core['distribution'][provider][arch]
            result = subprocess.run([str(args.helper), 'validate-runtime-image', str(args.manifest), provider, arch, selected],
                                    input=json.dumps(receipt), text=True, capture_output=True)
            assert (result.returncode == 0) == accepted, (accepted, result.stderr)
            count += 1
        for provider in ('codex', 'claude'):
            for arch in ('arm64', 'x86_64'):
                record = core['original_components'][provider][arch]
                reference = core['distribution'][provider][arch]
                digest = record['image'].split('@')[1]
                classic = {'Id': record['image_config'], 'Os': 'linux', 'Architecture': 'arm64' if arch == 'arm64' else 'amd64',
                           'Config': {'Labels': {'org.opencontainers.image.revision': record['source_revision'],
                            'com.symphony.agent': provider, 'com.symphony.inputs.sha256': record['input_identity'], 'com.symphony.protocol': '1'}}}
                manifest = dict(classic, Id=digest, RepoDigests=[reference], Descriptor={
                    'digest': digest, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'})
                check([classic], provider, arch, True)
                check([manifest], provider, arch, True)
                docker_schema = copy.deepcopy(manifest)
                docker_schema['Descriptor']['mediaType'] = 'application/vnd.docker.distribution.manifest.v2+json'
                check([docker_schema], provider, arch, True)
                for field, value in (('Id', 'sha256:' + '8' * 64), ('RepoDigests', []),
                                     ('RepoDigests', ['foreign/repo@' + digest]),
                                     ('Descriptor', {'digest': 'sha256:' + '8' * 64, 'mediaType': manifest['Descriptor']['mediaType']}),
                                     ('Descriptor', {'digest': digest, 'mediaType': 'application/vnd.oci.image.index.v1+json'}),
                                     ('Descriptor', {'digest': digest, 'mediaType': 'foreign'}),
                                     ('Architecture', 'foreign'), ('Os', 'foreign')):
                    altered = copy.deepcopy(manifest); altered[field] = value
                    check([altered], provider, arch, False)
                for key in classic['Config']['Labels']:
                    altered = copy.deepcopy(manifest); altered['Config']['Labels'][key] = 'foreign'
                    check([altered], provider, arch, False)
                check([manifest], provider, arch, False, reference='foreign/repo@' + digest)
                check([manifest, manifest], provider, arch, False)
                check([], provider, arch, False)
                if provider == 'codex' and arch == 'arm64':
                    saved = (manifest, record, reference)
        manifest, record, reference = saved
        # A writer that never closes must be rejected after the bounded prefix,
        # rather than buffered until EOF. Both subprocesses belong to this test.
        producer = subprocess.Popen(['/usr/bin/python3', '-c',
            "import os\ntry:\n while True: os.write(1,b' '*65536)\nexcept BrokenPipeError: pass"], stdout=subprocess.PIPE)
        bounded = subprocess.Popen([str(args.helper), 'validate-runtime-image', str(args.manifest),
            'codex', 'arm64', reference], stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        producer.stdout.close()
        try:
            _, error = bounded.communicate(timeout=10)
            assert bounded.returncode != 0 and b'bounded input' in error, error
            producer.wait(timeout=10)
            count += 1
        finally:
            for process in (bounded, producer):
                if process.poll() is None: process.kill(); process.wait()
        # Execute the exact production function, with only the container runtime
        # stubbed. Its final full JSON receipt is processed by the real helper.
        source = (Path(__file__).resolve().parent.parent / 'symphony-package.sh').read_text()
        function = source[source.index('package_release_image() {'):source.index('# Validate retained project images')]
        stub = root / 'runtime'
        stub.write_text('''#!/usr/bin/env python3
import json,os,sys
v=json.load(open(os.environ['IMAGE_RECEIPT']))[0]
a=sys.argv[1:]
if a[:2]==['image','inspect']:
 if '--format' not in a: print(json.dumps([v])); raise SystemExit(0)
 f=a[a.index('--format')+1]
 if f=='{{.Os}}/{{.Architecture}}': print(v['Os']+'/'+v['Architecture'])
 else:
  key=f.split('"')[1]; print(v['Config']['Labels'][key])
 raise SystemExit(0)
raise SystemExit(97)
'''); stub.chmod(0o755)
        receipt = root / 'inspect.json'
        script = root / 'check.sh'
        script.write_text('set -euo pipefail\n' + function + '''
die() { echo "$*" >&2; return 1; }
package_helper() { "$HELPER" "$@"; }
package_image() { printf '%s\\n' "$IMAGE"; }
package_revision="$REVISION"; package_arch=arm64
package_release_image "$RUNTIME" "$IMAGE" codex "$REVISION"
''')
        env = dict(os.environ, HELPER=str(args.helper), RUNTIME=str(stub), IMAGE=reference,
                   REVISION=core['manager_source_revision'], IMAGE_RECEIPT=str(receipt), symphony_home=str(args.manifest.parent))
        for accepted in (True, False):
            actual = copy.deepcopy(manifest)
            if not accepted:
                # Even exact labels/config cannot authorize a transport manifest.
                actual['Id'] = actual['Descriptor']['digest'] = 'sha256:' + '8' * 64
                actual['RepoDigests'] = [reference.split('@')[0] + '@sha256:' + '8' * 64]
            receipt.write_text(json.dumps([actual]))
            result = subprocess.run(['/bin/bash', str(script)], env=env, capture_output=True, text=True)
            assert (result.returncode == 0) == accepted, (accepted, result.stderr)
            count += 1
    print(json.dumps({'passed': True, 'checks': count, 'scope': 'real native helper and production function; stub runtime only, no Docker'}))


if __name__ == '__main__': main()
