<template>
  <div class="graph-page">
    <header class="graph-header">
      <button class="icon-btn" title="返回知识库" @click="router.push('/knowledge')">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 18 9 12 15 6" />
        </svg>
      </button>
      <div class="title-block">
        <h1>知识图谱</h1>
        <p class="subtitle">每个节点与关系都保留可核验的原文证据</p>
      </div>
      <div class="filters">
        <select v-model="spaceId" class="filter-input" @change="loadGraph">
          <option value="">个人知识库</option>
          <option v-for="space in spaces" :key="space.id" :value="space.id">{{ space.name }}</option>
        </select>
        <input
          v-model="query"
          class="filter-input"
          placeholder="筛选概念名…"
          @keyup.enter="loadGraph"
        />
        <button class="primary-btn" :disabled="loading" @click="loadGraph">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </header>

    <div v-if="errorMessage" class="state-card state-error">
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="loadGraph">重试</button>
    </div>

    <div v-else-if="!loading && nodes.length === 0" class="state-card">
      <h2>还没有可展示的知识图谱</h2>
      <p>在文档洞察中生成摘要后，已核验的概念和关系会自动汇入图谱。</p>
      <button class="primary-btn" @click="router.push('/knowledge')">去知识库</button>
    </div>

    <div v-else class="graph-layout">
      <section class="canvas-wrap">
        <svg class="graph-canvas" viewBox="0 0 800 600" role="img" aria-label="知识图谱">
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="18"
              refY="5"
              markerWidth="6"
              markerHeight="6"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(100,116,139,0.7)" />
            </marker>
          </defs>
          <g>
            <line
              v-for="edge in positionedEdges"
              :key="edge.id"
              :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
              class="graph-edge"
              :class="{ selected: selectedEdgeId === edge.id }"
              marker-end="url(#arrow)"
              @click.stop="selectEdge(edge)"
            />
          </g>
          <g
            v-for="node in positionedNodes"
            :key="node.id"
            class="graph-node"
            :class="{ selected: selectedNodeId === node.id }"
            @click.stop="selectNode(node)"
          >
            <circle :cx="node.x" :cy="node.y" :r="node.radius" />
            <text :x="node.x" :y="node.y + node.radius + 16" text-anchor="middle">
              {{ truncate(node.name, 14) }}
            </text>
          </g>
        </svg>
        <div class="legend">
          <span>{{ nodes.length }} 个概念</span>
          <span>{{ edges.length }} 条关系</span>
          <span>点击节点/连线查看证据</span>
        </div>

        <div class="edge-list">
          <button
            v-for="edge in edges"
            :key="edge.id"
            class="edge-item"
            :class="{ selected: selectedEdgeId === edge.id }"
            @click="selectEdge(edge)"
          >
            <span>{{ nodeName(edge.source) }} → {{ nodeName(edge.target) }}</span>
            <span class="edge-type">{{ relationText(edge.type) }}</span>
            <span class="edge-count">{{ edge.evidence_count }} 条证据</span>
          </button>
        </div>
      </section>

      <aside class="detail-panel">
        <template v-if="detail">
          <header class="detail-header">
            <h2>{{ detailTitle }}</h2>
            <button class="icon-btn" @click="clearSelection">✕</button>
          </header>

          <template v-if="detailKind === 'node'">
            <p v-if="detail.description" class="detail-desc">{{ detail.description }}</p>
            <p class="detail-meta">状态：{{ statusText(detail.status) }}</p>
            <div class="action-row">
              <button class="small-btn" @click="patchNode('confirmed')">确认概念</button>
              <button class="small-btn danger" @click="patchNode('rejected')">拒绝</button>
            </div>
          </template>

          <template v-else>
            <p v-if="detail.description" class="detail-desc">{{ detail.description }}</p>
            <p class="detail-meta">关系类型：{{ relationText(detail.type) }}</p>
            <p class="detail-meta">状态：{{ statusText(detail.verification_state) }}</p>
            <div class="action-row">
              <button class="small-btn" @click="patchRelation('confirmed')">确认关系</button>
              <button class="small-btn danger" @click="patchRelation('rejected')">拒绝关系</button>
            </div>
          </template>

          <h3 class="evidence-title">原文证据</h3>
          <div v-if="detail.evidence && detail.evidence.length" class="evidence-list">
            <blockquote v-for="item in detail.evidence" :key="item.anchor_id" class="evidence-item">
              <div class="evidence-locator">
                <span v-if="item.page_no">第 {{ item.page_no }} 页</span>
                <span v-if="item.heading_path">{{ item.heading_path }}</span>
              </div>
              <p>{{ item.quote }}</p>
            </blockquote>
          </div>
          <p v-else class="detail-meta">当前没有有效证据（来源可能已失效）。</p>
        </template>

        <div v-else class="panel-empty">
          <p>选择左侧节点或关系，查看其来源证据。</p>
          <p class="hint">拒绝的关系不会出现在图谱中；确认状态会保留审计。</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import { insightApi } from '../../services/insightApi'
