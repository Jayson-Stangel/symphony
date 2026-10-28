"""Narrow fixed-origin opaque upload transport; never changes candidate records."""
import hashlib
import re
from urllib.parse import parse_qsl, quote, urlsplit

import graph_publication as original

DIGEST = r'sha256:[a-f0-9]{64}'
SESSION = r'[A-Za-z0-9_.=~-]{1,4096}'


def require(condition, message):
    if not condition:
        raise ValueError('transport recovery: ' + message)


def upload_target(repository, location, digest):
    """Preserve opaque query bytes; credentials can reach only the fixed repo."""
    require(repository in original.REPOSITORIES, 'unsupported repository')
    require(type(location) is str and 0 < len(location) <= 16384 and '#' not in location and
            all(33 <= ord(c) <= 126 for c in location), 'invalid location')
    require(re.fullmatch(DIGEST, digest or ''), 'invalid digest')
    value = urlsplit(location)
    require(not value.fragment and not value.username and not value.password and
            ((not value.scheme and not value.netloc) or
             (value.scheme == 'https' and value.netloc == 'ghcr.io')), 'foreign location')
    prefix = '/v2/' + repository
    require(value.path.startswith(prefix + '/'), 'foreign repository')
    suffix = value.path[len(prefix):]
    match = re.fullmatch(r'/blobs/uploads?/(' + SESSION + ')', suffix)
    require(match and match[1] not in ('.', '..'), 'unsupported upload path')
    # The state remains opaque and byte-for-byte unchanged, including escaping.
    try:
        query = parse_qsl(value.query, keep_blank_values=True, strict_parsing=True) if value.query else []
    except ValueError:
        raise ValueError('transport recovery: invalid query') from None
    require(len(query) <= 1 and all(k == '_state' and len(v) <= 8192 and
            all(32 <= ord(c) <= 126 for c in v) for k, v in query), 'unsupported query')
    require(not re.search(r'%(?![a-fA-F0-9]{2})', value.query), 'invalid query escaping')
    return suffix + '?' + (value.query + '&' if value.query else '') + 'digest=' + quote(digest, safe='')


def valid_resource(method, suffix):
    if method == 'HEAD':
        return bool(re.fullmatch('/blobs/' + DIGEST, suffix))
    if method == 'POST':
        return suffix == '/blobs/uploads/'
    if method == 'PUT' and re.fullmatch('/manifests/' + DIGEST, suffix):
        return True
    if method == 'PUT':
        path, separator, query = suffix.partition('?')
        match = re.fullmatch(r'/blobs/uploads?/(' + SESSION + ')', path)
        if not separator or not match or match[1] in ('.', '..'):
            return False
        # Reuse identical Location validation after removing the one final digest.
        state, separator, digest = query.rpartition('digest=')
        if not separator or state and not state.endswith('&'):
            return False
        if state:
            state = state[:-1]
        from urllib.parse import unquote
        decoded = unquote(digest)
        try:
            expected = upload_target('jayson-stangel/symphony-codex',
                '/v2/jayson-stangel/symphony-codex' + path + ('?' + state if state else ''), decoded)
            return expected == suffix
        except ValueError:
            return False
    return False


class GHCR(original.GHCR):
    def __init__(self, *args, journal, **kwargs):
        super().__init__(*args, **kwargs)
        self.journal = journal

    def upload_location(self, location, digest):
        # Structural evidence only; never retain or print opaque URL/state.
        row = {'event': 'upload-location', 'repository': self.repository,
               'location_sha256': hashlib.sha256(str(location).encode()).hexdigest()}
        if type(location) is str and len(location) <= 16384:
            try:
                value = urlsplit(location)
                row.update(path_segments=len(value.path.split('/')),
                           session_length=len(value.path.rsplit('/', 1)[-1]),
                           origin_allowed=(not value.scheme and not value.netloc) or
                                          (value.scheme == 'https' and value.netloc == original.HOST),
                           repository_prefix=value.path.startswith('/v2/' + self.repository + '/'),
                           session_character_classes={name: any(c in chars for c in value.path.rsplit('/', 1)[-1])
                               for name, chars in {'underscore': '_', 'dot': '.', 'equals': '=', 'tilde': '~'}.items()},
                           endpoint='singular' if '/blobs/upload/' in value.path else
                                    'plural' if '/blobs/uploads/' in value.path else 'unknown',
                           query_present=bool(value.query),
                           query_has_state=value.query.startswith('_state='),
                           query_field_count=len(value.query.split('&')) if value.query else 0)
            except ValueError:
                pass
        self.journal(row)
        return upload_target(self.repository, location, digest)

    def request(self, method, suffix, body=None, size=None, expected=(200,),
                maximum=original.graph.MAX_METADATA, content_type=None):
        require(type(suffix) is str and len(suffix) <= 20000 and valid_resource(method, suffix), 'invalid resource')
        start = body.tell() if hasattr(body, 'tell') else None
        for attempt in range(2):
            require(self.clock() < self.deadline, 'request deadline exceeded')
            token = self.authorize(force=bool(attempt))
            if start is not None:
                body.seek(start)
            headers = {'Authorization': 'Bearer ' + token,
                       'Accept': ', '.join(sorted(original.graph.SINGLE))}
            if size is not None:
                headers['Content-Length'] = str(size)
            if content_type:
                headers['Content-Type'] = content_type
            client = self.connection(original.HOST, timeout=60)
            try:
                client.request(method, '/v2/' + self.repository + suffix, body=body, headers=headers)
                response = client.getresponse()
                self.journal({'event': 'registry-response', 'repository': self.repository,
                              'method': method, 'resource_kind': suffix.split('/')[1],
                              'status': response.status, 'authorization_attempt': attempt + 1})
                if response.status == 401 and attempt == 0:
                    response.read(original.MAX_TOKEN + 1)
                    continue
                require(response.status in expected, 'registry status ' + str(response.status))
                raw = response.read(maximum + 1)
                require(len(raw) <= maximum, 'response exceeds bound')
                return response.status, response.headers, raw
            finally:
                client.close()
        raise ValueError('transport recovery: bounded authorization failed')
