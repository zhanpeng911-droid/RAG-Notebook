<template>
  <div class="insight-page">
    <header class="insight-header">
      <button class="icon-btn" title="返回" @click="goBack">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <div class="title-block">
        <h1 class="insight-title">{{ title || (isNote ? '笔记洞察' : '文档洞察') }}</h1>
        <div class="title-meta">
          <span v-if="status === 'ready'" class="badge badge-ok">可核验摘要</span>
          <span v-else-if="status === 'stale'" class="badge badge-warn">源已更新，摘要失效</span>
          <span v-else class="badge badge-muted">尚未生成</span>
          <span v-if="artifact?.meta?.anchor_count" class="meta-text">
            {{ artifact.meta.anchor_count }} 个原文锚点
          </span>
        </div>
      </div>
      <button
        class="primary-btn"
        :disabled="generating"
        @click="generate(!!artifact)"
      >
        {{ generating ? '生成中…' : (artifact ? '重新生成' : '生成摘要') }}
      </button>
    </header>

    <div v-if="loading" class="state-card">
      <div class="spinner"></div>
      <p>加载洞察…</p>
    </div>

    <div v-else-if="errorMessage" class="state-card state-error">
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="reload">重试</button>
    </div>

    <div v-else-if="status !== 'ready'" class="state-card">
      <h2>{{ status === 'stale' ? '旧摘要已失效' : '还没有结构化摘要' }}</h2>
      <p>
        {{ status === 'stale'
          ? '源文档已更新或删除旧代次，旧摘要与证据不再展示。请重新生成。'
          : '系统会基于原文锚点生成概览、章节摘要、关键概念与可核验证据。' }}
      </p>
      <button class="primary-btn" :disabled="generating" @click="generate(false)">
        {{ generating ? '生成中…' : '立即生成' }}
      </button>
    </div>

    <template v-else>
      <nav class="tab-bar">
        <button :class="{ active: tab === 'overview' }" @click="tab = 'overview'">概览与证据</button>
        <button :class="{ active: tab === 'mindmap' }" @click="tab = 'mindmap'">思维导图</button>
      </nav>

      <section v-if="tab === 'overview'" class="insight-content">
        <div v-if="trustStatus === 'insufficient_evidence'" class="warning-banner">
          本次生成没有取得足够的可核验原文，事实性结论未被展示。请检查文档解析结果后重试。
        </div>

        <article v-if="artifact.overview.text" class="panel">
          <h2>全文概览</h2>
          <p class="claim-text">
            {{ artifact.overview.text }}
            <EvidenceChips :items="evidence.overview" @open="openAnchor" />
          </p>
        </article>

        <article v-if="artifact.key_points.length" class="panel">
          <h2>关键结论</h2>
          <ul class="claim-list">
            <li v-for="point in artifact.key_points" :key="point.id">
              <span>{{ point.text }}</span>
              <EvidenceChips :items="evidence[point.id]" @open="openAnchor" />
            </li>
          </ul>
        </article>

        <article v-for="section in artifact.sections" :key="section.id" class="panel">
          <h2>{{ section.title }}</h2>
          <p v-if="section.summary" class="claim-text">
            {{ section.summary }}
            <EvidenceChips :items="evidence[section.id]" @open="openAnchor" />
          </p>
          <ul v-if="section.claims.length" class="claim-list">
            <li v-for="claim in section.claims" :key="claim.id">
              <span>{{ claim.text }}</span>
              <EvidenceChips :items="evidence[claim.id]" @open="openAnchor" />
            </li>
          </ul>
        </article>

        <article v-if="artifact.terms.length" class="panel">
          <h2>关键概念</h2>
          <div class="term-grid">
            <div v-for="term in artifact.terms" :key="term.id" class="term-card">
              <strong>{{ term.name }}</strong>
              <p v-if="term.description">{{ term.description }}</p>
              <EvidenceChips :items="evidence[term.id]" @open="openAnchor" />
            </div>
          </div>
        </article>

        <article v-if="artifact.open_questions.length" class="panel">
          <h2>待确认的问题</h2>
          <ul class="claim-list">
            <li v-for="question in artifact.open_questions" :key="question.id">
              <span>{{ question.text }}</span>
              <EvidenceChips :items="evidence[question.id]" @open="openAnchor" />
            </li>
          </ul>
        </article>
      </section>

      <section v-else class="insight-content">
        <div v-if="mindmap" class="panel mindmap-panel">
          <div class="mindmap-meta">
            <span>{{ mindmap.node_count }} 个节点</span>
            <span>最大深度 {{ mindmap.max_depth }}</span>
            <span class="hint">带蓝点的节点可查看原文证据</span>
          </div>
          <MindmapTree :node="mindmap.root" :selected-id="selectedMindmapId" @select="selectMindmapNode" />
        </div>
        <div v-else class="state-card">
          <p>摘要生成后会自动生成思维导图。</p>
        </div>
      </section>
    </template>

    <div v-if="anchorDetail" class="drawer-overlay" @click="anchorDetail = null">
      <aside class="anchor-drawer" @click.stop>
        <header>
          <h3>原文证据</h3>
          <button class="icon-btn" @click="anchorDetail = null">✕</button>
        </header>
        <div class="anchor-locator">
          <span v-if="anchorDetail.page_no">第 {{ anchorDetail.page_no }} 页</span>
          <span v-if="anchorDetail.heading_path">{{ anchorDetail.heading_path }}</span>
          <span v-if="anchorDetail.char_start !== null && anchorDetail.char_start !== undefined">
            字符 {{ anchorDetail.char_start }}–{{ anchorDetail.char_end }}
          </span>
          <span>定位方式：{{ locatorText(anchorDetail.locator_kind) }}</span>
        </div>
        <blockquote class="anchor-quote">{{ anchorDetail.quote }}</blockquote>
        <p class="anchor-hash">引文指纹：{{ anchorDetail.quote_hash }}</p>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'
