import http from './http'
import { apiConfig } from '../config/api'

export const sessionApi = {
  getUserSessions(userId) {
    return http.get(`${apiConfig.endpoints.getUserSessions}/${userId}`).then(r => r.data)
  },

  getSession(sessionId) {
    return http.get(`${apiConfig.endpoints.getSession}${sessionId}`).then(r => r.data)
  },

  deleteSession(sessionId) {
    return http.delete(`${apiConfig.endpoints.deleteSession}${sessionId}`).then(r => r.data)
  },
}
