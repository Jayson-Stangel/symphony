#!/usr/bin/env python3
"""Finite owned-Mac capacity replies. Readiness is never test qualification.

The foreground host provisioner retains admission, one-session exclusion and
watchdog ownership. This module cannot provision, register or start a runner.
"""
import base64
import datetime
import json
import re
import time
from urllib.parse import urlsplit
import uuid

from mac_runner_preflight import Refusal

OWNER = 'Jayson-Stangel'
REPOSITORY = OWNER + '/symphony'
MAX_WAIT_SECONDS = 300
MAX_READS = 15  # At most two status pages per read: <=30 HTTP GETs total.
FIELDS = {'schema', 'repository', 'event_name', 'controller_workflow', 'workflow_sha', 'source_sha',
          'head_sha', 'base_sha', 'pr_number', 'run_id', 'run_attempt', 'consumer_workflow', 'job_key',
          'session_id', 'runner_label', 'runner_id', 'admission_sha256', 'expires_at',
          'outcome', 'reason', 'execution_started', 'cleanup_complete'}
IDENTITY = FIELDS - {'schema', 'session_id', 'runner_label', 'runner_id', 'admission_sha256',
                     'expires_at', 'outcome', 'reason', 'execution_started', 'cleanup_complete'}
UNAVAILABLE_REASONS = {'offline', 'busy', 'provisioning-failed-clean'}


def timestamp(value):
    try:
        return datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        raise Refusal('handoff requires an exact UTC timestamp') from None


def context(request):
    """Request comes from independently verified caller metadata, never a reply."""
    try:
        if (request['repository'] != REPOSITORY or set(request) != IDENTITY
                or any(type(request[key]) is not int or request[key] <= 0 for key in ('run_id', 'run_attempt'))
                or request['event_name'] not in {'pull_request', 'push', 'workflow_dispatch'}
                or any(not isinstance(request[key], str) or not re.fullmatch(r'[a-f0-9]{40}', request[key])
                       for key in ('source_sha', 'head_sha', 'workflow_sha'))
                or not re.fullmatch(r'\.github/workflows/[a-z][a-z0-9-]*\.yml', request['controller_workflow'])
                or not re.fullmatch(r'\.github/workflows/[a-z][a-z0-9-]*\.yml', request['consumer_workflow'])
                or not re.fullmatch(r'[a-z][a-z0-9-]*', request['job_key'])):
            raise Refusal('handoff caller identity is unsupported')
        if request['event_name'] == 'pull_request':
            if (type(request['pr_number']) is not int or request['pr_number'] <= 0 or
                    not isinstance(request['base_sha'], str) or not re.fullmatch(r'[a-f0-9]{40}', request['base_sha'])):
                raise Refusal('handoff PR source metadata is incomplete')
        elif request['pr_number'] is not None or request['base_sha'] is not None or request['head_sha'] != request['source_sha']:
            raise Refusal('handoff branch source metadata differs')
        stem = request['consumer_workflow'].removeprefix('.github/workflows/').removesuffix('.yml')
        return f"owned-mac/v1/{request['run_id']}/{request['run_attempt']}/{stem}/{request['job_key']}"
    except (KeyError, TypeError):
        raise Refusal('handoff caller metadata is incomplete') from None


def ready_url(payload):
    """Encoding only; publishing requires the separately authorized host caller."""
    data = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(data).decode().rstrip('=')
    result = (f"https://github.com/{REPOSITORY}/actions/runs/{payload['run_id']}/attempts/{payload['run_attempt']}"
              '#owned-mac-v1=' + encoded)
    if len(result) > 2048:
        raise Refusal('handoff response URL exceeds its bound')
    return result


def decode_url(value, request):
    if not isinstance(value, str) or len(value) > 2048:
        raise Refusal('handoff response URL is missing or oversized')
    url = urlsplit(value)
    expected_path = f"/{REPOSITORY}/actions/runs/{request['run_id']}/attempts/{request['run_attempt']}"
    if (url.scheme != 'https' or url.netloc != 'github.com' or url.path != expected_path or url.query
            or not url.fragment.startswith('owned-mac-v1=')):
        raise Refusal('handoff response origin/run differs')
    encoded = url.fragment.removeprefix('owned-mac-v1=')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', encoded):
        raise Refusal('handoff response encoding is invalid')
    try:
        raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        value = json.loads(raw)
        if ready_url(value) != url.geturl():
            raise ValueError('not canonical')
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise Refusal('handoff response payload is malformed or noncanonical') from None


