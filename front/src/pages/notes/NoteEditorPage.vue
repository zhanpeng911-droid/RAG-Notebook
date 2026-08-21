<template>
  <div class="editor-layout">
    <!-- 左侧：编辑器主区域 -->
    <div class="editor-main" :class="{ 'editor-main--full': !sidebarVisible && !isNew }">
      <!-- 顶部操作栏 -->
      <div class="editor-toolbar">
        <div class="toolbar-left">
          <button class="btn-back" @click="goBack">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15 18 9 12 15 6"/>
            </svg>
            返回
          </button>
          <span class="toolbar-label">{{ isNew ? '新建笔记' : '编辑笔记' }}</span>
        </div>
        <div class="toolbar-right">
          <span v-if="autoSaved" class="auto-saved">已自动保存</span>
          <button class="btn-save" :disabled="saving" @click="handleSave">
            {{ saving ? '保存中...' : '保存' }}
          </button>
          <button v-if="!isNew" class="btn-delete" @click="handleDelete">删除</button>
        </div>
      </div>

      <!-- 标题栏 -->
      <div class="title-bar">
        <input
          v-model="title"
          class="title-input"
          placeholder="输入笔记标题..."
          maxlength="200"
        />
        <div class="title-meta">
          <select v-model="category" class="category-select">
            <option value="">选择分类</option>
            <option value="work">工作</option>
            <option value="study">学习</option>
            <option value="life">生活</option>
            <option value="project">项目</option>
          </select>
          <div class="tags-input">
            <span v-for="(tag, index) in tags" :key="index" class="tag-item">
              {{ tag }}
              <button class="tag-remove" @click="removeTag(index)">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="18" y1="6" x2="6" y2="18"/>
                  <line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </span>
            <input
              v-model="newTag"
              class="tag-input"
              placeholder="添加标签..."
              @keydown.enter.prevent="addTag"
              @keydown.comma.prevent="addTag"
            />
          </div>
        </div>
      </div>

      <!-- 快捷工具栏 -->
      <QuickToolbar :editor-ref="markdownEditorRef" />

      <!-- 编辑器主体 -->
      <div class="editor-body" ref="editorBodyRef">
        <MarkdownEditor ref="markdownEditorRef" v-model="content" />
        <InlineCompletion
          :context="completionContext"
          :position="cursorPosition"
          @accept="handleAccept"
        />
      </div>
    </div>

    <!-- 右侧：相关笔记侧边栏 -->
    <div class="sidebar-zone" v-if="!isNew">
      <!-- 折叠态 -->
      <div v-if="!sidebarVisible" class="sidebar-collapsed" @click="toggleSidebar">
        <div class="sidebar-toggle-btn">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"/>
          </svg>
        </div>
        <div class="sidebar-toggle-hint">相关</div>
      </div>

      <!-- 展开态 -->
      <div v-else class="related-sidebar">
        <!-- 详情视图 -->
        <template v-if="expandedNote">
          <div class="related-header">
            <button class="btn-sidebar-back" @click="expandedNote = null">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="15 18 9 12 15 6"/>
              </svg>
              返回列表
            </button>
            <button class="btn-sidebar-close" @click="toggleSidebar">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </button>
          </div>
          <div v-if="detailLoading" class="related-loading">
            <div class="spinner-sm"></div>
          </div>
          <div v-else class="related-detail">
            <h3 class="detail-title">{{ expandedNote.title }}</h3>
            <div v-if="expandedNote.category || (expandedNote.tags && expandedNote.tags.length > 0)" class="detail-meta">
              <span v-if="expandedNote.category" class="detail-category">{{ categoryMap[expandedNote.category] || expandedNote.category }}</span>
              <span v-for="t in expandedNote.tags || []" :key="t" class="detail-tag">{{ t }}</span>
            </div>
            <div class="detail-body markdown-body" v-html="renderedExpandedContent"></div>
          </div>
        </template>

        <!-- 列表视图 -->
        <template v-else>
          <div class="related-header">
            <span class="related-title">相关笔记</span>
            <button class="btn-sidebar-close" @click="toggleSidebar">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9 18 15 12 9 6"/>
              </svg>
            </button>
          </div>

          <div v-if="loadingRelated" class="related-loading">
            <div class="spinner-sm"></div>
          </div>

          <div v-else-if="relatedItems.length === 0" class="related-empty">
            暂无相关笔记
          </div>

          <div v-else class="related-list">
            <div
              v-for="item in relatedItems"
              :key="item.id"
              class="related-card"
              @click="expandRelatedNote(item)"
            >
              <div class="related-card-header">
                <span class="related-source" :class="item.source === 'note' ? 'src-note' : 'src-kb'">
                  {{ item.source === 'note' ? '笔记' : '知识库' }}
                </span>
                <span class="related-similarity">{{ (item.similarity * 100).toFixed(0) }}%</span>
              </div>
              <h4 class="related-card-title ellipsis">{{ item.title }}</h4>
              <p class="related-card-preview">{{ item.content_preview }}</p>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * NoteEditorPage — 笔记编辑器
 * 双栏布局：左侧编辑器 + 右侧相关笔记侧边栏
 */
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { noteApi } from '../../services/noteApi'
import { useModelStore } from '../../store/model'
import MarkdownEditor from '../../components/MarkdownEditor.vue'
import QuickToolbar from '../../components/QuickToolbar.vue'
import InlineCompletion from '../../components/InlineCompletion.vue'

