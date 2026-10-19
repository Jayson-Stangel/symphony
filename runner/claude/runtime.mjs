import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { existsSync, readFileSync, realpathSync, statSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
export const runtime = Object.freeze({ sdk: '0.3.303', cli: '2.1.303', policy: 1, protocol: 1 });
export const home = '/home/claude';
export const nativeConfig = `${home}/.claude`;
export const attestationPath = '/etc/symphony/claude/attestation.json';

export class RuntimeError extends Error {
  constructor(code) { super(code); this.name = 'RuntimeError'; }
}

export function nativeExecutable() {
  if (process.platform !== 'linux' || !['arm64', 'x64'].includes(process.arch)) throw new RuntimeError('unsupported_platform');
  const pkg = `@anthropic-ai/claude-code-linux-${process.arch}`;
  return realpathSync(join(dirname(require.resolve(`${pkg}/package.json`)), 'claude'));
}

export function approvedEnvironment(source) {
  const allowed = new Set(['CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_FAST_MODE']);
  for (const [key, value] of Object.entries(source)) {
    if (value && /^(ANTHROPIC_|CLAUDE_|AWS_|AZURE_|GOOGLE_|VERTEX_|BEDROCK_)/i.test(key) && !allowed.has(key)) {
      throw new RuntimeError('conflicting_inference_environment');
    }
  }
  if (source.HOME !== home || source.CLAUDE_CONFIG_DIR !== nativeConfig || source.CLAUDE_CODE_DISABLE_FAST_MODE !== '1') {
    throw new RuntimeError('native_identity_mismatch');
  }
  const env = { HOME: home, CLAUDE_CONFIG_DIR: nativeConfig, CLAUDE_CODE_DISABLE_FAST_MODE: '1',
    DISABLE_AUTOUPDATER: '1', PATH: '/usr/local/bin:/usr/bin:/bin', LANG: 'C.UTF-8', TERM: 'xterm-256color',
    SYMPHONY_SKILLS_ROOT: '/opt/symphony/claude/plugin/skills' };
  // Only the explicitly selected repository credential is available to Git/gh.
  // Ambient tracker credentials are not copied into the coding environment.
  if (source.SYMPHONY_GIT_TOKEN) env.GH_TOKEN = source.SYMPHONY_GIT_TOKEN;
  return env;
}

export function profileFromStatus(status) {
  if (status?.loggedIn === false) throw new RuntimeError('auth_required');
  if (status?.loggedIn !== true || status.authMethod !== 'claude.ai'
    || !['pro', 'max'].includes(status.subscriptionType) || typeof status.email !== 'string' || !status.email) {
    throw new RuntimeError('unqualified_auth');
  }
  return createHash('sha256').update(JSON.stringify({ email: status.email, orgId: status.orgId ?? null,
    authMethod: status.authMethod, subscriptionType: status.subscriptionType })).digest('hex');
}

export function nativeIdentity(binary, env) {
  const version = spawnSync(binary, ['--version'], { env, encoding: 'utf8', timeout: 15_000, maxBuffer: 64 * 1024 });
  if (version.status !== 0 || version.stdout?.trim() !== `${runtime.cli} (Claude Code)`) throw new RuntimeError('runtime_version_mismatch');
  const sdkVersion = JSON.parse(readFileSync(join(dirname(require.resolve('@anthropic-ai/claude-agent-sdk')), 'package.json'), 'utf8')).version;
  if (sdkVersion !== runtime.sdk) throw new RuntimeError('sdk_version_mismatch');
  const auth = spawnSync(binary, ['auth', 'status', '--json'], { env, encoding: 'utf8', timeout: 15_000, maxBuffer: 64 * 1024 });
  let status;
  try { status = JSON.parse(auth.stdout); } catch { throw new RuntimeError('unknown_auth_status'); }
  const profile = profileFromStatus(status);
  if (auth.status !== 0) throw new RuntimeError('auth_required');
  return { profile, category: 'subscription', sdk: runtime.sdk, cli: runtime.cli, policy: runtime.policy };
}

export function checkSettings(workspace) {
  // Fail rather than removing organizational policy or silently loading native
  // settings that the qualified subscription-only configuration does not support.
  const paths = [
    `${nativeConfig}/settings.json`, `${nativeConfig}/settings.local.json`,
    `${nativeConfig}/managed-settings.json`, `${nativeConfig}/remote-settings.json`,
    `${nativeConfig}/plugins/installed_plugins.json`, '/etc/claude-code/managed-settings.json',
    '/etc/claude-code/managed-mcp.json',
  ];
  if (workspace) paths.push(`${workspace}/.claude/settings.json`, `${workspace}/.claude/settings.local.json`, `${workspace}/.mcp.json`);
  if (paths.some(path => existsSync(path))) throw new RuntimeError('unqualified_native_settings');
}

export function validateAttestation(value, profile, now = Date.now()) {
  if (value?.version !== 1 || value.policy !== runtime.policy || value.profile !== profile
    || value.overages_disabled !== true || typeof value.attested_at !== 'string') throw new RuntimeError('overage_attestation_required');
  const time = Date.parse(value.attested_at);
  if (!Number.isFinite(time) || time > now || now - time > 30 * 24 * 60 * 60 * 1000) throw new RuntimeError('overage_attestation_expired');
}

export function validateContainer() {
  if (process.getuid?.() !== 11001) throw new RuntimeError('nonroot_profile_required');
  const status = readFileSync('/proc/self/status', 'utf8');
  if (process.env.SYMPHONY_CONTAINERIZED !== '1' || !/^CapEff:\s+0+$/m.test(status)
    || !/^NoNewPrivs:\s+1$/m.test(status) || existsSync('/var/run/docker.sock')) throw new RuntimeError('container_profile_required');
}

export function readiness({ workspace, requireAttestation = true } = {}) {
  validateContainer();
  const env = approvedEnvironment(process.env);
  checkSettings(workspace);
  const binary = nativeExecutable();
  const identity = nativeIdentity(binary, env);
  if (requireAttestation) {
    let value;
    try { value = JSON.parse(readFileSync(attestationPath, 'utf8')); } catch { throw new RuntimeError('overage_attestation_required'); }
    validateAttestation(value, identity.profile);
    identity.overages_disabled_attested = true;
    identity.attested_at = value.attested_at;
  }
  if (workspace && (realpathSync(workspace) !== process.cwd() || !statSync(workspace).isDirectory())) throw new RuntimeError('workspace_identity_mismatch');
  return { env, binary, identity };
}