def response(statuses, request, now):
    """Return the newest authentic response or None; matching bad data refuses."""
    name = context(request)
    if (not isinstance(statuses, list) or len(statuses) > 200
            or any(not isinstance(item, dict) for item in statuses)):
        raise Refusal('handoff status inventory malformed or exceeds bound')
    matching = [item for item in statuses if isinstance(item, dict) and item.get('context') == name]
    if not matching:
        return None
    for item in matching:
        if (not isinstance(item.get('creator'), dict) or item['creator'].get('login') != OWNER or type(item.get('id')) is not int or item['id'] <= 0
                or item.get('url') != f"https://api.github.com/repos/{REPOSITORY}/statuses/{request['source_sha']}"):
            raise Refusal('matching handoff producer/service identity differs')
        timestamp(item.get('created_at'))
    newest = max(matching, key=lambda item: item['id'])
    if any(timestamp(item['created_at']) > timestamp(newest['created_at']) for item in matching):
        raise Refusal('handoff service ordering is inconsistent')
    created = timestamp(newest['created_at'])
    if created > now:
        raise Refusal('handoff service time is in the future')
    payload = decode_url(newest.get('target_url'), request)
    if (not isinstance(payload, dict) or set(payload) != FIELDS or type(payload.get('schema')) is not int
            or payload['schema'] != 1 or any(payload.get(key) != request[key] or type(payload.get(key)) is not type(request[key]) for key in IDENTITY)
            or payload.get('execution_started') is not False
            or not isinstance(payload.get('admission_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', payload['admission_sha256'])):
        raise Refusal('handoff scope/authority receipt differs or execution already started')
    expiry = timestamp(payload['expires_at'])
    if not now < expiry <= created + datetime.timedelta(hours=2):
        raise Refusal('matching handoff expired or exceeds the permitted window')
    if payload['outcome'] == 'preparing':
        try:
            session = uuid.UUID(payload['session_id'])
        except (ValueError, TypeError, AttributeError):
            raise Refusal('preparing handoff session is invalid') from None
        if (str(session) != payload['session_id'] or payload['runner_label'] != 'symphony-backup-mac-' + session.hex
                or payload['runner_id'] is not None or payload['reason'] is not None
                or newest.get('state') != 'pending' or payload['cleanup_complete'] is not False):
            raise Refusal('preparing handoff is malformed; no hosted fallback')
        return dict(path='preparing', runner=None, reason='owned preparation in progress; no label selected',
                    original_status_id=newest['id'], payload=payload)
    if payload['outcome'] == 'refused':
        raise Refusal('owned preparation cleanup unverified; no hosted fallback')
    if payload['outcome'] == 'ready':
        try:
            session = uuid.UUID(payload['session_id'])
        except (ValueError, TypeError, AttributeError):
            raise Refusal('ready handoff session is invalid') from None
        if (str(session) != payload['session_id'] or payload['runner_label'] != 'symphony-backup-mac-' + session.hex
                or type(payload['runner_id']) is not int or payload['runner_id'] <= 0
                or payload['reason'] is not None or newest.get('state') != 'success'
                or payload['cleanup_complete'] is not False):
            raise Refusal('ready handoff lacks a real offline prepared resource')
        return dict(path='owned', runner=payload['runner_label'], reason='prepared owned capacity',
                    original_status_id=newest['id'], payload=payload)
    if payload['outcome'] == 'unavailable':
        # A busy response concerns THIS request; it never acquires or cleans the
        # other healthy session holding the shared local lease.
        if (payload['reason'] not in UNAVAILABLE_REASONS or newest.get('state') != 'error'
                or payload['cleanup_complete'] is not True
                or any(payload[key] is not None for key in ('runner_label', 'runner_id'))):
            raise Refusal('unavailable handoff has active/uncertain resources')
        if payload['session_id'] is not None:
            try:
                session = uuid.UUID(payload['session_id'])
            except (ValueError, TypeError, AttributeError):
                raise Refusal('clean terminal session identity invalid') from None
            if str(session) != payload['session_id'] or payload['reason'] != 'provisioning-failed-clean':
                raise Refusal('unavailable handoff session does not name clean preparation')
        return dict(path='hosted', runner=None, reason=payload['reason'], original_status_id=newest['id'], payload=payload)
    raise Refusal('unknown matching handoff outcome')


def await_response(request, read_statuses, *, clock=time.monotonic, pause=time.sleep,
                   utcnow=lambda: datetime.datetime.now(datetime.timezone.utc)):
    """Before ANY Mac allocation. Read errors never masquerade as unavailable."""
    deadline = clock() + MAX_WAIT_SECONDS
    preparation = None
    for _ in range(MAX_READS):
        if clock() >= deadline:
            break
        try:
            rows = read_statuses(request['source_sha'], timeout=max(0, deadline - clock()))
            # Slow API delivery cannot turn an expired handoff into a private
            # allocation after the ordinary hosted decision deadline.
            result = response(rows, request, utcnow())
            if clock() >= deadline:
                if result is not None:
                    raise Refusal('owned reply arrived after deadline; cleanup unverified; no hosted fallback')
                break
        except Refusal:
            raise
        except Exception:
            raise Refusal('owned handoff API unavailable; no capacity decision established') from None
        if result is not None:
            if result['path'] == 'preparing':
                if preparation is not None and result['original_status_id'] != preparation['original_status_id']:
                    raise Refusal('owned preparation status was replaced unexpectedly')
                preparation = result
            else:
                if preparation is not None:
                    before, after = preparation['payload'], result['payload']
                    if (result['original_status_id'] <= preparation['original_status_id']
                            or any(after[key] != before[key] for key in ('session_id', 'admission_sha256'))
                            or (result['path'] == 'owned' and after['runner_label'] != before['runner_label'])
                            or (result['path'] == 'hosted' and after['reason'] != 'provisioning-failed-clean')):
                        raise Refusal('owned preparation terminal identity differs; no hosted fallback')
                return result
        pause(min(MAX_WAIT_SECONDS / MAX_READS, max(0, deadline - clock())))
    if preparation is not None:
        raise Refusal('owned preparation timed out with cleanup unverified; no hosted fallback')
    return dict(path='hosted', runner=None, reason='owned-handoff-timeout; no owned label allocated', original_status_id=None)
