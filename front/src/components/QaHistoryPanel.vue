<template>
  <div class="qa-history-panel">
    <!-- 头部 -->
    <div class="qa-header">
      <div class="qa-header-title">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 8v4l3 3" />
          <circle cx="12" cy="12" r="9" />
        </svg>
        <span>问答记录</span>
        <span v-if="qaHistory.length" class="qa-count-badge">{{ qaHistory.length }}</span>
      </div>
      <button v-if="qaHistory.length" class="qa-clear-btn" title="清空记录" @click="clearQaHistory">
        清空
      </button>
    </div>

    <!-- 空状态 -->
    <div v-if="!qaHistory.length" class="qa-empty">
      <div class="qa-empty-icon">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
      </div>
      <p>发送消息后，最近 {{ limit }} 组问答的检索过程、引用来源与相关笔记将在此暂存</p>
    </div>

    <!-- 手风琴列表 -->
    <div v-else class="qa-list">
      <div
        v-for="(item, index) in qaHistory"
        :key="item.id"
        class="qa-item"
        :class="{ expanded: expandedQaId === item.id }"
      >
        <!-- 手风琴头部：点击展开/收起 -->
        <button class="qa-item-header" @click="toggleQaExpand(item.id)">
          <span class="qa-index" :class="{ latest: index === 0 }">{{ index === 0 ? '最新' : `#${qaHistory.length - index}` }}</span>
          <span class="qa-question ellipsis">{{ item.question }}</span>
          <span class="qa-time">{{ item.time }}</span>
          <svg
            class="qa-chevron"
            :class="{ rotated: expandedQaId === item.id }"
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>

        <!-- 展开内容 -->
        <div v-show="expandedQaId === item.id" class="qa-item-body">
          <!-- 统计摘要条 -->
          <div class="qa-stats">
            <span v-if="item.citations.length" class="qa-stat-chip">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
              引用 {{ item.citations.length }}
            </span>
            <span v-if="item.thinking.length" class="qa-stat-chip">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></svg>
              步骤 {{ item.thinking.length }}
            </span>
            <span v-if="item.relatedNotes.length" class="qa-stat-chip">
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>
              笔记 {{ item.relatedNotes.length }}
            </span>
          </div>

          <!-- 引用来源 -->
          <div v-if="item.citations.length" class="qa-section">
            <div class="qa-section-title">引用来源</div>
            <div
              v-for="(citation, cIndex) in item.citations"
              :key="cIndex"
              class="qa-citation"
            >
              <span class="qa-citation-index">[{{ cIndex + 1 }}]</span>
              <div class="qa-citation-main">
                <div class="qa-citation-title ellipsis">{{ citation.title || citation.source_id || '未知来源' }}</div>
                <div class="qa-citation-preview">{{ truncatePreview(citation.content_preview, 60) }}</div>
              </div>
              <span v-if="citation.score != null" class="qa-citation-score">{{ (citation.score * 100).toFixed(0) }}%</span>
            </div>
          </div>

          <!-- 检索过程（思考步骤链） -->
          <div v-if="item.thinking.length" class="qa-section">
            <div class="qa-section-title">检索过程</div>
            <!-- 检索链路可视化摘要 -->
            <RetrievalTrace :steps="item.thinking" />
            <div class="qa-steps">
              <div v-for="(step, sIndex) in item.thinking" :key="sIndex" class="qa-step">
                <span class="qa-step-dot" :style="{ backgroundColor: getStageColor(step.stage) }"></span>
                <span class="qa-step-label">{{ getStageLabel(step.stage) }}</span>
                <span class="qa-step-content ellipsis">{{ step.content }}</span>
              </div>
            </div>
          </div>

          <!-- 相关笔记 -->
          <div v-if="item.relatedNotes.length" class="qa-section">
            <div class="qa-section-title">相关笔记</div>
            <div
              v-for="note in item.relatedNotes"
              :key="note.note_id || note.id"
              class="qa-note"
              @click="goToNote(note.note_id || note.id)"
            >
              <div class="qa-note-title-row">
                <span class="qa-note-title ellipsis">{{ note.title || '无标题' }}</span>
                <span v-if="note.similarity != null && !isNaN(note.similarity)" class="qa-note-score">
                  {{ (note.similarity * 100).toFixed(0) }}%
                </span>
              </div>
              <p class="qa-note-preview">{{ truncatePreview(note.content_preview || note.content, 50) }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * QaHistoryPanel — 右栏问答记录手风琴面板
 *
 * 暂存最近 N 组问答（默认 10）的引用来源 / 检索过程 / 相关笔记，
 * 每组以问题为标题做手风琴展开，最新置顶。
 */
import RetrievalTrace from './RetrievalTrace.vue'

defineProps({
  qaHistory: { type: Array, default: () => [] },
  expandedQaId: { type: String, default: null },
  limit: { type: Number, default: 10 },
})

const emit = defineEmits(['toggle', 'clear'])

function toggleQaExpand(id) {
  emit('toggle', id)
}
function clearQaHistory() {
  emit('clear')
}

const stageColors = {
  retrieval: '#3f8cff',
  hyde: '#5ea8ff',
  reorder: '#2a78f0',
  summarize: '#22a060',
  // Agentic 阶段
  planning: '#8b5cf6',
  retrieving: '#3f8cff',
  grading_evidence: '#f59e0b',
  rewriting_query: '#ec4899',
  generating_answer: '#22a060',
  citation: '#14b8a6',
}

function getStageColor(stage) {
  return stageColors[stage] || '#999'
}

const stageLabels = {
  retrieval: '检索',
  hyde: 'HyDE',
  reorder: '重排',
  summarize: '总结',
  planning: '规划',
  retrieving: '检索',
  retrieval_completed: '检索完成',
  grading_evidence: '评估',
  rewriting_query: '改写',
  generating_answer: '生成',
  citation: '引用',
  started: '开始',
  completed: '完成',
}

function getStageLabel(stage) {
  return stageLabels[stage] || stage || '处理中'
}

function truncatePreview(text, maxLen) {
  if (!text) return ''
  return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
}

function goToNote(noteId) {
  if (noteId) window.location.hash = `#/notes/${noteId}`
}
</script>

<style scoped>
.qa-history-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

/* ===== 头部 ===== */
.qa-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-md) var(--space-sm);
  flex-shrink: 0;
}

