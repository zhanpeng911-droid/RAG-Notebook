<template>
  <div class="audit-page">
    <!-- 筛选栏 -->
    <div class="filter-bar">
      <select v-model="selectedOrgId" class="filter-select" @change="loadLogs(1)">
        <option value="">全部组织</option>
        <option v-for="org in orgs" :key="org.id" :value="org.id">{{ org.name }}</option>
      </select>
      <select v-model="filters.actionType" class="filter-select" @change="loadLogs(1)">
        <option value="">全部操作</option>
        <option value="create">创建</option>
        <option value="update">更新</option>
        <option value="delete">删除</option>
        <option value="login">登录</option>
        <option value="upload">上传</option>
      </select>
      <input
        v-model="filters.keyword"
        type="text"
        class="filter-search"
        placeholder="搜索关键词..."
        @keydown.enter="loadLogs(1)"
      />
      <van-button size="small" type="primary" @click="loadLogs(1)">搜索</van-button>
    </div>

    <div v-if="loading" class="loading-state">
      <van-loading type="spinner" />
    </div>

    <div v-else-if="logs.length === 0" class="empty-state">
      <van-empty description="暂无审计日志" />
    </div>

    <!-- 日志列表 -->
    <div v-else class="log-list">
      <div v-for="log in logs" :key="log.id" class="log-item">
        <div class="log-header">
          <span class="log-action-tag" :class="'action-' + (log.action || 'default')">
            {{ actionLabel(log.action) }}
          </span>
          <span class="log-time">{{ formatRelativeTime(log.created_at) }}</span>
        </div>
        <div class="log-body">
          <span class="log-user">{{ log.user_id || '系统' }}</span>
          <span class="log-detail">{{ formatDetail(log.detail) }}</span>
        </div>
        <div v-if="log.resource_type" class="log-resource">
          {{ log.resource_type }}{{ log.resource_id ? ' #' + log.resource_id.substring(0, 8) : '' }}
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="total > pageSize" class="pagination">
      <van-pagination
        v-model="currentPage"
        :total-items="total"
        :page-size="pageSize"
        mode="simple"
        @change="loadLogs"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { showToast } from 'vant'
import { orgApi } from '../services/orgApi'

const loading = ref(false)
const logs = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = 20
const orgs = ref([])
const selectedOrgId = ref('')

const filters = reactive({
  actionType: '',
  keyword: '',
})

const actionLabel = (type) => {
  const map = {
    create: '创建', update: '更新', delete: '删除',
    login: '登录', upload: '上传', invite: '邀请',
  }
  return map[type] || type || '操作'
}

function formatDetail(detail) {
  if (!detail) return '—'
  if (typeof detail === 'string') return detail
  if (typeof detail === 'object') {
    const parts = []
    if (detail.name) parts.push(detail.name)
    if (detail.filename) parts.push(detail.filename)
    if (parts.length > 0) return parts.join('：')
    return JSON.stringify(detail)
  }
  return String(detail)
}

function formatRelativeTime(dateStr) {
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

async function loadOrgs() {
  try {
    const res = await orgApi.listOrgs()
    orgs.value = res.code === 200 ? (res.data?.orgs || []) : []
    orgs.value.forEach(org => { if (org.org_id && !org.id) org.id = org.org_id })
    if (orgs.value.length > 0 && !selectedOrgId.value) {
      selectedOrgId.value = orgs.value[0].id
    }
  } catch (e) {
    console.error('加载组织列表失败:', e)
  }
}

async function loadLogs(page = 1) {
  currentPage.value = page
  loading.value = true
  try {
    const params = { page, page_size: pageSize }
    if (selectedOrgId.value) params.org_id = selectedOrgId.value
    if (filters.actionType) params.action = filters.actionType
    if (filters.keyword.trim()) params.keyword = filters.keyword.trim()

    const res = await orgApi.getAuditLogs(params)
    if (res.code === 200 && res.data) {
      logs.value = res.data.logs || res.data.items || res.data || []
      total.value = res.data.total || logs.value.length
    }
  } catch (e) {
    console.error('加载审计日志失败:', e)
    showToast('加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadOrgs()
  if (selectedOrgId.value) await loadLogs()
})
</script>

<style scoped>
.audit-page {
  min-height: 100%;
  padding: var(--space-lg);
  background: var(--color-bg);
}
.filter-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.filter-select {
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-card);
}
.filter-search {
  flex: 1;
  min-width: 120px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-card);
  outline: none;
}
.filter-search:focus { border-color: var(--color-primary); }
.loading-state { display: flex; justify-content: center; padding: 40px; }
.log-list { display: flex; flex-direction: column; gap: 8px; }
.log-item {
  background: var(--color-card);
  border-radius: 10px;
  padding: 14px 16px;
  border: 1px solid var(--color-border-light);
}
.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.log-action-tag {
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 10px;
  font-weight: 500;
}
.action-create { background: rgba(34,160,96,0.1); color: #22a060; }
.action-delete { background: rgba(239,68,68,0.1); color: #ef4444; }
.action-update { background: rgba(59,130,246,0.1); color: #3b82f6; }
.action-login { background: rgba(232,163,61,0.1); color: #e8a33d; }
.action-upload { background: rgba(105,183,255,0.1); color: #3f8cff; }
.action-default { background: var(--color-surface); color: var(--color-text-lighter); }
.log-time { font-size: 12px; color: var(--color-text-lightest); }
.log-body { font-size: 14px; color: var(--color-text); line-height: 1.5; }
.log-user { font-weight: 500; margin-right: 8px; }
.log-detail { color: var(--color-text-light); }
.log-resource {
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text-lighter);
  background: var(--color-surface);
  padding: 4px 8px;
  border-radius: 4px;
}
.pagination { margin-top: 16px; display: flex; justify-content: center; }
</style>
