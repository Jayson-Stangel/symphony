// Mounted only into a disposable Linux test image at the adapter's fixed helper
// path. Uses the production helper/ownership code with a non-inference query peer.
import { appendFileSync, existsSync, unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { serve } from '/opt/symphony/runner/claude/helper.mjs';

const identity = { profile: 'a'.repeat(64), category: 'subscription' };
if (process.argv[2] === 'check') {
  console.log(JSON.stringify({ ready: true, ...identity, sdk: '0.3.303', cli: '2.1.303',
    policy: 1, overages_disabled_attested: true, attested_at: new Date().toISOString() }));
} else {
  class WaitingQuery {
    constructor({ prompt, options }) {
      this.child = options.spawnClaudeCodeProcess({ command: '/bin/sh', args: ['-c', 'sleep 600'],
        cwd: process.cwd(), env: {}, stdio: ['pipe', 'pipe', 'pipe'] });
      this.child.stdout.resume();
      this.child.stderr.resume();
      this.queue = [];
      this.timer = setInterval(() => {
        if (existsSync('fail-once')) process.exit(2);
        if (this.active && existsSync('complete-once')) {
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
        appendFileSync('turns', JSON.stringify({ pid: process.pid, input }) + '\n');
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
    { makeQuery: options => new WaitingQuery(options), graceMs: 20, killMs: 20 });
}
