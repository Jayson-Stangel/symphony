"""Exercise the actual workflow guards; no dispatch, secrets, or Apple calls."""
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]


def jobs(filename):
    text = (ROOT / '.github/workflows' / filename).read_text().split('\njobs:\n', 1)[1]
    return dict(re.findall(r'(?ms)^  ([a-z][a-z0-9-]*):\n(.*?)(?=^  [a-z][a-z0-9-]*:|\Z)', text))


def guard(block):
    match = re.search(r'^    if: (.+)$', block, re.M)
    value = match.group(1)
    if value == '>-':
        value = ' '.join(re.match(r'((?:      .+\n)+)', block[match.end()+1:]).group(1).splitlines())
    if value.startswith('${{ '):
        value = value[4:-3]
    return value


def evaluate(expression, *, event='workflow_dispatch', ref='refs/heads/dev', source='a'*40,
             approved='a'*40, enabled=True, repository='Jayson-Stangel/symphony',
             caller='runner-integration.yml'):
    # These actual guards use only boolean operators, comparisons and format.
    expression = re.sub(r'!(?!=)', ' not ', expression.replace('&&', ' and ').replace('||', ' or ')).strip()
    context = {'github': SimpleNamespace(event_name=event, ref=ref, sha=source, repository=repository,
                                         workflow_ref=f'{repository}/.github/workflows/{caller}@{ref}'),
               'inputs': SimpleNamespace(sign_macos=enabled, approved_revision=approved),
               'format': lambda template, *values: template.format(*values)}
    return bool(eval(expression, {'__builtins__': {}}, context))


class SigningEntrypointTest(unittest.TestCase):
    def test_production_resume_admission_precedes_credentials_and_retains_only_fixed_state(self):
        text = (ROOT / '.github/workflows/macos-production-signing.yml').read_text()
        worker = jobs('macos-production-signing.yml')['sign']
        expression = guard(worker)
        self.assertTrue(evaluate(expression, ref='refs/heads/main'))
        for changed in ({'ref': 'refs/heads/dev'}, {'approved': 'b' * 40}, {'repository': 'fork/symphony'}):
            self.assertFalse(evaluate(expression, **dict({'ref': 'refs/heads/main'}, **changed)))
        self.assertIn('runs-on: symphony-macos14', worker)
        self.assertIn('environment: macos-production-signing', worker)
        self.assertIn('cancel-in-progress: false', text)
        imported = worker.split('      - name: Import immutable', 1)[1].split('      - name: Reuse established', 1)[0]
        self.assertNotIn('secrets.', imported)
        self.assertLess(imported.index('signing-ready'), imported.index('production_release.py qualify'))
        self.assertLess(imported.index('production_release.py qualify'), imported.index('production_release.py checkpoint-inputs'))
        self.assertIn('macos_distribution.py resume', worker)
        retained = worker.split('      - name: Retain exact private signing checkpoint', 1)[1]
        for name in ('checkpoint.json', 'signed-updater.tar.gz', 'payload.zip', 'outer.dmg', 'preparing.dmg', 'stapled.dmg',
                     'package-source.txt', 'package-diagnostics.json'):
            self.assertIn('production-signed.checkpoint/' + name, retained)
        for private in ('identity.p12', 'signing.keychain', '.keychain-cleanup', 'checkpoint/*'):
            self.assertNotIn(private, retained)

    def test_actual_caller_and_worker_guards_refuse_unapproved_events_refs_and_heads(self):
        caller = guard(jobs('runner-integration.yml')['sign-macos'])
        worker = guard(jobs('macos-signing.yml')['sign'])
        self.assertTrue(evaluate(caller))
        self.assertTrue(evaluate(worker))
        for changed in ({'event': 'push'}, {'event': 'pull_request'}, {'ref': 'refs/heads/main'},
                        {'ref': 'refs/heads/codex/unreviewed'}, {'approved': 'b'*40}):
            self.assertFalse(evaluate(caller, **changed), changed)
            self.assertFalse(evaluate(worker, **changed), changed)
        self.assertFalse(evaluate(caller, enabled=False))
        self.assertFalse(evaluate(worker, repository='fork/symphony'))
        self.assertFalse(evaluate(worker, caller='unreviewed.yml'))
        self.assertTrue(evaluate(worker, caller='macos-signing.yml'))

    def test_signing_mode_short_circuits_every_normal_job_including_always_jobs(self):
        blocks = jobs('runner-integration.yml')
        normal = {name: block for name, block in blocks.items() if name != 'sign-macos'}
        self.assertIn('listener-and-cleanup', normal)
        self.assertIn('runners', normal)
        for name, block in normal.items():
            # Undefined needs/always deliberately raise if the signing guard
            # fails to short-circuit before ordinary qualification expressions.
            self.assertFalse(evaluate(guard(block), enabled=True), name)
        self.assertTrue(evaluate(guard(normal['changes']), enabled=False))

    def test_same_commit_reusable_call_keeps_secrets_inside_protected_worker(self):
        caller = jobs('runner-integration.yml')['sign-macos']
        worker = jobs('macos-signing.yml')['sign']
        self.assertIn('uses: ./.github/workflows/macos-signing.yml', caller)
        self.assertIn('approved_revision: ${{ inputs.approved_revision }}', caller)
        self.assertIn('qualified_run: ${{ inputs.qualified_run }}', caller)
        expected_secrets = {'MACOS_SIGNING_P12_BASE64', 'MACOS_SIGNING_P12_PASSWORD',
                            'MACOS_NOTARY_APPLE_ID', 'MACOS_NOTARY_APP_PASSWORD'}
        forwarded = re.findall(r'^      ([A-Z0-9_]+): \$\{\{ secrets\.([A-Z0-9_]+) \}\}$', caller, re.M)
        self.assertEqual(dict(forwarded), {name: name for name in expected_secrets})
        self.assertNotIn('secrets: inherit', caller)
        self.assertIn('environment: macos-signing', worker)
        self.assertIn('MACOS_SIGNING_P12_BASE64: ${{ secrets.MACOS_SIGNING_P12_BASE64 }}', worker)
        self.assertNotIn('packages: write', caller + worker)
        text = (ROOT / '.github/workflows/runner-integration.yml').read_text()
        signing_input = text.split('      sign_macos:\n', 1)[1].split('      approved_revision:', 1)[0]
        self.assertIn('default: false', signing_input)
        reusable = (ROOT / '.github/workflows/macos-signing.yml').read_text().split('  workflow_dispatch:', 1)[0]
        self.assertIn('workflow_call:', reusable)
        self.assertEqual(reusable.count('required: true'), 6)
        declarations = reusable.split('    secrets:\n', 1)[1]
        declared = re.findall(r'^      ([A-Z0-9_]+):\n        required: true$', declarations, re.M)
        self.assertEqual(set(declared), expected_secrets)
        self.assertEqual(len(declared), 4)
