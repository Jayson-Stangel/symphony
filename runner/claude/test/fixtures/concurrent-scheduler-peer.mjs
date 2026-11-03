// Mounted only into a disposable Linux test image at the adapter's fixed helper
// path. Uses the production helper/ownership code with a non-inference query peer.
import { appendFileSync, existsSync, readFileSync, unlinkSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { serve } from '/opt/symphony/runner/claude/helper.mjs';
import { runtime } from '/opt/symphony/runner/claude/runtime.mjs';

const identity = { profile: 'a'.repeat(64), category: 'subscription' };
if (process.argv[2] === 'check') {
  console.log(JSON.stringify({ ready: true, ...identity, ...runtime }));
} else {
  class WaitingQuery {
    constructor({ prompt, options }) {
      this.queryId = randomUUID();
      this.systemPrompt = options.systemPrompt;
      this.child = options.spawnClaudeCodeProcess({ command: '/bin/sh', args: ['-c', 'sleep 600'],
        cwd: process.cwd(), env: {}, stdio: ['pipe', 'pipe', 'pipe'] });
      this.child.stdout.resume();
      this.child.stderr.resume();
      this.queue = [];
      this.timer = setInterval(() => {
        if (existsSync('fail-once')) process.exit(2);
        if (this.active && existsSync('complete-once') && readFileSync('complete-once', 'utf8') === this.active.uuid) {
          unlinkSync('complete-once');
          const input = this.active; this.active = null;
          this.push({ type: 'result', subtype: 'success', is_error: false, uuid: randomUUID(),
            session_id: `native-${process.pid}`, user_message_uuid: input.uuid });
        }
      }, 10);
      void this.inputs(prompt);
    }
    async inputs(prompt) {
      for await (const input of prompt) {
        this.active = input;
        this.push({ type: 'system', subtype: 'init', session_id: `native-${process.pid}` });
        // This is the actual production Helper.input() iterator passed to query(),
        // before inference. Retain the complete SDK-shaped input, not a model echo.
        const content = input.message.content;
        appendFileSync('turns', JSON.stringify({ pid: process.pid, input_id: input.uuid, input,
          query_id: this.queryId, child_pid: this.child.pid, system_prompt: this.systemPrompt,
          bytes: Buffer.byteLength(content), sha256: createHash('sha256').update(content).digest('hex') }) + '\n');
      }
    }
    initializationResult() { return Promise.resolve({}); }
    [Symbol.asyncIterator]() { return this; }
    next() {
      if (this.queue.length) return Promise.resolve({ value: this.queue.shift(), done: false });
      if (this.done) return Promise.resolve({ done: true });
      return new Promise(resolve => { this.waiter = resolve; });
    }
    push(value) {
      if (this.waiter) { const waiter = this.waiter; this.waiter = null; waiter({ value, done: false }); }
      else this.queue.push(value);
    }
    interrupt() { return Promise.resolve(); }
    close() { this.done = true; clearInterval(this.timer); this.waiter?.({ done: true }); }
  }
  serve({ binary: '/unused-no-inference', env: {}, identity },
    { makeTemporary: () => ({ path: '/tmp', cleanup() {} }), makeQuery: options => new WaitingQuery(options), graceMs: 20, killMs: 20 });
}
