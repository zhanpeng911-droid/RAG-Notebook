// @ts-check
import { test, expect } from '@playwright/test';

test.describe('主题切换', () => {
  test('默认主题是浅色，CSS 变量不是纯白', async ({ page }) => {
    await page.goto('/login');

    // data-theme 应为 light
    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(theme).toBe('light');

    // --color-bg 应为浅蓝色 #e7eef7，不是纯白
    const bgColor = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()
    );
    expect(bgColor).not.toBe('#fff');
    expect(bgColor).not.toBe('#ffffff');
    expect(bgColor).not.toBe('rgb(255, 255, 255)');
    // 应包含浅蓝灰色调
    expect(bgColor.toLowerCase()).toContain('#e7eef7');

    // --color-card 不应为空
    const cardColor = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-card').trim()
    );
    expect(cardColor).not.toBe('');
  });

  test('localStorage 设置 dark 后刷新生效', async ({ page }) => {
    await page.goto('/login');

    // 设置深色主题
    await page.evaluate(() => localStorage.setItem('theme', 'dark'));
    await page.reload();

    // 等待主题应用
    const theme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(theme).toBe('dark');

    // --color-bg 应为深色值
    const bgColor = await page.evaluate(() =>
      getComputedStyle(document.documentElement).getPropertyValue('--color-bg').trim()
    );
    expect(bgColor).not.toBe('#e7eef7');
    expect(bgColor).not.toBe('#fff');
    expect(bgColor).not.toBe('rgb(255, 255, 255)');
  });

  test('localStorage 设置 light 后刷新生效', async ({ page }) => {
    await page.goto('/login');

    // 先设为 dark
    await page.evaluate(() => localStorage.setItem('theme', 'dark'));
    await page.reload();
    const darkTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(darkTheme).toBe('dark');

    // 再切回 light
    await page.evaluate(() => localStorage.setItem('theme', 'light'));
    await page.reload();
    const lightTheme = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme')
    );
    expect(lightTheme).toBe('light');
  });
});
