import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { mkdtempSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { processIdentity, ownedGroupAlive } from '../processes.mjs';

for (const mode of ['eof', 'signal', 'owner-death']) test(`Linux owned subprocess cleanup: ${mode}`, { skip: process.platform !== 'linux', timeout: 12_000 }, async t => {
  const root = mkdtempSync(join(tmpdir(), 'symphony-process-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const sentinel = join(root, 'delayed-write');
  const helper = spawn(process.execPath, [new URL('./fixtures/background-probe.mjs', import.meta.url).pathname, sentinel], { cwd: root });
  t.after(() => { try { helper.kill('SIGKILL'); } catch {} });
  helper.stderr.resume();
  const lines = createInterface({ input: helper.stdout })[Symbol.asyncIterator]();
  assert.equal(JSON.parse((await lines.next()).value).type, 'hello');
  helper.stdin.write(JSON.stringify({ v: 1, type: 'start', session_id: 'session', request_id: 'start', workspace: root,
    config: { model: 'sonnet', max_agentic_turns: 5, turn_timeout_ms: 10_000 }, tools: [], instructions: '' }) + '\n');
  const ready = JSON.parse((await lines.next()).value);
  assert.equal(ready.type, 'ready');
  assert.equal(ready.owned_native.length, 1);
  const helperOwned = processIdentity(helper.pid);
  const exit = once(helper, 'exit');
  if (mode === 'eof') helper.stdin.end();
  else helper.kill(mode === 'signal' ? 'SIGTERM' : 'SIGKILL');
  await exit;
  if (mode === 'owner-death') {
    // Production fallback used when the OTP Port owner cannot service its helper.
    // The delayed child must remain alive long enough to prove cleanup prevents it.
    const cleanup = spawn(process.execPath, [new URL('../cleanup.mjs', import.meta.url).pathname,
      JSON.stringify({ helper: { pid: helperOwned.pid, start: helperOwned.start }, native: ready.owned_native })]);
    cleanup.stderr.resume();
    assert.equal((await once(cleanup, 'exit'))[0], 0);
  }
  await delay(4200);
  assert.equal(existsSync(sentinel), false);
  assert.equal(ready.owned_native.some(ownedGroupAlive), false);
});
