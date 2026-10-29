"""Credential-free protocol and evidence-invalidation proof; no service calls."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import transport_compatibility as proof

CONTEXT = {'actor': 'Fixture', 'authentication': 'github-actions',
           'repository_visibility': 'private', 'configuration_digest': 'sha256:' + 'b' * 64}


class BindingTests(unittest.TestCase):
    def fixture(self, root):
        paths = proof.inputs(proof.ROOT)
        for name in paths:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(proof.ROOT / name, destination)
        return proof.snapshot(root, CONTEXT)

    def test_import_closure_and_workflows_bound_and_unrelated_docs_reusable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.fixture(root)
            for path in ('scripts/release/ghcr_transport_recovery.py',
                         'scripts/release/qualified_graph.py',
                         'scripts/release/transport_compatibility.py',
                         'scripts/release/transport_fixture.py', *proof.FIXTURE_FILES, *proof.WORKFLOWS):
                self.assertIn(path, before['files'])
            (root / 'README.md').write_text('unrelated prose')
            result = proof.compare(before, proof.snapshot(root, CONTEXT))
            self.assertTrue(result['input_binding_matches'])
            self.assertIn('service capability and production acceptance unverified', result['scope'])
            for path in ('scripts/release/qualified_graph.py', *proof.WORKFLOWS):
                original = (root / path).read_text()
                (root / path).write_text(original + '\n# changed\n')
                result = proof.compare(before, proof.snapshot(root, CONTEXT))
                self.assertEqual(result['changed_files'], [path])
                self.assertFalse(result['input_binding_matches'])
                (root / path).write_text(original)
            # A newly consumed module invalidates the evidence inventory too.
            entry = root / 'scripts/release/graph_publication.py'
            entry.write_text(entry.read_text() + '\nimport fixture_added_dependency\n')
            new = root / 'scripts/release/fixture_added_dependency.py'
            new.write_text('value = 1\n')
            self.assertIn(new.relative_to(root).as_posix(),
                          proof.compare(before, proof.snapshot(root, CONTEXT))['changed_files'])

    def test_fixture_executor_and_data_mutations_invalidate_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = self.fixture(root)
            executor = root / 'scripts/release/transport_fixture.py'
            original = executor.read_text()
            changed = original.replace("'refs/heads/dev'", "'refs/heads/other'")
            self.assertNotEqual(changed, original)
            executor.write_text(changed)
            result = proof.compare(before, proof.snapshot(root, CONTEXT))
            self.assertFalse(result['input_binding_matches'])
            self.assertEqual(result['changed_files'], ['scripts/release/transport_fixture.py'])
            executor.write_text(original)
            data = root / proof.FIXTURE_FILES[0]
            content = json.loads(data.read_text()); content['scope'] = 'different-fixture-contract'
            data.write_text(json.dumps(content))
            result = proof.compare(before, proof.snapshot(root, CONTEXT))
            self.assertFalse(result['input_binding_matches'])
            self.assertEqual(result['changed_files'], list(proof.FIXTURE_FILES))

    def test_missing_or_symlinked_fixture_executor_and_data_fail_closed(self):
        for relative in ('scripts/release/transport_fixture.py', *proof.FIXTURE_FILES):
            with self.subTest(input=relative), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); self.fixture(root)
                path = root / relative
                path.unlink()
                with self.assertRaises(ValueError):proof.inputs(root)
                path.symlink_to(proof.ROOT / relative)
                with self.assertRaises(ValueError):proof.inputs(root)

    def test_each_context_change_invalidates_and_no_credentials_accepted(self):
        before = proof.snapshot(proof.ROOT, CONTEXT)
        for key, value in (('actor', 'Different'), ('authentication', 'gh-cli'),
                           ('repository_visibility', 'public'), ('configuration_digest', 'sha256:' + 'c' * 64)):
            current = proof.snapshot(proof.ROOT, dict(CONTEXT, **{key: value}))
            self.assertTrue(proof.compare(before, current)['context_changed'])
            self.assertFalse(proof.compare(before, current)['input_binding_matches'])
        for invalid in ({**CONTEXT, 'token': 'DO-NOT-RETAIN'}, {}, {**CONTEXT, 'actor': 'https://user:secret@host'}):
            with self.assertRaises(ValueError):proof.context(invalid)

    def test_invalid_snapshot_missing_input_or_symlink_fail_closed(self):
        before = proof.snapshot(proof.ROOT, CONTEXT)
        for key, value in (('scope', 'production-accepted'), ('schema', True),
                           ('repository', 'foreign/repo'), ('files', {'x': 'bad'})):
            changed = copy.deepcopy(before); changed[key] = value
            with self.assertRaises(ValueError):proof.compare(changed, before)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); self.fixture(root)
            path = root / proof.WORKFLOWS[0]
            path.unlink()
            with self.assertRaises(ValueError):proof.inputs(root)
            path.symlink_to(proof.ROOT / proof.WORKFLOWS[0])
            with self.assertRaises(ValueError):proof.inputs(root)


if __name__ == '__main__':unittest.main()
