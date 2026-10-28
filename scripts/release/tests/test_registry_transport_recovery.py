import hashlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/release'))
import ghcr_transport_recovery as transport
import recover_ec2_registry as recovery

REPO = 'jayson-stangel/symphony-codex'
DIGEST = 'sha256:' + 'a' * 64
PREFIX = '/v2/' + REPO


class LocationTests(unittest.TestCase):
    def test_supported_opaque_paths_reach_final_put_unchanged(self):
        for endpoint in ('upload', 'uploads'):
            for session in ('uuid-123', 'opaque_A.b==', 'safe~segment'):
                for state in ('', '?_state=a%2Bb%3D%3D'):
                    relative = PREFIX + '/blobs/' + endpoint + '/' + session + state
                    for location in (relative, 'https://ghcr.io' + relative):
                        with self.subTest(location=location):
                            value = transport.upload_target(REPO, location, DIGEST)
                            self.assertTrue(transport.valid_resource('PUT', value))
                            self.assertEqual(value, relative[len(PREFIX):] +
                                ('&' if state else '?') + 'digest=sha256%3A' + 'a' * 64)

    def test_foreign_and_ambiguous_locations_are_refused(self):
        base = PREFIX + '/blobs/uploads/'
        invalid = [base + x for x in ('', '.', '..', '../x', 'x/y', 'x\\y', '%2e%2e',
            'x%2fy', 'x%5cy', 'x;', 'x\r\n', 'x#', 'x#fragment', 'x' * 4097)]
        invalid += ['http://ghcr.io' + base + 'a', 'https://ghcr.io:443' + base + 'a',
                    'https://user@ghcr.io' + base + 'a', 'https://foreign.invalid' + base + 'a',
                    '//foreign.invalid' + base + 'a', '/v2/foreign/blobs/uploads/a',
                    PREFIX + '-other/blobs/uploads/a']
        invalid += [base + 'a?' + q for q in ('other=x', '_state=a&_state=b',
            '_state=a&digest=x', '_state=%0d%0a', '_state=%00', '_state=%7f',
            '_state=%FF', '_state=bad%', '_state=' + 'x' * 8193)]
        for location in invalid:
            with self.subTest(location=location):
                with self.assertRaises((ValueError, TypeError)):
                    transport.upload_target(REPO, location, DIGEST)
        with self.assertRaises(ValueError):
            transport.upload_target('foreign', base + 'a', DIGEST)

    def test_request_validator_cannot_bypass_location_policy(self):
        for method, suffix in [('GET', '/blobs/' + DIGEST), ('POST', '/blobs/upload/'),
                ('PUT', '/blobs/uploads/../x?digest=sha256%3A' + 'a' * 64),
                ('PUT', '/blobs/uploads/a?_state=%00&digest=sha256%3A' + 'a' * 64),
                ('PUT', '/blobs/uploads/a?digest=sha256%3A' + 'a' * 64 + '&other=x'),
                ('PUT', '/blobs/uploads/a?digest=sha256%3A' + 'a' * 64 + '#')]:
            self.assertFalse(transport.valid_resource(method, suffix))


class Response:
    def __init__(self, status, headers=None, raw=b''):
        self.status, self.headers, self.body = status, headers or {}, io.BytesIO(raw)
    def read(self, size):
        return self.body.read(size)


