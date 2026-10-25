<template>
  <div class="analysis-home">
    <header class="analysis-header">
      <div>
        <h1>文档分析</h1>
        <p>分析一篇笔记或知识库文档，查看可追溯的总结、章节与思维导图。</p>
      </div>
      <div class="header-actions">
        <input
          v-model="query"
          class="search-input"
          placeholder="搜索笔记标题或文件名…"
          @keyup.enter="fetchAll"
        />
        <button class="ghost-btn" @click="fetchAll">搜索</button>
        <button class="ghost-btn" @click="router.push('/knowledge')">上传新文档</button>
      </div>
    </header>

    <div v-if="loading" class="state-card">
      <div class="spinner"></div>
      <p>加载分析来源…</p>
    </div>
    <div v-else-if="errorMessage" class="state-card error">
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="fetchAll">重试</button>
    </div>

    <template v-else>
      <section class="block">
        <div class="block-head">
          <h2>选择要分析的内容</h2>
          <div class="tabs">
            <button :class="{ active: tab === 'note' }" @click="tab = 'note'">
              笔记 ({{ notes.length }})
            </button>
            <button :class="{ active: tab === 'document' }" @click="tab = 'document'">
              知识库文档 ({{ documents.length }})
            </button>
          </div>
        </div>

        <div v-if="currentSources.length === 0" class="empty-card">
          <p>{{ tab === 'note' ? '还没有可分析的笔记。' : '知识库里还没有文档。' }}</p>
          <button class="primary-btn" @click="router.push(tab === 'note' ? '/notes/new' : '/knowledge')">
            {{ tab === 'note' ? '新建笔记' : '去知识库上传' }}
          </button>
        </div>

        <div v-else class="source-grid">
          <button
            v-for="source in currentSources"
            :key="`${source.source_type}-${source.source_id}`"
            class="source-card"
            @click="openAnalysis(source)"
          >
            <span class="source-type">{{ source.resource_type || (source.source_type === 'note' ? '笔记' : '文档') }}</span>
            <strong>{{ source.title }}</strong>
            <small>
              {{ source.category || source.file_type || '未分类' }}
              <span v-if="source.updated_at"> · {{ formatTime(source.updated_at) }}</span>
            </small>
            <span class="source-action">分析 →</span>
          </button>
        </div>
      </section>

      <section class="block">
        <div class="block-head">
          <h2>最近分析</h2>
          <button class="link-btn" @click="fetchAll">刷新</button>
        </div>
        <div v-if="recent.length === 0" class="empty-card">
          <p>还没有分析记录。选择上方任一内容开始。</p>
        </div>
        <div v-else class="recent-list">
          <div v-for="item in recent" :key="item.job_id" class="recent-item">
            <div class="recent-main">
              <strong>{{ item.title || item.source_id }}</strong>
              <small>{{ item.resource_type }} · {{ formatTime(item.finished_at || item.created_at) }}</small>
            </div>
            <span class="status-pill" :class="statusClass(item.status)">{{ statusText(item.status) }}</span>
            <p v-if="item.error" class="recent-error">{{ item.error }}</p>
            <button class="ghost-btn" @click="openRecent(item)">
              打开
            </button>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { insightApi } from '../../services/insightApi'

const router = useRouter()
const loading = ref(true)
const errorMessage = ref('')
const query = ref('')
const tab = ref('note')
const notes = ref([])
const documents = ref([])
const recent = ref([])

const currentSources = computed(() => (tab.value === 'note' ? notes.value : documents.value))

function formatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

function statusText(status) {
  return {
    queued: '排队中', running: '分析中', succeeded: '已生成',
    partial: '部分结果', failed: '失败', stale: '已过期',
  }[status] || status
}

function statusClass(status) {
  return { succeeded: 'ok', partial: 'warn', failed: 'error', stale: 'error', running: 'warn', queued: 'muted' }[status] || 'muted'
}

