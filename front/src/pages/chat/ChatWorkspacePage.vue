<template>
  <div class="chat-workspace">
    <!-- 左栏：会话列表 (260px) -->
    <aside class="session-panel" :class="{ collapsed: sessionPanelCollapsed }">
      <div class="session-panel-header">
        <h3 v-if="!sessionPanelCollapsed">会话</h3>
        <button class="btn-icon" @click="createNewSession" title="新建对话">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"/>
            <line x1="5" y1="12" x2="19" y2="12"/>
          </svg>
        </button>
      </div>

      <div v-if="!sessionPanelCollapsed" class="session-search">
        <input
          v-model="sessionSearchQuery"
          type="text"
          class="search-input"
          placeholder="搜索会话..."
        />
      </div>

      <div class="session-list" v-if="!sessionPanelCollapsed">
        <div v-if="isSessionsLoading" class="session-loading">
          <div class="spinner"></div>
        </div>
        <div v-else-if="filteredSessions.length === 0" class="session-empty">
          <p>暂无会话</p>
          <p class="session-empty-hint">点击上方按钮开始新对话</p>
        </div>
        <div
          v-for="session in filteredSessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: currentSessionId === session.session_id }"
          @click="selectSession(session)"
        >
          <div class="session-item-content">
            <div class="session-title ellipsis">{{ session.title || '新会话' }}</div>
            <div class="session-time">{{ formatSessionTime(session.updated_at || session.created_at) }}</div>
          </div>
          <button class="session-delete" @click.stop="deleteSession(session.session_id)" title="删除会话">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <!-- 中栏：聊天区 -->
    <main class="chat-main">
      <div class="messages-container" ref="messagesContainer">
        <!-- 欢迎状态 -->
        <div v-if="showWelcome" class="welcome-card">
          <div class="welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 15a7 7 0 0 1 7-7h.5"/>
              <path d="M21 9a7 7 0 0 1-7 7h-.5"/>
              <circle cx="8" cy="12" r="1" fill="currentColor" stroke="none"/>
              <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>
              <circle cx="16" cy="12" r="1" fill="currentColor" stroke="none"/>
              <path d="M3 5l2.5 2L3 9" opacity="0.5"/>
              <path d="M21 15l-2.5 2L21 19" opacity="0.5"/>
            </svg>
          </div>
          <h3 class="welcome-title">智能笔记助手</h3>
          <p class="welcome-desc">基于你的笔记和知识库的智能助手。帮你整理思路、优化内容、随时问答。</p>
          <div class="model-badge">
            <span class="model-dot"></span>
            <span>当前模型：{{ modelStore.displayName }}</span>
          </div>
          <div class="welcome-questions">
            <button
              v-for="(q, i) in quickQuestions"
              :key="i"
              class="quick-question"
              @click="sendQuickQuestion(q)"
            >
              {{ q }}
            </button>
          </div>
        </div>

        <!-- 消息列表 -->
        <div
          v-for="(message, index) in messages"
          v-show="!showWelcome || message.role === 'user' || index > 0"
          :key="index"
          :class="['message', message.role === 'user' ? 'user-message' : 'ai-message']"
        >
          <div class="message-avatar">
            <template v-if="message.role === 'user'">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
            </template>
            <template v-else>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2l1 4.5L17.5 8l-4.5 1.5L12 14l-1-4.5L6.5 8 11 6.5z"/>
              </svg>
            </template>
          </div>
          <div class="message-body">
            <!-- 思考过程 -->
            <div v-if="message.thinking && message.thinking.length > 0" class="thinking-section">
              <div class="thinking-header" @click="toggleThinking(message)">
                <span class="thinking-label">思考过程</span>
                <span class="thinking-toggle">{{ message.thinkingCollapsed ? '展开' : '收起' }}</span>
              </div>
              <div v-show="!message.thinkingCollapsed" class="thinking-body">
                <div v-for="(step, sIndex) in message.thinking" :key="sIndex" class="thinking-step">
                  <span class="thinking-stage-label" :style="{ backgroundColor: getStageColor(step.stage) }">
                    {{ getStageLabel(step.stage) }}
                  </span>
                  <span class="thinking-step-content">{{ step.content }}</span>
                  <div v-if="step.details" class="thinking-details">
                    <template v-if="step.details.documents">
                      <div v-for="(doc, dIndex) in step.details.documents.slice(0, 3)" :key="dIndex" class="thinking-doc-item">
                        <span class="thinking-doc-source">{{ doc.source }}</span>
                        <span class="thinking-doc-score">{{ (doc.score * 100).toFixed(0) }}%</span>
                      </div>
                      <div v-if="step.details.documents.length > 3" class="thinking-doc-more">
                        ... 还有 {{ step.details.documents.length - 3 }} 个文档
                      </div>
                    </template>
                    <template v-else-if="step.details.scores">
                      <div v-for="(sc, cIndex) in step.details.scores.slice(0, 3)" :key="cIndex" class="thinking-score-item">
                        <span>#{{ sc.rank || sc.index }}</span>
                        <span>{{ (sc.score * 100).toFixed(0) }}%</span>
                        <span class="thinking-score-preview">{{ truncateText(sc.preview, 40) }}</span>
                      </div>
                    </template>
                    <template v-else-if="step.details.hypothetical_doc_preview">
                      <div class="thinking-detail-text">{{ truncateText(step.details.hypothetical_doc_preview, 80) }}</div>
                    </template>
                    <template v-else>
                      <div v-for="(val, key) in step.details" :key="key" class="thinking-detail-kv">
                        <span class="thinking-detail-key">{{ key }}:</span>
                        <span class="thinking-detail-val">{{ typeof val === 'object' ? JSON.stringify(val) : val }}</span>
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>
            <!-- 回复正文 -->
            <div v-if="message.content" class="message-content" v-html="formatMessage(message.content)"></div>
            <!-- 打字指示器 -->
            <div v-if="message.role === 'assistant' && !message.content && (!message.thinking || message.thinking.length === 0)" class="typing-indicator">
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        </div>
      </div>

      <!-- 输入区 -->
      <div class="input-container">
        <div class="input-wrapper">
          <textarea
            v-model="userInput"
            rows="1"
            class="chat-input"
            placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
            @keydown="handleKeydown"
            :disabled="isLoading"
          ></textarea>
          <button
            class="send-button"
            :disabled="isLoading || !userInput.trim()"
            @click="sendMessage"
          >
            <svg v-if="!isLoading" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"/>
              <polygon points="22 2 15 22 11 13 2 9 22 2"/>
            </svg>
            <div v-else class="spinner-small"></div>
          </button>
        </div>
      </div>
    </main>

    <!-- 右栏：引用来源/思考时间线 (320px, 可折叠) -->
    <aside class="reference-panel" :class="{ collapsed: referencePanelCollapsed }">
      <button class="panel-toggle" @click="referencePanelCollapsed = !referencePanelCollapsed" :title="referencePanelCollapsed ? '展开面板' : '折叠面板'">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline :points="referencePanelCollapsed ? '9 18 15 12 9 6' : '15 18 9 12 15 6'"/>
        </svg>
      </button>

      <div v-if="!referencePanelCollapsed" class="reference-content">
        <div class="reference-tabs">
          <button
            v-for="tab in referenceTabs"
            :key="tab.key"
            class="tab-item"
            :class="{ active: activeReferenceTab === tab.key }"
            @click="activeReferenceTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>

        <div class="reference-body">
          <!-- 引用来源 Tab -->
          <div v-if="activeReferenceTab === 'references'" class="reference-list">
            <div v-if="currentThinkingSteps.length === 0" class="reference-empty">
              <p>发送消息后，引用来源将在此显示</p>
            </div>
            <template v-else>
              <div v-for="(step, idx) in referenceDocuments" :key="idx" class="reference-item">
                <div class="reference-source">{{ step.source || '未知来源' }}</div>
                <div class="reference-score" v-if="step.score != null && !isNaN(step.score)">{{ (step.score * 100).toFixed(0) }}% 相关</div>
                <div class="reference-score" v-else>引用</div>
              </div>
            </template>
          </div>

          <!-- 检索过程 Tab -->
          <div v-if="activeReferenceTab === 'retrieval'" class="reference-list">
            <div v-if="currentThinkingSteps.length === 0" class="reference-empty">
              <p>发送消息后，检索过程将在此显示</p>
            </div>
            <template v-else>
              <div v-for="(step, idx) in currentThinkingSteps" :key="idx" class="thinking-timeline-item">
                <div class="timeline-dot" :style="{ backgroundColor: getStageColor(step.stage) }"></div>
                <div class="timeline-content">
                  <div class="timeline-stage">{{ getStageLabel(step.stage) }}</div>
                  <div class="timeline-text">{{ step.content }}</div>
                </div>
              </div>
            </template>
          </div>

          <!-- 相关笔记 Tab -->
          <div v-if="activeReferenceTab === 'notes'" class="reference-list">
            <div v-if="relatedLoading" class="reference-loading">
              <div class="spinner-small"></div>
              <p>检索相关笔记中...</p>
            </div>
            <div v-else-if="relatedNotes.length === 0" class="reference-empty">
              <p>{{ relatedQuery ? '未找到相关笔记' : '发送消息后自动检索相关笔记' }}</p>
            </div>
            <template v-else>
              <div
                v-for="note in relatedNotes"
                :key="note.id || note.note_id"
                class="related-note-item"
                @click="goToNote(note.id || note.note_id)"
              >
                <div class="related-note-header">
                  <span class="related-note-title ellipsis">{{ note.title || '无标题' }}</span>
                  <span v-if="note.similarity != null && !isNaN(note.similarity)" class="related-note-score">
                    {{ (note.similarity * 100).toFixed(0) }}% 相关
                  </span>
                </div>
                <p class="related-note-preview">{{ truncatePreview(note.content_preview || note.content || note.summary) }}</p>
              </div>
            </template>
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup>
/**
 * ChatWorkspacePage — AI 对话工作区（三栏布局）
 * 逻辑已抽离到 composables/useChatWorkspace.js
 */
