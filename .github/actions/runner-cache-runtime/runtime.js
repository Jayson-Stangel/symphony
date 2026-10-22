'use strict';
const fs = require('node:fs');
const env = process.env;
if (env.GITHUB_ACTIONS !== 'true' || !env.GITHUB_ENV || !env.ACTIONS_RUNTIME_TOKEN || !env.ACTIONS_RESULTS_URL) {
  throw new Error('Actions cache runtime is unavailable; refusing an unconfigured candidate cache');
}
const names = ['ACTIONS_RUNTIME_TOKEN', 'ACTIONS_RESULTS_URL', 'ACTIONS_CACHE_URL'];
for (const name of names) {
  const value = env[name];
  if (!value) continue;
  if (/[\r\n]/.test(value)) throw new Error('Invalid cache runtime environment');
  if (name === 'ACTIONS_RUNTIME_TOKEN') {
    process.stdout.write('::add-mask::' + value.replace(/%/g, '%25') + '\n');
  }
  fs.appendFileSync(env.GITHUB_ENV, name + '=' + value + '\n');
}
fs.appendFileSync(env.GITHUB_ENV, 'ACTIONS_CACHE_SERVICE_V2=true\nSYMPHONY_GHA_RUNNER_CACHE=true\n');
