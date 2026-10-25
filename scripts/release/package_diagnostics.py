"""Fixed package-stage evidence. Never retain argv, paths, output or exception text."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

VARIABLE = 'SYMPHONY_MACOS_PACKAGE_DIAGNOSTICS'
STAGES = frozenset(('assembly.source', 'assembly.helper', 'assembly.package',
    'package.helper-integrity', 'package.inputs', 'package.source', 'package.metadata',
    'package.copy', 'package.payload-signing', 'package.inventory', 'package.archive',
    'payload.inventory', 'payload.revision', 'payload.receipt', 'signature.sign',
    'signature.verify', 'signature.inspect', 'signature.policy', 'signature.entitlements',
    'signature.certificate'))


def record(stage, status, exit_status=None, outcome=None, source_line=None):
    if stage not in STAGES or status not in ('running', 'success', 'failed'):
        raise ValueError('invalid package diagnostic label')
    if exit_status is not None and type(exit_status) is not int:
        raise ValueError('invalid package diagnostic exit status')
    if source_line is not None and (type(source_line) is not int or source_line < 0):
        raise ValueError('invalid package diagnostic source line')
    if outcome not in (None, 'failed', 'timed out', 'could not start', 'operation refused', 'interrupted'):
        raise ValueError('invalid package diagnostic outcome')
    target = os.environ.get(VARIABLE)
    if not target:
        return
    try:
        path = Path(target)
        value = json.loads(path.read_text()) if path.exists() else {'schema': 1, 'operations': []}
        item = {'stage': stage, 'status': status}
        if exit_status is not None:
            item['exit_status'] = exit_status
        if outcome is not None:
            item['outcome'] = outcome
        if source_line is not None:
            item['source_line'] = source_line
        value['operations'].append(item)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('package diagnostic evidence unavailable') from None


@contextmanager
def stage(label):
    record(label, 'running')
    try:
        yield
    except BaseException as error:
        # Preserve the primary failure even if the evidence destination fails too.
        outcome = getattr(error, 'outcome', 'interrupted' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'operation refused')
        try:
            record(label, 'failed', getattr(error, 'returncode', None), outcome)
        except ValueError:
            pass
        raise
    record(label, 'success')


def require_complete(path):
    try:
        value = json.loads(Path(path).read_text())
        latest = {}
        for item in value['operations']:
            if item['stage'] not in STAGES or item['status'] not in ('running', 'success', 'failed'):
                raise ValueError()
            latest[item['stage']] = item['status']
        # All signed package stages are mandatory, including each native signing check.
        if value['schema'] != 1 or set(latest) != STAGES or any(s != 'success' for s in latest.values()):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('package diagnostic evidence incomplete') from None


if __name__ == '__main__':
    try:
        record(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) >= 4 else None,
               source_line=int(sys.argv[4]) if len(sys.argv) == 5 else None)
    except (ValueError, IndexError):
        sys.exit('package diagnostic evidence refused')
