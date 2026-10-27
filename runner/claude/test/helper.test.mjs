import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { setImmediate as tick } from 'node:timers/promises';
import { Helper } from '../helper.mjs';

class FakeQuery {
  constructor(params) { this.params = params; this.queue = []; this.done = false; }
  initializationResult() { return Promise.resolve({}); }
  [Symbol.asyncIterator]() { return this; }
  next() {
    if (this.queue.length) return Promise.resolve({ value: this.queue.shift(), done: false });
    if (this.done) return Promise.resolve({ done: true });
    return new Promise(resolve => { this.waiter = resolve; });
  }
  push(value) {
    if (this.waiter) { const resolve = this.waiter; this.waiter = null; resolve({ value, done: false }); }
    else this.queue.push(value);
  }
  close() { this.done = true; this.waiter?.({ done: true }); this.waiter = null; }
  interrupt() { this.interrupted = true; return Promise.resolve(); }
}

async function fixture(t, model = 'sonnet', effort, maxTurns = 20) {
  const messages = [];
  let query;
  const helper = new Helper({ identity: { profile: 'test' }, env: {}, binary: '/test/native',
    makeTemporary: () => ({ path: '/fixture/owned-scratch', cleanup() {} }),
    graceMs: 0, killMs: 0, send: value => messages.push(value),
    makeQuery: params => { query = new FakeQuery(params); return query; } });
  t.after(() => helper.stop('test_cleanup'));
  await helper.command({ type: 'start', session_id: 'session', request_id: 'start', workspace: process.cwd(),
    config: { model, effort, max_turns: maxTurns, max_agentic_turns: 5, turn_timeout_ms: 30_000 }, tools: [], instructions: 'policy' });
  query.push({ type: 'system', subtype: 'init', session_id: 'native-one' });
  await tick();
  assert.equal(query.params.options.env.TMPDIR, '/fixture/owned-scratch');
  const input = query.params.prompt[Symbol.asyncIterator]();
  return { helper, messages, query, input };
}

async function turn(ctx, request = 'turn-one') {
  await ctx.helper.command({ type: 'turn', session_id: 'session', request_id: request, prompt: 'test' });
  return (await ctx.input.next()).value;
}

function result(query, input, extra = {}) {
  query.push({ type: 'result', subtype: 'success', is_error: false, uuid: randomUUID(),
    session_id: 'native-one', user_message_uuid: input.uuid, ...extra });
}

test('a future native model ID reaches the SDK unchanged', async t => {
  const ctx = await fixture(t, 'future-native-model-99');
  assert.equal(ctx.query.params.options.model, 'future-native-model-99');
  const input = await turn(ctx);
  result(ctx.query, input);
  await tick();
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'completed');
});

test('configured effort reaches the SDK while omitted effort preserves its default', async t => {
  for (const effort of [undefined, null, 'low', 'medium', 'high', 'xhigh', 'max']) {
    const ctx = await fixture(t, 'claude-opus-5-5', effort);
    if (effort) assert.equal(ctx.query.params.options.effort, effort);
    else assert.ok(!Object.hasOwn(ctx.query.params.options, 'effort'));
  }
});

test('one conversation retains native context and emits one outcome per input', async t => {
  const ctx = await fixture(t);
  const first = await turn(ctx);
  const uuid = randomUUID();
  result(ctx.query, first, { uuid });
  await tick();
  assert.equal(ctx.messages.filter(value => value.type === 'turn_result').length, 1);
  const second = await turn(ctx, 'turn-two');
  // A duplicated prior result cannot complete a new turn.
  result(ctx.query, first, { uuid });
  ctx.query.push({ type: 'system', subtype: 'init', session_id: 'native-one' });
  await tick();
  assert.equal(ctx.messages.filter(value => value.type === 'turn_result').length, 1);
  result(ctx.query, second);
  await tick();
  assert.deepEqual(ctx.messages.filter(value => value.type === 'turn_result').map(value => value.request_id), ['turn-one', 'turn-two']);
  assert.ok(ctx.messages.filter(value => value.type === 'turn_result').every(value => value.outcome === 'completed'));
  await assert.rejects(() => ctx.helper.command({ type: 'turn', session_id: 'session', request_id: 'turn-one', prompt: 'replay' }));
});

