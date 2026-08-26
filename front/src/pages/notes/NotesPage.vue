<template>
  <div class="notes-page">
    <!-- 顶部工具栏 -->
    <div class="notes-toolbar">
      <div class="toolbar-left">
        <h2 class="notes-title">笔记</h2>
        <span class="notes-count">{{ notes.length }} 篇</span>
      </div>
      <div class="toolbar-right">
        <div class="search-box">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            v-model="searchQuery"
            type="text"
            class="search-input"
            placeholder="搜索笔记..."
            @keydown.enter="handleSearch"
          />
        </div>
        <button class="btn-view-toggle" :title="viewMode === 'card' ? '切换表格视图' : '切换卡片视图'" @click="viewMode = viewMode === 'card' ? 'table' : 'card'">
          <svg v-if="viewMode === 'card'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="7" height="7" />
            <rect x="14" y="3" width="7" height="7" />
            <rect x="14" y="14" width="7" height="7" />
            <rect x="3" y="14" width="7" height="7" />
          </svg>
          <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="8" y1="6" x2="21" y2="6" />
            <line x1="8" y1="12" x2="21" y2="12" />
            <line x1="8" y1="18" x2="21" y2="18" />
            <line x1="3" y1="6" x2="3.01" y2="6" />
            <line x1="3" y1="12" x2="3.01" y2="12" />
            <line x1="3" y1="18" x2="3.01" y2="18" />
          </svg>
        </button>
        <button class="btn-primary" @click="createNote">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          新建笔记
        </button>
      </div>
    </div>

    <!-- 分类筛选 -->
    <div class="category-tabs">
      <button
        v-for="c in categories"
        :key="c.key"
        class="category-tab"
        :class="{ active: currentCategory === c.key }"
        @click="filterByCategory(c.key)"
      >
        {{ c.label }}
      </button>
    </div>

    <!-- 加载状态 -->
    <div v-if="isLoading && notes.length === 0" class="loading-state">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>

    <!-- 空状态 -->
    <div v-else-if="!isLoading && notes.length === 0" class="empty-state">
      <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
      </svg>
      <p>{{ searchQuery ? '没有找到匹配的笔记' : '还没有笔记，点击上方按钮创建' }}</p>
    </div>

    <!-- 卡片视图 -->
    <div v-else-if="viewMode === 'card'" class="card-grid">
      <div
        v-for="note in notes"
        :key="note.id"
        class="note-card"
        @click="goToEditor(note.id)"
      >
        <div class="card-header">
          <h3 class="card-title ellipsis">{{ note.title || '无标题' }}</h3>
          <span class="card-date">{{ formatDate(note.updated_at) }}</span>
        </div>
        <p class="card-preview">{{ getPreview(note.content) }}</p>
        <div class="card-footer">
          <div class="card-tags">
            <span v-if="note.category" class="card-category">{{ categoryMap[note.category] || note.category }}</span>
            <span v-for="tag in (note.tags || []).slice(0, 3)" :key="tag" class="card-tag">{{ tag }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 表格视图 -->
    <div v-else class="table-view">
      <div class="table-header">
        <span class="col-title">标题</span>
        <span class="col-category">分类</span>
        <span class="col-tags">标签</span>
        <span class="col-date">更新时间</span>
        <span class="col-actions">操作</span>
      </div>
      <div
        v-for="note in notes"
        :key="note.id"
        class="table-row"
        @click="goToEditor(note.id)"
      >
        <span class="col-title">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <span class="ellipsis">{{ note.title || '无标题' }}</span>
        </span>
        <span class="col-category">{{ categoryMap[note.category] || '-' }}</span>
        <span class="col-tags">
          <span v-for="tag in (note.tags || []).slice(0, 2)" :key="tag" class="table-tag">{{ tag }}</span>
        </span>
        <span class="col-date">{{ formatDate(note.updated_at) }}</span>
        <span class="col-actions">
          <button class="btn-icon-sm" title="删除" @click.stop="handleDelete(note)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </span>
      </div>
    </div>

    <!-- 加载更多 -->
    <div v-if="notes.length > 0 && !finished" class="load-more">
      <button class="btn-text" :disabled="isLoading" @click="loadMore">
        {{ isLoading ? '加载中...' : '加载更多' }}
      </button>
    </div>
  </div>
</template>

<script setup>
/**
 * NotesPage — 笔记列表页
 * 支持卡片/表格双模式、搜索、分类筛选、无限滚动
 */
import { ref, onMounted, onActivated, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { showToast, showDialog } from 'vant'
import { noteApi } from '../../services/noteApi'

const router = useRouter()
const route = useRoute()

// ===== State =====
const notes = ref([])
const isLoading = ref(false)
const finished = ref(false)
const searchQuery = ref('')
const currentCategory = ref('all')
const viewMode = ref('card')
const page = ref(1)
const pageSize = 20

// ===== Config =====
const categories = [
  { key: 'all', label: '全部' },
  { key: 'work', label: '工作' },
  { key: 'study', label: '学习' },
  { key: 'life', label: '生活' },
  { key: 'project', label: '项目' },
]

const categoryMap = { work: '工作', study: '学习', life: '生活', project: '项目' }

// ===== Functions =====
function getPreview(content) {
  if (!content) return ''
  const text = content.replace(/[#*`~>[]()!|-]/g, '').replace(/\s+/g, ' ').trim()
  return text.length > 120 ? text.slice(0, 120) + '...' : text
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function fetchNotes(isRefresh = false) {
  if (isLoading.value) return
  if (finished.value && !isRefresh) return
  isLoading.value = true

  if (isRefresh) {
    page.value = 1
    finished.value = false
  }

  try {
    const params = { page: page.value, page_size: pageSize, _t: Date.now() }
    if (currentCategory.value !== 'all') {
      params.category = currentCategory.value
    }
    const result = await noteApi.getList(params)
    if (result.code === 200) {
      const newNotes = result.data?.notes || []
      if (isRefresh) {
        notes.value = newNotes
      } else {
        notes.value = [...notes.value, ...newNotes]
      }
      if (newNotes.length < pageSize) {
        finished.value = true
      }
      page.value++
    } else {
      console.warn('[NotesPage] unexpected result code:', result.code, result)
    }
  } catch (error) {
    console.error('加载笔记失败:', error)
    showToast('加载失败')
  } finally {
    isLoading.value = false
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    fetchNotes(true)
    return
  }
  isLoading.value = true
  try {
    const result = await noteApi.search(searchQuery.value)
    if (result.code === 200) {
      notes.value = result.data?.notes || []
      finished.value = true
    }
  } catch (error) {
    console.error('搜索失败:', error)
    showToast('搜索失败')
  } finally {
    isLoading.value = false
  }
}

function filterByCategory(key) {
  currentCategory.value = key
  notes.value = []
  page.value = 1
  finished.value = false
  searchQuery.value = ''
  fetchNotes(true)
}

function loadMore() {
  if (searchQuery.value.trim()) return
  fetchNotes(false)
}

function createNote() {
  localStorage.removeItem('note_draft')
  router.push('/notes/new')
}

function goToEditor(id) {
  router.push(`/notes/${id}`)
}

async function handleDelete(note) {
  try {
    await showDialog({
      title: '确认删除',
      message: `确定要删除笔记 "${note.title || '无标题'}" 吗？`,
      showCancelButton: true,
    })
    await noteApi.delete(note.id)
    showToast('删除成功')
    notes.value = notes.value.filter(n => n.id !== note.id)
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除失败:', error)
      showToast('删除失败')
    }
  }
}

// ===== Lifecycle =====
let firstMount = true

onMounted(() => {
  // 如果 URL 带搜索参数，自动填充搜索框并触发搜索
  const q = route.query.q
  if (q) {
    searchQuery.value = q
    handleSearch()
  } else {
    fetchNotes(true)
  }
  firstMount = false
})

onActivated(() => {
  // 首次挂载时 onMounted 已处理，跳过
  if (firstMount) return
  page.value = 1
  finished.value = false
  notes.value = []
  isLoading.value = false  // 确保不被旧状态阻塞
  fetchNotes(true)
})

// 监听 URL 搜索参数变化（从 TopBar 搜索跳转过来）
watch(() => route.query.q, (newQ) => {
  if (newQ) {
    searchQuery.value = newQ
    handleSearch()
  }
})
</script>

<style scoped>
.notes-page {
  min-height: 100%;
  background: var(--color-bg);
}

/* ===== Toolbar ===== */
.notes-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
  flex-wrap: wrap;
  gap: var(--space-md);
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.notes-title {
  font-family: var(--font-heading);
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
}

.notes-count {
  font-size: 13px;
  color: var(--color-text-lighter);
  background: var(--color-surface);
  padding: var(--space-xs) var(--space-md);
  border-radius: var(--radius-full);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.search-box {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(10px);
  backdrop-filter: blur(10px);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-md);
  transition: border-color 0.15s ease;
}

.search-box:focus-within {
  border-color: var(--color-primary);
}

.search-box svg {
  color: var(--color-text-lightest);
  flex-shrink: 0;
}

.search-input {
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--color-text);
  outline: none;
  width: 180px;
}

.search-input::placeholder {
  color: var(--color-text-lightest);
}

.btn-view-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--glass-border);
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  color: var(--color-text-lighter);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-view-toggle:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.btn-primary {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-primary);
  color: var(--color-card);
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.btn-primary:hover {
  background: var(--color-primary-hover);
}

/* ===== Category Tabs ===== */
.category-tabs {
  display: flex;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
  overflow-x: auto;
}

.category-tab {
  padding: var(--space-xs) var(--space-md);
  border-radius: var(--radius-full);
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  font-size: 13px;
  color: var(--color-text-light);
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.15s ease;
  border: 1px solid var(--glass-border);
}

.category-tab:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}

.category-tab.active {
  background: var(--color-primary);
  color: var(--color-card);
  border-color: var(--color-primary);
}

/* ===== States ===== */
.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: calc(var(--space-2xl) * 2);
  color: var(--color-text-lighter);
}

