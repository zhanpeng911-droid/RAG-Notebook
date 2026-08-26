import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createSSEStream } from '../../../src/services/sseClient'

/**
 * 构造一个 fetch Response 形状的对象，body 按 chunks 顺序吐出。
 * 用于精确控制 SSE 分帧（半包/粘包）场景。
 */
function sseResponse(chunks) {
  const encoder = new TextEncoder()
  let i = 0
  return {
    ok: true,
    body: {
      getReader() {
        return {
          read: async () => {
            if (i < chunks.length) {
              return { done: false, value: encoder.encode(chunks[i++]) }
            }
            return { done: true }
          },
        }
      },
    },
  }
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0))
}

describe('sseClient.createSSEStream', () => {
  beforeEach(() => {
    localStorage.removeItem('jwt_token')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('事件体跨 chunk 到达（半包）时能正确拼帧解析', async () => {
    const onThinking = vi.fn()
    const onFinally = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"thinking","stage":"retri',
        'eval","content":"正在检索"}\n\n',
      ])
    )

    createSSEStream('/api/v1/x', { query: 'q' }, { onThinking, onFinally })
    await flush()

    expect(onThinking).toHaveBeenCalledTimes(1)
    expect(onThinking).toHaveBeenCalledWith(
      expect.objectContaining({ stage: 'retrieval', content: '正在检索' })
    )
    expect(onFinally).toHaveBeenCalledTimes(1)
  })

  it('同一 chunk 内多条事件（粘包）全部分发', async () => {
    const onThinking = vi.fn()
    const onDone = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"started"}\n\n' +
          'data: {"type":"planning"}\n\n' +
          'data: {"type":"done","session_id":"s-1"}\n\n',
      ])
    )

    const handlers = { onThinking, onDone }
    createSSEStream('/api/v1/x', {}, handlers)
    await flush()

    // started/planning 走 agentic stage → thinking 步骤
    expect(onThinking).toHaveBeenCalledTimes(2)
    // done 事件透传完整 json
    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 's-1' })
    )
  })

  it('completed 事件：onResponse 携带 answer，等待 onCompleted 后触发 onDone', async () => {
    const order = []
    const onResponse = vi.fn((json) => order.push('response'))
    const onCompleted = vi.fn(async () => {
      await new Promise((r) => setTimeout(r, 5))
      order.push('completed')
    })
    const onDone = vi.fn(() => order.push('done'))

    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"completed","answer":"答案","citations":[],"session_id":"s-9"}\n\n',
      ])
    )

    createSSEStream('/api/v1/x', {}, { onResponse, onCompleted, onDone })
    // onCompleted 内部有异步延时，等待其完成后再断言顺序
    await new Promise((r) => setTimeout(r, 20))

    expect(onResponse).toHaveBeenCalledWith({ content: '答案' })
    expect(order).toEqual(['response', 'completed', 'done'])
  })

  it('Agentic 阶段事件转换为 thinking 步骤并携带中文标签与过程数据', async () => {
    const onThinking = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse([
        'data: {"type":"retrieving","state":{"query_type":"factual"},"plan":{"top_k":8}}\n\n',
      ])
    )

    createSSEStream('/api/v1/x', {}, { onThinking })
    await flush()

    expect(onThinking).toHaveBeenCalledTimes(1)
    const arg = onThinking.mock.calls[0][0]
    expect(arg.stage).toBe('retrieving')
    expect(arg.content).toBe('正在检索知识库')
    expect(arg.details.plan).toEqual({ top_k: 8 })
  })

  it('非法 JSON 数据行不抛未捕获异常，后续事件继续处理', async () => {
    const warnSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const onDone = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse(['data: {{{not-json}}}\n\ndata: {"type":"done","session_id":"s-2"}\n\n'])
    )

    expect(() =>
      createSSEStream('/api/v1/x', {}, { onDone })
    ).not.toThrow()
    await flush()

    expect(onDone).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: 's-2' })
    )
    warnSpy.mockRestore()
  })

  it('error 事件触发 onError 并透出消息', async () => {
    const onError = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(
      sseResponse(['data: {"type":"error","content":"生成失败"}\n\n'])
    )

    createSSEStream('/api/v1/x', {}, { onError })
    await flush()

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0].message).toBe('生成失败')
  })

  it('HTTP 非 2xx 响应触发 onError（优先取 detail）', async () => {
    const onError = vi.fn()
    const onFinally = vi.fn()
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      json: async () => ({ detail: '会话不存在' }),
    })

    createSSEStream('/api/v1/x', {}, { onError, onFinally })
    await flush()

    expect(onError).toHaveBeenCalledTimes(1)
    expect(onError.mock.calls[0][0].message).toBe('会话不存在')
    expect(onFinally).toHaveBeenCalledTimes(1)
  })

  it('abort 后不触发 onError', async () => {
    const onError = vi.fn()
    let holdReject
    global.fetch = vi.fn().mockReturnValue(
      new Promise((_, reject) => {
        holdReject = reject
      })
    )

    const abort = createSSEStream('/api/v1/x', {}, { onError })
    abort() // AbortError → 静默
    await flush()

    expect(onError).not.toHaveBeenCalled()
  })

  it('携带本地 token 时设置 Authorization 头', async () => {
    localStorage.setItem('jwt_token', 'tok-123')
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(['data: {"type":"done"}\n\n']))
    global.fetch = fetchMock

    createSSEStream('/api/v1/x', { query: 'q' }, {})
    await flush()

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers.Authorization).toBe('Bearer tok-123')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.body).toBe(JSON.stringify({ query: 'q' }))
  })
})
