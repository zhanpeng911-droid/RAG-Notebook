<template>
  <div class="settings-container">
    <div class="settings-list">
      <!-- 模型配置区 -->
      <div class="model-status-section">
        <h3 class="section-title">AI 模型配置</h3>
        <div class="model-card">
          <div class="model-header">
            <span class="model-name">{{ modelStore.displayName }}</span>
            <span class="model-status-badge" :class="{ configured: modelStore.isConfigured }">
              <span class="status-dot"></span>
              {{ modelStore.isConfigured ? '已配置' : '未配置' }}
            </span>
          </div>

          <!-- 提供商选择 -->
          <div class="config-field">
            <label class="config-label">AI 提供商</label>
            <div class="provider-grid">
              <div
                v-for="p in modelStore.providerList"
                :key="p.id"
                class="provider-item"
                :class="{ active: modelStore.provider === p.id }"
                @click="modelStore.setProvider(p.id)"
              >
                <span class="provider-name">{{ p.name }}</span>
              </div>
            </div>
          </div>

          <!-- 模型选择/输入 -->
          <div class="config-field">
            <label class="config-label">模型名称</label>
            <div v-if="modelStore.modelList.length > 0" class="model-select">
              <select v-model="selectedModel" @change="onModelChange" class="config-select">
                <option v-for="m in modelStore.modelList" :key="m.id" :value="m.id">{{ m.name }}</option>
              </select>
            </div>
            <input
              v-else
              v-model="customModelName"
              type="text"
              class="config-input"
              placeholder="输入模型名称，如 gpt-4o"
              @blur="onCustomModelBlur"
            />
          </div>

          <!-- API Key -->
          <div class="config-field">
            <label class="config-label">API Key</label>
            <div class="input-with-toggle">
              <input
                v-model="apiKeyInput"
                :type="showApiKey ? 'text' : 'password'"
                class="config-input"
                :placeholder="apiKeyPlaceholder"
                @blur="onApiKeyBlur"
              />
              <button class="toggle-visibility" @click="showApiKey = !showApiKey" type="button">
                {{ showApiKey ? '隐藏' : '显示' }}
              </button>
            </div>
          </div>

          <!-- 自定义 Base URL（仅自定义提供商显示） -->
          <div v-if="modelStore.provider === 'custom'" class="config-field">
            <label class="config-label">Base URL</label>
            <input
              v-model="customBaseUrl"
              type="text"
              class="config-input"
              placeholder="https://your-api.com/v1"
              @blur="onBaseUrlBlur"
            />
          </div>

          <!-- 协议信息 -->
          <div class="config-field">
            <label class="config-label">协议</label>
            <span class="config-value">{{ modelStore.protocol === 'openai' ? 'OpenAI 兼容' : 'Anthropic' }}</span>
          </div>

          <!-- 操作按钮 -->
          <div class="config-actions">
            <van-button size="small" type="primary" @click="saveConfig">保存配置</van-button>
            <van-button size="small" plain @click="resetConfig">重置</van-button>
          </div>
        </div>
      </div>

      <!-- 个性化设置 -->
      <van-cell-group inset :title="$t('settings.personalization')">
        <van-cell :title="$t('settings.themeCustomization')" is-link @click="showThemePopup = true" />
      </van-cell-group>
    </div>

    <!-- 主题选择弹出层 -->
    <van-popup
      v-model:show="showThemePopup"
      position="bottom"
      round
      :style="{ height: '40%' }"
    >
      <div class="popup-title">{{ $t('settings.selectTheme') }}</div>
      <div class="theme-list">
        <div
          v-for="theme in themeList"
          :key="theme.id"
          class="theme-item"
          :class="{ active: currentTheme === theme.id }"
          @click="changeTheme(theme.id)"
        >
          <div class="theme-preview" :style="{ backgroundColor: theme.bgColor || '#edf6ff' }">
            <div class="theme-preview-primary" :style="{ backgroundColor: theme.primaryColor }"></div>
            <div class="theme-preview-text" :style="{ backgroundColor: '#0f1d30' }"></div>
            <div class="theme-preview-text2" :style="{ backgroundColor: '#607d9e' }"></div>
          </div>
          <div class="theme-name">{{ theme.name }}</div>
        </div>
      </div>
    </van-popup>

  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { showToast } from 'vant';
import { useThemeStore } from '../store/theme';
import { useModelStore } from '../store/model';
import { useI18n } from 'vue-i18n';

const themeStore = useThemeStore();
const modelStore = useModelStore();
const { t } = useI18n();

// ===== 模型配置 =====
const showApiKey = ref(false)
const apiKeyInput = ref(modelStore.apiKey)
const customModelName = ref(modelStore.modelName)
const customBaseUrl = ref(modelStore.baseUrl)
const selectedModel = ref(modelStore.modelName)

// 提供商切换时同步模型名
watch(() => modelStore.provider, (newProvider) => {
  selectedModel.value = modelStore.modelName
  customModelName.value = modelStore.modelName
})

