import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, realpathSync, mkdirSync, writeFileSync, readFileSync, readdirSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createBuildTemporary, cleanupBuildTemporary } from '../temporary.mjs';
function fixture(t) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'symphony-scratch-test-')));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return { root, owned: { pid: process.pid, start: '12345' } };
}
test('two sequential owned helper cycles reclaim scratch and preserve unrelated helpers', t => {
  const { root, owned } = fixture(t);
  const unrelated = createBuildTemporary({ ...owned, start: '12344' }, root);
  writeFileSync(join(unrelated.path, 'preserved'), 'other helper');
  for (const start of ['12345', '12346']) {
    const temporary = createBuildTemporary({ ...owned, start }, root);
    writeFileSync(join(temporary.path, 'build'), Buffer.alloc(1024 * 1024));
    temporary.cleanup(); temporary.cleanup();
    assert.deepEqual(readdirSync(root), [unrelated.path.split('/').at(-1)]);
    assert.equal(readFileSync(join(unrelated.path, 'preserved'), 'utf8'), 'other helper');
  }
  unrelated.cleanup(); assert.deepEqual(readdirSync(root), []);
});
test('cleanup rejects foreign ownership markers and preserves their contents', t => {
  const { root, owned } = fixture(t), temporary = createBuildTemporary(owned, root);
  writeFileSync(join(temporary.path, '.symphony-owner'), 'foreign owner');
  writeFileSync(join(temporary.path, 'preserved'), 'foreign content');
  assert.throws(() => temporary.cleanup(), /build_scratch_cleanup_unconfirmed/);
  assert.equal(readFileSync(join(temporary.path, 'preserved'), 'utf8'), 'foreign content');
});
test('cleanup never follows root, target or ownership-marker symlinks', t => {
  const { root, owned } = fixture(t), foreign = join(root, 'foreign'); mkdirSync(foreign);
  writeFileSync(join(foreign, 'preserved'), 'preserve');
  const alias = join(root, 'alias'); symlinkSync(foreign, alias);
  assert.throws(() => createBuildTemporary(owned, alias), /build_scratch_cleanup_unconfirmed/);
  const target = join(root, `helper-${owned.pid}-${owned.start}`); symlinkSync(foreign, target);
  assert.throws(() => cleanupBuildTemporary(owned, root), /build_scratch_cleanup_unconfirmed/);
  rmSync(target);
  const temporary = createBuildTemporary(owned, root);
  rmSync(join(temporary.path, '.symphony-owner'));
  symlinkSync(join(foreign, 'preserved'), join(temporary.path, '.symphony-owner'));
  assert.throws(() => temporary.cleanup(), /build_scratch_cleanup_unconfirmed/);
  assert.equal(readFileSync(join(foreign, 'preserved'), 'utf8'), 'preserve');
});
test('owned cleanup removes child links without touching foreign targets', t => {
  const { root, owned } = fixture(t), foreign = join(root, 'foreign'); mkdirSync(foreign);
  writeFileSync(join(foreign, 'preserved'), 'preserve');
  const temporary = createBuildTemporary(owned, root);
  symlinkSync(foreign, join(temporary.path, 'child'));
  temporary.cleanup(); assert.equal(readFileSync(join(foreign, 'preserved'), 'utf8'), 'preserve');
});
test('wrong generations preserve other helpers and incomplete creation removes only empty scratch', t => {
  const { root, owned } = fixture(t), temporary = createBuildTemporary(owned, root);
  cleanupBuildTemporary({ ...owned, start: '12346' }, root);
  assert.ok(readdirSync(temporary.path).includes('.symphony-owner')); temporary.cleanup();
  mkdirSync(temporary.path, { mode: 0o700 }); cleanupBuildTemporary(owned, root);
  assert.deepEqual(readdirSync(root), []);
  mkdirSync(temporary.path, { mode: 0o700 }); writeFileSync(join(temporary.path, 'foreign'), 'preserve');
  assert.throws(() => cleanupBuildTemporary(owned, root));
  assert.equal(readFileSync(join(temporary.path, 'foreign'), 'utf8'), 'preserve');
});
