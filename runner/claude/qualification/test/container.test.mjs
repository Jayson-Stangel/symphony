import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { executable, versions } from '../policy.mjs';

const inLab = process.platform === 'linux' && existsSync('/opt/symphony-claude-lab/qualification/lab.mjs');

test('real lab container has no capabilities, no privilege escalation and protected installed files',
  { skip: !inLab }, () => {
    assert.equal(process.getuid(), 11001);
    const status = readFileSync('/proc/self/status', 'utf8');
    assert.match(status, /^CapEff:\s+0+$/m);
    assert.match(status, /^NoNewPrivs:\s+1$/m);
    assert.throws(() => writeFileSync('/opt/symphony-claude-lab/write-canary', 'denied', { flag: 'wx' }),
      error => ['EACCES', 'EROFS'].includes(error.code));
    assert.ok(!existsSync('/var/run/docker.sock'));
    assert.ok(!existsSync('/root/.codex'));
  });

test('real pinned native executable starts without model access', { skip: !inLab }, () => {
  const result = spawnSync(executable(), ['--version'], {
    env: { HOME: '/home/claude', PATH: '/usr/local/bin:/usr/bin:/bin',
      CLAUDE_CONFIG_DIR: '/home/claude/.claude', CLAUDE_CODE_DISABLE_FAST_MODE: '1', DISABLE_AUTOUPDATER: '1' },
    encoding: 'utf8', timeout: 15_000, maxBuffer: 64 * 1024,
  });
  assert.equal(result.status, 0);
  assert.equal(result.stdout.trim(), `${versions.cli} (Claude Code)`);
});
