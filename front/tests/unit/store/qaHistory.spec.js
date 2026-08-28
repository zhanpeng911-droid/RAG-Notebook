import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useQaHistoryStore } from '../../../src/store/qaHistory'

describe('qaHistory store', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
  })

  it('初始为空', () => {
    const store = useQaHistoryStore()
    expect(store.history).toEqual([])
    expect(store.expandedId).toBeNull()
    expect(store.limit).toBe(10)
  })

  it('从 sessionStorage 恢复历史', () => {
    const valid = [{ id: 'qa-1', question: '问', thinking: [], citations: [], relatedNotes: [], time: '12:00' }]
    sessionStorage.setItem('qa-history-store', JSON.stringify(valid))
    const store = useQaHistoryStore()
    expect(store.history).toHaveLength(1)
    expect(store.history[0].id).toBe('qa-1')
  })

  it('忽略非法存储内容', () => {
    sessionStorage.setItem('qa-history-store', JSON.stringify([{ question: '缺id' }, 'junk', null]))
    const store = useQaHistoryStore()
    expect(store.history).toEqual([])
  })

  it('push 插入头部并限制 10 条', () => {
    const store = useQaHistoryStore()
    for (let i = 0; i < 12; i++) {
      store.push({ question: `问题${i}` })
    }
    expect(store.history).toHaveLength(10)
    expect(store.history[0].question).toBe('问题11')
    expect(store.expandedId).toBe(store.history[0].id)
    // 已持久化
    const saved = JSON.parse(sessionStorage.getItem('qa-history-store'))
    expect(saved).toHaveLength(10)
  })

  it('toggleExpand 切换展开状态', () => {
    const store = useQaHistoryStore()
    const item = store.push({ question: 'q' })
    expect(store.expandedId).toBe(item.id)  // push 已自动展开
    store.toggleExpand(item.id)
    expect(store.expandedId).toBeNull()
    store.toggleExpand(item.id)
    expect(store.expandedId).toBe(item.id)
  })

  it('clear 清空历史并移除存储', () => {
    const store = useQaHistoryStore()
    store.push({ question: 'q' })
    store.clear()
    expect(store.history).toEqual([])
    expect(sessionStorage.getItem('qa-history-store')).toBeNull()
  })
})
