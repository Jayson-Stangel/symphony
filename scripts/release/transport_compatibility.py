#!/usr/bin/env python3
"""Offline transport input binding, never a token/service capability verdict."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINTS = ('transport_compatibility', 'transport_fixture', 'staging_archive', 'staging_ingress', 'local_distribution',
               'graph_publication', 'component_publication', 'production_release')
WORKFLOWS = ('.github/workflows/production-publish.yml',
             '.github/workflows/macos-local-signing-import.yml')
FIXTURE_FILES = ('scripts/release/fixtures/transport-fixture-v1.json',)
CONTEXT_KEYS = {'actor', 'authentication', 'repository_visibility', 'configuration_digest'}


def require(value, message):
    if not value:
        raise ValueError('transport compatibility: ' + message)


def context(value):
    """Explicit non-secret operator metadata; equality is not authentication."""
    require(type(value) is dict and set(value) == CONTEXT_KEYS, 'unexpected context fields')
    require(type(value['actor']) is str and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,100}', value['actor']),
            'invalid actor')
    require(value['authentication'] in ('gh-cli', 'github-actions'), 'invalid authentication kind')
    require(value['repository_visibility'] in ('private', 'public'), 'invalid repository visibility')
    require(type(value['configuration_digest']) is str and
            re.fullmatch(r'sha256:[a-f0-9]{64}', value['configuration_digest']), 'invalid configuration digest')
    return value


def inputs(root):
    """Hash the Python import closure, consuming workflows and fixed fixture data.

    Both branches of conditional imports count. Dynamic import additions require
    updating these roots/reviewing this inventory before evidence reuse.
    """
    # macOS /var and /tmp are aliases for their /private counterparts. Accept
    # the chosen root's canonical location, then refuse symlinked descendants.
    root = Path(root).resolve()
    directory = root / 'scripts/release'
    pending = list(ENTRYPOINTS)
    files = set(WORKFLOWS + FIXTURE_FILES)
    while pending:
        name = pending.pop()
        path = directory / (name + '.py')
        relative = path.relative_to(root).as_posix()
        if relative in files:
            continue
        require(path.is_file() and path.resolve() == path.absolute(), 'missing/nonregular transport input')
        files.add(relative)
        for node in ast.walk(ast.parse(path.read_text())):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                     [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            pending.extend(name for name in names if (directory / (name + '.py')).is_file())
    result = {}
    for relative in sorted(files):
        path = root / relative
        require(path.is_file() and path.resolve() == path.absolute(), 'missing/nonregular transport input')
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot(root, service_context):
    return {'schema': 1, 'scope': 'transport-input-binding-only',
            'repository': 'Jayson-Stangel/symphony', 'context': context(service_context),
            'files': inputs(root)}


def compare(previous, current):
    require(type(previous) is dict and set(previous) == set(current) and
            previous['schema'] == 1 and type(previous['schema']) is int and
            previous['scope'] == current['scope'] and previous['repository'] == current['repository'],
            'invalid snapshot identity')
    context(previous['context'])
    require(type(previous['files']) is dict and previous['files'] and
            all(type(k) is str and type(v) is str and re.fullmatch(r'[a-f0-9]{64}', v)
                for k, v in previous['files'].items()), 'invalid input inventory')
    changed = sorted(name for name in previous['files'].keys() | current['files'].keys()
                     if previous['files'].get(name) != current['files'].get(name))
    context_changed = previous['context'] != current['context']
    return {'input_binding_matches': not changed and not context_changed,
            'changed_files': changed, 'context_changed': context_changed,
            'scope': 'offline-input-comparison; service capability and production acceptance unverified'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('snapshot', 'compare'))
    parser.add_argument('--context', type=Path, required=True,
                        help='non-secret actor/authentication/visibility/configuration digest JSON')
    parser.add_argument('--previous', type=Path, help='previous snapshot for comparison')
    args = parser.parse_args()
    try:
        current = snapshot(ROOT, json.loads(args.context.read_text()))
        if args.command == 'snapshot':
            require(args.previous is None, 'snapshot does not accept previous evidence')
            print(json.dumps(current, sort_keys=True, indent=2))
            return 0
        require(args.previous is not None, 'comparison requires previous snapshot')
        result = compare(json.loads(args.previous.read_text()), current)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result['input_binding_matches'] else 1
    except (ValueError, OSError, SyntaxError):
        # Do not print malformed input, filesystem contents or secret-bearing errors.
        parser.exit(2, 'Transport input check refused; inspect the non-secret inputs locally.\n')


if __name__ == '__main__':
    raise SystemExit(main())
