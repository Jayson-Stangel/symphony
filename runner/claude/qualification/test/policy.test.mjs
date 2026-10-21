import test from 'node:test';
import assert from 'node:assert/strict';
import { childEnvironment, validateEnvironment, authCategory, terminalSummary } from '../policy.mjs';

const safe = { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude', CLAUDE_CODE_DISABLE_FAST_MODE: '1' };

test('conflicting and unknown provider credentials fail without exposing secret values', () => {
  for (const key of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_PROFILE',
    'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'ANTHROPIC_BASE_URL',
    'AWS_ACCESS_KEY_ID', 'GOOGLE_APPLICATION_CREDENTIALS', 'CLAUDE_NEW_AUTH_SELECTOR']) {
    assert.throws(() => validateEnvironment({ ...safe, [key]: 'secret-canary' }), error => {
      assert.ok(error.message.includes(key));
      assert.ok(!error.message.includes('secret-canary'));
      return true;
    });
  }
});

test('child environment excludes tracker secrets and host process settings', () => {
  const env = childEnvironment({ ...safe, GH_TOKEN: 'secret-canary', LINEAR_API_KEY: 'secret-canary',
    NODE_OPTIONS: '--require=malicious', PATH: '/host/bin', HTTPS_PROXY: 'https://secret-canary' });
  assert.equal(JSON.stringify(env).includes('secret-canary'), false);
  assert.equal(env.NODE_OPTIONS, undefined);
  assert.equal(env.PATH, '/usr/local/bin:/usr/bin:/bin');
});

test('wrong native identity or paid speed settings are rejected', () => {
  for (const patch of [{ HOME: '/root' }, { CLAUDE_CONFIG_DIR: '/host/.claude' },
    { CLAUDE_CODE_DISABLE_FAST_MODE: '0' }]) assert.throws(() => validateEnvironment({ ...safe, ...patch }));
});

test('unknown/Console OAuth does not prove subscription funding', () => {
  assert.equal(authCategory({ loggedIn: true, authMethod: 'claude.ai', subscriptionType: 'max' }), 'subscription');
  assert.equal(authCategory({ loggedIn: true, authMethod: 'claude.ai', subscriptionType: 'pro' }), 'subscription');
  for (const status of [{ loggedIn: true, authMethod: 'api_key' },
    { loggedIn: true, authMethod: 'claude.ai' }, { loggedIn: true, authMethod: 'console' }]) {
    assert.equal(authCategory(status), 'unqualified');
  }
  assert.equal(authCategory({ loggedIn: false }), 'missing');
  assert.equal(authCategory({}), 'unqualified');
});

test('partial text and success-subtype errors cannot establish completion', () => {
  assert.equal(terminalSummary({ type: 'assistant', message: 'Done' }), null);
  const result = terminalSummary({ type: 'result', subtype: 'success', is_error: true,
    result: 'secret-canary', errors: ['secret-canary'], num_turns: 1 });
  assert.equal(result.success, false);
  assert.equal(JSON.stringify(result).includes('secret-canary'), false);
  assert.equal(terminalSummary({ type: 'result', subtype: 'unknown', is_error: false }).success, false);
});
