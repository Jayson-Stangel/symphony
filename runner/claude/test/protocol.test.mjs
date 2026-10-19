import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCommand, classifyEvent, summarizeResult, usageTotals } from '../protocol.mjs';
import { approvedEnvironment, profileFromStatus, validateAttestation } from '../runtime.mjs';
import { sameGroupGeneration } from '../processes.mjs';
import { toolShape } from '../tools.mjs';

test('protocol rejects versions, unknown options, oversized frames and stale shapes', () => {
  const command = { v: 1, type: 'turn', session_id: 'one', request_id: 'two', prompt: 'hello' };
  assert.deepEqual(parseCommand(JSON.stringify(command)), command);
  for (const value of [{ ...command, v: 2 }, { ...command, api_key: 'secret-canary' },
    { ...command, session_id: '' }, { ...command, prompt: 'a'.repeat(1_048_577) }]) {
    assert.throws(() => parseCommand(JSON.stringify(value)));
  }
});

test('only authoritative quota buckets establish a reset time; prose does not', () => {
  assert.equal(classifyEvent({ type: 'assistant', message: 'quota exceeded' }), null);
  assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: {
    status: 'rejected', rateLimitType: 'five_hour', resetsAt: 12345,
  } }), { outcome: 'quota_exhausted', resets_at: 12345 });
  assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: {
    status: 'rejected', rateLimitType: 'seven_day', resetsAt: 'tomorrow',
  } }), { outcome: 'quota_exhausted', resets_at: null });
  assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: { status: 'rejected', rateLimitType: 'unknown' } }), { outcome: 'policy_blocked' });
  assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: { status: 'allowed', overageStatus: 'allowed' } }), { outcome: 'policy_blocked' });
  for (const info of [{ status: 'allowed_warning', overageStatus: 'allowed_warning' },
    { status: 'allowed', isUsingOverage: true }, { status: 'rejected', rateLimitType: 'overage' }]) {
    assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: info }), { outcome: 'policy_blocked' });
  }
  assert.deepEqual(classifyEvent({ type: 'assistant', error: 'billing_error' }), { outcome: 'policy_blocked' });
  assert.deepEqual(classifyEvent({ type: 'assistant', error: 'authentication_failed' }), { outcome: 'auth_required' });
  assert.deepEqual(classifyEvent({ type: 'assistant', error: 'rate_limit' }), { outcome: 'transient_error' });
});

test('completion requires success and no cancellation/latched error', () => {
  const result = { subtype: 'success', is_error: false };
  assert.deepEqual(summarizeResult(result), { outcome: 'completed' });
  assert.deepEqual(summarizeResult(result, { outcome: 'quota_exhausted' }), { outcome: 'quota_exhausted' });
  assert.deepEqual(summarizeResult(result, null, true), { outcome: 'cancelled' });
  assert.deepEqual(summarizeResult({ ...result, is_error: true }), { outcome: 'protocol_error' });
});

test('usage is cumulative across models, includes cache input once, and excludes thinking double count', () => {
  assert.deepEqual(usageTotals({ sonnet: { inputTokens: 10, cacheReadInputTokens: 20,
    cacheCreationInputTokens: 30, outputTokens: 40, thinkingTokens: 15 } }), {
    input_tokens: 60, output_tokens: 40, total_tokens: 100,
  });
  assert.equal(usageTotals({ sonnet: { inputTokens: 'secret-canary' } }), null);
  assert.equal(usageTotals(undefined), null);
});

test('only native subscription identity produces an account fingerprint', () => {
  const value = { loggedIn: true, authMethod: 'claude.ai', subscriptionType: 'pro', email: 'owner@example.test', orgId: 'one' };
  const profile = profileFromStatus(value);
  assert.match(profile, /^[a-f0-9]{64}$/);
  assert.notEqual(profileFromStatus({ ...value, orgId: 'two' }), profile);
  for (const patch of [{ authMethod: 'console' }, { loggedIn: false }, { email: null }, { subscriptionType: null }]) {
    assert.throws(() => profileFromStatus({ ...value, ...patch }));
  }
  const now = Date.now();
  const attestation = { version: 1, policy: 1, profile, overages_disabled: true, attested_at: new Date(now).toISOString() };
  assert.doesNotThrow(() => validateAttestation(attestation, profile, now));
  for (const patch of [{ profile: 'other' }, { policy: 2 }, { overages_disabled: false }, { attested_at: 'invalid' }]) {
    assert.throws(() => validateAttestation({ ...attestation, ...patch }, profile, now));
  }
});

test('environment allowlist strips tracker credentials and rejects competing inference credentials', () => {
  const source = { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude', CLAUDE_CODE_DISABLE_FAST_MODE: '1' };
  assert.ok(!JSON.stringify(approvedEnvironment({ ...source, GH_TOKEN: 'secret-canary', NODE_OPTIONS: 'secret-canary' })).includes('secret-canary'));
  for (const key of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_VERTEX', 'ANTHROPIC_PROFILE']) {
    assert.throws(() => approvedEnvironment({ ...source, [key]: 'secret-canary' }), error => !error.message.includes('secret-canary'));
  }
});

test('tool schemas preserve required/nullable/freeform fields and reject unsupported root policy', () => {
  const schema = toolShape({ type: 'object', additionalProperties: false, required: ['query'], properties: {
    query: { type: 'string' }, variables: { type: ['object', 'null'], additionalProperties: true },
  } });
  assert.deepEqual(schema.validate({ query: 'query', variables: { nested: [1, 'two'] } }), { query: 'query', variables: { nested: [1, 'two'] } });
  assert.deepEqual(schema.validate({ query: 'query', variables: null }), { query: 'query', variables: null });
  assert.throws(() => schema.validate({ query: 'query', extra: true }));
  assert.throws(() => schema.validate({ variables: {} }));
  assert.throws(() => toolShape({ type: 'object', additionalProperties: true, properties: {} }));
  assert.throws(() => toolShape({ type: 'object', additionalProperties: false, properties: {}, oneOf: [] }));
});

test('process cleanup refuses a reused leader PID or foreign group generation', () => {
  const owned = { pid: 123, start: '100' };
  const member = { pid: 124, group: 123, start: '101' };
  assert.equal(sameGroupGeneration(owned, { pid: 123, group: 123, start: '100' }, [member]), true);
  assert.equal(sameGroupGeneration(owned, { pid: 123, group: 123, start: '200' }, [member]), false);
  assert.equal(sameGroupGeneration(owned, null, [{ ...member, start: '99' }]), false);
  assert.equal(sameGroupGeneration(owned, null, []), false);
});

// REST tool bodies deliberately accept arbitrary JSON, including nested values.
test('existing REST schemas preserve unconstrained optional JSON request bodies', () => {
  const { validate } = toolShape({ type: 'object', additionalProperties: false, required: ['method', 'path'],
    properties: { method: { type: 'string' }, path: { type: 'string' }, body: { description: 'JSON body' } } });
  assert.deepEqual(validate({ method: 'PATCH', path: '/repos/owner/repo/issues/1', body: { nested: [null, true] } }).body, { nested: [null, true] });
});
