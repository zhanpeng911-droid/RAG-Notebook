import { createSSEStream } from './sseClient'

export const chatApi = {
  queryStream({ query, sessionId, llmConfig }, handlers) {
    return createSSEStream('/chat/agent/query/stream', {
      query,
      session_id: sessionId || undefined,
      llm_config: llmConfig || undefined,
    }, handlers)
  },
}
