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
        self.assertNotIn('secrets:', caller)
        self.assertIn('environment: macos-signing', worker)
        self.assertIn('MACOS_SIGNING_P12_BASE64: ${{ secrets.MACOS_SIGNING_P12_BASE64 }}', worker)
        self.assertNotIn('packages: write', caller + worker)
        text = (ROOT / '.github/workflows/runner-integration.yml').read_text()
        signing_input = text.split('      sign_macos:\n', 1)[1].split('      approved_revision:', 1)[0]
        self.assertIn('default: false', signing_input)
        reusable = (ROOT / '.github/workflows/macos-signing.yml').read_text().split('  workflow_dispatch:', 1)[0]
        self.assertIn('workflow_call:', reusable)
        self.assertEqual(reusable.count('required: true'), 2)