.loading-state p,
.empty-state p {
  margin-top: var(--space-md);
  font-size: 14px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ===== Card Grid ===== */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: var(--space-lg);
}

.note-card {
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  cursor: pointer;
  transition: all 0.2s ease;
}

.note-card:hover {
  border-color: var(--color-primary);
  box-shadow: 0 8px 24px var(--color-shadow-strong);
  transform: translateY(-2px);
}

.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-sm);
  margin-bottom: var(--space-sm);
}

.card-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
}

.card-date {
  font-size: 12px;
  color: var(--color-text-lightest);
  flex-shrink: 0;
}

.card-preview {
  font-size: 14px;
  color: var(--color-text-lighter);
  line-height: 1.6;
  margin: 0 0 var(--space-md);
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-tags {
  display: flex;
  gap: var(--space-xs);
  flex-wrap: wrap;
}

.card-category {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--color-surface);
  color: var(--color-text-lighter);
  border-radius: var(--radius-sm);
}

.card-tag {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-radius: var(--radius-sm);
}

/* ===== Table View ===== */
.table-view {
  background: var(--glass-bg-strong);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-radius: var(--radius-lg);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
  overflow: hidden;
}

.table-header {
  display: grid;
  grid-template-columns: 1fr 80px 120px 100px 60px;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
  background: var(--color-surface);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-lighter);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.table-row {
  display: grid;
  grid-template-columns: 1fr 80px 120px 100px 60px;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: background 0.15s ease;
  align-items: center;
}