const route = useRoute()
const router = useRouter()
const modelStore = useModelStore()

// ===== Refs =====
const markdownEditorRef = ref(null)
const editorBodyRef = ref(null)

// ===== Editor State =====
const title = ref('')
const content = ref('')
const tags = ref([])
const newTag = ref('')
const category = ref('')
const saving = ref(false)
const noteId = ref('')
const autoSaved = ref(false)

// ===== Inline Completion =====
const completionContext = ref('')
const cursorPosition = ref({ top: 0, left: 0 })
let cmCleanup = null

// ===== Sidebar State =====
const sidebarVisible = ref(false)
const relatedItems = ref([])
const loadingRelated = ref(false)
const expandedNote = ref(null)
const detailLoading = ref(false)
const renderedExpandedContent = ref('')

let autoSaveTimer = null
let relatedRefreshTimer = null

// ===== Computed =====
const isNew = computed(() => route.name === 'NoteNew' || route.params.id === 'new')
const categoryMap = { work: '工作', study: '学习', life: '生活', project: '项目' }

// ===== Tags =====
function addTag() {
  const tag = newTag.value.trim().replace(/,/g, '')
  if (tag && !tags.value.includes(tag)) {
    tags.value.push(tag)
  }
  newTag.value = ''
}

function removeTag(index) {
  tags.value.splice(index, 1)
}

// ===== Sidebar =====
function toggleSidebar() {
  sidebarVisible.value = !sidebarVisible.value
  if (sidebarVisible.value && relatedItems.value.length === 0) {
    fetchRelated()
  }
}

async function expandRelatedNote(item) {
  if (item.source === 'knowledge_base') {
    expandedNote.value = {
      id: item.id,
      title: item.title,
      tags: [],
      category: '',
    }
    const html = await marked.parse(item.content || item.content_preview || '')
    renderedExpandedContent.value = DOMPurify.sanitize(html)
    return
  }

  detailLoading.value = true
  expandedNote.value = { id: item.id, title: item.title }
  renderedExpandedContent.value = ''

  try {
    const result = await noteApi.getDetail(item.id)
    if (result.code === 200 && result.data) {
      expandedNote.value = result.data
      const html = await marked.parse(result.data.content || '')
      renderedExpandedContent.value = DOMPurify.sanitize(html)
    }
  } catch (error) {
    console.error('加载笔记失败:', error)
    showToast('加载笔记失败')
    expandedNote.value = null
  } finally {
    detailLoading.value = false
  }
}

// ===== CRUD =====
async function loadNote() {
  try {
    const result = await noteApi.getDetail(noteId.value)
    if (result.code === 200 && result.data) {
      title.value = result.data.title
      content.value = result.data.content
      tags.value = result.data.tags || []
      category.value = result.data.category || ''
    }
  } catch (error) {
    console.error('加载笔记失败:', error)
    showToast('加载笔记失败')
  }
}

