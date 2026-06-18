// @ts-check
import { test, expect } from '@playwright/test';

const FAKE_TOKEN = 'e2e-fake-token-for-testing';

async function injectAuth(page) {
  await page.goto('/login');
  await page.evaluate((token) => {
    localStorage.setItem('jwt_token', token);
    localStorage.setItem('user-store', JSON.stringify({
      state: { token, isLogin: true, userInfo: { username: 'e2e-user' } },
      version: 1
    }));
  }, FAKE_TOKEN);
}

test.describe('受保护路由 — 未登录状态', () => {
  test('访问 /settings 应跳转到 /login 并保留 redirect', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/login/);
    await expect(page).toHaveURL(/redirect=\/settings/);
    await expect(page.getByText('欢迎回来')).toBeVisible();
  });

  test('访问 /notes 应跳转到 /login 并保留 redirect', async ({ page }) => {
    await page.goto('/notes');
    await expect(page).toHaveURL(/\/login/);
    await expect(page).toHaveURL(/redirect=\/notes/);
  });

  test('访问 /chat 应跳转到 /login', async ({ page }) => {
    await page.goto('/chat');
    await expect(page).toHaveURL(/\/login/);
  });

  test('访问 /knowledge 应跳转到 /login', async ({ page }) => {
    await page.goto('/knowledge');
    await expect(page).toHaveURL(/\/login/);
  });

  test('访问 /my 应跳转到 /login', async ({ page }) => {
    await page.goto('/my');
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe('受保护路由 — 设置 fake token 后（200 mock）', () => {
  test('访问 /settings 应停留在 /settings', async ({ page }) => {
    await injectAuth(page);
    await page.route('**/user/detail/**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ data: { username: 'e2e-user' } }),
      })
    );
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/settings/);
    await expect(page.getByText('AI 模型配置')).toBeVisible();
  });

  test('访问 /notes 应停留在 /notes', async ({ page }) => {
    await injectAuth(page);
    await page.route('**/user/detail/**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ data: { username: 'e2e-user' } }),
      })
    );
    await page.route('**/note/list**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ code: 200, data: { notes: [], total: 0 } }),
      })
    );
    await page.goto('/notes');
    await expect(page).toHaveURL(/\/notes/);
    await expect(page.locator('.notes-title')).toBeVisible();
    await expect(page.getByText('还没有笔记')).toBeVisible();
  });

  test('访问 /chat 应停留在 /chat', async ({ page }) => {
    await injectAuth(page);
    await page.route('**/user/detail/**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ data: { username: 'e2e-user' } }),
      })
    );
    await page.route('**/chat/sessions**', (route) =>
      route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify([]),
      })
    );
    await page.goto('/chat');
    await expect(page).toHaveURL(/\/chat/);
  });
});

test.describe('受保护路由 — 401 清除 token', () => {
  test('note/list 返回 401 时应清除 jwt_token', async ({ page }) => {
    await injectAuth(page);
    // 笔记页 mount 时会请求 note/list，mock 返回 401
    await page.route('**/note/list**', (route) =>
      route.fulfill({
        status: 401, contentType: 'application/json',
        body: JSON.stringify({ detail: 'Unauthorized' }),
      })
    );
    await page.goto('/notes');
    // 等待 HTTP 拦截器处理 401（内部有 500ms isRedirecting 保护）
    await page.waitForTimeout(1000);
    // 断言：jwt_token 被清除（clearAuth() 的核心行为）
    const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
    expect(token).toBeNull();
    // 断言：重定向到 /login（clearAuth 后 router.push 触发）
    await expect(page).toHaveURL(/\/login/);
  });
});
