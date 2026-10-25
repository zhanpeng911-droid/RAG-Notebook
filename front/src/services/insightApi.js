import http from './http'

const API = '/api/v1/insights'

function unwrap(response) {
  return response.data?.data ?? response.data
}

export const insightApi = {
  generateDocument(documentId, options = {}) {
    return http.post(`${API}/documents/${documentId}/generate`, {
      force: !!options.force,
      model: options.model || null,
    }).then(unwrap)
  },

  generateNote(noteId, options = {}) {
    return http.post(`${API}/notes/${noteId}/generate`, {
      force: !!options.force,
      model: options.model || null,
    }).then(unwrap)
  },

  getJob(jobId) {
    return http.get(`${API}/jobs/${jobId}`).then(unwrap)
  },

  listSources(params = {}) {
    return http.get(`${API}/sources`, { params }).then(unwrap)
  },

  recentAnalyses(limit = 20) {
    return http.get(`${API}/recent`, { params: { limit } }).then(unwrap)
  },

  getSourceState(sourceType, sourceId) {
    return http.get(`${API}/sources/${sourceType}/${sourceId}/state`).then(unwrap)
  },

  getSourceText(sourceType, sourceId) {
    return http.get(`${API}/sources/${sourceType}/${sourceId}/text`).then(unwrap)
  },

  patchMindmap(sourceType, sourceId, expectedRevision, edits) {
    return http.patch(`${API}/sources/${sourceType}/${sourceId}/mindmap`, {
      expected_revision: expectedRevision,
      edits,
    }).then(unwrap)
  },

  getDocumentInsight(documentId) {
    return http.get(`${API}/documents/${documentId}`).then(unwrap)
  },

  getDocumentMindmap(documentId) {
    return http.get(`${API}/documents/${documentId}/mindmap`).then(unwrap)
  },

  getNoteInsight(noteId) {
    return http.get(`${API}/notes/${noteId}`).then(unwrap)
  },

  getNoteMindmap(noteId) {
    return http.get(`${API}/notes/${noteId}/mindmap`).then(unwrap)
  },

  getAnchor(anchorId, quote) {
    return http.get(`${API}/anchors/${anchorId}`, {
      params: quote ? { quote } : undefined,
    }).then(unwrap)
  },

  getGraph(params = {}) {
    return http.get(`${API}/graph`, { params }).then(unwrap)
  },

  getNode(conceptId) {
    return http.get(`${API}/graph/nodes/${conceptId}`).then(unwrap)
  },

  getRelation(relationId) {
    return http.get(`${API}/graph/relations/${relationId}`).then(unwrap)
  },

  patchNode(conceptId, status) {
    return http.patch(`${API}/graph/nodes/${conceptId}`, { status }).then(unwrap)
  },

  patchRelation(relationId, verificationState) {
    return http.patch(`${API}/graph/relations/${relationId}`, {
      verification_state: verificationState,
    }).then(unwrap)
  },
}
