#!/usr/bin/env python3
"""Explicit dev-only service fixture; never production or native admission."""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import time

import graph_publication as publication
import qualified_graph as graph
import staging_ingress as ingress
from component_provenance import api
from local_distribution import download_staging
from registry_closure import manifest_available, registry_available

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'scripts/release/fixtures/transport-fixture-v1.json'
PAYLOAD_DIGEST = 'sha256:482d1958552ae15e11c2de25afe9ac2401b052e6b21ccac73650db58665affe1'
MANIFEST = 'sha256:89592386ae9565b9fd3428236f9a0d2cabce69e281b02025d0c030dc6096e9a1'
CONFIG = 'sha256:ce3c5c6941afa7baecf582904b39b09e9411f31a647f5549cb97e4d7eb7407f1'
LAYER = 'sha256:85298463ee2565e877b68c4c275a2f5566740d6f1db0754d775f588074f00855'
REPOSITORY = 'jayson-stangel/symphony-codex'
IMAGE = 'ghcr.io/' + REPOSITORY + '@' + MANIFEST
READ_JOB = 'Verify fixed transport fixture with read-only token'
CONTRACT = ingress.TransportContract('refs/heads/dev', 'private-staging-fixture-proof-only',
    'transport-fixture', 'Read fixed transport fixture into opaque handoff',
    'transport-source', 'transport-reader')
JOBS = {'stage': CONTRACT.producer_key, 'receive': CONTRACT.consumer_key, 'registry': 'transport-registry'}


def require(value, message):
    if not value:
        raise ValueError('transport fixture: ' + message)


def fixture(path=DATA):
    value = graph.strict(graph.read(path, 16384))
    require(set(value) == {'schema', 'scope', 'staging_zip', 'record', 'graph_files'} and
            type(value['schema']) is int and value['schema'] == 1 and
            value['scope'] == 'synthetic-transport-data-only-never-runnable-or-qualified', 'fixture schema differs')
    payload = base64.b64decode(value['staging_zip'], validate=True)
    require(len(payload) == 165 and graph.digest(payload) == PAYLOAD_DIGEST, 'fixture payload differs')
    files = {name: base64.b64decode(raw, validate=True) for name, raw in value['graph_files'].items()}
    expected = {'index.json'} | {'blobs/sha256/' + digest[7:] for digest in (MANIFEST, CONFIG, LAYER)}
    require(set(files) == expected and sum(len(raw) for name, raw in files.items() if name != 'index.json') == 917,
            'fixture graph inventory differs')
    for digest in (MANIFEST, CONFIG, LAYER):
        require(graph.digest(files['blobs/sha256/' + digest[7:]]) == digest, 'fixture graph bytes differ')
    require(value['record'] == {'image': 'localhost:5000/symphony-codex@' + MANIFEST,
        'image_config': CONFIG, 'archive_tag': 'localhost:5000/symphony-codex:candidate',
        'provider': 'codex', 'architecture': 'arm64', 'source_revision': '7' * 40,
        'input_identity': '7' * 64, 'protocol': 1}, 'fixture record differs')
    return payload, value['record'], files


def bind(mode, env, fetch, invoke):
    revision = env.get('GITHUB_SHA', '')
    require(env.get('TRANSPORT_FIXTURE_ONLY') == 'true' and env.get('APPROVED_REVISION') == revision
            and env.get('GITHUB_ACTOR') == 'Jayson-Stangel' and env.get('GITHUB_RUN_ATTEMPT') == '1',
            'explicit owner/source/first-attempt fixture approval required')
    bound = ingress.source_context(revision, JOBS[mode], env, invoke, contract=CONTRACT)
    ingress.current_run(bound, fetch)
    require(env.get('STAGING_DIGEST') == PAYLOAD_DIGEST, 'fixture staging digest differs')
    ingress.positive(env.get('STAGING_RELEASE')); ingress.positive(env.get('STAGING_ASSET'))
    return bound


