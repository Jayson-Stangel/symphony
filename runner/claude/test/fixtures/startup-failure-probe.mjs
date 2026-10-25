// Production helper lifecycle with a credential-free SDK initialization failure.
import { readFileSync, writeFileSync } from 'node:fs';
import { serve } from '../../helper.mjs';
import { processIdentity } from '../../processes.mjs';
class StartupFailure {
  constructor({ options }) {
    const child = options.spawnClaudeCodeProcess({ command: '/bin/sh', args: ['-c',
      'trap "" TERM; sleep 30'], env: process.env, cwd: process.cwd() });
    child.stdout.resume();
    writeFileSync('startup-owned.json', JSON.stringify(processIdentity(child.pid)));
  }
  initializationResult() {
    return ['stream', 'eof'].includes(readFileSync('startup-fail-mode', 'utf8'))
      ? new Promise(() => {}) : Promise.reject(new Error('fixture initialization failure'));
  }
  [Symbol.asyncIterator]() { return this; }
  next() {
    if (readFileSync('startup-fail-mode', 'utf8') === 'eof') return Promise.resolve({ done: true });
    return readFileSync('startup-fail-mode', 'utf8') === 'stream'
      ? Promise.reject(new Error('fixture startup stream failure')) : new Promise(() => {});
  }
  close() {}
}
serve({ binary: '/unused', env: {}, identity: { profile: 'a'.repeat(64) } },
  { makeTemporary: () => ({ path: '/tmp', cleanup() {} }), makeQuery: options => new StartupFailure(options) });
