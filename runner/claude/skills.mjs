import { cpSync, lstatSync, mkdtempSync, readdirSync, realpathSync, rmSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, relative, isAbsolute } from 'node:path';
import { tmpdir } from 'node:os';
import { RuntimeError } from './runtime.mjs';

const names = new Set(['symphony-commit', 'symphony-push', 'symphony-pull', 'symphony-linear', 'symphony-land']);
const base = '/opt/symphony/claude/plugin';

export function skillPlugin(workspace, sources, canonical = base) {
  if (Object.keys(sources).length === 0) return { path: canonical, root: join(canonical, 'skills'), cleanup() {} };
  const path = mkdtempSync(join(tmpdir(), 'symphony-skills-'));
  const cleanup = () => rmSync(path, { recursive: true, force: true });
  let count = 0, bytes = 0;
  function validate(source) {
    const stat = lstatSync(source);
    if (++count > 512 || (bytes += stat.size) > 2_097_152 || (!stat.isDirectory() && !stat.isFile())) throw new RuntimeError('unsafe_skill_override');
    if (stat.isDirectory()) for (const child of readdirSync(source)) validate(join(source, child));
  }
  try {
    mkdirSync(join(path, '.claude-plugin'));
    mkdirSync(join(path, 'skills'));
    writeFileSync(join(path, '.claude-plugin/plugin.json'), JSON.stringify({ name: 'symphony', version: '1.0.0' }));
    for (const name of names) {
      let source = sources[name];
      if (source) {
        const resolved = realpathSync(source), local = relative(realpathSync(join(workspace, '.codex/skills')), resolved);
        if (!local || local.startsWith('..') || isAbsolute(local)) throw new RuntimeError('unsafe_skill_override');
        validate(resolved);
        source = resolved;
      } else source = realpathSync(join(canonical, 'skills', name));
      cpSync(source, join(path, 'skills', name), { recursive: true, dereference: false, errorOnExist: true });
    }
    if (Object.keys(sources).some(name => !names.has(name))) throw new RuntimeError('unknown_skill_override');
    return { path, root: join(path, 'skills'), cleanup };
  } catch (error) { cleanup(); throw error; }
}
