import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import { RuntimeError } from './runtime.mjs';

const supported = new Set(['type', 'properties', 'required', 'additionalProperties', 'description', 'title', 'enum', 'items']);

export function validateSchema(schema, depth = 0) {
  if (!schema || typeof schema !== 'object' || Array.isArray(schema) || depth > 12) throw new RuntimeError('unsupported_tool_schema');
  if (Object.keys(schema).some(key => !supported.has(key))) throw new RuntimeError('unsupported_tool_schema');
  // Unconstrained JSON bodies are part of the existing REST tracker contract.
  // An absent type is valid only for an unconstrained/annotation-only schema.
  if (schema.type === undefined && Object.keys(schema).every(key => ['description', 'title'].includes(key))) return;
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (!types.length || types.some(type => !['string', 'number', 'integer', 'boolean', 'null', 'object', 'array'].includes(type))) {
    throw new RuntimeError('unsupported_tool_schema');
  }
  for (const value of Object.values(schema.properties ?? {})) validateSchema(value, depth + 1);
  if (schema.items) validateSchema(schema.items, depth + 1);
  if (schema.additionalProperties && typeof schema.additionalProperties === 'object') validateSchema(schema.additionalProperties, depth + 1);
}

export function toolShape(schema) {
  validateSchema(schema);
  if (schema.type !== 'object' || schema.additionalProperties !== false || !schema.properties) throw new RuntimeError('unsupported_tool_schema');
  const converted = z.fromJSONSchema(schema);
  if (!(converted instanceof z.ZodObject)) throw new RuntimeError('unsupported_tool_schema');
  // The SDK's raw-shape API cannot express arbitrary root refinements. Restrict
  // schemas above to the closed object shapes existing tracker tools actually use.
  return { shape: converted.shape, validate: value => converted.strict().parse(value) };
}

export function trackerServer(specs, execute) {
  const names = new Set();
  const tools = specs.map(spec => {
    if (names.has(spec.name)) throw new RuntimeError('duplicate_tool_name');
    names.add(spec.name);
    const schema = toolShape(spec.inputSchema);
    return tool(spec.name, spec.description, schema.shape, async args => {
      const result = await execute(spec.name, schema.validate(args));
      return { isError: !result.success, content: [{ type: 'text', text: result.output }] };
    });
  });
  return createSdkMcpServer({ name: 'symphony', version: '1.0.0', tools });
}
