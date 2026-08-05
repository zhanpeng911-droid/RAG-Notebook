<template>
  <div class="kb-console">
    <!-- 顶部工具栏 -->
    <div class="kb-toolbar">
      <div class="kb-toolbar-left">
        <h2 class="kb-title">知识库管理</h2>
        <span class="kb-doc-count">{{ documents.length }} 个文档</span>
      </div>
      <div class="kb-toolbar-right">
        <button
          v-if="documents.length > 0"
          class="btn-danger-plain"
          @click="handleCleanAll"
        >
          清除全部
        </button>
      </div>
    </div>

    <!-- 空间选择（上传归属） -->
    <div class="space-selector-bar">
      <span class="space-selector-label">归属空间</span>
      <select v-model="uploadSpaceId" class="space-select">
        <option value="">未分配空间</option>
        <option v-for="s in spaces" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
    </div>

    <!-- 上传区域 -->
    <div
      class="upload-zone"
      :class="{ 'upload-zone-active': isDragOver }"
      @click="openFilePicker"
      @dragover.prevent="isDragOver = true"
      @dragleave="isDragOver = false"
      @drop.prevent="handleDrop"
    >
      <div class="upload-icon">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="17 8 12 3 7 8"/>
          <line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
      </div>
      <p class="upload-text">拖拽文件到此处，或点击选择文件</p>
      <p class="upload-hint">支持 .md, .txt, .pdf, .docx, .pptx 格式</p>
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".md,.txt,.pdf,.docx,.pptx"
        class="file-input"
        @change="handleFileSelect"
      />
    </div>

    <!-- 已选文件列表 -->
    <div v-if="selectedFiles.length > 0" class="selected-files">
      <div class="selected-header">
        <span class="selected-count">已选择 {{ selectedFiles.length }} 个文件</span>
        <button class="btn-text" @click="selectedFiles = []">清空</button>
      </div>
      <div class="file-list">
        <div v-for="(file, index) in selectedFiles" :key="index" class="file-item">
          <div class="file-info">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            <span class="file-name">{{ file.name }}</span>
            <span class="file-size">{{ formatFileSize(file.size) }}</span>
          </div>
          <button class="file-remove" @click="removeFile(index)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>
      <button
        class="btn-primary btn-block"
        :disabled="uploading"
        @click="uploadFiles"
      >
        {{ uploading ? '上传中...' : '开始上传' }}
      </button>
    </div>

    <!-- 上传进度 -->
    <div v-if="uploading || uploadComplete" class="upload-progress">
      <div class="progress-header">
        <h3 class="section-title">上传进度</h3>
        <span v-if="uploadComplete" class="upload-result-text">
          完成：{{ successCount }} 成功，{{ failedCount }} 失败
        </span>
      </div>
      <div v-for="(progress, index) in uploadProgressList" :key="index" class="progress-item">
        <div class="progress-item-header">
          <span class="progress-filename ellipsis">{{ progress.filename }}</span>
          <span class="progress-status" :class="getStatusClass(progress.status)">
            {{ getStatusText(progress.status) }}
          </span>
        </div>
        <div v-if="progress.percentage !== null" class="progress-bar-wrapper">
          <div class="progress-bar" :style="{ width: progress.percentage + '%' }"></div>
        </div>
        <p class="progress-message">{{ progress.message }}</p>
      </div>
    </div>

    <!-- 索引状态 -->
    <div v-if="documents.length > 0 && !uploading" class="index-status">
      <div class="status-card">
        <div class="status-value">{{ documents.length }}</div>
        <div class="status-label">文档总数</div>
      </div>
      <div class="status-card">
        <div class="status-value">{{ totalChunks }}</div>
        <div class="status-label">切片总数</div>
      </div>
      <div class="status-card">
        <div class="status-value">{{ documentsWithImages }}</div>
        <div class="status-label">含图片文档</div>
      </div>
    </div>

    <!-- 文档表格 -->
    <div v-if="!uploading" class="document-section">
      <div class="section-header">
        <h3 class="section-title">文档列表</h3>
        <div class="section-actions">
          <select v-model="filterSpaceId" class="space-filter-select" @change="fetchDocuments">
            <option value="">全部空间</option>
            <option v-for="s in spaces" :key="s.id" :value="s.id">{{ s.name }}</option>
          </select>
          <input
            v-model="searchQuery"
            type="text"
            class="search-input"
            placeholder="搜索文档..."
          />
        </div>
      </div>

      <div v-if="loadingDocuments" class="loading-state">
        <div class="spinner"></div>
        <p>加载中...</p>
      </div>

      <div v-else-if="documentError" class="error-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <p class="error-title">文档列表加载失败</p>
        <p class="error-desc">{{ documentError }}</p>
        <div class="error-actions">
          <button class="btn-retry" @click="fetchDocuments">重试</button>
          <button class="btn-secondary" @click="router.push('/')">返回首页</button>
        </div>
      </div>

      <div v-else-if="filteredDocuments.length === 0" class="empty-state">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
        <p>{{ searchQuery ? '没有找到匹配的文档' : '暂无文档，上传文件开始构建知识库' }}</p>
      </div>

      <div v-else class="doc-table">
        <div class="doc-table-header">
          <span class="col-name">文件名</span>
          <span class="col-type">类型</span>
          <span class="col-chunks">切片数</span>
          <span class="col-status">索引状态</span>
          <span class="col-actions">操作</span>
        </div>
        <div
          v-for="doc in filteredDocuments"
          :key="doc.id || doc.filename"
          class="doc-table-row"
          @click="viewDocumentDetail(doc)"
        >
          <span class="col-name">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
              <polyline points="14 2 14 8 20 8"/>
            </svg>
            <span class="ellipsis">{{ doc.original_filename || doc.filename }}</span>
          </span>
          <span class="col-type">{{ getFileType(doc.filename) }}</span>
          <span class="col-chunks">{{ doc.chunk_count || 0 }}</span>
          <span class="col-status">
            <span v-if="doc.index_status" class="index-badge" :class="getIndexStatusClass(doc.index_status)">
              {{ getIndexStatusText(doc.index_status) }}
            </span>
            <span v-else class="index-badge status-legacy">旧文档</span>
            <span v-if="doc.index_error" class="index-error-hint" :title="doc.index_error">⚠</span>
          </span>
          <span class="col-actions">
            <button
              v-if="doc.index_status === 'indexed'"
              class="btn-icon-sm btn-ask-ai"
              title="向 AI 提问"
              @click.stop="askAIAboutDoc(doc)"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
            </button>
            <button
              v-if="doc.index_status === 'pending_index' || doc.index_status === 'index_failed'"
              class="btn-icon-sm btn-reindex"
              title="重新索引"
              @click.stop="handleReindex(doc)"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="23 4 23 10 17 10"/>
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>
              </svg>
            </button>
            <button class="btn-icon-sm" title="查看详情" @click.stop="viewDocumentDetail(doc)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
            </button>
            <button class="btn-icon-sm" title="查看切片" @click.stop="viewDocumentChunks(doc)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="7" height="7"/>
                <rect x="14" y="3" width="7" height="7"/>
                <rect x="14" y="14" width="7" height="7"/>
                <rect x="3" y="14" width="7" height="7"/>
              </svg>
            </button>
            <button class="btn-icon-sm btn-danger" title="删除" @click.stop="handleDeleteDocument(doc)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          </span>
        </div>
      </div>
    </div>

    <!-- 文档详情抽屉 -->
    <div v-if="showDetail" class="drawer-overlay" @click="showDetail = false">
      <div class="drawer" @click.stop>
        <div class="drawer-header">
          <h3>{{ currentDocument?.original_filename || currentDocument?.filename }}</h3>
          <button class="btn-icon-sm" @click="showDetail = false">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"/>
              <line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="drawer-tabs">
          <button
            class="drawer-tab"
            :class="{ active: detailTab === 'content' }"
            @click="detailTab = 'content'"
          >
            文档内容
          </button>
          <button
            class="drawer-tab"
            :class="{ active: detailTab === 'chunks' }"
            @click="detailTab = 'chunks'"
          >
            切片列表
          </button>
        </div>
        <div class="drawer-body">
          <div v-if="loadingDetail || loadingChunks" class="drawer-loading">
            <div class="spinner"></div>
          </div>
          <template v-else>
            <!-- 文档内容 Tab -->
            <div v-if="detailTab === 'content'" class="detail-content">
              <div class="detail-meta">
                <span>{{ currentDocument?.chunk_count }} 个切片</span>
                <span v-if="currentDocument?.md5">MD5: {{ currentDocument.md5.substring(0, 8) }}...</span>
              </div>
              <div class="detail-text">{{ currentDocument?.content || currentDocument?.preview }}</div>
              <div v-for="group in detailPageImages" :key="group.page" class="detail-page-group">
                <div class="detail-page-label">第 {{ group.page + 1 }} 页</div>
                <div class="detail-images">
                  <img
                    v-for="(url, i) in group.urls"
                    :key="i"
                    :src="url"
                    class="detail-image-item"
                  />
                </div>
              </div>
            </div>

            <!-- 切片列表 Tab -->
            <div v-if="detailTab === 'chunks'" class="chunks-content">
              <div class="chunks-header">
                <span>{{ chunks.length }} 个切片</span>
              </div>
              <div
                v-for="chunk in chunks"
                :key="chunk.chunk_id"
                class="chunk-item"
              >
                <div class="chunk-index">{{ chunk.index + 1 }}</div>
                <div class="chunk-body">
                  <div class="chunk-text">{{ chunk.content }}</div>
                  <div v-if="chunk._imageUrls && chunk._imageUrls.length > 0" class="chunk-images">
                    <img
                      v-for="(url, idx) in chunk._imageUrls"
                      :key="idx"
                      :src="url"
                      class="chunk-image-item"
                    />
                  </div>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// helpers: composables/useKnowledgeBase.js (formatters + shared state factory)