test('stale or uncorrelated native success fails closed', async t => {
  const ctx = await fixture(t);
  const input = await turn(ctx);
  result(ctx.query, input, { user_message_uuid: 'some-other-input' });
  await tick();
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'protocol_error');
});

test('tool callbacks can resolve while the stream is active and stale replies are rejected', async t => {
  const ctx = await fixture(t);
  const input = await turn(ctx);
  const pending = ctx.helper.callTool('linear_graphql', { query: 'query' });
  const call = ctx.messages.find(value => value.type === 'tool_call');
  assert.equal(call.request_id, 'turn-one');
  const response = { type: 'tool_result', session_id: 'session', request_id: 'turn-one',
    tool_id: call.tool_id, result: { success: false, output: 'tracker error' } };
  await ctx.helper.command(response);
  assert.deepEqual(await pending, response.result);
  assert.ok(ctx.messages.some(value => value.type === 'tool_result_accepted'));
  await assert.rejects(() => ctx.helper.command(response));
  result(ctx.query, input);
  await tick();
});

test('Fable quota evidence cannot be downgraded by a later generic rate-limit error', async t => {
  const ctx = await fixture(t, 'claude-fable-5-1');
  const input = await turn(ctx);
  ctx.query.push({ type: 'rate_limit_event', rate_limit_info: { status: 'rejected', rateLimitType: 'seven_day_overage_included', resetsAt: 12345 } });
  ctx.query.push({ type: 'assistant', error: 'rate_limit' });
  result(ctx.query, input);
  await tick();
  const outcome = ctx.messages.find(value => value.type === 'turn_result');
  assert.equal(outcome.outcome, 'quota_exhausted');
  assert.equal(outcome.resets_at, 12345);
});

test('native allowed overage preserves successful completion and genuine errors retain precedence', async t => {
  for (const [error, expected] of [[null, 'completed'], ['billing_error', 'policy_blocked'],
    ['authentication_failed', 'auth_required']]) {
    const ctx = await fixture(t);
    const input = await turn(ctx);
    for (const status of ['allowed', 'allowed_warning']) {
      ctx.query.push({ type: 'rate_limit_event', rate_limit_info: {
        status, overageStatus: status, isUsingOverage: true, rateLimitType: 'overage',
      } });
    }
    if (error) ctx.query.push({ type: 'assistant', error });
    result(ctx.query, input);
    await tick();
    assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, expected);
  }
});

test('assistant text and stream EOF do not establish completion', async t => {
  const ctx = await fixture(t);
  await turn(ctx);
  ctx.query.push({ type: 'assistant', message: { content: 'done' } });
  await tick();
  assert.equal(ctx.messages.some(value => value.type === 'turn_result'), false);
  ctx.query.close();
  await tick();
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'protocol_error');
});

for (const [mode, code] of [['eof', 'native_eof'], ['event-error', 'native_event_error'], ['timeout', 'turn_timeout']]) {
  test(`bounded turn cause distinguishes ${mode} without changing its outcome`, async t => {
    const ctx = await fixture(t);
    const outcome = new Promise(resolve => {
      const send = ctx.helper.send;
      ctx.helper.send = message => { send(message); if (message.type === 'turn_result') resolve(message); };
    });
    ctx.helper.config.turn_timeout_ms = 20;
    await turn(ctx);
    if (mode === 'eof') ctx.query.close();
    if (mode === 'event-error') ctx.query.push({ type: 'result', uuid: 'secret-canary', session_id: 'foreign' });
    const message = await outcome;
    assert.equal(message.outcome, 'protocol_error');
    assert.equal(message.code, code);
    assert.equal(message.session_id, 'session');
    assert.equal(message.request_id, 'turn-one');
    assert.equal(ctx.messages.filter(value => value.type === 'turn_result').length, 1);
    assert.ok(!JSON.stringify(ctx.messages).includes('secret-canary'));
  });
}