import { orgApi } from '../../services/orgApi'

const router = useRouter()
const loading = ref(false)
const errorMessage = ref('')
const nodes = ref([])
const edges = ref([])
const query = ref('')
const spaceId = ref('')
const spaces = ref([])
const detail = ref(null)
const detailKind = ref('')
const selectedNodeId = ref('')
const selectedEdgeId = ref('')

const CENTER_X = 400
const CENTER_Y = 300
const RADIUS = 220

const positionedNodes = computed(() => {
  const total = nodes.value.length
  return nodes.value.map((node, index) => {
    const angle = total === 1 ? 0 : (2 * Math.PI * index) / total - Math.PI / 2
    const radius = node.radius || 14
    return {
      ...node,
      x: total === 1 ? CENTER_X : CENTER_X + RADIUS * Math.cos(angle),
      y: total === 1 ? CENTER_Y : CENTER_Y + RADIUS * Math.sin(angle),
      radius,
    }
  })
})

const nodePosition = computed(() => {
  const map = {}
  for (const node of positionedNodes.value) map[node.id] = node
  return map
})

const positionedEdges = computed(() => {
  return edges.value
    .map((edge) => {
      const source = nodePosition.value[edge.source]
      const target = nodePosition.value[edge.target]
      if (!source || !target) return null
      return { ...edge, x1: source.x, y1: source.y, x2: target.x, y2: target.y }
    })
    .filter(Boolean)
})

const detailTitle = computed(() => {
  if (!detail.value) return ''
  return detailKind.value === 'node' ? detail.value.name : '关系详情'
})

function relationText(type) {
  return {
    is_a: '属于',
    part_of: '组成部分',
    depends_on: '依赖',
    related_to: '相关',
    contrasts_with: '对比',
  }[type] || type
}

function statusText(status) {
  return {
    auto: '自动生成',
    confirmed: '已确认',
    rejected: '已拒绝',
    unverified: '待验证',
  }[status] || status
}

function truncate(text, limit) {
  const value = String(text || '')
  return value.length > limit ? `${value.slice(0, limit)}…` : value
}

function nodeName(nodeId) {
  return nodes.value.find((node) => node.id === nodeId)?.name || '未知概念'
}

async function loadSpaces() {
  try {
    const orgsRes = await orgApi.listOrgs()
    const orgs = orgsRes.code === 200 ? (orgsRes.data?.orgs || []) : []
    const orgId = orgs[0]?.org_id || orgs[0]?.id
    if (!orgId) return
    const res = await orgApi.listSpaces(orgId)
    if (res.code === 200) {
      spaces.value = (res.data?.spaces || []).map((space) => ({
        id: space.id || space.space_id,
        name: space.name,
      }))
    }
  } catch (error) {
    spaces.value = []
  }
}

async function loadGraph() {
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await insightApi.getGraph({
      space_id: spaceId.value || undefined,
      q: query.value.trim() || undefined,
      limit: 200,
    })
    nodes.value = (data.nodes || []).map((node) => ({
      ...node,
      radius: Math.min(22, 12 + Math.log2((node.evidence_count || 0) + 1) * 2),
    }))
    edges.value = data.edges || []
    clearSelection()
  } catch (error) {
    errorMessage.value = error.response?.data?.detail || '知识图谱加载失败'
  } finally {
    loading.value = false
  }
}

function clearSelection() {
  detail.value = null
  detailKind.value = ''
  selectedNodeId.value = ''
  selectedEdgeId.value = ''
}

async function selectNode(node) {
  selectedNodeId.value = node.id
  selectedEdgeId.value = ''
  detailKind.value = 'node'
  try {
    detail.value = await insightApi.getNode(node.id)
  } catch (error) {
    showToast(error.response?.data?.detail || '节点证据加载失败')
  }
}

