import { defineConfig, devices } from '@playwright/test';

const isFullStack = process.env.E2E_FULL_STACK === 'true';

export default defineConfig({
  testDir: isFullStack ? './tests/e2e-full' : './tests/e2e',
  timeout: 30000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:3076',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 7'] },
    },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:3076',
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
