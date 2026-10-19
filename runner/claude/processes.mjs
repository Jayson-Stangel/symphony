import { readFileSync, readdirSync } from 'node:fs';

export function processIdentity(pid) {
  try {
    const text = readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = text.slice(text.lastIndexOf(')') + 2).trim().split(/\s+/);
    return { pid, group: Number(fields[2]), start: fields[19], state: fields[0] };
  } catch { return null; }
}

export function sameGroupGeneration(owned, leader, members) {
  if (!owned || owned.pid <= 1 || !/^\d+$/.test(owned.start)) return false;
  if (leader && (leader.start !== owned.start || leader.group !== owned.pid)) return false;
  return members.length > 0 && members.every(member => member.group === owned.pid
    && /^\d+$/.test(member.start) && BigInt(member.start) >= BigInt(owned.start));
}

export function signalOwnedGroup(owned, signal) {
  if (!owned) return;
  const leader = processIdentity(owned.pid);
  const members = groupMembers(owned);
  if (!sameGroupGeneration(owned, leader, members)) return;
  try { process.kill(-owned.pid, signal); } catch { /* Already exited or no longer ours. */ }
}

function groupMembers(owned) {
  return readdirSync('/proc').filter(name => /^\d+$/.test(name))
    .map(Number).map(processIdentity).filter(value => value?.group === owned.pid);
}

export function ownedGroupAlive(owned) {
  const members = groupMembers(owned);
  return sameGroupGeneration(owned, processIdentity(owned.pid), members)
    && members.some(member => !['Z', 'X'].includes(member.state));
}
