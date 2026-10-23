#!/usr/bin/env python3
"""Offline native validation of finite receipts; consumes an explicit source-bound helper/core."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--helper', type=Path, required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--generic-control', type=Path, help='actual Git C/D inputs with explicitly synthetic native metadata')
    args = parser.parse_args()
    core = json.loads(args.core.read_text())
    transition = core['compatibility']['input_transition']
    revision = core['manager_source_revision']
    selections = {provider: {arch: {
        'schema_version': 1, 'authority_sha256': transition['authority_sha256'],
        'source_revision': transition['source_revision'], 'manager_source_revision': revision,
        'provider': provider, 'architecture': arch,
        'selected_input_identity': transition['source_input_identities'][provider][arch],
        'desired_input_identity': transition['manager_input_identities'][provider][arch],
        'changes': transition['changes'],
    } for arch in ('arm64', 'x86_64')} for provider in ('codex', 'claude')}
    manifest = {'schema_version': 2, 'version': core['base_version'], 'source_revision': revision,
                'channel': core['channel'], 'host_architectures': ['arm64', 'x86_64'],
                'supported_hosts': core['supported_hosts'], 'compatibility': {'runner_protocol': 1},
                'runners': core['original_components'], 'distribution': core['distribution'],
                'component_selections': selections,
                'bundle': {'filename': 'symphony-' + core['base_version'] + '-macos.tar.gz', 'architectures': ['arm64']}}
    checked = 0
    with tempfile.TemporaryDirectory(prefix='native-component-selection-') as directory:
        path = Path(directory) / 'release.json'
        def validate(value, accepted):
            nonlocal checked
            path.write_text(json.dumps(value))
            result = subprocess.run([str(args.helper), 'validate-manifest', str(path)], capture_output=True, text=True)
            assert (result.returncode == 0) == accepted, (accepted, result.stderr)
            checked += 1
        validate(manifest, True)
        for provider in ('codex', 'claude'):
            for arch in ('arm64', 'x86_64'):
                for field, value in (('authority_sha256', '0' * 64), ('source_revision', '0' * 40),
                                     ('manager_source_revision', '0' * 40), ('provider', 'foreign'),
                                     ('architecture', 'foreign'), ('selected_input_identity', '0' * 64),
                                     ('desired_input_identity', '0' * 64), ('schema_version', True), ('changes', [])):
                    changed = copy.deepcopy(manifest)
                    changed['component_selections'][provider][arch][field] = value
                    validate(changed, False)
                changed = copy.deepcopy(manifest)
                del changed['component_selections'][provider][arch]
                validate(changed, False)
        changed = copy.deepcopy(manifest)
        del changed['component_selections']
        validate(changed, False)
        # A qualified current-source provider requires no finite exception;
        # retaining that provider's old receipt would be an orphan.
        changed = copy.deepcopy(manifest)
        for arch, record in changed['runners']['claude'].items():
            record['source_revision'] = revision
            record['qualification']['source_revision'] = revision
        validate(changed, False)
        changed['component_selections']['claude'] = {}
        validate(changed, True)
        if args.generic_control:
            control = args.generic_control
            scope = json.loads((control / 'scope.json').read_text())
            assert scope['scope'] == 'actual Git inputs; SYNTHETIC native metadata; no image qualification'
            before, after = scope['runtime_source_C'], scope['manager_source_D']
            checkout = control / 'checkout'
            def git(*arguments):
                return subprocess.check_output(['git', '-C', str(checkout), *arguments], text=True).strip()
            assert git('rev-parse', before + '^{commit}') == before
            assert git('rev-parse', after + '^{commit}') == after
            assert git('diff', '--name-only', before, after).splitlines() == ['scripts/release/manager-version']
            generic = json.loads((control / 'release.json').read_text())
            assert generic['source_revision'] == after and before != after
            assert 'component_selections' not in generic
            for provider in ('codex', 'claude'):
                for arch in ('arm64', 'x86_64'):
                    record = generic['runners'][provider][arch]
                    assert record['source_revision'] == before
                    assert record['input_identity'] == scope['input_identities'][provider][arch]
            validate(generic, True)
            for provider in ('codex', 'claude'):
                for arch in ('arm64', 'x86_64'):
                    for field in ('source_revision', 'input_identity'):
                        invalid = copy.deepcopy(generic)
                        invalid['runners'][provider][arch][field] = '0' * (40 if field == 'source_revision' else 64)
                        validate(invalid, False)
            invalid = copy.deepcopy(generic)
            invalid['component_selections'] = {'codex': {'arm64': selections['codex']['arm64']}, 'claude': {}}
            validate(invalid, False)
    print(json.dumps({'passed': True, 'checks': checked, 'scope': 'offline native manifest receipt validation; no runtime qualification'}))


if __name__ == '__main__':
    main()
