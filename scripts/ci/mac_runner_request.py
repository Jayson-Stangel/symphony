"""Build an eligible source-bound capacity request using existing live guards."""
import re

from mac_runner_preflight import Refusal, REPOSITORY, SHA, github_read, supported_pr_route
from mac_runner_handoff import context as handoff_context
from select_mac_runner import CONTROLLERS, OWNER, TARGETS, verify_live


def request_from_event(env, event, consumer, job_key, verify=verify_live):
    """None means explicitly ineligible; corrupt eligible metadata refuses.

    This verifies identity only. No runner is selected, no permit is invented,
    and execution admission remains with the finite foreground host controller.
    """
    if consumer not in TARGETS or job_key not in TARGETS[consumer]:
        return None, 'unsupported credential-free Mac target'
    if (env.get('GITHUB_REPOSITORY') != REPOSITORY or env.get('GITHUB_ACTOR') != OWNER
            or env.get('GITHUB_TRIGGERING_ACTOR') != OWNER):
        return None, 'run is not owner-authored and owner-triggered'
    kind = env.get('GITHUB_EVENT_NAME')
    if kind not in {'pull_request', 'push', 'workflow_dispatch'}:
        return None, 'event is not an ordinary trusted branch or PR event'
    if kind != 'pull_request' and not env.get('GITHUB_REF', '').startswith('refs/heads/'):
        return None, 'tag and publication events are ineligible'
    try:
        workflow_ref = env['GITHUB_WORKFLOW_REF']
        prefix = REPOSITORY + '/'
        suffix = '@' + env['GITHUB_REF']
        if not workflow_ref.startswith(prefix) or not workflow_ref.endswith(suffix):
            raise Refusal('handoff controller ref differs')
        controller = workflow_ref[len(prefix):-len(suffix)]
        if controller not in CONTROLLERS:
            return None, 'controller is outside the supported isolated Mac boundary'
        if any(not isinstance(env[key], str) or not SHA.fullmatch(env[key])
               for key in ('GITHUB_SHA', 'GITHUB_WORKFLOW_SHA')):
            raise Refusal('handoff source revisions are invalid')
        if any(not re.fullmatch(r'[1-9][0-9]*', env[key]) for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')):
            raise Refusal('handoff run identity is invalid')
        request = dict(repository=REPOSITORY, event_name=kind, controller_workflow=controller,
                       workflow_sha=env['GITHUB_WORKFLOW_SHA'], source_sha=env['GITHUB_SHA'],
                       head_sha=env['GITHUB_SHA'], base_sha=None, pr_number=None,
                       run_id=int(env['GITHUB_RUN_ID']), run_attempt=int(env['GITHUB_RUN_ATTEMPT']),
                       consumer_workflow=consumer, job_key=job_key)
        if kind == 'pull_request':
            pr = event['pull_request']
            if (pr['user']['login'] != OWNER or pr['head']['repo']['full_name'] != REPOSITORY
                    or pr['base']['repo']['full_name'] != REPOSITORY or not supported_pr_route(pr)):
                return None, 'fork, foreign author or unsupported PR route'
            number = event['number']
            if type(number) is not int or number <= 0 or env['GITHUB_REF'] != f'refs/pull/{number}/merge':
                raise Refusal('handoff PR merge ref differs')
            request.update(pr_number=number, head_sha=pr['head']['sha'], base_sha=pr['base']['sha'])
        handoff_context(request)
        # Existing verifier consumes identity fields only; this dictionary is
        # never serialized as a routing permit or used as execution authority.
        identity = dict(request, pr_head=request['head_sha'])
        if kind == 'pull_request':
            verify(identity, expected_pr_base_ref=pr['base']['ref'], expected_pr_head_ref=pr['head'].get('ref'))
        else:
            verify(identity)
        return request, 'eligible owner source verified; awaiting actual owned capacity'
    except (KeyError, TypeError, AttributeError):
        raise Refusal('handoff caller metadata is incomplete') from None


def read_statuses(source, read=github_read, *, timeout=None):
    """Bounded GET-only capability probe and response inventory; no retries."""
    if not isinstance(source, str) or not SHA.fullmatch(source):
        raise Refusal('status source is invalid')
    import time
    deadline = time.monotonic() + timeout if timeout is not None else None
    result = []
    for page in (1, 2):
        path = f'repos/{REPOSITORY}/commits/{source}/statuses?per_page=100&page={page}'
        if deadline is None:
            rows = read(path)
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refusal('status inventory deadline exhausted')
            rows = read(path, timeout=min(30, remaining))
        if (not isinstance(rows, list) or len(rows) > 100
                or any(not isinstance(item, dict) for item in rows)):
            raise Refusal('status inventory is invalid')
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise Refusal('status inventory exceeds the bounded handoff scan')


def select_owned_runner(raw, env, event, consumer, job_key, requested=True, *,
                        verify=verify_live, statuses=read_statuses, wait=None):
    from mac_runner_handoff import await_response
    from select_mac_runner import HOSTED_DEFAULTS, select_runner
    hosted = HOSTED_DEFAULTS.get(consumer, 'macos-14')
    if type(requested) is not bool:
        raise Refusal('Mac target request classification is invalid')
    if not requested:
        return dict(path='hosted', runner=hosted, reason='Mac qualification intentionally omitted; no capacity requested')
    # An existing exact admitted session must survive the policy transition.
    # The original validator refuses malformed matching permits, including ones
    # that do not select this particular reusable consumer.
    previous = select_runner(raw, env, event, consumer, job_key, verify=verify)
    if previous != hosted:
        return dict(path='owned', runner=previous, reason='existing exact source-bound session permit')
    request, reason = request_from_event(env, event, consumer, job_key, verify)
    if request is None:
        return dict(path='hosted', runner=hosted, reason='ineligible: ' + reason)
    result = (wait or await_response)(request, statuses)
    if result['path'] == 'hosted':
        result = dict(result, runner=hosted)
    return result


def main():
    import json
    import os
    from pathlib import Path
    import subprocess
    import sys
    try:
        requested = os.environ.get('SELECT_TARGET_REQUESTED', 'true')
        if requested not in {'true', 'false'}:
            raise Refusal('Mac target request classification is invalid')
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        if sys.argv[1:] == ['--probe']:
            if requested == 'false':
                print('eligible=false')
                print('status_read=not-requested')
                return 0
            request, reason = request_from_event(os.environ, event,
                os.environ['SELECT_WORKFLOW_PATH'], os.environ['SELECT_JOB_KEY'])
            if request is None:
                print('eligible=false')
                print('status_read=ineligible')
                print('Owned Mac capacity probe omitted: ' + reason, file=sys.stderr)
                return 0
            read_statuses(request['source_sha'], timeout=60)
            print('eligible=true')
            print('status_read=success')
            print('Owned Mac status-read GET succeeded for tested source ' + request['source_sha'], file=sys.stderr)
            return 0
        if sys.argv[1:]:
            raise Refusal('unknown owned Mac selector argument')
        probed = False
        def statuses(source, *, timeout=None):
            nonlocal probed
            rows = read_statuses(source, timeout=timeout)
            if not probed:
                print('Owned Mac status-read GET succeeded for tested source ' + source, file=sys.stderr)
                probed = True
            return rows
        result = select_owned_runner(os.environ.get('BACKUP_MAC_PERMIT', ''), os.environ, event,
            os.environ['SELECT_WORKFLOW_PATH'], os.environ['SELECT_JOB_KEY'], requested == 'true', statuses=statuses)
        summary = ('Owned Mac capacity route: ' + result['path'] + '; ' + result['reason']
                   + '; readiness is not test qualification.'
                   + (' Original status ID: ' + str(result['original_status_id']) if result.get('original_status_id') else ''))
        print(summary, file=sys.stderr)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
                output.write(summary + '\n')
        print('runner=' + result['runner'])
        return 0
    except (Refusal, KeyError, OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired) as error:
        message = str(error) if isinstance(error, Refusal) else 'selector metadata is unavailable or invalid'
        print('Owned Mac selection refused: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
