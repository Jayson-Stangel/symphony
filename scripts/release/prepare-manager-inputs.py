#!/usr/bin/env python3
"""Join exact ordinary-event decisions; retrieve original payloads only locally."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
from types import SimpleNamespace

import artifacts
import manager_version
from runner_inputs import identity
from reuse_payload import retrieve


def _prepare(root, plan, built, output, payloads, verify, distribution_alias=False):
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    artifacts.require(plan.get('planning_complete') is True and plan.get('manager_source_revision') == revision,
                      'ordinary-event plan belongs to another manager source')
    if plan.get('release_mode')=='manager-envelope-v1' and built is not None:
        artifacts.require(built.get('manager_envelope',{}).get('manager_source_revision')==revision, 'envelope manager differs')
        # Core records are selected from the exact source-bound plan below,
        # never from the stripped stock projection.
        built=None
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
            from reuse_payload import validate_decision_inputs
            validate_decision_inputs(record,decision,revision,provider,architecture)
            snapshot = identity(root, provider, architecture)
            artifacts.require(snapshot['input_identity'] == decision['input_identity'] and record['input_identity']==decision.get('selected_input_identity',decision['input_identity']),
                              'actual component input closure differs from current source')
            if decision.get('selection_mode')=='finite-component-v1':
                from finite_transition import evaluate_component
                artifacts.require(evaluate_component(root,record,provider,architecture)==decision['input_transition'],
                                  'actual manager input differs from finite component selection')
            origin = verify(record)
            if decision['action'] == 'reuse':
                artifacts.require(record == decision['record'], 'selected original component changed after planning')
                artifacts.require(origin == decision['origin'], 'original component origin changed after planning')
            else:
                artifacts.require(record['source_revision'] == revision, 'planned build requires actual current-source component')
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
        if plan.get('release_mode')=='manager-envelope-v1':
            from manager_envelope import make_core,write_release
            grouped={};origins={}
            for record in records:
                p,a=record['provider'],record['architecture'];grouped.setdefault(p,{})[a]=record
                origins.setdefault(p,{})[a]=verify(record)
            write_release(make_core(root,grouped,origins,plan['input_transition']),output)
        else:
            artifacts.assemble(SimpleNamespace(records=files, schema_version=2, platform_bundles=True,
                           version=manager_version.read(root), revision=revision, channel='candidate',
                           component_selections=__import__('finite_transition').selections_from_plan(plan),
                           bundle=None, output=str(output / 'release.json')), verifier=verify)
    if distribution_alias:
        artifacts.require(plan.get('release_mode')!='manager-envelope-v1','envelope aliases must be selected before core binding')
        path = output / 'release.json'
        manifest = json.loads(path.read_text())
        manifest['distribution'] = {provider: {architecture:
            'localhost:5000/symphony-distribution-' + provider + '@' + manifest['runners'][provider][architecture]['image'].split('@', 1)[1]
            for architecture in artifacts.ARCHITECTURES} for provider in artifacts.PROVIDERS}
        artifacts.validate(manifest, require_bundle=False)
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n')
    return records


def prepare(root, plan, built, output, payloads, verify, distribution_alias=False):
    artifacts.require(not output.exists() and (payloads is None or not payloads.exists()),
                      'refusing existing manager preparation destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    if payloads is not None:
        payloads.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manager-inputs-', dir=output.parent) as temporary:
        staging = Path(temporary)
        staged_output = staging / 'metadata'
        staged_payloads = staging / 'payloads' if payloads is not None else None
        records = _prepare(root, plan, built, staged_output, staged_payloads, verify, distribution_alias)
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
    parser.add_argument('--distribution-alias', action='store_true', help='explicit manual isolated same-digest distribution proof')
    parser.add_argument('--baseline-run', type=int, help='explicit manual original-record run only')
    args = parser.parse_args()
    if args.distribution_alias:
        artifacts.require(os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'distribution alias diagnostic requires explicit manual invocation')
    # Producer-owned policy verifies this exact active run, protected history,
    # or an explicitly selected original-record run in manual diagnostics.
    from component_provenance import selection_verifier
    prepare(args.root, json.loads(args.plan.read_text()),
            json.loads(args.built_metadata.read_text()) if args.built_metadata else None,
            args.output, args.payloads, selection_verifier(args.baseline_run, root=args.root), args.distribution_alias)


if __name__ == '__main__':
    main()
