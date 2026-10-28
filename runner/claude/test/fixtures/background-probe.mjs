import { serve } from '../../helper.mjs';
class Background {
  constructor({ options }) {
    const child = options.spawnClaudeCodeProcess({ command: '/bin/sh', args: ['-c',
      'trap "" TERM; sleep "$2"; printf leaked > "$1"', 'probe', process.argv[2], process.argv[3] ?? '4'], env: process.env, cwd: process.cwd() });
    child.stdout.resume();
  }
  initializationResult() { return Promise.resolve({}); }
  [Symbol.asyncIterator]() { return this; }
  next() { return new Promise(() => {}); }
  close() {}
}
serve({ binary: '/unused', env: {}, identity: { profile: 'a'.repeat(64) } },
  { makeTemporary: () => ({ path: '/tmp', cleanup() {} }), makeQuery: options => new Background(options), graceMs: 20, killMs: 20 });
