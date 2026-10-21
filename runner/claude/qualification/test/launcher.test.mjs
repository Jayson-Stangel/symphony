import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, cpSync, copyFileSync, mkdirSync, appendFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const launcher = fileURLToPath(new URL('../lab', import.meta.url));
const namespace = 'symphony-claude-qualification-01a15554';

function fixture(t, args, extraEnv = {}, executable = launcher) {
  const root = mkdtempSync(join(tmpdir(), 'symphony-claude-launcher-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const log = join(root, 'calls');
  writeFileSync(join(root, 'docker'), `#!${process.execPath}
import('node:fs').then(fs => {
  const args = process.argv.slice(2);
  fs.appendFileSync(process.env.LAB_CALLS, JSON.stringify(args) + '\\n');
  if (args[0] === 'build') {
    if (process.env.LAB_BUILD_FAIL === '1') process.exitCode = 1;
    else console.log(process.env.LAB_BUILD_IMAGE ?? 'sha256:qualification-fixture');
  } else if (args[0] === 'image' && args[1] === 'inspect') {
    console.log(args[3].includes('.Id') ? 'sha256:stale-fixture' : process.env.LAB_IMAGE_OWNER);
  } else if (args[0] === 'volume' && args[1] === 'inspect' && args.includes('--format')) {
    console.log(process.env.LAB_VOLUME_OWNER);
  }
});
`, { mode: 0o755 });
  const result = spawnSync('bash', [executable, ...args], {
    env: { PATH: `${root}:${process.env.PATH}`, LAB_CALLS: log,
      LAB_IMAGE_OWNER: namespace, LAB_VOLUME_OWNER: namespace, ...extraEnv },
    encoding: 'utf8', timeout: 10_000,
  });
  const calls = (() => { try { return readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse); } catch { return []; } })();
  return { ...result, calls };
}

test('live without explicit overage confirmation fails before any Docker action', t => {
  for (const command of ['live', 'cancel']) {
    const result = fixture(t, [command]);
    assert.equal(result.status, 1);
    assert.deepEqual(result.calls, []);
  }
});

test('foreign image or auth volume is never used or replaced', t => {
  const image = fixture(t, ['inspect'], { LAB_IMAGE_OWNER: 'another-lab' });
  assert.equal(image.status, 1);
  assert.equal(image.calls.some(args => ['volume', 'run'].includes(args[0])), false);
  const volume = fixture(t, ['inspect'], { LAB_VOLUME_OWNER: 'another-lab' });
  assert.equal(volume.status, 1);
  assert.equal(volume.calls.some(args => args[0] === 'run'), false);
});

test('inspect uses immutable lab image and only dedicated volumes without network', t => {
  const result = fixture(t, ['inspect'], { GH_TOKEN: 'secret-canary', ANTHROPIC_API_KEY: 'secret-canary' });
  assert.equal(result.status, 0, result.stderr);
  const call = result.calls.find(args => args[0] === 'run');
  assert.ok(call.includes('sha256:qualification-fixture'));
  assert.ok(call.includes('--read-only'));
  assert.ok(call.includes('no-new-privileges'));
  assert.equal(call[call.indexOf('--network') + 1], 'none');
  const mounts = call.filter((value, i) => call[i - 1] === '--mount');
  assert.deepEqual(mounts, [
    `type=volume,source=${namespace}-auth,target=/home/claude`,
    `type=volume,source=${namespace}-workspace,target=/lab-workspace`,
  ]);
  for (const forbidden of ['--env-file', '--privileged', '--pid', '--env']) assert.ok(!call.includes(forbidden));
  assert.ok(!JSON.stringify(result.calls).includes('secret-canary'));
});

test('native login gets a terminal and unknown arguments cannot reach Docker', t => {
  const login = fixture(t, ['login']);
  assert.equal(login.status, 0, login.stderr);
  assert.ok(login.calls.find(args => args[0] === 'run').includes('-it'));
  const invalid = fixture(t, ['login', '--extra-flag']);
  assert.equal(invalid.status, 1);
  assert.deepEqual(invalid.calls, []);
});


test('build and offline test reuse the production dependency owner without starting live work', t => {
  const build = fixture(t, ['build']);
  assert.equal(build.status, 0, build.stderr);
  const args = build.calls.find(call => call[0] === 'build');
  assert.ok(args[args.indexOf('--file') + 1].endsWith('/qualification/Dockerfile'));
  assert.ok(args.at(-1).endsWith('/qualification/..'));
  assert.ok(args.includes(`${namespace}:runtime`));
  const offline = fixture(t, ['test']);
  assert.equal(offline.status, 0, offline.stderr);
  const run = offline.calls.find(call => call[0] === 'run');
  assert.equal(run[run.indexOf('--network') + 1], 'none');
  assert.ok(run.includes('node --test test/*.test.mjs'));
  assert.ok(run.includes('/opt/symphony-claude-lab/qualification'));
});


test('changed checkout inputs must build successfully before an old tagged image can execute', t => {
  const checkout = mkdtempSync(join(tmpdir(), 'symphony-claude-changed-inputs-'));
  t.after(() => rmSync(checkout, { recursive: true, force: true }));
  const lab = join(checkout, 'qualification');
  mkdirSync(lab);
  cpSync(new URL('../', import.meta.url), lab, { recursive: true,
    filter: source => !source.split('/').includes('node_modules') });
  for (const name of ['package.json', 'package-lock.json', 'runtime.mjs']) {
    copyFileSync(new URL(`../../${name}`, import.meta.url), join(checkout, name));
  }
  // Image inputs are read-only; the owned temporary checkout must permit edits and cleanup.
  const writable = spawnSync('chmod', ['-R', 'u+w', checkout], { encoding: 'utf8' });
  assert.equal(writable.status, 0, writable.stderr);
  const manifest = JSON.parse(readFileSync(join(checkout, 'package.json'), 'utf8'));
  manifest.description = 'Changed checkout manifest.';
  writeFileSync(join(checkout, 'package.json'), JSON.stringify(manifest));
  appendFileSync(join(lab, 'lab.mjs'), '\n// Changed qualification harness.\n');
  const executable = join(lab, 'lab');

  // The fake Docker runtime retains an old tag, but any failed build of this
  // changed checkout must stop before tag inspection, volume access or execution.
  for (const command of ['test', 'inspect', 'status', 'login', 'live', 'cancel']) {
    const args = ['live', 'cancel'].includes(command) ? [command, '--confirm-overages-disabled'] : [command];
    const failed = fixture(t, args, { LAB_BUILD_FAIL: '1' }, executable);
    assert.equal(failed.status, 1, failed.stderr);
    assert.equal(failed.calls.length, 1);
    assert.equal(failed.calls[0][0], 'build');
    assert.equal(failed.calls[0].at(-1), `${lab}/..`);
  }

  const fresh = fixture(t, ['test'], { LAB_BUILD_IMAGE: 'sha256:fresh-fixture' }, executable);
  assert.equal(fresh.status, 0, fresh.stderr);
  assert.ok(fresh.calls[0].includes('--quiet'));
  assert.equal(fresh.calls.find(args => args[0] === 'image').at(-1), 'sha256:fresh-fixture');
  const run = fresh.calls.find(args => args[0] === 'run');
  assert.ok(run.includes('sha256:fresh-fixture'));
  assert.ok(!run.includes('sha256:stale-fixture'));
  assert.ok(!run.includes(`${namespace}:runtime`));
});