/**
 * KnowledgeBasePage — 知识库管理控制台
 * 上传区 + 文档表格 + 索引状态 + 详情抽屉 + 切片抽屉
 */
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showDialog } from 'vant'
import http from '../../services/http'
import { useUserStore } from '../../store/user'
import { useAuthImage } from '../../composables/useAuthImage'
import { orgApi } from '../../services/orgApi'

const router = useRouter()
const userStore = useUserStore()
const { getAllImages, resolveImageUrls } = useAuthImage()

function classifyError(error) {
  if (error.name === 'AbortError') return { type: 'timeout', message: '请求超时，请检查后端服务状态或稍后重试' }
  if (!navigator.onLine) return { type: 'network', message: '网络连接已断开，请检查网络' }
  return { type: 'network', message: '无法连接后端服务，请确认 FastAPI 已启动' }
}

// ===== State =====
const fileInput = ref(null)
const selectedFiles = ref([])
const isDragOver = ref(false)
const uploading = ref(false)
const uploadProgressList = ref([])
const uploadComplete = ref(false)
const successCount = ref(0)
const failedCount = ref(0)

const documents = ref([])
const loadingDocuments = ref(false)
const documentError = ref('')
const searchQuery = ref('')

// 空间筛选（上传归属与列表筛选分离，互不影响）
const spaces = ref([])
const uploadSpaceId = ref('')
const filterSpaceId = ref('')

