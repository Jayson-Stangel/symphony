#!/usr/bin/env python3
"""Join actual successful native records and original verified reused records."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import artifacts
from component_provenance import verify_origin, selection_verifier
import manager_version


def join(plan, records, revision, verify):
    artifacts.require(plan.get('planning_complete') is True and plan.get('manager_source_revision') == revision,
                      'plan does not belong to the current manager source')
    selected = []
    for provider in artifacts.PROVIDERS:
        for architecture in artifacts.ARCHITECTURES:
            decision = plan['components'][provider][architecture]
            if decision['action'] == 'reuse':
                record = decision['record']
            else:
                artifacts.require(decision['action'] == 'build', 'unknown planned action')
                record = json.loads((Path(records) / f'{provider}-{architecture}.json').read_text())
                artifacts.require(record.get('source_revision') == revision, 'new native build source differs')
            selected_input=decision.get('selected_input_identity',decision['input_identity'])
            if decision.get('selection_mode')=='finite-input-transition-v1':
                from finite_transition import validate_evidence
                evidence=validate_evidence(decision['input_transition'],revision)
                artifacts.require(selected_input==evidence['source_input_identities'][provider][architecture] and
                                  decision['input_identity']==decision.get('desired_input_identity')==evidence['manager_input_identities'][provider][architecture],
                                  'selected original and desired manager input identities differ from finite transition')
            artifacts.require(record.get('provider') == provider and record.get('architecture') == architecture and
                              record.get('input_identity') == selected_input, 'actual native record differs from plan')
            origin=verify(record)
            if decision['action']=='reuse':
                artifacts.require(origin==decision['origin'],'original reused origin changed after planning')
            selected.append(record)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--records', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--schema', type=int, choices=(1, 2), required=True)
    parser.add_argument('--baseline-run', type=int)
    args = parser.parse_args()
    try:
        revision = os.environ['GITHUB_SHA']
        manual = os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
        current_run = int(os.environ['GITHUB_RUN_ID'])
        artifacts.require(args.baseline_run is None or manual, 'candidate baseline override requires manual qualification')
        selected_verifier = selection_verifier(args.baseline_run)
        def verify(record):
            run_id = record['qualification'].get('record_origin', {}).get('run_id', record['qualification']['run_id'])
            if manual and run_id == current_run:
                return verify_origin(record, candidate_run=current_run)
            return selected_verifier(record)
        plan = json.loads(Path(args.plan).read_text())
        selected = join(plan, args.records, revision, verify)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        files = []
        for record in selected:
            path = output / f"{record['provider']}-{record['architecture']}.json"
            path.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
            files.append(str(path))
        if plan.get('release_mode')=='manager-envelope-v1':
            from manager_envelope import make_core,write_release
            records={};origins={}
            for record in selected:
                p,a=record['provider'],record['architecture'];records.setdefault(p,{})[a]=record
                origins.setdefault(p,{})[a]=verify(record)
            core=make_core('.',records,origins,plan['input_transition'])
            write_release(core,output)
        else:
            artifacts.assemble(SimpleNamespace(records=files, schema_version=args.schema,
                               version=manager_version.read(), revision=revision, channel='candidate',
                               bundle=None, platform_bundles=True, output=str(output / 'release.json')), verifier=verify)
        (output/'component-plan.json').write_text(json.dumps(plan,sort_keys=True,indent=2)+'\n')
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'component join: {type(error).__name__}; candidate assembly refused\n')


if __name__ == '__main__':
    main()
