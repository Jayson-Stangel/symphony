"""Bounded anonymous immutable registry closure; never invokes Docker or executes images."""
import hashlib
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

from artifacts import DIGEST_IMAGE,require
MAX_DOCUMENT=4*1024*1024
MAX_LAYER=4*1024**3
MAX_TOTAL=16*1024**3
ACCEPT=', '.join(('application/vnd.oci.image.index.v1+json','application/vnd.oci.image.manifest.v1+json',
                 'application/vnd.docker.distribution.manifest.list.v2+json','application/vnd.docker.distribution.manifest.v2+json'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None


def open_anonymous(url,headers,method,opener=None):
    """Only anonymous GHCR blob storage may redirect, without any auth header."""
    opener=opener or build_opener(NoRedirect()).open
    original=urlsplit(url); current=url
    selected=dict(headers or {})
    blob=(original.scheme=='https' and original.netloc=='ghcr.io' and
          re.fullmatch(r'/v2/[a-z0-9._/-]+/blobs/sha256:[a-f0-9]{64}',original.path) is not None and not original.query)
    for attempt in range(3):
        try:return opener(Request(current,headers=selected,method=method),timeout=60)
        except HTTPError as error:
            if error.code not in (301,302,303,307,308):
                if current!=url:raise ValueError('anonymous registry storage request failed') from None
                raise
            location=error.headers.get('Location','')
            error.close()
            require(blob and attempt<2 and method in ('GET','HEAD') and type(location) is str and len(location)<=16384,
                    'anonymous registry redirect refused')
            target=urlsplit(location)
            require(target.scheme=='https' and target.netloc=='pkg-containers.githubusercontent.com' and
                    not target.username and not target.password and not target.fragment and
                    target.path.startswith('/') and '/..' not in target.path and '%' not in target.path,
                    'anonymous registry storage destination refused')
            # Never forward even an anonymous pull Bearer token to the CDN.
            selected={key:value for key,value in selected.items() if key.lower()=='range'}
            current=location
    raise ValueError('anonymous registry redirect bound exceeded')


def request(url,headers=None,method='GET',blob_digest=None,blob_size=None,range_size=None):
    deadline=time.monotonic()+900
    if range_size is not None:headers=dict(headers or {},Range='bytes=0-0')
    with open_anonymous(url,headers or {},method) as response:
        if range_size is not None:
            require(response.status==206 and response.headers.get('Content-Range')=='bytes 0-0/'+str(range_size),
                    'registry did not prove bounded anonymous blob read')
            data=response.read(2)
            require(len(data)==1,'registry blob range response differs')
            return data,response.headers
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
            # HEAD can be served from descriptor caches. Force an actual bounded
            # storage read too, without downloading an unchanged layer again.
            sample,sample_headers=read('blobs/'+digest,range_size=size)
            require(len(sample)==1 and sample_headers.get('Content-Range')=='bytes 0-0/'+str(size),
                    'anonymous layer bytes unavailable despite metadata')
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