import 'highlight.js/styles/github.css'
import 'highlight.js/lib/common'
import { useChatWorkspace } from '../../composables/useChatWorkspace'

defineOptions({ name: 'AIChat' })

const {
  modelStore,
  messages,
  userInput,
  messagesContainer,
  isLoading,
  isSessionsLoading,
  sessionPanelCollapsed,
  referencePanelCollapsed,
  sessionSearchQuery,
  activeReferenceTab,
  relatedNotes,
  relatedLoading,
  showWelcome,
  currentSessionId,
  filteredSessions,
  currentThinkingSteps,
  referenceDocuments,
  quickQuestions,
  referenceTabs,
  getStageLabel,
  getStageColor,
  truncateText,
  formatMessage,
  toggleThinking,
  formatSessionTime,
  selectSession,
  deleteSession,
  createNewSession,
  sendQuickQuestion,
  handleKeydown,
  sendMessage,
  truncatePreview,
  goToNote,
} = useChatWorkspace()
</script>
<style scoped>
.chat-workspace {
  display: flex;
  height: 100%;
  width: 100%;
  background: var(--color-bg);
  overflow: hidden;
}

/* ===== 左栏：会话列表 ===== */
.session-panel {
  width: 260px;
  background: var(--color-card);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.2s ease;
}

