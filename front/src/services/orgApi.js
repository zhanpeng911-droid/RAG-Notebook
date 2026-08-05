import http from './http'

const API = '/api/v1'

export const orgApi = {
  // 组织
  createOrg(data) { return http.post(`${API}/org/create`, data).then(r => r.data) },
  listOrgs() { return http.get(`${API}/org/list`).then(r => r.data) },
  getOrg(orgId) { return http.get(`${API}/org/${orgId}`).then(r => r.data) },
  updateOrg(orgId, data) { return http.put(`${API}/org/${orgId}`, data).then(r => r.data) },
  deleteOrg(orgId) { return http.delete(`${API}/org/${orgId}`).then(r => r.data) },

  // 成员
  inviteMember(orgId, data) { return http.post(`${API}/org/${orgId}/invite`, data).then(r => r.data) },
  removeMember(orgId, userId) { return http.delete(`${API}/org/${orgId}/member/${userId}`).then(r => r.data) },
  updateRole(orgId, userId, role) { return http.put(`${API}/org/${orgId}/member/${userId}/role`, { role }).then(r => r.data) },
  listMembers(orgId) { return http.get(`${API}/org/${orgId}/members`).then(r => r.data) },

  // 空间
  createSpace(data) { return http.post(`${API}/space/create`, data).then(r => r.data) },
  listSpaces(orgId) { return http.get(`${API}/space/list`, { params: { org_id: orgId } }).then(r => r.data) },
  getSpace(spaceId) { return http.get(`${API}/space/${spaceId}`).then(r => r.data) },
  updateSpace(spaceId, data) { return http.put(`${API}/space/${spaceId}`, data).then(r => r.data) },
  deleteSpace(spaceId) { return http.delete(`${API}/space/${spaceId}`).then(r => r.data) },
  listSpaceDocuments(spaceId) { return http.get(`${API}/space/${spaceId}/documents`).then(r => r.data) },
  listAvailableNotes(spaceId) { return http.get(`${API}/space/${spaceId}/available-notes`).then(r => r.data) },
  addNoteToSpace(spaceId, noteId) { return http.post(`${API}/space/${spaceId}/documents/note/${noteId}`).then(r => r.data) },
  removeSpaceDocument(spaceId, spaceDocumentId) { return http.delete(`${API}/space/${spaceId}/documents/${spaceDocumentId}`).then(r => r.data) },

  // 审计日志
  getAuditLogs(params) { return http.get(`${API}/audit/logs`, { params }).then(r => r.data) },
  getAuditStats(orgId) { return http.get(`${API}/audit/stats`, { params: { org_id: orgId } }).then(r => r.data) },
}
