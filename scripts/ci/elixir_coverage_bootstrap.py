#!/usr/bin/env python3
"""Stdlib-only loader, fetched from immutable protected dev by the workflow.

No candidate module is imported to select the verifier. This is one fixed family
loader, not a workflow dispatcher; unavailable/foreign state means fresh tests.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

REPOSITORY = 'Jayson-Stangel/symphony'
FILES = ('validation_inputs.py', 'elixir_coverage_profile.py', 'elixir-coverage-profile.json',
         'validation_evidence.py', 'elixir_coverage.py', 'classify_changes.py',
         'release_tests.py', 'presentation_only.rb')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def main():
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    candidate = Path(os.environ['GITHUB_WORKSPACE']).resolve() / 'scripts/ci'
    trusted = False
    selected = candidate
    deadline = time.monotonic() + 30
    api_home = temporary / 'symphony-evidence-bootstrap-api-v1'
    try:
        if not (os.environ.get('GITHUB_EVENT_NAME') == 'pull_request'
                and os.environ.get('GITHUB_HEAD_REF') == 'dev'
                and os.environ.get('GITHUB_BASE_REF') == 'main'
                and os.environ.get('COVERAGE_HEAD_REPOSITORY') == REPOSITORY):
            raise ValueError('ordinary caller executes fresh')
        api_home.mkdir(mode=0o700)
        environment = dict(PATH='/usr/bin:/bin', HOME=str(api_home), GH_CONFIG_DIR=str(api_home / 'gh'),
                           GH_TOKEN=os.environ['GH_TOKEN'], GH_HOST='github.com', GH_PROMPT_DISABLED='1')
        remaining_calls = 14
        def get(path):
            nonlocal remaining_calls
            remaining_calls -= 1
            if remaining_calls < 0 or time.monotonic() >= deadline:
                raise ValueError('immutable loader budget exhausted')
            raw = subprocess.check_output(['/usr/bin/gh', 'api', '--method', 'GET',
                    'repos/' + REPOSITORY + '/' + path], env=environment,
                    stderr=subprocess.DEVNULL, timeout=min(8, max(0.01, deadline - time.monotonic())))
            if len(raw) > 2 * 1024 * 1024 or time.monotonic() > deadline:
                raise ValueError('immutable loader response exceeded bound')
            return json.loads(raw)
        dev = get('branches/dev')
        if dev.get('name') != 'dev' or dev.get('protected') is not True:
            raise ValueError('protected dev source unavailable')
        revision = dev['commit']['sha']
        if not SHA.fullmatch(revision):
            raise ValueError('invalid protected dev source')
        commit = get('git/commits/' + revision)
        if commit.get('sha') != revision or not SHA.fullmatch(commit['tree']['sha']):
            raise ValueError('protected commit/tree binding differs')
        tree = get('git/trees/' + commit['tree']['sha'] + '?recursive=1')
        if tree.get('sha') != commit['tree']['sha'] or tree.get('truncated') is not False:
            raise ValueError('complete protected source unavailable')
        rows = tree['tree']
        directory = temporary / 'symphony-trusted-coverage-v1'
        directory.mkdir(mode=0o700)
        for name in FILES:
            path = 'scripts/ci/' + name
            found = [row for row in rows if row.get('path') == path]
            if len(found) != 1 or found[0].get('type') != 'blob' or found[0].get('mode') not in ('100644', '100755'):
                raise ValueError('trusted module missing or unsupported')
            oid = found[0]['sha']
            if not SHA.fullmatch(oid):
                raise ValueError('invalid trusted module object')
            blob = get('git/blobs/' + oid)
            if blob.get('sha') != oid or blob.get('encoding') != 'base64':
                raise ValueError('trusted module origin differs')
            raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
            if len(raw) > 256 * 1024 or hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != oid:
                raise ValueError('trusted module bytes differ')
            fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as target:
                target.write(raw)
        # All imported Python code is now the immutable upstream snapshot.
        sys.path.insert(0, str(directory))
        import validation_evidence as evidence
        from validation_evidence import Service
        service = Service(os.environ['GH_TOKEN'], api_home, seconds=max(0.1, deadline - time.monotonic()))
        caller = evidence.current_context(service, run_id=int(os.environ['GITHUB_RUN_ID']),
            attempt=int(os.environ['GITHUB_RUN_ATTEMPT']), source=os.environ['GITHUB_SHA'],
            event=os.environ['GITHUB_EVENT_NAME'], ref=os.environ['GITHUB_REF'],
            pr_number=int(os.environ['COVERAGE_PR_NUMBER']))
        if not caller['eligible_consumer'] or caller['trusted_dev_revision'] != revision:
            raise ValueError('actual caller differs from immutable protected dev source')
        _, tested_tree = evidence.immutable_tree(service, os.environ['GITHUB_SHA'])
        controls = evidence.tree_controls(tree)
        if evidence.tree_controls(tested_tree) != controls:
            raise ValueError('actual tested workflow/controllers differ')
        with (directory / 'trusted-source.json').open('x') as target:
            os.chmod(directory / 'trusted-source.json', 0o600)
            json.dump(dict(trusted_dev_revision=revision, controls=controls, source=os.environ['GITHUB_SHA']), target)
        selected, trusted = directory, True
    except (ValueError, OSError, KeyError, TypeError, AttributeError, subprocess.SubprocessError):
        # Never let failed service access turn into a reused green context.
        selected, trusted = candidate, False
    with open(os.environ['GITHUB_OUTPUT'], 'a') as target:
        target.write('controller=' + str(selected) + '\ntrusted=' + str(trusted).lower() + '\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as target:
        target.write('Controller: ' + ('immutable protected-dev snapshot; actual tested merge/controller identities verified' if trusted else
            'ordinary caller or unavailable trusted snapshot; coverage is forced fresh') + '.\n')


if __name__ == '__main__':
    main()
