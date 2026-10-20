import { randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { realpathSync } from 'node:fs';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { parseCommand, maxFrameBytes, classifyEvent, summarizeResult, usageTotals, assistantInputTokens } from './protocol.mjs';
import { readiness, RuntimeError, runtime } from './runtime.mjs';
import { trackerServer } from './tools.mjs';
import { processIdentity, signalOwnedGroup, ownedGroupAlive } from './processes.mjs';
import { skillPlugin } from './skills.mjs';
import { progressDetails } from './progress.mjs';

const ranks = { transient_error: 1, protocol_error: 2, permission_blocked: 3, quota_exhausted: 4, auth_required: 5, policy_blocked: 6 };

export class Helper {
  constructor({ identity, env, binary, send, makeQuery = query, terminate = () => {}, graceMs = 2000, killMs = 250 }) {
    Object.assign(this, { identity, env, binary, send, makeQuery, terminate, graceMs, killMs });
    this.state = 'new';
    this.pendingInput = [];
    this.tools = new Map();
    this.terminals = new Set();
    this.usedRequests = new Set();
    this.children = new Set();
    this.usage = { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  }

  async command(command) {
    if (this.state === 'stopped') throw new RuntimeError('helper_stopped');
    if (command.type === 'start') return this.start(command);
    if (command.session_id !== this.sessionId) throw new RuntimeError('session_mismatch');
    if (command.type === 'tool_result') {
      const pending = this.tools.get(command.tool_id);
      if (!pending || pending.request !== command.request_id) throw new RuntimeError('stale_tool_result');
      this.tools.delete(command.tool_id);
      clearTimeout(pending.timer);
      pending.resolve(command.result);
      this.emit('tool_result_accepted', command.request_id, { tool_id: command.tool_id });
      return;
    }
    if (command.type === 'stop') return this.stop(command.request_id);
    if (command.type === 'interrupt') {
      if (this.active) this.active.cancelled = true;
      this.rejectTools();
      let timeout;
      try {
        await Promise.race([this.session?.interrupt(), new Promise(resolve => { timeout = setTimeout(resolve, 2_000); })]);
      } finally { clearTimeout(timeout); }
      if (this.active) this.finish({ outcome: 'cancelled' });
      await this.stop(command.request_id);
      return;
    }
    if (this.state !== 'ready' || this.active || this.usedRequests.has(command.request_id)) throw new RuntimeError('invalid_turn_state');
    if (this.usedRequests.size >= 128) throw new RuntimeError('attempt_turn_limit');
    this.usedRequests.add(command.request_id);
    this.active = { id: command.request_id, uuid: randomUUID(), cause: null, cancelled: false, messages: new Set(), progressEvents: new Set() };
    this.pendingInput.push({ type: 'user', session_id: '', uuid: this.active.uuid, parent_tool_use_id: null,
      message: { role: 'user', content: command.prompt } });
    this.wake?.();
    this.turnDeadline = setTimeout(() => {
      if (this.active) this.finish({ outcome: 'protocol_error' });
      void this.stop(command.request_id);
    }, this.config.turn_timeout_ms);
  }

  emit(type, request, data = {}) {
    this.send({ v: 1, type, session_id: this.sessionId, request_id: request, ...data });
  }

  async start(command) {
    if (this.state !== 'new') throw new RuntimeError('session_already_started');
    if (realpathSync(command.workspace) !== process.cwd()) throw new RuntimeError('workspace_identity_mismatch');
    this.sessionId = command.session_id;
    this.config = command.config;
    this.state = 'starting';
    this.skillPlugin = skillPlugin(command.workspace, command.skills ?? {});
    this.env.SYMPHONY_SKILLS_ROOT = this.skillPlugin.root;
    const server = trackerServer(command.tools, (name, args) => this.callTool(name, args));
    const aliases = command.tools.map(spec => `${spec.name}: mcp__symphony__${spec.name}`).join('\n');
    this.session = this.makeQuery({ prompt: this.input(), options: {
      cwd: command.workspace, env: this.env, pathToClaudeCodeExecutable: this.binary,
      settingSources: [], strictMcpConfig: true, mcpServers: { symphony: server },
      plugins: [{ type: 'local', path: this.skillPlugin.path }], skills: 'all',
      systemPrompt: { type: 'preset', preset: 'claude_code', append: `${command.instructions}\nCanonical skill aliases: symphony-commit, symphony-push, symphony-pull, symphony-linear and symphony-land are available as symphony:<name>. Follow the same procedures.\nTracker tool aliases:\n${aliases}` },
      model: command.config.model,
      ...(command.config.effort ? { effort: command.config.effort } : {}),
      maxTurns: command.config.max_agentic_turns,
      permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true,
      promptSuggestions: false, agentProgressSummaries: false, persistSession: false, stderr: () => {},
      spawnClaudeCodeProcess: options => {
        if (this.children.size) throw new RuntimeError('unexpected_native_restart');
        const child = spawn(options.command, options.args, { cwd: options.cwd, env: options.env,
          signal: options.signal, detached: true, stdio: ['pipe', 'pipe', 'pipe'] });
        child.stderr.resume();
        const owned = processIdentity(child.pid);
        if (!owned) { child.kill('SIGKILL'); throw new RuntimeError('native_ownership_unavailable'); }
        this.children.add(owned);
        return child;
      },
    } });
    this.consume = this.readEvents().catch(() => {
      if (this.active) this.finish({ outcome: 'protocol_error' });
      void this.stop(command.request_id);
    });
    let deadline;
    try {
      await Promise.race([this.session.initializationResult(), new Promise((_, reject) => {
        deadline = setTimeout(() => reject(new RuntimeError('native_initialization_timeout')), 10_000);
      })]);
    } finally { clearTimeout(deadline); }
    if (this.state !== 'starting') throw new RuntimeError('native_startup_failed');
    this.state = 'ready';
    this.emit('ready', command.request_id, { identity: this.identity,
      owned_native: [...this.children].map(({ pid, start }) => ({ pid, start })) });
  }

  async *input() {
    while (this.state !== 'stopped') {
      if (this.pendingInput.length) yield this.pendingInput.shift();
      else await new Promise(resolve => { this.wake = resolve; });
    }
  }

  async readEvents() {
    for await (const event of this.session) {
      if (this.state === 'stopped') break;
      if (event.type === 'system' && event.subtype === 'init') {
        if (this.nativeSession && this.nativeSession !== event.session_id) throw new RuntimeError('native_session_changed');
        this.nativeSession = event.session_id;
      }
      if (!this.active) continue;
      const cause = classifyEvent(event);
      if (cause && (!this.active.cause || ranks[cause.outcome] > ranks[this.active.cause.outcome])) this.active.cause = cause;
      if (event.type === 'result') {
        if (typeof event.uuid !== 'string' || !this.nativeSession || event.session_id !== this.nativeSession) {
          throw new RuntimeError('uncorrelated_native_result');
        }
        if (this.terminals.has(event.uuid)) continue;
        this.terminals.add(event.uuid);
        if (event.user_message_uuid !== this.active.uuid) throw new RuntimeError('stale_native_result');
        const totals = usageTotals(event.modelUsage);
        // modelUsage already includes earlier turns and auxiliary calls. Never sum results.
        if (totals) this.reportUsage(totals);
        this.finish(summarizeResult(event, this.active.cause, this.active.cancelled));
      } else if (['assistant', 'tool_progress', 'system'].includes(event.type)) {
        if (event.type === 'assistant' && !event.error && event.session_id === this.nativeSession &&
            (!event.user_message_uuid || event.user_message_uuid === this.active.uuid)) {
          const id = event.message?.id;
          const input = assistantInputTokens(event.message?.usage);
          if (typeof id === 'string' && id.length && input !== null && !this.active.messages.has(id)) {
            this.active.messages.add(id);
            const inputTokens = this.usage.input_tokens + input;
            const totalTokens = inputTokens + this.usage.output_tokens;
            if (Number.isSafeInteger(totalTokens)) {
              this.reportUsage({ input_tokens: inputTokens, output_tokens: this.usage.output_tokens, total_tokens: totalTokens });
            }
          }
        }
        const details = progressDetails(event);
        if (!details) continue;
        const seen = this.active.progressEvents;
        // Tool heartbeats can carry fresh UUIDs forever. Only a new assistant
        // block establishes advancement; cap retained IDs for the bounded turn.
        const productive = ['response', 'tool_started'].includes(details.status) && !event.error &&
          event.session_id === this.nativeSession &&
          (!event.user_message_uuid || event.user_message_uuid === this.active.uuid) &&
          typeof event.uuid === 'string' && !seen.has(event.uuid) && seen.size < 4096;
        if (productive) seen.add(event.uuid);
        this.emit('progress', this.active.id, { ...details, productive });
      }
    }
    if (this.state !== 'stopped') {
      if (this.active) this.finish({ outcome: 'protocol_error' });
      await this.stop('native_eof');
    }
  }

  reportUsage(totals) {
    // Crash results may be zeroed. Keep already observed usage and session totals monotonic.
    const input = Math.max(this.usage.input_tokens, totals.input_tokens);
    const output = Math.max(this.usage.output_tokens, totals.output_tokens);
    if (!Number.isSafeInteger(input + output)) return;
    this.usage = { input_tokens: input, output_tokens: output, total_tokens: input + output };
    this.emit('usage', this.active.id, { totals: { ...this.usage } });
  }

  finish(outcome) {
    if (!this.active) return;
    clearTimeout(this.turnDeadline);
    const request = this.active.id;
    this.active = null;
    this.rejectTools();
    this.emit('turn_result', request, outcome);
  }

  callTool(name, args) {
    if (!this.active || this.state !== 'ready') return Promise.reject(new RuntimeError('unbound_tool_call'));
    if (this.tools.size >= 16 || Buffer.byteLength(JSON.stringify(args)) > 524288) {
      this.finish({ outcome: 'protocol_error' });
      void this.stop('tool_limit');
      return Promise.reject(new RuntimeError('tool_limit'));
    }
    const toolId = randomUUID();
    return new Promise((resolve, reject) => {
      const pending = { request: this.active.id, resolve, reject, timer: setTimeout(() => {
        this.tools.delete(toolId);
        reject(new RuntimeError('tool_result_uncertain'));
        this.finish({ outcome: 'tool_outcome_uncertain' });
        void this.stop('tool_timeout');
      }, 60_000) };
      this.tools.set(toolId, pending);
      this.emit('tool_call', this.active.id, { tool_id: toolId, name, arguments: args });
    });
  }

  rejectTools() {
    for (const pending of this.tools.values()) {
      clearTimeout(pending.timer);
      pending.reject(new RuntimeError('tool_cancelled'));
    }
    this.tools.clear();
  }

  async stop(request = 'parent_eof') {
    if (this.state === 'stopped') return;
    this.state = 'stopped';
    clearTimeout(this.turnDeadline);
    this.rejectTools();
    this.wake?.();
    this.session?.close();
    // Only groups created by this helper's detached native spawner are targeted.
    // Signaling the owned group also catches children after the CLI exits.
    await new Promise(resolve => setTimeout(resolve, this.graceMs));
    for (const child of this.children) signalOwnedGroup(child, 'SIGTERM');
    await new Promise(resolve => setTimeout(resolve, this.killMs));
    for (const child of this.children) signalOwnedGroup(child, 'SIGKILL');
    await new Promise(resolve => setTimeout(resolve, 100));
    if ([...this.children].some(ownedGroupAlive)) { this.terminate(); return; }
    this.skillPlugin?.cleanup();
    try { this.emit('stopped', request); } finally { this.terminate(); }
  }
}

export function serve(context, options = {}) {
  let output = Promise.resolve();
  let queuedBytes = 0;
  const send = message => {
    const line = JSON.stringify(message) + '\n';
    if (Buffer.byteLength(line) > maxFrameBytes) throw new RuntimeError('output_frame_too_large');
    const bytes = Buffer.byteLength(line);
    if (queuedBytes + bytes > maxFrameBytes * 4) throw new RuntimeError('output_backpressure_limit');
    queuedBytes += bytes;
    output = output.then(async () => {
      if (!process.stdout.write(line)) await once(process.stdout, 'drain');
      queuedBytes -= bytes;
    });
    output.catch(() => { void helper.stop('output_closed'); });
  };
  const helper = new Helper({ ...context, ...options, send, terminate: () => {
    setTimeout(() => process.exit(1), 1_000).unref();
    void output.finally(() => process.exit(0));
  } });
  let buffer = Buffer.alloc(0);
  let closing = false;
  const fail = error => {
    if (closing) return;
    closing = true;
    process.stdin.pause();
    buffer = Buffer.alloc(0);
    const code = error instanceof RuntimeError && ['unsupported_tool_schema', 'duplicate_tool_name', 'native_ownership_unavailable', 'native_initialization_timeout', 'unexpected_native_restart'].includes(error.message) ? error.message : 'protocol_error';
    try { send({ v: 1, type: 'error', code }); } catch { /* Broken or full output. */ }
    void helper.stop('protocol_error');
  };
  process.on('unhandledRejection', fail);
  process.on('uncaughtException', fail);
  process.stdin.on('data', chunk => {
    if (closing) return;
    buffer = Buffer.concat([buffer, chunk]);
    for (let index; (index = buffer.indexOf(10)) !== -1;) {
      const line = buffer.subarray(0, index);
      buffer = buffer.subarray(index + 1);
      try {
        const command = parseCommand(new TextDecoder('utf-8', { fatal: true }).decode(line));
        // Each state transition is synchronous before any awaited native control;
        // keep reading tool responses while an interrupt is waiting for the SDK.
        void helper.command(command).catch(fail);
      } catch { fail(); }
      if (closing) return;
    }
    if (buffer.length > maxFrameBytes) fail();
  });
  process.stdin.once('end', () => { void helper.stop('parent_eof'); });
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => { void helper.stop('signal'); });
  process.stdout.once('error', () => { void helper.stop('output_closed'); });
  process.stdout.write(JSON.stringify({ v: 1, type: 'hello', ...runtime }) + '\n');
  return helper;
}

async function main() {
  const command = process.argv[2] ?? 'serve';
  if (!['serve', 'check', 'profile'].includes(command) || process.argv.length > 3) throw new RuntimeError('unknown_command');
  const context = readiness({ workspace: command === 'serve' ? process.cwd() : undefined, requireAttestation: command !== 'profile' });
  if (command !== 'serve') {
    console.log(JSON.stringify({ ready: true, ...context.identity, model_requests: 0 }));
    return;
  }
  serve(context);
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(new URL(import.meta.url).pathname)) {
  main().catch(error => {
    console.log(JSON.stringify({ ready: false, code: error instanceof RuntimeError ? error.message : 'runtime_error', model_requests: 0 }));
    process.exitCode = 1;
  });
}
