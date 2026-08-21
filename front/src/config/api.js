/**
 * API配置文件
 * 包含API基础URL和所有API端点配置
 * 后端统一使用 /api/v1 前缀（API 版本管理）
 */

// API版本前缀
const API_V1 = '/api/v1'

// API基础URL配置
export const apiConfig = {
  // 后端API基础URL（使用相对路径，通过Vite代理访问）
  baseURL: import.meta.env.VITE_BASE_URL || '',
  // 用户服务基础URL（使用相对路径，通过Vite代理访问）
  userBaseURL: import.meta.env.VITE_USER_BASE_URL || '',

  // API端点配置
  endpoints: {
    // 认证相关（Django 用户服务，不走 /api/v1 前缀）
    login: '/user/login/',
    logout: '/user/logout/',
    register: '/user/register/',
    profile: '/user/detail/',

    // 文件上传（Django 用户服务）
    uploadFile: '/file/upload/',

    // AI对话相关
    agentQueryStream: `${API_V1}/chat/agent/query/stream`,

    // 会话管理
    getSession: `${API_V1}/chat/session/`,
    deleteSession: `${API_V1}/chat/session/`,
    getAllSessions: `${API_V1}/chat/sessions`,
    getUserSessions: `${API_V1}/chat/sessions`,

    // 笔记管理
    noteCreate: `${API_V1}/note/create`,
    noteUpdate: (noteId) => `${API_V1}/note/${noteId}`,
    noteDelete: (noteId) => `${API_V1}/note/${noteId}`,
    noteDetail: (noteId) => `${API_V1}/note/${noteId}`,
    noteList: `${API_V1}/note/list`,
    noteSearch: `${API_V1}/note/search`,
    noteAutoTag: (noteId) => `${API_V1}/note/${noteId}/auto-tag`,
    noteRelated: (noteId) => `${API_V1}/note/${noteId}/related`,
    noteAutocomplete: `${API_V1}/note/autocomplete`,
    noteAssistStream: `${API_V1}/note/assist/stream`,

    // 回顾提醒
    reviewToday: `${API_V1}/review/today`,
    reviewDone: (noteId) => `${API_V1}/review/done/${noteId}`,

    // 运行时配置（检索参数热更新）
    runtimeConfig: `${API_V1}/admin/runtime-config`,
    runtimeConfigReset: `${API_V1}/admin/runtime-config/reset`,
  }
}