.qa-header-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.qa-header-title svg {
  color: var(--color-primary);
}

.qa-count-badge {
  font-size: 10px;
  font-weight: 600;
  color: var(--color-primary);
  background: var(--color-primary-light);
  border-radius: var(--radius-full);
  padding: 0 7px;
  line-height: 16px;
}

.qa-clear-btn {
  border: none;
  background: transparent;
  font-size: 11px;
  color: var(--color-text-lightest);
  cursor: pointer;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}

.qa-clear-btn:hover {
  color: var(--color-error);
  background: rgba(239, 68, 68, 0.08);
}

/* ===== 空状态 ===== */
.qa-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  padding: var(--space-xl) var(--space-lg);
  text-align: center;
}

.qa-empty-icon {
  color: var(--color-border);
}

.qa-empty p {
  font-size: 12px;
  color: var(--color-text-lightest);
  line-height: 1.6;
  margin: 0;
  max-width: 220px;
}

/* ===== 手风琴列表 ===== */
/* min-height: 0 —— 允许 flex 子项收缩到小于内容高度，overflow 滚动才能生效 */
.qa-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 var(--space-sm) var(--space-md);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.qa-item {
  /* flex-shrink: 0 —— 阻止 flex 布局压缩条目；
     否则内容超高时条目被压扁而非溢出，滚动条永远不出现 */
  flex-shrink: 0;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  overflow: hidden;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.qa-item.expanded {
  border-color: var(--color-border);
  box-shadow: 0 2px 8px var(--color-shadow);
}

/* 展开态仅在左侧加一道细主色标线，克制不刺眼 */
.qa-item.expanded .qa-item-header {
  box-shadow: inset 3px 0 0 var(--color-primary);
}

/* 手风琴头部 */
.qa-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.qa-item-header:hover {
  background: var(--color-surface);
}

.qa-index {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  color: var(--color-text-lightest);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  padding: 1px 6px;
  min-width: 34px;
  text-align: center;
}

.qa-index.latest {
  color: var(--color-primary);
  background: var(--color-primary-light);
}

.qa-question {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text);
  line-height: 1.4;
}

.qa-time {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-text-lightest);
}

.qa-chevron {
  flex-shrink: 0;
  color: var(--color-text-lightest);
  transition: transform 0.2s ease;
}

.qa-chevron.rotated {
  transform: rotate(180deg);
}

/* ===== 展开内容 ===== */
.qa-item-body {
  padding: 0 10px 10px;
  border-top: 1px dashed var(--color-border-light);
  animation: qaExpand 0.2s ease-out;
}

@keyframes qaExpand {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 统计摘要条 */
.qa-stats {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 0;
}

.qa-stat-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  color: var(--color-text-lighter);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-full);
  padding: 2px 8px;
}

.qa-stat-chip svg {
  color: var(--color-primary);
}

/* 分区 */
.qa-section {
  margin-top: 4px;
  padding-top: 8px;
  border-top: 1px solid var(--color-border-light);
}

.qa-section-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-lighter);
  margin-bottom: 6px;
  letter-spacing: 0.5px;
}

/* 引用条目 */
.qa-citation {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 6px;
  border-radius: var(--radius-sm);
  transition: background 0.15s ease;
}

.qa-citation:hover {
  background: var(--color-surface);
}

.qa-citation-index {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 600;
  color: var(--color-primary);
  margin-top: 2px;
}

.qa-citation-main {
  flex: 1;
  min-width: 0;
}

.qa-citation-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text);
}

.qa-citation-preview {
  font-size: 10px;
  color: var(--color-text-lightest);
  line-height: 1.4;
  margin-top: 1px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.qa-citation-score {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-primary);
  font-weight: 500;
  margin-top: 2px;
}

/* 思考步骤链 */
.qa-steps {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.qa-step {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 4px;
}

.qa-step-dot {
  flex-shrink: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}

.qa-step-label {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 500;
  color: var(--color-text-lighter);
  min-width: 42px;
}

.qa-step-content {
  flex: 1;
  min-width: 0;
  font-size: 10px;
  color: var(--color-text-lightest);
}

/* 相关笔记 */
.qa-note {
  padding: 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s ease;
}

.qa-note:hover {
  background: var(--color-surface);
}

.qa-note-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.qa-note-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text);
}

.qa-note-score {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-primary);
  font-weight: 500;
}

.qa-note-preview {
  font-size: 10px;
  color: var(--color-text-lightest);
  line-height: 1.4;
  margin: 2px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 滚动条美化 */
.qa-list::-webkit-scrollbar {
  width: 4px;
}

.qa-list::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

.qa-list::-webkit-scrollbar-track {
  background: transparent;
}
</style>
