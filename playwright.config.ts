import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: { browserName: 'chromium', channel: process.platform === 'win32' ? 'msedge' : undefined, headless: true, viewport: { width: 1440, height: 1000 }, trace: 'retain-on-failure' },
  projects: [
    { name: 'offline', testMatch: 'product.spec.ts', use: { baseURL: 'http://127.0.0.1:8091' } },
    { name: 'pipeline', testMatch: ['generation.spec.ts', 'models.spec.ts', 'model-dialog.spec.ts'], use: { baseURL: 'http://127.0.0.1:8092' } },
  ],
  webServer: [{
    command: (process.platform === 'win32' ? '".venv\\Scripts\\python.exe"' : '.venv/bin/python') + ' scripts/run-e2e-server.py',
    url: 'http://127.0.0.1:8091/api/health',
    reuseExistingServer: false,
    timeout: 30000,
  }, {
    command: (process.platform === 'win32' ? '".venv\\Scripts\\python.exe"' : '.venv/bin/python') + ' scripts/run-e2e-server.py --provider-fixture',
    url: 'http://127.0.0.1:8092/api/health',
    reuseExistingServer: false,
    timeout: 30000,
  }],
});
