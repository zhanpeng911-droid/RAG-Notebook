<template>
  <div class="analysis-workspace">
    <header class="workspace-header">
      <button class="icon-btn" title="返回" @click="goBack">←</button>
      <div class="workspace-title">
        <h1>{{ sourceTitle || (isNote ? '笔记分析' : '文档分析') }}</h1>
        <div class="workspace-meta">
          <span class="type-badge">{{ isNote ? '笔记' : '知识库文档' }}</span>
          <span class="status-badge" :class="statusClass">{{ statusLabel }}</span>
          <span v-if="state.job?.model_id" class="model-text">{{ state.job.model_id }}</span>
        </div>
      </div>
      <div class="workspace-actions">
        <button class="ghost-btn" :disabled="generating" @click="generate(!!artifact)">
          {{ generating ? '生成中…' : (artifact ? '重新分析' : '生成分析') }}
        </button>
        <button class="ghost-btn" :disabled="!artifact" @click="openSavePreview">保存为笔记</button>
        <button class="ghost-btn" :disabled="!artifact" @click="exportMarkdown">导出 Markdown</button>
        <button class="ghost-btn" @click="router.push(isNote ? `/notes/${sourceId}` : `/knowledge` )">返回{{ isNote ? '笔记' : '知识库' }}</button>
      </div>
    </header>

    <div v-if="loading" class="state-block">
      <div class="spinner"></div>
      <p>正在加载原文与分析状态…</p>
    </div>
    <div v-else-if="errorMessage" class="state-block error">
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="loadAll">重试</button>
    </div>
    <div v-else ref="bodyRef" class="workspace-body" :style="bodyStyle">
      <section v-show="isDesktop || mobileTab === 'source'" class="source-panel">
        <div class="panel-head">
          <h2>原文</h2>
          <div class="panel-actions">
            <button v-if="fileUrl && isPdf" class="mini-btn" @click="togglePdf">
              {{ showPdf ? '提取文本' : '原文件预览' }}
            </button>
            <button v-if="fileUrl" class="mini-btn" @click="openOriginalFile">
              原文件
            </button>
          </div>
        </div>
        <div ref="sourceScrollRef" class="source-scroll">
          <iframe v-if="showPdf && pdfObjectUrl" :src="pdfSrc" class="pdf-frame" title="原文件预览"></iframe>
          <template v-else>
            <div v-if="truncated" class="empty-text">原文较长，当前仅显示前 500 个片段。</div>
            <div v-if="segments.length === 0" class="empty-text">原文暂不可用，请检查解析状态。</div>
            <section v-for="group in segmentGroups" :key="group.key" class="segment-group">
              <h3>{{ group.label }}</h3>
              <p
                v-for="segment in group.segments"
                :id="`anchor-${segment.anchor_id}`"
                :key="segment.anchor_id"
                class="segment"
                :class="{ active: activeAnchorId === segment.anchor_id }"
              >
                {{ segment.text }}
              </p>
            </section>
          </template>
        </div>
      </section>

      <div v-if="isDesktop" class="splitter" @pointerdown="startResize"></div>

      <section v-show="isDesktop || mobileTab !== 'source'" class="result-panel">
        <nav class="result-tabs">
          <button :class="{ active: resultTab === 'summary' }" @click="resultTab = 'summary'">总结</button>
          <button :class="{ active: resultTab === 'mindmap' }" @click="resultTab = 'mindmap'">思维导图</button>
        </nav>

        <div class="result-scroll">
          <div v-if="generating || state.status === 'queued' || state.status === 'running'" class="task-banner" role="status">
            <div class="spinner"></div>
            <h2>{{ state.status === 'queued' ? '排队中' : '正在分析' }}</h2>
            <p>{{ statusLabel }}</p>
            <p class="hint">任务已持久化，刷新页面会从服务端恢复进度。</p>
            <p v-if="artifact" class="hint">下方保留上一次分析结果；新结果完成后会自动更新。</p>
          </div>

          <div v-if="state.status === 'failed' || state.status === 'stale'" class="task-banner error" role="alert">
            <h2>{{ state.status === 'stale' ? '结果已过期' : '分析失败' }}</h2>
            <p>{{ state.job?.error || '来源已更新，请重新分析。' }}</p>
            <button class="primary-btn" @click="generate(true)">重新分析</button>
          </div>

          <div v-if="pollWarning" class="warning-banner">{{ pollWarning }} <button class="mini-btn" @click="loadAll">刷新状态</button></div>

          <div v-if="!artifact && !generating && !['failed', 'stale'].includes(state.status)" class="state-block">
            <h2>还没有分析结果</h2>
            <p>基于原文锚点生成概览、章节摘要、关键概念与思维导图。</p>
            <button class="primary-btn" @click="generate(false)">生成分析</button>
          </div>

          <template v-if="artifact">
            <div v-if="artifact.trust_status === 'insufficient_evidence'" class="warning-banner">
              本次生成没有取得足够的可核验原文，事实性结论未被展示。请检查解析结果后重试。
            </div>

            <template v-if="resultTab === 'summary'">
              <article v-if="artifact.overview?.text" class="analysis-card">
                <h2>概览</h2>
                <p>{{ artifact.overview.text }}</p>
                <EvidenceChips :items="evidence.overview" @open="openAnchor" />
              </article>

              <article v-if="artifact.key_points?.length" class="analysis-card">
                <h2>关键结论</h2>
                <ul class="claim-list">
                  <li v-for="point in artifact.key_points" :key="point.id">
                    <span>{{ point.text }}</span>
                    <EvidenceChips :items="evidence[point.id]" @open="openAnchor" />
                  </li>
                </ul>
              </article>

              <article v-for="section in artifact.sections" :key="section.id" class="analysis-card">
                <h2>{{ section.title }}</h2>
                <p v-if="section.summary">
                  {{ section.summary }}
                  <EvidenceChips :items="evidence[section.id]" @open="openAnchor" />
                </p>
                <ul v-if="section.claims?.length" class="claim-list">
                  <li v-for="claim in section.claims" :key="claim.id">
                    <span>{{ claim.text }}</span>
                    <EvidenceChips :items="evidence[claim.id]" @open="openAnchor" />
                  </li>
                </ul>
              </article>

              <article v-if="artifact.terms?.length" class="analysis-card">
                <h2>关键概念</h2>
                <div class="term-grid">
                  <div v-for="term in artifact.terms" :key="term.id" class="term-card">
                    <strong>{{ term.name }}</strong>
                    <p v-if="term.description">{{ term.description }}</p>
                    <EvidenceChips :items="evidence[term.id]" @open="openAnchor" />
                  </div>
                </div>
              </article>

              <article v-if="artifact.open_questions?.length" class="analysis-card">
                <h2>待思考的问题</h2>
                <ul class="claim-list">
                  <li v-for="question in artifact.open_questions" :key="question.id">
                    <span>{{ question.text }}</span>
                    <EvidenceChips :items="evidence[question.id]" @open="openAnchor" />
                  </li>
                </ul>
              </article>

              <p v-if="artifact.meta?.coverage?.partial" class="coverage-note">
                部分内容已分析：{{ artifact.meta.coverage.analyzed_blocks }}/{{ artifact.meta.coverage.total_blocks }} 段。
                <span v-if="artifact.meta.coverage.missing_blocks?.length">
                  未覆盖：{{ artifact.meta.coverage.missing_blocks.join('、') }}
                </span>
              </p>
            </template>

            <template v-else>
              <div v-if="mindmap" class="mindmap-wrap">
                <div class="mindmap-mode">
                  <button :class="{ active: mindmapMode === 'canvas' }" @click="mindmapMode = 'canvas'">画布</button>
                  <button :class="{ active: mindmapMode === 'outline' }" @click="mindmapMode = 'outline'">大纲</button>
                </div>
                <MindmapCanvas
                  v-if="mindmapMode === 'canvas'"
                  :mindmap="mindmap"
                  :revision="revision"
                  :source-type="sourceType"
                  :source-id="sourceId"
                  @anchor="openAnchor"
                  @saved="onMindmapSaved"
                />
                <div v-else class="outline-wrap">
                  <MindmapTree :node="mindmap.root" @select="onOutlineSelect" />
                </div>
              </div>
              <div v-else class="state-block">
                <p>摘要生成后会自动生成思维导图。</p>
              </div>
            </template>
          </template>
        </div>
      </section>
    </div>

    <nav v-if="!isDesktop" class="mobile-tabs">
      <button :class="{ active: mobileTab === 'source' }" @click="mobileTab = 'source'">原文</button>
      <button :class="{ active: mobileTab === 'summary' }" @click="mobileTab = 'summary'; resultTab = 'summary'">总结</button>
      <button :class="{ active: mobileTab === 'mindmap' }" @click="mobileTab = 'mindmap'; resultTab = 'mindmap'">导图</button>
    </nav>

    <div v-if="savePreviewVisible" class="modal-overlay" @click="savePreviewVisible = false">
      <div class="modal" @click.stop>
        <header class="modal-head">
          <h3>保存为新笔记</h3>
          <button class="icon-btn" @click="savePreviewVisible = false">✕</button>
        </header>
        <p class="hint">会创建一篇新笔记，不会覆盖原笔记；来源链接会保留。</p>
        <textarea v-model="saveMarkdown" class="preview-textarea" readonly></textarea>
        <div class="modal-actions">
          <button class="ghost-btn" @click="savePreviewVisible = false">取消</button>
          <button class="primary-btn" :disabled="savingNote" @click="confirmSaveNote">
            {{ savingNote ? '保存中…' : '确认创建笔记' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'
import http from '../../services/http'
import { insightApi } from '../../services/insightApi'
import { noteApi } from '../../services/noteApi'
import { buildAnalysisMarkdown } from '../../services/analysisExport'
import { useUserStore } from '../../store/user'
import EvidenceChips from '../../components/EvidenceChips.vue'
import MindmapCanvas from '../../components/MindmapCanvas.vue'
import MindmapTree from '../../components/MindmapTree.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const sourceId = computed(() => String(route.params.id || ''))
const isNote = computed(() => route.meta.sourceType === 'note'
  || route.path.startsWith('/notes/'))
const sourceType = computed(() => (isNote.value ? 'note' : 'document'))

const loading = ref(true)
const generating = ref(false)
const pollWarning = ref('')
const errorMessage = ref('')
const state = ref({ status: 'not_generated', job: null })
const artifact = ref(null)
const evidence = ref({})
const mindmap = ref(null)
const revision = ref('base')
const segments = ref([])
const sourceTitle = ref('')
const fileUrl = ref('')
const truncated = ref(false)
const activeAnchorId = ref('')
const resultTab = ref('summary')
const mindmapMode = ref('canvas')
const mobileTab = ref('summary')
const isDesktop = ref(window.innerWidth >= 900)
const splitPercent = ref(44)
const bodyRef = ref(null)
const sourceScrollRef = ref(null)
const showPdf = ref(false)
const pdfObjectUrl = ref('')
const pdfPage = ref(1)
const savePreviewVisible = ref(false)
const saveMarkdown = ref('')
const savingNote = ref(false)

let jobTimer = null
let loadToken = 0
let resizing = false

const statusLabel = computed(() => {
  const status = state.value.status
  if (generating.value && !['queued', 'running'].includes(status)) return '正在提交'
  return {
    not_generated: '未生成',
    queued: '排队中',
    running: '分析中',
    ready: '已生成',
    partial: '部分结果',
    failed: '失败',
    stale: '已过期',
  }[status] || status
})

const statusClass = computed(() => ({
  ready: 'ok', partial: 'warn', failed: 'error', stale: 'error',
}[state.value.status] || 'muted'))

const bodyStyle = computed(() => {
  if (!isDesktop.value) return {}
  return { gridTemplateColumns: `${splitPercent.value}% 6px minmax(0, 1fr)` }
})

const isPdf = computed(() => /\.pdf$/i.test(sourceTitle.value || ''))
const pdfSrc = computed(() => pdfObjectUrl.value
  ? `${pdfObjectUrl.value}#page=${pdfPage.value}&toolbar=1`
  : '')

const segmentGroups = computed(() => {
  const groups = []
  const map = new Map()
  for (const segment of segments.value) {
    const key = segment.page_no
      ? `page-${segment.page_no}`
      : (segment.heading_path || '正文')
    if (!map.has(key)) {
      const group = {
        key,
        label: segment.page_no
          ? `第 ${segment.page_no} 页`
          : (segment.heading_path || '正文'),
        segments: [],
      }
      map.set(key, group)
      groups.push(group)
    }
    map.get(key).segments.push(segment)
  }
  return groups
})

function goBack() {
  router.push(isNote.value ? `/notes/${sourceId.value}` : '/knowledge')
}

async function fetchInsight() {
  return isNote.value
    ? insightApi.getNoteInsight(sourceId.value)
    : insightApi.getDocumentInsight(sourceId.value)
}

async function fetchMindmap() {
  return isNote.value
    ? insightApi.getNoteMindmap(sourceId.value)
    : insightApi.getDocumentMindmap(sourceId.value)
}

async function submitGeneration(force) {
  return isNote.value
    ? insightApi.generateNote(sourceId.value, { force })
    : insightApi.generateDocument(sourceId.value, { force })
}

function resetArtifactState() {
  artifact.value = null
  evidence.value = {}
  mindmap.value = null
  revision.value = 'base'
  activeAnchorId.value = ''
}

async function loadArtifact(token) {
  const [insight, mind] = await Promise.all([fetchInsight(), fetchMindmap()])
  if (token !== loadToken) return
  artifact.value = insight.artifact || null
  evidence.value = insight.evidence || {}
  sourceTitle.value = insight.title || sourceTitle.value
  if (mind.status === 'ready') {
    mindmap.value = mind.mindmap
    revision.value = mind.revision || 'base'
  } else {
    mindmap.value = null
  }
}

async function loadAll() {
  const token = ++loadToken
  stopPolling()
  loading.value = true
  errorMessage.value = ''
  generating.value = false
  pollWarning.value = ''
  if (pdfObjectUrl.value) URL.revokeObjectURL(pdfObjectUrl.value)
  pdfObjectUrl.value = ''
  pdfPage.value = 1
  resetArtifactState()
  try {
    const [stateData, textData] = await Promise.all([
      insightApi.getSourceState(sourceType.value, sourceId.value),
      insightApi.getSourceText(sourceType.value, sourceId.value),
    ])
    if (token !== loadToken) return
    state.value = stateData
    sourceTitle.value = stateData.title || textData.title || ''
    segments.value = textData.segments || []
    fileUrl.value = textData.file_url || ''
    truncated.value = !!textData.truncated
    showPdf.value = false
    if (stateData.result_available || ['ready', 'partial'].includes(stateData.status)) {
      await loadArtifact(token)
    }
    if (token !== loadToken) return
    if (route.query.anchor) openAnchor(String(route.query.anchor))
    if (['queued', 'running'].includes(stateData.status) && stateData.job?.job_id) {
      generating.value = true
      startPolling(token, stateData.job.job_id)
    }
    const query = { ...route.query }
    if (query.generate) {
      delete query.generate
      router.replace({ query })
      if (stateData.status === 'not_generated' || stateData.status === 'failed') {
        await generate(true)
      }
    }
  } catch (error) {
    if (token !== loadToken) return
    errorMessage.value = error.response?.data?.detail || '分析工作台加载失败，请稍后重试'
  } finally {
    if (token === loadToken) loading.value = false
  }
}

function stopPolling() {
  if (jobTimer) {
    clearTimeout(jobTimer)
    jobTimer = null
  }
}

function startPolling(token, jobId, attempt = 0) {
  stopPolling()
  jobTimer = setTimeout(async () => {
    if (token !== loadToken) return
    try {
      const job = await insightApi.getJob(jobId)
      if (token !== loadToken) return
      state.value = { ...state.value, status: job.status, job }
      if (job.status === 'succeeded') {
        generating.value = false
        await loadAll()
        showToast('分析已完成')
        return
      }
      if (job.status === 'failed' || job.status === 'stale') {
        generating.value = false
        errorMessage.value = ''
        return
      }
      if (attempt < 120) startPolling(token, jobId, attempt + 1)
      else pollWarning.value = '任务仍在服务端运行，自动轮询已暂停；可刷新继续查看。'
    } catch (error) {
      if (token !== loadToken) return
      if (attempt < 20) startPolling(token, jobId, attempt + 1)
      else pollWarning.value = '暂时无法取得任务状态，任务不会因此取消。请刷新重试。'
    }
  }, 1500)
}

async function generate(force) {
  if (generating.value) return
  generating.value = true
  errorMessage.value = ''
  const token = loadToken
  try {
    const result = await submitGeneration(force)
    if (token !== loadToken) return
    if (result.already_generated) {
      generating.value = false
      await loadAll()
      showToast('已存在当前版本的分析结果')
      return
    }
    state.value = { ...state.value, status: result.status, job: { job_id: result.job_id } }
    startPolling(token, result.job_id)
  } catch (error) {
    if (token !== loadToken) return
    generating.value = false
    errorMessage.value = error.response?.data?.detail || '任务提交失败，请稍后重试'
  }
}

function openAnchor(anchorId) {
  const segment = segments.value.find((item) => item.anchor_id === anchorId)
  if (!segment) {
    showToast('证据已失效或当前版本不可用')
    return
  }
  activeAnchorId.value = anchorId
  if (!isDesktop.value) mobileTab.value = 'source'
  if (segment.page_no) pdfPage.value = segment.page_no
  nextTick(() => {
    const element = document.getElementById(`anchor-${anchorId}`)
    element?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  })
}

async function togglePdf() {
  if (showPdf.value) {
    showPdf.value = false
    return
  }
  if (!fileUrl.value) return
  if (!pdfObjectUrl.value) {
    const token = loadToken
    try {
      const response = await http.get(fileUrl.value, { responseType: 'blob' })
      if (token !== loadToken) return
      pdfObjectUrl.value = URL.createObjectURL(response.data)
    } catch (_) {
      showToast('原文件加载失败')
      return
    }
  }
  showPdf.value = true
}

async function openOriginalFile() {
  if (!fileUrl.value) return
  try {
    const response = await http.get(fileUrl.value, { responseType: 'blob' })
    const url = URL.createObjectURL(response.data)
    window.open(url, '_blank', 'noopener')
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (_) {
    showToast('原文件加载失败')
  }
}

function onMindmapSaved(result) {
  if (result?.mindmap) {
    mindmap.value = result.mindmap
    revision.value = result.revision || revision.value
  } else {
    loadAll()
  }
}

function onOutlineSelect(node) {
  if (node?.anchor_ids?.length) openAnchor(node.anchor_ids[0])
}

function buildMarkdown() {
  return buildAnalysisMarkdown({ title: sourceTitle.value, sourceType: sourceType.value,
    sourceId: sourceId.value, artifact: artifact.value || {}, evidence: evidence.value })
}

function openSavePreview() {
  saveMarkdown.value = buildMarkdown()
  savePreviewVisible.value = true
}

async function confirmSaveNote() {
  if (savingNote.value) return
  savingNote.value = true
  try {
    const result = await noteApi.create({
      title: `${sourceTitle.value} - 分析`,
      content: saveMarkdown.value,
      category: 'project',
      tags: ['分析'],
    })
    if (result.code === 200) {
      savePreviewVisible.value = false
      showToast('已创建新笔记')
    } else {
      showToast(result.message || '保存失败')
    }
  } catch (error) {
    showToast(error.response?.data?.detail || '保存失败')
  } finally {
    savingNote.value = false
  }
}

function exportMarkdown() {
  const blob = new window.Blob([buildMarkdown()], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${sourceTitle.value || 'analysis'}-分析.md`
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function startResize(event) {
  resizing = true
  event.preventDefault()
}

function onResize(event) {
  if (!resizing || !bodyRef.value) return
  const rect = bodyRef.value.getBoundingClientRect()
  const percent = ((event.clientX - rect.left) / rect.width) * 100
  splitPercent.value = Math.max(28, Math.min(65, percent))
}

function stopResize() {
  resizing = false
}

function handleWindowResize() {
  isDesktop.value = window.innerWidth >= 900
}

watch(() => [route.params.id, route.meta.sourceType], () => {
  if (!route.path.includes('/insight') && !route.path.startsWith('/analysis')) return
  loadAll()
})
watch(() => route.query.anchor, (anchor) => {
  if (anchor && segments.value.length) openAnchor(String(anchor))
})
watch(() => userStore.authEpoch, () => {
  ++loadToken
  stopPolling()
  resetArtifactState()
  segments.value = []
  sourceTitle.value = ''
  fileUrl.value = ''
  if (pdfObjectUrl.value) URL.revokeObjectURL(pdfObjectUrl.value)
  pdfObjectUrl.value = ''
  generating.value = false
  errorMessage.value = '登录状态已变化，请重新打开分析页面。'
})

onMounted(() => {
  window.addEventListener('resize', handleWindowResize)
  window.addEventListener('pointermove', onResize)
  window.addEventListener('pointerup', stopResize)
  loadAll()
})

onBeforeUnmount(() => {
  ++loadToken
  stopPolling()
  window.removeEventListener('resize', handleWindowResize)
  window.removeEventListener('pointermove', onResize)
  window.removeEventListener('pointerup', stopResize)
  if (pdfObjectUrl.value) URL.revokeObjectURL(pdfObjectUrl.value)
})
</script>

<style scoped>
.task-banner { padding: 14px 18px; margin-bottom: 14px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-card); }
.task-banner h2 { font-size: 16px; margin: 4px 0; }
.task-banner.error { color: var(--color-error); }
.analysis-workspace {
  display: flex;
  flex-direction: column;
  height: calc(100vh - var(--topbar-height) - 16px);
  min-height: 560px;
  color: var(--color-text);
}
.workspace-header {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 8px 4px 12px;
}
.workspace-title { flex: 1; min-width: 180px; }
.workspace-title h1 {
  margin: 0 0 3px; font-size: 19px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.workspace-meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 12px; }
.type-badge { color: var(--color-primary); border: 1px solid color-mix(in srgb, var(--color-primary) 35%, transparent); border-radius: 999px; padding: 1px 9px; }
.status-badge { border-radius: 999px; padding: 1px 9px; border: 1px solid var(--color-border); color: var(--color-text-light); }
.status-badge.ok { color: #047857; border-color: rgba(4,120,87,.35); background: rgba(16,185,129,.1); }
.status-badge.warn { color: #b45309; border-color: rgba(180,83,9,.35); background: rgba(245,158,11,.1); }
.status-badge.error { color: var(--color-error); border-color: rgba(185,28,28,.35); }
.model-text { color: var(--color-text-lightest); }
.icon-btn { border: 1px solid var(--color-border); background: transparent; color: var(--color-text); border-radius: 8px; width: 32px; height: 32px; cursor: pointer; }
.workspace-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.ghost-btn, .mini-btn {
  border: 1px solid var(--color-border); background: transparent; color: var(--color-text);
  border-radius: 8px; padding: 6px 10px; font-size: 12px; cursor: pointer;
}
.ghost-btn:disabled, .mini-btn:disabled { opacity: .45; cursor: default; }
.primary-btn { border: none; border-radius: 8px; background: var(--color-primary); color: #fff; padding: 8px 14px; font-size: 13px; cursor: pointer; }
.workspace-body { display: grid; gap: 0; flex: 1; min-height: 0; }
.source-panel, .result-panel { min-width: 0; min-height: 0; border: 1px solid var(--color-border); background: var(--color-card); overflow: hidden; display: flex; flex-direction: column; }
.source-panel { border-radius: 12px 0 0 12px; }
.result-panel { border-radius: 0 12px 12px 0; }
.panel-head { display: flex; justify-content: space-between; align-items: center; padding: 9px 12px; border-bottom: 1px solid var(--color-border-light); }
.panel-head h2 { margin: 0; font-size: 14px; }
.panel-actions { display: flex; gap: 6px; }
.source-scroll, .result-scroll { flex: 1; overflow-y: auto; padding: 12px 14px; }
.pdf-frame { width: 100%; height: 100%; min-height: 500px; border: none; }
.segment-group h3 { position: sticky; top: -12px; background: var(--color-card); margin: 0 -14px 8px; padding: 6px 14px; font-size: 12px; color: var(--color-text-light); border-bottom: 1px solid var(--color-border-light); }
.segment { margin: 0 0 12px; line-height: 1.8; font-size: 14px; padding: 6px 8px; border-radius: 6px; transition: background .15s ease; }
.segment.active { background: color-mix(in srgb, var(--color-primary) 12%, transparent); box-shadow: inset 3px 0 0 var(--color-primary); }
.splitter { cursor: col-resize; background: var(--color-border-light); border-radius: 3px; }
.result-tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--color-border-light); padding: 0 10px; }
.result-tabs button { border: none; background: transparent; color: var(--color-text-light); padding: 10px 12px; cursor: pointer; border-bottom: 2px solid transparent; font-size: 13px; }
.result-tabs button.active { color: var(--color-primary); border-bottom-color: var(--color-primary); font-weight: 600; }
.analysis-card { border: 1px solid var(--color-border-light); border-radius: 10px; padding: 14px 16px; margin-bottom: 12px; }
.analysis-card h2 { font-size: 15px; margin: 0 0 8px; }
.analysis-card p { line-height: 1.75; margin: 0 0 6px; }
.claim-list { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 8px; line-height: 1.7; }
.term-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.term-card { border: 1px solid var(--color-border-light); border-radius: 8px; padding: 8px 10px; }
.term-card p { font-size: 12px; color: var(--color-text-light); margin: 4px 0; }
.warning-banner { border: 1px solid rgba(245,158,11,.4); background: rgba(245,158,11,.1); color: #92400e; border-radius: 8px; padding: 8px 12px; font-size: 12px; margin-bottom: 10px; }
.coverage-note { font-size: 12px; color: var(--color-text-light); }
.state-block { border: 1px solid var(--color-border-light); border-radius: 10px; padding: 40px 20px; text-align: center; color: var(--color-text-light); }
.state-block h2 { color: var(--color-text); font-size: 17px; }
.state-block.error { color: var(--color-error); }
.state-block .hint { font-size: 12px; }
.spinner { width: 26px; height: 26px; margin: 0 auto 10px; border: 2px solid var(--color-border); border-top-color: var(--color-primary); border-radius: 50%; animation: spin .8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.mindmap-wrap { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 480px; }
.mindmap-mode { display: flex; gap: 6px; }
.mindmap-mode button { border: 1px solid var(--color-border); background: transparent; color: var(--color-text); border-radius: 7px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.mindmap-mode button.active { border-color: var(--color-primary); color: var(--color-primary); }
.outline-wrap { border: 1px solid var(--color-border); border-radius: 10px; padding: 14px; overflow: auto; }
.mobile-tabs { display: flex; gap: 6px; padding: 8px 4px; }
.mobile-tabs button { flex: 1; border: 1px solid var(--color-border); background: transparent; color: var(--color-text); border-radius: 8px; padding: 8px; }
.mobile-tabs button.active { border-color: var(--color-primary); color: var(--color-primary); }
.modal-overlay { position: fixed; inset: 0; background: rgba(12,20,35,.45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.modal { width: min(720px, 94vw); max-height: 86vh; display: flex; flex-direction: column; background: var(--color-card); border-radius: 12px; padding: 16px; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; }
.preview-textarea { flex: 1; min-height: 320px; margin: 10px 0; border: 1px solid var(--color-border); border-radius: 8px; padding: 10px; background: var(--color-bg); color: var(--color-text); font-family: var(--font-mono); font-size: 12px; line-height: 1.6; resize: vertical; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.hint { color: var(--color-text-light); font-size: 12px; }
.empty-text { color: var(--color-text-light); font-size: 13px; }
@media (max-width: 899px) {
  .analysis-workspace { height: auto; min-height: 0; }
  .workspace-body { grid-template-columns: 1fr !important; }
  .source-panel, .result-panel { border-radius: 12px; }
  .splitter { display: none; }
}
</style>
