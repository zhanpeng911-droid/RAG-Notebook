import http from './http'

export const sessionApi = {
  getUserSessions(userId) {
    return http.get(`/chat/sessions/${userId}`).then(r => r.data)
  },

  getSession(sessionId) {
    return http.get(`/chat/session/${sessionId}`).then(r => r.data)
  },

  deleteSession(sessionId) {
    return http.delete(`/chat/session/${sessionId}`).then(r => r.data)
  },
}
