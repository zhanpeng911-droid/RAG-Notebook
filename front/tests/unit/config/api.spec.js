import { describe, it, expect } from 'vitest'
import { apiConfig } from '../../../src/config/api'
import { features, isOrgFeatureEnabled } from '../../../src/config/features'

describe('api config', () => {
  it('端点均使用 /api/v1 前缀（认证/文件除外）', () => {
    expect(apiConfig.endpoints.agentQueryStream).toBe('/api/v1/chat/agent/query/stream')
    expect(apiConfig.endpoints.noteCreate).toBe('/api/v1/note/create')
    expect(apiConfig.endpoints.getUserSessions).toBe('/api/v1/chat/sessions')
  })

  it('认证与文件端点不带 /api/v1', () => {
    expect(apiConfig.endpoints.login).toBe('/user/login/')
    expect(apiConfig.endpoints.uploadFile).toBe('/file/upload/')
  })

  it('动态端点函数返回带 id 的 URL', () => {
    expect(apiConfig.endpoints.noteUpdate('n1')).toBe('/api/v1/note/n1')
    expect(apiConfig.endpoints.noteDelete('n1')).toBe('/api/v1/note/n1')
    expect(apiConfig.endpoints.noteDetail('n1')).toBe('/api/v1/note/n1')
    expect(apiConfig.endpoints.noteAutoTag('n1')).toBe('/api/v1/note/n1/auto-tag')
    expect(apiConfig.endpoints.reviewDone('n1')).toBe('/api/v1/review/done/n1')
  })

  it('运行时配置端点', () => {
    expect(apiConfig.endpoints.runtimeConfig).toBe('/api/v1/admin/runtime-config')
    expect(apiConfig.endpoints.runtimeConfigReset).toBe('/api/v1/admin/runtime-config/reset')
  })

  it('baseURL 默认为空（走 Vite 代理）', () => {
    expect(apiConfig.baseURL).toBe('')
    expect(apiConfig.userBaseURL).toBe('')
  })
})

describe('feature flags', () => {
  it('org 功能默认开启', () => {
    expect(features.org).toBe(true)
    expect(isOrgFeatureEnabled()).toBe(true)
  })
})
