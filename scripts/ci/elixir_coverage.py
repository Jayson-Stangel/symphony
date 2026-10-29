#!/usr/bin/env python3
"""Existing Elixir job phases with one conservative full-coverage reuse seam."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil

import validation_cache as cache
import stat
import subprocess
import sys
import time

import elixir_coverage_profile as profile
from validation_inputs import UnknownInput, command, controlled_environment, digest
from validation_evidence import (Fresh, Service, REPOSITORY, WORKFLOW, SHA, MAX_ARTIFACT,
                                 current_context, coverage_summary, immutable_tree,
                                 latest_proof, merged_pr_proof, repository, strict_json, tree_controls)


def require(value, message):
    if not value:
        raise ValueError(message)


def locations():
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_OS') == 'Linux'
            and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY,
            'coverage controller is limited to the actual repository Linux CI job')
    root = Path(os.environ['GITHUB_WORKSPACE']).resolve()
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    require(str(root) == '/home/runner/work/symphony/symphony'
            and temporary == Path('/home/runner/work/_temp'), 'unknown hosted job/source paths')
    source = os.environ['GITHUB_SHA']
    require(SHA.fullmatch(source) and command(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip() == source,
            'checkout is not the actual platform validation source')
    return root, temporary, source


def store(path, value, *, new=False):
    if path.exists():
        require(not path.is_symlink() and path.stat().st_uid == os.getuid()
                and not path.stat().st_mode & 0o077, 'private validation record is unsafe')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_EXCL if new else os.O_TRUNC), 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, sort_keys=True, indent=2)
        output.write('\n')


def read(path):
    require(not path.is_symlink() and path.is_file() and path.stat().st_uid == os.getuid()
            and not path.stat().st_mode & 0o077 and path.stat().st_size <= 2 * 1024 * 1024,
            'private validation record is unavailable or unsafe')
    return strict_json(path.read_bytes())


def output(**values):
    with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
        for key, value in values.items():
            text = str(value).lower() if type(value) is bool else str(value)
            require('\n' not in text and '\r' not in text, 'unsafe workflow output')
            target.write(key + '=' + text + '\n')


def summary(text):
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as target:
        target.write(text + '\n')


def paths():
    root, temporary, source = locations()
    return root, temporary, source, temporary / 'symphony-coverage-v1', temporary / 'symphony-coverage-selection.json'


def select_profile():
    root, temporary, source, state, selected = paths()
    supported, reason = False, 'changed or unknown validation source; existing ordinary fresh path'
    try:
        _, rows = profile.source_identity(root, source)
        description = strict_json(profile.PROFILE_PATH.read_bytes())
        supported = profile.audited_code(rows) == description['audited_source_sha256']
        if supported:
            reason = 'reviewed full-coverage source profile; fresh unless complete upstream evidence verifies'
    except (UnknownInput, Fresh, OSError, ValueError, KeyError):
        pass
    store(selected, dict(source=source, supported=supported, reason=reason), new=True)
    output(supported=supported, cache_family='exunit-private-v1' if supported else 'mix')
    summary('Coverage preparation: ' + reason + '.')


def client(temporary):
    home = temporary / 'symphony-evidence-api-v1'
    home.mkdir(mode=0o700, exist_ok=True)
    require(not home.is_symlink() and home.stat().st_uid == os.getuid() and not home.stat().st_mode & 0o077,
            'private evidence client directory is unsafe')
    return Service(os.environ.get('GH_TOKEN', ''), home)


def context(service, source):
    number = os.environ.get('COVERAGE_PR_NUMBER', '')
    return current_context(service, run_id=int(os.environ['GITHUB_RUN_ID']),
                           attempt=int(os.environ['GITHUB_RUN_ATTEMPT']), source=source,
                           event=os.environ['GITHUB_EVENT_NAME'], ref=os.environ['GITHUB_REF'],
                           pr_number=int(number) if number else None)


def capability():
    _, temporary, source, _, _ = paths()
    try:
        service = client(temporary)
        value = context(service, source)
        artifacts = service.read('actions/artifacts?per_page=1')
        require(type(artifacts) is dict and type(artifacts.get('artifacts')) is list,
                'artifact read returned malformed metadata')
        store(temporary / 'symphony-evidence-capability.json', dict(available=True, source=source, context=value))
        output(available=True)
        summary('Evidence capability: actual workflow-token run/job/source and artifact metadata GETs succeeded; this is not reuse authorization.')
    except (Fresh, ValueError, KeyError, TypeError, OSError):
        store(temporary / 'symphony-evidence-capability.json', dict(available=False, source=source))
        output(available=False)
        summary('Evidence service unavailable or caller ineligible; full fresh qualification remains required.')


def run(arguments, root, environment, *, log=None, seconds=1200):
    started = time.monotonic()
    if log is None:
        result = subprocess.run(arguments, cwd=root / 'elixir', env=environment, timeout=seconds)
        require(result.returncode == 0, 'fresh qualification command failed')
    else:
        with log.open('xb') as target, subprocess.Popen(arguments, cwd=root / 'elixir', env=environment,
                                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            received = 0
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = seconds - (time.monotonic() - started)
                        require(remaining > 0 and ready.select(remaining), 'fresh coverage command timed out')
                        block = os.read(process.stdout.fileno(), 64 * 1024)
                        if not block:
                            break
                        received += len(block)
                        require(received <= 8 * 1024 * 1024, 'fresh coverage output exceeded bound')
                        target.write(block)
                        sys.stdout.buffer.write(block); sys.stdout.buffer.flush()
                require(process.wait(timeout=max(0.1, seconds - (time.monotonic() - started))) == 0,
                        'fresh coverage command failed')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
    return round(time.monotonic() - started, 3)


def prepare():
    root, temporary, source, state, selected = paths()
    selection = read(selected)
    require(selection['source'] == source, 'source selection changed')
    if selection['supported'] is not True:
        state.mkdir(mode=0o700)
        store(state / 'prepared.json', dict(source=source, supported=False, ordinary=True))
        return
    environment = controlled_environment(root, state, os.environ)
    path, tools = profile.executable_path(os.environ)
    environment.update(PATH=path, MIX_ARCHIVES=os.environ.get('MIX_ARCHIVES', str(Path(environment['MIX_HOME']) / 'archives')))
    profile.public_environment(environment, os.environ)
    # The same private paths precede all compilation. Existing quality/build
    # commands retain their ordinary dev Mix environment; test compile is test.
    development = dict(environment, MIX_ENV='dev'); development.pop('HEX_OFFLINE')
    preparation_seconds = run(['make', 'setup', 'build', 'fmt-check', 'lint', 'claude-setup'], root, development)
    test_compile_seconds = run(['mix', 'compile'], root, environment)
    store(state / 'environment.json', environment, new=True)
    store(state / 'prepared.json', dict(source=source, supported=True, ordinary=False), new=True)
    summary(f'EXECUTED: fresh dependency resolution/build/formatting/lint/npm {preparation_seconds}s; test compilation {test_compile_seconds}s. No compiled or toolchain cache restored. Hosted default schedulers are retained.')


def measure():
    root, temporary, source, state, _ = paths()
    prepared = read(state / 'prepared.json')
    require(prepared['source'] == source, 'prepared source changed')
    if prepared['supported'] is not True:
        output(available=False)
        return
    started = time.monotonic()
    try:
        measured = profile.fingerprint(root, source, read(state / 'environment.json'), os.environ)
        sidecar_started = time.monotonic()
        raw, metadata = cache.create(root / 'elixir/_build/test', root)
        cache_row = next(row for row in measured['identity']['public_inputs'] if row['path'] == str(root / 'elixir/_build/test'))
        require(metadata['cache'] == cache_row, 'test cache changed after complete measurement')
        (state / 'test-cache.tar.gz').write_bytes(raw)
        os.chmod(state / 'test-cache.tar.gz', 0o600)
        store(state / 'sidecar.json', metadata, new=True)
        sidecar_seconds = round(time.monotonic() - sidecar_started, 3)
        store(state / 'inputs.json', measured, new=True)
        output(available=True, input_sha256=measured['sha256'])
        summary(f"Complete public input profile: {measured['files']} entries, {measured['bytes']} bytes, {measured['elapsed_seconds']} seconds. Sidecar {len(raw)} compressed bytes in {sidecar_seconds}s; total measurement/archive {time.monotonic() - started:.3f}s. No reuse has been authorized.")
    except (UnknownInput, Fresh) as error:
        output(available=False)
        summary(f'Full input equivalence unavailable after {time.monotonic() - started:.3f}s: {error}. Full suite executes fresh; no reusable producer receipt.')
    except (OSError, ValueError, KeyError):
        output(available=False)
        summary(f'Full input equivalence unavailable after {time.monotonic() - started:.3f}s; full suite executes fresh and no reusable producer receipt is available.')


def decide():
    root, temporary, source, state, _ = paths()
    started = time.monotonic()
    decision = dict(mode='EXECUTED', reason='full fresh qualification is required')
    # Only the immutable protected-dev loader may create this envelope. Ordinary
    # feature/fork paths are additionally forced fresh by the workflow itself.
    trusted = temporary / 'symphony-trusted-coverage-v1' / 'trusted-source.json'
    try:
        if trusted.is_file() and (state / 'inputs.json').is_file():
            envelope = read(trusted)
            service = client(temporary)
            caller = context(service, source)
            if caller['eligible_consumer']:
                require(caller['trusted_dev_revision'] == envelope['trusted_dev_revision'],
                        'protected dev source changed during preparation')
                _, current_tree = immutable_tree(service, source)
                require(tree_controls(current_tree) == envelope['controls'], 'tested workflow/controllers differ from protected dev')
                inputs = read(state / 'inputs.json')
                description = strict_json(profile.PROFILE_PATH.read_bytes())
                if os.environ['GITHUB_EVENT_NAME'] == 'push':
                    decision = merged_pr_proof(service, source, inputs, description,
                                               envelope['controls'], caller['repository_id'])
                else:
                    decision = latest_proof(service, inputs, description, envelope['controls'],
                        consumer_repository_id=caller['repository_id'], trusted_dev_revision=caller['trusted_dev_revision'])
                # API provenance and every non-cache actual input matched before
                # even parsing/materializing these authenticated executable bytes.
                install_sidecar(root, state, source, decision)

    except (Fresh, UnknownInput, OSError, ValueError, KeyError, TypeError, AttributeError):
        decision = dict(mode='EXECUTED', reason='trusted complete upstream evidence unavailable or incompatible')
    decision['verification_seconds'] = round(time.monotonic() - started, 3)
    store(state / 'decision.json', decision, new=True)
    output(mode=decision['mode'], origin=decision.get('original_run_url', ''))
    summary('Coverage decision: ' + decision['mode'] + f" (verification {decision['verification_seconds']}s)" + ('. Original: ' + decision['original_run_url'] if decision['mode'] == 'REUSED' else '. Full suite will execute.'))


def install_sidecar(root, state, source, decision):
    target = root / 'elixir/_build/test'
    staging, backup = target.with_name('.test-evidence-v1'), target.with_name('.test-fresh-v1')
    require(target.is_dir() and not target.is_symlink() and not staging.exists()
            and not backup.exists() and not backup.is_symlink(), 'test-cache swap is not exclusively owned')
    installed = False
    try:
        cache.stage(decision['sidecar_bytes'], decision['sidecar'], staging, root)
        target.rename(backup)
        try:
            staging.rename(target)
        except OSError:
            backup.rename(target)
            raise
        installed = True
        actual = profile.fingerprint(root, source, read(state / 'environment.json'), os.environ)
        require(actual['sha256'] == decision['inputs_sha256'], 'restored full actual input identity differs')
    except (Fresh, UnknownInput, OSError, ValueError, KeyError, TypeError):
        if installed:
            target.rename(staging)
            backup.rename(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    # Keep the freshly compiled local cache as an inert fallback until job end.
    decision.pop('sidecar_bytes')
    decision.pop('original_inputs')
    decision.pop('sidecar')


def execute():
    root, _, source, state, _ = paths()
    prepared = read(state / 'prepared.json')
    require(prepared['source'] == source, 'prepared source changed')
    if prepared['ordinary']:
        environment = dict(os.environ); environment.pop('GH_TOKEN', None); environment.pop('GITHUB_TOKEN', None)
        seconds = run(['make', 'elixir-ci'], root, environment, log=state / 'coverage.log')
    else:
        seconds = run(['mix', 'test', '--cover', '--seed', '0', '--max-failures', '1'], root,
                      read(state / 'environment.json'), log=state / 'coverage.log')
    store(state / 'execution.json', dict(mode='EXECUTED', exit_code=0, seconds=seconds,
                                       coverage_log_sha256=hashlib.sha256((state / 'coverage.log').read_bytes()).hexdigest()), new=True)
    output(mode='EXECUTED', origin='')
    summary(f'EXECUTED: fresh coverage path succeeded in {seconds} seconds. The required wrapper still awaits remaining stages.')


def reused():
    _, _, _, state, _ = paths()
    decision = read(state / 'decision.json')
    require(decision.get('mode') == 'REUSED', 'missing verified reuse decision')
    output(mode='REUSED', origin=decision['original_run_url'])
    summary(f"REUSED: original full suite/coverage evidence retained from {decision['original_run_url']} (job {decision['original']['job_id']}, artifact {decision['artifact_id']}, {decision['artifact_digest']}). Original coverage {decision['original_coverage_seconds']} seconds; no current suite execution or saved allocation is claimed. Fresh build/lint/compilation and Dialyzer remain required.")


def dialyzer():
    root, _, source, state, _ = paths()
    prepared = read(state / 'prepared.json')
    require(prepared['source'] == source, 'prepared source changed')
    if not prepared['ordinary']:
        environment = dict(read(state / 'environment.json'), MIX_ENV='dev'); environment.pop('HEX_OFFLINE')
        seconds = run(['make', 'dialyzer'], root, environment)
        summary(f'EXECUTED: fresh Dialyzer including any cold PLT/dependency work {seconds}s.')
    store(state / 'dialyzer.json', dict(source=source, success=True), new=True)


def seal():
    root, temporary, source, state, _ = paths()
    available = False
    try:
        prepared = read(state / 'prepared.json')
        require(prepared['source'] == source and prepared['supported'] is True, 'unsupported producer source profile')
        require(read(state / 'dialyzer.json') == dict(source=source, success=True), 'fresh Dialyzer proof missing')
        execution = read(state / 'execution.json')
        require(execution['mode'] == 'EXECUTED' and execution['exit_code'] == 0, 'original full suite did not execute')
        inputs = read(state / 'inputs.json')
        require(inputs['sha256'] == digest(inputs['identity']), 'complete producer input identity differs')
        execution['summary'] = coverage_summary((state / 'coverage.log').read_bytes(), strict_json(profile.PROFILE_PATH.read_bytes()))
        caller = context(client(temporary), source)
        require(caller['eligible_producer'] is True, 'only actual same-repository supported producer can retain original evidence')
        sidecar = read(state / 'sidecar.json')
        require(cache.identity(root / 'elixir/_build/test', root) == sidecar['cache'], 'test cache changed during qualification')
        proof = state / 'proof'; proof.mkdir(mode=0o700)
        store(proof / 'receipt.json', dict(schema_version=2, family='full-exunit-coverage', inputs_sha256=inputs['sha256'],
              sidecar=sidecar, clean_preparation='no-mise-or-compiled-cache-v1',
              producer=dict(repository=REPOSITORY, repository_id=caller['repository_id'], run_id=caller['run_id'],
                            attempt=caller['attempt'], job_id=caller['job_id'], source=source, workflow=WORKFLOW),
              execution=execution), new=True)
        store(proof / 'inputs.json', inputs, new=True)
        (proof / 'coverage.log').write_bytes((state / 'coverage.log').read_bytes())
        (proof / 'test-cache.tar.gz').write_bytes((state / 'test-cache.tar.gz').read_bytes())
        available = True
    except (Fresh, UnknownInput, OSError, ValueError, KeyError, TypeError, AttributeError):
        summary('No transferable producer receipt: this caller/profile lacks complete original executed evidence. Fresh qualification status is unchanged.')
    output(available=available)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('select', 'capability', 'prepare', 'measure', 'decide', 'execute', 'reused', 'dialyzer', 'seal'))
    phase = parser.parse_args().phase
    globals()[dict(select='select_profile').get(phase, phase)]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('Full Elixir qualification controller refused an incomplete/failed phase.', file=sys.stderr)
        raise SystemExit(1)
