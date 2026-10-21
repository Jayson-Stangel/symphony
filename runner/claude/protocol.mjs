import { z } from 'zod';

export const maxFrameBytes = 1_048_576;
const id = z.string().min(1).max(128).regex(/^[A-Za-z0-9:_-]+$/);
const common = { v: z.literal(1), session_id: id, request_id: id };
export const toolSpec = z.object({ name: z.string().regex(/^[A-Za-z][A-Za-z0-9_]{0,63}$/),
  description: z.string().max(8192), inputSchema: z.record(z.string(), z.unknown()) }).strict();

// Native model support is runtime-owned; preserve identifiers without a second catalog.
const settings = z.object({ model: z.string().max(256).refine(value => value.trim().length > 0), effort: z.enum(['low', 'medium', 'high', 'xhigh', 'max']).nullish(),
  max_agentic_turns: z.number().int().positive().max(100),
  turn_timeout_ms: z.number().int().positive().max(3_600_000) }).strict();

export const commandSchema = z.discriminatedUnion('type', [
  z.object({ ...common, type: z.literal('start'), workspace: z.string().min(1).max(4096),
    skills: z.record(z.string().regex(/^[a-z][a-z0-9-]{0,127}$/), z.string().min(1).max(4096))
      .refine(sources => Object.keys(sources).length <= 5).default({}),
    config: settings, tools: z.array(toolSpec).max(16), instructions: z.string().max(131072) }).strict(),
  z.object({ ...common, type: z.literal('turn'), prompt: z.string().min(1).max(524288) }).strict(),
  z.object({ ...common, type: z.literal('tool_result'), tool_id: id,
    result: z.object({ success: z.boolean(), output: z.string().max(524288) }).strict() }).strict(),
  z.object({ ...common, type: z.literal('interrupt') }).strict(),
  z.object({ ...common, type: z.literal('stop') }).strict(),
]);

export function parseCommand(line) {
  if (Buffer.byteLength(line) > maxFrameBytes) throw new Error('frame_too_large');
  return commandSchema.parse(JSON.parse(line));
}

export function classifyEvent(event) {
  if (event.type === 'rate_limit_event') {
    const info = event.rate_limit_info;
    if (info?.status === 'rejected') {
      return { outcome: 'quota_exhausted', resets_at: Number.isSafeInteger(info.resetsAt) && info.resetsAt > 0 ? info.resetsAt : null };
    }
  }
  if (event.type === 'assistant' && event.error) {
    if (['authentication_failed', 'oauth_org_not_allowed', 'verification_required'].includes(event.error)) return { outcome: 'auth_required' };
    if (['account_on_hold', 'billing_error', 'cloud_credential_error'].includes(event.error)) return { outcome: 'policy_blocked' };
    if (['overloaded', 'server_error', 'rate_limit'].includes(event.error)) return { outcome: 'transient_error' };
    return { outcome: 'protocol_error' };
  }
  return null;
}

export function summarizeResult(event, cause, cancelled = false) {
  if (cancelled) return { outcome: 'cancelled' };
  if (cause) return cause;
  if (event.subtype === 'success' && event.is_error === false) return { outcome: 'completed' };
  if (event.subtype === 'error_max_turns') return { outcome: 'agent_turn_limit' };
  if (event.permission_denials?.length) return { outcome: 'permission_blocked' };
  return { outcome: 'protocol_error' }; // Never infer billing/quota from model prose.
}

export function usageTotals(modelUsage) {
  if (!modelUsage || typeof modelUsage !== 'object' || Object.keys(modelUsage).length === 0) return null;
  const totals = { input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  for (const usage of Object.values(modelUsage ?? {})) {
    const fields = ['inputTokens', 'cacheReadInputTokens', 'cacheCreationInputTokens', 'outputTokens'];
    if (!usage || !fields.every(key => Number.isSafeInteger(usage[key]) && usage[key] >= 0)) return null;
    totals.input_tokens += usage.inputTokens + usage.cacheReadInputTokens + usage.cacheCreationInputTokens;
    totals.output_tokens += usage.outputTokens;
  }
  totals.total_tokens = totals.input_tokens + totals.output_tokens;
  return Object.values(totals).every(Number.isSafeInteger) ? totals : null;
}

// Assistant output_tokens is a placeholder; only result.modelUsage confirms output.
export function assistantInputTokens(usage) {
  if (!usage || typeof usage !== 'object') return null;
  const values = [usage.input_tokens, usage.cache_read_input_tokens ?? 0, usage.cache_creation_input_tokens ?? 0];
  if (!values.every(value => Number.isSafeInteger(value) && value >= 0)) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  return Number.isSafeInteger(total) ? total : null;
}
