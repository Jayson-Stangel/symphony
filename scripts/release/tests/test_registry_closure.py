"""Real anonymous HTTP/config/blob checks; fixtures are not native image execution."""
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).parents[1]))
import registry_closure as closure


class RegistryClosureTest(unittest.TestCase):
    def setUp(self):
        self.config=b'{"architecture":"arm64","os":"linux"}'
        self.layer=b'opaque-small-layer-fixture'
        def descriptor(data):return {'digest':'sha256:'+hashlib.sha256(data).hexdigest(),'size':len(data)}
        self.configuration=descriptor(self.config);self.layer_descriptor=descriptor(self.layer)
        self.manifest=json.dumps({'schemaVersion':2,'config':self.configuration,'layers':[self.layer_descriptor]}).encode()
        self.digest='sha256:'+hashlib.sha256(self.manifest).hexdigest()
        self.image='localhost:5000/distribution/fixture@'+self.digest
        self.variant='valid';self.calls=[]
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def handle_request(self):
                owner.calls.append((self.command,self.path))
                digest=self.path.rsplit('/',1)[-1]
                mapping={owner.digest:owner.manifest,owner.configuration['digest']:owner.config,owner.layer_descriptor['digest']:owner.layer}
                if owner.variant in ('missing','cached-head-missing') and digest==owner.layer_descriptor['digest'] and not (owner.variant=='cached-head-missing' and self.command=='HEAD'):
                    self.send_error(404);return
                if owner.variant=='permission' and digest==owner.layer_descriptor['digest']:
                    self.send_error(403);return
                body=mapping.get(digest)
                if body is None:self.send_error(404);return
                ranged=self.headers.get('Range')=='bytes=0-0'
                self.send_response(206 if ranged else 200)
                if ranged:self.send_header('Content-Range','bytes 0-0/'+str(len(body)))
                self.send_header('Docker-Content-Digest',digest)
                self.send_header('Content-Length','1' if ranged else str(len(body)))
                self.end_headers()
                if self.command!='HEAD':
                    if owner.variant=='corrupt' and digest==owner.layer_descriptor['digest']:body=b'x'*len(body)
                    self.wfile.write(body[:1] if ranged else body)
            do_GET=handle_request
            do_HEAD=handle_request
            def log_message(self,*args):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)
    def cleanup(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
    def fetch(self,url,headers=None,**options):
        # Only test transport maps the fixed production/test registry endpoint to
        # this isolated random loopback port; production has no endpoint override.
        target='http://127.0.0.1:'+str(self.server.server_port)+urlsplit(url).path
        return closure.request(target,headers,**options)
    def test_lightweight_reuse_checks_actual_config_get_and_every_layer_head(self):
        self.assertTrue(closure.registry_available(self.image,fetch=self.fetch))
        self.assertIn(('GET','/v2/distribution/fixture/blobs/'+self.configuration['digest']),self.calls)
        self.assertIn(('HEAD','/v2/distribution/fixture/blobs/'+self.layer_descriptor['digest']),self.calls)
        self.assertIn(('GET','/v2/distribution/fixture/blobs/'+self.layer_descriptor['digest']),self.calls)
    def test_new_transfer_streams_actual_layer_bytes_and_rejects_corruption(self):
        self.assertTrue(closure.registry_available(self.image,fetch=self.fetch,full=True))
        self.assertIn(('GET','/v2/distribution/fixture/blobs/'+self.layer_descriptor['digest']),self.calls)
        self.variant='corrupt'
        with self.assertRaisesRegex(ValueError,'layer checksum'):
            closure.registry_available(self.image,fetch=self.fetch,full=True)
    def test_missing_and_permission_denied_blobs_refuse_despite_valid_manifest(self):
        for variant in ('missing','cached-head-missing','permission'):
            with self.subTest(variant=variant):
                self.variant=variant
                with self.assertRaises(HTTPError):closure.registry_available(self.image,fetch=self.fetch)
