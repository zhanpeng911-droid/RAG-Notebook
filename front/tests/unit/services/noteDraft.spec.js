import { describe, it, expect, beforeEach } from 'vitest'
import {
  saveDraft,
  loadDraft,
  clearDraft,
  draftStorageKey,
} from '../../../src/services/noteDraft'

describe('noteDraft 用户隔离', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('同一用户+同一笔记可保存并恢复', () => {
    saveDraft('user-a', 'note-1', { title: '标题', content: '内容', tags: ['t'], category: 'work' })
    const draft = loadDraft('user-a', 'note-1')
    expect(draft.title).toBe('标题')
    expect(draft.content).toBe('内容')
    expect(draft.tags).toEqual(['t'])
    expect(draft.owner).toBe('user-a')
  })

  it('B 用户看不到 A 用户的草稿', () => {
    saveDraft('user-a', 'note-1', { title: 'A 的草稿', content: 'secret' })
    expect(loadDraft('user-b', 'note-1')).toBeNull()
    expect(loadDraft('user-a', 'note-1')).not.toBeNull()
  })

  it('同一用户的不同笔记草稿互不串扰', () => {
    saveDraft('user-a', 'note-1', { title: '一', content: '1' })
    saveDraft('user-a', 'note-2', { title: '二', content: '2' })
    expect(loadDraft('user-a', 'note-1').content).toBe('1')
    expect(loadDraft('user-a', 'note-2').content).toBe('2')
  })

  it('旧无所有者草稿不得自动归属新登录用户', () => {
    // 模拟旧格式：单一键 + 无 owner 字段
    localStorage.setItem(draftStorageKey('user-a', 'note-1'), JSON.stringify({
      title: '旧草稿',
      content: 'x',
      noteId: 'note-1',
    }))
    // 无 owner 视为不属于任何当前用户
    expect(loadDraft('user-a', 'note-1')).toBeNull()
    expect(loadDraft('user-b', 'note-1')).toBeNull()
  })

  it('clearDraft 删除当前用户草稿', () => {
    saveDraft('user-a', 'note-1', { title: 'x', content: 'y' })
    clearDraft('user-a', 'note-1')
    expect(loadDraft('user-a', 'note-1')).toBeNull()
  })
})
