#!/usr/bin/env python3
"""Real native helper admission; receipts are fixtures, not authenticated proof."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HELPER = os.environ['SYMPHONY_TEST_HOST_HELPER']
IMAGE = 'sha256:' + 'a' * 64


def receipt(provider='codex', arch='arm64'):
    config = {'User': '', 'WorkingDir': '/opt/symphony/elixir',
              'Entrypoint': ['/usr/local/bin/symphony-runner'], 'Env': ['HOME=/root'],
              'Labels': {'com.symphony.agent': provider, 'com.symphony.protocol': '1',
                         'org.opencontainers.image.revision': 'b' * 40}}
    if provider == 'claude':
        config.update(User='11001:11001', WorkingDir='/var/lib/symphony',
                      Entrypoint=['/usr/bin/tini', '-g', '--', '/usr/local/bin/symphony-claude-runner'],
                      Env=['HOME=/home/claude', 'CLAUDE_CONFIG_DIR=/home/claude/.claude', 'SYMPHONY_CONTAINERIZED=1'])
    return [{'Id': IMAGE, 'Os': 'linux', 'Architecture': arch, 'Config': config}]


class SelectedImage(unittest.TestCase):
    def check(self, value, accepted, provider='codex', arch='arm64'):
        result = subprocess.run([HELPER, 'validate-selected-image', provider, arch, IMAGE],
                                input=json.dumps(value), text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, accepted, result.stderr)
        return result

    def test_both_providers_and_container_architectures(self):
        for provider in ('codex', 'claude'):
            for arch, cpu in (('arm64', 'arm64'), ('x86_64', 'amd64')):
                with self.subTest(provider=provider, arch=arch):
                    self.check(receipt(provider, cpu), True, provider, arch)

    def test_refuse_wrong_identity_platform_provider_protocol_and_runtime(self):
        base = receipt()
        for key, value in (('Id', 'sha256:' + 'c'*64), ('Os', 'darwin'), ('Architecture', 'amd64')):
            changed = copy.deepcopy(base); changed[0][key] = value
            self.check(changed, False)
        for key in ('com.symphony.agent', 'com.symphony.protocol', 'org.opencontainers.image.revision'):
            changed = copy.deepcopy(base); changed[0]['Config']['Labels'][key] = 'invalid'
            self.check(changed, False)
        for key, value in (('User', '11001:11001'), ('WorkingDir', '/tmp'), ('Entrypoint', ['/bin/sh']),
                           ('Env', ['HOME=/root', 'HOME=/foreign'])):
            changed = copy.deepcopy(base); changed[0]['Config'][key] = value
            self.check(changed, False)
        for value in ([], base + base, {}): self.check(value, False)

    def test_refuse_claude_layout_regressions(self):
        for key, value in (('User', '0:0'), ('WorkingDir', '/tmp'), ('Env', ['HOME=/root'])):
            changed = receipt('claude'); changed[0]['Config'][key] = value
            self.check(changed, False, 'claude')

    def test_existing_runner_storage_modes_preserve_ownership_without_chmod(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'runners'; root.mkdir()
            for mode, accepted in ((0o700, True), (0o755, True), (0o775, False), (0o777, False)):
                root.chmod(mode)
                result = subprocess.run([HELPER, 'selection-storage-directory', str(root)], capture_output=True)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
                self.assertEqual(root.stat().st_mode & 0o777, mode)
            root.chmod(0o700)
            link = Path(folder)/'alias'; link.symlink_to(root)
            self.assertNotEqual(subprocess.run([HELPER, 'selection-storage-directory', str(link)], capture_output=True).returncode, 0)

    def test_selected_record_is_private_owned_regular_and_strict(self):
        record = dict(schema_version=1, provider='codex', image_id=IMAGE, reference='team/runner:local',
                      runtime_revision='b'*40, platform='linux/arm64', protocol=1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'selection.json'
            def check(value, accepted, mode=0o600):
                path.write_text(json.dumps(value)); path.chmod(mode)
                result = subprocess.run([HELPER, 'validate-environment-selection', str(path)], capture_output=True)
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
            check(record, True)
            for field, value in (('schema_version', True), ('protocol', True), ('provider', 'foreign'),
                                 ('image_id', 'mutable:tag'), ('runtime_revision', 'unknown'),
                                 ('reference', 'x"; echo secret'), ('platform', 'darwin/arm64')):
                check(dict(record, **{field: value}), False)
            check(dict(record, extra=True), False)
            check(record, False, 0o644)
            check(record, True)
            link = Path(folder)/'link.json'; link.symlink_to(path)
            self.assertNotEqual(subprocess.run([HELPER, 'validate-environment-selection', str(link)], capture_output=True).returncode, 0)


if __name__ == '__main__': unittest.main()
