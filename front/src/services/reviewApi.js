import http from './http'

export const reviewApi = {
  getToday() {
    return http.get('/review/today').then(r => r.data)
  },

  markDone(noteId) {
    return http.post(`/review/done/${noteId}`).then(r => r.data)
  },

  dueCount() {
    return http.get('/review/due-count', { skipAuthRedirect: true }).then(r => r.data)
  },
}
