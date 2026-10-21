// Qualification policy only. Production effective-settings validation is CQ-04.
import { nativeExecutable, runtime } from '../runtime.mjs';
export const versions = Object.freeze({ sdk: runtime.sdk, cli: runtime.cli });

export class LabError extends Error {
  constructor(code) { super(code); this.name = 'LabError'; }
}

export function executable() {
  if (process.platform !== 'linux' || !['arm64', 'x64'].includes(process.arch)) {
    throw new LabError('lab_requires_supported_linux_container');
  }
  return nativeExecutable();
}

export function validateEnvironment(env) {
  // Reject unknown provider-related selectors too. Values never enter diagnostics.
  const allowed = new Set(['CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_FAST_MODE']);
  for (const [key, value] of Object.entries(env)) {
    if (!value) continue;
    if (/^(ANTHROPIC_|CLAUDE_|AWS_|AZURE_|GOOGLE_|VERTEX_|BEDROCK_)/i.test(key) && !allowed.has(key)) {
      throw new LabError(`unsupported_environment_key:${key.replace(/[^a-z0-9_]/gi, '_')}`);
    }
  }
  if (env.HOME !== '/home/claude' || env.CLAUDE_CONFIG_DIR !== '/home/claude/.claude') {
    throw new LabError('unexpected_native_config_identity');
  }
  if (env.CLAUDE_CODE_DISABLE_FAST_MODE !== '1') throw new LabError('fast_mode_must_be_disabled');
}

export function childEnvironment(env) {
  validateEnvironment(env);
  return {
    HOME: env.HOME,
    CLAUDE_CONFIG_DIR: env.CLAUDE_CONFIG_DIR,
    CLAUDE_CODE_DISABLE_FAST_MODE: '1',
    DISABLE_AUTOUPDATER: '1',
    PATH: '/usr/local/bin:/usr/bin:/bin',
    LANG: 'C.UTF-8',
    TERM: 'xterm-256color',
  };
}

export function authCategory(status) {
  if (status?.loggedIn === false) return 'missing';
  if (status?.loggedIn !== true) return 'unqualified';
  // These fields must be confirmed against native output from the pinned binary.
  if (status.authMethod === 'claude.ai' && ['pro', 'max'].includes(status.subscriptionType)) return 'subscription';
  return 'unqualified';
}

export function terminalSummary(message) {
  if (message?.type !== 'result') return null;
  return {
    type: 'result',
    success: message.subtype === 'success' && message.is_error === false,
    subtype: ['success', 'error_during_execution', 'error_max_turns', 'error_max_budget_usd',
      'error_max_structured_output_retries'].includes(message.subtype) ? message.subtype : 'unknown',
    // No free-form result/errors, raw usage payload or account identifiers in evidence.
    turns: Number.isSafeInteger(message.num_turns) ? message.num_turns : null,
  };
}
