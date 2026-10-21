import { cpSync, lstatSync, mkdtempSync, readdirSync, realpathSync, rmSync, writeFileSync, mkdirSync, symlinkSync, statSync } from 'node:fs';
import { join, relative, isAbsolute } from 'node:path';
import { tmpdir } from 'node:os';
import { RuntimeError } from './runtime.mjs';

const base = '/opt/symphony/claude/plugin';

export function skillPlugin(workspace, sources, canonical = base) {
  if (Object.keys(sources).length === 0) return { path: canonical, root: join(canonical, 'skills'), cleanup() {} };
  // The packaged plugin owns the available names; the protocol transports selections only.
  const canonicalRoot = join(canonical, 'skills');
  const names = readdirSync(canonicalRoot);
  if (!names.length || names.some(name => !/^[a-z][a-z0-9-]*$/.test(name) ||
      !statSync(join(canonicalRoot, name)).isDirectory() ||
      !statSync(join(canonicalRoot, name, 'SKILL.md')).isFile())) throw new RuntimeError('invalid_canonical_skills');
  if (Object.keys(sources).some(name => !names.includes(name))) throw new RuntimeError('unknown_skill_override');
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
      const destination = join(path, 'skills', name);
      if (Object.hasOwn(sources, name)) {
        const source = sources[name], resolved = realpathSync(source);
        const local = relative(join(realpathSync(workspace), '.codex/skills'), resolved);
        if (!local || local.startsWith('..') || isAbsolute(local) || !lstatSync(source).isDirectory() ||
            !lstatSync(join(resolved, 'SKILL.md')).isFile()) throw new RuntimeError('unsafe_skill_override');
        validate(resolved);
        cpSync(resolved, destination, { recursive: true, dereference: false, errorOnExist: true });
      } else {
        // Canonical files are immutable image contents. Only mutable overrides need a snapshot.
        symlinkSync(realpathSync(join(canonicalRoot, name)), destination, 'dir');
      }
    }
    return { path, root: join(path, 'skills'), cleanup };
  } catch (error) { cleanup(); throw error; }
}
