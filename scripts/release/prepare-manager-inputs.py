#!/usr/bin/env python3
"""Join exact ordinary-event decisions; retrieve original payloads only locally."""
import argparse
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
from types import SimpleNamespace

import artifacts
import manager_version
from runner_inputs import identity
from reuse_payload import retrieve


def _prepare(root, plan, built, output, payloads, verify):
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    artifacts.require(plan.get('planning_complete') is True and plan.get('manager_source_revision') == revision,
                      'ordinary-event plan belongs to another manager source')
    if built is not None:
        artifacts.validate(built, require_bundle=False)
        artifacts.require(built['schema_version'] == 2 and built['source_revision'] == revision,
                          'changed-component metadata belongs to another source/schema')
    records = []
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            decision = plan['components'][provider][architecture]
            artifacts.require(decision['action'] in ('build', 'reuse'), 'unknown component decision')
            if built is None:
                artifacts.require(decision['action'] == 'reuse', 'changed component requires actual native production evidence')
                record = decision['record']
            else:
                record = dict(built['runners'][provider][architecture], provider=provider, architecture=architecture)
            snapshot = identity(root, provider, architecture)
            artifacts.require(snapshot['input_identity'] == decision['input_identity'] == record['input_identity'],
                              'actual component input closure differs from current source')
            origin = verify(record)
            if built is None:
                artifacts.require(origin == decision['origin'], 'original component origin changed after planning')
            records.append(record)
            if payloads is not None and architecture == 'x86_64':
                destination = payloads / provider
                archive = retrieve(record, origin, destination, verify=verify)
                # The original archive bytes and tags remain untouched. Only
                # local filesystem layout is adapted for the existing consumer.
                archive.rename(payloads / archive.name)
                checksum = archive.name.removesuffix('.docker.tar.gz') + '.sha256'
                (destination / checksum).rename(payloads / checksum)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix='manager-records-') as directory:
        files = []
        for record in records:
            path = Path(directory) / f"{record['provider']}-{record['architecture']}.json"
            path.write_text(json.dumps(record))
            files.append(str(path))
        artifacts.assemble(SimpleNamespace(records=files, schema_version=2, platform_bundles=True,
                           version=manager_version.read(root), revision=revision, channel='candidate',
                           bundle=None, output=str(output / 'release.json')), verifier=verify)
    return records


def prepare(root, plan, built, output, payloads, verify):
    artifacts.require(not output.exists() and (payloads is None or not payloads.exists()),
                      'refusing existing manager preparation destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    if payloads is not None:
        payloads.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manager-inputs-', dir=output.parent) as temporary:
        staging = Path(temporary)
        staged_output = staging / 'metadata'
        staged_payloads = staging / 'payloads' if payloads is not None else None
        records = _prepare(root, plan, built, staged_output, staged_payloads, verify)
        published = None
        try:
            if payloads is not None:
                staged_payloads.rename(payloads)
                published = payloads.stat()
            staged_output.rename(output)
        except BaseException:
            if published is not None:
                observed = payloads.lstat()
                if (observed.st_dev, observed.st_ino) == (published.st_dev, published.st_ino):
                    shutil.rmtree(payloads)
            raise
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--built-metadata', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--payloads', type=Path)
    args = parser.parse_args()
    # This helper is owned by the provenance producer. It authorizes only this
    # exact active native run, or verifies protected-history original evidence.
    from component_provenance import verify_current_origin
    prepare(args.root, json.loads(args.plan.read_text()),
            json.loads(args.built_metadata.read_text()) if args.built_metadata else None,
            args.output, args.payloads, verify_current_origin)


if __name__ == '__main__':
    main()
