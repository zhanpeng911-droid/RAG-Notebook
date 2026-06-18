// @ts-check
import { test, expect } from '@playwright/test';

const FAKE_TOKEN = 'e2e-fake-token-for-testing';

// ==================== Mock 数据 ====================

const mockNotes = [
  {
    id: 'note-001',
    title: '测试笔记 Alpha',
    content: '这是笔记 Alpha 的内容，用于 E2E 测试。',
    category: 'study',
    tags: ['测试', 'E2E'],
    created_at: '2026-06-01T10:00:00Z',
    updated_at: '2026-06-08T12:00:00Z',
    user_id: 'e2e-user',
  },
  {
    id: 'note-002',
    title: '测试笔记 Beta',
    content: '这是笔记 Beta 的内容，包含一些不同的关键词。',
    category: 'work',
    tags: ['工作'],
    created_at: '2026-06-02T10:00:00Z',
    updated_at: '2026-06-07T12:00:00Z',
    user_id: 'e2e-user',
  },
];

let currentNotes = [];

function resetNotes() {
  currentNotes = [...mockNotes];
}

// ==================== Auth 注入 ====================

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

// ==================== Mock API ====================

async function setupMocks(page) {
  // Mock note/list
  await page.route('**/note/list**', (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get('q') || '';
    let filtered = currentNotes;
    if (q) {
      filtered = currentNotes.filter(
        (n) => n.title.includes(q) || n.content.includes(q)
      );
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, data: { notes: filtered, total: filtered.length } }),
    });
  });

  // Mock note/search
  await page.route('**/note/search**', (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get('q') || '';
    const filtered = currentNotes.filter(
      (n) => n.title.includes(q) || n.content.includes(q)
    );
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, data: { notes: filtered, total: filtered.length } }),
    });
  });

  // Mock note/create
  await page.route('**/note/create', (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    const body = route.request().postDataJSON();
    const newNote = {
      id: `note-${Date.now()}`,
      title: body.title || '',
      content: body.content || '',
      category: body.category || '',
      tags: body.tags || [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      user_id: 'e2e-user',
    };
    currentNotes.unshift(newNote);
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 200, data: { id: newNote.id } }),
    });
  });

  // Mock note/{id} (GET / PUT / DELETE)
  await page.route(/\/note\/note-[^/?]+(?:\?.*)?$/, (route) => {
    const url = route.request().url();
    const match = url.match(/\/note\/([^/?]+)/);
    if (!match) return route.fallback();
    const noteId = match[1];
    const method = route.request().method();

    if (method === 'GET') {
      const note = currentNotes.find((n) => n.id === noteId);
      if (!note) {
        return route.fulfill({
          status: 404, contentType: 'application/json',
          body: JSON.stringify({ code: 404, message: '笔记不存在' }),
        });
      }
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ code: 200, data: note }),
      });
    }
    if (method === 'PUT') {
      const body = route.request().postDataJSON();
      const idx = currentNotes.findIndex((n) => n.id === noteId);
      if (idx !== -1) {
        currentNotes[idx] = { ...currentNotes[idx], ...body, updated_at: new Date().toISOString() };
      }
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ code: 200 }),
      });
    }
    if (method === 'DELETE') {
      currentNotes = currentNotes.filter((n) => n.id !== noteId);
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ code: 200 }),
      });
    }
    return route.fallback();
  });
}

// ==================== 全端测试（列表/空状态/搜索） ====================

test.describe('笔记页面 — 列表与搜索（全端）', () => {
  test.beforeEach(async ({ page }) => {
    resetNotes();
    await injectAuth(page);
    await setupMocks(page);
  });

  test('有笔记时显示笔记列表和数量', async ({ page }) => {
    await page.goto('/notes');
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();
    await expect(page.getByText('测试笔记 Beta')).toBeVisible();
    await expect(page.getByText('2 篇')).toBeVisible();
  });

  test('空列表时显示空状态', async ({ page }) => {
    currentNotes = [];
    await page.goto('/notes');
    await expect(page.getByText('还没有笔记')).toBeVisible();
  });

  test('搜索过滤笔记', async ({ page }) => {
    await page.goto('/notes');
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();

    const searchInput = page.getByPlaceholder('搜索笔记...').first();
    await searchInput.fill('Alpha');
    await searchInput.press('Enter');

    // 搜索后应只显示 Alpha（mock 返回过滤后的列表）
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();
    await expect(page.getByText('1 篇')).toBeVisible();
  });
});

// ==================== 桌面端 CRUD 测试 ====================