.session-panel.collapsed {
  width: 48px;
}

.session-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
  min-height: 48px;
}

.session-panel-header h3 {
  font-size: 14px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
}

.btn-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  background: var(--color-primary);
  color: white;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s ease;
}

.btn-icon:hover {
  background: var(--color-primary-hover);
}

.session-search {
  padding: var(--space-sm) var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
}

.search-input {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  background: var(--color-surface);
  color: var(--color-text);
  outline: none;
  transition: border-color 0.15s ease;
}

.search-input:focus {
  border-color: var(--color-primary);
}

.search-input::placeholder {
  color: var(--color-text-lightest);
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-sm);
}

.session-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-xl);
}

.session-empty {
  text-align: center;
  padding: var(--space-xl);
  color: var(--color-text-lighter);
  font-size: 13px;
}

.session-empty-hint {
  color: var(--color-text-lightest);
  font-size: 12px;
  margin-top: var(--space-xs);
}

.session-item {
  display: flex;
  align-items: center;
  padding: var(--space-sm) var(--space-md);
  margin-bottom: 2px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.15s ease;
}

.session-item:hover {
  background: var(--color-surface);
}

.session-item.active {
  background: var(--color-primary-light);
}

.session-item-content {
  flex: 1;
  min-width: 0;
}

.session-title {
  font-size: 13px;
  color: var(--color-text);
  font-weight: 500;
}

