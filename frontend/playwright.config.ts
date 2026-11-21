import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    channel: 'chrome',
    baseURL: 'http://127.0.0.1:5173',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5173',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
})