async function fetchRelated() {
  if (!noteId.value || isNew.value) return
  loadingRelated.value = true
  try {
    const result = await noteApi.getRelated(noteId.value)
    if (result.code === 200) {
      relatedItems.value = result.data || []
    }
  } catch (error) {
    console.error('加载关联笔记失败:', error)
  } finally {
    loadingRelated.value = false
  }
}

function scheduleRelatedRefresh() {
  if (isNew.value || !noteId.value) return
  if (relatedRefreshTimer) clearTimeout(relatedRefreshTimer)
  relatedRefreshTimer = setTimeout(() => {
    fetchRelated()
  }, 3000)
}

async function handleSave() {
  if (!title.value.trim() && !content.value.trim()) {
    showToast('标题或内容不能为空')
    return
  }

  saving.value = true
  try {
    const data = { title: title.value, content: content.value, tags: tags.value, category: category.value }
    if (modelStore.isConfigured) {
      data.llm_config = modelStore.config
    }

    if (isNew.value) {
      const result = await noteApi.create(data)
      if (result.code === 200) {
        clearDraft()
        showToast('保存成功')
        const newId = result.data?.id
        router.replace(`/notes/${newId}`)
      } else {
        showToast('保存失败')
      }
    } else {
      const result = await noteApi.update(noteId.value, data)
      if (result.code === 200) {
        clearDraft()
        autoSaved.value = true
        setTimeout(() => { autoSaved.value = false }, 2000)
        showToast('保存成功')
      } else {
        showToast('保存失败')
      }
    }
  } catch (error) {
    console.error('保存失败:', error)
    showToast('网络错误')
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  try {
    await showConfirmDialog({
      title: '确认删除',
      message: '删除后无法恢复，确定要删除吗？',
    })
    await noteApi.delete(noteId.value)
    showToast('删除成功')
    router.replace('/notes')
  } catch (error) {
    // 用户取消
  }
}

// ===== Draft =====
function autoSaveDraft() {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(() => {
    localStorage.setItem('note_draft', JSON.stringify({
      title: title.value,
      content: content.value,
      tags: tags.value,
      category: category.value,
      noteId: noteId.value,
      timestamp: Date.now(),
    }))
    autoSaved.value = true
    setTimeout(() => { autoSaved.value = false }, 2000)
  }, 2000)
}

function clearDraft() {
  localStorage.removeItem('note_draft')
}

function loadDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem('note_draft') || 'null')
    if (draft && draft.noteId === noteId.value) {
      title.value = draft.title || ''
      content.value = draft.content || ''
      tags.value = draft.tags || []
      category.value = draft.category || ''
    }
  } catch (e) {
    // ignore
  }
}

// ===== Inline Completion =====
function handleAccept(completion) {
  const cm = markdownEditorRef.value?.getEditorCm()
  if (!cm) return
  const cursor = cm.getCursor()
  cm.getDoc().replaceRange(completion, cursor)
  completionContext.value = ''
}

let cursorTrackingRetries = 0
function setupCursorTracking() {
  const cm = markdownEditorRef.value?.getEditorCm()
  if (!cm) {
    if (cursorTrackingRetries < 50) {
      cursorTrackingRetries++
      setTimeout(setupCursorTracking, 100)
    }
    return
  }
  cursorTrackingRetries = 0

  function updateCursor() {
    const ctx = markdownEditorRef.value?.getCursorContext()
    if (!ctx) return
    completionContext.value = ctx.textBeforeCursor
    const rect = editorBodyRef.value?.getBoundingClientRect()
    if (rect) {
      cursorPosition.value = {
        top: ctx.cursorCoords.top - rect.top,
        left: ctx.cursorCoords.left - rect.left,
      }
    }
  }

  cm.on('cursorActivity', updateCursor)
  const scrollEl = cm.getScrollerElement()
  if (scrollEl) scrollEl.addEventListener('scroll', updateCursor)

  updateCursor()

  cmCleanup = () => {
    cm.off('cursorActivity', updateCursor)
    if (scrollEl) scrollEl.removeEventListener('scroll', updateCursor)
  }
}

