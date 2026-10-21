import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { checkSettings, nativeExecutable, nativeIdentity } from '../runtime.mjs';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'symphony-settings-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const config = join(root, 'config');
  const workspace = join(root, 'workspace');
  mkdirSync(config);
  mkdirSync(join(workspace, '.claude'), { recursive: true });
  const marker = join(root, 'excluded-hook');
  writeFileSync(join(config, 'settings.json'), JSON.stringify({
    env: { ANTHROPIC_API_KEY: 'inert-settings-fixture' },
    hooks: { SessionStart: [{ hooks: [{ type: 'command', command: `touch ${marker}` }] }] },
  }));
  writeFileSync(join(workspace, '.claude/settings.json'), JSON.stringify({ env: { SYMPHONY_EXCLUDED_PROJECT: 'yes' } }));
  writeFileSync(join(workspace, '.claude/settings.local.json'), JSON.stringify({ env: { SYMPHONY_EXCLUDED_LOCAL: 'yes' } }));
  writeFileSync(join(workspace, '.mcp.json'), JSON.stringify({ mcpServers: {
    excluded: { command: '/bin/sh', args: ['-c', `touch ${join(root, 'excluded-mcp')}; sleep 10`] },
  } }));
  return { root, config, workspace, marker, env: { HOME: root, CLAUDE_CONFIG_DIR: config,
    PATH: '/usr/local/bin:/usr/bin:/bin', DISABLE_AUTOUPDATER: '1' } };
}

test('benign excluded project settings do not gate admission', t => {
  const ctx = fixture(t);
  // The historical guard accepted a workspace and rejected these benign files.
  assert.doesNotThrow(() => checkSettings(ctx.workspace));
});

// These are real pinned native startup proofs, with no prompt or model request.
test('excluded settings do not gate startup or contaminate the native auth probe', {
  skip: process.platform !== 'linux' || !existsSync('/opt/symphony/claude'), timeout: 20_000,
}, async t => {
  const ctx = fixture(t);
  const binary = nativeExecutable();
  const probe = spawnSync(binary, ['auth', 'status', '--json'], {
    cwd: ctx.workspace, env: ctx.env, encoding: 'utf8', timeout: 15_000,
  });
  assert.equal(JSON.parse(probe.stdout).authMethod, 'api_key', 'positive control must consume the fake settings key');
  assert.throws(() => nativeIdentity(binary, ctx.env), /auth_required/);
  for (const command of ['login', 'logout']) {
    const help = spawnSync(binary, ['--setting-sources', '', 'auth', command, '--help'], {
      env: ctx.env, encoding: 'utf8', timeout: 15_000,
    });
    assert.equal(help.status, 0, `${command} accepts source isolation without performing auth`);
  }
  let release;
  const idle = new Promise(resolve => { release = resolve; });
  async function* input() { await idle; }
  const session = query({ prompt: input(), options: {
    cwd: ctx.workspace, env: ctx.env, pathToClaudeCodeExecutable: binary,
    settingSources: [], strictMcpConfig: true, mcpServers: {}, persistSession: false,
    permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true, stderr: () => {},
  } });
  try {
    await session.initializationResult();
    assert.deepEqual(await session.mcpServerStatus(), []);
    assert.equal(existsSync(ctx.marker), false);
    assert.equal(existsSync(join(ctx.root, 'excluded-mcp')), false);
  } finally { session.close(); release(); }
});
