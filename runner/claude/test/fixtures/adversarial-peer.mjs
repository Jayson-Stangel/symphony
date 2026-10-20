// Malformed/untrusted helper peer for parent transport fault tests; no SDK/auth.
import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
const mode = readFileSync('peer-mode', 'utf8').trim();
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
send({ v: mode === 'bad-version' ? 9 : 1, type: 'hello', protocol: mode === 'bad-protocol' ? 9 : 1,
  sdk: mode === 'bad-sdk' ? 'x'.repeat(129) : mode === 'future-runtime' ? '0.999.0' : 'fixture-sdk',
  cli: mode === 'future-runtime' ? '9.999.0' : 'fixture-cli', policy: mode === 'bad-policy' ? 9 : 1 });
let active, activePrompt;
for await (const line of createInterface({ input: process.stdin })) {
  const c = JSON.parse(line);
  const base = { v: 1, session_id: c.session_id, request_id: c.request_id };
  if (c.type === 'start') {
    if (mode === 'error-start') { send({ v: 1, type: 'error', code: 'unsupported_tool_schema' }); continue; }
    if (mode === 'auth-start') { send({ ready: false, code: 'auth_required' }); continue; }
    send({ ...base, type: 'ready', identity: { profile: mode === 'bad-profile' ? 12 : 'a'.repeat(64) },
      owned_native: mode === 'bad-owned' ? 'invalid' : mode === 'bad-pid' ? [{ pid: 1, start: '0' }] : mode === 'valid-owned' ? [{ pid: 999999, start: '0' }] : [] });
  } else if (c.type === 'turn') {
    active = base; activePrompt = c.prompt;
    const type = c.prompt;
    if (type === 'exit') process.exit(1);
    if (type === 'oversized') { process.stdout.write('x'.repeat(1_100_000) + '\n'); continue; }
    if (type === 'array') { send([]); continue; }
    if (type === 'wrong-session') { send({ ...base, session_id: 'wrong', type: 'progress', activity: 'agent' }); continue; }
    if (type === 'usage-invalid') { send({ ...base, type: 'usage', totals: { input_tokens: -1 } }); continue; }
    if (type === 'usage-array') { send({ ...base, type: 'usage', totals: [] }); continue; }
    if (type === 'unexpected') { send({ ...base, type: 'unknown' }); continue; }
    if (type === 'ack-without-call') { send({ ...base, type: 'tool_result_accepted', tool_id: 'unknown' }); continue; }
    if (type === 'unbound-tool' || type.startsWith('tool-')) {
      send({ ...base, type: 'tool_call', tool_id: 'one', name: type === 'unbound-tool' ? 'not_bound' : 'probe_tool', arguments: { query: type, method: type === 'tool-read-error' ? 'GET' : 'PATCH' } });
      continue;
    }
    if (type === 'progress-fallback') {
      for (const details of [{}, { status: 'unknown', payload: 'SECRET', productive: true },
        { status: 'tool_running', tool: 'SECRET', productive: true }]) {
        send({ ...base, type: 'progress', activity: 'agent', ...details });
      }
      send({ ...base, type: 'turn_result', outcome: 'completed' });
      continue;
    }
    send({ ...base, type: 'turn_result', outcome: type });
  } else if (c.type === 'tool_result') {
    if (['tool-definite-error', 'tool-read-error', 'tool-linear-definite-error', 'tool-linear-server-error', 'tool-linear-partial-error', 'tool-linear-read-error', 'tool-linear-read-timeout', 'tool-linear-local-error'].includes(activePrompt)) send({ ...active, type: 'tool_result_accepted', tool_id: 'one' });
    send({ ...active, type: 'turn_result', outcome: 'completed' }); // Deliberately missing tool ACK.
  } else if (c.type === 'stop') {
    if (mode === 'exit-on-stop') process.exit(1);
    if (mode === 'invalid-stop') { process.stdout.write('bad-json\n'); continue; }
    send({ ...base, type: 'progress', activity: 'agent' });
    send({ ...base, type: 'stopped' });
    process.exit(0);
  }
}
