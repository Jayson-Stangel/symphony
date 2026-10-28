import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { mkdtempSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { processIdentity, ownedGroupAlive, signalOwnedGroup } from '../processes.mjs';

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
    config: { model: 'sonnet', max_turns: 20, max_agentic_turns: 5, turn_timeout_ms: 10_000 }, tools: [], instructions: '' }) + '\n');
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

test('cleanup of an empty owned-process record is idempotent', { skip: process.platform !== 'linux', timeout: 5000 }, async () => {
  const cleanup = spawn(process.execPath, [new URL('../cleanup.mjs', import.meta.url).pathname, JSON.stringify({ helper: null, native: [] })]);
  cleanup.stderr.resume();
  assert.equal((await once(cleanup, 'exit'))[0], 0);
});

test('fallback cleanup repeats the same formerly-live record without affecting another session', { skip: process.platform !== 'linux', timeout: 15_000 }, async t => {
  const root = mkdtempSync(join(tmpdir(), 'symphony-repeat-cleanup-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const peers = [];
  t.after(() => { for (const peer of peers) {
    try { peer.helper.kill('SIGKILL'); } catch {}
    if (peer.owned) signalOwnedGroup(peer.owned, 'SIGKILL');
  } });
  for (let n = 0; n < 2; n++) {
    const sentinel = join(root, `delayed-${n}`);
    // Both real helper sessions must outlive two fallback cleanup grace periods.
    const helper = spawn(process.execPath, [new URL('./fixtures/background-probe.mjs', import.meta.url).pathname, sentinel, '30'], { cwd: root });
    helper.stderr.resume();
    const peer = { helper, sentinel, owned: null };
    peers.push(peer);
    const lines = createInterface({ input: helper.stdout })[Symbol.asyncIterator]();
    assert.equal(JSON.parse((await lines.next()).value).type, 'hello');
    helper.stdin.write(JSON.stringify({ v: 1, type: 'start', session_id: `repeat-session-${n}`, request_id: 'start', workspace: root,
      config: { model: 'sonnet', max_turns: 20, max_agentic_turns: 5, turn_timeout_ms: 10_000 }, tools: [], instructions: '' }) + '\n');
    const ready = JSON.parse((await lines.next()).value);
    assert.equal(ready.type, 'ready');
    assert.equal(ready.owned_native.length, 1);
    peer.owned = ready.owned_native[0];
    peer.helperIdentity = processIdentity(helper.pid);
    assert.ok(peer.helperIdentity);
  }
  assert.notEqual(peers[0].owned.pid, peers[1].owned.pid);
  assert.ok(peers.every(peer => ownedGroupAlive(peer.owned)));
  const first = peers[0], survivor = peers[1];
  const record = JSON.stringify({ helper: { pid: first.helperIdentity.pid, start: first.helperIdentity.start }, native: [first.owned] });
  // Reuse these exact nonempty identities after the first invocation removes them.
  for (const attempt of [1, 2]) {
    const cleanup = spawn(process.execPath, [new URL('../cleanup.mjs', import.meta.url).pathname, record]);
    let diagnostics = '';
    cleanup.stderr.setEncoding('utf8');
    cleanup.stderr.on('data', chunk => { diagnostics += chunk; });
    assert.equal((await once(cleanup, 'exit'))[0], 0, `cleanup ${attempt}: ${diagnostics}`);
    assert.equal(ownedGroupAlive(first.owned), false);
    const stoppedHelper = processIdentity(first.helperIdentity.pid);
    assert.ok(!stoppedHelper || stoppedHelper.start !== first.helperIdentity.start || ['Z', 'X'].includes(stoppedHelper.state));
    const survivingHelper = processIdentity(survivor.helper.pid);
    assert.equal(survivingHelper?.start, survivor.helperIdentity.start);
    assert.ok(!['Z', 'X'].includes(survivingHelper.state));
    assert.ok(ownedGroupAlive(survivor.owned), `unrelated session survives cleanup ${attempt}`);
    assert.equal(existsSync(first.sentinel), false);
  }
});

test('three concurrent helper groups survive another session cleanup independently', { skip: process.platform !== 'linux', timeout: 15_000 }, async t => {
  const root = mkdtempSync(join(tmpdir(), 'symphony-concurrent-process-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const peers = [];
  t.after(() => { for (const peer of peers) {
    try { peer.helper.kill('SIGKILL'); } catch {}
    if (peer.owned) signalOwnedGroup(peer.owned, 'SIGKILL');
  } });
  for (let n = 0; n < 3; n++) {
    const sentinel = join(root, `delayed-${n}`);
    const helper = spawn(process.execPath, [new URL('./fixtures/background-probe.mjs', import.meta.url).pathname, sentinel], { cwd: root });
    helper.stderr.resume();
    const peer = { helper, sentinel, owned: null };
    peers.push(peer);
    const lines = createInterface({ input: helper.stdout })[Symbol.asyncIterator]();
    assert.equal(JSON.parse((await lines.next()).value).type, 'hello');
    helper.stdin.write(JSON.stringify({ v: 1, type: 'start', session_id: `session-${n}`, request_id: 'start', workspace: root,
      config: { model: 'sonnet', max_turns: 20, max_agentic_turns: 5, turn_timeout_ms: 10_000 }, tools: [], instructions: '' }) + '\n');
    const ready = JSON.parse((await lines.next()).value);
    assert.equal(ready.type, 'ready');
    assert.equal(ready.owned_native.length, 1);
    peer.owned = ready.owned_native[0];
  }
  assert.equal(new Set(peers.map(peer => peer.owned.pid)).size, 3);
  assert.ok(peers.every(peer => ownedGroupAlive(peer.owned)));
  let exited = once(peers[0].helper, 'exit');
  peers[0].helper.stdin.end();
  await exited;
  assert.equal(ownedGroupAlive(peers[0].owned), false);
  assert.ok(peers.slice(1).every(peer => ownedGroupAlive(peer.owned)));
  for (const peer of peers.slice(1)) {
    exited = once(peer.helper, 'exit');
    peer.helper.kill('SIGTERM');
    await exited;
  }
  await delay(4200);
  assert.ok(peers.every(peer => !ownedGroupAlive(peer.owned) && !existsSync(peer.sentinel)));
});
