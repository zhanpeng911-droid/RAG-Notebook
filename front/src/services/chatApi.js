import { createSSEStream } from './sseClient'
import { apiConfig } from '../config/api'

export const chatApi = {
  queryStream({ query, sessionId, llmConfig }, handlers) {
    return createSSEStream(apiConfig.endpoints.agentQueryStream, {
      query,
      session_id: sessionId || undefined,
      llm_config: llmConfig || undefined,
    }, handlers)
  },
}
