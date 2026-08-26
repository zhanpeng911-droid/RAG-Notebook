// @ts-check
/**
 * Full-stack E2E 测试 —— 需要真实后端 + 数据库
 *
 * 仅限本地开发使用，不在仓库中内置默认测试账号或密码。
 *
 * 运行方式：
 *   $env:E2E_FULL_STACK="true"
 *   $env:E2E_USERNAME="your-local-user"
 *   $env:E2E_PASSWORD="your-local-password"
 *   npm run test:e2e:full -- --project=chromium
 *
 * 默认不运行，不会被 npm run test:e2e 或 test:e2e:phase2 执行。
 */
import { test, expect } from '@playwright/test';

// ==================== 环境变量 ====================

const E2E_ENABLED = process.env.E2E_FULL_STACK === 'true';
const USERNAME = process.env.E2E_USERNAME || '';
const PASSWORD = process.env.E2E_PASSWORD || '';
const FASTAPI_URL = process.env.E2E_FASTAPI_URL || 'http://127.0.0.1:8000';
const DJANGO_URL = process.env.E2E_DJANGO_URL || 'http://127.0.0.1:8001';

test.skip(!E2E_ENABLED, 'E2E_FULL_STACK 未设置，跳过 full-stack 测试（需要真实后端服务）');
test.skip(E2E_ENABLED && (!USERNAME || !PASSWORD), '需要显式设置 E2E_USERNAME 和 E2E_PASSWORD，仓库中不提供默认测试账号');

// ==================== 唯一测试数据 ====================

const TS = Date.now();
const RAND = Math.random().toString(36).slice(2, 6);
const UNIQUE_ID = `${TS}-${RAND}`;
const NOTE_TITLE = `E2E-PW-${UNIQUE_ID}`;
const NOTE_CONTENT = `Playwright full-stack content ${UNIQUE_ID}`;
const NOTE_TITLE_UPDATED = `E2E-PW-UPDATED-${UNIQUE_ID}`;

// ==================== 后端健康检查 ====================

async function waitForBackend(timeoutMs = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(`${FASTAPI_URL}/health/live`, { signal: AbortSignal.timeout(3000) });
      if (res.ok) return true;
    } catch { /* 继续重试 */ }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return false;
}

async function checkServices() {
  const errors = [];
  try {
    const res = await fetch(`${FASTAPI_URL}/health/live`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) errors.push(`FastAPI /health/live 返回 ${res.status}`);
  } catch (e) {
    errors.push(`FastAPI 不可达 (${FASTAPI_URL}): ${e.message}`);
  }
  try {
    const res = await fetch(`${DJANGO_URL}/user/detail/`, { signal: AbortSignal.timeout(5000) });
    if (res.status >= 500) errors.push(`Django 服务异常 (${DJANGO_URL}): status ${res.status}`);
  } catch (e) {
    errors.push(`Django 用户服务不可达 (${DJANGO_URL}): ${e.message}`);
  }
  return errors;
}

// ==================== Helper ====================

async function login(page) {
  await page.goto('/login');
  await page.getByPlaceholder('请输入用户名').fill(USERNAME);
  await page.getByPlaceholder('请输入密码').fill(PASSWORD);
  await page.getByRole('button', { name: '登录' }).click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 10000 });
  const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
  expect(token, '登录后应存储 jwt_token').toBeTruthy();
}

async function deleteNoteViaAPI(page, noteId) {
  const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
  if (!token || !noteId) return false;
  return await page.evaluate(
    async ({ id, tok }) => {
      const r = await fetch(`/note/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${tok}` },
      });
      return r.ok;
    },
    { id: noteId, tok: token }
  );
}

async function findNoteIdByTitle(page, title) {
  const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
  if (!token) return null;
  return await page.evaluate(
    async ({ t, tok }) => {
      const res = await fetch('/note/list?page=1&page_size=100', {
        headers: { Authorization: `Bearer ${tok}` },
      });
      const data = await res.json();
      return data.data?.notes?.find((n) => n.title === t)?.id || null;
    },
    { t: title, tok: token }
  );
}

