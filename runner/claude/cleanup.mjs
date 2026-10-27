// Internal last-resort cleanup for a worker that cannot service its Port.
// The caller supplies only identities captured from its own helper handshake.
import { setTimeout as delay } from 'node:timers/promises';
import { z } from 'zod';
import { cleanupBuildTemporary } from './temporary.mjs';
import { processIdentity, signalOwnedGroup, ownedGroupAlive } from './processes.mjs';

const identity = z.object({ pid: z.number().int().min(2), start: z.string().regex(/^\d+$/) }).strict();
const schema = z.object({ helper: identity.nullable(), native: z.array(identity).max(1) }).strict();

function signalHelper(owned, signal) {
  if (!owned || processIdentity(owned.pid)?.start !== owned.start) return;
  try { process.kill(owned.pid, signal); } catch { /* Already gone. */ }
}

async function main() {
  if (process.platform !== 'linux' || process.argv.length !== 3 || process.argv[2].length > 2048) throw new Error('invalid_cleanup');
  const owned = schema.parse(JSON.parse(process.argv[2]));
  signalHelper(owned.helper, 'SIGTERM');
  await delay(2_500);
  for (const native of owned.native) signalOwnedGroup(native, 'SIGTERM');
  await delay(250);
  for (const native of owned.native) signalOwnedGroup(native, 'SIGKILL');
  signalHelper(owned.helper, 'SIGKILL');
  await delay(100);
  const helper = owned.helper && processIdentity(owned.helper.pid);
  if (owned.native.some(ownedGroupAlive) || (owned.helper && helper?.start === owned.helper.start && !['Z', 'X'].includes(helper?.state))) {
    throw new Error('cleanup_unconfirmed');
  }
  cleanupBuildTemporary(owned.helper);
}

main().catch(() => { console.error('owned_process_cleanup_failed'); process.exitCode = 1; });
