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

async function fixture(t, model = 'sonnet') {
  const messages = [];
  let query;
  const helper = new Helper({ identity: { profile: 'test' }, env: {}, binary: '/test/native',
    graceMs: 0, killMs: 0, send: value => messages.push(value),
    makeQuery: params => { query = new FakeQuery(params); return query; } });
  t.after(() => helper.stop('test_cleanup'));
  await helper.command({ type: 'start', session_id: 'session', request_id: 'start', workspace: process.cwd(),
    config: { model, max_agentic_turns: 5, turn_timeout_ms: 30_000 }, tools: [], instructions: 'policy' });
  query.push({ type: 'system', subtype: 'init', session_id: 'native-one' });
  await tick();
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

test('an explicit Opus model reaches the SDK unchanged', async t => {
  const ctx = await fixture(t, 'claude-opus-5-5');
  assert.equal(ctx.query.params.options.model, 'claude-opus-5-5');
  const input = await turn(ctx);
  result(ctx.query, input);
  await tick();
  assert.equal(ctx.messages.find(value => value.type === 'turn_result').outcome, 'completed');
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

test('quota evidence cannot be downgraded by a later generic rate-limit error', async t => {
  const ctx = await fixture(t);
  const input = await turn(ctx);
  ctx.query.push({ type: 'rate_limit_event', rate_limit_info: { status: 'rejected', rateLimitType: 'five_hour', resetsAt: 12345 } });
  ctx.query.push({ type: 'assistant', error: 'rate_limit' });
  result(ctx.query, input);
  await tick();
  const outcome = ctx.messages.find(value => value.type === 'turn_result');
  assert.equal(outcome.outcome, 'quota_exhausted');
  assert.equal(outcome.resets_at, 12345);
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
