import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync, symlinkSync, lstatSync, realpathSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { skillPlugin } from '../skills.mjs';
import { nativeExecutable } from '../runtime.mjs';
import { query } from '@anthropic-ai/claude-agent-sdk';

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'symphony-skill-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const canonical = join(root, 'canonical'), workspace = join(root, 'repo');
  for (const name of ['commit', 'push', 'pull', 'linear', 'land']) {
    const dir = join(root, 'packaged', name);
    mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, 'SKILL.md'), `canonical-${name}`);
    writeFileSync(join(dir, 'helper.py'), 'from pathlib import Path\nprint(Path(__file__).with_name("SKILL.md").read_text())\n');
    mkdirSync(join(canonical, 'skills'), { recursive: true });
    symlinkSync(dir, join(canonical, 'skills', `symphony-${name}`), 'dir');
  }
  const local = join(workspace, '.codex/skills/land');
  mkdirSync(local, { recursive: true }); writeFileSync(join(local, 'SKILL.md'), 'local-land');
  writeFileSync(join(local, 'land_watch.py'), 'helper');
  return { root, canonical, workspace, local };
}

test('canonical sessions load the packaged plugin directly and cleanup preserves it', t => {
  const { canonical, workspace } = fixture(t);
  const plugin = skillPlugin(workspace, {}, canonical);
  assert.equal(plugin.path, canonical);
  plugin.cleanup();
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/SKILL.md'), 'utf8'), 'canonical-land');
});

test('mixed sessions snapshot only local procedures and link packaged helpers without checkout changes', t => {
  const { canonical, workspace, local } = fixture(t);
  const plugin = skillPlugin(workspace, { 'symphony-land': local }, canonical);
  t.after(plugin.cleanup);
  assert.equal(lstatSync(join(plugin.root, 'symphony-land')).isSymbolicLink(), false);
  assert.equal(lstatSync(join(plugin.root, 'symphony-push')).isSymbolicLink(), true);
  assert.equal(realpathSync(join(plugin.root, 'symphony-push')), realpathSync(join(canonical, 'skills/symphony-push')));
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/SKILL.md'), 'utf8'), 'local-land');
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/land_watch.py'), 'utf8'), 'helper');
  assert.equal(readFileSync(join(plugin.root, 'symphony-push/SKILL.md'), 'utf8'), 'canonical-push');
  const helper = spawnSync('python3', [join(plugin.root, 'symphony-push/helper.py')], { encoding: 'utf8' });
  assert.equal(helper.status, 0, helper.stderr);
  assert.equal(helper.stdout.trim(), 'canonical-push');
  writeFileSync(join(local, 'SKILL.md'), 'changed-after-start');
  assert.equal(readFileSync(join(plugin.root, 'symphony-land/SKILL.md'), 'utf8'), 'local-land');
  assert.equal(existsSync(join(workspace, '.claude')), false);
  plugin.cleanup();
  assert.equal(existsSync(plugin.path), false);
  assert.equal(readFileSync(join(local, 'SKILL.md'), 'utf8'), 'changed-after-start');
  assert.equal(readFileSync(join(canonical, 'skills/symphony-push/SKILL.md'), 'utf8'), 'canonical-push');
});

test('the packaged artifact owns names and inherited selections cannot replace canonical files', t => {
  const { canonical, workspace, local } = fixture(t);
  assert.throws(() => skillPlugin(workspace, { 'unknown-procedure': local }, canonical), /unknown_skill_override/);
  assert.throws(() => skillPlugin(workspace, JSON.parse('{"__proto__":"escape"}'), canonical), /unknown_skill_override/);
  const inherited = Object.assign(Object.create({ 'symphony-push': local }), { 'symphony-land': local });
  const plugin = skillPlugin(workspace, inherited, canonical);
  t.after(plugin.cleanup);
  assert.equal(readFileSync(join(plugin.root, 'symphony-push/SKILL.md'), 'utf8'), 'canonical-push');
  writeFileSync(join(canonical, 'skills/not-a-procedure'), 'invalid');
  assert.throws(() => skillPlugin(workspace, { 'symphony-land': local }, canonical), /invalid_canonical_skills/);
});

