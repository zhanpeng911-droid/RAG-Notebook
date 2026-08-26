import { describe, it, expect, vi, beforeEach } from 'vitest'

// http.js 内部引用 router 与 user store；单测中替换为受控 mock
const routerPush = vi.fn().mockResolvedValue(undefined)
const clearAuth = vi.fn()

vi.mock('../../../src/router', () => ({
  default: {
    push: routerPush,
    currentRoute: { value: { fullPath: '/notes', name: 'NoteList' } },
  },
}))

vi.mock('../../../src/store/user', () => ({
  useUserStore: () => ({ clearAuth }),
}))

const { default: http } = await import('../../../src/services/http')

function okAdapter(body = {}) {
  return vi.fn(async (config) => ({
    status: 200,
    statusText: 'OK',
    data: body,
    headers: {},
    config,
  }))
}

/**
 * 模拟真实 axios adapter：非 2xx 时按 config.validateStatus 构造
 * AxiosError 形状的 rejection（axios 本身不会在 adapter 之后二次校验）。
 */
function statusAdapter(status, body = {}) {
  return (config) => {
    if (config.validateStatus && !config.validateStatus(status)) {
      const err = new Error(`Request failed with status code ${status}`)
      err.isAxiosError = true
      err.config = config
      err.response = { status, statusText: 'ERR', data: body, headers: {}, config }
      return Promise.reject(err)
    }
    return Promise.resolve({ status, statusText: 'OK', data: body, headers: {}, config })
  }
}

describe('services/http 拦截器', () => {
  beforeEach(() => {
    localStorage.clear()
    routerPush.mockClear()
    clearAuth.mockClear()
  })

  it('携带本地 token 时注入 Authorization 头', async () => {
    localStorage.setItem('jwt_token', 'tok-abc')
    const adapter = okAdapter()
    http.defaults.adapter = adapter

    await http.get('/note/list')

    expect(adapter.mock.calls[0][0].headers.Authorization).toBe('Bearer tok-abc')
  })

  it('认证排除路径（/user/login/）不注入 Authorization', async () => {
    localStorage.setItem('jwt_token', 'tok-abc')
    const adapter = okAdapter()
    http.defaults.adapter = adapter

    await http.post('/user/login/', { username: 'u' })

    const cfg = adapter.mock.calls[0][0]
    expect(cfg.headers.Authorization).toBeUndefined()
  })

  it('敏感 GET 注入 Cache-Control no-store 与 X-Request-Id', async () => {
    const adapter = okAdapter()
    http.defaults.adapter = adapter

    await http.get('/note/list')

    const cfg = adapter.mock.calls[0][0]
    expect(cfg.headers['Cache-Control']).toBe('no-store')
    expect(cfg.headers['Pragma']).toBe('no-cache')
    expect(cfg.headers['X-Request-Id']).toBeTruthy()
  })

  it('非敏感路径与 POST 不注入 Cache-Control', async () => {
    const adapter = okAdapter()
    http.defaults.adapter = adapter

    await http.get('/some/other/path')
    const cfg1 = adapter.mock.calls[0][0]
    expect(cfg1.headers['Cache-Control']).toBeUndefined()

    await http.post('/note/create', {})
    const cfg2 = adapter.mock.calls[1][0]
    expect(cfg2.headers['Cache-Control']).toBeUndefined()
  })

  it('401 响应：清除认证并跳转登录页（带 redirect 回参）', async () => {
    localStorage.setItem('jwt_token', 'expired')
    http.defaults.adapter = statusAdapter(401)

    await expect(http.get('/note/list')).rejects.toBeTruthy()

    expect(clearAuth).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Login',
        query: { redirect: '/notes' },
      })
    )
    localStorage.removeItem('jwt_token')
  })

  it('公开认证页（Login）上的 401 不触发跳转', async () => {
    // 重写 currentRoute 为登录页
    const routerModule = await import('../../../src/router')
    routerModule.default.currentRoute = { value: { fullPath: '/login', name: 'Login' } }

    http.defaults.adapter = statusAdapter(401)

    await expect(http.post('/user/login/', {})).rejects.toBeTruthy()

    expect(routerPush).not.toHaveBeenCalled()

    // 还原为受保护页上下文
    routerModule.default.currentRoute = { value: { fullPath: '/notes', name: 'NoteList' } }
  })

  it('500 等其他错误不触发登出跳转，直接向上抛出', async () => {
    http.defaults.adapter = statusAdapter(500, { detail: 'server error' })

    await expect(http.get('/note/list')).rejects.toMatchObject({
      response: { status: 500 },
    })
    expect(clearAuth).not.toHaveBeenCalled()
    expect(routerPush).not.toHaveBeenCalled()
  })
})
