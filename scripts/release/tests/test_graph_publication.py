"""Synthetic exact raw transfer/auth controls only; never contacts GHCR."""
import copy
import io
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).parents[1]))
import graph_publication as publish
import qualified_graph as graph
import registry_closure
from test_qualified_graph import Fixture


class FakeClient:
    def __init__(self):self.calls=[];self.blobs={};self.manifests={}
    def authorize(self):self.calls.append(('authorize','fixed repository'))
    def request(self,method,suffix,body=None,size=None,**kwargs):
        self.calls.append((method,suffix));identity=suffix.rsplit('/',1)[-1]
        if method=='HEAD':
            value=self.blobs.get(identity)
            return (404,{},b'') if value is None else (200,{'Content-Length':str(len(value)),'Docker-Content-Digest':identity},b'')
        if method=='POST':return 202,{'Location':'unused'},b''
        if hasattr(body,'read'):
            parts=[]
            while chunk:=body.read(17):parts.append(chunk)
            raw=b''.join(parts)
        else:raw=body
        if '/uploads/' in suffix:self.blobs[identity]=raw
        else:self.manifests[identity]=raw
        return 201,{'Docker-Content-Digest':graph.digest(raw)},b''
    def upload_location(self,location,identity):return '/blobs/uploads/'+identity


class RawPublicationTests(unittest.TestCase):
    def test_actual_raw_graph_bytes_preserved_and_last_bad_graph_prevents_auth(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();selections=[];fixtures=[]
            for provider in ('codex','claude'):
                fixture=Fixture(provider=provider,sources=('a',));fixtures.append(fixture)
                folder=fixture.write(root/provider);record=fixture.records[0]
                selections.append({'directory':folder,'record':record,'image':'ghcr.io/jayson-stangel/symphony-'+provider+'@'+record['image'].split('@')[1]})
            clients={}
            def client(repo):clients[repo]=FakeClient();return clients[repo]
            publish.publish_selected(selections,client)
            for fixture,(repo,value) in zip(fixtures,clients.items()):
                self.assertEqual(value.manifests,fixture.roots)
                self.assertEqual(value.blobs,fixture.data)
            clients.clear()
            graph.blob_path(selections[1]['directory'],fixtures[1].layer_id).write_bytes(b'corrupted')
            with self.assertRaises(ValueError):publish.publish_selected(selections,client)
            self.assertEqual(clients,{})

    def test_all_destinations_before_auth_and_zero_selection_no_auth(self):
        def forbidden(*args):self.fail('authorization before full input validation')
        publish.publish_selected([],forbidden)
        with self.assertRaises(ValueError):publish.publish_selected([{'record':{'provider':'codex','image':'source@sha256:'+'a'*64},'directory':Path('/unused'),'image':'ghcr.io/foreign/repo@sha256:'+'a'*64}],forbidden)


class ActualHTTPPublicationTests(unittest.TestCase):
    def test_production_raw_client_with_owned_http_transport_keeps_exact_bytes(self):
        blobs={};manifests={};requests=[];unauthorized=[True]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,headers=None,body=b''):
                self.send_response(status)
                for key,value in (headers or {}).items():self.send_header(key,value)
                if 'Content-Length' not in (headers or {}):self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                if self.command!='HEAD':self.wfile.write(body)
            def do_GET(self):
                requests.append((self.command,self.path,self.headers.get('Authorization')))
                if self.path.startswith('/token?'):self.reply(200,body=json.dumps({'token':'SYNTHETIC-local-token','expires_in':300}).encode())
                else:self.reply(404)
            def do_HEAD(self):
                requests.append((self.command,self.path,self.headers.get('Authorization')))
                identity=self.path.rsplit('/',1)[-1];value=blobs.get(identity)
                if value is None:self.reply(404)
                else:self.reply(200,{'Content-Length':str(len(value)),'Docker-Content-Digest':identity})
            def do_POST(self):
                requests.append((self.command,self.path,self.headers.get('Authorization')))
                self.reply(202,{'Location':'https://ghcr.io'+self.path+'owned?_state=SYNTHETIC'})
            def do_PUT(self):
                requests.append((self.command,self.path,self.headers.get('Authorization')))
                size=int(self.headers['Content-Length'])
                if not 0<size<1048576:self.reply(400);return
                raw=self.rfile.read(size)
                if unauthorized[0]:
                    unauthorized[0]=False;self.reply(401);return
                identity=self.path.rsplit('digest=',1)[-1].replace('%3A',':') if '/uploads/' in self.path else self.path.rsplit('/',1)[-1]
                if graph.digest(raw)!=identity:self.reply(400);return
                (blobs if '/uploads/' in self.path else manifests)[identity]=raw
                self.reply(201,{'Docker-Content-Digest':identity})
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                fixture=Fixture(sources=('a',));directory=fixture.write(Path(temp).resolve()/'graph');record=fixture.records[0]
                def client(repository):return publish.GHCR(repository,'Fixture','NONCREDENTIAL-local-placeholder',
                    connection=lambda host,timeout:http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=timeout))
                publish.publish_selected([{'record':record,'directory':directory,'image':'ghcr.io/jayson-stangel/symphony-codex@'+record['image'].split('@')[1]}],client)
                self.assertEqual(blobs,fixture.data);self.assertEqual(manifests,fixture.roots)
                self.assertTrue(requests[0][1].startswith('/token?'))
                self.assertTrue(requests[0][2].startswith('Basic '))
                self.assertEqual(sum(path.startswith('/token?') for method,path,auth in requests),2)
                self.assertTrue(all(auth.startswith('Basic ') if path.startswith('/token?') else auth=='Bearer SYNTHETIC-local-token' for method,path,auth in requests))
        finally:server.shutdown();server.server_close();thread.join(timeout=5)