function goBack() {
  if (title.value || content.value) {
    autoSaveDraft()
    showToast('草稿已保存')
  }
  router.push('/notes')
}

// ===== Watchers =====
watch([title, content], () => {
  autoSaveDraft()
  scheduleRelatedRefresh()
})

// ===== Lifecycle =====
onMounted(() => {
  noteId.value = route.params.id
  if (isNew.value) {
    clearDraft()
    loadDraft()
  } else {
    loadNote()
  }
  setupCursorTracking()
})

onUnmounted(() => {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  if (relatedRefreshTimer) clearTimeout(relatedRefreshTimer)
  if (cmCleanup) cmCleanup()
})
</script>

<style scoped>
/* ===== Layout ===== */
.editor-layout {
  display: flex;
  height: 100%;
  background: var(--color-bg);
  overflow: hidden;
  padding-top: var(--topbar-height);
  box-sizing: border-box;
}

.editor-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  /* 编辑区用高不透明玻璃：保留磨砂质感同时保证长文可读性 */
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-right: 1px solid var(--glass-border);
}

/* ===== Toolbar ===== */
.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-lg);
  height: 48px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--glass-border);
  flex-shrink: 0;
}

.toolbar-left,
.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.btn-back {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  border: none;
  background: transparent;
  color: var(--color-text-lighter);
  font-size: 14px;
  cursor: pointer;
  padding: var(--space-xs) var(--space-sm);
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}

.btn-back:hover {
  color: var(--color-primary);
  background: var(--color-surface);
}

.toolbar-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

.auto-saved {
  font-size: 12px;
  color: var(--color-success);
}

.btn-save {
  padding: var(--space-xs) var(--space-lg);
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.btn-save:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.btn-save:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-delete {
  padding: var(--space-xs) var(--space-lg);
  background: transparent;
  color: var(--color-error);
  border: 1px solid var(--color-error);
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-delete:hover {
  background: var(--color-error);
  color: white;
}

/* ===== Title Bar ===== */
.title-bar {
  padding: var(--space-lg) var(--space-xl);
  background: transparent;
  border-bottom: 1px solid var(--glass-border);
  flex-shrink: 0;
}

.title-input {
  width: 100%;
  border: none;
  outline: none;
  font-size: 24px;
  font-weight: 700;
  font-family: var(--font-heading);
  color: var(--color-text);
  line-height: 1.4;
  background: transparent;
}

.title-input::placeholder {
  color: var(--color-text-lightest);
}

.title-meta {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  margin-top: var(--space-md);
  flex-wrap: wrap;
}

.category-select {
  padding: var(--space-xs) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text);
  background: var(--color-surface);
  outline: none;
  cursor: pointer;
}

.category-select:focus {
  border-color: var(--color-primary);
}

.tags-input {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  flex-wrap: wrap;
}

.tag-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-radius: var(--radius-sm);
  font-size: 12px;
}

.tag-remove {
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--color-primary);
  cursor: pointer;
  padding: 0;
}

.tag-input {
  border: none;
  outline: none;
  font-size: 13px;
  color: var(--color-text);
  background: transparent;
  min-width: 100px;
}

.tag-input::placeholder {
  color: var(--color-text-lightest);
}

/* ===== Editor Body ===== */
.editor-body {
  flex: 1;
  overflow-y: auto;
  background: transparent;
  position: relative;
  min-height: 0;
}

/* ===== Sidebar Zone ===== */
.sidebar-zone {
  position: relative;
  flex-shrink: 0;
}

.sidebar-collapsed {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 100%;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border-left: 1px solid var(--glass-border);
  cursor: pointer;
  transition: background 0.15s ease;
}

.sidebar-collapsed:hover {
  background: var(--color-surface);
}

.sidebar-toggle-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  color: var(--color-text-lightest);
}

.sidebar-collapsed:hover .sidebar-toggle-btn {
  color: var(--color-primary);
}