test('interrupt cancels callbacks and emits a single cancellation outcome', async t => {
  const ctx = await fixture(t);
  await turn(ctx);
  const pending = ctx.helper.callTool('linear_graphql', { query: 'query' }).catch(error => error);
  await ctx.helper.command({ type: 'interrupt', session_id: 'session', request_id: 'interrupt' });
  assert.equal((await pending).message, 'tool_cancelled');
  assert.equal(ctx.query.interrupted, true);
  assert.equal(ctx.messages.filter(value => value.type === 'turn_result').length, 1);
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'cancelled');
  assert.equal(ctx.helper.tools.size, 0);
  await ctx.helper.stop('repeat');
  assert.equal(ctx.messages.filter(value => value.type === 'stopped').length, 1);
});

test('temporary cleanup failure withholds stopped acknowledgement', async t => {
  const ctx = await fixture(t);
  let terminated = 0;
  ctx.helper.terminate = () => { terminated++; };
  ctx.helper.temporary.cleanup = () => { throw new Error('foreign ownership'); };
  await ctx.helper.stop('cleanup');
  assert.equal(terminated, 1);
  assert.equal(ctx.messages.some(value => value.type === 'stopped'), false);
});

function assistant(ctx, id, input, extra = {}) {
  ctx.query.push({ type: 'assistant', session_id: 'native-one', parent_tool_use_id: null,
    message: { id, usage: { input_tokens: input, cache_read_input_tokens: 20,
      cache_creation_input_tokens: 10, output_tokens: 999 } }, ...extra });
}

function modelUsage(input, output) {
  return { sonnet: { inputTokens: input, outputTokens: output,
    cacheReadInputTokens: 0, cacheCreationInputTokens: 0 } };
}

test('live input usage deduplicates tool blocks and reconciles cumulative multi-turn results', async t => {
  const ctx = await fixture(t);
  const first = await turn(ctx);
  assistant(ctx, 'message-one', 10);
  assistant(ctx, 'message-one', 10);
  assistant(ctx, 'message-two', 20);
  await tick();
  assert.deepEqual(ctx.messages.filter(value => value.type === 'usage').map(value => value.totals), [
    { input_tokens: 40, output_tokens: 0, total_tokens: 40 },
    { input_tokens: 90, output_tokens: 0, total_tokens: 90 },
  ]);
  result(ctx.query, first, { modelUsage: modelUsage(100, 50) });
  await tick();
  const second = await turn(ctx, 'turn-two');
  assistant(ctx, 'message-three', 10);
  await tick();
  assert.deepEqual(ctx.messages.filter(value => value.type === 'usage').at(-1).totals,
    { input_tokens: 140, output_tokens: 50, total_tokens: 190 });
  result(ctx.query, second, { modelUsage: modelUsage(150, 80) });
  await tick();
  assert.deepEqual(ctx.messages.filter(value => value.type === 'usage').at(-1).totals,
    { input_tokens: 150, output_tokens: 80, total_tokens: 230 });
});

test('missing, malformed and uncorrelated assistant usage is ignored without losing progress', async t => {
  const ctx = await fixture(t);
  await turn(ctx);
  assistant(ctx, 'other-session', 10, { session_id: 'other' });
  assistant(ctx, 'other-turn', 10, { user_message_uuid: 'stale' });
  assistant(ctx, 'error', 10, { error: 'server_error' });
  assistant(ctx, 'bad-count', -1);
  assistant(ctx, 'no-usage', 10, { message: { id: 'no-usage' } });
  await tick();
  assert.equal(ctx.messages.some(value => value.type === 'usage'), false);
  assert.ok(ctx.messages.some(value => value.type === 'progress'));
});