class Response:
    def __init__(self,status=200,value=None,headers=None):self.status=status;self.data=io.BytesIO(json.dumps(value).encode() if value is not None else b'');self.headers=headers or {}
    def read(self,size=-1):return self.data.read(size)


class AuthTests(unittest.TestCase):
    def client(self,responses):
        calls=[]
        class Connection:
            def __init__(self,host,**kwargs):self.host=host;self.response=responses.pop(0)
            def request(self,method,path,body=None,headers=None):calls.append((self.host,method,path,headers))
            def getresponse(self):return self.response
            def close(self):pass
        return publish.GHCR('jayson-stangel/symphony-codex','Fixture','NONCREDENTIAL-placeholder',connection=Connection),calls

    def test_upload_read_default_and_oversized_requests_are_capped(self):
        class Spy:
            def __init__(self):self.sizes=[]
            def read(self,size):self.sizes.append(size);return b'x'
        stream=Spy();reader=publish.Upload(stream,2,'sha256:'+'a'*64)
        reader.read();reader.read(1<<40)
        self.assertEqual(stream.sizes,[1<<20,1<<20])

    def test_basic_only_fixed_token_endpoint_bearer_only_fixed_repo(self):
        client,calls=self.client([Response(value={'token':'SYNTHETIC-bearer','expires_in':300}),Response(200,headers={'Content-Length':'0'})])
        client.request('HEAD','/blobs/sha256:'+'a'*64,maximum=0)
        self.assertEqual(calls[0][0:2],('ghcr.io','GET'))
        self.assertTrue(calls[0][2].startswith('/token?service=ghcr.io&scope=repository%3Ajayson-stangel%2Fsymphony-codex%3Apull%2Cpush'))
        self.assertTrue(calls[0][3]['Authorization'].startswith('Basic '))
        self.assertEqual(calls[1][3]['Authorization'],'Bearer SYNTHETIC-bearer')
        self.assertNotIn('NONCREDENTIAL-placeholder',str(calls))

    def test_401_refresh_bounded_and_redirect_never_followed(self):
        client,calls=self.client([Response(value={'token':'ONE'}),Response(401),Response(value={'token':'TWO'}),Response(200)])
        client.request('HEAD','/blobs/sha256:'+'a'*64,maximum=0)
        self.assertEqual(client.refreshes,2)
        client,calls=self.client([Response(value={'token':'ONE'}),Response(307,headers={'Location':'https://foreign.invalid/steal'})])
        with self.assertRaisesRegex(ValueError,'status 307'):client.request('HEAD','/blobs/sha256:'+'a'*64,maximum=0)
        self.assertEqual(len(calls),2)

    def test_bad_token_lifetime_issue_time_and_foreign_upload_refuse(self):
        for value in ({'token':True},{'token':'x','expires_in':True},{'token':'x','issued_at':'not a timestamp'}, {'token':'x','expires_in':1},{'token':'x','expires_in':3601},{'token':'x','issued_at':'2000-01-01T00:00:00Z'}):
            client,_=self.client([Response(value=value)])
            with self.assertRaises(ValueError):client.authorize()
        client,_=self.client([])
        for location in ('https://foreign.invalid/v2/jayson-stangel/symphony-codex/blobs/uploads/a',
                         'https://ghcr.io/v2/foreign/blobs/uploads/a','/v2/jayson-stangel/symphony-codex/blobs/uploads/../a',
                         '/v2/jayson-stangel/symphony-codex/blobs/uploads/a?foreign=1'):
            with self.assertRaises(ValueError):client.upload_location(location,'sha256:'+'a'*64)
        self.assertIn('digest=sha256%3A',client.upload_location('/v2/jayson-stangel/symphony-codex/blobs/uploads/a?_state=opaque','sha256:'+'a'*64))


