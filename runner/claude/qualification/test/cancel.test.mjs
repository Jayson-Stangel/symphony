import test from 'node:test';
import assert from 'node:assert/strict';
import { allowCancellationTool, cancellationCommand } from '../cancel.mjs';

test('cancellation permission permits only one exact foreground command', () => {
  assert.deepEqual(allowCancellationTool('Bash', { command: cancellationCommand }, false), {
    behavior: 'allow', updatedInput: { command: cancellationCommand, timeout: 30000, run_in_background: false },
  });
  for (const [name, input, used] of [
    ['Bash', { command: `${cancellationCommand}; echo extra` }, false],
    ['Bash', { command: cancellationCommand, run_in_background: true }, false],
    ['Bash', { command: cancellationCommand }, true],
    ['Write', { command: cancellationCommand }, false],
  ]) assert.equal(allowCancellationTool(name, input, used).behavior, 'deny');
});