const showDetail = ref(false)
const currentDocument = ref(null)
const detailTab = ref('content')
const loadingDetail = ref(false)
const detailPageImages = ref([])

const loadingChunks = ref(false)
const chunks = ref([])

// 切换到切片列表标签时自动加载切片
watch(detailTab, async (newTab) => {
  if (newTab === 'chunks' && currentDocument.value && chunks.value.length === 0) {
    await fetchDocumentChunks(currentDocument.value.filename)
    await loadChunkImages(chunks.value, currentDocument.value.md5)
  }
})

// ===== Computed =====
const filteredDocuments = computed(() => {
  if (!searchQuery.value.trim()) return documents.value
  const query = searchQuery.value.toLowerCase()
  return documents.value.filter(doc => {
    const name = (doc.original_filename || doc.filename || '').toLowerCase()
    return name.includes(query)
  })
})

const totalChunks = computed(() => {
  return documents.value.reduce((sum, doc) => sum + (doc.chunk_count || 0), 0)
})

const documentsWithImages = computed(() => {
  return documents.value.filter(doc => doc.image_count > 0).length
})

// ===== File Handling =====
function openFilePicker() {
  fileInput.value?.click()
}

function handleFileSelect(event) {
  const files = Array.from(event.target.files)
  selectedFiles.value = [...selectedFiles.value, ...files]
  event.target.value = ''
}

function handleDrop(event) {
  isDragOver.value = false
  const files = Array.from(event.dataTransfer.files)
  selectedFiles.value = [...selectedFiles.value, ...files]
}