class RequestTests(unittest.TestCase):
    def client(self, responses):
        calls, journal = [], []
        class Connection:
            def __init__(self, host, **kwargs):
                self.response = responses.pop(0)
                self.host = host
            def request(self, method, path, body=None, headers=None):
                calls.append((self.host, method, path, headers))
            def getresponse(self):
                return self.response
            def close(self):
                pass
        client = transport.GHCR(REPO, 'Fixture', 'SYNTHETIC', connection=Connection,
                                clock=lambda: 0, journal=journal.append)
        client.token, client.expiry = 'SYNTHETIC-TOKEN', 100
        return client, calls, journal

    def test_actual_final_put_uses_fixed_host_and_exact_opaque_target(self):
        client, calls, journal = self.client([Response(201)])
        target = client.upload_location(PREFIX + '/blobs/upload/opaque_A.b==?_state=x%2By', DIGEST)
        client.request('PUT', target, size=0, expected=(201,))
        self.assertEqual(calls[0][:3], ('ghcr.io', 'PUT', PREFIX + target))
        self.assertEqual(calls[0][3]['Authorization'], 'Bearer SYNTHETIC-TOKEN')
        encoded = json.dumps(journal)
        self.assertNotIn('opaque_A.b==', encoded)
        self.assertNotIn('x%2By', encoded)
        self.assertNotIn('SYNTHETIC-TOKEN', encoded)
        self.assertEqual(journal[0]['endpoint'], 'singular')

    def test_redirect_refused_without_second_request_and_bound_response(self):
        client, calls, journal = self.client([Response(307, {'Location': 'https://foreign.invalid'})])
        with self.assertRaisesRegex(ValueError, 'status 307'):
            client.request('HEAD', '/blobs/' + DIGEST)
        self.assertEqual(len(calls), 1)
        client, calls, _ = self.client([Response(200, raw=b'xx')])
        with self.assertRaisesRegex(ValueError, 'response exceeds'):
            client.request('HEAD', '/blobs/' + DIGEST, maximum=0)


