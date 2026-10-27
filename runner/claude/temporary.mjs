import { lstatSync, realpathSync, mkdirSync, writeFileSync, readFileSync, rmSync, rmdirSync } from 'node:fs';
import { join } from 'node:path';
import { buildScratch, RuntimeError } from './runtime.mjs';
import { processIdentity } from './processes.mjs';

function pathFor(owned, root) {
  if (!owned || !Number.isSafeInteger(owned.pid) || owned.pid <= 1 || !/^\d+$/.test(owned.start)) {
    throw new RuntimeError('build_scratch_cleanup_unconfirmed');
  }
  const parent = lstatSync(root);
  if (!parent.isDirectory() || parent.isSymbolicLink() || realpathSync(root) !== root
    || parent.uid !== process.getuid() || (parent.mode & 0o777) !== 0o700) {
    throw new RuntimeError('build_scratch_cleanup_unconfirmed');
  }
  return join(root, `helper-${owned.pid}-${owned.start}`);
}

export function createBuildTemporary(owned = processIdentity(process.pid), root = buildScratch) {
  const path = pathFor(owned, root);
  mkdirSync(path, { mode: 0o700 });
  try {
    const stat = lstatSync(path);
    writeFileSync(join(path, '.symphony-owner'), JSON.stringify({ pid: owned.pid, start: owned.start,
      device: stat.dev, inode: stat.ino }), { flag: 'wx', mode: 0o600 });
  } catch (error) {
    rmdirSync(path); // Only the newly owned empty directory is removable without its marker.
    throw error;
  }
  return { path, cleanup: () => cleanupBuildTemporary(owned, root) };
}

// Call only after the helper's owned native groups are confirmed dead. The
// external cleanup path also confirms helper death before reaching this point.
export function cleanupBuildTemporary(owned, root = buildScratch) {
  if (!owned) return;
  let path, stat;
  try { path = pathFor(owned, root); stat = lstatSync(path); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  if (!stat.isDirectory() || stat.isSymbolicLink() || realpathSync(path) !== path
    || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o700) {
    throw new RuntimeError('build_scratch_cleanup_unconfirmed');
  }
  const marker = join(path, '.symphony-owner');
  let markerStat;
  try { markerStat = lstatSync(marker); }
  catch (error) {
    if (error.code !== 'ENOENT') throw error;
    rmdirSync(path); // A crash between mkdir and marker publication can leave only an empty directory.
    return;
  }
  if (!markerStat.isFile() || markerStat.isSymbolicLink() || markerStat.uid !== process.getuid()
    || (markerStat.mode & 0o777) !== 0o600 || markerStat.size > 256
    || readFileSync(marker, 'utf8') !== JSON.stringify({ pid: owned.pid, start: owned.start,
      device: stat.dev, inode: stat.ino })) throw new RuntimeError('build_scratch_cleanup_unconfirmed');
  rmSync(path, { recursive: true }); // Child symlinks are removed, never traversed.
}
