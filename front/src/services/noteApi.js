import http from './http'

export const noteApi = {
  create(data) {
    return http.post('/note/create', data).then(r => r.data)
  },

  update(noteId, data) {
    return http.put(`/note/${noteId}`, data).then(r => r.data)
  },

  delete(noteId) {
    return http.delete(`/note/${noteId}`).then(r => r.data)
  },

  getDetail(noteId) {
    return http.get(`/note/${noteId}`).then(r => r.data)
  },

  getList(params) {
    return http.get('/note/list', { params }).then(r => r.data)
  },

  search(query) {
    return http.get('/note/search', { params: { q: query } }).then(r => r.data)
  },

  autocomplete(context, llmConfig) {
    return http.post('/note/autocomplete', { context, llm_config: llmConfig }).then(r => r.data)
  },

  getRelatedNotes(query, topK = 5) {
    return http.get('/note/related', { params: { q: query, top_k: topK } }).then(r => r.data)
  },

  getRelated(noteId) {
    return http.get(`/note/${noteId}/related`).then(r => r.data)
  },
}