test.describe('笔记页面 — 创建流程（桌面端）', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test.beforeEach(async ({ page }) => {
    resetNotes();
    await injectAuth(page);
    await setupMocks(page);
  });

  test('新建笔记：输入标题和内容，保存成功', async ({ page }) => {
    let createCalled = false;
    page.on('request', (req) => {
      if (req.url().includes('/note/create') && req.method() === 'POST') {
        createCalled = true;
      }
    });

    await page.goto('/notes');
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();

    await page.getByText('新建笔记').click();
    await expect(page).toHaveURL(/\/notes\/new/);

    // 输入标题
    await page.getByPlaceholder('输入笔记标题...').fill('E2E 创建的笔记');

    // 输入内容（bytemd textarea 在移动端可能不可见）
    const editor = page.locator('.bytemd-editor textarea, .bytemd textarea').first();
    if (await editor.isVisible().catch(() => false)) {
      await editor.fill('这是 E2E 自动创建的笔记内容');
    }

    // 点击保存（真实点击，不使用 dispatchEvent）
    await page.getByRole('button', { name: '保存' }).click();

    // 断言：保存成功 toast
    await expect(page.getByText('保存成功')).toBeVisible();
    // 断言：mock API 被调用
    expect(createCalled).toBe(true);
    // 断言：内存数据已更新
    const hasNewNote = currentNotes.some((n) => n.title === 'E2E 创建的笔记');
    expect(hasNewNote).toBe(true);
  });
});

test.describe('笔记页面 — 编辑流程（桌面端）', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test.beforeEach(async ({ page }) => {
    resetNotes();
    await injectAuth(page);
    await setupMocks(page);
  });

  test('编辑笔记：修改标题，保存成功', async ({ page }) => {
    let updateCalled = false;
    page.on('request', (req) => {
      if (req.url().includes('/note/note-001') && req.method() === 'PUT') {
        updateCalled = true;
      }
    });

    await page.goto('/notes/note-001');
    await expect(page.locator('.title-input')).toBeVisible();

    await page.locator('.title-input').fill('测试笔记 Alpha 已修改');
    await page.getByRole('button', { name: '保存' }).click();

    await expect(page.getByText('保存成功')).toBeVisible();
    expect(updateCalled).toBe(true);

    const updated = currentNotes.find((n) => n.id === 'note-001');
    expect(updated?.title).toBe('测试笔记 Alpha 已修改');
  });
});

test.describe('笔记页面 — 删除流程（桌面端）', () => {
  test.use({ viewport: { width: 1280, height: 720 } });

  test.beforeEach(async ({ page }) => {
    resetNotes();
    await injectAuth(page);
    await setupMocks(page);
  });

  test('删除笔记：确认后列表移除', async ({ page }) => {
    let deleteCalled = false;
    page.on('request', (req) => {
      if (req.url().includes('/note/note-001') && req.method() === 'DELETE') {
        deleteCalled = true;
      }
    });

    await page.goto('/notes');
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();

    // 切换到表格视图
    await page.locator('.btn-view-toggle').click();

    // 找到 Alpha 行的删除按钮
    const alphaRow = page.locator('.table-row').filter({ hasText: '测试笔记 Alpha' });
    await alphaRow.locator('.btn-icon-sm').click();

    // 确认删除弹窗
    await expect(page.getByText('确认删除')).toBeVisible();
    await page.locator('.van-dialog__confirm, .van-button--primary').last().click();

    await expect(page.getByText('删除成功')).toBeVisible();
    expect(deleteCalled).toBe(true);

    const hasAlpha = currentNotes.some((n) => n.id === 'note-001');
    expect(hasAlpha).toBe(false);
  });
});

// ==================== 移动端可用性测试 ====================

test.describe('笔记页面 — 移动端可用性', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    resetNotes();
    await injectAuth(page);
    await setupMocks(page);
  });

  test('笔记列表正常显示', async ({ page }) => {
    await page.goto('/notes');
    await expect(page.getByText('测试笔记 Alpha')).toBeVisible();
    await expect(page.getByText('测试笔记 Beta')).toBeVisible();
    // 移动端无水平溢出
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    const clientWidth = await page.evaluate(() => document.documentElement.clientWidth);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth + 1);
  });

  test('点击新建笔记可进入编辑页', async ({ page }) => {
    await page.goto('/notes');
    await page.getByText('新建笔记').click();
    await expect(page).toHaveURL(/\/notes\/new/);
    // 标题输入框可见
    await expect(page.getByPlaceholder('输入笔记标题...')).toBeVisible();
    // 保存按钮可见
    await expect(page.getByRole('button', { name: '保存' })).toBeVisible();
  });

  test('移动端表格视图隐藏操作列（产品设计）', async ({ page }) => {
    await page.goto('/notes');
    // 切换到表格视图
    await page.locator('.btn-view-toggle').click();
    // 操作列在移动端应隐藏（CSS: .col-actions { display: none }）
    const actionsVisible = await page.locator('.col-actions').first().isVisible();
    expect(actionsVisible).toBe(false);
  });
});
