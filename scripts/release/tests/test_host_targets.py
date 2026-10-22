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
        self.assertNotIn('Download linux_arm64', workflow)
        self.assertNotIn('Download macos_x86_64', workflow)
        config = (ROOT / 'elixir/mix.exs').read_text()
        self.assertIn('macos_arm64: [os: :darwin, cpu: :aarch64]', config)
        self.assertIn('linux_x86_64: [os: :linux, cpu: :x86_64]', config)
        self.assertNotIn('macos_x86_64:', config)
        self.assertNotIn('linux_arm64:', config)

    def test_actual_matrix_output_is_valid_actions_output(self):
        workflow = (ROOT / '.github/workflows/burrito-release.yml').read_text()
        block = workflow.split('        name: Select supported standalone host targets\n        run: |\n', 1)[1].split('\n\n', 1)[0]
        command = '\n'.join(line[10:] for line in block.splitlines())
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

    def test_both_linux_container_architectures_and_providers_remain(self):
        workflow = (ROOT / '.github/workflows/runner-release.yml').read_text()
        self.assertIn('provider: [codex, claude]', workflow)
        self.assertIn('architecture: [arm64, x86_64]', workflow)
        self.assertIn('runner: ubuntu-24.04-arm', workflow)
        self.assertIn('runner: ubuntu-24.04', workflow)


if __name__ == '__main__':
    unittest.main()
