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

      <!-- 检索参数（运行时热更新） -->
      <div class="model-status-section">
        <h3 class="section-title">检索参数</h3>
        <div class="model-card">
          <p class="retrieval-params-hint">
            调整 Agentic RAG 检索链路参数，保存后即时生效（无需重启服务）。
            建议配合后端 IR 评测（run_ir_eval）验证调参收益。
          </p>

          <div v-if="retrievalParamsLoading" class="retrieval-params-loading">加载中...</div>
          <template v-else>
            <div v-for="param in retrievalParams" :key="param.key" class="config-field">
              <label class="config-label">
                {{ param.key }}
                <span v-if="param.overridden" class="param-overridden-badge">已覆盖</span>
              </label>
              <p class="param-description">{{ param.description }}</p>

              <div v-if="param.value_type === 'bool'" class="param-bool-row">
                <van-switch :model-value="param.editValue" size="20px" @update:model-value="param.editValue = $event" />
              </div>
              <input
                v-else
                v-model.number="param.editValue"
                type="number"
                class="config-input"
                :min="param.min_value"
                :max="param.max_value"
                :step="param.value_type === 'float' ? 0.05 : 1"
              />
              <p class="param-range">
                默认 {{ param.default }}
                <template v-if="param.min_value != null"> · 范围 [{{ param.min_value }}, {{ param.max_value }}]</template>
              </p>
            </div>

            <div class="config-actions">
              <van-button size="small" type="primary" :disabled="!hasParamChanges || savingParams" @click="saveRetrievalParams">
                {{ savingParams ? '保存中...' : '保存修改' }}
              </van-button>
              <van-button size="small" plain :disabled="resettingParams" @click="resetRetrievalParams">
                {{ resettingParams ? '重置中...' : '恢复默认' }}
              </van-button>
            </div>
          </template>
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
import { ref, computed, watch, onMounted } from 'vue';
import { showToast } from 'vant';
import { useThemeStore } from '../store/theme';
import { useModelStore } from '../store/model';
import { useI18n } from 'vue-i18n';
import http from '../services/http';
import { apiConfig } from '../config/api';

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

// ===== 检索参数（运行时热更新） =====
const retrievalParams = ref([]);
const retrievalParamsLoading = ref(false);
const savingParams = ref(false);
const resettingParams = ref(false);

const hasParamChanges = computed(() =>
  retrievalParams.value.some((p) => p.editValue !== p.value)
);

async function loadRetrievalParams() {
  retrievalParamsLoading.value = true;
  try {
    // skipAuthRedirect：未登录/测试环境下静默失败，不触发 401 跳转登录页
    const res = await http.get(apiConfig.endpoints.runtimeConfig, { skipAuthRedirect: true });
    if (res.data?.code === 200 && res.data?.data?.params) {
      retrievalParams.value = res.data.data.params.map((p) => ({
        ...p,
        editValue: p.value,
      }));
    }
  } catch (e) {
    // 静默降级：参数区显示为空（不影响页面其他功能）
    console.debug('检索参数加载失败（可能未登录）:', e?.response?.status || e?.message);
  } finally {
    retrievalParamsLoading.value = false;
  }
}

async function saveRetrievalParams() {
  const changed = {};
  for (const p of retrievalParams.value) {
    if (p.editValue !== p.value) {
      changed[p.key] = p.editValue;
    }
  }
  if (Object.keys(changed).length === 0) return;

  savingParams.value = true;
  try {
    const res = await http.put(apiConfig.endpoints.runtimeConfig, { values: changed });
    if (res.data?.code === 200) {
      showToast('检索参数已生效');
      await loadRetrievalParams();
    } else {
      showToast(res.data?.message || '保存失败');
    }
  } catch (e) {
    const detail = e.response?.data?.detail;
    showToast(detail || '保存失败');
  } finally {
    savingParams.value = false;
  }
}

async function resetRetrievalParams() {
  resettingParams.value = true;
  try {
    const res = await http.post(apiConfig.endpoints.runtimeConfigReset, { keys: [] });
    if (res.data?.code === 200) {
      showToast('已恢复默认值');
      await loadRetrievalParams();
    } else {
      showToast(res.data?.message || '重置失败');
    }
  } catch (e) {
    showToast('重置失败');
  } finally {
    resettingParams.value = false;
  }
}

onMounted(() => {
  loadRetrievalParams();
});

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
/* ===== 检索参数卡片 ===== */
.retrieval-params-hint {
  font-size: 12px;
  color: var(--color-text-lighter);
  margin: 0 0 12px;
  line-height: 1.5;
}

.retrieval-params-loading {
  padding: 16px 0;
  text-align: center;
  color: var(--color-text-lighter);
  font-size: 13px;
}

.param-description {
  font-size: 11px;
  color: var(--color-text-lightest);
  margin: 2px 0 6px;
  line-height: 1.4;
}

.param-range {
  font-size: 11px;
  color: var(--color-text-lightest);
  margin: 4px 0 0;
}

.param-overridden-badge {
  display: inline-block;
  font-size: 10px;
  color: var(--color-primary);
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-full);
  padding: 0 6px;
  margin-left: 6px;
  vertical-align: middle;
}

.param-bool-row {
  display: flex;
  align-items: center;
  padding: 4px 0;
}

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
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-radius: var(--radius-lg);
  padding: 16px;
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
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
