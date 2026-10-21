// Codex protocol peer: exercises native tool delivery without auth or inference.
import { appendFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
const [mode, query, trace] = process.argv.slice(2);
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
let calls = 0;
const call = () => send({ id: ++calls + 100, method: 'item/tool/call', params: { tool: 'linear_graphql', arguments: { query: calls === 1 ? query : 'query { issue(id: "one") { id title } }' } } });
for await (const line of createInterface({ input: process.stdin })) {
  const message = JSON.parse(line);
  appendFileSync(trace, line + '\n');
  if (message.method === 'initialize') send({ id: 1, result: {} });
  if (message.method === 'thread/start') send({ id: 2, result: { thread: { id: 'linear-thread' } } });
  if (message.method === 'turn/start') {
    if (mode === 'startup') call();
    else { send({ id: 3, result: { turn: { id: 'linear-turn' } } }); call(); }
  }
  if (message.id >= 101) {
    if (mode === 'startup') send({ id: 3, result: { turn: { id: 'linear-turn' } } });
    if (calls === 1) call();
    else send({ method: 'turn/completed' });
  }
}
