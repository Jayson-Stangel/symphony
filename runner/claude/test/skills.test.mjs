import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { skillPlugin } from '../skills.mjs';

test('private skill overlay preserves canonical helpers and local procedures without checkout changes', t => {
  const root = mkdtempSync(join(tmpdir(), 'symphony-skill-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const canonical = join(root, 'canonical'), workspace = join(root, 'repo');
  for (const name of ['commit', 'push', 'pull', 'linear', 'land']) {
    const dir = join(canonical, 'skills', `symphony-${name}`);
    mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, 'SKILL.md'), `canonical-${name}`);
  }
  const local = join(workspace, '.codex/skills/land');
  mkdirSync(local, { recursive: true }); writeFileSync(join(local, 'SKILL.md'), 'local-land');
  writeFileSync(join(local, 'land_watch.py'), 'helper');
  const plugin = skillPlugin(workspace, { 'symphony-land': local }, canonical);
  t.after(plugin.cleanup);
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/SKILL.md'), 'utf8'), 'local-land');
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/land_watch.py'), 'utf8'), 'helper');
  assert.equal(readFileSync(join(plugin.root, 'symphony-push/SKILL.md'), 'utf8'), 'canonical-push');
  assert.equal(readFileSync(join(local, 'SKILL.md'), 'utf8'), 'local-land');
  assert.equal(existsSync(join(workspace, '.claude')), false);
  symlinkSync('/etc/passwd', join(local, 'escape'));
  assert.throws(() => skillPlugin(workspace, { 'symphony-land': local }, canonical), /unsafe_skill_override/);
  assert.throws(() => skillPlugin(workspace, { 'symphony-land': canonical }, canonical), /unsafe_skill_override/);
});