function removeFile(index) {
  selectedFiles.value.splice(index, 1)
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function getFileType(filename) {
  if (!filename) return '未知'
  const ext = filename.split('.').pop().toLowerCase()
  const typeMap = { md: 'Markdown', txt: '文本', pdf: 'PDF', docx: 'Word', pptx: 'PPT' }
  return typeMap[ext] || ext.toUpperCase()
}

// ===== Upload =====
async function uploadFiles() {
  if (selectedFiles.value.length === 0) {
    showToast('\u8bf7\u5148\u9009\u62e9\u6587\u4ef6')
    return
  }

  const token = userStore.token
  if (!token) {
    showToast('\u8bf7\u5148\u767b\u5f55')
    router.push('/login')
    return
  }

  uploading.value = true
  uploadComplete.value = false
  uploadProgressList.value = []
  successCount.value = 0
  failedCount.value = 0

  const formData = new FormData()
  selectedFiles.value.forEach((file) => {
    formData.append('files', file)
    uploadProgressList.value.push({
      filename: file.name,
      percentage: 0,
      status: 'processing',
      message: '\u51c6\u5907\u4e0a\u4f20...'
    })
  })

  let uploadUrl = '/api/v1/knowledge/add/multiple/v2'
  if (uploadSpaceId.value) {
    uploadUrl += `?space_id=${encodeURIComponent(uploadSpaceId.value)}`
  }

  try {
    const response = await fetch(uploadUrl, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body: formData
    })
    const payload = await response.json().catch(() => ({}))

    if (!response.ok || payload.code !== 200) {
      throw new Error(payload.message || 'Upload failed')
    }

    const results = payload.data?.results || []
    for (const item of results) {
      const progress = uploadProgressList.value.find(p => p.filename === item.filename)
      if (!progress) continue

      const failed = item.status === 'error'
      progress.status = failed ? 'failed' : 'completed'
      progress.percentage = failed ? 0 : 100
      progress.message = item.message || (failed ? '\u4e0a\u4f20\u5931\u8d25' : '\u6587\u4ef6\u5df2\u4fdd\u5b58\uff0c\u7b49\u5f85\u7d22\u5f15')
    }
    successCount.value = results.filter(item => item.status !== 'error').length
    failedCount.value = results.filter(item => item.status === 'error').length
  } catch (error) {
    console.error('Upload error:', error)
    showToast(error.message || '\u4e0a\u4f20\u5931\u8d25')
    uploadProgressList.value.forEach((item) => {
      if (item.status !== 'completed') {
        item.status = 'failed'
        item.message = '\u4e0a\u4f20\u5931\u8d25'
      }
    })
    failedCount.value = uploadProgressList.value.filter(p => p.status === 'failed').length
  } finally {
    uploading.value = false
    uploadComplete.value = true
    await fetchDocuments()
    selectedFiles.value = []
  }
}

const currentOrgId = ref('')

async function loadSpaces() {
  if (!userStore.token) return
  try {
    // 先获取用户的组织
    if (!currentOrgId.value) {
      const orgsRes = await orgApi.listOrgs()
      const orgs = orgsRes.code === 200 ? (orgsRes.data?.orgs || []) : []
      if (orgs.length > 0) {
        currentOrgId.value = orgs[0].org_id || orgs[0].id
      }
    }
    if (!currentOrgId.value) {
      spaces.value = []
      return
    }
    const res = await orgApi.listSpaces(currentOrgId.value)
    const result = res.data || res
    if (result.code === 200) {
      spaces.value = result.data?.spaces || result.data || []
    }
  } catch (e) {
    spaces.value = []
  }
}

// ===== Document CRUD =====
async function fetchDocuments() {
  if (!userStore.token) {
    documentError.value = '\u672a\u767b\u5f55\uff0c\u8bf7\u5148\u767b\u5f55'
    return
  }

  loadingDocuments.value = true
  documentError.value = ''
  try {
    const queryParams = {}
    if (filterSpaceId.value) queryParams.space_id = filterSpaceId.value

    const [legacyResponse, indexResponse] = await Promise.allSettled([
      http.get('/api/v1/knowledge/list', { params: queryParams, timeout: 8000 }),
      http.get('/api/v1/knowledge/index-status', { params: queryParams, timeout: 8000 })
    ])
    const legacyResult = legacyResponse.status === 'fulfilled' ? legacyResponse.value.data : null
    const indexResult = indexResponse.status === 'fulfilled' ? indexResponse.value.data : null

    const legacyDocuments = legacyResult?.code === 200 ? (legacyResult.data?.documents || []) : []
    const indexDocuments = indexResult?.code === 200
      ? (indexResult.data?.documents || []).map(record => ({
          id: record.id,
          filename: record.filename,
          original_filename: record.filename,
          md5: record.md5,
          chunk_count: record.chunk_count || 0,
          image_count: 0,
          preview: '',
          created_at: record.created_at,
          index_status: record.status,
          index_error: record.error_message,
          retry_count: record.retry_count || 0
        }))
      : []

    if (legacyResponse.status === 'rejected' && indexResponse.status === 'rejected') {
      throw legacyResponse.reason || indexResponse.reason
    }

    const documentsByKey = new Map()
    for (const doc of legacyDocuments) {
      documentsByKey.set(doc.md5 || doc.filename, doc)
    }
    for (const doc of indexDocuments) {
      const key = doc.md5 || doc.filename
      documentsByKey.set(key, { ...documentsByKey.get(key), ...doc })
    }
    documents.value = Array.from(documentsByKey.values())
  } catch (error) {
    console.error('Fetch documents error:', error)
    if (error.response?.status === 401 || error.response?.status === 403) {
      documentError.value = '\u767b\u5f55\u5df2\u5931\u6548\uff0c\u8bf7\u91cd\u65b0\u767b\u5f55'
    } else {
      documentError.value = classifyError(error).message
    }
  } finally {
    loadingDocuments.value = false
  }
}

