// Only fixed categories cross the helper boundary. Never forward provider prose,
// custom tool names, arguments, prompts, thinking blocks, or raw events.
const toolKinds = { Bash: 'command', Read: 'read', Write: 'edit', Edit: 'edit',
  MultiEdit: 'edit', Glob: 'search', Grep: 'search', WebSearch: 'search', WebFetch: 'read' };

export function progressDetails(event) {
  if (event.type === 'assistant') {
    if (event.error) return { activity: 'agent', status: 'waiting' };
    const content = event.message?.content;
    if (!Array.isArray(content) || !content.some(block => ['text', 'thinking', 'tool_use'].includes(block?.type))) {
      return { activity: 'agent', status: 'waiting' };
    }
    const tool = content.find(block => block?.type === 'tool_use');
    return { activity: tool ? 'tool' : 'agent', status: tool ? 'tool_started' : 'response',
      ...(tool ? { tool: Object.hasOwn(toolKinds, tool.name) ? toolKinds[tool.name] : 'tool' } : {}) };
  }
  if (event.type === 'tool_progress') {
    return { activity: 'tool', status: event.subagent_retry ? 'retrying' : 'tool_running',
      tool: Object.hasOwn(toolKinds, event.tool_name) ? toolKinds[event.tool_name] : 'tool' };
  }
  if (event.type === 'system') {
    const status = event.subtype === 'api_retry' ? 'retrying'
      : event.subtype === 'status' && event.status === 'compacting' ? 'compacting' : 'waiting';
    return { activity: 'agent', status };
  }
  return null;
}
