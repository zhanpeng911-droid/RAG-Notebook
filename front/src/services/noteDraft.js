/**
 * 笔记草稿存储 —— 按 用户 ID + 笔记 ID 隔离。
 *
 * 旧实现用单一键 `note_draft`，无所有者：切换账号后可能把 A 的草稿
 * 恢复给 B。现在键为 `note_draft:{userId}:{noteId}`，草稿内容也保存
 * owner，恢复时再次比对；无所有者旧草稿不再自动归属新登录用户。
 *
 * 登出时不清空 localStorage 全量（保留主题等非敏感偏好），旧草稿由
 * 当前用户主动导出/丢弃；这里保证的是不会被静默恢复给下一位用户。
 */
const PREFIX = 'note_draft';

function normalizeUser(userId) {
  return (userId && String(userId).trim()) || 'anon';
}

function normalizeNote(noteId) {
  return (noteId && String(noteId).trim()) || 'new';
}

export function draftStorageKey(userId, noteId) {
  return `${PREFIX}:${normalizeUser(userId)}:${normalizeNote(noteId)}`;
}

export function saveDraft(userId, noteId, data) {
  try {
    const owner = normalizeUser(userId);
    localStorage.setItem(draftStorageKey(owner, noteId), JSON.stringify({
      owner,
      noteId: normalizeNote(noteId),
      title: data.title || '',
      content: data.content || '',
      tags: data.tags || [],
      category: data.category || '',
      timestamp: Date.now(),
    }));
    return true;
  } catch {
    return false;
  }
}

export function loadDraft(userId, noteId) {
  try {
    const owner = normalizeUser(userId);
    const raw = localStorage.getItem(draftStorageKey(owner, noteId));
    if (!raw) return null;
    const draft = JSON.parse(raw);
    // 必须同时满足：草稿属于当前用户、属于当前笔记；否则视为不存在
    if (!draft || draft.owner !== owner) return null;
    if (noteId && draft.noteId !== normalizeNote(noteId)) return null;
    return draft;
  } catch {
    return null;
  }
}

export function clearDraft(userId, noteId) {
  try {
    localStorage.removeItem(draftStorageKey(normalizeUser(userId), noteId));
  } catch {
    /* ignore */
  }
}