async function fetchDocumentDetail(filename) {
  if (!userStore.token) return null

  loadingDetail.value = true
  try {
    const res = await http.get(`/api/v1/knowledge/detail?filename=${encodeURIComponent(filename)}`, { timeout: 10000 })
    const result = res.data
    if (result.code === 200 && result.data) {
      return result.data
    }
    showToast(result.message || '获取文档详情失败')
  } catch (error) {
    console.error('Fetch document detail error:', error)
    if (error.response?.status === 401 || error.response?.status === 403) {
      showToast('登录已失效，请重新登录')
    } else {
      showToast(classifyError(error).message)
    }
  } finally {
    loadingDetail.value = false
  }
  return null
}

async function fetchDocumentChunks(filename) {
  if (!userStore.token) return

  loadingChunks.value = true
  chunks.value = []
  try {
    const res = await http.get(`/api/v1/knowledge/chunks?filename=${encodeURIComponent(filename)}`, { timeout: 10000 })
    const result = res.data
    if (result.code === 200 && result.data) {
      chunks.value = result.data.chunks || []
    } else {
      showToast(result.message || '获取切片列表失败')
    }
  } catch (error) {
    console.error('Fetch document chunks error:', error)
    if (error.response?.status === 401 || error.response?.status === 403) {
      showToast('登录已失效，请重新登录')
    } else {
      showToast(classifyError(error).message)
    }
  } finally {
    loadingChunks.value = false
  }
}

async function deleteDocumentByFilename(filename) {
  if (!userStore.token || !filename) return false

  try {
    const qs = new URLSearchParams({ filename, delete_documents: 'true' })
    const res = await http.delete(`/api/v1/knowledge/delete/filename?${qs.toString()}`)
    return res.data?.code === 200
  } catch (error) {
    console.error('Delete document error:', error)
  }
  return false
}

async function deleteDocumentById(documentId) {
  if (!userStore.token || !documentId) return false

  try {
    const res = await http.delete(`/api/v1/knowledge/documents/${encodeURIComponent(documentId)}`)
    return res.data?.code === 200
  } catch (error) {
    console.error('Delete document by id error:', error)
    return false
  }
}

async function cleanAllVectors() {
  if (!userStore.token) return

  try {
    let url = '/api/v1/knowledge/clean'
    if (filterSpaceId.value) {
      url += `?space_id=${encodeURIComponent(filterSpaceId.value)}`
    }
    await http.delete(url)
    showToast('清除成功')
    await fetchDocuments()
  } catch (error) {
    console.error('Clean vectors error:', error)
    showToast('清除失败')
  }
}

// ===== Image Handling =====
function groupImagesByPage(imagePaths, imageMap) {
  const pageMap = {}
  const pageOrder = []

  for (const path of imagePaths) {
    const filename = path.split('/').pop()
    const match = filename.match(/^p(\d+)_i/)
    const page = match ? parseInt(match[1]) : 0
    const url = resolveImageUrls([path], imageMap)[0]
    if (!url) continue

    if (!pageMap[page]) {
      pageMap[page] = { page, urls: [] }
      pageOrder.push(pageMap[page])
    }
    pageMap[page].urls.push(url)
  }
  return pageOrder
}

async function loadChunkImages(chunksList, md5) {
  if (!md5) return
  const imageMap = await getAllImages(md5)
  for (const chunk of chunksList) {
    if (chunk.images?.length) {
      chunk._imageUrls = resolveImageUrls(chunk.images, imageMap)
    }
  }
}

// ===== UI Actions =====
async function viewDocumentDetail(doc) {
  if (doc.index_status && doc.index_status !== 'indexed') {
    showToast(doc.index_status === 'index_failed'
      ? `\u6587\u6863\u7d22\u5f15\u5931\u8d25\uff1a${doc.index_error || '\u8bf7\u7a0d\u540e\u91cd\u8bd5'}`
      : '\u6587\u6863\u5df2\u4fdd\u5b58\uff0c\u6b63\u5728\u7b49\u5f85\u7d22\u5f15\u5b8c\u6210\u540e\u67e5\u770b\u5185\u5bb9\u548c\u5207\u7247')
    return
  }

  currentDocument.value = doc
  detailTab.value = 'content'
  detailPageImages.value = []

  const detail = await fetchDocumentDetail(doc.filename)
  if (detail) {
    currentDocument.value = detail
    if (detail.md5 && detail.images?.length) {
      const imageMap = await getAllImages(detail.md5)
      detailPageImages.value = groupImagesByPage(detail.images, imageMap)
    }
  }
  showDetail.value = true
}