.table-row:hover {
  background: var(--color-surface);
}

.col-title {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: 14px;
  color: var(--color-text);
  min-width: 0;
}

.col-title svg {
  flex-shrink: 0;
  color: var(--color-text-lighter);
}

.col-category {
  font-size: 13px;
  color: var(--color-text-light);
  text-align: center;
}

.col-tags {
  display: flex;
  gap: var(--space-xs);
  flex-wrap: wrap;
}

.table-tag {
  font-size: 11px;
  padding: 2px 6px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  border-radius: var(--radius-sm);
}

.col-date {
  font-size: 13px;
  color: var(--color-text-lighter);
  text-align: center;
}

.col-actions {
  display: flex;
  justify-content: flex-end;
}

.btn-icon-sm {
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
  transition: all 0.15s ease;
}

.btn-icon-sm:hover {
  background: var(--color-error);
  color: var(--color-card);
}

/* ===== Load More ===== */
.load-more {
  display: flex;
  justify-content: center;
  padding: var(--space-lg);
}

.btn-text {
  background: transparent;
  border: none;
  color: var(--color-primary);
  font-size: 14px;
  cursor: pointer;
  padding: var(--space-sm) var(--space-lg);
}

.btn-text:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* ===== Utility ===== */
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== Responsive ===== */
@media (max-width: 767px) {
  .notes-toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-right {
    flex-wrap: wrap;
  }

  .search-box {
    flex: 1;
  }

  .search-input {
    width: 100%;
  }

  .card-grid {
    grid-template-columns: 1fr;
  }

  .table-header,
  .table-row {
    grid-template-columns: 1fr 60px 80px;
  }

  .col-tags,
  .col-actions {
    display: none;
  }
}
</style>