.session-time {
  font-size: 11px;
  color: var(--color-text-lightest);
  margin-top: 2px;
}

.session-delete {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  background: transparent;
  color: var(--color-text-lightest);
  cursor: pointer;
  border-radius: var(--radius-sm);
  opacity: 0;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.session-item:hover .session-delete {
  opacity: 1;
}

.session-delete:hover {
  background: var(--color-error);
  color: white;
}

/* ===== 中栏：聊天区 ===== */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.messages-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-lg);
}

/* 欢迎卡片 */
.welcome-card {
  text-align: center;
  padding: 60px 40px 40px;
  animation: fadeIn 0.5s ease-out;
}

.welcome-icon {
  color: var(--color-primary);
  margin-bottom: var(--space-md);
  opacity: 0.8;
}

.welcome-title {
  font-family: var(--font-heading);
  font-size: 24px;
  color: var(--color-text);
  margin: 0 0 var(--space-sm);
  font-weight: 600;
}

.welcome-desc {
  font-size: 14px;
  color: var(--color-text-light);
  line-height: 1.6;
  margin: 0 0 var(--space-lg);
}

.model-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-radius: var(--radius-full);
  font-size: 13px;
  font-weight: 500;
  margin-bottom: var(--space-xl);
}

.model-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}

.welcome-questions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-sm);
  max-width: 480px;
  margin: 0 auto;
}

.quick-question {
  all: unset;
  display: inline-block;
  font-size: 13px;
  color: var(--color-text-light);
  background: var(--color-card);
  padding: var(--space-sm) var(--space-lg);
  border-radius: var(--radius-full);
  cursor: pointer;
  box-shadow: 0 1px 3px var(--color-shadow);
  border: 1px solid var(--color-border-light);
  transition: all 0.15s ease;
  line-height: 1.4;
  font-family: var(--font-body);
}

.quick-question:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

/* 消息 */
.message {
  display: flex;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
  max-width: 85%;
  animation: fadeIn 0.3s ease-out;
}

.user-message {
  margin-left: auto;
  flex-direction: row-reverse;
}

.ai-message {
  margin-right: auto;
}

.message-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.user-message .message-avatar {
  background: var(--color-primary);
  color: white;
}

.ai-message .message-avatar {
  background: var(--color-surface);
  color: var(--color-primary);
}

.message-body {
  flex: 1;
  min-width: 0;
}

.message-content {
  padding: var(--space-md) var(--space-lg);
  border-radius: var(--radius-lg);
  word-break: break-word;
  line-height: 1.7;
  font-size: 14px;
}

.user-message .message-content {
  background: var(--color-primary);
  color: white;
  border-bottom-right-radius: var(--radius-sm);
}

.ai-message .message-content {
  background: var(--color-card);
  color: var(--color-text);
  border-bottom-left-radius: var(--radius-sm);
  box-shadow: 0 1px 3px var(--color-shadow);
}