class AnonymousRedirectTests(unittest.TestCase):
    def test_only_anonymous_blob_redirect_and_auth_removed(self):
        calls=[];sentinel=object()
        def opener(request,**kwargs):
            calls.append(request)
            if len(calls)==1:
                raise HTTPError(request.full_url,307,'fixture',{'Location':'https://pkg-containers.githubusercontent.com/ghcr1/blobs/sha256:abc?signed=SYNTHETIC'},io.BytesIO())
            return sentinel
        result=registry_closure.open_anonymous('https://ghcr.io/v2/jayson-stangel/symphony-codex/blobs/sha256:'+'a'*64,
            {'Authorization':'Bearer SYNTHETIC-anonymous','Range':'bytes=0-0'},'GET',opener)
        self.assertIs(result,sentinel)
        self.assertEqual(calls[1].headers,{'Range':'bytes=0-0'})
        self.assertNotIn('Authorization',calls[1].headers)

    def test_foreign_auth_redirect_and_metadata_redirect_refuse(self):
        for original,location in (
            ('https://ghcr.io/v2/repo/manifests/sha256:'+'a'*64,'https://pkg-containers.githubusercontent.com/blob'),
            ('https://ghcr.io/v2/repo/blobs/sha256:'+'a'*64,'https://foreign.invalid/blob'),
            ('https://ghcr.io/v2/repo/blobs/sha256:'+'a'*64,'https://user@pkg-containers.githubusercontent.com/blob'),
            ('https://ghcr.io/v2/repo/blobs/sha256:'+'a'*64,'https://pkg-containers.githubusercontent.com:443/blob'),
            ('https://ghcr.io/v2/repo/blobs/sha256:'+'a'*64,'http://pkg-containers.githubusercontent.com/blob'),
            ('https://ghcr.io/v2/repo/blobs/sha256:'+'a'*64,'https://pkg-containers.githubusercontent.com/%2e%2e/blob')):
            calls=[]
            def opener(request,**kwargs):
                calls.append(request);raise HTTPError(request.full_url,307,'fixture',{'Location':location},io.BytesIO())
            with self.assertRaises(ValueError):registry_closure.open_anonymous(original,{},'GET',opener)
            self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()
