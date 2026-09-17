import { defineConfig } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';

// 配置会在 worker 中再次加载；继承本次运行的目录和端口。
if (!process.env.GOODTEAM_E2E_ROOT) {
  process.env.GOODTEAM_E2E_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'goodteam-e2e-'));
  process.env.GOODTEAM_E2E_OWNER = crypto.randomUUID();
  fs.writeFileSync(path.join(process.env.GOODTEAM_E2E_ROOT, '.owner'), process.env.GOODTEAM_E2E_OWNER);
}
if (!process.env.GOODTEAM_E2E_PORT) {
  const socket = net.createServer();
  await new Promise((resolve, reject) => socket.once('error', reject).listen(0, '127.0.0.1', resolve));
  process.env.GOODTEAM_E2E_PORT = String(socket.address().port);
  await new Promise((resolve) => socket.close(resolve));
}
const baseURL = `http://127.0.0.1:${process.env.GOODTEAM_E2E_PORT}`;

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.js',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/results.json' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: 'node e2e/start-server.js',
    url: `${baseURL}/projects`,
    reuseExistingServer: false,
    timeout: 120000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 5000 },
  },
});