/* 输入区 */
.input-container {
  flex-shrink: 0;
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--color-border-light);
  background: var(--color-card);
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: var(--space-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-sm) var(--space-sm) var(--space-sm) var(--space-md);
  transition: border-color 0.15s ease;
}

.input-wrapper:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}

.chat-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 14px;
  color: var(--color-text);
  resize: none;
  outline: none;
  font-family: var(--font-body);
  line-height: 1.5;
  min-height: 24px;
  max-height: 120px;
}

.chat-input::placeholder {
  color: var(--color-text-lightest);
}

.chat-input:disabled {
  opacity: 0.6;
}

.send-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: var(--color-primary);
  color: white;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.send-button:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.send-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== 右栏：引用来源 ===== */
.reference-panel {
  width: 320px;
  background: var(--color-card);
  border-left: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  position: relative;
  transition: width 0.2s ease;
}

.reference-panel.collapsed {
  width: 32px;
}

.panel-toggle {
  position: absolute;
  top: var(--space-md);
  left: -12px;
  width: 24px;
  height: 24px;
  border: 1px solid var(--color-border);
  background: var(--color-card);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 10;
  color: var(--color-text-lighter);
  transition: all 0.15s ease;
}

.panel-toggle:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

.reference-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.reference-tabs {
  display: flex;
  border-bottom: 1px solid var(--color-border-light);
  padding: 0 var(--space-sm);
}

.tab-item {
  flex: 1;
  padding: var(--space-md) var(--space-sm);
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--color-text-lighter);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s ease;
}

.tab-item:hover {
  color: var(--color-text);
}

.tab-item.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 500;
}

.reference-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-md);
}

.reference-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.reference-empty {
  text-align: center;
  padding: var(--space-xl);
  color: var(--color-text-lightest);
  font-size: 13px;
}

.reference-loading {
  text-align: center;
  padding: var(--space-xl);
  color: var(--color-text-lighter);
  font-size: 13px;
}

.reference-loading .spinner-small {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  margin: 0 auto var(--space-sm);
}

.related-note-item {
  padding: var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: background 0.15s ease;
}

.related-note-item:last-child {
  border-bottom: none;
}

.related-note-item:hover {
  background: var(--color-surface);
}

.related-note-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
}

.related-note-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
}

.related-note-score {
  font-size: 11px;
  color: var(--color-primary);
  font-weight: 500;
  white-space: nowrap;
}

.related-note-preview {
  font-size: 12px;
  color: var(--color-text-lighter);
  line-height: 1.5;
  margin: 0;
}

.reference-item {
  padding: var(--space-sm) var(--space-md);
  background: var(--color-surface);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
}

.reference-source {
  font-size: 13px;
  color: var(--color-text);
  font-weight: 500;
  margin-bottom: 2px;
}

.reference-score {
  font-size: 11px;
  color: var(--color-primary);
}

/* 思考时间线 */
.thinking-timeline-item {
  display: flex;
  gap: var(--space-sm);
  padding: var(--space-sm) 0;
  border-bottom: 1px solid var(--color-border-light);
}

.thinking-timeline-item:last-child {
  border-bottom: none;
}

.timeline-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
}

.timeline-content {
  flex: 1;
}

.timeline-stage {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-light);
  margin-bottom: 2px;
}

.timeline-text {
  font-size: 12px;
  color: var(--color-text-lighter);
  line-height: 1.4;
}

/* ===== 思考过程（消息内） ===== */
.thinking-section {
  margin-bottom: var(--space-sm);
  border-left: 3px solid rgba(63, 140, 255, 0.25);
  background-color: var(--color-surface);
  border-radius: var(--radius-md);
  padding: var(--space-sm) var(--space-md);
  font-size: 12px;
}

.thinking-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  user-select: none;
}

.thinking-label {
  color: var(--color-text-lighter);
  font-weight: 500;
}

.thinking-toggle {
  color: var(--color-text-lightest);
  font-size: 11px;
}

.thinking-body {
  margin-top: var(--space-sm);
}

