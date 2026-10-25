import http from './http'

const API = '/api/v1/notes'

function unwrap(response) {
  return response.data?.data ?? response.data
}

export const noteGraphApi = {
  graph(params = {}) {
    return http.get(`${API}/graph`, { params }).then(unwrap)
  },

  localGraph(noteId, depth = 1) {
    return http.get(`${API}/${noteId}/local-graph`, {
      params: { depth, limit: 200 },
    }).then(unwrap)
  },

  links(noteId) {
    return http.get(`${API}/${noteId}/links`).then(unwrap)
  },

  candidates(q = '', excludeNoteId) {
    return http.get(`${API}/link-candidates`, {
      params: { q, limit: 10, exclude_note_id: excludeNoteId || undefined },
    }).then(unwrap)
  },

  addManual(noteId, targetNoteId, reason) {
    return http.post(`${API}/${noteId}/manual-relations`, {
      target_note_id: targetNoteId,
      reason: reason || null,
    }).then(unwrap)
  },

  removeManual(noteId, relationId) {
    return http.delete(`${API}/${noteId}/manual-relations/${relationId}`).then(unwrap)
  },
}
