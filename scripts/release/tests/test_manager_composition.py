"""Current-manager source binding and original runner identity are independent."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'scripts/release'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = load('manager_lifecycle', ROOT / 'scripts/release/package-lifecycle.py')
PREPARE = load('manager_prepare', ROOT / 'scripts/release/prepare-manager-inputs.py')
FINALIZE = load('manager_finalize', ROOT / 'scripts/release/finalize-manager-packages.py')
FIXTURE = load('origin_fixture', Path(__file__).with_name('test_component_provenance.py'))


class ManagerComposition(unittest.TestCase):
    def test_shared_assembly_checks_actual_helper_source_cpu_and_unsupported_hosts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'; (source / 'scripts/release').mkdir(parents=True)
            packer = source / 'scripts/release/package.sh'
            packer.write_text('#!/bin/sh\nprintf packed > "$2/actual-invocation"\n')
            subprocess.run(['git', 'init', '--quiet', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'add', 'scripts'], check=True)
            subprocess.run(['git', '-C', str(source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'source'], check=True)
            revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
            helper = root / 'qualified-helper'
            helper.write_text('#!/bin/sh\ncase "$1" in build-revision) echo "$HELPER_REVISION" ;; host-platform) echo macos ;; esac\n')
            helper.chmod(0o755)
            tools = root / 'tools'; tools.mkdir()
            (tools / 'uname').write_text('#!/bin/sh\ncase "$1" in -s) echo Darwin ;; -m) echo "$FIXTURE_CPU" ;; esac\n')
            (tools / 'lipo').write_text('#!/bin/sh\necho "$HELPER_CPU"\n')
            for tool in tools.iterdir(): tool.chmod(0o755)
            manifest = root / 'internal.json'; manifest.write_text('{}')
            env = dict(os.environ, PATH=str(tools)+os.pathsep+os.environ['PATH'],
                       HELPER_REVISION=revision, FIXTURE_CPU='arm64', HELPER_CPU='arm64')
            for name, changed, accepted in (('good', {}, True), ('wrong-source', {'HELPER_REVISION': 'a'*40}, False),
                                             ('wrong-slice', {'HELPER_CPU': 'arm64 x86_64'}, False),
                                             ('unsupported-host', {'FIXTURE_CPU': 'x86_64'}, False)):
                with self.subTest(name=name):
                    output = root / name
                    result = subprocess.run(['bash', str(ROOT / 'scripts/release/build-manager-package.sh'), str(manifest),
                                             str(output), 'macos', str(source), revision, str(helper)],
                                            env=dict(env, **changed), capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, accepted, result.stderr)
                    self.assertEqual((output / 'actual-invocation').exists(), accepted)
                    if name == 'unsupported-host': self.assertFalse(output.exists())

    def test_negative_image_cleanup_requires_owned_config_and_positive_absence(self):
        reference, token, identifier = 'symphony-negative:test', 'owned-fixture-token', 'sha256:'+'d'*64
        def run_for(label, unavailable=False, changed_after=False):
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if args[2] == 'rm':
                    self.assertEqual(args[-1], identifier)
                    return subprocess.CompletedProcess(args, 0, 'removed', '')
                if len(calls) == 1:
                    return subprocess.CompletedProcess(args, 0, json.dumps([{'Id': identifier, 'Config': {
                        'Labels': {'com.symphony.lifecycle-negative': label}}}]), '')
                if unavailable:
                    return subprocess.CompletedProcess(args, 1, '', 'Cannot connect to Docker daemon')
                if changed_after:
                    return subprocess.CompletedProcess(args, 0, json.dumps([{'Id': 'sha256:'+'e'*64}]), '')
                return subprocess.CompletedProcess(args, 1, '[]', 'Error response from daemon: No such image: '+args[-1])
            return run, calls
        run, calls = run_for(token)
        LIFECYCLE.cleanup_negative_image(run, reference, token)
        self.assertEqual(calls[1], ['docker', 'image', 'rm', identifier])
        for label, unavailable, changed in [('foreign', False, False), (token, True, False), (token, False, True)]:
            with self.subTest(label=label, unavailable=unavailable, changed=changed):
                run, calls = run_for(label, unavailable, changed)
                with self.assertRaises(AssertionError):
                    LIFECYCLE.cleanup_negative_image(run, reference, token)
                if label == 'foreign':
                    self.assertFalse(any('rm' in call for call in calls))

    def test_single_manager_mode_requires_current_source_and_no_invented_previous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = {'source_revision': 'a' * 40}
            (root / 'release.json').write_text(json.dumps(manifest))
            self.assertEqual(LIFECYCLE.package_selections(root, True, 'a' * 40), [(root, manifest)])
            with self.assertRaisesRegex(AssertionError, 'exact manager source'):
                LIFECYCLE.package_selections(root, True, 'b' * 40)
            with self.assertRaises(FileNotFoundError):
                LIFECYCLE.package_selections(root, False, 'a' * 40)
            (root / 'previous').mkdir()
            (root / 'previous/release.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(AssertionError, 'distinct actual'):
                LIFECYCLE.package_selections(root, False, 'a' * 40)

    def test_loaded_runner_checks_original_source_config_protocol_and_inputs(self):
        record = {'platform': 'linux/amd64', 'source_revision': 'b' * 40,
                  'image_config': 'sha256:' + 'c' * 64, 'protocol': 1, 'input_identity': 'd' * 64}
        config = {'Architecture': 'amd64', 'Id': record['image_config'], 'Config': {'Labels': {
            'org.opencontainers.image.revision': record['source_revision'], 'com.symphony.agent': 'codex',
            'com.symphony.architecture': 'amd64', 'com.symphony.protocol': '1',
            'com.symphony.inputs.sha256': record['input_identity']}}}
        LIFECYCLE.verify_loaded_component(config, record, 'codex', True)
        for field, value in (('source_revision', 'a' * 40), ('image_config', 'sha256:' + 'e' * 64),
                             ('protocol', 2), ('input_identity', 'f' * 64)):
            with self.subTest(field=field), self.assertRaises(AssertionError):
                LIFECYCLE.verify_loaded_component(config, dict(record, **{field: value}), 'codex', True)

    def fixture(self, root):
        (root / 'scripts/release').mkdir(parents=True)
        (root / 'scripts/release/manager-version').write_text('0.0.4\n')
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
        subprocess.run(['git', '-C', str(root), 'add', 'scripts'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '--quiet', '-m', 'actual manager source'], check=True)
        revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        components = {}
        for provider in ('codex', 'claude'):
            components[provider] = {}
            for architecture in ('arm64', 'x86_64'):
                record = FIXTURE.record(provider, architecture)
                components[provider][architecture] = {'action': 'reuse', 'record': record,
                    'input_identity': record['input_identity'], 'origin': {'original': record['image']}}
        return {'planning_complete': True, 'manager_source_revision': revision, 'components': components}

    def test_metadata_retains_original_runner_source_under_actual_current_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; root.mkdir()
            plan = self.fixture(root)
            output = Path(directory) / 'metadata'
            with patch.object(PREPARE, 'identity', return_value={'input_identity': 'd' * 64}):
                PREPARE.prepare(root, plan, None, output, None, lambda record: {'original': record['image']})
            value = json.loads((output / 'release.json').read_text())
            self.assertEqual(value['source_revision'], plan['manager_source_revision'])
            self.assertEqual(value['schema_version'], 2)
            for group in value['runners'].values():
                for record in group.values():
                    self.assertEqual(record['source_revision'], 'b' * 40)
                    self.assertNotEqual(record['source_revision'], value['source_revision'])
            archives = Path(directory) / 'archives'; archives.mkdir()
            for platform, content in (('macos', b'actual mac native archive fixture'), ('linux_x86_64', b'actual linux native archive fixture')):
                (archives / value['bundles'][platform]['filename']).write_bytes(content)
            final = FINALIZE.finalize(output / 'release.json', archives, plan['manager_source_revision'])
            self.assertEqual(final['runners'], value['runners'])
            self.assertEqual(final['bundle']['sha256'], hashlib.sha256(b'actual mac native archive fixture').hexdigest())

    def test_legacy_chain_requires_three_actual_sources_and_real_bridge_schemas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'previous').mkdir(); (root/'legacy').mkdir()
            for folder, source, schema in ((root, 'c'*40, 2), (root/'previous', 'b'*40, 1), (root/'legacy', 'a'*40, 1)):
                (folder/'release.json').write_text(json.dumps({'source_revision': source, 'schema_version': schema}))
            selected = LIFECYCLE.package_selections(root, False, 'c'*40, True)
            self.assertEqual([record['source_revision'] for _,record in selected], ['a'*40, 'b'*40, 'c'*40])
            (root/'legacy/release.json').write_text(json.dumps({'source_revision': 'b'*40, 'schema_version': 1}))
            with self.assertRaisesRegex(AssertionError, 'three distinct'):
                LIFECYCLE.package_selections(root, False, 'c'*40, True)
            (root/'legacy/release.json').write_text(json.dumps({'source_revision': 'a'*40, 'schema_version': 2}))
            with self.assertRaisesRegex(AssertionError, 'actual schema1'):
                LIFECYCLE.package_selections(root, False, 'c'*40, True)

    def test_separate_prior_is_verified_and_aliased_before_current_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            images = Path(directory)
            (images / 'previous').mkdir()
            for folder, content in ((images, b'actual-current-archive-fixture'), (images/'previous', b'actual-previous-archive-fixture')):
                (folder/'codex-x86_64.docker.tar.gz').write_bytes(content)
                (folder/'codex-x86_64.sha256').write_text(hashlib.sha256(content).hexdigest()+'  codex-x86_64.docker.tar.gz\n')
            record = FIXTURE.record('codex', 'x86_64')
            record['archive_tag'] = 'localhost:5000/symphony-codex:candidate'
            original = copy.deepcopy(record)
            config = {'Id': record['image_config'], 'Architecture': 'amd64', 'Config': {'Labels': {
                'com.symphony.agent': 'codex', 'com.symphony.architecture': 'amd64', 'org.opencontainers.image.revision': record['source_revision'],
                'com.symphony.inputs.sha256': record['input_identity'], 'com.symphony.protocol': '1'}}}
            calls = []
            def run(args, **kwargs):
                calls.append((args, kwargs.get('stdin').read() if kwargs.get('stdin') else None))
                return subprocess.CompletedProcess(args, 0, json.dumps([config]) if 'inspect' in args else '')
            selected = LIFECYCLE.stage_separate_previous(run, images, 'codex', {'schema_version': 2, 'runners': {'codex': {'x86_64': record}}})
            LIFECYCLE.load_original_archive(run, images, 'codex')
            self.assertEqual(selected, record['image_config'])
            self.assertEqual(calls[0][1], b'actual-previous-archive-fixture')
            self.assertEqual(calls[2][0], ['docker', 'tag', record['archive_tag'], 'localhost:5000/symphony-codex:previous'])
            self.assertEqual(calls[-1][1], b'actual-current-archive-fixture')
            self.assertEqual(record, original, 'local role alias mutated original qualification')
            (images/'previous/codex-x86_64.sha256').write_text('0'*64+'  codex-x86_64.docker.tar.gz\n')
            with self.assertRaisesRegex(AssertionError, 'checksum differs'):
                LIFECYCLE.stage_separate_previous(run, images, 'codex', {'schema_version': 2, 'runners': {'codex': {'x86_64': record}}})

    def test_distribution_alias_preserves_complete_original_records_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; root.mkdir()
            plan = self.fixture(root)
            output = Path(directory) / 'metadata'
            originals = copy.deepcopy(plan['components'])
            with patch.object(PREPARE, 'identity', return_value={'input_identity': 'd' * 64}):
                PREPARE.prepare(root, plan, None, output, None, lambda record: {'original': record['image']}, True)
            manifest = json.loads((output / 'release.json').read_text())
            for provider in ('codex', 'claude'):
                for architecture in ('arm64', 'x86_64'):
                    record = originals[provider][architecture]['record']
                    selected = dict(manifest['runners'][provider][architecture], provider=provider, architecture=architecture)
                    self.assertEqual(selected, record)
                    self.assertEqual(manifest['distribution'][provider][architecture],
                        'localhost:5000/symphony-distribution-'+provider+'@'+record['image'].split('@',1)[1])

    def test_built_join_cannot_replace_a_valid_original_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; root.mkdir()
            plan = self.fixture(root)
            original = Path(directory) / 'original'
            verify = lambda record: {'original': record['image']}
            with patch.object(PREPARE, 'identity', return_value={'input_identity': 'd' * 64}):
                PREPARE.prepare(root, plan, None, original, None, verify)
                built = json.loads((original / 'release.json').read_text())
                replacement = copy.deepcopy(built)
                replacement['runners']['codex']['arm64']['image'] = 'localhost:5000/symphony-codex@sha256:'+'e'*64
                replacement['runners']['codex']['arm64']['qualification']['image'] = replacement['runners']['codex']['arm64']['image']
                output = Path(directory) / 'changed-record'
                with self.assertRaisesRegex(ValueError, 'component changed after planning'):
                    PREPARE.prepare(root, plan, replacement, output, None, verify)
                self.assertFalse(output.exists())
                output = Path(directory) / 'changed-origin'
                with self.assertRaisesRegex(ValueError, 'origin changed after planning'):
                    PREPARE.prepare(root, plan, built, output, None, lambda record: {'original': record['image'], 'other_job': True})
                self.assertFalse(output.exists())

    def test_changed_or_unavailable_component_never_publishes_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'source'; root.mkdir()
            plan = self.fixture(root)
            output, payloads = Path(directory) / 'metadata', Path(directory) / 'payloads'
            with patch.object(PREPARE, 'identity', return_value={'input_identity': 'd' * 64}):
                broken = copy.deepcopy(plan); broken['components']['codex']['arm64']['action'] = 'build'
                with self.assertRaisesRegex(ValueError, 'actual native'):
                    PREPARE.prepare(root, broken, None, output, None, lambda record: {'original': record['image']})
                self.assertFalse(output.exists())
                def unavailable(*args, **kwargs):
                    raise ValueError('actual original payload corrupt')
                with patch.object(PREPARE, 'retrieve', side_effect=unavailable), self.assertRaisesRegex(ValueError, 'payload corrupt'):
                    PREPARE.prepare(root, plan, None, output, payloads, lambda record: {'original': record['image']})
                self.assertFalse(output.exists()); self.assertFalse(payloads.exists())
            self.assertEqual(list(Path(directory).iterdir()), [root])


if __name__ == '__main__':
    unittest.main()
