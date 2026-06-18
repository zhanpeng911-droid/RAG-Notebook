// @ts-check
import { test, expect } from '@playwright/test';

const PUBLIC_ROUTES = [
  { path: '/login', titleContains: '登录' },
  { path: '/register', titleContains: '注册' },
];

test.describe('路由 smoke test', () => {
  for (const { path, titleContains } of PUBLIC_ROUTES) {
    test(`${path} 不白屏、不抛前端 runtime error`, async ({ page }) => {
      const errors = [];
      page.on('pageerror', (err) => errors.push(err.message));

      await page.goto(path);

      // 页面有内容（不白屏）
      const bodyText = await page.textContent('body');
      expect(bodyText?.length).toBeGreaterThan(0);

      // 不应有未捕获的 JS 运行时错误
      // 过滤 Vite HMR 和网络错误噪音
      const realErrors = errors.filter(
        (e) => !e.includes('net::') && !e.includes('Failed to fetch') && !e.includes('HMR')
      );
      expect(realErrors).toEqual([]);
    });
  }
});