async function viewDocumentChunks(doc) {
  if (doc.index_status && doc.index_status !== 'indexed') {
    showToast(doc.index_status === 'index_failed'
      ? `\u6587\u6863\u7d22\u5f15\u5931\u8d25\uff1a${doc.index_error || '\u8bf7\u7a0d\u540e\u91cd\u8bd5'}`
      : '\u6587\u6863\u5df2\u4fdd\u5b58\uff0c\u6b63\u5728\u7b49\u5f85\u7d22\u5f15\u5b8c\u6210\u540e\u67e5\u770b\u5185\u5bb9\u548c\u5207\u7247')
    return
  }

  currentDocument.value = doc
  detailTab.value = 'chunks'

  await fetchDocumentChunks(doc.filename)
  await loadChunkImages(chunks.value, currentDocument.value.md5)
  showDetail.value = true
}

function handleDeleteDocument(doc) {
  showDialog({
    title: '确认删除',
    message: `确定要删除文档 "${doc.original_filename || doc.filename}" 吗？此操作将同时删除向量数据。`,
    showCancelButton: true,
  }).then(async (result) => {
    if (result) {
      let success = false
      // v2 文档优先用 document_id 删除
      if (doc.id && doc.index_status) {
        success = await deleteDocumentById(doc.id)
      }
      // 旧文档回退 filename 删除
      if (!success) {
        const filename = doc.original_filename || doc.filename
        success = await deleteDocumentByFilename(filename)
      }
      if (success) {
        showToast('删除成功')
        await fetchDocuments()
      } else {
        showToast('删除失败')
      }
    }
  })
}

function handleCleanAll() {
  showDialog({
    title: '确认清除',
    message: '确定要清除全部向量数据吗？此操作不可恢复。',
    showCancelButton: true,
  }).then(async (action) => {
    if (action === 'confirm') {
      await cleanAllVectors()
    }
  })
}

function getStatusClass(status) {
  switch (status) {
    case 'completed': return 'status-success'
    case 'failed': return 'status-failed'
    default: return 'status-processing'
  }
}

function getStatusText(status) {
  switch (status) {
    case 'completed': return '完成'
    case 'failed': return '失败'
    default: return '处理中'
  }
}

function getIndexStatusText(status) {
  switch (status) {
    case 'pending_index': return '待索引'
    case 'indexing': return '索引中'
    case 'indexed': return '已索引'
    case 'index_failed': return '索引失败'
    default: return status || ''
  }
}

function getIndexStatusClass(status) {
  switch (status) {
    case 'indexed': return 'status-success'
    case 'index_failed': return 'status-failed'
    case 'indexing': return 'status-processing'
    default: return 'status-pending'
  }
}

/** 向 AI 提问：跳转到对话页并预填查询 */
function askAIAboutDoc(doc) {
  const filename = doc.original_filename || doc.filename || ''
  router.push({ path: '/chat', query: { doc: filename } })
}

async function handleReindex(doc) {
  if (!userStore.token || !doc.id) return
  try {
    const res = await http.post(`/knowledge/${doc.id}/reindex`)
    if (res.data?.code === 200) {
      showToast('已提交重新索引任务')
      await fetchDocuments()
    } else {
      showToast(res.data?.message || '重新索引失败')
    }
  } catch (error) {
    console.error('Reindex error:', error)
    showToast(error.response?.data?.detail || '重新索引失败')
  }
}

// ===== Lifecycle =====
onMounted(() => {
  loadSpaces()
  fetchDocuments()
})
</script>

<style scoped>
.kb-console {
  min-height: 100%;
  background: var(--color-bg);
}

/* ===== Toolbar ===== */
.kb-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
}

.kb-toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.kb-title {
  font-family: var(--font-heading);
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
}

.kb-doc-count {
  font-size: 13px;
  color: var(--color-text-lighter);
  background: var(--color-surface);
  padding: var(--space-xs) var(--space-md);
  border-radius: var(--radius-full);
}

/* ===== Space Selector ===== */
.space-selector-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: var(--space-lg);
}
.space-selector-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-light);
  white-space: nowrap;
}
.space-select,
.space-filter-select {
  padding: 6px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  color: var(--color-text);
  background: var(--color-card);
  outline: none;
}
.space-select:focus,
.space-filter-select:focus {
  border-color: var(--color-primary);
}

/* ===== Upload Zone ===== */
.upload-zone {
  border: 2px dashed var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-2xl);
  text-align: center;
  cursor: pointer;
  background: var(--color-card);
  transition: all 0.2s ease;
  margin-bottom: var(--space-lg);
}

.upload-zone:hover,
.upload-zone-active {
  border-color: var(--color-primary);
  background: var(--color-primary-light);
}

.upload-icon {
  color: var(--color-text-lighter);
  margin-bottom: var(--space-md);
}

.upload-text {
  font-size: 15px;
  font-weight: 500;
  margin: 0 0 var(--space-xs);
  color: var(--color-text);
}

.upload-hint {
  font-size: 13px;
  color: var(--color-text-lighter);
  margin: 0;
}

.file-input {
  display: none;
}