const apiKeyPlaceholder = computed(() => {
  const map = {
    deepseek: '输入 DeepSeek API Key',
    openai: '输入 OpenAI API Key',
    anthropic: '输入 Anthropic API Key',
    ollama: '本地模型可留空',
    custom: '输入 API Key',
  }
  return map[modelStore.provider] || '输入 API Key'
})

function onModelChange() {
  modelStore.setModelName(selectedModel.value)
}

function onCustomModelBlur() {
  if (customModelName.value.trim()) {
    modelStore.setModelName(customModelName.value.trim())
  }
}

function onApiKeyBlur() {
  modelStore.setApiKey(apiKeyInput.value.trim())
}

function onBaseUrlBlur() {
  modelStore.setBaseUrl(customBaseUrl.value.trim())
}

function saveConfig() {
  modelStore.setApiKey(apiKeyInput.value.trim())
  modelStore.setBaseUrl(customBaseUrl.value.trim())
  if (customModelName.value.trim()) {
    modelStore.setModelName(customModelName.value.trim())
  }
  modelStore.save()
  showToast('模型配置已保存')
}

function resetConfig() {
  modelStore.reset()
  apiKeyInput.value = ''
  customModelName.value = modelStore.modelName
  customBaseUrl.value = ''
  selectedModel.value = modelStore.modelName
  showToast('已重置为默认配置')
}

// ===== 主题 =====
const showThemePopup = ref(false);
const themeList = computed(() => themeStore.getAllThemes);
const currentTheme = computed(() => themeStore.getCurrentTheme);

const changeTheme = (themeId) => {
  themeStore.setTheme(themeId);
  showToast(t('settings.themeChanged'));
  showThemePopup.value = false;
};
</script>

<style scoped>
.settings-container {
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);
  padding-top: 46px;
  padding-bottom: 20px;
}

.settings-list {
  margin-top: 20px;
  padding: 0 16px;
}

/* 模型配置区 */
.model-status-section {
  margin-bottom: 24px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-light);
  margin: 0 0 12px;
}

.model-card {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: 16px;
  border: 1px solid var(--color-border-light);
}

.model-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.model-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}

.model-status-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--status-error-bg);
  color: var(--status-error-text);
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
}

.model-status-badge.configured {
  background: var(--status-success-bg);
  color: var(--status-success-text);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

/* 配置字段 */
.config-field {
  margin-bottom: 14px;
}

.config-label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-light);
  margin-bottom: 6px;
}

.config-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-surface);
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.config-input:focus {
  border-color: var(--color-primary);
}

.config-select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-surface);
  outline: none;
  box-sizing: border-box;
}

.config-value {
  font-size: 13px;
  color: var(--color-text-light);
  padding: 10px 0;
  display: block;
}

/* 提供商选择网格 */
.provider-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
}

.provider-item {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  text-align: center;
  font-size: 13px;
  color: var(--color-text-light);
  cursor: pointer;
  transition: all 0.15s;
  background: var(--color-surface);
}

.provider-item:active {
  transform: scale(0.97);
}

.provider-item.active {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-weight: 500;
}

/* API Key 输入 */
.input-with-toggle {
  display: flex;
  gap: 8px;
  align-items: center;
}

.input-with-toggle .config-input {
  flex: 1;
}

.toggle-visibility {
  flex-shrink: 0;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  color: var(--color-text-light);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

/* 操作按钮 */
.config-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

/* 个性化设置 */
.popup-title {
  text-align: center;
  padding: 16px;
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-heading);
  border-bottom: 1px solid var(--color-divider);
  color: var(--color-text);
}

.theme-list {
  display: flex;
  flex-wrap: wrap;
  padding: 20px 16px;
  gap: 12px;
  justify-content: center;
}

.theme-item {
  width: 40%;
  max-width: 140px;
  display: flex;
  flex-direction: column;
  align-items: center;
  cursor: pointer;
  padding: 12px 8px;
  border-radius: var(--radius-md);
  transition: background 0.2s;
}

.theme-item:active {
  background: var(--color-surface);
}

.theme-item.active {
  background: var(--color-surface);
  box-shadow: 0 0 0 2px var(--color-primary);
}

.theme-preview {
  width: 72px;
  height: 48px;
  border-radius: var(--radius-md);
  margin-bottom: 8px;
  padding: 8px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-content: flex-start;
  overflow: hidden;
  box-shadow: 0 1px 3px var(--color-shadow);
}

.theme-preview-primary {
  width: 100%;
  height: 6px;
  border-radius: 2px;
}

.theme-preview-text {
  width: 60%;
  height: 4px;
  border-radius: 2px;
  opacity: 0.6;
}

.theme-preview-text2 {
  width: 40%;
  height: 4px;
  border-radius: 2px;
  opacity: 0.3;
}

.theme-name {
  font-size: 12px;
  color: var(--color-text-light);
}

</style>