test('zeroed or missing result usage preserves live counts into the next turn', async t => {
  const ctx = await fixture(t);
  const first = await turn(ctx);
  assistant(ctx, 'first', 10);
  result(ctx.query, first, { modelUsage: modelUsage(0, 0) });
  await tick();
  const second = await turn(ctx, 'turn-two');
  assistant(ctx, 'second', 10);
  result(ctx.query, second);
  await tick();
  assert.deepEqual(ctx.messages.filter(value => value.type === 'usage').at(-1).totals,
    { input_tokens: 80, output_tokens: 0, total_tokens: 80 });
});

test('progress is categorical and only new assistant blocks advance activity', async t => {
  const ctx = await fixture(t);
  await turn(ctx);
  const secret = 'secret prompt reasoning credential argument provider payload';
  const assistant = { type: 'assistant', session_id: 'native-one', uuid: 'block-one', message: { content: [
    { type: 'tool_use', name: 'Bash', input: { command: secret } },
    { type: 'thinking', thinking: secret }, { type: 'text', text: secret },
  ] } };
  for (const event of [assistant, assistant,
    { ...assistant, uuid: 'stale-block', user_message_uuid: 'prior-turn' },
    { type: 'tool_progress', uuid: 'heartbeat-one', tool_name: 'Bash', heartbeat: true },
    { type: 'tool_progress', uuid: 'heartbeat-two', tool_name: secret, elapsed_time_seconds: 60 },
    { type: 'system', subtype: 'api_retry', error: secret, attempt: 2 },
    { type: 'system', subtype: 'status', status: 'compacting', compact_error: secret },
    { type: 'system', subtype: 'status', status: null },
    { type: 'assistant', message: {} },
    { type: 'assistant', session_id: 'native-one', uuid: 'block-two', message: { content: [{ type: 'text', text: secret }] } },
  ]) ctx.query.push(event);
  await tick();
  const progress = ctx.messages.filter(value => value.type === 'progress');
  assert.deepEqual(progress.map(({ status, productive, tool }) => [status, productive, tool]), [
    ['tool_started', true, 'command'], ['tool_started', false, 'command'], ['tool_started', false, 'command'],
    ['tool_running', false, 'command'], ['tool_running', false, 'tool'],
    ['retrying', false, undefined], ['compacting', false, undefined],
    ['waiting', false, undefined], ['waiting', false, undefined], ['response', true, undefined],
  ]);
  assert.ok(!JSON.stringify(progress).includes(secret));
  assert.ok(!JSON.stringify(progress).includes('tool_use'));
});


test('shared attempt allowance permits 129 inputs while bounding replay history', async t => {
  const ctx = await fixture(t, 'sonnet', undefined, 129);
  assert.equal(ctx.query.params.options.maxTurns, 5); // Native agentic allowance remains separate.
  for (let index = 1; index <= 129; index++) {
    const input = await turn(ctx, `turn-${index}`);
    const uuid = randomUUID();
    result(ctx.query, input, { uuid });
    result(ctx.query, input, { uuid });
    await tick();
    assert.equal(ctx.helper.usedRequests.size, index);
    assert.equal(ctx.helper.terminals.size, index);
  }
  assert.equal(ctx.messages.filter(value => value.type === 'turn_result').length, 129);
  await assert.rejects(() => turn(ctx, 'turn-130'), { message: 'agent_max_turns' });
  await assert.rejects(() => turn(ctx, 'turn-1'), { message: 'invalid_turn_state' });
  assert.equal(ctx.helper.usedRequests.size, 129);
  assert.equal(ctx.helper.terminals.size, 129);
});

test('stale native IDs cannot grow replay history or complete the current input', async t => {
  const ctx = await fixture(t);
  const input = await turn(ctx);
  result(ctx.query, input, { user_message_uuid: 'stale-input' });
  await tick();
  assert.equal(ctx.helper.usedRequests.size, 1);
  assert.equal(ctx.helper.terminals.size, 0);
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'protocol_error');
});
