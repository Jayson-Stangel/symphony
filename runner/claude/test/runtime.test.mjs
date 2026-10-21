import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, copyFileSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { runtime } from '../runtime.mjs';

const manifest = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));

test('runtime pins derive from the production npm manifest', () => {
  assert.equal(runtime.sdk, manifest.dependencies['@anthropic-ai/claude-agent-sdk']);
  assert.equal(runtime.cli, manifest.dependencies['@anthropic-ai/claude-code']);
});

test('a compatible pin update needs only manifest/installed dependencies, with native checks still enforced', async t => {
  const root = mkdtempSync(join(tmpdir(), 'symphony-runtime-pins-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const dependencies = { ...manifest.dependencies, '@anthropic-ai/claude-agent-sdk': '0.999.0', '@anthropic-ai/claude-code': '9.999.0' };
  writeFileSync(join(root, 'package.json'), JSON.stringify({ type: 'module', dependencies }));
  copyFileSync(new URL('../runtime.mjs', import.meta.url), join(root, 'runtime.mjs'));
  const sdk = join(root, 'node_modules/@anthropic-ai/claude-agent-sdk');
  mkdirSync(sdk, { recursive: true });
  writeFileSync(join(sdk, 'package.json'), JSON.stringify({ name: '@anthropic-ai/claude-agent-sdk', version: '0.999.0', main: 'sdk.mjs' }));
  writeFileSync(join(sdk, 'sdk.mjs'), '');
  const binary = join(root, 'native-probe');
  writeFileSync(binary, '#!/bin/sh\nif [ "$1" = --version ]; then echo "9.999.0 (Claude Code)"; else echo \'{"loggedIn":true,"authMethod":"claude.ai","subscriptionType":"max","email":"fixture@example.invalid"}\'; fi\n', { mode: 0o755 });
  const updated = await import(pathToFileURL(join(root, 'runtime.mjs')));
  assert.equal(updated.runtime.sdk, '0.999.0');
  assert.equal(updated.runtime.cli, '9.999.0');
  assert.equal(updated.nativeIdentity(binary, {}).sdk, '0.999.0');
  writeFileSync(join(sdk, 'package.json'), JSON.stringify({ name: '@anthropic-ai/claude-agent-sdk', version: '0.998.0', main: 'sdk.mjs' }));
  assert.throws(() => updated.nativeIdentity(binary, {}), /sdk_version_mismatch/);
  writeFileSync(binary, '#!/bin/sh\necho "8.0.0 (Claude Code)"\n', { mode: 0o755 });
  assert.throws(() => updated.nativeIdentity(binary, {}), /runtime_version_mismatch/);
});