import { insightApi } from '../../services/insightApi'
import EvidenceChips from '../../components/EvidenceChips.vue'
import MindmapTree from '../../components/MindmapTree.vue'

const route = useRoute()
const router = useRouter()
const documentId = computed(() => String(route.params.id || ''))
const isNote = computed(() => route.meta.sourceType === 'note')

const loading = ref(true)
const generating = ref(false)
const errorMessage = ref('')
const title = ref('')
const status = ref('not_generated')
const artifact = ref(null)
const evidence = ref({})
const mindmap = ref(null)
const tab = ref('overview')
const anchorDetail = ref(null)
const selectedMindmapId = ref('')

let pollTimer = null
let pollAttempts = 0

const trustStatus = computed(() => artifact.value?.trust_status || 'verified')

function locatorText(kind) {
  return {
    page: '页码 + 原文',
    char: '字符偏移 + 原文',
    section: '章节 + 原文',
    chunk: '原文片段',
  }[kind] || kind
}

function goBack() {
  router.push(isNote.value ? `/notes/${documentId.value}` : '/knowledge')
}

async function fetchInsight() {
  return isNote.value
    ? insightApi.getNoteInsight(documentId.value)
    : insightApi.getDocumentInsight(documentId.value)
}

async function fetchMindmap() {
  return isNote.value
    ? insightApi.getNoteMindmap(documentId.value)
    : insightApi.getDocumentMindmap(documentId.value)
}

async function submitGeneration(force) {
  return isNote.value
    ? insightApi.generateNote(documentId.value, { force })
    : insightApi.generateDocument(documentId.value, { force })
}

async function reload() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await fetchInsight()
    title.value = data.title || title.value
    status.value = data.status
    artifact.value = data.artifact || null
    evidence.value = data.evidence || {}
    if (data.status === 'ready') {
      const mind = await fetchMindmap()
      mindmap.value = mind.status === 'ready' ? mind.mindmap : null
    } else {
      mindmap.value = null
    }
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '洞察加载失败，请稍后重试'
  } finally {
    loading.value = false
  }
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

async function pollJob(jobId) {
  pollAttempts += 1
  try {
    const job = await insightApi.getJob(jobId)
    if (job.status === 'succeeded') {
      stopPolling()
      generating.value = false
      await reload()
      showToast('摘要已生成')
      return
    }
    if (job.status === 'failed' || job.status === 'stale') {
      stopPolling()
      generating.value = false
      errorMessage.value = job.error || '生成失败，请重试'
      return
    }
    if (pollAttempts >= 80) {
      stopPolling()
      generating.value = false
      showToast('生成仍在后台进行，可稍后刷新查看')
      return
    }
    pollTimer = setTimeout(() => pollJob(jobId), 1500)
  } catch (error) {
    stopPolling()
    generating.value = false
    errorMessage.value = '任务状态查询失败，请稍后刷新'
  }
}