class ControllerTests(unittest.TestCase):
    def test_controller_committed_bytes_refuse_dirty_tool_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'adapter.py').write_bytes(b'edited')
            with patch.object(recovery.subprocess, 'check_output', return_value=b'committed'):
                with self.assertRaisesRegex(ValueError, 'controller file bytes differ'):
                    recovery.committed(root, 'b' * 40, 'adapter.py')

    def test_every_accepted_input_pin_refuses_substitution_before_transfer(self):
        frozen = json.loads((ROOT / 'scripts/release/ec2-registry-recovery-files.json').read_text())
        origin = {'import_artifact': {'id': recovery.IMPORT_ARTIFACT, 'digest': recovery.IMPORT_DIGEST}}
        qualified = dict(run_id=recovery.Q_RUN, run_attempt=recovery.Q_ATTEMPT, source_revision=recovery.CANDIDATE)
        plan = dict(manager_source_revision=recovery.CANDIDATE, distribution_checks=[{}] * 4)
        recovery.accepted_inputs(origin, qualified, plan, frozen, frozen)
        for group, field, value in [('origin', 'id', 123), ('origin', 'digest', 'sha256:' + 'f' * 64),
                ('qualified', 'run_id', 123), ('qualified', 'run_attempt', 1),
                ('qualified', 'source_revision', 'f' * 40),
                ('plan', 'manager_source_revision', 'f' * 40), ('plan', 'distribution_checks', [])]:
            a, b, c = copy.deepcopy((origin, qualified, plan))
            target = {'origin': a['import_artifact'], 'qualified': b, 'plan': c}[group]
            target[field] = value
            with self.assertRaises(ValueError):
                recovery.accepted_inputs(a, b, c, frozen, frozen)
        for filename in frozen:
            wrong = dict(frozen); wrong[filename] = 'f' * 64
            with self.assertRaises(ValueError):
                recovery.accepted_inputs(origin, qualified, plan, wrong, frozen)
        for changed in ({**frozen, 'extra': 'f' * 64}, {k:v for k,v in frozen.items() if k != 'SHA256SUMS'}):
            with self.assertRaises(ValueError):
                recovery.accepted_inputs(origin, qualified, plan, changed, frozen)

    def environment(self):
        return dict(GITHUB_SHA='b' * 40, GITHUB_REPOSITORY='Jayson-Stangel/symphony',
                    GITHUB_EVENT_NAME='push', GITHUB_REF='refs/tags/' + recovery.REPAIR_TAG,
                    GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1', SYMPHONY_PRODUCTION_RELEASES='enabled')

    def test_tool_and_candidate_are_distinct_and_no_environment_spoof(self):
        environment = self.environment()
        with patch.object(recovery, 'head', side_effect=['b' * 40, recovery.CANDIDATE]), \
             patch.object(recovery, 'committed'), \
             patch.object(recovery.subprocess, 'check_output', return_value=b''):
            self.assertEqual(recovery.context(Path('/candidate'), Path('/tool'), environment), 'b' * 40)
        self.assertEqual(environment['GITHUB_SHA'], 'b' * 40)

    def test_context_refuses_retries_wrong_tag_event_source_and_unbounded_selection(self):
        for key, value in [('GITHUB_RUN_ATTEMPT', '2'), ('GITHUB_REF', 'refs/heads/dev'),
                ('GITHUB_EVENT_NAME', 'workflow_dispatch'), ('GITHUB_REPOSITORY', 'foreign/repo'),
                ('GITHUB_RUN_ID', str(recovery.Q_RUN)), ('SYMPHONY_PRODUCTION_RELEASES', 'disabled'),
                ('GITHUB_SHA', recovery.CANDIDATE)]:
            environment = self.environment(); environment[key] = value
            with patch.object(recovery, 'head', side_effect=['b' * 40, recovery.CANDIDATE]), \
                 patch.object(recovery, 'committed'), \
                 patch.object(recovery.subprocess, 'check_output', return_value=b''):
                with self.assertRaises(ValueError):
                    recovery.context(Path('/candidate'), Path('/tool'), environment)

    def test_journal_hash_chain_failure_retention_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'journal.json'
            journal = recovery.Journal(path, {'controller_revision': 'b' * 40})
            journal.add({'event': 'failure', 'message': 'sanitized'})
            previous = '0' * 64
            for row in json.loads(path.read_text()):
                raw = json.dumps(row['entry'], sort_keys=True, separators=(',', ':')).encode()
                previous = hashlib.sha256(bytes.fromhex(previous) + raw).hexdigest()
                self.assertEqual(row['chain_sha256'], previous)
            self.assertEqual(path.with_suffix('.sha256').read_text().split()[0],
                             hashlib.sha256(path.read_bytes()).hexdigest())
            with self.assertRaises(ValueError):
                recovery.Journal(path, {})


class CompositionTests(unittest.TestCase):
    def exercise(self, fault=None):
        events = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); candidate = root / 'candidate'; controller = root / 'controller'
            (candidate / 'scripts/release').mkdir(parents=True)
            (controller / 'scripts/release').mkdir(parents=True)
            (controller / 'scripts/release/ghcr_transport_recovery.py').write_bytes(
                (ROOT / 'scripts/release/ghcr_transport_recovery.py').read_bytes())
            modules = {}
            for name in ('component_provenance', 'component_publication', 'graph_publication',
                         'local_distribution', 'production_release'):
                module = types.ModuleType(name)
                module.__file__ = str(candidate / 'scripts/release' / (name + '.py'))
                Path(module.__file__).write_bytes(b'candidate fixture')
                modules[name] = module
            provenance, publication, graph, local, production = [modules[n] for n in modules]
            api_calls = []
            provenance.api = lambda path: api_calls.append(path) or {
                'id': recovery.IMPORT_ARTIFACT,
                'digest': 'wrong' if fault == 'import' else recovery.IMPORT_DIGEST}
            provenance.verify_origin = lambda record: events.append('verify-origin') or {}
            production.live_source = lambda *args: events.append('candidate-tag')
            def tool_tag(*args):
                events.append('tool-tag')
                if fault == 'tag': raise ValueError('synthetic tag moved')
            production.live_tag = tool_tag
            production.checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            images = ['ghcr.io/' + REPO + '@sha256:' + str(i) * 64 for i in range(1, 5)]
            plan = {'manager_source_revision': recovery.CANDIDATE,
                    'distribution_checks': [{'image': image} for image in images], 'manifest': {'exact': True}}
            def retained(*args):
                destination = args[3]; signed = destination / 'signed'; signed.mkdir(parents=True)
                names = json.loads((ROOT / 'scripts/release/ec2-registry-recovery-files.json').read_text())
                for name in names:
                    p = signed / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'fixture')
                (signed / 'qualification.json').write_text(json.dumps({'run_id': recovery.Q_RUN,
                    'run_attempt': 2 if fault == 'Q' else recovery.Q_ATTEMPT, 'source_revision': recovery.CANDIDATE}))
                (signed / 'production-plan.json').write_text(json.dumps(plan))
                (destination / 'signed-origin.json').write_text(json.dumps({'import_artifact':
                    {'id': recovery.IMPORT_ARTIFACT, 'digest': recovery.IMPORT_DIGEST}}))
                freeze = {str(p.relative_to(signed)): production.checksum(p) for p in signed.rglob('*') if p.is_file()}
                if fault == 'freeze': freeze['production-plan.json'] = 'f' * 64
                (controller / 'scripts/release/ec2-registry-recovery-files.json').write_text(json.dumps(freeze))
            local.retained_local = retained
            available = {images[0]}
            def full(image, full=False):
                self.assertTrue(full)
                events.append(('closure', image, image in available))
                return image in available
            publication.registry_available = full
            def publish(plan, verify, transfer, before_transfer, probe, qualify):
                record = {'source_revision': recovery.CANDIDATE, 'qualification': {'run_id': 987}}
                verify(record)
                missing = [image for image in images if not probe(image)]
                before_transfer()
                transfer([{'image': image} for image in missing])
                for image in missing: self.assertTrue(qualify(image))
                return plan['manifest']
            publication.publish = publish
            graph.GHCR, graph.REPOSITORIES = transport.original.GHCR, transport.original.REPOSITORIES
            graph.graph, graph.HOST, graph.MAX_TOKEN = transport.original.graph, transport.original.HOST, transport.original.MAX_TOKEN
            def transfer(selected, factory):
                events.append(('transfer', [v['image'] for v in selected]))
                factory(REPO)  # Construct real adapter; no authorization/network.
                available.update(v['image'] for v in selected)
            graph.publish_selected = transfer
            if fault == 'module': provenance.__file__ = '/foreign/module.py'
            environment = dict(GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                               GITHUB_ACTOR='Fixture', GH_TOKEN='SYNTHETIC')
            with patch.dict(sys.modules, modules), patch.object(sys, 'path', list(sys.path)), \
                 patch.dict(os.environ, environment), patch.object(recovery, 'context', return_value='b' * 40), \
                 patch.object(recovery.subprocess, 'check_output', return_value=b'candidate fixture'):
                if fault:
                    with self.assertRaises(SystemExit): recovery.recover(candidate, controller, root / 'output')
                else:
                    recovery.recover(candidate, controller, root / 'output')
            rows = json.loads((root / 'output/journal.json').read_text())
            self.assertEqual(rows[-1]['entry']['event'], 'failure' if fault else 'success')
            if fault:
                self.assertFalse(any(isinstance(e, tuple) and e[0] == 'transfer' for e in events))
            else:
                self.assertEqual([e for e in events if isinstance(e, tuple) and e[0] == 'transfer'],
                                 [('transfer', images[1:])])
                self.assertEqual({e[1] for e in events if isinstance(e, tuple) and e[0] == 'closure' and e[2]}, set(images))
                self.assertGreaterEqual(events.count('tool-tag'), 3)
            if fault in ('module', 'tag'): self.assertEqual(api_calls, [])

    def test_mixed_preexisting_missing_full_four_closures_and_frozen_manifest(self):
        self.exercise()

    def test_import_Q_freeze_tag_and_module_substitution_refuse_before_transfer(self):
        for fault in ('import', 'Q', 'freeze', 'tag', 'module'):
            with self.subTest(fault=fault): self.exercise(fault)


if __name__ == '__main__':
    unittest.main()
