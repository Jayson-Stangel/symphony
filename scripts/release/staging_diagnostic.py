"""Closed read-only HTTP observations for one fixed public fixture, never admission."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

from component_provenance import api_command

REPOSITORY = 'Jayson-Stangel/symphony'
WORKFLOW = '.github/workflows/macos-local-signing-import.yml'
FIXTURE_NAME = 'permission-probe.zip'
FIXTURE_SIZE = 240
FIXTURE_DIGEST = 'sha256:bcd7136193af68bb874051656edae4326f8dacd0deca505580a5035c6e2875c1'
SHA = re.compile(r'[a-f0-9]{40}\Z')
ID = re.compile(r'[1-9][0-9]{0,19}\Z')


class Refusal(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Read-only staging diagnostic refused')


def require(condition, reason):
    if not condition:
        raise Refusal(reason)


def source_guard(root, env, approved, fixture, conflicting, git=subprocess.run):
    require(conflicting == 'false', 'invalid-mode')
    revision = env.get('GITHUB_SHA', '')
    ref = env.get('GITHUB_REF', '')
    require(env.get('GITHUB_REPOSITORY') == REPOSITORY and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and SHA.fullmatch(revision) and approved == revision and SHA.fullmatch(fixture or '')
            and ref.startswith('refs/heads/codex/')
            and env.get('GITHUB_WORKFLOW_REF') == REPOSITORY + '/' + WORKFLOW + '@' + ref
            and env.get('GITHUB_WORKFLOW_SHA') == revision, 'source-binding-mismatch')
    def read(*args):
        result = git(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=20)
        require(result.returncode == 0, 'source-binding-mismatch')
        return result.stdout.strip()
    require(read('rev-parse', 'HEAD') == revision and not read('status', '--porcelain', '--untracked-files=all'),
            'source-binding-mismatch')
    read('merge-base', '--is-ancestor', fixture, revision)


def split_response(raw):
    """Observe the real included status; raw headers/body never enter a receipt."""
    require(isinstance(raw, bytes) and len(raw) <= 1024 * 1024, 'invalid-response')
    header, separator, body = raw.partition(b'\r\n\r\n')
    if not separator:
        header, separator, body = raw.partition(b'\n\n')
    if not separator or not header.splitlines() or len(header) > 65536:
        return None, b''
    match = re.fullmatch(rb'HTTP/(?:1\.[01]|2(?:\.0)?|3(?:\.0)?) ([1-5][0-9]{2})(?: [^\r\n]*)?', header.splitlines()[0])
    return (int(match[1]), body) if match else (None, b'')


def error_status(raw):
    """A bounded unambiguous numeric marker from gh error lines only."""
    if not isinstance(raw, bytes) or len(raw) > 65536:
        return None
    codes = {int(code) for line in raw.splitlines() if line.startswith(b'gh:')
             for code in re.findall(rb'\(HTTP[ \t]+([1-5][0-9]{2})\)', line)}
    return codes.pop() if len(codes) == 1 else None


def http_reason(status):
    if status == 401: return 'http-unauthorized'
    if status == 403: return 'http-forbidden'
    if status == 404: return 'http-not-found'
    if status == 429: return 'http-rate-limited'
    if status is not None and status >= 500: return 'http-server-error'
    return 'unknown-http-result' if status is None else 'http-other'


def diagnose(root, destination, approved, fixture, release_id, asset_id, digest, env, download,
             conflicting='false', invoke=subprocess.run, git=subprocess.run, command=api_command):
    """Only the production-independent source guard can enable these GETs."""
    require(not destination.exists() and not destination.is_symlink(), 'receipt-already-exists')
    receipt = {'schema': 1, 'scope': 'transport-http-diagnostic-only', 'status': 'failed',
               'controller_revision': None, 'fixture_revision': None,
               'fixture_name': FIXTURE_NAME, 'fixture_size': FIXTURE_SIZE, 'fixture_digest': FIXTURE_DIGEST,
               'observations': []}
    # Do not retain unchecked user input, paths, refs, accounts, URLs or output.
    def save():
        destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        destination.chmod(0o600)
    with destination.open('x') as target:
        target.write('{}\n')
    destination.chmod(0o600)
    stage = 'source-binding'
    status = None
    exit_status = None
    try:
        source_guard(root, env, approved, fixture, conflicting, git)
        receipt.update(controller_revision=env['GITHUB_SHA'], fixture_revision=fixture)
        require(ID.fullmatch(str(release_id)) and ID.fullmatch(str(asset_id)) and digest == FIXTURE_DIGEST,
                'fixture-binding-mismatch')
        receipt.update(release_id=int(release_id), asset_id=int(asset_id))
        receipt['observations'].append({'stage': stage, 'status': 'success', 'http_status': None, 'reason': 'ok'})
        save()
        def fetch(path, binary=False):
            nonlocal stage, status, exit_status
            require(path in ('releases/' + str(release_id), 'releases/assets/' + str(asset_id)), 'fixture-binding-mismatch')
            stage = 'octet-download' if binary else 'release-metadata' if path.startswith('releases/') and not path.startswith('releases/assets/') else 'asset-metadata'
            status = None; exit_status = None
            args = command(path, binary=binary) + ['--include']
            if binary:
                args += ['-H', 'Accept: application/octet-stream']
            result = invoke(args, capture_output=True, timeout=20)
            exit_status = result.returncode if type(result.returncode) is int and -255 <= result.returncode <= 255 else None
            status, body = split_response(result.stdout)
            if status is None and exit_status not in (None, 0):
                status = error_status(result.stderr)
            require(exit_status == 0 and status is not None and 200 <= status < 300, http_reason(status))
            value = body if binary else json.loads(body)
            if stage == 'release-metadata':
                require(isinstance(value, dict) and type(value.get('id')) is int
                        and value['id'] == int(release_id), 'release-binding-mismatch')
            if stage == 'asset-metadata':
                require(isinstance(value, dict) and type(value.get('id')) is int and value['id'] == int(asset_id)
                        and value.get('name') == FIXTURE_NAME
                        and type(value.get('size')) is int and value['size'] == FIXTURE_SIZE
                        and value.get('digest') == FIXTURE_DIGEST, 'asset-binding-mismatch')
            receipt['observations'].append({'stage': stage, 'status': 'success', 'http_status': status,
                                            'exit_status': exit_status, 'reason': 'ok'})
            save()
            return value
        raw, _ = download(release_id, asset_id, digest, fixture, fetch, 4096)
        stage = 'fixture-binding'
        require(len(raw) == FIXTURE_SIZE and 'sha256:' + hashlib.sha256(raw).hexdigest() == FIXTURE_DIGEST,
                'fixture-bytes-mismatch')
        receipt['observations'].append({'stage': stage, 'status': 'success', 'http_status': None, 'reason': 'ok'})
        receipt['status'] = 'success'
    except Refusal as error:
        receipt['observations'].append({'stage': stage, 'status': 'failed', 'http_status': status,
                                       'exit_status': exit_status, 'reason': error.reason})
    except subprocess.TimeoutExpired:
        receipt['observations'].append({'stage': stage, 'status': 'failed', 'http_status': None,
                                       'reason': 'request-timeout'})
    except (OSError, subprocess.SubprocessError):
        receipt['observations'].append({'stage': stage, 'status': 'failed', 'http_status': None,
                                       'reason': 'request-unavailable'})
    except (ValueError, KeyError, TypeError, AttributeError):
        reason = {'release-metadata': 'release-binding-mismatch', 'asset-metadata': 'asset-binding-mismatch',
                  'octet-download': 'fixture-bytes-mismatch'}.get(stage, 'invalid-response')
        receipt['observations'].append({'stage': stage, 'status': 'failed', 'http_status': status,
                                       'exit_status': exit_status, 'reason': reason})
    finally:
        save()
    require(receipt['status'] == 'success', 'diagnostic-failed')
    return receipt
