import http from './http'
import { apiConfig } from '../config/api'

export const noteApi = {
  create(data) {
    return http.post(apiConfig.endpoints.noteCreate, data).then(r => r.data)
  },

  update(noteId, data) {
    return http.put(apiConfig.endpoints.noteUpdate(noteId), data).then(r => r.data)
  },

  delete(noteId) {
    return http.delete(apiConfig.endpoints.noteDelete(noteId)).then(r => r.data)
  },

  getDetail(noteId) {
    return http.get(apiConfig.endpoints.noteDetail(noteId)).then(r => r.data)
  },

  getList(params) {
    return http.get(apiConfig.endpoints.noteList, { params }).then(r => r.data)
  },

  search(query) {
    return http.get(apiConfig.endpoints.noteSearch, { params: { q: query } }).then(r => r.data)
  },

  autocomplete(context, llmConfig) {
    return http.post(apiConfig.endpoints.noteAutocomplete, { context, llm_config: llmConfig }).then(r => r.data)
  },

  getRelatedNotes(query, topK = 5) {
    return http.get('/api/v1/note/related', { params: { q: query, top_k: topK } }).then(r => r.data)
  },

  getRelated(noteId) {
    return http.get(apiConfig.endpoints.noteRelated(noteId)).then(r => r.data)
  },
}