/* ===== Selected Files ===== */
.selected-files {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  margin-bottom: var(--space-lg);
  border: 1px solid var(--color-border-light);
}

.selected-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-md);
}

.selected-count {
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text);
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  margin-bottom: var(--space-lg);
}

.file-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-sm) var(--space-md);
  background: var(--color-surface);
  border-radius: var(--radius-md);
}

.file-info {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex: 1;
  min-width: 0;
  color: var(--color-text-light);
}

.file-name {
  font-size: 13px;
  flex: 1;
  min-width: 0;
}

.file-size {
  font-size: 12px;
  color: var(--color-text-lightest);
  flex-shrink: 0;
}

.file-remove {
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
  transition: all 0.15s ease;
}

.file-remove:hover {
  background: var(--color-error);
  color: white;
}

/* ===== Buttons ===== */
.btn-primary {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
}

.btn-primary:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.btn-primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-block {
  width: 100%;
}

.btn-danger-plain {
  padding: var(--space-xs) var(--space-md);
  background: transparent;
  color: var(--color-error);
  border: 1px solid var(--color-error);
  border-radius: var(--radius-md);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-danger-plain:hover {
  background: var(--color-error);
  color: white;
}

.btn-text {
  background: transparent;
  border: none;
  color: var(--color-primary);
  font-size: 13px;
  cursor: pointer;
  padding: 0;
}

.btn-icon-sm {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--color-text-lighter);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition: all 0.15s ease;
}

.btn-icon-sm:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

.btn-icon-sm.btn-danger:hover {
  background: var(--color-error);
  color: white;
}

/* ===== Upload Progress ===== */
.upload-progress {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  margin-bottom: var(--space-lg);
  border: 1px solid var(--color-border-light);
}

.progress-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-md);
}

.upload-result-text {
  font-size: 13px;
  color: var(--color-text-lighter);
}

.progress-item {
  padding: var(--space-md);
  background: var(--color-surface);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-sm);
}

.progress-item:last-child {
  margin-bottom: 0;
}

.progress-item-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-sm);
}

.progress-filename {
  font-size: 13px;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
}

.progress-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  flex-shrink: 0;
}

.status-processing {
  background-color: var(--status-warning-bg);
  color: var(--status-warning-text);
}

.status-success {
  background-color: var(--status-success-bg);
  color: var(--status-success-text);
}

.status-failed {
  background-color: var(--status-error-bg);
  color: var(--status-error-text);
}

.progress-bar-wrapper {
  height: 4px;
  background: var(--color-border-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
  margin-bottom: var(--space-xs);
}

.progress-bar {
  height: 100%;
  background: var(--color-primary);
  border-radius: var(--radius-sm);
  transition: width 0.3s ease;
}

.progress-message {
  font-size: 12px;
  color: var(--color-text-lighter);
  margin: 0;
}

/* ===== Index Status ===== */
.index-status {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
}

.status-card {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  text-align: center;
  border: 1px solid var(--color-border-light);
}

.status-value {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-primary);
  margin-bottom: var(--space-xs);
}

.status-label {
  font-size: 13px;
  color: var(--color-text-lighter);
}

/* ===== Document Section ===== */
.document-section {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  padding: var(--space-lg);
  border: 1px solid var(--color-border-light);
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
}

.search-input {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 13px;
  background: var(--color-surface);
  color: var(--color-text);
  outline: none;
  width: 200px;
  transition: border-color 0.15s ease;
}

.search-input:focus {
  border-color: var(--color-primary);
}

.search-input::placeholder {
  color: var(--color-text-lightest);
}

/* ===== Document Table ===== */
.doc-table {
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.doc-table-header {
  display: grid;
  grid-template-columns: 1fr 80px 80px 120px 120px;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
  background: var(--color-surface);
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-lighter);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.doc-table-row {
  display: grid;
  grid-template-columns: 1fr 80px 80px 120px 120px;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--color-border-light);
  cursor: pointer;
  transition: background 0.15s ease;
  align-items: center;
}

.doc-table-row:hover {
  background: var(--color-surface);
}

.col-name {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: 14px;
  color: var(--color-text);
  min-width: 0;
}

.col-name svg {
  flex-shrink: 0;
  color: var(--color-text-lighter);
}

.col-type,
.col-chunks {
  font-size: 13px;
  color: var(--color-text-light);
  text-align: center;
}

.col-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
}

.index-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-full);
  white-space: nowrap;
}

.index-badge.status-success {
  background-color: var(--status-success-bg);
  color: var(--status-success-text);
}

.index-badge.status-failed {
  background-color: var(--status-error-bg);
  color: var(--status-error-text);
}

.index-badge.status-processing {
  background-color: var(--status-warning-bg);
  color: var(--status-warning-text);
}

