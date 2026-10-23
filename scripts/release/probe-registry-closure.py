#!/usr/bin/env python3
"""Tiny synthetic OCI graph in a new owned registry; separate from qualified runners."""
import argparse
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import tarfile
import threading
from urllib.error import HTTPError
from urllib.parse import urljoin,urlsplit,urlencode
from urllib.request import Request,urlopen

from artifacts import require
import registry_closure as closure
import test_registry
BASE='http://localhost:5000'
REPOSITORY='distribution/synthetic-closure'


def call(path,data=None,method='GET',content_type='application/octet-stream'):
    url=urljoin(BASE,path);parts=urlsplit(url)
    require(parts.scheme=='http' and parts.netloc=='localhost:5000' and not parts.fragment and
            parts.path.startswith('/v2/'+REPOSITORY+'/'),'synthetic registry operation outside owned namespace')
    with urlopen(Request(url,data=data,method=method,headers={'Content-Type':content_type}),timeout=30) as response:
        body=response.read(4*1024*1024+1)
        require(len(body)<=4*1024*1024,'synthetic registry response exceeds bound')
        return response.status,body,response.headers


def blob(data):
    digest='sha256:'+hashlib.sha256(data).hexdigest()
    status,_,headers=call('/v2/'+REPOSITORY+'/blobs/uploads/',b'','POST')
    require(status==202,'synthetic upload session was not acquired')
    location=urljoin(BASE,headers['Location'])
    separator='&' if urlsplit(location).query else '?'
    status,_,headers=call(location+separator+urlencode({'digest':digest}),data,'PUT')
    require(status==201 and headers.get('Docker-Content-Digest')==digest,'synthetic blob receipt differs')
    return {'digest':digest,'size':len(data)}


def permission_refuses(image):
    class DenyBlob(BaseHTTPRequestHandler):
        def handle_request(self):
            if '/blobs/' in self.path:
                self.send_error(403,'Synthetic anonymous permission denial');return
            require(self.path.startswith('/v2/'+REPOSITORY+'/'),'proxy outside synthetic namespace')
            data,headers=closure.request(BASE+self.path,method=self.command)
            self.send_response(200)
            self.send_header('Docker-Content-Digest',headers['Docker-Content-Digest'])
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            if self.command!='HEAD':self.wfile.write(data)
        do_GET=handle_request
        do_HEAD=handle_request
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),DenyBlob)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        def fetch(url,headers=None,**options):
            return closure.request('http://127.0.0.1:'+str(server.server_port)+urlsplit(url).path,headers,**options)
        try:closure.registry_available(image,fetch=fetch)
        except HTTPError as error:return error.code==403
        return False
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def probe(output):
    require(os.environ.get('GITHUB_EVENT_NAME')=='workflow_dispatch' and
            os.environ.get('GITHUB_REPOSITORY')=='Jayson-Stangel/symphony',
            'synthetic registry proof requires an explicit manual fork diagnostic')
    output.mkdir(parents=True,exist_ok=True)
    state=output/'registry-closure-state.txt'
    result={'source_revision':os.environ['GITHUB_SHA'],'qualified_native_runner':False,
            'subject':'tiny synthetic OCI graph; actual runner alias integration is separate',
            'image_build_executed':False,'runner_export_executed':False,'large_artifact_upload_executed':False,
            'passed':False}
    try:
        test_registry.start(state)
        owned=json.loads(state.read_text());identifier=owned['container_id']
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w') as archive:
            payload=('synthetic-closure-'+secrets.token_hex(16)).encode()
            item=tarfile.TarInfo('synthetic-fixture');item.size=len(payload);archive.addfile(item,io.BytesIO(payload))
        layer=blob(gzip.compress(data.getvalue(),mtime=0))
        layer['mediaType']='application/vnd.oci.image.layer.v1.tar+gzip'
        config=blob(json.dumps({'architecture':'amd64','os':'linux','config':{},
            'rootfs':{'type':'layers','diff_ids':['sha256:'+hashlib.sha256(data.getvalue()).hexdigest()]}}).encode())
        config['mediaType']='application/vnd.oci.image.config.v1+json'
        manifest=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json',
                             'config':config,'layers':[layer]},separators=(',',':')).encode()
        digest='sha256:'+hashlib.sha256(manifest).hexdigest()
        status,_,headers=call('/v2/'+REPOSITORY+'/manifests/fixture',manifest,'PUT','application/vnd.oci.image.manifest.v1+json')
        require(status==201 and headers.get('Docker-Content-Digest')==digest,'synthetic manifest digest differs')
        image='localhost:5000/'+REPOSITORY+'@'+digest
        require(closure.registry_available(image) and closure.registry_available(image,full=True),'actual synthetic blob closure failed')
        require(permission_refuses(image),'anonymous HTTP permission denial was accepted')
        foreign='localhost:5000/'+REPOSITORY+'@sha256:'+secrets.token_hex(32)
        require(not closure.registry_available(foreign),'foreign immutable digest was accepted')
        observed=json.loads(test_registry.docker('inspect',identifier))[0]
        require(observed['Id']==identifier and observed['Config']['Labels'][test_registry.LABEL]==owned['owner'],
                'synthetic registry ownership changed before missing-blob fixture')
        hex_digest=layer['digest'].split(':')[1]
        storage='/var/lib/registry/docker/registry/v2/blobs/sha256/'+hex_digest[:2]+'/'+hex_digest+'/data'
        test_registry.docker('exec',identifier,'rm','--',storage)
        require(closure.manifest_available(image),'root manifest disappeared in missing-blob fixture')
        try:closure.registry_available(image)
        except HTTPError as error:require(error.code==404,'unexpected missing-blob status')
        else:raise ValueError('manifest-only proof accepted a missing real registry blob')
        result.update(image=image,config_digest=config['digest'],layer_digest=layer['digest'],
                      synthetic_blob_bytes=config['size']+layer['size'],
                      actual_registry_missing_blob_refused=True,actual_http_permission_refused=True,
                      foreign_digest_refused=True,complete_anonymous_blob_checksum_verified=True,passed=True)
    finally:
        try:
            test_registry.cleanup(state)
            result['cleanup']='verified-owned-resource-absent'
        except BaseException:
            result['passed']=False;result['cleanup']='failed-or-uncertain'
            raise
        finally:(output/'registry-closure-evidence.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    probe(parser.parse_args().output)