test('local overrides reject escapes, symlinks, missing procedures and oversized snapshots', t => {
  const { canonical, workspace, local, root } = fixture(t);
  const build = source => skillPlugin(workspace, { 'symphony-land': source }, canonical);
  assert.throws(() => build(canonical), /unsafe_skill_override/);
  symlinkSync('/etc/passwd', join(local, 'escape'));
  assert.throws(() => build(local), /unsafe_skill_override/);
  rmSync(join(local, 'escape'));
  symlinkSync(local, join(workspace, '.codex/skills/link'), 'dir');
  assert.throws(() => build(join(workspace, '.codex/skills/link')), /unsafe_skill_override/);
  rmSync(join(local, 'SKILL.md'));
  assert.throws(() => build(local));
  symlinkSync(join(canonical, 'skills/symphony-land/SKILL.md'), join(local, 'SKILL.md'));
  assert.throws(() => build(local), /unsafe_skill_override/);
  rmSync(join(local, 'SKILL.md'));
  writeFileSync(join(local, 'SKILL.md'), 'local-land');
  writeFileSync(join(local, 'too-large'), Buffer.alloc(2_097_153));
  assert.throws(() => build(local), /unsafe_skill_override/);
  rmSync(join(local, 'too-large'));
  for (let i = 0; i < 512; i++) writeFileSync(join(local, `file-${i}`), '');
  assert.throws(() => build(local), /unsafe_skill_override/);
  rmSync(join(workspace, '.codex/skills'), { recursive: true });
  symlinkSync(join(root, 'packaged'), join(workspace, '.codex/skills'), 'dir');
  assert.throws(() => build(join(workspace, '.codex/skills/land')), /unsafe_skill_override/);
});

// The runner image supplies the pinned native runtime and immutable plugin.
// Initialization receives no user message, so this proof needs no inference credentials.
test('native initialization discovers canonical links and the local override together', {
  skip: process.platform !== 'linux' || !existsSync('/opt/symphony/claude/plugin/skills'), timeout: 20_000,
}, async t => {
  const { workspace, local } = fixture(t);
  writeFileSync(join(local, 'SKILL.md'), '---\nname: symphony-land\ndescription: Local override discovery proof\n---\nFollow local procedure.\n');
  const plugin = skillPlugin(workspace, { 'symphony-land': local });
  t.after(plugin.cleanup);
  let release;
  const idle = new Promise(resolve => { release = resolve; });
  async function* input() { await idle; }
  const session = query({ prompt: input(), options: {
    cwd: workspace, env: { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude',
      PATH: '/usr/local/bin:/usr/bin:/bin', DISABLE_AUTOUPDATER: '1' },
    pathToClaudeCodeExecutable: nativeExecutable(), settingSources: [],
    plugins: [{ type: 'local', path: plugin.path }], skills: 'all',
    strictMcpConfig: true, mcpServers: {}, persistSession: false,
    permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true, stderr: () => {},
  } });
  try {
    await session.initializationResult();
    const commands = await session.supportedCommands();
    for (const name of ['commit', 'push', 'pull', 'linear', 'land']) {
      assert.ok(commands.some(command => command.name === `symphony:symphony-${name}`), `missing ${name}`);
    }
    assert.match(commands.find(command => command.name === 'symphony:symphony-land').description, /Local override discovery proof/);
  } finally { session.close(); release(); }
});

test('three native no-model sessions retain independent skill overlays after one closes', {
  skip: process.platform !== 'linux' || !existsSync('/opt/symphony/claude/plugin/skills'), timeout: 30_000,
}, async t => {
  const peers = [];
  t.after(() => { for (const peer of peers) { peer.session.close(); peer.release(); peer.plugin.cleanup(); } });
  for (let n = 0; n < 3; n++) {
    const { workspace, local } = fixture(t);
    writeFileSync(join(local, 'SKILL.md'), `---\nname: symphony-land\ndescription: Independent override ${n}\n---\nLocal procedure ${n}.\n`);
    const plugin = skillPlugin(workspace, { 'symphony-land': local });
    let release;
    const idle = new Promise(resolve => { release = resolve; });
    async function* input() { await idle; }
    const session = query({ prompt: input(), options: {
      cwd: workspace, env: { HOME: '/home/claude', CLAUDE_CONFIG_DIR: '/home/claude/.claude',
        PATH: '/usr/local/bin:/usr/bin:/bin', DISABLE_AUTOUPDATER: '1' },
      pathToClaudeCodeExecutable: nativeExecutable(), settingSources: [],
      plugins: [{ type: 'local', path: plugin.path }], skills: 'all',
      strictMcpConfig: true, mcpServers: {}, persistSession: false,
      permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true, stderr: () => {},
    } });
    peers.push({ session, release, plugin });
  }
  await Promise.all(peers.map(peer => peer.session.initializationResult()));
  assert.equal(new Set(peers.map(peer => peer.plugin.path)).size, 3);
  for (const [n, peer] of peers.entries()) {
    const commands = await peer.session.supportedCommands();
    assert.match(commands.find(command => command.name === 'symphony:symphony-land').description, new RegExp(`Independent override ${n}`));
  }
  peers[0].session.close(); peers[0].release(); peers[0].plugin.cleanup();
  for (const [n, peer] of peers.entries()) if (n > 0) {
    const commands = await peer.session.supportedCommands();
    assert.match(commands.find(command => command.name === 'symphony:symphony-land').description, new RegExp(`Independent override ${n}`));
    assert.equal(existsSync(peer.plugin.path), true);
  }
});