.index-badge.status-pending {
  background-color: var(--status-neutral-bg);
  color: var(--status-neutral-text);
}

.index-badge.status-legacy {
  background-color: var(--status-neutral-bg);
  color: var(--status-neutral-text);
}

.index-error-hint {
  font-size: 12px;
  cursor: help;
}

.btn-reindex {
  color: var(--color-primary) !important;
}

.btn-reindex:hover {
  background: var(--color-primary-light) !important;
  color: var(--color-primary) !important;
}

.col-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-xs);
}

/* ===== States ===== */
.loading-state,
.empty-state,
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-2xl);
  color: var(--color-text-lighter);
}

.loading-state p,
.empty-state p {
  margin-top: var(--space-md);
  font-size: 14px;
}

.error-state {
  color: var(--color-error);
}

.error-state svg {
  opacity: 0.6;
  margin-bottom: var(--space-sm);
}

.error-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
  margin: var(--space-sm) 0 var(--space-xs);
}

.error-desc {
  font-size: 13px;
  color: var(--color-text-lighter);
  text-align: center;
  max-width: 320px;
  margin-bottom: var(--space-lg);
}

.error-actions {
  display: flex;
  gap: var(--space-sm);
}

.btn-retry {
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s;
}

.btn-retry:hover {
  background: var(--color-primary-hover);
}

.btn-secondary {
  padding: var(--space-sm) var(--space-lg);
  background: transparent;
  color: var(--color-text-lighter);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-secondary:hover {
  border-color: var(--color-text-lighter);
  color: var(--color-text);
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

/* ===== Drawer ===== */
.drawer-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: var(--z-overlay);
  display: flex;
  justify-content: flex-end;
}

.drawer {
  width: 480px;
  max-width: 90vw;
  background: var(--color-card);
  display: flex;
  flex-direction: column;
  animation: slideIn 0.2s ease-out;
}

@keyframes slideIn {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg);
  border-bottom: 1px solid var(--color-border-light);
}

.drawer-header h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-tabs {
  display: flex;
  border-bottom: 1px solid var(--color-border-light);
  padding: 0 var(--space-lg);
}

.drawer-tab {
  padding: var(--space-md) var(--space-lg);
  border: none;
  background: transparent;
  font-size: 14px;
  color: var(--color-text-lighter);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: all 0.15s ease;
}

.drawer-tab:hover {
  color: var(--color-text);
}

.drawer-tab.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 500;
}

.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-lg);
}

.drawer-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-2xl);
}

/* ===== Detail Content ===== */
.detail-meta {
  display: flex;
  gap: var(--space-md);
  font-size: 12px;
  color: var(--color-text-lighter);
  margin-bottom: var(--space-lg);
  padding-bottom: var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
}

.detail-text {
  font-size: 14px;
  line-height: 1.8;
  color: var(--color-text);
  white-space: pre-wrap;
  word-wrap: break-word;
}

.detail-page-group {
  margin-top: var(--space-xl);
  padding-top: var(--space-lg);
  border-top: 1px solid var(--color-border-light);
}

.detail-page-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-primary);
  margin-bottom: var(--space-md);
}

.detail-images {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.detail-image-item {
  width: 100%;
  border-radius: var(--radius-md);
  box-shadow: 0 1px 3px var(--color-shadow);
}

/* ===== Chunks Content ===== */
.chunks-header {
  font-size: 13px;
  color: var(--color-text-lighter);
  margin-bottom: var(--space-lg);
  padding-bottom: var(--space-md);
  border-bottom: 1px solid var(--color-border-light);
}

.chunk-item {
  display: flex;
  gap: var(--space-md);
  padding-bottom: var(--space-lg);
  margin-bottom: var(--space-lg);
  border-bottom: 1px dashed var(--color-border-light);
}

.chunk-item:last-child {
  border-bottom: none;
  margin-bottom: 0;
  padding-bottom: 0;
}

.chunk-index {
  min-width: 28px;
  height: 28px;
  background: var(--color-primary);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 500;
  flex-shrink: 0;
}

.chunk-body {
  flex: 1;
  min-width: 0;
}

.chunk-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-light);
  white-space: pre-wrap;
  word-break: break-all;
}

.chunk-images {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  margin-top: var(--space-md);
}

.chunk-image-item {
  width: 100%;
  border-radius: var(--radius-md);
}

/* ===== Utility ===== */
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== Responsive ===== */
@media (max-width: 767px) {
  .index-status {
    grid-template-columns: 1fr;
  }

  .doc-table-header,
  .doc-table-row {
    grid-template-columns: 1fr 60px 100px;
  }

  .col-type,
  .col-status {
    display: none;
  }

  .search-input {
    width: 140px;
  }

  .drawer {
    width: 100%;
    max-width: 100vw;
  }
}
</style>
