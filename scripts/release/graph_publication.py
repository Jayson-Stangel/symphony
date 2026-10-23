"""Protected exact-byte GHCR transfer; no Docker image conversion or execution."""
import base64
import datetime
import hashlib
import http.client
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit

import qualified_graph as graph

HOST = 'ghcr.io'
REPOSITORIES = {'jayson-stangel/symphony-codex', 'jayson-stangel/symphony-claude'}
MAX_TOKEN = 65536


class GHCR:
    """Credentials reach only the fixed token endpoint, never blob hosts/logs."""
    def __init__(self, repository, actor, credential, connection=http.client.HTTPSConnection, clock=time.monotonic):
        graph.require(repository in REPOSITORIES, 'unsupported publication repository')
        graph.require(type(actor) is str and re.fullmatch(r'[A-Za-z0-9_.\[\]-]{1,100}', actor), 'invalid registry actor')
        graph.require(type(credential) is str and 0 < len(credential) <= 4096 and all(33 <= ord(x) <= 126 for x in credential), 'invalid registry credential')
        self.repository, self.actor, self.credential = repository, actor, credential
        self.connection, self.clock = connection, clock
        self.token, self.expiry, self.refreshes = None, 0, 0
        self.deadline = clock() + 3600

    def authorize(self, force=False):
        if not force and self.token and self.clock() < self.expiry: return self.token
        graph.require(self.clock() < self.deadline and self.refreshes < 32, 'publication authorization bound exceeded')
        self.refreshes += 1
        basic = base64.b64encode((self.actor + ':' + self.credential).encode()).decode()
        path = '/token?' + urlencode({'service':HOST, 'scope':'repository:' + self.repository + ':pull,push'})
        client = self.connection(HOST, timeout=60)
        try:
            client.request('GET', path, headers={'Authorization':'Basic ' + basic, 'Accept':'application/json'})
            response = client.getresponse()
            graph.require(response.status == 200, 'fixed registry authorization refused')
            raw = response.read(MAX_TOKEN + 1)
            graph.require(len(raw) <= MAX_TOKEN, 'registry authorization response exceeds bound')
            value = graph.strict(raw)
            token = value.get('token') or value.get('access_token')
            graph.require(type(token) is str and 0 < len(token) <= 16384 and all(33 <= ord(x) <= 126 for x in token), 'invalid registry authorization token')
            ttl = value.get('expires_in', 60)  # Distribution's conservative absent-expiry default.
            graph.require(type(ttl) is int and 30 <= ttl <= 3600, 'invalid registry authorization lifetime')
            age = 0
            if 'issued_at' in value:
                issued = value['issued_at']
                graph.require(type(issued) is str and len(issued) <= 64, 'invalid registry issue time')
                try:
                    stamp = datetime.datetime.fromisoformat(issued.replace('Z', '+00:00'))
                    graph.require(stamp.tzinfo is not None, 'registry issue time lacks timezone')
                    age = time.time() - stamp.timestamp()
                except (ValueError, OverflowError): raise ValueError('qualified graph: invalid registry issue time') from None
                graph.require(-30 <= age < ttl - 10, 'registry authorization already expired')
            self.token = token; self.expiry = self.clock() + min(ttl, ttl - max(0, age)) - 10
            return token
        finally: client.close()

    def request(self, method, suffix, body=None, size=None, expected=(200,), maximum=graph.MAX_METADATA, content_type=None):
        graph.require(method in ('HEAD', 'GET', 'POST', 'PUT'), 'unsupported publication method')
        # Every caller resource is content addressed or an exact upload session.
        graph.require(re.fullmatch(r'/(?:blobs/sha256:[a-f0-9]{64}|manifests/sha256:[a-f0-9]{64}|blobs/uploads/(?:[A-Za-z0-9-]+(?:\?[^#\r\n]{1,16384})?)?)', suffix), 'invalid publication resource')
        start = body.tell() if hasattr(body, 'tell') else None
        for attempt in range(2):
            graph.require(self.clock() < self.deadline, 'publication request deadline exceeded')
            token = self.authorize(force=bool(attempt))
            if start is not None: body.seek(start)
            headers = {'Authorization':'Bearer ' + token, 'Accept':', '.join(sorted(graph.SINGLE))}
            if size is not None: headers['Content-Length'] = str(size)
            if content_type: headers['Content-Type'] = content_type
            client = self.connection(HOST, timeout=60)
            try:
                client.request(method, '/v2/' + self.repository + suffix, body=body, headers=headers)
                response = client.getresponse()
                if response.status == 401 and attempt == 0:
                    response.read(MAX_TOKEN + 1)
                    continue
                # No redirects: Bearer credentials can never leave fixed GHCR.
                graph.require(response.status in expected, 'registry publication refused with status ' + str(response.status))
                raw = response.read(maximum + 1)
                graph.require(len(raw) <= maximum, 'registry publication response exceeds bound')
                return response.status, response.headers, raw
            finally: client.close()
        raise ValueError('qualified graph: bounded registry reauthorization failed')

    def upload_location(self, location, digest):
        graph.require(type(location) is str and len(location) <= 16384, 'invalid publication upload location')
        value = urlsplit(location)
        graph.require(not value.fragment and not value.username and not value.password and
                      ((not value.scheme and not value.netloc) or (value.scheme == 'https' and value.netloc == HOST)), 'foreign publication upload location')
        prefix = '/v2/' + self.repository + '/blobs/uploads/'
        graph.require(value.path.startswith(prefix) and re.fullmatch('[A-Za-z0-9-]+', value.path[len(prefix):]), 'upload outside selected repository')
        try:query = parse_qsl(value.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:raise ValueError('qualified graph: invalid publication upload state') from None
        graph.require(len(query) <= 1 and all(key == '_state' and len(item) <= 8192 for key,item in query), 'invalid publication upload state')
        return value.path[len('/v2/' + self.repository):] + '?' + urlencode(query + [('digest',digest)])


class Upload:
    def __init__(self, stream, size, identity):
        self.stream, self.size, self.identity = stream, size, identity
        self.count, self.digest = 0, hashlib.sha256()
        self.deadline = time.monotonic() + 1200
    def read(self, size=-1):
        graph.require(time.monotonic() < self.deadline, 'publication upload deadline exceeded')
        block = self.stream.read(size); self.count += len(block); self.digest.update(block)
        graph.require(self.count <= self.size, 'publication upload grew')
        return block
    def tell(self): return self.stream.tell()
    def seek(self, value):
        self.stream.seek(value); self.count = 0; self.digest = hashlib.sha256()
    def verify(self):
        graph.require(self.count == self.size and 'sha256:' + self.digest.hexdigest() == self.identity, 'publication upload bytes differ')


def publish_selected(selections, client_factory=None):
    """Preflight ALL graph bytes and destinations before requesting any token."""
    graph.require(type(selections) is list and len(selections)<=4,'publication selection bound exceeded')
    prepared = []; destinations=set()
    for selected in selections:
        record, directory, image = selected['record'], selected['directory'], selected['image']
        match = re.fullmatch(r'ghcr.io/(jayson-stangel/symphony-(codex|claude))@(sha256:[a-f0-9]{64})', image)
        graph.require(match and match[2] == record['provider'] and match[3] == record['image'].rsplit('@',1)[-1], 'publication changes selected provider/digest')
        graph.require(image not in destinations,'duplicate publication destination')
        destinations.add(image)
        verified = graph.validate_graphs(directory, [record], exact=False)[record['image']]
        prepared.append((directory, record, verified, match[1], match[3]))
    # Recheck every input snapshot immediately before any external authorization.
    snapshots = [{path:graph.file_identity(graph.regular(path)) for path in Path(directory).rglob('*') if path.is_file()}
                 for directory, *_ in prepared]
    for (directory, record, *_), before in zip(prepared,snapshots):
        graph.validate_graphs(directory,[record],exact=False)
        graph.require(all(graph.file_identity(graph.regular(path))==identity for path,identity in before.items()), 'publication graph changed during preflight')
    if client_factory is None:
        actor, credential = os.environ.get('GITHUB_ACTOR'), os.environ.get('GH_TOKEN')
        client_factory = lambda repository:GHCR(repository, actor, credential)
    clients = {}
    for *_, repository, root in prepared:
        if repository not in clients:clients[repository]=client_factory(repository)
    # Resolve every fixed repository authorization before the first blob write.
    # This is a capability preflight, not a claim of atomic registry mutation.
    for client in clients.values():client.authorize()
    for directory, record, verified, repository, root in prepared:
        client=clients[repository]
        for descriptor in verified['descriptors']:
            status, headers, _ = client.request('HEAD','/blobs/'+descriptor['digest'],expected=(200,404),maximum=0)
            if status == 404:
                _,headers,_=client.request('POST','/blobs/uploads/',size=0,expected=(202,))
                target=client.upload_location(headers.get('Location'),descriptor['digest'])
                path=graph.blob_path(directory,descriptor['digest'])
                expected_identity=verified['file_identities'][descriptor['digest']]
                graph.require(graph.file_identity(graph.regular(path))==expected_identity,'publication blob changed before upload')
                with path.open('rb') as stream:
                    graph.require(graph.file_identity(os.fstat(stream.fileno()))==expected_identity,'publication blob changed before open')
                    upload=Upload(stream,descriptor['size'],descriptor['digest'])
                    _,headers,_=client.request('PUT',target,body=upload,size=descriptor['size'],content_type='application/octet-stream',expected=(201,))
                    upload.verify()
                graph.require(graph.file_identity(graph.regular(path))==expected_identity,'publication blob changed during upload')
                graph.require(headers.get('Docker-Content-Digest')==descriptor['digest'],'publication blob digest changed')
            _,headers,_=client.request('HEAD','/blobs/'+descriptor['digest'],maximum=0)
            graph.require(headers.get('Docker-Content-Digest')==descriptor['digest'] and headers.get('Content-Length')==str(descriptor['size']),'published blob metadata differs')
        _,headers,_=client.request('PUT','/manifests/'+root,body=verified['manifest'],size=len(verified['manifest']),content_type=verified['media_type'],expected=(201,))
        graph.require(headers.get('Docker-Content-Digest')==root,'publication manifest digest changed')
