"""TEMPORARY fixed-fixture feature proof; remove before the final production PR."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess

sys.dont_write_bytecode = True
from local_distribution import download_staging, staging_api
from staging_diagnostic import FIXTURE_DIGEST, FIXTURE_NAME, FIXTURE_SIZE, source_guard
from staging_ingress import TransportContract, stage_core, receive_core

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = TransportContract(ref='refs/heads/codex/local-staging-release-id',
    scope='private-staging-fixture-proof-only', artifact_prefix='local-signing-fixture-proof',
    producer_job='Read fixed fixture into opaque handoff', producer_key='fixture-ingress',
    consumer_key='fixture-read-consumer')


def require(condition):
    if not condition:
        raise ValueError('Fixed public fixture handoff refused')


def fixture_download(release_id, asset_id, digest, revision):
    require(digest == FIXTURE_DIGEST)
    def fetch(path, binary=False):
        value = staging_api(path, binary=binary)
        if not binary and path == 'releases/assets/' + str(asset_id):
            require(isinstance(value, dict) and value.get('name') == FIXTURE_NAME
                    and type(value.get('size')) is int and value['size'] == FIXTURE_SIZE
                    and value.get('digest') == FIXTURE_DIGEST)
        return value
    raw, origin = download_staging(release_id, asset_id, digest, revision, fetch, 4096)
    require(len(raw) == FIXTURE_SIZE and 'sha256:' + hashlib.sha256(raw).hexdigest() == FIXTURE_DIGEST)
    return raw, origin


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('produce','receive'))
    parser.add_argument('release_id');parser.add_argument('asset_id');parser.add_argument('digest')
    parser.add_argument('destination',type=Path)
    parser.add_argument('--approved-revision',required=True)
    parser.add_argument('--fixture-revision',required=True)
    parser.add_argument('--diagnostic-only',choices=('true','false'),required=True)
    parser.add_argument('--transport-probe-only',choices=('true','false'),required=True)
    parser.add_argument('--artifact-id');parser.add_argument('--artifact-name');parser.add_argument('--artifact-digest')
    args=parser.parse_args()
    stage='source-binding'
    try:
        require(args.diagnostic_only=='true' and args.digest==FIXTURE_DIGEST)
        source_guard(ROOT,os.environ,args.approved_revision,args.fixture_revision,args.transport_probe_only)
        revision=os.environ['GITHUB_SHA']
        require(args.fixture_revision==revision and os.environ['GITHUB_REF']==CONTRACT.ref)
        if args.mode=='produce':
            stage='opaque-stage'
            stage_core(args.release_id,args.asset_id,args.digest,revision,args.destination,
                       contract=CONTRACT,download=fixture_download)
        else:
            stage='read-only-artifact-consumer'
            result=receive_core(args.artifact_id,args.artifact_name,args.artifact_digest,
                args.release_id,args.asset_id,args.digest,revision,args.destination,contract=CONTRACT)
            raw=result['payload'].read_bytes()
            require(len(raw)==FIXTURE_SIZE and 'sha256:'+hashlib.sha256(raw).hexdigest()==FIXTURE_DIGEST
                    and result['origin']['name']==FIXTURE_NAME)
            proof={'schema':1,'scope':'fixed-public-fixture-handoff-only-not-native-or-production',
                   'source_revision':revision,'artifact':result['artifact'],
                   'original_origin':result['origin'],'payload_sha256':hashlib.sha256(raw).hexdigest(),
                   'payload_size':len(raw),'status':'success'}
            (args.destination/'fixture-proof.json').write_text(json.dumps(proof,sort_keys=True,indent=2)+'\n')
        print('Fixed public fixture handoff stage passed:',stage)
    except (ValueError,KeyError,OSError,TypeError,subprocess.SubprocessError):
        # Never retain raw API, header, account, token or exception text.
        parser.exit(1,'Fixed public fixture handoff refused at '+stage+'\n')


if __name__=='__main__':main()
