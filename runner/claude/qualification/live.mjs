import { randomBytes } from 'node:crypto';
import { writeFileSync, readFileSync } from 'node:fs';
import { query, createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import { terminalSummary } from './policy.mjs';

// One tiny session. This is qualification code, not a production adapter.
export async function runLive(binary, env) {
  let release;
  let complete = false;
  let toolCalls = 0;
  const nonce = randomBytes(12).toString('hex');
  const pending = [];
  function push(text) {
    pending.push({ type: 'user', session_id: '', parent_tool_use_id: null,
      message: { role: 'user', content: text } });
    release?.();
  }
  async function* input() {
    while (!complete) {
      if (pending.length) yield pending.shift();
      else await new Promise(resolve => { release = resolve; });
    }
  }
  const server = createSdkMcpServer({ name: 'qualification', version: '1.0.0', tools: [
    tool('record', 'Record the remembered nonce in a harmless qualification file.',
      { nonce: z.string().regex(/^[a-f0-9]{24}$/) }, async ({ nonce: value }) => {
        if (value !== nonce) return { isError: true, content: [{ type: 'text', text: 'Nonce mismatch' }] };
        if (toolCalls !== 0) return { isError: true, content: [{ type: 'text', text: 'Already recorded' }] };
        writeFileSync('/lab-workspace/qualification.txt', 'subscription-lab-ok\n', { flag: 'wx', mode: 0o600 });
        toolCalls += 1;
        return { content: [{ type: 'text', text: 'Recorded successfully' }] };
      }),
  ] });
  const session = query({ prompt: input(), options: {
    cwd: '/lab-workspace', env, pathToClaudeCodeExecutable: binary,
    settingSources: [], strictMcpConfig: true, mcpServers: { qualification: server },
    tools: [], allowedTools: ['mcp__qualification__record'],
    permissionMode: 'dontAsk', maxTurns: 4, persistSession: false,
    model: 'sonnet', promptSuggestions: false, agentProgressSummaries: false,
    stderr: () => {}, // Raw diagnostics are deliberately never persisted.
  } });
  const deadline = setTimeout(() => { session.close(); }, 120_000);
  const hardDeadline = setTimeout(() => {
    console.error('Claude qualification stopped: deadline_exceeded');
    process.exit(124); // Container exit also tears down its own process namespace.
  }, 125_000);
  hardDeadline.unref(); // Does not keep a cleanly closed session alive; still bounds leaked child handles.
  let results = 0;
  let sessionId;
  try {
    push(`Remember this nonce for the next turn: ${nonce}. Reply only READY. Do not call any tool.`);
    for await (const event of session) {
      if (event.type === 'system' && event.subtype === 'init') {
        if (sessionId && sessionId !== event.session_id) throw new Error('session_changed');
        sessionId = event.session_id;
        console.log(JSON.stringify({ type: 'session_started' }));
      }
      const result = terminalSummary(event);
      if (!result) continue;
      console.log(JSON.stringify({ ...result, turn: results + 1 }));
      if (!result.success || !sessionId || event.session_id !== sessionId) throw new Error('turn_not_successful');
      results += 1;
      if (results === 1) {
        if (toolCalls) throw new Error('unexpected_first_turn_tool');
        push('Call mcp__qualification__record with the nonce remembered from the previous turn. Then reply only DONE.');
      } else if (results === 2) {
        if (toolCalls !== 1 || readFileSync('/lab-workspace/qualification.txt', 'utf8') !== 'subscription-lab-ok\n') {
          throw new Error('tool_proof_missing');
        }
        console.log(JSON.stringify({ type: 'live_session_passed', turns: 2, toolCalls,
          funding: 'requires_external_account_evidence', cancellation: 'not_yet_qualified' }));
        break;
      } else throw new Error('extra_terminal_result');
    }
    if (results !== 2) throw new Error('incomplete_session');
  } finally {
    clearTimeout(deadline);
    complete = true;
    release?.();
    session.close();
  }
}
