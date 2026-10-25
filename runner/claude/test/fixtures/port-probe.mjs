// Credential-free protocol peer using the production framing/state machine.
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { serve } from '../../helper.mjs';

let helper;
class Probe {
  constructor({ prompt, options }) {
    this.options = options;
    this.queue = [];
    this.done = false;
    void this.inputs(prompt).catch(() => this.close());
  }
  async inputs(prompt) {
    for await (const input of prompt) {
      this.push({ type: 'system', subtype: 'init', session_id: 'native-probe' });
      // Worker prompts append recovery instructions after the scenario's first line.
      const text = input.message.content.split('\n', 1)[0];
      if (text.startsWith('effort:') && this.options.effort !== text.slice(7)) throw new Error('effort_mismatch');
      if (text === 'silence') continue;
      if (text === 'native-allowance') {
        const path = join(this.options.cwd, 'native-allowance-count');
        const count = existsSync(path) ? Number(readFileSync(path, 'utf8')) + 1 : 1;
        writeFileSync(path, String(count));
        if (count === 1) {
          this.push({ type: 'result', subtype: 'error_max_turns', is_error: true, uuid: randomUUID(),
            session_id: 'native-probe', user_message_uuid: input.uuid,
            permission_denials: [{ tool_name: 'Read', tool_use_id: 'prior-denial', tool_input: {} }] });
          continue;
        }
      }
      if (text === 'event-error') { this.push({ type: 'result', uuid: 'secret-canary', session_id: 'foreign' }); continue; }
      if (text === 'malformed') { process.stdout.write('not JSON\n'); continue; }
      if (text === 'partial') { this.push({ type: 'assistant', message: { content: 'Done' } }); this.close(); continue; }
      if (text.startsWith('fable-quota:')) this.push({ type: 'rate_limit_event', rate_limit_info: {
        status: 'rejected', rateLimitType: 'seven_day_overage_included', resetsAt: Number(text.split(':')[1]),
      } });
      if (text.startsWith('allowed-overage')) {
        for (const status of ['allowed', 'allowed_warning']) this.push({ type: 'rate_limit_event', rate_limit_info: {
          status, overageStatus: status, isUsingOverage: true, rateLimitType: 'overage',
        } });
        if (text === 'allowed-overage-billing-error') this.push({ type: 'assistant', error: 'billing_error' });
      }
      if (text.startsWith('new-bucket:')) this.push({ type: 'rate_limit_event', rate_limit_info: {
        status: 'rejected', rateLimitType: 'future-native-bucket', resetsAt: Number(text.split(':')[1]),
      } });
      if (text === 'quota') this.push({ type: 'rate_limit_event', rate_limit_info: { status: 'rejected', rateLimitType: 'five_hour' } });
      if (text === 'auth') this.push({ type: 'assistant', error: 'authentication_failed' });
      if (text === 'linear-recovery') {
        const rejected = await helper.callTool('linear_graphql', { query: 'mutation { rejected }' });
        if (rejected.success || !JSON.parse(rejected.output).errors) throw new Error('missing request error');
        const corrected = await helper.callTool('linear_graphql', { query: 'mutation { corrected }' });
        if (!corrected.success) throw new Error('correction failed');
      }
      if (text === 'linear-uncertain-recovery') {
        const failed = await helper.callTool('linear_graphql', { query: 'mutation { uncertain }' });
        if (failed.success || JSON.parse(failed.output).errors[0].message !== 'partial write') throw new Error('error was withheld');
        const read = await helper.callTool('linear_graphql', { query: 'query { issue(id: "one") { id title } }' });
        if (!read.success || JSON.parse(read.output).data.issue.title !== 'x') throw new Error('reconciliation read blocked');
      }
      if (text === 'progress') {
        this.push({ type: 'assistant', session_id: 'native-probe', uuid: randomUUID(), message: { content: [
          { type: 'tool_use', name: 'Bash', input: { command: 'SECRET' } },
        ] } });
        this.push({ type: 'tool_progress', tool_name: 'Bash', heartbeat: true });
        this.push({ type: 'system', subtype: 'api_retry', error: 'SECRET' });
        this.push({ type: 'system', subtype: 'status', status: 'compacting' });
        this.push({ type: 'assistant', message: {} });
        for (const name of ['Read', 'Edit', 'Grep', 'custom-SECRET']) {
          this.push({ type: 'assistant', session_id: 'native-probe', uuid: randomUUID(), message: { content: [
            { type: 'tool_use', name, input: { secret: 'SECRET' } },
          ] } });
        }
        this.push({ type: 'assistant', session_id: 'native-probe', uuid: randomUUID(), message: { content: [{ type: 'text', text: 'SECRET' }] } });
      }
      if (text === 'tool') await helper.callTool('probe_tool', { query: 'query' });
      this.push({ type: 'result', subtype: 'success', is_error: false, uuid: randomUUID(),
        session_id: 'native-probe', user_message_uuid: input.uuid,
        modelUsage: { sonnet: { inputTokens: 10, outputTokens: 20, cacheReadInputTokens: 0, cacheCreationInputTokens: 0 } } });
    }
  }
  push(value) {
    if (this.waiter) { const waiter = this.waiter; this.waiter = null; waiter({ value, done: false }); }
    else this.queue.push(value);
  }
  [Symbol.asyncIterator]() { return this; }
  next() {
    if (this.queue.length) return Promise.resolve({ value: this.queue.shift(), done: false });
    if (this.done) return Promise.resolve({ done: true });
    return new Promise(resolve => { this.waiter = resolve; });
  }
  initializationResult() { return Promise.resolve({}); }
  interrupt() { return Promise.resolve(); }
  close() { this.done = true; this.waiter?.({ done: true }); this.waiter = null; }
}

helper = serve({ binary: '/credential-free-probe', env: {}, identity: { profile: 'a'.repeat(64), category: 'subscription' } },
  { makeTemporary: () => ({ path: '/tmp', cleanup() {} }), makeQuery: options => new Probe(options), graceMs: 0, killMs: 0 });
