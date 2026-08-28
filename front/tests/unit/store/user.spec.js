import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const httpGet = vi.fn()
const httpPost = vi.fn()
const httpPut = vi.fn()

vi.mock('../../../src/services/http', () => ({
  default: { get: (...a) => httpGet(...a), post: (...a) => httpPost(...a), put: (...a) => httpPut(...a) },
}))

const { useUserStore } = await import('../../../src/store/user')

describe('user store', () => {
  beforeEach(() => {
    localStorage.clear()
    httpGet.mockReset()
    httpPost.mockReset()
    httpPut.mockReset()
    setActivePinia(createPinia())
  })

  it('login 成功写入 token 与用户信息', async () => {
    httpPost.mockResolvedValue({
      status: 200,
      data: { token: 'jwt-1', user: { id: 1 }, message: 'ok' },
    })
    const store = useUserStore()
    const out = await store.login({ username: 'a', password: 'b' })
    expect(out.success).toBe(true)
    expect(store.isLogin).toBe(true)
    expect(store.token).toBe('jwt-1')
    expect(localStorage.getItem('jwt_token')).toBe('jwt-1')
  })

  it('login 无 token 返回失败', async () => {
    httpPost.mockResolvedValue({ status: 200, data: { message: '无 token' } })
    const store = useUserStore()
    const out = await store.login({ username: 'a', password: 'b' })
    expect(out.success).toBe(false)
  })

  it('login 请求异常返回失败消息', async () => {
    httpPost.mockRejectedValue({ response: { data: { message: '密码错误' } } })
    const store = useUserStore()
    const out = await store.login({ username: 'a', password: 'b' })
    expect(out.success).toBe(false)
    expect(out.message).toBe('密码错误')
  })

  it('logout 清理认证状态', async () => {
    httpPost.mockResolvedValue({ data: {} })
    const store = useUserStore()
    store.token = 't'
    store.isLogin = true
    localStorage.setItem('jwt_token', 't')
    await store.logout()
    expect(store.isLogin).toBe(false)
    expect(localStorage.getItem('jwt_token')).toBeNull()
  })

  it('clearAuth 清空状态', () => {
    const store = useUserStore()
    store.token = 't'
    store.isLogin = true
    localStorage.setItem('jwt_token', 't')
    store.clearAuth()
    expect(store.token).toBe('')
    expect(store.isLogin).toBe(false)
  })

  it('getUserInfoDetail 无 token 返回未登录', async () => {
    const store = useUserStore()
    const out = await store.getUserInfoDetail()
    expect(out.success).toBe(false)
    expect(out.message).toBe('未登录')
  })

  it('getUserInfoDetail 成功拉取', async () => {
    localStorage.setItem('jwt_token', 't')
    httpGet.mockResolvedValue({ status: 200, data: { data: { id: 1 } } })
    const store = useUserStore()
    const out = await store.getUserInfoDetail()
    expect(out.success).toBe(true)
    expect(store.userInfo.id).toBe(1)
  })

  it('updateUserInfo 无 token 返回未登录', async () => {
    const store = useUserStore()
    const out = await store.updateUserInfo({ bio: 'x' })
    expect(out.success).toBe(false)
  })

  it('updatePassword 成功', async () => {
    localStorage.setItem('jwt_token', 't')
    httpPost.mockResolvedValue({ status: 200, data: { message: 'ok' } })
    const store = useUserStore()
    const out = await store.updatePassword('old', 'new')
    expect(out.success).toBe(true)
  })

  it('register 成功自动登录', async () => {
    httpPost.mockResolvedValue({
      data: { status: 201, token: 't', user: { id: 1 }, message: 'ok' },
    })
    const store = useUserStore()
    const out = await store.register({ username: 'a', email: 'a@b.c', password: 'p', confirm_password: 'p' })
    expect(out.success).toBe(true)
    expect(store.isLogin).toBe(true)
  })

  it('register 服务端校验错误格式化', async () => {
    httpPost.mockRejectedValue({
      response: { data: { detail: { password: ['太短'] } } },
    })
    const store = useUserStore()
    const out = await store.register({ username: 'a', password: 'p', confirm_password: 'p' })
    expect(out.success).toBe(false)
    expect(out.message).toContain('密码：太短')
  })

  it('getUserBio 回退默认', () => {
    const store = useUserStore()
    expect(store.getUserBio).toBe('这是我的个人简介')
    store.userInfo = { bio: '自定义' }
    expect(store.getUserBio).toBe('自定义')
  })
})
