// @ts-check
import { test, expect } from '@playwright/test';

const AUTH_SCRIPT = `
  localStorage.setItem('jwt_token', 'e2e-fake-token');
  localStorage.setItem('user-store', JSON.stringify({
    state: { token: 'e2e-fake-token', isLogin: true, userInfo: { username: 'e2e-user' } },
    version: 1
  }));
`;

test.describe('设置页主题交互', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(AUTH_SCRIPT);
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
  });

  test('设置页关键内容可见', async ({ page }) => {
    // 断言：没有被重定向到 login
    await expect(page).toHaveURL(/\/settings/);
    // 断言：设置页核心内容可见
    await expect(page.getByText('AI 模型配置')).toBeVisible();
    await expect(page.getByText('主题定制').or(page.getByText('个性化设置'))).toBeVisible();
  });

  test('通过设置页 UI 切换到深色主题', async ({ page }) => {
    // 断言：初始是浅色
    const initTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(initTheme).toBe('light');

    // 操作：点击主题定制入口
    await page.getByText('主题定制').or(page.getByText('个性化设置')).click();
    await expect(page.getByText('深色·深蓝')).toBeVisible();
    await page.getByText('深色·深蓝').click();

    // 断言：data-theme 切换为 dark
    const darkTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(darkTheme).toBe('dark');

    // 断言：localStorage 持久化
    const stored = await page.evaluate(() => localStorage.getItem('theme'));
    expect(stored).toBe('dark');

    // 断言：CSS 变量更新（不再是浅色纸感底色）
    const bgColor = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()
    );
    expect(bgColor).not.toBe('#eef2f8');
  });

  test('通过设置页 UI 切换回浅色主题', async ({ page }) => {
    // 先设为 dark
    await page.evaluate(() => localStorage.setItem('theme', 'dark'));
    await page.reload();
    await page.waitForLoadState('networkidle');

    const darkTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(darkTheme).toBe('dark');

    // 操作：切换回浅色
    await page.getByText('主题定制').or(page.getByText('个性化设置')).click();
    await expect(page.getByText('浅色·湛蓝')).toBeVisible();
    await page.getByText('浅色·湛蓝').click();

    // 断言：data-theme 切换为 light
    const lightTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(lightTheme).toBe('light');

    // 断言：localStorage 持久化
    const stored = await page.evaluate(() => localStorage.getItem('theme'));
    expect(stored).toBe('light');
  });

  test('主题切换后刷新页面仍保持', async ({ page }) => {
    // 切换到 dark
    await page.getByText('主题定制').or(page.getByText('个性化设置')).click();
    await page.getByText('深色·深蓝').click();

    // 断言：切换成功
    expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe('dark');

    // 刷新
    await page.reload();
    await page.waitForLoadState('networkidle');

    // 断言：刷新后仍为 dark
    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(theme).toBe('dark');

    // 断言：localStorage 仍为 dark
    const stored = await page.evaluate(() => localStorage.getItem('theme'));
    expect(stored).toBe('dark');
  });
});