.sidebar-toggle-hint {
  margin-top: var(--space-xs);
  font-size: 11px;
  color: var(--color-text-lightest);
  writing-mode: vertical-rl;
  letter-spacing: 2px;
}

.related-sidebar {
  width: 320px;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-left: 1px solid var(--glass-border);
  animation: slideIn 0.2s ease;
}

@keyframes slideIn {
  from { width: 0; opacity: 0; }
  to { width: 320px; opacity: 1; }
}

.related-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  border-bottom: 1px solid var(--color-border-light);
  flex-shrink: 0;
}

.related-title {
  font-weight: 600;
}

.btn-sidebar-back {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  border: none;
  background: transparent;
  color: var(--color-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  padding: var(--space-xs) var(--space-sm);
  border-radius: var(--radius-sm);
}

.btn-sidebar-back:hover {
  background: var(--color-surface);
}

.btn-sidebar-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-text-lightest);
  cursor: pointer;
  border-radius: var(--radius-sm);
}

.btn-sidebar-close:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

.related-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-2xl);
}

.related-empty {
  padding: var(--space-2xl);
  text-align: center;
  font-size: 14px;
  color: var(--color-text-lightest);
}

.related-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-md);
}

.related-card {
  padding: var(--space-md);
  margin-bottom: var(--space-sm);
  background: var(--color-surface);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: all 0.15s ease;
}

.related-card:hover {
  background: var(--glass-bg-strong);
  border-color: var(--color-primary);
}

.related-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-sm);
}

.related-source {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}

.src-note {
  background: var(--color-primary-light);
  color: var(--color-primary);
}

.src-kb {
  background: var(--status-warning-bg);
  color: var(--status-warning-text);
}

.related-similarity {
  font-size: 12px;
  color: var(--color-text-lightest);
}

.related-card-title {
  margin: 0 0 var(--space-xs);
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
}

.related-card-preview {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-lighter);
  line-height: 1.5;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

/* ===== Related Detail ===== */
.related-detail {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-lg);
}

.detail-title {
  margin: 0 0 var(--space-md);
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text);
  line-height: 1.4;
}

.detail-meta {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
  margin-bottom: var(--space-lg);
  padding-bottom: var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
}

.detail-category {
  font-size: 12px;
  padding: 2px 8px;
  background: var(--color-surface);
  color: var(--color-text-lighter);
  border-radius: var(--radius-sm);
}

.detail-tag {
  font-size: 12px;
  padding: 2px 8px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-radius: var(--radius-sm);
}

.detail-body {
  font-size: 14px;
  color: var(--color-text);
  line-height: 1.8;
}

.detail-body :deep(h1),
.detail-body :deep(h2),
.detail-body :deep(h3) {
  margin-top: var(--space-lg);
  margin-bottom: var(--space-sm);
  font-weight: 600;
  color: var(--color-text);
}

.detail-body :deep(p) {
  margin: 0 0 var(--space-sm);
}

.detail-body :deep(pre) {
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  padding: var(--space-md);
  overflow-x: auto;
  font-size: 13px;
}

.detail-body :deep(code) {
  background: var(--color-surface);
  padding: 2px 4px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.detail-body :deep(pre code) {
  background: transparent;
  padding: 0;
}

.detail-body :deep(blockquote) {
  margin: var(--space-sm) 0;
  padding: var(--space-sm) var(--space-md);
  border-left: 3px solid var(--color-border);
  color: var(--color-text-light);
}

.detail-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: var(--space-sm) 0;
}

.detail-body :deep(th),
.detail-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: var(--space-xs) var(--space-sm);
  font-size: 13px;
}

.detail-body :deep(img) {
  max-width: 100%;
}

/* ===== Spinner ===== */
.spinner-sm {
  width: 20px;
  height: 20px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ===== Utility ===== */
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== Responsive ===== */
@media (max-width: 768px) {
  .sidebar-zone {
    display: none;
  }

  .title-bar {
    padding: var(--space-md);
  }

  .title-input {
    font-size: 20px;
  }
}
</style>
