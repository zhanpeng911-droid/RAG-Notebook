/**
 * SSE stream client.
 * @param {string} url
 * @param {object|FormData} body - JSON object or FormData for file uploads
 * @param {object} handlers - { onThinking, onResponse, onDone, onError, onStep }
 * @returns {function} abort
 */
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
              case 'error':      handlers.onError?.(new Error(json.content || 'Stream error')); break
              case 'step':       handlers.onStep?.(json);     break
              // knowledge upload stream events
              case 'processing': handlers.onProcessing?.(json); break
              case 'completed':  handlers.onCompleted?.(json);  break
              case 'finish':     handlers.onFinish?.(json);     break
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
