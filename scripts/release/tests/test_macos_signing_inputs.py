"""Immutable qualification fixtures, with no network or signing credentials."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).parents[1]))
import macos_signing_inputs as inputs
from test_artifacts import manifest


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class SigningInputsTest(unittest.TestCase):
    def fixture(self):
        helper = b'OBVIOUS FAKE QUALIFIED NATIVE HELPER'
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            entry = tarfile.TarInfo('./libexec/symphony-host')
            entry.size = len(helper)
            archive.addfile(entry, io.BytesIO(helper))
        mac = buffer.getvalue()
        linux = b'OBVIOUS FAKE LINUX ARCHIVE'
        internal = manifest()
        del internal['bundle']['sha256']
        internal['bundles'] = {'macos': copy.deepcopy(internal['bundle']), 'linux_x86_64': {
            'filename': 'symphony-0.0.3-linux_x86_64.tar.gz'}}
        external = copy.deepcopy(internal)
        external['bundles']['macos']['sha256'] = digest(mac)
        external['bundles']['linux_x86_64']['sha256'] = digest(linux)
        external['bundle'] = dict(external['bundles']['macos'])
        inventory, payloads = [], {}
        groups = (
            ('current-manager-helper-macos-123-2', {'symphony-host': helper,
                                                  'symphony-host.sha256': (digest(helper)+'  symphony-host\n').encode()}),
            ('current-manager-inputs-123-2', {'release.json': json.dumps(internal).encode()}),
            ('current-manager-package-123-2', {'release.json': json.dumps(external).encode(),
                                              internal['bundle']['filename']: mac,
                                              internal['bundles']['linux_x86_64']['filename']: linux}))
        for identifier, (name, files) in enumerate(groups, 10):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for filename, raw in files.items():
                    archive.writestr(filename, raw)
            raw = buffer.getvalue()
            inventory.append({'id': identifier, 'name': name, 'expired': False, 'size_in_bytes': len(raw),
                              'digest': 'sha256:' + digest(raw)})
            payloads[f'actions/artifacts/{identifier}/zip'] = raw
        run = {'head_sha': 'a'*40, 'head_branch': 'dev', 'event': 'workflow_dispatch', 'status': 'completed',
               'conclusion': 'success', 'path': '.github/workflows/runner-integration.yml', 'run_attempt': 2,
               'head_repository': {'full_name': inputs.REPOSITORY}}

        def fetch(path, binary=False):
            if path == 'actions/runs/123':
                return run
            if path == 'actions/runs/123/artifacts?per_page=100':
                return {'artifacts': inventory}
            return payloads[path]
        return fetch, run, inventory, payloads, internal, helper, linux

    def test_exact_original_binding_and_linux_bytes_remain_unchanged(self):
        fetch, run, inventory, payloads, internal, helper, linux = self.fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'inputs'
            self.assertEqual(inputs.retrieve('123', root, 'a'*40, fetch), internal)
            receipt = json.loads((root / 'qualification.json').read_text())
            self.assertEqual(receipt['run_attempt'], 2)
            self.assertEqual(receipt['original_qualified_helper_sha256'], digest(helper))
            self.assertEqual((root / 'packages/symphony-0.0.3-linux_x86_64.tar.gz').read_bytes(), linux)
            self.assertEqual((root / 'helper/symphony-host').read_bytes(), helper)

    def test_failed_foreign_pr_wrong_revision_and_wrong_workflow_refuse(self):
        for change in ({'conclusion': 'failure'}, {'event': 'pull_request'}, {'head_sha': 'b'*40},
                       {'head_branch': 'codex/unreviewed'}, {'path': '.github/workflows/other.yml'},
                       {'head_repository': {'full_name': 'foreign/repo'}}):
            fetch, run, *_ = self.fixture()
            run.update(change)
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                inputs.retrieve('123', Path(temporary) / 'inputs', 'a'*40, fetch)

    def test_corrupt_expired_missing_and_foreign_attempt_artifacts_refuse(self):
        for change in (lambda i: i[0].update(digest='sha256:'+'0'*64),
                       lambda i: i[0].update(expired=True), lambda i: i.pop(),
                       lambda i: i[0].update(name='current-manager-helper-macos-123-1')):
            fetch, run, inventory, *_ = self.fixture()
            change(inventory)
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                inputs.retrieve('123', Path(temporary) / 'inputs', 'a'*40, fetch)

    def test_zip_traversal_and_different_qualified_helper_refuse(self):
        for filename, raw in (('../escape', b'unsafe'), ('symphony-host', b'different helper')):
            fetch, run, inventory, payloads, *_ = self.fixture()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr(filename, raw)
                archive.writestr('symphony-host.sha256', digest(raw)+'  symphony-host\n')
            changed = buffer.getvalue()
            payloads['actions/artifacts/10/zip'] = changed
            inventory[0].update(digest='sha256:'+digest(changed), size_in_bytes=len(changed))
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                inputs.retrieve('123', Path(temporary) / 'inputs', 'a'*40, fetch)