async function generate(force) {
  if (generating.value) return
  generating.value = true
  errorMessage.value = ''
  pollAttempts = 0
  try {
    const result = await submitGeneration(force)
    if (result.already_generated) {
      generating.value = false
      await reload()
      showToast('已存在当前版本的可信摘要')
      return
    }
    if (result.status === 'succeeded') {
      generating.value = false
      await reload()
      return
    }
    pollTimer = setTimeout(() => pollJob(result.job_id), 800)
  } catch (error) {
    generating.value = false
    errorMessage.value = error.response?.data?.detail || '任务提交失败，请稍后重试'
  }
}

async function openAnchor(anchorId) {
  try {
    anchorDetail.value = await insightApi.getAnchor(anchorId)
  } catch (error) {
    showToast(error.response?.data?.detail || '证据已失效或不可访问')
  }
}

function selectMindmapNode(node) {
  selectedMindmapId.value = node.id
  if (node.anchor_ids && node.anchor_ids.length) {
    openAnchor(node.anchor_ids[0])
  }
}

onMounted(reload)
onBeforeUnmount(stopPolling)
</script>

<style scoped>
.insight-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 20px 16px 80px;
  color: var(--color-text);
}
.insight-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 18px;
}
.title-block { flex: 1; min-width: 0; }
.insight-title {
  font-size: 21px;
  margin: 0 0 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.title-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.meta-text { font-size: 12px; color: var(--color-text-light); }
.badge {
  font-size: 12px;
  border-radius: 999px;
  padding: 2px 10px;
  border: 1px solid transparent;
}
.badge-ok { color: #0f766e; background: rgba(16, 185, 129, 0.12); border-color: rgba(16, 185, 129, 0.35); }
.badge-warn { color: #b45309; background: rgba(245, 158, 11, 0.12); border-color: rgba(245, 158, 11, 0.35); }
.badge-muted { color: var(--color-text-light); background: rgba(148, 163, 184, 0.12); }
.icon-btn {
  border: 1px solid var(--color-border);
  background: var(--color-bg-elevated, #fff);
  color: var(--color-text);
  border-radius: 10px;
  width: 36px;
  height: 36px;
  cursor: pointer;
}
.primary-btn {
  border: none;
  border-radius: 10px;
  background: var(--color-primary);
  color: #fff;
  padding: 9px 16px;
  font-size: 14px;
  cursor: pointer;
}
.primary-btn:disabled { opacity: 0.6; cursor: default; }
.state-card {
  border: 1px solid var(--color-border);
  border-radius: 14px;
  padding: 36px 24px;
  text-align: center;
  background: var(--color-bg-elevated, #fff);
}
.state-error { color: #b91c1c; }
.tab-bar {
  display: flex;
  gap: 8px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 16px;
}
.tab-bar button {
  border: none;
  background: transparent;
  color: var(--color-text-light);
  padding: 10px 14px;
  font-size: 14px;
  cursor: pointer;
  border-bottom: 2px solid transparent;
}
.tab-bar button.active {
  color: var(--color-primary);
  border-bottom-color: var(--color-primary);
  font-weight: 600;
}
.insight-content { display: flex; flex-direction: column; gap: 14px; }
.panel {
  border: 1px solid var(--color-border);
  border-radius: 14px;
  padding: 18px 20px;
  background: var(--color-bg-elevated, #fff);
}
.panel h2 { font-size: 16px; margin: 0 0 10px; }
.claim-text { margin: 0; line-height: 1.75; }
.claim-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; line-height: 1.7; }
.term-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.term-card { border: 1px solid var(--color-border); border-radius: 10px; padding: 10px 12px; }
.term-card p { margin: 6px 0; font-size: 13px; color: var(--color-text-light); }
.warning-banner {
  border: 1px solid rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.1);
  color: #92400e;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13px;
}
.mindmap-panel { overflow-x: auto; }
.mindmap-meta { display: flex; gap: 14px; font-size: 12px; color: var(--color-text-light); margin-bottom: 12px; }
.drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.anchor-drawer {
  width: min(460px, 92vw);
  height: 100%;
  background: var(--color-bg-elevated, #fff);
  padding: 18px 20px;
  overflow-y: auto;
  box-shadow: -8px 0 30px rgba(15, 23, 42, 0.2);
}
.anchor-drawer header { display: flex; justify-content: space-between; align-items: center; }
.anchor-locator { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--color-text-light); margin: 10px 0 14px; }
.anchor-quote {
  margin: 0;
  padding: 14px;
  border-left: 3px solid var(--color-primary);
  background: rgba(30, 64, 175, 0.06);
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
.anchor-hash { font-size: 11px; color: var(--color-text-lightest, #94a3b8); word-break: break-all; }
.spinner {
  width: 26px; height: 26px; margin: 0 auto 12px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
