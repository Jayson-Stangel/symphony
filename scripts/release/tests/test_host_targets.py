"""Future host policy is distinct from both retained Linux container architectures."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]


class HostTargets(unittest.TestCase):
    def test_only_supported_native_standalone_targets(self):
        hosts = json.loads((ROOT / 'scripts/release/host-targets.json').read_text())['include']
        self.assertEqual({h['target'] for h in hosts}, {'linux_x86_64', 'macos_arm64'})
        self.assertEqual(len(hosts), 2)
        self.assertEqual({(h['target'], h['runner']) for h in hosts},
                         {('linux_x86_64', 'ubuntu-24.04'), ('macos_arm64', 'macos-14')})
        workflow = (ROOT / '.github/workflows/burrito-release.yml').read_text()
        self.assertEqual(workflow.count('matrix: ${{ fromJSON(needs.host-policy.outputs.matrix) }}'), 2)
        self.assertNotIn('macos-15-intel', workflow)
        self.assertIn('engine_only:', workflow)
        self.assertIn('!inputs.controller_only && !inputs.engine_only', workflow)
        self.assertNotIn('Download linux_arm64', workflow)
        self.assertNotIn('Download macos_x86_64', workflow)
        config = (ROOT / 'elixir/mix.exs').read_text()
        self.assertIn('macos_arm64: [os: :darwin, cpu: :aarch64]', config)
        self.assertIn('linux_x86_64: [os: :linux, cpu: :x86_64]', config)
        self.assertNotIn('macos_x86_64:', config)
        self.assertNotIn('linux_arm64:', config)

    def test_actual_matrix_output_is_valid_actions_output(self):
        command = 'python3 scripts/release/select-hosts.py >> "$GITHUB_OUTPUT"'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            subprocess.run(['bash', '-e', '-c', command], cwd=ROOT,
                           env=dict(os.environ, GITHUB_OUTPUT=str(output)), check=True)
            lines = output.read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(output.read_bytes().endswith(b'\n'))
            key, matrix = lines[0].split('=', 1)
            self.assertEqual(key, 'matrix')
            self.assertEqual(len(json.loads(matrix)['include']), 2)

    def test_filtered_native_policy_retains_one_matching_host(self):
        for platform, target in [('macos', 'macos_arm64'), ('linux_x86_64', 'linux_x86_64')]:
            with self.subTest(platform=platform):
                output = subprocess.check_output(['python3', str(ROOT/'scripts/release/select-hosts.py'), '--platform', platform], text=True)
                matrix = json.loads(output.strip().split('=', 1)[1])
                self.assertEqual([host['target'] for host in matrix['include']], [target])
        package = (ROOT/'.github/workflows/package-checks.yml').read_text()
        self.assertNotIn('macos-15-intel', package)
        self.assertIn('fromJSON(needs.macos-host-policy.outputs.matrix)', package)

    def test_helper_builder_compiles_only_the_matching_supported_native_target(self):
        # Execute the real build wrapper with a recording compiler. These prove
        # selection/refusal, while actual native compiler execution remains CI.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'tools'
            fake.mkdir()
            (fake / 'uname').write_text('#!/bin/bash\ncase "$1" in -s) echo "$TEST_OS" ;; -m) echo "$TEST_ARCH" ;; esac\n')
            (fake / 'swiftc').write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$TEST_ARGS"\nwhile [ "$1" != -o ]; do shift; done\ntouch "$2"\n')
            build_bin = root / 'swift-bin'
            build_bin.mkdir()
            (build_bin / 'symphony-host').write_text('fixture')
            (fake / 'swift').write_text('#!/bin/bash\nprintf "%s\\n" "$@" >> "$TEST_ARGS"\ncase "$*" in *--show-bin-path*) echo "$TEST_BUILD_BIN" ;; esac\n')
            for path in fake.iterdir():
                path.chmod(0o755)
            for system, architecture, target in [('Darwin', 'arm64', 'arm64-apple-macosx14.0'),
                                                  ('Linux', 'x86_64', 'x86_64-swift-linux-musl'),
                                                  ('Darwin', 'x86_64', None), ('Linux', 'aarch64', None)]:
                with self.subTest(system=system, architecture=architecture):
                    output = root / (system + architecture) / 'helper'
                    args = root / 'arguments'
                    if args.exists():
                        args.unlink()
                    env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ['PATH'],
                               TEST_OS=system, TEST_ARCH=architecture, TEST_ARGS=str(args), TEST_BUILD_BIN=str(build_bin))
                    result = subprocess.run(['bash', str(ROOT / 'scripts/release/build-host-helper.sh'), str(output)],
                                            env=env, text=True, capture_output=True)
                    if target:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn(target, args.read_text().splitlines())
                        self.assertTrue(output.is_file())
                        self.assertNotIn('x86_64-apple', args.read_text())
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('only macOS ARM64 and Linux x86-64', result.stderr)
                        self.assertFalse(args.exists())
                        self.assertFalse(output.parent.exists())

    def test_packaged_launcher_refuses_unsupported_host_before_native_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / 'bundle'
            (bundle / 'scripts').mkdir(parents=True)
            (bundle / 'libexec').mkdir()
            (bundle / 'scripts/symphony').write_text((ROOT / 'scripts/symphony').read_text())
            (bundle / 'release.json').write_text('{}')
            (bundle / 'libexec/symphony-host').write_text('#!/bin/bash\necho helper >> "$TEST_CALLS"\nexit 99\n')
            (bundle / 'libexec/symphony-host').chmod(0o755)
            (bundle / 'scripts/symphony-package.sh').write_text('echo package >> "$TEST_CALLS"\nexit 42\n')
            fake = root / 'tools'
            fake.mkdir()
            (fake / 'uname').write_text('#!/bin/bash\ncase "$1" in -s) echo "$TEST_OS" ;; -m) echo "$TEST_ARCH" ;; esac\n')
            (fake / 'uname').chmod(0o755)
            current = root / 'current'
            current.symlink_to(bundle, target_is_directory=True)
            canary = root / 'project-data'
            canary.write_bytes(b'preserve project data\x00')
            for system, architecture, supported in [('Darwin', 'arm64', True), ('Linux', 'x86_64', True),
                                                       ('Darwin', 'x86_64', False), ('Linux', 'aarch64', False)]:
                with self.subTest(system=system, architecture=architecture):
                    calls = root / 'calls'
                    if calls.exists():
                        calls.unlink()
                    env = dict(os.environ, SYMPHONY_HOME=str(bundle),
                               PATH=str(fake) + os.pathsep + os.environ['PATH'],
                               TEST_OS=system, TEST_ARCH=architecture, TEST_CALLS=str(calls))
                    result = subprocess.run(['bash', str(current / 'scripts/symphony'), '_complete-package-update'],
                                            env=env, text=True, capture_output=True)
                    if supported:
                        self.assertEqual(result.returncode, 42)
                        self.assertEqual(calls.read_text(), 'package\n')
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('only macOS ARM64 and Linux x86-64', result.stderr)
                        self.assertFalse(calls.exists())
                    self.assertEqual(current.readlink(), bundle)
                    self.assertEqual(canary.read_bytes(), b'preserve project data\x00')

    def test_both_linux_container_architectures_and_providers_remain(self):
        workflow = (ROOT / '.github/workflows/runner-release.yml').read_text()
        self.assertIn('provider: [codex, claude]', workflow)
        self.assertIn('architecture: [arm64, x86_64]', workflow)
        self.assertIn('runner: ubuntu-24.04-arm', workflow)
        self.assertIn('runner: ubuntu-24.04', workflow)


if __name__ == '__main__':
    unittest.main()
