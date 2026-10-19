import { spawn, spawnSync } from 'node:child_process';
import { approvedEnvironment, nativeExecutable, readiness, runtime, RuntimeError, validateContainer, checkSettings } from './runtime.mjs';

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (!['login', 'check', 'logout', 'attest'].includes(command)) throw new RuntimeError('unknown_auth_command');
  if (command === 'attest') {
    if (args.length !== 1 || args[0] !== '--overages-disabled') throw new RuntimeError('explicit_overage_attestation_required');
    const { identity } = readiness({ requireAttestation: false });
    console.log(JSON.stringify({ version: 1, policy: runtime.policy, profile: identity.profile,
      overages_disabled: true, attested_at: new Date().toISOString() }));
    return;
  }
  if (args.length) throw new RuntimeError('unknown_auth_argument');
  if (command === 'check') {
    const { identity } = readiness();
    console.log(JSON.stringify({ ready: true, ...identity, model_requests: 0 }));
    return;
  }
  validateContainer();
  checkSettings();
  const env = approvedEnvironment(process.env);
  const binary = nativeExecutable();
  const version = spawnSync(binary, ['--version'], { env, encoding: 'utf8', timeout: 15_000, maxBuffer: 65536 });
  if (version.status !== 0 || version.stdout?.trim() !== `${runtime.cli} (Claude Code)`) throw new RuntimeError('runtime_version_mismatch');
  const child = spawn(binary, ['auth', command], { env, stdio: 'inherit' });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => child.kill(signal));
  process.exitCode = await new Promise((resolve, reject) => {
    child.once('error', () => reject(new RuntimeError('native_auth_unavailable')));
    child.once('exit', code => resolve(code ?? 1));
  });
}

main().catch(error => {
  console.error(JSON.stringify({ ready: false, code: error instanceof RuntimeError ? error.message : 'native_auth_failed', model_requests: 0 }));
  process.exitCode = 1;
});
