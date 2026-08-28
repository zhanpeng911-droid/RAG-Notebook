import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const httpGet = vi.fn()
const httpDelete = vi.fn()
const httpPost = vi.fn()

vi.mock('../../../src/services/http', () => ({
  default: { get: (...a) => httpGet(...a), delete: (...a) => httpDelete(...a), post: (...a) => httpPost(...a) },
}))

const { useSessionStore } = await import('../../../src/store/session')

describe('session store', () => {
  beforeEach(() => {
    localStorage.clear()
    httpGet.mockReset()
    httpDelete.mockReset()
    httpPost.mockReset()
    setActivePinia(createPinia())
  })

  it('getUserSessions 拉取并排序', async () => {
    httpGet.mockResolvedValue({
      data: { data: { sessions: [
        { id: 's1', title: '旧', created_at: '2026-01-01', updated_at: '2026-01-01' },
        { id: 's2', title: '新', created_at: '2026-02-01', updated_at: '2026-02-01' },
      ] } },
    })
    const store = useSessionStore()
    const out = await store.getUserSessions('u1')
    expect(out.success).toBe(true)
    expect(store.sessions[0].session_id).toBe('s2')
  })

  it('getUserSessions 失败返回错误', async () => {
    httpGet.mockRejectedValue({ response: { data: { detail: '挂了' } } })
    const store = useSessionStore()
    const out = await store.getUserSessions('u1')
    expect(out.success).toBe(false)
    expect(out.message).toBe('挂了')
    expect(store.loading).toBe(false)
  })

  it('getSession 设置 currentSession', async () => {
    httpGet.mockResolvedValue({ data: { data: { session_id: 's1', title: '会话' } } })
    const store = useSessionStore()
    const out = await store.getSession('s1')
    expect(out.success).toBe(true)
    expect(store.currentSession.session_id).toBe('s1')
  })

  it('deleteSession 从列表移除并清 currentSession', async () => {
    httpDelete.mockResolvedValue({ data: {} })
    const store = useSessionStore()
    store.sessions = [{ session_id: 's1' }, { session_id: 's2' }]
    store.currentSession = { session_id: 's1' }
    const out = await store.deleteSession('s1')
    expect(out.success).toBe(true)
    expect(store.sessions).toHaveLength(1)
    expect(store.currentSession).toBeNull()
  })

  it('createSession 无 token 走 fetch 失败路径', async () => {
    const store = useSessionStore()
    const out = await store.createSession('你好')
    expect(out.success).toBe(false)
  })

  it('setCurrentSession / clearSessions', () => {
    const store = useSessionStore()
    store.setCurrentSession({ session_id: 's1' })
    expect(store.currentSession.session_id).toBe('s1')
    store.clearSessions()
    expect(store.sessions).toEqual([])
    expect(store.currentSession).toBeNull()
  })
})