async function selectEdge(edge) {
  selectedEdgeId.value = edge.id
  selectedNodeId.value = ''
  detailKind.value = 'edge'
  try {
    detail.value = await insightApi.getRelation(edge.id)
  } catch (error) {
    showToast(error.response?.data?.detail || '关系证据加载失败')
  }
}

async function patchNode(status) {
  if (!detail.value) return
  try {
    await insightApi.patchNode(detail.value.id, status)
    showToast('概念状态已更新')
    await loadGraph()
  } catch (error) {
    showToast(error.response?.data?.detail || '更新失败')
  }
}

async function patchRelation(state) {
  if (!detail.value) return
  try {
    await insightApi.patchRelation(detail.value.id, state)
    showToast('关系状态已更新')
    await loadGraph()
  } catch (error) {
    showToast(error.response?.data?.detail || '更新失败')
  }
}

onMounted(async () => {
  await Promise.all([loadSpaces(), loadGraph()])
})
</script>

<style scoped>
.graph-page {
  max-width: 1180px;
  margin: 0 auto;
  padding: 20px 16px 80px;
  color: var(--color-text);
}
.graph-header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.title-block { flex: 1; min-width: 180px; }
.title-block h1 { margin: 0; font-size: 21px; }
.subtitle { margin: 4px 0 0; font-size: 12px; color: var(--color-text-light); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; }
.filter-input {
  border: 1px solid var(--color-border);
  border-radius: 9px;
  padding: 8px 10px;
  background: var(--color-bg-elevated, #fff);
  color: var(--color-text);
}
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
.primary-btn:disabled { opacity: 0.6; }
.graph-layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; }
.canvas-wrap {
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-bg-elevated, #fff);
  padding: 10px;
  min-width: 0;
}
.graph-canvas { width: 100%; height: auto; display: block; }
.graph-node { cursor: pointer; }
.graph-node circle {
  fill: rgba(30, 64, 175, 0.12);
  stroke: rgba(30, 64, 175, 0.55);
  stroke-width: 1.5;
  transition: fill 0.15s ease;
}
.graph-node.selected circle { fill: rgba(56, 189, 248, 0.35); stroke: #0284c7; stroke-width: 2.5; }
.graph-node text { font-size: 12px; fill: var(--color-text); }
.graph-edge { stroke: rgba(100, 116, 139, 0.55); stroke-width: 1.4; cursor: pointer; }
.graph-edge.selected { stroke: #0284c7; stroke-width: 2.6; }
.legend { display: flex; gap: 14px; font-size: 12px; color: var(--color-text-light); padding: 4px 8px 10px; }
.edge-list { display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto; }
.edge-item {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  cursor: pointer;
  text-align: left;
}
.edge-item.selected { border-color: var(--color-primary); }
.edge-type { color: var(--color-primary); }
.edge-count { margin-left: auto; color: var(--color-text-light); }
.detail-panel {
  border: 1px solid var(--color-border);
  border-radius: 14px;
  background: var(--color-bg-elevated, #fff);
  padding: 16px;
  max-height: 720px;
  overflow-y: auto;
}
.detail-header { display: flex; justify-content: space-between; align-items: center; }
.detail-header h2 { font-size: 16px; margin: 0; }
.detail-desc { line-height: 1.7; }
.detail-meta { font-size: 12px; color: var(--color-text-light); }
.action-row { display: flex; gap: 8px; margin: 8px 0 14px; }
.small-btn {
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-text);
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12px;
  cursor: pointer;
}
.small-btn.danger { color: #b91c1c; border-color: rgba(185, 28, 28, 0.35); }
.evidence-title { font-size: 14px; margin: 16px 0 8px; }
.evidence-list { display: flex; flex-direction: column; gap: 10px; }
.evidence-item {
  margin: 0;
  border-left: 3px solid var(--color-primary);
  background: rgba(30, 64, 175, 0.05);
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.7;
}
.evidence-locator { font-size: 11px; color: var(--color-text-light); margin-bottom: 4px; }
.panel-empty { text-align: center; color: var(--color-text-light); padding: 24px 10px; line-height: 1.8; }
.hint { font-size: 12px; }
.state-card {
  border: 1px solid var(--color-border);
  border-radius: 14px;
  padding: 36px 24px;
  text-align: center;
  background: var(--color-bg-elevated, #fff);
}
.state-error { color: #b91c1c; }
@media (max-width: 900px) {
  .graph-layout { grid-template-columns: 1fr; }
  .detail-panel { max-height: none; }
}
</style>