def completed_reader(bound, fetch):
    value = fetch(f"actions/runs/{bound['run_id']}/attempts/{bound['attempt']}/jobs?per_page=100")
    require(type(value) is dict and type(value.get('jobs')) is list and
            type(value.get('total_count')) is int and 0 <= value['total_count'] <= 100
            and len(value['jobs']) == value['total_count'] and all(type(j) is dict for j in value['jobs']),
            'incomplete fixture job inventory')
    matches = [job for job in value['jobs'] if job.get('name') == READ_JOB]
    require(len(matches) == 1 and matches[0].get('status') == 'completed' and
            matches[0].get('conclusion') == 'success', 'fixture reader did not uniquely succeed')
    return ingress.positive(matches[0].get('id'))


class ConfirmedCheckpoint(Exception):
    """Only the one preapproved, positively confirmed blob boundary may resume."""


class Journal:
    def __init__(self, path, origin):
        self.path, self.rows = path, []
        require(not path.exists(), 'fixture evidence already exists')
        self.add({'event': 'start', **origin})

    def add(self, row):
        require(len(self.rows) < 64, 'fixture evidence budget exceeded')
        self.rows.append(row)
        # All callers pass closed structural fields, never headers/URLs/tokens/errors.
        raw = (json.dumps(self.rows, sort_keys=True, indent=2) + '\n').encode()
        require(len(raw) <= 32768, 'fixture evidence size exceeded')
        with self.path.open('wb') as output:
            self.path.chmod(0o600)
            output.write(raw)


def registry(destination, bound, env, fetch, connection, probe, closure):
    reader = completed_reader(bound, fetch)
    payload, record, files = fixture()
    require(not destination.exists(), 'fixture destination already exists')
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    journal = Journal(destination / 'registry-proof.json', {'scope': CONTRACT.scope,
        'source_revision': bound['revision'], 'run_id': bound['run_id'], 'attempt': bound['attempt'],
        'reader_job_id': reader, 'staging_release_id': ingress.positive(env['STAGING_RELEASE']),
        'staging_asset_id': ingress.positive(env['STAGING_ASSET']), 'staging_digest': PAYLOAD_DIGEST,
        'ingress_artifact_id': ingress.positive(env.get('INGRESS_ARTIFACT_ID')),
        'ingress_artifact_digest': env.get('INGRESS_ARTIFACT_DIGEST'), 'image': IMAGE,
        'graph_bytes': 917, 'staging_bytes': len(payload)})
    try:
        folder = destination / 'graph'; folder.mkdir(mode=0o700)
        for name, raw in files.items():
            path = folder / name; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open('xb') as output:
                path.chmod(0o600); output.write(raw)
        verified = graph.validate_graphs(folder, [record], exact=True)[record['image']]
        require(not probe(IMAGE), 'fixture manifest already present; stop without a new fixture')

        class Observed(publication.GHCR):
            def __init__(self):
                super().__init__(REPOSITORY, env['GITHUB_ACTOR'], env.get('GH_TOKEN'), connection=connection)
                self.deadline = min(self.deadline, time.monotonic() + 180)
                self.checkpoint, self.confirmed = True, None

            def upload_location(self, location, digest):
                target = super().upload_location(location, digest)
                journal.add({'event': 'accepted-upload-target',
                    'location_sha256': hashlib.sha256(location.encode()).hexdigest(),
                    'endpoint': 'singular' if '/blobs/upload/' in location else 'plural'})
                return target

            def request(self, method, suffix, body=None, **kwargs):
                journal.add({'event': 'request', 'method': method, 'kind': suffix.split('/')[1]})
                result = super().request(method, suffix, body=body, **kwargs)
                status, headers, _ = result
                journal.add({'event': 'response', 'status': status})
                if self.checkpoint and method == 'PUT' and suffix.startswith('/blobs/'):
                    require(isinstance(body, publication.Upload), 'unexpected fixture upload body')
                    body.verify()
                    require(status == 201 and headers.get('Docker-Content-Digest') == body.identity,
                            'fixture commit is not positively confirmed')
                    self.confirmed = (body.identity, body.size)
                    self.checkpoint = False
                    journal.add({'event': 'confirmed-interruption', 'digest': body.identity, 'size': body.size})
                    raise ConfirmedCheckpoint()
                return result

        client = Observed()
        # A prior fixture's blobs cannot silently eliminate the upload-session proof.
        missing = []
        for descriptor in verified['descriptors']:
            status, _, _ = client.request('HEAD', '/blobs/' + descriptor['digest'], expected=(200, 404), maximum=0)
            if status == 404:missing.append(descriptor['digest'])
        require(missing, 'all fixture blobs already present; stop before manifest mutation')
        selected = [{'directory': folder, 'record': record, 'image': IMAGE}]
        try:
            publication.publish_selected(selected, lambda repository: client)
        except ConfirmedCheckpoint:
            identity, size = client.confirmed
            _, headers, _ = client.request('HEAD', '/blobs/' + identity, maximum=0)
            require(headers.get('Docker-Content-Digest') == identity and
                    headers.get('Content-Length') == str(size), 'confirmed fixture checkpoint changed')
            journal.add({'event': 'explicit-continuation', 'digest': identity})
            publication.publish_selected(selected, lambda repository: client)
        else:
            raise ValueError('transport fixture: confirmed interruption was not exercised')
        require(closure(IMAGE, full=True), 'anonymous full fixture closure failed')
        journal.add({'event': 'success', 'image': IMAGE, 'graph_bytes': 917,
                     'continuations': 1, 'token_refreshes': client.refreshes,
                     'scope': 'dev fixture role/protocol only; production and native admission separate'})
        return {'scope': CONTRACT.scope, 'image': IMAGE, 'result': 'success', 'continuations': 1}
    except Exception as error:
        journal.add({'event': 'failure', 'category': type(error).__name__,
                     'result': 'stop; preserve confirmed IDs and unknown effects; no automatic replay'})
        raise


