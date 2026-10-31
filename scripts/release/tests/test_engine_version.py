"""Portable producer/consumer version checks; Docker responses here are fixtures."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import engine_version
import record as producer
import qualified_graph
import artifacts
from test_artifacts import manifest

ROOT = Path(__file__).resolve().parents[3]


class EngineVersionTest(unittest.TestCase):
    def test_single_authority_and_malformed_declarations(self):
        self.assertEqual(engine_version.read(ROOT), '0.0.5')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'elixir').mkdir()
            for text, valid in [('  version: "1.2.3",\n', True),
                                ('  version: "1.2.3-rc.1",\n', True),
                                ('  version: "01.2.3",\n', False),
                                ('  version: "1.2.3-01",\n', False),
                                ('  version: "",\n', False),
                                ('  version: "development",\n', False),
                                ('  version: version(),\n', False),
                                ('  version: "1.2.3",\n  version: "2.0.0",\n', False),
                                ('  version: "1.2.3",\n  version: version(),\n', False)]:
                (root / 'elixir/mix.exs').write_text(text)
                if valid:
                    self.assertTrue(engine_version.read(root))
                else:
                    with self.subTest(text=text), self.assertRaises(ValueError):
                        engine_version.read(root)

    def produce(self, label='0.0.5', application='0.0.5', revision='a'*40):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'record.json'
            labels = {'org.opencontainers.image.version': label,
                      'org.opencontainers.image.revision': revision,
                      'com.symphony.agent': 'codex', 'com.symphony.architecture': 'amd64'}
            config = {'Id': 'sha256:'+'b'*64, 'Architecture': 'amd64', 'Config': {'Labels': labels}}
            calls = []
            def docker(*args):
                calls.append(args)
                if args[:2] == ('image', 'inspect'):
                    return json.dumps([config])
                return application if args[-2] == '-e' else 'tool 1.2.3'
            argv = ['record.py', '--image', 'example.invalid/codex@sha256:'+'c'*64,
                    '--provider', 'codex', '--architecture', 'x86_64', '--revision', 'a'*40,
                    '--source-root', str(ROOT), '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch.object(producer, 'docker', docker):
                producer.main()
            return json.loads(output.read_text()), calls

    def test_source_label_application_and_record_agree(self):
        value, calls = self.produce()
        self.assertEqual(value['engine_version'], engine_version.read(ROOT))
        self.assertEqual(value['source_revision'], 'a'*40)
        self.assertIn('@sha256:', value['image'])
        self.assertTrue(any('Application.spec' in call[-1] for call in calls))

    def test_bad_labels_source_and_compiled_application_refuse(self):
        for label, application, revision in [('0.0.4','0.0.5','a'*40),
                ('0.0.5','0.0.4','a'*40), ('0.0.5','', 'a'*40),
                ('0.0.5','0.0.5','d'*40), (None,'0.0.5','a'*40),
                ('bad','0.0.5','a'*40), (5,'0.0.5','a'*40)]:
            with self.subTest(label=label, application=application), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises((ValueError, SystemExit)):
                    self.produce(label, application, revision)

    def test_record_version_must_match_actual_immutable_config(self):
        value, _ = self.produce()
        config = {'architecture': 'amd64', 'os': 'linux', 'config': {'Labels': {
            'org.opencontainers.image.version': '0.0.5',
            'org.opencontainers.image.revision': 'a'*40,
            'com.symphony.agent': 'codex', 'com.symphony.architecture': 'amd64'}}}
        raw = json.dumps(config).encode()
        value['image_config'] = qualified_graph.digest(raw)
        qualified_graph.verify_config(raw, value)
        for version in ('0.0.4', None, 'invalid', 5):
            changed = dict(value, engine_version=version)
            with self.assertRaises(ValueError):
                qualified_graph.verify_config(raw, changed)
        # Historical absence remains absence; do not retrofit original evidence.
        value.pop('engine_version')
        qualified_graph.verify_config(raw, value)

    def test_package_metadata_rejects_malformed_versions_but_allows_independence(self):
        value = manifest()
        value['runners']['codex']['arm64']['engine_version'] = '0.0.4'
        artifacts.validate(value)
        for malformed in (None, 4, '', 'development', '01.0.4'):
            changed = copy.deepcopy(value)
            changed['runners']['codex']['arm64']['engine_version'] = malformed
            with self.assertRaises(ValueError):
                artifacts.validate(changed)
