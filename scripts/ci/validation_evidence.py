"""Bounded read-only GitHub origin verification for one ExUnit proof family.

Receipts never select a workflow/controller or declare their own success. Those
facts come from the original GitHub run, exact attempt, executed job/steps and
immutable artifact metadata. Any ambiguity returns to fresh qualification.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
import zipfile

REPOSITORY = 'Jayson-Stangel/symphony'
WORKFLOW = '.github/workflows/make-all.yml'
JOB = 'Elixir quality, build and full coverage'
MAX_ARTIFACT = 65 * 1024 * 1024
MAX_PROOF = 1024 * 1024
MAX_AGE = timedelta(days=7)
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
STEPS = ('Set up mise tools', 'Prepare controlled full Elixir qualification',
         'Measure complete full-coverage inputs',
         'Choose fresh or verified full-coverage evidence',
         'Execute full ExUnit coverage (EXECUTED)',
         'Verify fresh Dialyzer', 'Prepare original executed coverage proof',
         'Retain original executed coverage proof')


class Fresh(ValueError):
    """No reuse authorization; the ordinary full suite must execute."""


def require(value, message):
    if not value:
        raise Fresh(message)


def positive(value):
    return type(value) is int and value > 0


def strict_json(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate evidence/API field')
            result[key] = value
        return result
    def finite(_):
        raise Fresh('nonfinite evidence value')
    def floating(value):
        result = float(value)
        require(math.isfinite(result), 'nonfinite evidence value')
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=finite, parse_float=floating)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, Fresh):
            raise
        raise Fresh('invalid evidence/API JSON') from None


def instant(value):
    require(type(value) is str, 'missing evidence timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise Fresh('invalid evidence timestamp') from None
    require(result.tzinfo is not None, 'evidence timestamp lacks timezone')
    return result


class Service:
    """Finite GET-only client; no retries or service mutation capability."""
    def __init__(self, token, home, *, seconds=45, maximum_requests=40):
        require(type(token) is str and bool(token), 'workflow evidence read credential is unavailable')
        self.environment = dict(PATH='/usr/bin:/bin', HOME=str(home), GH_CONFIG_DIR=str(Path(home) / 'gh'),
                                GH_TOKEN=token, GH_HOST='github.com', GH_PROMPT_DISABLED='1')
        self.deadline = time.monotonic() + seconds
        self.remaining = maximum_requests
        self.binary_flag = None

    def read(self, path, *, binary=False, maximum=2 * 1024 * 1024):
        require(type(path) is str and re.fullmatch(r'[A-Za-z0-9_./?=&%:-]+', path)
                and not path.startswith('/') and '..' not in path, 'unsafe evidence API path')
        self.remaining -= 1
        require(self.remaining >= 0 and time.monotonic() < self.deadline, 'evidence service budget exhausted')
        if binary and self.binary_flag is None:
            try:
                help_text = subprocess.check_output(['/usr/bin/gh', 'api', '--help'], env=self.environment,
                                                    stderr=subprocess.DEVNULL, timeout=2)
            except (OSError, subprocess.SubprocessError):
                raise Fresh('evidence download client unavailable') from None
            self.binary_flag = ['--allow-escape-sequences'] if b'--allow-escape-sequences' in help_text else []
        arguments = ['/usr/bin/gh', 'api', '--method', 'GET', *(self.binary_flag or [] if binary else []),
                     'repos/' + REPOSITORY + '/' + path]
        data = bytearray()
        try:
            with subprocess.Popen(arguments, env=self.environment, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL) as process:
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(process.stdout, selectors.EVENT_READ)
                        while True:
                            remaining = min(8, self.deadline - time.monotonic())
                            require(remaining > 0 and ready.select(remaining), 'evidence service timed out')
                            block = os.read(process.stdout.fileno(), 64 * 1024)
                            if not block:
                                break
                            data.extend(block)
                            require(len(data) <= maximum, 'evidence service payload exceeded bound')
                    require(process.wait(timeout=max(0.01, self.deadline - time.monotonic())) == 0,
                            'evidence service unavailable')
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
        except (OSError, subprocess.SubprocessError):
            raise Fresh('evidence service unavailable') from None
        require(time.monotonic() <= self.deadline, 'evidence service budget exhausted')
        return bytes(data) if binary else strict_json(data)


def repository(value):
    require(type(value) is dict and value.get('full_name') == REPOSITORY and positive(value.get('id')),
            'foreign repository identity')
    return value['id']


def original_run(run, *, now=None):
    now = now or datetime.now(timezone.utc)
    require(type(run) is dict and positive(run.get('id')) and positive(run.get('run_attempt')),
            'missing original run/attempt identity')
    require(repository(run.get('head_repository')) == repository(run.get('repository')),
            'foreign/fork original repository')
    require(run.get('event') in ('push', 'pull_request') and run.get('path') == WORKFLOW
            and SHA.fullmatch(run.get('head_sha', '')), 'foreign original workflow/source')
    require(now - MAX_AGE <= instant(run.get('created_at')) <= now + timedelta(minutes=1), 'stale/future original')
    require(run.get('status') == 'completed' and run.get('conclusion') == 'success', 'latest original did not succeed')
    if run['event'] == 'push':
        require(run.get('head_branch') == 'dev', 'non-dev original push')
    return run


def trusted_dev_run(run, *, terminal=True, now=None):
    now = now or datetime.now(timezone.utc)
    require(type(run) is dict and positive(run.get('id')) and positive(run.get('run_attempt')),
            'missing original run/attempt identity')
    repo_id = repository(run.get('repository'))
    require(repository(run.get('head_repository')) == repo_id, 'foreign producer head repository')
    require(run.get('event') == 'push' and run.get('head_branch') == 'dev'
            and SHA.fullmatch(run.get('head_sha', '')) and run.get('path') == WORKFLOW,
            'original is not the protected dev push workflow')
    when = instant(run.get('created_at'))
    require(now - MAX_AGE <= when <= now + timedelta(minutes=1), 'stale or future producer')
    if terminal:
        require(run.get('status') == 'completed' and run.get('conclusion') == 'success',
                'latest selected dev producer did not succeed')
    return run


def actual_job(jobs, run, *, terminal=True):
    require(type(jobs) is dict and type(jobs.get('jobs')) is list
            and type(jobs.get('total_count')) is int and jobs['total_count'] == len(jobs['jobs']), 'incomplete original job inventory')
    require(all(type(job) is dict for job in jobs['jobs']), 'malformed original job inventory')
    matching = [job for job in jobs['jobs'] if job.get('name') == JOB]
    require(len(matching) == 1, 'missing or ambiguous original full-coverage job')
    job = matching[0]
    require(positive(job.get('id')) and positive(job.get('run_id')) and job.get('run_id') == run['id']
            and positive(job.get('run_attempt')) and job.get('run_attempt') == run['run_attempt'] and job.get('head_sha') == run['head_sha'],
            'original job belongs to a different source/run/attempt')
    if terminal:
        require(job.get('status') == 'completed' and job.get('conclusion') == 'success',
                'original full-coverage job did not execute successfully')
    require(job.get('labels') == ['ubuntu-24.04'] and job.get('runner_group_id') == 0
            and type(job.get('runner_group_id')) is int
            and job.get('runner_group_name') == 'GitHub Actions' and positive(job.get('runner_id')),
            'original is not the actual hosted Ubuntu executor')
    return job


def successful_steps(job):
    steps = job.get('steps')
    require(type(steps) is list and all(type(step) is dict for step in steps), 'invalid original execution steps')
    observed = []
    for name in STEPS:
        matches = [step for step in steps if step.get('name') == name]
        require(len(matches) == 1, 'missing/ambiguous original executed stage')
        step = matches[0]
        require(step.get('status') == 'completed' and step.get('conclusion') == 'success'
                and positive(step.get('number')), 'original stage was skipped/failed/cancelled')
        require(instant(step.get('started_at')) <= instant(step.get('completed_at')),
                'invalid original executed stage timing')
        observed.append(step)
    require([step['number'] for step in observed] == sorted(set(step['number'] for step in observed)),
            'original executed stage ordering differs')
    reused = [step for step in steps if step.get('name') == 'Retain upstream full-coverage proof (REUSED)']
    require(len(reused) == 1 and reused[0].get('status') == 'completed'
            and reused[0].get('conclusion') == 'skipped',
            'reused/wrapper-only evidence cannot mint an executed baseline')
    return observed


def artifact_metadata(meta, run, upload, *, now=None):
    now = now or datetime.now(timezone.utc)
    require(type(meta) is dict and positive(meta.get('id'))
            and meta.get('name') == f"elixir-full-coverage-{run['id']}-{run['run_attempt']}",
            'foreign artifact identity/name')
    require(meta.get('expired') is False and type(meta.get('size_in_bytes')) is int
            and 0 < meta['size_in_bytes'] <= MAX_ARTIFACT, 'missing/oversized/expired original artifact')
    value = meta.get('digest', '')
    require(type(value) is str and value.startswith('sha256:') and DIGEST.fullmatch(value[7:]),
            'original immutable artifact digest unavailable')
    require(instant(meta.get('expires_at')) > now, 'original artifact expired')
    origin = meta.get('workflow_run')
    require(type(origin) is dict and origin.get('id') == run['id']
            and origin.get('head_sha') == run['head_sha'], 'foreign original artifact source/run')
    created = instant(meta.get('created_at'))
    require(instant(upload.get('started_at')) <= created <= instant(upload.get('completed_at')) + timedelta(seconds=1),
            'artifact was not created by the actual original upload step')
    return meta


def archive(raw, meta):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ARTIFACT,
            'original artifact unavailable or oversized')
    require('sha256:' + hashlib.sha256(raw).hexdigest() == meta['digest'], 'original artifact integrity differs')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
            entries = bundle.infolist()
            require(len(entries) == 4 and {entry.filename for entry in entries} == {'receipt.json', 'inputs.json', 'coverage.log', 'test-cache.tar.gz'},
                    'unexpected/duplicate original proof archive members')
            require(all(not entry.is_dir() and not entry.flag_bits & 1
                        and not stat.S_ISLNK(entry.external_attr >> 16)
                        and 0 < entry.file_size <= (64 * MAX_PROOF if entry.filename == 'test-cache.tar.gz' else MAX_PROOF) for entry in entries)
                    and sum(entry.file_size for entry in entries) <= 2 * MAX_ARTIFACT,
                    'unsafe/oversized original proof members')
            values = {entry.filename: bundle.read(entry) for entry in entries}
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        raise Fresh('corrupt original proof archive') from None
    return strict_json(values['receipt.json']), strict_json(values['inputs.json']), values['coverage.log'], values['test-cache.tar.gz']


def coverage_summary(raw, profile):
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise Fresh('invalid original coverage output') from None
    matches = re.findall(r'^(\d+) tests, (\d+) failures(?:, (\d+) skipped)?$', text, re.M)
    totals = re.findall(r'^\|\s*([0-9]+\.[0-9]+)%\s*\|\s*Total\s*\|$', text, re.M)
    require(len(matches) == len(totals) == 1, 'missing or ambiguous original full-suite summary')
    tests, failures, skipped = matches[0]
    result = dict(tests=int(tests), failures=int(failures), skipped=int(skipped or 0),
                  measured_coverage=float(totals[0]))
    require(result == dict(tests=profile['expected_tests'], failures=0,
                           skipped=profile['expected_linux_skips'], measured_coverage=profile['measured_coverage']),
            'original is not the complete audited suite/coverage profile')
    return result


def verify_bundle(run, job, meta, raw, current, profile, *, merged_source=None, restore=False):
    from validation_inputs import digest
    if merged_source is None:
        trusted_dev_run(run)
    else:
        original_run(run)
        require(run['event'] == 'pull_request', 'merged origin is not a PR workflow')
    actual_job(dict(jobs=[job], total_count=1), run)
    stages = successful_steps(job)
    artifact_metadata(meta, run, stages[-1])
    receipt, inputs, log, cache = archive(raw, meta)
    require(type(receipt) is dict and receipt.get('schema_version') == 2
            and type(receipt.get('schema_version')) is int
            and receipt.get('family') == 'full-exunit-coverage', 'unsupported original receipt format')
    producer = receipt.get('producer')
    require(producer == dict(repository=REPOSITORY, repository_id=repository(run['repository']),
                             run_id=run['id'], attempt=run['run_attempt'], job_id=job['id'],
                             source=merged_source or run['head_sha'], workflow=WORKFLOW), 'receipt origin differs from actual service provenance')
    require(type(inputs) is dict and type(inputs.get('identity')) is dict
            and inputs.get('sha256') == digest(inputs['identity']), 'original complete input manifest integrity differs')
    if restore:
        require(without_test_cache(inputs) == without_test_cache(current), 'non-cache actual validation inputs differ')
    else:
        require(inputs['sha256'] == current['sha256'] == digest(current['identity']),
                'actual effective validation inputs differ')
    sidecar = receipt.get('sidecar')
    require(type(sidecar) is dict and sidecar.get('sha256') == hashlib.sha256(cache).hexdigest()
            and sidecar.get('size') == len(cache), 'original sidecar integrity differs')
    require(receipt.get('clean_preparation') == 'no-mise-or-compiled-cache-v1', 'untrusted cache origin')
    require(receipt.get('inputs_sha256') == inputs['sha256'], 'receipt is not bound to original complete inputs')
    execution = receipt.get('execution')
    require(type(execution) is dict and execution.get('mode') == 'EXECUTED'
            and execution.get('exit_code') == 0 and type(execution.get('exit_code')) is int,
            'receipt is not an original successful executed suite')
    require(execution.get('coverage_log_sha256') == hashlib.sha256(log).hexdigest(), 'original test output integrity differs')
    require(execution.get('summary') == coverage_summary(log, profile), 'receipt summary differs from original test output')
    seconds = execution.get('seconds')
    require(type(seconds) in (int, float) and 0 < seconds <= 1200, 'invalid original execution duration')
    service_seconds = (instant(stages[STEPS.index('Execute full ExUnit coverage (EXECUTED)')]['completed_at']) - instant(stages[STEPS.index('Execute full ExUnit coverage (EXECUTED)')]['started_at'])).total_seconds()
    require(seconds <= service_seconds + 2, 'execution duration is outside the actual executed step')
    return dict(mode='REUSED', sidecar_bytes=cache, sidecar=sidecar, original_inputs=inputs, original=producer, artifact_id=meta['id'], artifact_digest=meta['digest'],
                inputs_sha256=inputs['sha256'], original_coverage_seconds=seconds,
                original_run_url=f"https://github.com/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}",
                original_job_url=f"https://github.com/{REPOSITORY}/actions/runs/{run['id']}/job/{job['id']}",
                original_summary=execution['summary'])


def tree_controls(tree):
    require(type(tree) is dict and tree.get('truncated') is False and type(tree.get('tree')) is list,
            'complete immutable controller inventory unavailable')
    from elixir_coverage_profile import control_path
    result = {}
    for row in tree['tree']:
        require(type(row) is dict and type(row.get('path')) is str, 'malformed immutable tree inventory')
        path = row['path']
        if not control_path(path) or row.get('type') == 'tree':
            continue
        require(row.get('type') == 'blob' and row.get('mode') in ('100644', '100755')
                and SHA.fullmatch(row.get('sha', '')) and path not in result,
                'unsupported or duplicate immutable controller input')
        result[path] = [row['mode'], row['sha']]
    require(WORKFLOW in result, 'actual qualification workflow is unavailable')
    return result


def immutable_tree(service, source):
    require(type(source) is str and SHA.fullmatch(source), 'invalid immutable source identity')
    commit = service.read('git/commits/' + source)
    require(type(commit) is dict and commit.get('sha') == source
            and type(commit.get('tree')) is dict and SHA.fullmatch(commit['tree'].get('sha', '')),
            'immutable source commit/tree binding differs')
    tree = service.read('git/trees/' + commit['tree']['sha'] + '?recursive=1')
    require(type(tree) is dict and tree.get('sha') == commit['tree']['sha'], 'immutable source tree differs')
    return commit, tree


def required_join(jobs, run):
    for name in ('Classify changes / Classify event inputs', 'Application checks'):
        found = [job for job in jobs['jobs'] if type(job) is dict and job.get('name') == name]
        require(len(found) == 1 and found[0].get('status') == 'completed'
                and found[0].get('conclusion') == 'success'
                and found[0].get('run_id') == run['id']
                and found[0].get('run_attempt') == run['run_attempt']
                and found[0].get('head_sha') == run['head_sha'],
                'original classification/required join did not succeed in this exact attempt')


def latest_proof(service, current, profile, trusted_controls, *, consumer_repository_id, trusted_dev_revision):
    """At most three real protected-dev runs; never bypass a selected failure.

    A successful whole workflow with a skipped full-family job can be crossed
    only when its exact complete validation controllers equal this audited one.
    Everything else, including absent jobs/proofs or active reruns, is fresh.
    """
    require(type(trusted_dev_revision) is str and SHA.fullmatch(trusted_dev_revision),
            'immutable protected dev controller source is unavailable')
    listing = service.read('actions/workflows/make-all.yml/runs?branch=dev&event=push&per_page=3')
    require(type(listing) is dict and type(listing.get('workflow_runs')) is list
            and 0 < len(listing['workflow_runs']) <= 3, 'no bounded trusted dev producer history')
    candidates = listing['workflow_runs']
    require(all(type(candidate) is dict and positive(candidate.get('id')) for candidate in candidates),
            'malformed producer history')
    dates = [instant(candidate.get('created_at')) for candidate in candidates]
    require(dates == sorted(dates, reverse=True), 'producer history is not newest-first')
    for candidate in candidates:
        require(type(candidate) is dict and positive(candidate.get('id')), 'malformed producer history')
        run = trusted_dev_run(service.read('actions/runs/' + str(candidate['id'])))
        require(run['id'] == candidate['id'], 'producer API run identity differs from requested run')
        require(repository(run['repository']) == consumer_repository_id, 'producer repository ID differs')
        lineage = service.read('compare/' + run['head_sha'] + '...' + trusted_dev_revision)
        require(type(lineage) is dict and lineage.get('status') in ('ahead', 'identical')
                and type(lineage.get('merge_base_commit')) is dict
                and lineage['merge_base_commit'].get('sha') == run['head_sha'],
                'original source is outside the immutable protected dev history')
        _, tree = immutable_tree(service, run['head_sha'])
        require(tree_controls(tree) == trusted_controls, 'original validation controllers differ')
        jobs = service.read(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
        require(type(jobs) is dict and type(jobs.get('jobs')) is list
                and type(jobs.get('total_count')) is int and jobs['total_count'] == len(jobs['jobs']), 'incomplete producer attempt inventory')
        required_join(jobs, run)
        matching = [job for job in jobs['jobs'] if type(job) is dict and job.get('name') == JOB]
        require(len(matching) == 1, 'missing or ambiguous original selected-family job')
        if matching[0].get('conclusion') == 'skipped':
            # Whole workflow SUCCESS + audited exact workflow's push predicate
            # and strict required join prove intentional family omission.
            require(matching[0].get('status') == 'completed'
                    and matching[0].get('run_id') == run['id']
                    and matching[0].get('run_attempt') == run['run_attempt']
                    and matching[0].get('head_sha') == run['head_sha'],
                    'skipped family is not bound to this successful exact attempt')
            continue
        primary = actual_job(jobs, run)
        reused = [step for step in primary.get('steps', []) if step.get('name') == 'Retain upstream full-coverage proof (REUSED)']
        if len(reused) == 1 and reused[0].get('conclusion') == 'success':
            executed = [step for step in primary.get('steps', []) if step.get('name') == 'Execute full ExUnit coverage (EXECUTED)']
            require(len(executed) == 1 and executed[0].get('status') == 'completed'
                    and executed[0].get('conclusion') == 'skipped', 'dev reuse did not omit only the verified full suite')
            result = merged_pr_proof(service, run['head_sha'], current, profile, trusted_controls, consumer_repository_id)
            latest = trusted_dev_run(service.read('actions/runs/' + str(run['id'])))
            require(latest['id'] == run['id'] and latest['run_attempt'] == run['run_attempt']
                    and latest['head_sha'] == run['head_sha'], 'selected dev latest attempt changed during original verification')
            return result
        job = primary
        steps = successful_steps(job)
        artifacts = service.read(f"actions/runs/{run['id']}/artifacts?per_page=100")
        require(type(artifacts) is dict and type(artifacts.get('artifacts')) is list
                and type(artifacts.get('total_count')) is int
                and artifacts['total_count'] == len(artifacts['artifacts']), 'incomplete original artifact inventory')
        expected = f"elixir-full-coverage-{run['id']}-{run['run_attempt']}"
        matches = [meta for meta in artifacts['artifacts'] if type(meta) is dict and meta.get('name') == expected]
        require(len(matches) == 1, 'missing or ambiguous latest original full-suite artifact')
        meta = artifact_metadata(service.read('actions/artifacts/' + str(matches[0]['id'])), run, steps[-1])
        require(meta['id'] == matches[0]['id'], 'artifact API identity differs from requested artifact')
        raw = service.read('actions/artifacts/' + str(meta['id']) + '/zip', binary=True, maximum=MAX_ARTIFACT)
        result = verify_bundle(run, job, meta, raw, current, profile, restore=True)
        # A stale list/attempt cannot authorize reuse during a new rerun.
        latest = trusted_dev_run(service.read('actions/runs/' + str(run['id'])))
        require(latest['run_attempt'] == run['run_attempt'] and latest['head_sha'] == run['head_sha'],
                'original latest attempt changed during verification')
        final_meta = artifact_metadata(service.read('actions/artifacts/' + str(meta['id'])), latest, steps[-1])
        require(final_meta['digest'] == meta['digest'] and final_meta['id'] == meta['id'],
                'original immutable artifact changed during verification')
        return result
    raise Fresh('no complete original proof within bounded trusted history')


def current_context(service, *, run_id, attempt, source, event, ref, pr_number=None):
    """Authenticate platform caller metadata; no receipt may select this route."""
    require(positive(run_id) and positive(attempt) and type(source) is str and SHA.fullmatch(source),
            'invalid actual caller identity')
    run = service.read('actions/runs/' + str(run_id))
    require(type(run) is dict and run.get('id') == run_id and run.get('run_attempt') == attempt
            and run.get('event') == event and run.get('path') == WORKFLOW,
            'actual caller run/attempt/workflow differs')
    repo_id = repository(run.get('repository'))
    jobs = service.read(f'actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100')
    job = actual_job(jobs, run, terminal=False)
    dev = service.read('branches/dev')
    require(type(dev) is dict and dev.get('name') == 'dev' and dev.get('protected') is True
            and type(dev.get('commit')) is dict
            and SHA.fullmatch(dev['commit'].get('sha', '')), 'protected dev snapshot unavailable')
    trusted_dev = dev['commit']['sha']
    value = dict(run_id=run_id, attempt=attempt, source=source, repository_id=repo_id,
                 job_id=job['id'], trusted_dev_revision=trusted_dev,
                 eligible_consumer=False, eligible_producer=False)
    if event == 'push':
        require(ref in ('refs/heads/dev', 'refs/heads/main')
                and run.get('head_branch') == ref.removeprefix('refs/heads/')
                and run.get('head_sha') == source, 'actual push source/ref differs')
        if ref == 'refs/heads/dev':
            trusted_dev_run(run, terminal=False)
            lineage = service.read('compare/' + source + '...' + trusted_dev)
            require(type(lineage) is dict and lineage.get('status') in ('ahead', 'identical')
                    and type(lineage.get('merge_base_commit')) is dict
                    and lineage['merge_base_commit'].get('sha') == source,
                    'producer source is outside protected dev history')
            value.update(eligible_producer=True, eligible_consumer=True)
    elif event == 'pull_request':
        require(positive(pr_number) and ref == f'refs/pull/{pr_number}/merge', 'actual PR merge reference differs')
        pr = service.read('pulls/' + str(pr_number))
        require(type(pr) is dict and pr.get('number') == pr_number
                and type(pr.get('head')) is dict and type(pr.get('base')) is dict
                and run.get('head_sha') == pr['head'].get('sha'), 'actual PR head differs')
        head, base = pr['head'], pr['base']
        for part in (head, base):
            require(type(part.get('sha')) is str and SHA.fullmatch(part['sha'])
                    and type(part.get('repo')) is dict, 'actual PR source identity unavailable')
        require(type(run.get('head_repository')) is dict
                and run['head_repository'].get('id') == head['repo'].get('id')
                and run['head_repository'].get('full_name') == head['repo'].get('full_name'),
                'caller run and PR head repositories differ')
        if (base.get('ref') == 'dev' and head['repo'].get('id') == base['repo'].get('id') == repo_id
                and head['repo'].get('full_name') == REPOSITORY and pr.get('state') == 'open'
                and pr.get('draft') is False and pr.get('merge_commit_sha') == source):
            value['eligible_producer'] = True
        # Ordinary feature/fork PRs qualify fresh. Only the protected same-repo
        # dev→main caller can enter immutable-controller loading/reuse.
        if (head.get('ref') == 'dev' and base.get('ref') == 'main'
                and head['repo'].get('full_name') == REPOSITORY
                and head['repo'].get('id') == repo_id
                and base['repo'].get('id') == repo_id
                and head['sha'] == trusted_dev and pr.get('state') == 'open'
                and pr.get('draft') is False):
            require(pr.get('merge_commit_sha') == source, 'actual tested merge source is stale or different')
            commit = service.read('git/commits/' + source)
            require(type(commit) is dict and commit.get('sha') == source
                    and type(commit.get('parents')) is list
                    and all(type(parent) is dict and SHA.fullmatch(parent.get('sha', ''))
                            for parent in commit['parents'])
                    and [parent.get('sha') for parent in commit['parents'] if type(parent) is dict]
                        == [base['sha'], head['sha']], 'tested merge parents/base/head differ')
            value.update(eligible_consumer=True, pr_number=pr_number, head=head['sha'], base=base['sha'])
    return value


def without_test_cache(inputs):
    """Compare every actual input before accepting any executable archive bytes."""
    from copy import deepcopy
    from validation_inputs import digest
    require(type(inputs) is dict and inputs.get('sha256') == digest(inputs.get('identity')),
            'complete input identity is invalid')
    value = deepcopy(inputs['identity'])
    rows = value.get('public_inputs')
    require(type(rows) is list and all(type(row) is dict for row in rows), 'public runtime inventory unavailable')
    matches = [row for row in rows if row.get('path') == '/home/runner/work/symphony/symphony/elixir/_build/test']
    require(len(matches) == 1, 'exact test cache inventory unavailable')
    rows.remove(matches[0])
    return value


def merged_pr_proof(service, merge_source, current, profile, trusted_controls, repository_id):
    """Only a final same-repository PR actually merged at this protected commit.

    The original PR workflow is untrusted before merge. Immutable tested merge
    tree/controller equality to the actual protected merge authenticates it now;
    service run/job/attempt/steps, clean setup and artifact provenance still apply.
    No artifact is downloaded or materialized before these service checks.
    """
    require(SHA.fullmatch(merge_source), 'invalid protected merge source')
    merged, merged_tree = immutable_tree(service, merge_source)
    require(tree_controls(merged_tree) == trusted_controls, 'merged controllers differ')
    parents = merged.get('parents')
    require(type(parents) is list and len(parents) == 2
            and all(type(row) is dict and SHA.fullmatch(row.get('sha', '')) for row in parents),
            'only actual two-parent reviewed merges can reuse PR evidence')
    prs = service.read('commits/' + merge_source + '/pulls?per_page=10')
    require(type(prs) is list and len(prs) <= 10 and all(type(row) is dict for row in prs),
            'merged PR association unavailable')
    matches = [row for row in prs if row.get('merge_commit_sha') == merge_source]
    require(len(matches) == 1 and positive(matches[0].get('number')), 'ambiguous actual merged PR')
    pr = service.read('pulls/' + str(matches[0]['number']))
    require(type(pr) is dict and pr.get('number') == matches[0]['number'] and pr.get('merged') is True
            and pr.get('state') == 'closed' and pr.get('merge_commit_sha') == merge_source,
            'original PR is not actually merged into protected dev')
    head, base = pr.get('head'), pr.get('base')
    require(type(head) is dict and type(base) is dict and base.get('ref') == 'dev'
            and repository(head.get('repo')) == repository(base.get('repo')) == repository_id
            and head.get('sha') == parents[1]['sha'], 'foreign/fork or non-final merged PR head')
    listing = service.read('actions/workflows/make-all.yml/runs?event=pull_request&head_sha=' + head['sha'] + '&per_page=3')
    require(type(listing) is dict and type(listing.get('workflow_runs')) is list
            and 0 < len(listing['workflow_runs']) <= 3, 'bounded original PR run unavailable')
    candidates = listing['workflow_runs']
    require(all(type(row) is dict and positive(row.get('id')) for row in candidates), 'malformed original PR history')
    require([instant(row.get('created_at')) for row in candidates] == sorted(
            [instant(row.get('created_at')) for row in candidates], reverse=True), 'PR history ordering differs')
    # Never walk past a newer failed/in-progress/missing original qualification.
    run = service.read('actions/runs/' + str(candidates[0]['id']))
    original_run(run)
    require(run['id'] == candidates[0]['id'], 'original PR run response identity differs')
    require(run.get('event') == 'pull_request' and run.get('head_sha') == head['sha']
            and repository(run.get('repository')) == repository_id, 'original run is not the actual merged PR')
    jobs = service.read(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    required_join(jobs, run)
    job = actual_job(jobs, run)
    stages = successful_steps(job)
    artifacts = service.read(f"actions/runs/{run['id']}/artifacts?per_page=100")
    require(type(artifacts) is dict and type(artifacts.get('artifacts')) is list
            and artifacts.get('total_count') == len(artifacts['artifacts']), 'incomplete original PR artifact inventory')
    matches = [row for row in artifacts['artifacts'] if type(row) is dict
               and row.get('name') == f"elixir-full-coverage-{run['id']}-{run['run_attempt']}"]
    require(len(matches) == 1 and positive(matches[0].get('id')), 'missing original PR artifact')
    meta = artifact_metadata(service.read('actions/artifacts/' + str(matches[0]['id'])), run, stages[-1])
    require(meta['id'] == matches[0]['id'], 'original artifact response identity differs')
    raw = service.read('actions/artifacts/' + str(meta['id']) + '/zip', binary=True, maximum=MAX_ARTIFACT)
    receipt, _, _, _ = archive(raw, meta)
    source = receipt.get('producer', {}).get('source')
    require(type(source) is str and SHA.fullmatch(source), 'original tested merge source unavailable')
    tested, tested_tree = immutable_tree(service, source)
    require(tested.get('tree') == merged.get('tree') and tested.get('parents') == parents
            and tree_controls(tested_tree) == trusted_controls, 'original tested merge/tree/base/head differs from protected merge')
    result = verify_bundle(run, job, meta, raw, current, profile, merged_source=source, restore=True)
    final = original_run(service.read('actions/runs/' + str(run['id'])))
    require(final['run_attempt'] == run['run_attempt'] and final['head_sha'] == run['head_sha'],
            'original PR latest attempt changed during verification')
    final_meta = artifact_metadata(service.read('actions/artifacts/' + str(meta['id'])), final, stages[-1])
    require(final_meta['id'] == meta['id'] and final_meta['digest'] == meta['digest'], 'original immutable artifact changed')
    return result
