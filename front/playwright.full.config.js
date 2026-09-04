import { defineConfig, devices } from '@playwright/test';

/**
 * 真实全栈 E2E 配置 —— 需要真实后端 + 数据库 + Nginx。
 *
 * - 默认指向本地 Vite dev server（127.0.0.1:3076），自动启动。
 * - 设置 E2E_BASE_URL 指向已启动的 Docker Nginx（如 http://localhost:3000）时，
 *   不启动 Vite webServer，测试创建/读取/删除均经统一入口。
 * - 测试账号由环境变量 E2E_USERNAME / E2E_PASSWORD 提供（仓库不内置默认账号）。
 */
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:3076';
const useDockerNginx = Boolean(process.env.E2E_BASE_URL);

export default defineConfig({
  testDir: './tests/e2e-full',
  timeout: 60000,
  expect: {
    timeout: 10000,
  },
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  // 指向已启动的 Docker Nginx 时不启动 Vite dev server
  webServer: useDockerNginx
    ? undefined
    : {
        command: 'npm run dev',
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 60000,
      },
});
