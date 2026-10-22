"""Bounded anonymous immutable registry closure; never invokes Docker or executes images."""
import hashlib
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from artifacts import DIGEST_IMAGE,require
MAX_DOCUMENT=4*1024*1024
MAX_LAYER=4*1024**3
MAX_TOTAL=16*1024**3
ACCEPT=', '.join(('application/vnd.oci.image.index.v1+json','application/vnd.oci.image.manifest.v1+json',
                 'application/vnd.docker.distribution.manifest.list.v2+json','application/vnd.docker.distribution.manifest.v2+json'))


def request(url,headers=None,method='GET',blob_digest=None,blob_size=None):
    deadline=time.monotonic()+900
    with urlopen(Request(url,headers=headers or {},method=method),timeout=60) as response:
        if blob_digest is not None:
            count=0;digest=hashlib.sha256()
            while True:
                require(time.monotonic()<deadline,'anonymous layer read deadline exceeded')
                data=response.read(1024*1024)
                if not data:break
                count+=len(data)
                require(count<=blob_size<=MAX_LAYER,'anonymous layer exceeds declared bound')
                digest.update(data)
            require(count==blob_size and 'sha256:'+digest.hexdigest()==blob_digest,'anonymous layer checksum differs')
            return b'',response.headers
        data=response.read(MAX_DOCUMENT+1)
        require(len(data)<=MAX_DOCUMENT,'registry document exceeds bound')
        return data,response.headers


def reader(image,fetch):
    require(DIGEST_IMAGE.fullmatch(image or ''),'invalid distribution reference')
    host,repository=image.split('/',1);repository,digest=repository.split('@',1)
    require(host in ('ghcr.io','localhost:5000'),'unsupported distribution registry')
    base=('http' if host=='localhost:5000' else 'https')+'://'+host+'/v2/'+repository+'/'
    headers={'Accept':ACCEPT}
    def read(resource,**options):
        try:return fetch(base+resource,headers,**options)
        except HTTPError as error:
            if error.code!=401 or host!='ghcr.io':raise
            # Never follow server-supplied auth URLs or send GitHub credentials.
            token_data,_=fetch('https://ghcr.io/token?'+urlencode({'service':'ghcr.io','scope':'repository:'+repository+':pull'}))
            token=json.loads(token_data).get('token')
            require(isinstance(token,str) and token and '\n' not in token,'invalid anonymous registry token')
            headers['Authorization']='Bearer '+token
            return fetch(base+resource,headers,**options)
    return read,digest


def missing(error):
    if error.code!=404:raise error
    data=error.read(65537)
    require(len(data)<=65536,'registry error exceeds bound')
    errors=json.loads(data).get('errors',[])
    require(errors and all(item.get('code')=='MANIFEST_UNKNOWN' for item in errors),'registry did not prove missing manifest')
    return False


def manifest_bytes(read,digest):
    data,headers=read('manifests/'+digest)
    require(headers.get('Docker-Content-Digest')==digest and 'sha256:'+hashlib.sha256(data).hexdigest()==digest,
            'distribution registry changes original OCI manifest digest')
    return data


def manifest_available(image,fetch=request):
    read,digest=reader(image,fetch)
    try:manifest_bytes(read,digest)
    except HTTPError as error:return missing(error)
    return True


def registry_available(image,fetch=request,full=False):
    """Hash manifests/configs; prove all layer HEADs, or stream/hash layers for new transfers."""
    read,root=reader(image,fetch)
    try:initial=manifest_bytes(read,root)
    except HTTPError as error:return missing(error)
    seen=set();blobs={};manifests=0;total=0;deadline=time.monotonic()+1800
    def descriptor(item):
        require(isinstance(item,dict) and re.fullmatch(r'sha256:[a-f0-9]{64}',item.get('digest','')) and
                type(item.get('size')) is int and 0<item['size']<=MAX_LAYER,'invalid anonymous closure descriptor')
        return item['digest'],item['size']
    def blob(item,config=False):
        nonlocal total
        require(time.monotonic()<deadline,'anonymous blob closure deadline exceeded')
        digest,size=descriptor(item)
        if digest in blobs:
            require(blobs[digest]==size,'conflicting blob closure size');return
        blobs[digest]=size;total+=size
        require(len(blobs)<=1024 and total<=MAX_TOTAL,'anonymous image blob closure exceeds bound')
        if config:
            require(size<=MAX_DOCUMENT,'anonymous config exceeds bound')
            data,headers=read('blobs/'+digest)
            require(len(data)==size and 'sha256:'+hashlib.sha256(data).hexdigest()==digest,'anonymous config checksum differs')
            require(isinstance(json.loads(data),dict),'anonymous config is not an object')
        elif full:
            _,headers=read('blobs/'+digest,blob_digest=digest,blob_size=size)
        else:
            _,headers=read('blobs/'+digest,method='HEAD')
            require(headers.get('Docker-Content-Digest')==digest and headers.get('Content-Length')==str(size),
                    'anonymous layer unavailable or metadata differs')
    def walk(digest,data,depth):
        nonlocal manifests
        require(time.monotonic()<deadline,'anonymous manifest closure deadline exceeded')
        if digest in seen:return
        seen.add(digest);manifests+=1
        require(depth<=8 and manifests<=32,'anonymous manifest closure exceeds bound')
        value=json.loads(data)
        require(isinstance(value,dict) and type(value.get('schemaVersion')) is int and value['schemaVersion']==2,
                'unknown anonymous image manifest schema')
        if 'manifests' in value:
            children=value['manifests']
            require(isinstance(children,list) and 0<len(children)<=32 and 'layers' not in value,'invalid anonymous index closure')
            for child in children:
                child_digest,size=descriptor(child)
                require(size<=MAX_DOCUMENT,'child manifest exceeds bound')
                body=manifest_bytes(read,child_digest)
                require(len(body)==size,'child manifest size differs')
                walk(child_digest,body,depth+1)
        else:
            layers=value.get('layers')
            require(isinstance(layers,list) and 0<len(layers)<=1024,'invalid anonymous layer closure')
            blob(value.get('config'),config=True)
            for layer in layers:blob(layer)
    walk(root,initial,0)
    require(blobs,'empty anonymous image closure')
    return True
