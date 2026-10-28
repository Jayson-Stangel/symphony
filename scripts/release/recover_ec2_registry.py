#!/usr/bin/env python3
"""One separately approved controller; frozen ec2 remains the package source."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.dont_write_bytecode = True
CANDIDATE = 'ec2be6e1c21c7dc7f7d7d8536bcac1ce06f3202b'
TAG = 'v0.0.6'
REPAIR_TAG = 'v0.0.6-transport-repair-1'
IMPORT_RUN, IMPORT_ATTEMPT = '40596350252', '2'
IMPORT_ARTIFACT = 12776502137
IMPORT_DIGEST = 'sha256:4e811bb59217f382c58d103743642de5eaafb9c63a1ec5bb5ec20c9e1b7fa31e'
Q_RUN, Q_ATTEMPT = 40588897389, 3


def require(condition, message):
    if not condition:
        raise ValueError('registry recovery: ' + message)


def head(path):
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def committed(path, revision, relative):
    expected = subprocess.check_output(['git', '-C', str(path), 'show', revision + ':' + relative])
    require((path / relative).read_bytes() == expected, 'controller file bytes differ')


def accepted_inputs(origin, qualified, plan, snapshots, frozen):
    require(origin['import_artifact']['id'] == IMPORT_ARTIFACT and
            origin['import_artifact']['digest'] == IMPORT_DIGEST, 'retained import differs')
    require(qualified['run_id'] == Q_RUN and qualified['run_attempt'] == Q_ATTEMPT and
            qualified['source_revision'] == CANDIDATE, 'original qualification differs')
    require(plan['manager_source_revision'] == CANDIDATE and len(plan['distribution_checks']) == 4,
            'candidate plan differs')
    require(snapshots == frozen and len(snapshots) == 17, 'accepted signed file freeze differs')


def context(candidate, controller, environment=os.environ):
    tool = head(controller)
    require(re.fullmatch('[a-f0-9]{40}', tool) and tool != CANDIDATE and
            tool == environment.get('GITHUB_SHA'), 'controller identity differs')
    require(head(candidate) == CANDIDATE, 'candidate checkout differs')
    require(not subprocess.check_output(['git', '-C', str(candidate), 'status', '--porcelain']).strip(),
            'candidate checkout is modified')
    require(not subprocess.check_output(['git', '-C', str(controller), 'status', '--porcelain']).strip(),
            'controller checkout is modified')
    for relative in ('scripts/release/recover_ec2_registry.py', 'scripts/release/ghcr_transport_recovery.py',
                     'scripts/release/ec2-registry-recovery-files.json', '.github/workflows/ec2-registry-transport-repair.yml'):
        committed(controller, tool, relative)
    require(environment.get('GITHUB_REPOSITORY') == 'Jayson-Stangel/symphony' and
            environment.get('GITHUB_EVENT_NAME') == 'push' and
            environment.get('GITHUB_REF') == 'refs/tags/' + REPAIR_TAG and
            environment.get('GITHUB_RUN_ATTEMPT') == '1' and
            environment.get('SYMPHONY_PRODUCTION_RELEASES') == 'enabled', 'protected repair context missing')
    run = environment.get('GITHUB_RUN_ID', '')
    require(re.fullmatch('[1-9][0-9]*', run) and int(run) not in (Q_RUN, int(IMPORT_RUN)), 'recovery run identity differs')
    return tool


class Journal:
    """Bounded, hash-chained receipt, persisted after every safe event."""
    def __init__(self, path, identity):
        require(not path.exists(), 'journal already exists')
        self.path, self.rows, self.previous = path, [], '0' * 64
        path.parent.mkdir(parents=True, exist_ok=True)
        self.add(dict(event='start', **identity))

    def add(self, row):
        require(len(self.rows) < 10000, 'journal event bound exceeded')
        row = dict(row, observed_utc=datetime.now(timezone.utc).isoformat())
        raw = json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
        self.previous = hashlib.sha256(bytes.fromhex(self.previous) + raw).hexdigest()
        self.rows.append({'entry': row, 'chain_sha256': self.previous})
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.rows, indent=2) + '\n')
        temporary.replace(self.path)
        self.path.with_suffix('.sha256').write_text(hashlib.sha256(self.path.read_bytes()).hexdigest() + '  ' + self.path.name + '\n')


def recover(candidate, controller, output):
    tool = context(candidate, controller)
    journal = Journal(output / 'journal.json', {'controller_revision': tool,
        'candidate_revision': CANDIDATE, 'candidate_tag': TAG, 'repair_tag': REPAIR_TAG,
        'run_id': int(os.environ['GITHUB_RUN_ID']), 'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']})
    try:
        # Frozen modules resolve imports and __file__ roots from the candidate.
        sys.path.insert(0, str(candidate / 'scripts/release'))
        import component_provenance as provenance
        import component_publication as publication
        import graph_publication as graph_transport
        import local_distribution
        import production_release as production
        for module in (provenance, publication, graph_transport, local_distribution, production):
            require(Path(module.__file__).resolve().is_relative_to(candidate.resolve()), 'foreign candidate module')
            relative = str(Path(module.__file__).resolve().relative_to(candidate.resolve()))
            committed = subprocess.check_output(['git', '-C', str(candidate), 'show', CANDIDATE + ':' + relative])
            require(Path(module.__file__).read_bytes() == committed, 'candidate module bytes differ')
        production.live_source(CANDIDATE, TAG)
        production.live_tag(tool, REPAIR_TAG)
        artifact = provenance.api('actions/artifacts/' + str(IMPORT_ARTIFACT))
        require(artifact['id'] == IMPORT_ARTIFACT and artifact['digest'] == IMPORT_DIGEST,
                'original import artifact differs')
        retained = output / 'retained'
        local_distribution.retained_local(IMPORT_RUN, IMPORT_ATTEMPT, CANDIDATE, retained)
        origin = json.loads((retained / 'signed-origin.json').read_text())
        qualified = json.loads((retained / 'signed/qualification.json').read_text())
        plan = json.loads((retained / 'signed/production-plan.json').read_text())
        snapshots = {str(p.relative_to(retained / 'signed')): production.checksum(p)
                     for p in (retained / 'signed').rglob('*') if p.is_file()}
        frozen = json.loads((controller / 'scripts/release/ec2-registry-recovery-files.json').read_text())
        accepted_inputs(origin, qualified, plan, snapshots, frozen)
        journal.add({'event': 'retained-inputs-verified', 'import_artifact': IMPORT_ARTIFACT,
                     'import_digest': IMPORT_DIGEST, 'qualification_run': Q_RUN,
                     'qualification_attempt': Q_ATTEMPT, 'signed_files': snapshots})
        def verify(record):
            require(record['source_revision'] == CANDIDATE and
                    record['qualification']['run_id'] != int(os.environ['GITHUB_RUN_ID']),
                    'not an original external qualified record')
            # selection_verifier(root=candidate) requires GITHUB_SHA==candidate;
            # these completed external origins use its protected-history branch.
            return provenance.verify_origin(record)
        spec = importlib.util.spec_from_file_location('repair_transport',
            controller / 'scripts/release/ghcr_transport_recovery.py')
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        def transfer(selections):
            graph_transport.publish_selected(selections, lambda repository:
                adapter.GHCR(repository, os.environ['GITHUB_ACTOR'], os.environ['GH_TOKEN'], journal=journal.add))
        def source_tags():
            production.live_source(CANDIDATE, TAG)
            production.live_tag(tool, REPAIR_TAG)
        fully_verified = set()
        def full_closure(image):
            started = time.monotonic()
            passed = publication.registry_available(image, full=True)
            journal.add({'event': 'anonymous-full-graph-check', 'image': image, 'passed': bool(passed),
                         'elapsed_seconds': round(time.monotonic() - started, 3)})
            if passed:
                fully_verified.add(image)
            return passed
        result = publication.publish(plan, verify, transfer=transfer, before_transfer=source_tags,
            probe=full_closure, qualify=full_closure)
        require(result == plan['manifest'], 'publication changed candidate manifest')
        for check in plan['distribution_checks']:
            require(check['image'] in fully_verified or full_closure(check['image']), 'anonymous full graph closure unavailable')
        source_tags()
        require(snapshots == {str(p.relative_to(retained / 'signed')): production.checksum(p)
                for p in (retained / 'signed').rglob('*') if p.is_file()}, 'signed inputs changed')
        journal.add({'event': 'success', 'public_graphs': [v['image'] for v in plan['distribution_checks']],
                     'signed_inputs_unchanged': True, 'github_release_mutations': False})
    except Exception:
        journal.add({'event': 'failure', 'message': 'transport or provenance refused; no automatic retry'})
        raise SystemExit('Registry recovery refused; see sanitized journal') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    recover(args.candidate.resolve(), Path(__file__).resolve().parents[2], args.output)
