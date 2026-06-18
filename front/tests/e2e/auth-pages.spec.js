// @ts-check
import { test, expect } from '@playwright/test';

// ==================== 登录页 ====================

test.describe('登录页 /login', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
  });

  test('页面正常打开，关键元素可见', async ({ page }) => {
    await expect(page.getByText('欢迎回来')).toBeVisible();
    await expect(page.getByText('继续管理你的笔记和知识库')).toBeVisible();
    await expect(page.getByPlaceholder('请输入用户名')).toBeVisible();
    await expect(page.getByPlaceholder('请输入密码')).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
    await expect(page.getByText('去注册')).toBeVisible();
  });

  test('空表单点击登录显示校验提示', async ({ page }) => {
    await page.getByRole('button', { name: '登录' }).click();
    // Vant 表单校验或 toast 提示
    await expect(page.getByText('请输入用户名').or(page.getByText('请填写用户名'))).toBeVisible();
  });

  test('点击去注册跳转到注册页', async ({ page }) => {
    await page.getByText('去注册').click();
    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByText('创建账号')).toBeVisible();
  });
});

// ==================== 注册页 ====================

test.describe('注册页 /register', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/register');
  });

  test('页面正常打开，关键元素可见', async ({ page }) => {
    await expect(page.getByText('创建账号')).toBeVisible();
    await expect(page.getByText('开始整理你的笔记和知识库')).toBeVisible();
    await expect(page.getByPlaceholder('请输入用户名')).toBeVisible();
    await expect(page.getByPlaceholder('请输入邮箱地址')).toBeVisible();
    await expect(page.getByPlaceholder('请输入手机号码')).toBeVisible();
    await expect(page.getByPlaceholder('请输入密码（6-20位）')).toBeVisible();
    await expect(page.getByPlaceholder('请确认密码')).toBeVisible();
    await expect(page.getByRole('button', { name: '注册' })).toBeVisible();
    await expect(page.getByText('去登录')).toBeVisible();
  });

  test('空表单点击注册显示校验提示', async ({ page }) => {
    await page.getByRole('button', { name: '注册' }).click();
    await expect(page.getByText('请输入用户名')).toBeVisible();
  });

  test('邮箱格式错误显示校验', async ({ page }) => {
    await page.getByPlaceholder('请输入用户名').fill('testuser');
    await page.getByPlaceholder('请输入邮箱地址').fill('bad-email');
    await page.getByPlaceholder('请输入密码（6-20位）').fill('TestPwd123');
    await page.getByPlaceholder('请确认密码').fill('TestPwd123');
    await page.getByRole('button', { name: '注册' }).click();
    // Vant 同时显示 field error 和 toast，用 .first() 避免 strict mode 冲突
    await expect(page.getByText('请输入正确的邮箱地址').first()).toBeVisible();
  });

  test('密码不一致显示校验', async ({ page }) => {
    await page.getByPlaceholder('请输入用户名').fill('testuser');
    await page.getByPlaceholder('请输入邮箱地址').fill('test@example.com');
    await page.getByPlaceholder('请输入密码（6-20位）').fill('TestPwd123');
    await page.getByPlaceholder('请确认密码').fill('Mismatch321');
    await page.getByRole('button', { name: '注册' }).click();
    await expect(page.getByText('两次输入的密码不一致')).toBeVisible();
  });

  test('点击去登录跳转到登录页', async ({ page }) => {
    await page.getByText('去登录').click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByText('欢迎回来')).toBeVisible();
  });
});

// ==================== 移动端登录页 ====================

test.describe('移动端登录页', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('登录页关键元素可见且无水平溢出', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByText('欢迎回来')).toBeVisible();
    await expect(page.getByRole('button', { name: '登录' })).toBeVisible();

    // 无水平滚动
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
  });

  test('注册页关键元素可见且无水平溢出', async ({ page }) => {
    await page.goto('/register');
    await expect(page.getByText('创建账号')).toBeVisible();
    await expect(page.getByRole('button', { name: '注册' })).toBeVisible();

    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
  });
});
