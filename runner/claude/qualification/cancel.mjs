import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { LabError, terminalSummary } from './policy.mjs';

export const cancellationCommand = 'printf "%s" "$$" > /lab-workspace/cancel-pid; sleep 15; printf delayed > /lab-workspace/cancel-sentinel';

export function allowCancellationTool(name, input, used) {
  if (used || name !== 'Bash' || input?.command !== cancellationCommand || input.run_in_background === true) {
    return { behavior: 'deny', message: 'Only the single fixed qualification command is allowed.' };
  }
  return { behavior: 'allow', updatedInput: { command: cancellationCommand, timeout: 30_000, run_in_background: false } };
}

function processIdentity(pid) {
  try {
    const stat = readFileSync(`/proc/${pid}/stat`, 'utf8');
    const fields = stat.slice(stat.lastIndexOf(')') + 2).split(' ');
    return { pid, state: fields[0], parent: Number(fields[1]), start: fields[19] };
  } catch { return null; }
}

function ownedDescendants(pid) {
  const all = readdirSync('/proc').filter(name => /^\d+$/.test(name)).map(Number).map(processIdentity).filter(Boolean);
  const owned = new Set([pid]);
  for (let changed = true; changed;) {
    changed = false;
    for (const process of all) if (owned.has(process.parent) && !owned.has(process.pid)) {
      owned.add(process.pid); changed = true;
    }
  }
  return all.filter(process => owned.has(process.pid));
}

async function waitUntil(predicate, timeout) {
  const end = performance.now() + timeout;
  while (!predicate()) {
    if (performance.now() >= end) throw new LabError('cancellation_observation_timed_out');
    await delay(50);
  }
}

export async function runCancellation(binary, env) {
  for (const path of ['cancel-pid', 'cancel-sentinel']) {
    if (existsSync(`/lab-workspace/${path}`)) throw new LabError('cancellation_requires_unused_sentinels');
  }
  let permitted = false;
  let done = false;
  let release;
  let streamFailed = false;
  async function* input() {
    yield { type: 'user', session_id: '', parent_tool_use_id: null, message: { role: 'user', content:
      `Call Bash exactly once with this exact command, timeout 30000, run_in_background false: ${cancellationCommand}. Do not run any other command or tool.` } };
    if (!done) await new Promise(resolve => { release = resolve; });
  }
  const session = query({ prompt: input(), options: {
    cwd: '/lab-workspace', env, pathToClaudeCodeExecutable: binary,
    settingSources: [], strictMcpConfig: true, mcpServers: {}, tools: ['Bash'], allowedTools: [],
    permissionMode: 'default', maxTurns: 2, persistSession: false, model: 'sonnet',
    promptSuggestions: false, agentProgressSummaries: false, stderr: () => {},
    canUseTool: async (name, input) => {
      const result = allowCancellationTool(name, input, permitted);
      if (result.behavior === 'allow') permitted = true;
      return result;
    },
  } });
  const hardDeadline = setTimeout(() => {
    console.error('Claude qualification stopped: cancellation_deadline_exceeded');
    process.exit(124);
  }, 75_000);
  hardDeadline.unref();
  const consume = (async () => {
    for await (const event of session) {
      const result = terminalSummary(event);
      if (result) console.log(JSON.stringify({ ...result, type: 'cancellation_terminal' }));
    }
  })().catch(() => { streamFailed = true; });
  try {
    await waitUntil(() => existsSync('/lab-workspace/cancel-pid') || streamFailed, 45_000);
    if (streamFailed || !permitted) throw new LabError('cancellation_command_not_started');
    const text = readFileSync('/lab-workspace/cancel-pid', 'utf8');
    if (!/^\d+$/.test(text)) throw new LabError('unexpected_cancellation_pid');
    await delay(100); // Give the foreground sleep time to appear in /proc.
    const owned = ownedDescendants(Number(text));
    if (owned.length < 2) throw new LabError('cancellation_child_not_observed');
    console.log(JSON.stringify({ type: 'cancellation_command_started', ownedProcesses: owned.length }));
    const interruptDeadline = setTimeout(() => session.close(), 5_000);
    try { await session.interrupt(); } finally { clearTimeout(interruptDeadline); }
    await waitUntil(() => owned.every(old => {
      const current = processIdentity(old.pid);
      return !current || current.start !== old.start || current.state === 'Z';
    }), 5_000);
    session.close();
    // The container deliberately stays alive beyond the sleep. Container teardown
    // must not hide a child that would have written after cancellation.
    await delay(16_000);
    if (existsSync('/lab-workspace/cancel-sentinel')) throw new LabError('delayed_writer_survived_cancellation');
    console.log(JSON.stringify({ type: 'cancellation_passed', ownedProcesses: owned.length,
      delayedWrite: false, observedAfterMs: 16000 }));
  } finally {
    done = true;
    release?.();
    session.close();
    // consume owns the stream; never wait forever for a failed producer.
    await Promise.race([consume, delay(1_000)]);
  }
}
