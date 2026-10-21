import test from 'node:test';
import assert from 'node:assert/strict';
import { approvedEnvironment } from '../runtime.mjs';

const base = { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude', CLAUDE_CODE_DISABLE_FAST_MODE: '1' };
const envelope = (names, values = {}, reserved = []) => JSON.stringify({ names, values, reserved });

test('only explicitly selected tool values join the fixed native environment', () => {
  const env = approvedEnvironment({ ...base, ANTHROPIC_API_KEY: 'api-canary', AWS_ACCESS_KEY_ID: 'ambient-canary',
    CUSTOM_TRACKER: 'tracker-canary', NODE_OPTIONS: 'loader-canary', SYMPHONY_GIT_TOKEN: 'selected-git',
    SYMPHONY_TOOL_ENVIRONMENT: envelope(['AWS_ACCESS_KEY_ID', 'AWS_REGION', 'OPTIONAL'],
      { AWS_ACCESS_KEY_ID: 'selected-tool', AWS_REGION: 'us-east-1', CUSTOM_TRACKER: 'hidden-canary' }, ['CUSTOM_TRACKER']) });
  assert.equal(env.AWS_ACCESS_KEY_ID, 'selected-tool');
  assert.equal(env.AWS_REGION, 'us-east-1');
  assert.equal(env.GH_TOKEN, 'selected-git');
  for (const name of ['ANTHROPIC_API_KEY', 'CUSTOM_TRACKER', 'NODE_OPTIONS', 'SYMPHONY_TOOL_ENVIRONMENT', 'OPTIONAL']) {
    assert.equal(Object.hasOwn(env, name), false, name);
  }
  const ambient = approvedEnvironment({ ...base, AWS_ACCESS_KEY_ID: 'ambient-canary', ANTHROPIC_API_KEY: 'api-canary' });
  assert.equal(Object.hasOwn(ambient, 'AWS_ACCESS_KEY_ID'), false);
  assert.equal(Object.hasOwn(ambient, 'ANTHROPIC_API_KEY'), false);
});

test('reserved tool selections fail with a bounded code and no values', () => {
  for (const name of ['HOME', 'PATH', 'LANG', 'TERM', 'CLAUDE_CONFIG_DIR', 'DISABLE_AUTOUPDATER',
    'ANTHROPIC_API_KEY', 'CLAUDE_CODE_USE_BEDROCK', 'SYMPHONY_GIT_TOKEN', 'GH_TOKEN', 'GITHUB_TOKEN',
    'NODE_OPTIONS', 'NODE_PATH', 'BUN_OPTIONS', 'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'CUSTOM_TRACKER', 'custom_tracker']) {
    assert.throws(() => approvedEnvironment({ ...base,
      SYMPHONY_TOOL_ENVIRONMENT: envelope([name], { [name]: 'secret-canary' }, ['CUSTOM_TRACKER']) }),
    error => error.message === 'invalid_tool_environment' && !String(error).includes('secret-canary'), name);
  }
  for (const encoded of ['invalid', 'null', '[]', 'x'.repeat(65_537), envelope(['bad-name']),
    envelope(Array(129).fill('VALID')), envelope(['VALID'], { VALID: 42 }), envelope(['VALID'], { VALID: 'bad\0value' })]) {
    assert.throws(() => approvedEnvironment({ ...base, SYMPHONY_TOOL_ENVIRONMENT: encoded }), /invalid_tool_environment/);
  }
});


test('the serialized tool envelope is bounded in UTF-8 bytes while small Unicode remains valid', () => {
  const value = '日本語 🔑';
  assert.equal(approvedEnvironment({ ...base,
    SYMPHONY_TOOL_ENVIRONMENT: envelope(['TOOL_TOKEN'], { TOOL_TOKEN: value }) }).TOOL_TOKEN, value);
  const encoded = envelope(['TOOL_TOKEN'], { TOOL_TOKEN: '界'.repeat(50_000) });
  assert.ok(encoded.length < 65_536);
  assert.ok(Buffer.byteLength(encoded, 'utf8') > 131_072);
  assert.throws(() => approvedEnvironment({ ...base, SYMPHONY_TOOL_ENVIRONMENT: encoded }),
    error => error.message === 'invalid_tool_environment' && !String(error).includes('界'));
});