// ==================== 测试套件 ====================

test.describe('Full-stack: 登录 + 笔记 CRUD', () => {
  // 整个 describe 的前置检查：后端必须可用
  test.beforeAll(async () => {
    const healthy = await waitForBackend(15000);
    if (!healthy) {
      const errors = await checkServices();
      console.error(`[full-stack] 后端服务不可用:\n${errors.join('\n')}`);
    }
    expect(healthy, `后端服务不可用，无法运行 full-stack 测试。请先启动 FastAPI (${FASTAPI_URL}) 和 Django (${DJANGO_URL})。`).toBe(true);
  });

  test('真实登录成功', async ({ page }) => {
    await login(page);
    await expect(page).not.toHaveURL(/\/login/);
    const bodyText = await page.textContent('body');
    expect(bodyText?.length, '页面不应白屏').toBeGreaterThan(0);
    const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
    expect(token).toBeTruthy();
    expect(token.length).toBeGreaterThan(10);
  });

  test('创建、读取、编辑、删除笔记', async ({ page }) => {
    let createdNoteId;

    // --- 登录 ---
    await login(page);

    // --- 创建 ---
    await page.goto('/notes');
    await page.getByText('新建笔记').click();
    await expect(page).toHaveURL(/\/notes\/new/);
    await page.getByPlaceholder('输入笔记标题...').fill(NOTE_TITLE);
    const editor = page.locator('.bytemd-editor textarea, .bytemd textarea').first();
    if (await editor.isVisible().catch(() => false)) {
      await editor.fill(NOTE_CONTENT);
    }
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.getByText('保存成功')).toBeVisible({ timeout: 10000 });

    // --- 读取 ---
    await page.goto('/notes');
    await expect(page.getByText(NOTE_TITLE)).toBeVisible({ timeout: 10000 });
    createdNoteId = await findNoteIdByTitle(page, NOTE_TITLE);
    expect(createdNoteId, '应能找到创建的笔记').toBeTruthy();

    // --- 编辑 ---
    await page.goto(`/notes/${createdNoteId}`);
    await expect(page.locator('.title-input')).toBeVisible();
    await page.locator('.title-input').fill(NOTE_TITLE_UPDATED);
    await page.getByRole('button', { name: '保存' }).click();
    await expect(page.getByText('保存成功')).toBeVisible({ timeout: 10000 });
    await page.goto('/notes');
    await expect(page.getByText(NOTE_TITLE_UPDATED)).toBeVisible({ timeout: 10000 });

    // --- 删除 ---
    await page.locator('.btn-view-toggle').click();
    const row = page.locator('.table-row').filter({ hasText: NOTE_TITLE_UPDATED });
    await row.locator('.btn-icon-sm').click();
    await expect(page.getByText('确认删除')).toBeVisible();
    await page.locator('.van-dialog__confirm, .van-button--primary').last().click();
    await expect(page.getByText('删除成功')).toBeVisible({ timeout: 10000 });
    await expect(page.getByText(NOTE_TITLE_UPDATED)).not.toBeVisible({ timeout: 5000 });

    // --- 清理兜底 ---
    if (createdNoteId) await deleteNoteViaAPI(page, createdNoteId);
  });

  // afterEach 兜底清理
  test.afterEach(async ({ page }) => {
    try {
      const token = await page.evaluate(() => localStorage.getItem('jwt_token'));
      if (!token) {
        await page.goto('/login');
        await page.getByPlaceholder('请输入用户名').fill(USERNAME);
        await page.getByPlaceholder('请输入密码').fill(PASSWORD);
        await page.getByRole('button', { name: '登录' }).click();
        await page.waitForURL(/\/notes/, { timeout: 10000 }).catch(() => {});
      }
      const noteId = await findNoteIdByTitle(page, NOTE_TITLE_UPDATED)
        || await findNoteIdByTitle(page, NOTE_TITLE);
      if (noteId) await deleteNoteViaAPI(page, noteId);
    } catch { /* 清理失败不阻塞 */ }
  });
});
