import test from 'node:test';
import assert from 'node:assert/strict';
import { parseCommand, classifyEvent, summarizeResult, usageTotals, assistantInputTokens } from '../protocol.mjs';
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

test('start accepts current Claude model IDs and family aliases without rewriting them', () => {
  const start = { v: 1, type: 'start', session_id: 'one', request_id: 'two', workspace: '/workspaces/issue',
    config: { model: 'sonnet', max_agentic_turns: 5, turn_timeout_ms: 30_000 }, tools: [], instructions: '' };
  for (const model of ['sonnet', 'opus', 'haiku', 'fable',
    'claude-opus-5-5', 'claude-sonnet-5-5', 'claude-haiku-5-5', 'claude-fable-5-1']) {
    const command = { ...start, config: { ...start.config, model } };
    assert.deepEqual(parseCommand(JSON.stringify(command)), { ...command, skills: {} });
  }
  for (const model of ['', 'Opus', 'claude-opus-5-5-typo', 'claude-opus-99', 'opusplan', 'default', 'sonnet[1m]', 123, null, undefined]) {
    assert.throws(() => parseCommand(JSON.stringify({ ...start, config: { ...start.config, model } })));
  }
});

test('effort accepts SDK levels and leaves omitted effort unset', () => {
  const start = { v: 1, type: 'start', session_id: 'one', request_id: 'two', workspace: '/workspaces/issue',
    config: { model: 'claude-opus-5-5', max_agentic_turns: 5, turn_timeout_ms: 30_000 }, tools: [], instructions: '' };
  assert.ok(!Object.hasOwn(parseCommand(JSON.stringify(start)).config, 'effort'));
  for (const effort of [null, 'low', 'medium', 'high', 'xhigh', 'max']) {
    assert.equal(parseCommand(JSON.stringify({ ...start, config: { ...start.config, effort } })).config.effort, effort);
  }
  for (const effort of ['', 'auto', 'ultracode', 'High', 'ultra', 42, {}]) {
    assert.throws(() => parseCommand(JSON.stringify({ ...start, config: { ...start.config, effort } })));
  }
});

test('only authoritative quota buckets establish a reset time; prose does not', () => {
  assert.equal(classifyEvent({ type: 'assistant', message: 'quota exceeded' }), null);
  for (const rateLimitType of ['five_hour', 'seven_day', 'seven_day_opus', 'seven_day_sonnet', 'seven_day_overage_included']) {
    assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: {
      status: 'rejected', rateLimitType, resetsAt: 12345,
    } }), { outcome: 'quota_exhausted', resets_at: 12345 });
  }
  for (const resetsAt of [undefined, null, 'tomorrow', -1, 0]) {
    assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: {
      status: 'rejected', rateLimitType: 'seven_day_overage_included', resetsAt,
    } }), { outcome: 'quota_exhausted', resets_at: null });
  }
  for (const info of [{ isUsingOverage: true }, { overageStatus: 'allowed' }, { overageStatus: 'allowed_warning' }]) {
    assert.deepEqual(classifyEvent({ type: 'rate_limit_event', rate_limit_info: {
      status: 'rejected', rateLimitType: 'seven_day_overage_included', resetsAt: 12345, ...info,
    } }), { outcome: 'policy_blocked' });
  }
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
  assert.equal(usageTotals({ sonnet: null }), null);
});

test('live input validates cached counts and excludes placeholder output', () => {
  assert.equal(assistantInputTokens({ input_tokens: 10, output_tokens: 999 }), 10);
  assert.equal(assistantInputTokens({ input_tokens: 10, cache_read_input_tokens: 20, cache_creation_input_tokens: 30 }), 60);
  for (const usage of [null, {}, { input_tokens: -1 }, { input_tokens: '10' },
    { input_tokens: 1, cache_read_input_tokens: -1 },
    { input_tokens: Number.MAX_SAFE_INTEGER, cache_creation_input_tokens: 1 }]) {
    assert.equal(assistantInputTokens(usage), null);
  }
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

test('environment allowlist strips unselected tracker and inference credentials', () => {
  const source = { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude', CLAUDE_CODE_DISABLE_FAST_MODE: '1' };
  assert.ok(!JSON.stringify(approvedEnvironment({ ...source, GH_TOKEN: 'secret-canary', NODE_OPTIONS: 'secret-canary' })).includes('secret-canary'));
  for (const key of ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_VERTEX', 'ANTHROPIC_PROFILE']) {
    assert.ok(!JSON.stringify(approvedEnvironment({ ...source, [key]: 'secret-canary' })).includes('secret-canary'));
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

// A read-only root does not make a nested writable policy bind safe.
test('containment requires the effective root and policy mounts to be read-only', async () => {
  const { isReadOnlyMount } = await import('../runtime.mjs');
  const root = '1 0 0:1 / / ro,relatime - overlay overlay ro';
  const policy = '2 1 0:2 / /etc/symphony/claude rw,relatime - tmpfs tmpfs rw';
  assert.equal(isReadOnlyMount('/', root), true);
  assert.equal(isReadOnlyMount('/', root.replace('ro,relatime', 'rw,relatime')), false);
  assert.equal(isReadOnlyMount('/etc/symphony/claude/attestation.json', root + '\n' + policy), false);
  assert.equal(isReadOnlyMount('/etc/symphony/claude/attestation.json', root + '\n' + policy.replace('rw,relatime', 'ro,relatime')), true);
  assert.equal(isReadOnlyMount('/', ''), false);
});

test('skill transport bounds names, paths and count while the packaged plugin validates membership', () => {
  const start = { v: 1, type: 'start', session_id: 'one', request_id: 'two', workspace: '/workspaces/issue',
    config: { model: 'sonnet', max_agentic_turns: 5, turn_timeout_ms: 30_000 }, tools: [], instructions: '' };
  const skills = { 'packaged-procedure': '/workspaces/issue/.codex/skills/local' };
  assert.deepEqual(parseCommand(JSON.stringify({ ...start, skills })).skills, skills);
  // Zod drops the JSON prototype key instead of installing it as an inherited selection.
  assert.deepEqual(parseCommand(JSON.stringify({ ...start, skills: JSON.parse('{"__proto__":"escape"}') })).skills, {});
  for (const invalid of [{ '../escape': '/tmp/x' },
    { 'symphony-land': '' }, { 'symphony-land': 'x'.repeat(4097) },
    Object.fromEntries(Array.from({ length: 6 }, (_, i) => [`procedure-${i}`, '/tmp/x']))]) {
    assert.throws(() => parseCommand(JSON.stringify({ ...start, skills: invalid })));
  }
});
