import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  globalSetup: './e2e/global-setup.js',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8899',
    trace: 'retain-on-failure',
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: 'PYTHONPATH=../src ../.venv/bin/python -m goodteam_os.cli serve --workspace /private/tmp/goodteam-project-load-e2e/workspace --host 127.0.0.1 --port 8899',
    url: 'http://127.0.0.1:8899/projects',
    reuseExistingServer: false,
    timeout: 120000,
  },
});
