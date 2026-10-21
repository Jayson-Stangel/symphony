// Run only in a disposable root-owned test container; native probes run as 11001.
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';

if (process.argv[2] !== 'child') {
  assert.equal(process.getuid(), 0);
  assert.ok(existsSync('/.dockerenv'), 'requires a disposable Docker test container');
  const path = '/etc/claude-code/managed-settings.json';
  assert.equal(existsSync(path), false, 'requires an empty disposable policy profile');
  mkdirSync('/etc/claude-code', { recursive: true });
  writeFileSync(path, JSON.stringify({ env: { ANTHROPIC_API_KEY: 'inert-managed-policy-fixture' } }));
  try {
    const result = spawnSync(process.execPath, [process.argv[1], 'child'], {
      uid: 11001, gid: 11001, encoding: 'utf8', timeout: 15_000,
      env: { PATH: '/usr/local/bin:/usr/bin:/bin' },
    });
    assert.equal(result.status, 0, result.stderr);
    process.stdout.write(result.stdout);
  } finally { rmSync(path); }
} else {
  const root = mkdtempSync(join(tmpdir(), 'symphony-native-policy-'));
  process.env.HOME = root;
  process.env.CLAUDE_CONFIG_DIR = join(root, 'config');
  const { query, resolveSettings } = await import('@anthropic-ai/claude-agent-sdk');
  const { nativeExecutable } = await import('../../runtime.mjs');
  const settings = await resolveSettings({ cwd: root, settingSources: [] });
  assert.ok(settings.sources.some(source => source.source === 'managed' && source.policyOrigin === 'file'));
  assert.ok(settings.effective.env.ANTHROPIC_API_KEY);
  const env = { HOME: root, CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR,
    PATH: '/usr/local/bin:/usr/bin:/bin', DISABLE_AUTOUPDATER: '1' };
  const auth = spawnSync(nativeExecutable(), ['--setting-sources', '', 'auth', 'status', '--json'], {
    cwd: root, env, encoding: 'utf8', timeout: 10_000,
  });
  assert.equal(JSON.parse(auth.stdout).authMethod, 'api_key');
  let release;
  const idle = new Promise(resolve => { release = resolve; });
  async function* input() { await idle; }
  const session = query({ prompt: input(), options: {
    cwd: root, env, pathToClaudeCodeExecutable: nativeExecutable(), settingSources: [], strictMcpConfig: true,
    mcpServers: {}, persistSession: false, permissionMode: 'bypassPermissions',
    allowDangerouslySkipPermissions: true, stderr: () => {},
  } });
  try {
    await session.initializationResult();
    assert.equal((await session.accountInfo()).apiKeySource, 'ANTHROPIC_API_KEY');
    console.log(JSON.stringify({ sdkManagedActive: true, nativeAuthMethod: 'api_key',
      nativeApiKeySource: 'ANTHROPIC_API_KEY', noPrompt: true }));
  } finally { session.close(); release(); rmSync(root, { recursive: true, force: true }); }
}
