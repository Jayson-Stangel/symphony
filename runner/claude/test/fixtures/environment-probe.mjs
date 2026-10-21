// Credential-free Linux proof driven by the real Elixir environment transport.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { query } from '@anthropic-ai/claude-agent-sdk';
import { approvedEnvironment, nativeExecutable } from '../../runtime.mjs';
const env = approvedEnvironment(process.env);
for (const name of ['CUSTOM_TRACKER', 'ANTHROPIC_API_KEY', 'NODE_OPTIONS', 'SYMPHONY_TOOL_ENVIRONMENT']) {
  assert.equal(Object.hasOwn(env, name), false);
}
const child = spawnSync('/bin/sh', ['-c', 'test "$AWS_ACCESS_KEY_ID" = tool-sentinel && test "$AWS_REGION" = us-east-1'], { env });
assert.equal(child.status, 0);
const auth = spawnSync(nativeExecutable(), ['--setting-sources', '', 'auth', 'status', '--json'], { env, encoding: 'utf8', timeout: 15_000 });
assert.equal(JSON.parse(auth.stdout).apiProvider, 'firstParty');
let release;
const idle = new Promise(resolve => { release = resolve; });
async function* input() { await idle; }
const session = query({ prompt: input(), options: {
  cwd: '/tmp', env, pathToClaudeCodeExecutable: nativeExecutable(), settingSources: [], strictMcpConfig: true,
  mcpServers: {}, persistSession: false, permissionMode: 'bypassPermissions', allowDangerouslySkipPermissions: true, stderr: () => {},
} });
try {
  await session.initializationResult();
  assert.equal((await session.accountInfo()).apiProvider, 'firstParty');
  console.log(JSON.stringify({ toolCredentialAvailable: true, trackerAndLoaderAbsent: true, provider: 'firstParty', modelRequests: 0 }));
} finally { session.close(); release(); }
