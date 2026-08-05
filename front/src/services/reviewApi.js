import http from './http'
import { apiConfig } from '../config/api'

export const reviewApi = {
  getToday() {
    return http.get(apiConfig.endpoints.reviewToday).then(r => r.data)
  },

  markDone(noteId) {
    return http.post(apiConfig.endpoints.reviewDone(noteId)).then(r => r.data)
  },

  dueCount() {
    return http.get('/api/v1/review/due-count', { skipAuthRedirect: true }).then(r => r.data)
  },
}
