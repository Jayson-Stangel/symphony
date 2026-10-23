import os
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import subprocess
from unittest import mock
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import reuse_payload as payload


class ActualPayloadTests(unittest.TestCase):
    def setUp(self):
        self.record = {'provider': 'codex', 'architecture': 'x86_64', 'input_identity': 'c' * 64,
                       'source_revision': 'b' * 40, 'image': 'localhost:5000/symphony-codex@sha256:' + 'd' * 64}
        self.archive = b'actual original opaque compressed Docker archive bytes'
        self.origin = {'artifact_id': 123, 'archive_filename': 'codex-x86_64.docker.tar.gz',
                       'archive_sha256': hashlib.sha256(self.archive).hexdigest(), 'run_id': 456}
        self.make_zip()

    def make_zip(self, duplicate=False, member=None):
        data = io.BytesIO()
        name = member or self.origin['archive_filename']
        with zipfile.ZipFile(data, 'w') as bundle:
            bundle.writestr(name, self.archive)
            # Foreign members are never extracted.
            bundle.writestr('../../foreign', 'do not write')
            if duplicate:
                bundle.writestr(name, self.archive)
        self.zip = data.getvalue()
        self.origin['artifact_digest'] = 'sha256:' + hashlib.sha256(self.zip).hexdigest()

    def retrieve(self, destination, verify=None, fetch=None):
        def downloaded(artifact_id, path):
            self.assertEqual(artifact_id, 123)
            path.write_bytes(self.zip)
        return payload.retrieve(self.record, self.origin, destination,
                                verify=verify or (lambda record: copy.deepcopy(self.origin)),
                                fetch=fetch or downloaded, validate=lambda *args: None)

    def test_manual_candidate_override_cannot_be_granted_by_a_plan_or_ordinary_event(self):
        for event in ('pull_request', 'push', None):
            with self.assertRaisesRegex(ValueError, 'explicit manual'):
                payload.origin_verifier(123, event)
        record = {'qualification': {'run_id': 123}}
        with mock.patch.object(payload, 'verify_origin') as verify:
            payload.origin_verifier(None, 'pull_request')(record)
            verify.assert_called_once_with(record, candidate_run=None)
        with mock.patch.dict(os.environ,{'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_REPOSITORY':'Jayson-Stangel/symphony'}), \
             mock.patch('component_selection.proof.verify_origin') as verify:
            payload.origin_verifier(123, 'workflow_dispatch')(record)
            verify.assert_called_once_with(record,fetch=__import__('component_provenance').api,candidate_run=123)

    def test_real_download_stream_is_bounded_and_child_failure_propagates(self):
        original_popen = subprocess.Popen
        for code, bound, error in (("import sys;sys.stdout.buffer.write(b'x'*10)", 8, ValueError),
                                   ("import sys;sys.stdout.buffer.write(b'x');sys.exit(7)", 20, subprocess.CalledProcessError)):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temporary:
                def child(*args, **kwargs):
                    return original_popen([sys.executable, '-c', code], **kwargs)
                with mock.patch.object(payload.subprocess, 'Popen', side_effect=child), mock.patch.object(payload, 'MAX_ARTIFACT_BYTES', bound):
                    with self.assertRaises(error):
                        payload.download(123, Path(temporary) / 'partial.zip')
                self.assertLessEqual((Path(temporary) / 'partial.zip').stat().st_size, bound)

    def test_actual_bytes_retained_with_original_record_and_no_foreign_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'current'
            archive = self.retrieve(destination)
            self.assertEqual(archive.read_bytes(), self.archive)
            self.assertEqual(json.loads((destination / 'codex-x86_64.json').read_text()), self.record)
            self.assertEqual(json.loads((destination / 'origin.json').read_text()), self.origin)
            self.assertFalse((Path(temporary) / 'foreign').exists())
            self.assertEqual(list(Path(temporary).iterdir()), [destination])

    def test_changed_origin_refuses_before_payload_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            def forbidden(*args):
                self.fail('payload fetched after origin changed')
            with self.assertRaisesRegex(ValueError, 'origin changed'):
                self.retrieve(Path(temporary) / 'current', verify=lambda _: dict(self.origin, run_id=999), fetch=forbidden)

    def test_artifact_corruption_and_original_archive_corruption_leave_no_receipt(self):
        for artifact_corruption in (True, False):
            with self.subTest(artifact_corruption=artifact_corruption), tempfile.TemporaryDirectory() as temporary:
                original = copy.deepcopy(self.origin)
                if artifact_corruption:
                    self.origin['artifact_digest'] = 'sha256:' + '0' * 64
                else:
                    self.origin['archive_sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'checksum differs'):
                    self.retrieve(Path(temporary) / 'current')
                self.assertEqual(list(Path(temporary).iterdir()), [])
                self.origin = original

    def test_missing_exact_member_refuses_and_does_not_follow_foreign_names(self):
        self.make_zip(member='previous/codex-x86_64.docker.tar.gz')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'missing or duplicate'):
                self.retrieve(Path(temporary) / 'current')
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_missing_or_corrupt_graph_refuses_before_local_receipt_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'current'
            def downloaded(artifact_id, path):path.write_bytes(self.zip)
            def refuse(*args):raise ValueError('qualified graph missing/corrupt')
            with self.assertRaisesRegex(ValueError, 'graph missing/corrupt'):
                payload.retrieve(self.record, self.origin, destination, verify=lambda _:copy.deepcopy(self.origin),
                                 fetch=downloaded, validate=refuse)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_existing_destination_and_failed_download_preserve_foreign_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'current'
            destination.mkdir()
            marker = destination / 'foreign'
            marker.write_text('retained')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                self.retrieve(destination)
            self.assertEqual(marker.read_text(), 'retained')
            def failed(_, path):
                path.write_bytes(b'partial')
                raise OSError('download failed')
            with self.assertRaises(OSError):
                self.retrieve(Path(temporary) / 'other', fetch=failed)
            self.assertEqual(list(Path(temporary).iterdir()), [destination])

    def test_only_complete_exact_manager_plan_can_authorize_reuse(self):
        revision = 'a' * 40
        entry = {'action': 'reuse', 'input_identity': self.record['input_identity'], 'record': self.record}
        plan = {'planning_complete': True, 'manager_source_revision': revision,
                'components': {'codex': {'x86_64': entry}}}
        self.assertIs(payload.selected(plan, 'codex', 'x86_64', revision), entry)
        for changed in (dict(plan, planning_complete=False), dict(plan, manager_source_revision='b' * 40)):
            with self.assertRaises(ValueError):
                payload.selected(changed, 'codex', 'x86_64', revision)
        entry['action'] = 'build'
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            payload.selected(plan, 'codex', 'x86_64', revision)


if __name__ == '__main__':
    unittest.main()
