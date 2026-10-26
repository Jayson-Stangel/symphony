#!/usr/bin/env python3
"""Run portable release policy once; exercise process fixtures on native hosts."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
# Only suites proved independent of native process/platform behavior go here.
# New suites conservatively run on each native qualification host.
PORTABLE = {
    'test_artifacts.py',
    'test_candidate_build.py',
    'test_candidate_cache.py',
    'test_component_discovery.py',
    'test_component_events.py',
    'test_component_join.py',
    'test_component_provenance.py',
    'test_component_publication.py',
    'test_component_restore.py',
    'test_component_selection.py',
    'test_distribution.py',
    'test_generic_exact_reuse.py',
    'test_graph_publication.py',
    'test_host_targets.py',
    'test_image_export.py',
    'test_manager_composition.py',
    'test_manager_envelope.py',
    'test_manager_protocol.py',
    'test_manager_selected_environment.py',
    'test_manual_integration_origin.py',
    'test_macos_distribution.py',
    'test_macos_signing_inputs.py',
    'test_native_recovery.py',
    'test_package_lifecycle.py',
    'test_previous_package_subject.py',
    'test_prior_source.py',
    'test_qualified_graph.py',
    'test_recovery_archive.py',
    'test_registry_closure.py',
    'test_registry_ownership.py',
    'test_retained_compatibility.py',
    'test_reuse_payload.py',
    'test_runner_cache.py',
    'test_runner_inputs.py',
    'test_runner_reuse.py',
}
CONTRACTS = {'test_codex_doctor_lock.py', 'test_project_credentials.py', 'test_readiness_stages.py'}


def run(kind, directory):
    def selected(path):
        if kind == 'contracts':
            return path.name in CONTRACTS
        if kind == 'process':
            return path.name not in PORTABLE | CONTRACTS
        return (path.name in PORTABLE) == (kind == 'portable')
    modules = sorted(p for p in directory.glob('test_*.py') if selected(p))
    if not modules:
        raise ValueError('no release qualification suites selected')
    suite = unittest.TestSuite()
    # unittest discovery normally provides its start directory. Preserve that
    # sibling-fixture import contract for the explicitly partitioned loader.
    sys.path.insert(0,str(directory))
    for module in modules:
        spec = importlib.util.spec_from_file_location(module.stem + '_' + str(id(module)), module)
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = loaded
        spec.loader.exec_module(loaded)
        suite.addTests(unittest.TestLoader().loadTestsFromModule(loaded))
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    record = {'kind': kind, 'modules': [p.name for p in modules],
              'tests': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skips': len(result.skipped),
              'elapsed_seconds': round(time.monotonic() - start, 6)}
    print(json.dumps(record), flush=True)
    # A missing native prerequisite must never become a successful skipped suite.
    return result.wasSuccessful() and result.testsRun > 0 and not result.skipped


def codex_config_contracts():
    """Cheap file/mount-policy fixtures; no native helper or provider startup."""
    for filename in ('codex_config_boundary_test.py', 'codex_config_snapshot_test.py'):
        if not (ROOT / 'elixir/test' / filename).is_file():
            raise ValueError('missing Codex config contract: ' + filename)
    if sys.version_info < (3, 11):
        raise ValueError('Codex config contracts require Python 3.11+ with tomllib')
    try:
        import tomllib
    except ImportError as error:
        raise ValueError('Codex config contracts require Python 3.11+ with tomllib') from error
    print(json.dumps({'kind': 'codex-config', 'python': sys.version.split()[0],
                      'executable': sys.executable}), flush=True)
    subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover',
                    '-s', str(ROOT / 'elixir/test'), '-p', 'codex_config*_test.py', '-v'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('portable', 'native', 'codex-config', 'contracts', 'process', 'release-policy'))
    args = parser.parse_args()
    if args.kind in ('portable', 'codex-config'):
        try:
            codex_config_contracts()
        except ValueError as error:
            parser.error(str(error))
    if args.kind == 'codex-config':
        raise SystemExit(0)
    if args.kind == 'contracts':
        for filename in CONTRACTS:
            if not (ROOT / 'scripts/release/tests' / filename).is_file():
                parser.error('missing launcher contract: ' + filename)
    if args.kind in ('portable', 'release-policy'):
        # Ordinary policy runs have separate selected fast-family executors.
        # Manual preflight keeps the complete historical portable coverage.
        if args.kind == 'portable':
            subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'scripts/ci/tests'), '-v'], check=True)
        for filename in ('scripts/symphony', 'scripts/symphony-package.sh',
                         'scripts/symphony-package-update.sh', 'scripts/symphony-apparmor.sh', 'scripts/symphony-runner-import.sh', 'scripts/symphony-runner-provision.sh', 'scripts/release/package.sh',
                         'scripts/release/build-host-helper.sh'):
            subprocess.run(['bash', '-n', str(ROOT / filename)], check=True)
        for filename in ['elixir/WORKFLOW.md', 'scripts/package/host.swift'] + [
                f'.codex/skills/{skill}/SKILL.md' for skill in ('commit', 'push', 'pull', 'linear', 'land')]:
            if not (ROOT / filename).is_file():
                parser.error('missing package input: ' + filename)
    kind = 'portable' if args.kind == 'release-policy' else args.kind
    raise SystemExit(0 if run(kind, ROOT / 'scripts/release/tests') else 1)


if __name__ == '__main__':
    main()
