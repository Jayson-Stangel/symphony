import { spawn, spawnSync } from 'node:child_process';
import { mkdirSync, existsSync, readdirSync } from 'node:fs';
import { childEnvironment, executable, authCategory, versions, LabError } from './policy.mjs';

export function nativeStatus(binary, env) {
  const result = spawnSync(binary, ['auth', 'status', '--json'], {
    env, cwd: '/lab-workspace', encoding: 'utf8', timeout: 15_000, maxBuffer: 64 * 1024,
  });
  // Native output may contain identity/secrets. Never print or persist it wholesale.
  if (result.error) throw new LabError('native_status_unavailable');
  let status;
  try { status = JSON.parse(result.stdout); } catch { throw new LabError('unknown_native_status_format'); }
  const category = authCategory(status);
  if (result.status !== 0 && category !== 'missing') throw new LabError('native_status_failed');
  return category;
}

function assertLab() {
  if (process.getuid?.() !== 11001 || process.cwd() !== '/lab-workspace') {
    throw new LabError('lab_requires_nonroot_container_workspace');
  }
}

// No imports of model-capable SDK until explicit live command passes local gates.
async function main() {
  assertLab();
  const env = childEnvironment(process.env);
  const binary = executable();
  const [command = 'inspect', ...args] = process.argv.slice(2);
  if (!['inspect', 'login', 'status', 'live', 'cancel'].includes(command)) throw new LabError('unknown_lab_command');
  if (!['live', 'cancel'].includes(command) && args.length) throw new LabError('unexpected_arguments');
  mkdirSync(env.CLAUDE_CONFIG_DIR, { recursive: true, mode: 0o700 });

  if (command === 'inspect') {
    const version = spawnSync(binary, ['--version'], { env, encoding: 'utf8', timeout: 15_000 });
    if (version.status !== 0 || !version.stdout?.startsWith(`${versions.cli} `)) throw new LabError('native_version_mismatch');
    console.log(JSON.stringify({ type: 'lab_identity', uid: process.getuid(), sdk: versions.sdk,
      cli: versions.cli, executable: binary, home: env.HOME, config: env.CLAUDE_CONFIG_DIR,
      workspace: process.cwd(), modelRequests: 0 }));
    return;
  }
  if (command === 'login') {
    // Deliberately direct terminal I/O: only Anthropic collects the sign-in code.
    const child = spawn(binary, ['auth', 'login'], { env, stdio: 'inherit' });
    for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
    process.exitCode = await new Promise((resolve, reject) => {
      child.once('error', () => reject(new LabError('native_login_unavailable')));
      child.once('exit', code => resolve(code ?? 1));
    });
    return;
  }
  const category = nativeStatus(binary, env);
  if (command === 'status') {
    console.log(JSON.stringify({ type: 'auth_status', category, modelRequests: 0 }));
    if (category !== 'subscription') process.exitCode = 1;
    return;
  }
  if (args.length !== 1 || args[0] !== '--confirm-overages-disabled') throw new LabError('explicit_overage_confirmation_required');
  if (category !== 'subscription') throw new LabError('qualified_subscription_auth_required');
  // Fresh lab only: do not load user hooks, project settings, profiles or MCP files.
  for (const file of ['settings.json', 'settings.local.json', '.mcp.json', 'CLAUDE.md']) {
    if (existsSync(`${env.CLAUDE_CONFIG_DIR}/${file}`) || existsSync(`/lab-workspace/${file}`)
      || existsSync(`/lab-workspace/.claude/${file}`)) throw new LabError('lab_has_unqualified_settings');
  }
  if (command === 'live') {
    if (readdirSync('/lab-workspace').length !== 0) throw new LabError('live_requires_fresh_lab_workspace');
    const { runLive } = await import('./live.mjs');
    await runLive(binary, env);
  } else {
    const { runCancellation } = await import('./cancel.mjs');
    await runCancellation(binary, env);
  }
}

if (process.argv[1]?.endsWith('/lab.mjs')) main().catch(error => {
  // Detailed native exceptions may contain arbitrary output; keep boundary failures fixed.
  console.error(`Claude qualification stopped: ${error instanceof LabError ? error.message : 'runtime_failure_redacted'}`);
  process.exitCode = 1;
});
