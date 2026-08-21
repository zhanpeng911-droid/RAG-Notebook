/**
 * SSE stream client.
 * @param {string} url
 * @param {object|FormData} body - JSON object or FormData for file uploads
 * @param {object} handlers - { onThinking, onResponse, onDone, onError, onStep }
 * @returns {function} abort
 */

// Agentic RAG 阶段事件的中文标签
const AGENTIC_STAGE_LABELS = {
  started: '开始处理',
  planning: '正在规划检索策略',
  retrieving: '正在检索知识库',
  retrieval_completed: '检索完成',
  grading_evidence: '正在评估证据质量',
  rewriting_query: '正在改写查询',
  generating_answer: '正在生成答案',
  citation: '整理引用来源',
}

function _agenticStageLabel(type) {
  return AGENTIC_STAGE_LABELS[type] || type
}

export function createSSEStream(url, body, handlers) {
  const controller = new AbortController()
  const isFormData = body instanceof FormData

  ;(async () => {
    try {
      const token = localStorage.getItem('jwt_token')
      const headers = {}
      if (!isFormData) {
        headers['Content-Type'] = 'application/json'
      }
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
      }

      const response = await fetch(url, {
        method: 'POST',
        headers,
        body: isFormData ? body : JSON.stringify(body),
        signal: controller.signal
      })

      if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (!data) continue

          try {
            const json = JSON.parse(data)
            switch (json.type || json.event_type) {
              case 'thinking':   handlers.onThinking?.(json); break
              case 'response':   handlers.onResponse?.(json); break
              case 'done':       handlers.onDone?.(json);     break
              case 'error':      handlers.onError?.(new Error(json.content || json.error || 'Stream error')); break
              case 'step':       handlers.onStep?.(json);     break
              // knowledge upload stream events
              case 'processing': handlers.onProcessing?.(json); break
              case 'finish':     handlers.onFinish?.(json);     break

              // Agentic RAG SSE 事件
              case 'started':
              case 'planning':
              case 'retrieving':
              case 'retrieval_completed':
              case 'grading_evidence':
              case 'rewriting_query':
              case 'generating_answer':
              case 'citation':
                // 将 Agentic 阶段事件转为 thinking 步骤展示；
                // details 合并 state 与过程数据（plan/retrieval/grading/rewrite），
                // 供 RetrievalTrace 组件渲染检索链路
                {
                  const details = {
                    ...(json.state || null),
                    ...(json.plan ? { plan: json.plan } : null),
                    ...(json.retrieval ? { retrieval: json.retrieval } : null),
                    ...(json.grading ? { grading: json.grading } : null),
                    ...(json.rewrite ? { rewrite: json.rewrite } : null),
                  }
                  handlers.onThinking?.({
                    stage: json.type,
                    content: _agenticStageLabel(json.type),
                    details: Object.keys(details).length > 0 ? details : null,
                  })
                }
                break
              case 'completed':
                // Agentic 完成事件，提取 answer 和 citations
                handlers.onResponse?.({ content: json.answer || '' })
                handlers.onCompleted?.(json)
                handlers.onDone?.({ session_id: json.session_id })
                break
            }
          } catch (e) {
            if (e.message === 'Stream error') throw e
          }
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError') {
        handlers.onError?.(error)
      }
    }
  })()

  return () => controller.abort()
}