def execute(mode, destination, *, env=None, fetch=api, download=download_staging, invoke=subprocess.run,
            connection=http.client.HTTPSConnection, probe=manifest_available, closure=registry_available):
    env = os.environ if env is None else env
    bound = bind(mode, env, fetch, invoke)
    payload, _, _ = fixture()
    args = (env['STAGING_RELEASE'], env['STAGING_ASSET'], PAYLOAD_DIGEST, bound['revision'], destination)
    if mode == 'stage':
        def exact_download(*selection):
            raw, origin = download(*selection, max_bytes=165)
            require(raw == payload and origin['name'] == 'jay772-opaque-fixture.zip', 'original fixture bytes/name differ')
            return raw, origin
        result = ingress.stage_core(*args, contract=CONTRACT, download=exact_download,
                                    fetch=fetch, context=env, invoke=invoke)
        return {'scope': CONTRACT.scope, 'artifact_name': result['artifact_name'],
                'payload_sha256': ingress.digest(payload), 'payload_size': len(payload), 'origin': result['origin']}
    require(env.get('INGRESS_ARTIFACT_NAME') == ingress.expected_artifact_name(bound['revision'],
            bound['run_id'], bound['attempt'], CONTRACT.artifact_prefix)
            and graph.HEX.fullmatch(env.get('INGRESS_ARTIFACT_DIGEST', '')) is not None,
            'fixture immutable artifact identity differs')
    ingress.positive(env.get('INGRESS_ARTIFACT_ID'))
    if mode == 'receive':
        # Bound the actual immutable transport download before core parsing.
        def bounded_fetch(path, binary=False):
            value = fetch(path, binary=binary)
            if path == 'actions/artifacts/' + str(env['INGRESS_ARTIFACT_ID']):
                require(type(value.get('size_in_bytes')) is int and value['size_in_bytes'] <= 65536,
                        'fixture artifact metadata exceeds bound')
            if binary:require(type(value) is bytes and len(value) <= 65536, 'fixture artifact exceeds bound')
            return value
        result = ingress.receive_core(env['INGRESS_ARTIFACT_ID'], env['INGRESS_ARTIFACT_NAME'],
            env['INGRESS_ARTIFACT_DIGEST'], *args, contract=CONTRACT,
            fetch=bounded_fetch, context=env, invoke=invoke)
        require(result['payload'].read_bytes() == payload, 'received fixture bytes differ')
        return {'scope': CONTRACT.scope, 'payload_size': len(payload), 'payload_sha256': ingress.digest(payload),
                'origin': result['origin'], 'artifact': result['artifact']}
    return registry(destination, bound, env, fetch, connection, probe, closure)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=tuple(JOBS))
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(execute(args.mode, args.destination), sort_keys=True))
    except Exception:
        parser.exit(1, 'Fixture refused; preserve known IDs and stop without replay.\n')


if __name__ == '__main__':main()
