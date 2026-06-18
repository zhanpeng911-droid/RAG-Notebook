import { defineStore } from 'pinia'

// 支持的 AI 提供商配置
const PROVIDERS = {
  deepseek: {
    name: 'DeepSeek',
    baseUrl: 'https://api.deepseek.com',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    defaultModel: 'deepseek-chat',
    protocol: 'openai',  // 兼容 OpenAI 协议
  },
  openai: {
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
    defaultModel: 'gpt-4o-mini',
    protocol: 'openai',
  },
  anthropic: {
    name: 'Anthropic',
    baseUrl: 'https://api.anthropic.com',
    models: ['claude-sonnet-4-20250514', 'claude-haiku-4-20250514', 'claude-opus-4-20250514'],
    defaultModel: 'claude-sonnet-4-20250514',
    protocol: 'anthropic',
  },
  ollama: {
    name: 'Ollama (本地)',
    baseUrl: 'http://localhost:11434/v1',
    models: ['qwen2.5', 'llama3.1', 'mistral', 'codellama', 'deepseek-r1'],
    defaultModel: 'qwen2.5',
    protocol: 'openai',  // Ollama 兼容 OpenAI 协议
  },
  custom: {
    name: '自定义',
    baseUrl: '',
    models: [],
    defaultModel: '',
    protocol: 'openai',
  },
}

export const useModelStore = defineStore('model', {
  state: () => {
    let saved = {}
    try { saved = JSON.parse(localStorage.getItem('model-config') || '{}') } catch { saved = {} }
    return {
      provider: saved.provider || 'deepseek',
      modelName: saved.modelName || 'deepseek-chat',
      apiKey: saved.apiKey || '',
      baseUrl: saved.baseUrl || '',
      isConfigured: saved.isConfigured || false,
    }
  },

  getters: {
    // 当前提供商配置
    providerConfig: (state) => PROVIDERS[state.provider] || PROVIDERS.deepseek,

    // 显示名称（TopBar/聊天页用）
    displayName: (state) => {
      if (state.modelName) return state.modelName
      return PROVIDERS[state.provider]?.defaultModel || 'AI'
    },

    // 提供商显示名称
    providerName: (state) => {
      return PROVIDERS[state.provider]?.name || '自定义'
    },

    // 所有可用提供商列表
    providerList: () => {
      return Object.entries(PROVIDERS).map(([key, val]) => ({
        id: key,
        name: val.name,
      }))
    },

    // 当前提供商的模型列表
    modelList: (state) => {
      const provider = PROVIDERS[state.provider]
      if (!provider) return []
      return provider.models.map(m => ({ id: m, name: m }))
    },

    // 当前协议
    protocol: (state) => {
      return PROVIDERS[state.provider]?.protocol || 'openai'
    },

    // 完整配置（发送给后端用）
    config: (state) => ({
      provider: state.provider,
      model: state.modelName,
      api_key: state.apiKey,
      base_url: state.baseUrl || PROVIDERS[state.provider]?.baseUrl || '',
      protocol: PROVIDERS[state.provider]?.protocol || 'openai',
    }),
  },

  actions: {
    // 更新提供商
    setProvider(providerId) {
      this.provider = providerId
      const p = PROVIDERS[providerId]
      if (p) {
        this.modelName = p.defaultModel
        // 只在切换到非 custom 提供商时清空 baseUrl
        if (providerId !== 'custom') {
          this.baseUrl = ''
        }
        // Ollama 本地模型无需 API Key；其他 provider 仍取决于是否已有 key
        this.isConfigured = providerId === 'ollama' || !!this.apiKey
      }
      this.save()
    },

    // 更新模型名
    setModelName(name) {
      this.modelName = name
      this.save()
    },

    // 更新 API Key
    setApiKey(key) {
      this.apiKey = key
      this.isConfigured = this.provider === 'ollama' || !!key
      this.save()
    },

    // 更新自定义 Base URL
    setBaseUrl(url) {
      this.baseUrl = url
      this.save()
    },

    // 保存全部配置
    save() {
      localStorage.setItem('model-config', JSON.stringify({
        provider: this.provider,
        modelName: this.modelName,
        apiKey: this.apiKey,
        baseUrl: this.baseUrl,
        isConfigured: this.isConfigured,
      }))
    },

    // 重置配置
    reset() {
      this.provider = 'deepseek'
      this.modelName = 'deepseek-chat'
      this.apiKey = ''
      this.baseUrl = ''
      this.isConfigured = false
      this.save()
    },
  },
})