.thinking-step {
  padding: var(--space-xs) 0;
  border-bottom: 1px solid var(--color-border-light);
  line-height: 1.4;
}

.thinking-step:last-child {
  border-bottom: none;
}

.thinking-stage-label {
  display: inline-block;
  font-size: 10px;
  color: #fff;
  padding: 2px 7px;
  border-radius: 3px;
  margin-right: 5px;
  vertical-align: middle;
  line-height: 1.5;
}

.thinking-step-content {
  color: var(--color-text-light);
  font-size: 12px;
  vertical-align: middle;
}

.thinking-details {
  margin-top: var(--space-xs);
  padding: var(--space-sm);
  background-color: var(--color-primary-softer);
  border-radius: var(--radius-sm);
  font-size: 11px;
  color: var(--color-text-lighter);
}

.thinking-doc-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2px 0;
}

.thinking-doc-source {
  color: var(--color-text-lighter);
  font-size: 11px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.thinking-doc-score {
  color: var(--color-text-light);
  font-size: 11px;
  margin-left: 8px;
}

.thinking-doc-more {
  color: var(--color-text-lightest);
  font-size: 11px;
  margin-top: 2px;
}

.thinking-score-item {
  display: flex;
  gap: 6px;
  align-items: center;
  font-size: 11px;
  color: var(--color-text-lighter);
}

.thinking-score-preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-lightest);
}

/* ===== 打字指示器 ===== */
.typing-indicator {
  display: flex;
  padding: var(--space-xs) 0;
  gap: 4px;
}

.typing-indicator span {
  height: 7px;
  width: 7px;
  background-color: var(--color-text-lighter);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.15s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.3s; }

@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-6px); }
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.spinner-small {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ===== Markdown 排版 ===== */
:deep(p) { margin: 6px 0; line-height: 1.7; }
:deep(ul), :deep(ol) { padding-left: 20px; margin: 6px 0; }
:deep(li) { margin: 3px 0; line-height: 1.6; }
:deep(a) { color: var(--color-primary); text-decoration: none; }
:deep(a:hover) { text-decoration: underline; }
:deep(h1), :deep(h2), :deep(h3), :deep(h4), :deep(h5), :deep(h6) {
  margin: 10px 0 6px; font-weight: 600; color: var(--color-text);
}
:deep(h1) { font-size: 1.4em; }
:deep(h2) { font-size: 1.25em; }
:deep(h3) { font-size: 1.1em; }
:deep(blockquote) {
  border-left: 3px solid var(--color-primary);
  padding: 6px 12px; margin: 8px 0;
  color: var(--color-text-light);
  background-color: var(--color-surface);
  border-radius: 0 6px 6px 0;
  font-size: 0.95em;
}
:deep(hr) { border: 0; border-top: 1px solid var(--color-divider); margin: 14px 0; }
:deep(img) { max-width: 100%; border-radius: 6px; margin: 6px 0; }
:deep(table) { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 0.95em; }
:deep(th), :deep(td) { border: 1px solid var(--color-border); padding: 6px 10px; text-align: left; }
:deep(th) { background-color: var(--color-surface); font-weight: 600; }
:deep(pre) {
  background-color: var(--color-surface); padding: 14px; border-radius: 8px;
  overflow-x: auto; margin: 10px 0; border: 1px solid var(--color-border-light);
  font-size: 0.9em; line-height: 1.5;
}
:deep(pre code) {
  background-color: transparent; padding: 0; border-radius: 0;
  font-family: var(--font-mono); font-size: inherit; color: inherit;
}
:deep(code) {
  font-family: var(--font-mono); background-color: var(--color-surface);
  padding: 2px 6px; border-radius: 4px; font-size: 0.9em; color: var(--color-text-light);
}

/* ===== 响应式 ===== */
@media (max-width: 1023px) {
  .reference-panel {
    display: none;
  }
}

@media (max-width: 767px) {
  .session-panel {
    display: none;
  }

  .message {
    max-width: 95%;
  }
}
</style>
