const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium } = require('@playwright/test');

async function main() {
  assert.equal(process.versions.node.split('.')[0], '24', 'browser tooling requires Node 24');
  const server = http.createServer((_request, response) => {
    response.setHeader('Content-Type', 'text/html');
    response.end('<title>Browser smoke test</title><button onclick="this.textContent=\'Passed\'">Run</button>');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    assert.equal(await page.title(), 'Browser smoke test');
    await page.getByRole('button', { name: 'Run', exact: true }).click();
    assert.equal(await page.getByRole('button').textContent(), 'Passed');
    assert.ok((await page.screenshot()).length > 0);
    console.log(`Chromium ${browser.version()}: navigation, JavaScript, interaction, and screenshot passed`);
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