function openAnalysis(source) {
  router.push(`/analysis/${source.source_type}/${source.source_id}`)
}

function openRecent(item) {
  router.push(`/analysis/${item.source_type}/${item.source_id}`)
}

async function fetchAll() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [noteData, docData, recentData] = await Promise.all([
      insightApi.listSources({ source_type: 'note', q: query.value.trim() || undefined, limit: 30 }),
      insightApi.listSources({ source_type: 'document', q: query.value.trim() || undefined, limit: 30 }),
      insightApi.recentAnalyses(30),
    ])
    notes.value = noteData.sources || []
    documents.value = docData.sources || []
    recent.value = recentData.items || []
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '分析首页加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(fetchAll)
</script>

<style scoped>
.analysis-home { max-width: 1180px; margin: 0 auto; color: var(--color-text); }
.analysis-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 20px; }
.analysis-header h1 { margin: 0 0 4px; font-size: 24px; }
.analysis-header p { margin: 0; color: var(--color-text-light); font-size: 13px; }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.search-input { border: 1px solid var(--color-border); border-radius: 8px; padding: 8px 10px; background: var(--color-card); color: var(--color-text); min-width: 220px; }
.ghost-btn, .primary-btn { border-radius: 8px; padding: 8px 12px; font-size: 13px; cursor: pointer; }
.ghost-btn { border: 1px solid var(--color-border); background: transparent; color: var(--color-text); }
.primary-btn { border: none; background: var(--color-primary); color: #fff; }
.block { margin-bottom: 24px; }
.block-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 10px; }
.block-head h2 { font-size: 16px; margin: 0; }
.tabs { display: flex; gap: 6px; }
.tabs button { border: 1px solid var(--color-border); background: transparent; color: var(--color-text); border-radius: 999px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
.tabs button.active { border-color: var(--color-primary); color: var(--color-primary); background: color-mix(in srgb, var(--color-primary) 8%, transparent); }
.source-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
.source-card { display: flex; flex-direction: column; gap: 5px; align-items: flex-start; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-card); color: var(--color-text); padding: 13px 14px; cursor: pointer; text-align: left; }
.source-card:hover { border-color: var(--color-primary); }
.source-type { font-size: 11px; color: var(--color-primary); }
.source-card strong { font-size: 14px; }
.source-card small { color: var(--color-text-light); font-size: 11px; }
.source-action { margin-top: 4px; font-size: 12px; color: var(--color-primary); }
.recent-list { display: flex; flex-direction: column; gap: 6px; }
.recent-item { display: flex; align-items: center; gap: 10px; border: 1px solid var(--color-border); border-radius: 9px; background: var(--color-card); padding: 10px 12px; flex-wrap: wrap; }
.recent-main { flex: 1; min-width: 180px; display: flex; flex-direction: column; gap: 2px; }
.recent-main small { color: var(--color-text-light); font-size: 11px; }
.recent-error { width: 100%; margin: 0; color: var(--color-error); font-size: 11px; }
.status-pill { border-radius: 999px; padding: 2px 9px; font-size: 11px; border: 1px solid var(--color-border); color: var(--color-text-light); }
.status-pill.ok { color: #047857; border-color: rgba(4,120,87,.35); }
.status-pill.warn { color: #b45309; border-color: rgba(180,83,9,.35); }
.status-pill.error { color: var(--color-error); border-color: rgba(185,28,28,.35); }
.empty-card, .state-card { border: 1px dashed var(--color-border); border-radius: 10px; padding: 28px 20px; text-align: center; color: var(--color-text-light); }
.state-card.error { color: var(--color-error); }
.spinner { width: 24px; height: 24px; margin: 0 auto 10px; border: 2px solid var(--color-border); border-top-color: var(--color-primary); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.link-btn { border: none; background: transparent; color: var(--color-primary); font-size: 12px; cursor: pointer; }
</style>
