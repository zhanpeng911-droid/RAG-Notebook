import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useModelStore } from '../../../src/store/model'

describe('model store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('默认 DeepSeek 配置', () => {
    const store = useModelStore()
    expect(store.provider).toBe('deepseek')
    expect(store.modelName).toBe('deepseek-v4-flash')
    expect(store.displayName).toBe('deepseek-v4-flash')
    expect(store.providerName).toBe('DeepSeek')
  })

  it('providerConfig / protocol / modelList 派生正确', () => {
    const store = useModelStore()
    expect(store.providerConfig.baseUrl).toContain('api.deepseek.com')
    expect(store.protocol).toBe('openai')
    expect(store.modelList.map(m => m.id)).toContain('deepseek-v4-pro')
  })

  it('config 输出后端格式（含默认 base_url）', () => {
    const store = useModelStore()
    const cfg = store.config
    expect(cfg.provider).toBe('deepseek')
    expect(cfg.model).toBe('deepseek-v4-flash')
    expect(cfg.api_key).toBe('')
    expect(cfg.base_url).toContain('api.deepseek.com')
    expect(cfg.protocol).toBe('openai')
  })

  it('providerList 列出全部提供商', () => {
    const store = useModelStore()
    const ids = store.providerList.map(p => p.id)
    expect(ids).toContain('deepseek')
    expect(ids).toContain('ollama')
    expect(ids).toContain('custom')
  })

  it('setProvider 切到 ollama 自动配置', () => {
    const store = useModelStore()
    store.setProvider('ollama')
    expect(store.provider).toBe('ollama')
    expect(store.isConfigured).toBe(true)
    expect(localStorage.getItem('model-config')).toContain('ollama')
  })

  it('setApiKey 更新 isConfigured', () => {
    const store = useModelStore()
    store.setApiKey('sk-test')
    expect(store.apiKey).toBe('sk-test')
    expect(store.isConfigured).toBe(true)
  })

  it('setModelName / setBaseUrl 持久化', () => {
    const store = useModelStore()
    store.setModelName('custom-model')
    store.setBaseUrl('http://localhost:8080')
    expect(store.modelName).toBe('custom-model')
    expect(store.baseUrl).toBe('http://localhost:8080')
  })

  it('reset 恢复默认', () => {
    const store = useModelStore()
    store.setApiKey('sk')
    store.setModelName('x')
    store.reset()
    expect(store.provider).toBe('deepseek')
    expect(store.modelName).toBe('deepseek-v4-flash')
    expect(store.apiKey).toBe('')
    expect(store.isConfigured).toBe(false)
  })
})
