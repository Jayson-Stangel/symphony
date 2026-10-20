// Mounted only into a disposable Linux test image at the adapter's fixed helper
// path. Uses the production helper/ownership code with a non-inference query peer.
import { appendFileSync, existsSync } from 'node:fs';
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
      this.timer = setInterval(() => { if (existsSync('fail-once')) process.exit(2); }, 10);
      void this.inputs(prompt);
    }
    async inputs(prompt) {
      for await (const input of prompt) appendFileSync('turns', JSON.stringify({ pid: process.pid, input }) + '\n');
    }
    initializationResult() { return Promise.resolve({}); }
    [Symbol.asyncIterator]() { return this; }
    next() { return new Promise(resolve => { this.waiter = resolve; }); }
    interrupt() { return Promise.resolve(); }
    close() { clearInterval(this.timer); this.waiter?.({ done: true }); }
  }
  serve({ binary: '/unused-no-inference', env: {}, identity },
    { makeQuery: options => new WaitingQuery(options), graceMs: 20, killMs: 20 });
}
