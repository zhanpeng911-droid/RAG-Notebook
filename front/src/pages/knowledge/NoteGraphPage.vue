<template>
  <div class="note-graph-page">
    <header class="graph-header">
      <div class="header-left">
        <h1>知识图谱</h1>
        <span class="graph-subtitle">
          {{ localNoteId ? `局部图 · ${localDepth} 跳` : '笔记之间的真实链接与手动关联' }}
        </span>
      </div>
      <div class="header-actions">
        <button class="ghost-btn" :class="{ active: !localNoteId }" @click="backToGlobal">
          全局图
        </button>
        <template v-if="localNoteId">
          <button class="ghost-btn" :class="{ active: localDepth === 1 }" @click="setDepth(1)">1 跳</button>
          <button class="ghost-btn" :class="{ active: localDepth === 2 }" @click="setDepth(2)">2 跳</button>
        </template>
        <button class="ghost-btn" @click="toggleList">{{ showList ? '图形视图' : '列表视图' }}</button>
        <button class="primary-btn" :disabled="loading" @click="loadGraph">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </header>

    <div class="graph-toolbar">
      <input
        v-model="query"
        :disabled="!!localNoteId"
        class="filter-input search"
        placeholder="搜索笔记标题…"
        @keyup.enter="loadGraph"
      />
      <input v-model="tag" :disabled="!!localNoteId" class="filter-input" placeholder="标签筛选" @keyup.enter="loadGraph" />
      <select v-model="category" :disabled="!!localNoteId" class="filter-input" @change="loadGraph">
        <option value="">全部分类</option>
        <option value="work">工作</option>
        <option value="study">学习</option>
        <option value="life">生活</option>
        <option value="project">项目</option>
      </select>
      <select v-model="edgeType" :disabled="!!localNoteId" class="filter-input" @change="loadGraph">
        <option value="all">全部关系</option>
        <option value="explicit">正文链接</option>
        <option value="manual">手动关联</option>
      </select>
      <label class="checkbox-label">
        <input v-model="includeOrphans" :disabled="!!localNoteId" type="checkbox" @change="loadGraph" />
        显示孤立笔记
      </label>
      <span class="counts">
        {{ counts.visible_notes || 0 }} 笔记 · {{ counts.visible_edges || 0 }} 关系
        <span v-if="counts.truncated" class="truncated">已截断，当前只显示部分数据</span>
      </span>
    </div>
    <p v-if="localNoteId" class="local-filter-hint">局部图展示中心笔记的完整邻接关系；以上全局筛选暂不应用。返回全局图可继续筛选。</p>

    <div v-if="errorMessage" class="state-card error">
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="loadGraph">重试</button>
    </div>

    <div v-else-if="!loading && nodes.length === 0" class="state-card">
      <h2>{{ query || tag || category ? '没有匹配的笔记' : '还没有笔记' }}</h2>
      <p v-if="query || tag || category">调整筛选条件，或清空搜索后重试。</p>
      <p v-else>先创建一篇笔记，再在正文输入 <code>[[</code> 建立链接。</p>
      <button class="primary-btn" @click="router.push('/notes/new')">新建笔记</button>
    </div>

    <div v-else class="graph-layout">
      <section class="canvas-shell">
        <div v-if="!loading && nodes.length > 0 && edges.length === 0" class="empty-edges">
          这些笔记还没有连接。在正文输入 <code>[[</code> 选择另一篇笔记，或从右侧添加手动关联。
        </div>

        <div v-if="showList" class="graph-list">
          <button
            v-for="node in nodes"
            :key="node.id"
            class="graph-list-item"
            :class="{ selected: selectedId === node.id }"
            @click="selectNode(node)"
            @dblclick="openNote(node.id)"
          >
            <span class="list-title">{{ node.title }}</span>
            <span class="list-meta">{{ node.degree }} 条连接</span>
            <span v-if="node.is_orphan" class="list-orphan">孤立</span>
          </button>
        </div>

        <div
          v-else
          ref="viewportRef"
          class="graph-canvas"
          @pointerdown="startPan"
          @wheel.prevent="onWheel"
        >
          <svg width="100%" height="100%">
            <g :transform="`translate(${view.x},${view.y}) scale(${view.zoom})`">
              <line
                v-for="edge in positionedEdges"
                :key="'hit-' + edge.id"
                :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
                class="edge-hit"
                @click.stop="selectEdge(edge)"
              />
              <line
                v-for="edge in positionedEdges"
                :key="edge.id"
                :x1="edge.x1" :y1="edge.y1" :x2="edge.x2" :y2="edge.y2"
                :class="['graph-edge', edge.type, { selected: selectedEdgeId === edge.id, dimmed: isEdgeDimmed(edge) }]"
                :stroke-width="edge.type === 'manual_relation' ? 1.2 : Math.min(4, 1 + Math.log2(edge.count + 1))"
                :stroke-dasharray="edge.type === 'manual_relation' ? '6 5' : ''"
              />
              <g
                v-for="(node, nodeIndex) in positionedNodes"
                :key="node.id"
                class="graph-node"
                :class="{
                  selected: selectedId === node.id,
                  hovered: hoveredId === node.id,
                  dimmed: isNodeDimmed(node.id),
                  orphan: node.is_orphan,
                }"
                :transform="`translate(${node.x},${node.y})`"
                @pointerdown.stop="startDrag($event, node)"
                @pointerenter="hoveredId = node.id"
                @pointerleave="hoveredId = ''"
                @click.stop="onNodeClick(node)"
                @dblclick.stop="onNodeDoubleClick(node)"
              >
                <g class="node-visual" :style="{ animationDelay: `${Math.min(nodeIndex, 30) * 18}ms` }">
                  <circle class="node-hit" :r="node.radius + 8" />
                  <circle :r="node.radius" />
                  <text
                    v-if="view.zoom > 0.55 || selectedId === node.id || hoveredId === node.id"
                    :y="node.radius + 15"
                    text-anchor="middle"
                  >{{ truncate(node.title, 16) }}</text>
                </g>
              </g>
            </g>
          </svg>
        </div>

        <div class="canvas-footer">
          <div class="zoom-controls">
            <button title="放大" @click="zoomBy(1.2)">＋</button>
            <button title="缩小" @click="zoomBy(1 / 1.2)">－</button>
            <button title="适应视图" @click="fitView">适应</button>
            <button title="重置视野" @click="resetView">重置</button>
          </div>
          <button class="ghost-btn" @click="unlockLayout">解锁节点</button>
        </div>
      </section>

      <aside class="detail-panel">
        <template v-if="detailKind === 'node' && detail">
          <header class="detail-header">
            <h2>{{ detail.note.title }}</h2>
            <button class="icon-btn" @click="clearSelection">✕</button>
          </header>
          <div class="detail-meta">
            <span v-if="detail.note.category">{{ categoryText(detail.note.category) }}</span>
            <span v-for="item in detail.note.tags || []" :key="item" class="tag">{{ item }}</span>
            <span>{{ detail.note.degree }} 条连接</span>
          </div>
          <div class="detail-actions">
            <button class="primary-btn" @click="openNote(detail.note.id)">打开笔记</button>
            <button class="ghost-btn" @click="router.push(`/analysis/note/${detail.note.id}`)">分析笔记</button>
            <button class="ghost-btn" @click="enterLocal(detail.note.id)">局部图</button>
          </div>

          <section class="relation-block">
            <h3>链接到 ({{ detail.outgoing.length }})</h3>
            <button
              v-for="item in detail.outgoing"
              :key="'out-' + item.note.id"
              class="relation-item"
              @click="openNote(item.note.id)"
            >
              <span>{{ item.note.title }}</span>
              <span class="relation-count">×{{ item.count }}</span>
              <small>{{ occurrencePreview(item.occurrences) }}</small>
            </button>
            <p v-if="detail.outgoing.length === 0" class="empty-text">正文中没有出链</p>
          </section>

          <section class="relation-block">
            <h3>被链接 ({{ detail.incoming.length }})</h3>
            <button
              v-for="item in detail.incoming"
              :key="'in-' + item.note.id"
              class="relation-item"
              @click="openNote(item.note.id)"
            >
              <span>{{ item.note.title }}</span>
              <span class="relation-count">×{{ item.count }}</span>
              <small>{{ occurrencePreview(item.occurrences) }}</small>
            </button>
            <p v-if="detail.incoming.length === 0" class="empty-text">还没有反向链接</p>
          </section>

          <section class="relation-block">
            <div class="block-head">
              <h3>手动关联 ({{ detail.manual.length }})</h3>
              <button class="link-btn" @click="openPicker(detail.note.id)">添加</button>
            </div>
            <div v-for="item in detail.manual" :key="item.relation_id" class="relation-item manual">
              <button class="relation-open" @click="openNote(item.note.id)">
                <span>{{ item.note.title }}</span>
                <small>{{ item.direction === 'outgoing' ? '我关联到' : '关联到本篇' }}</small>
              </button>
              <button class="link-btn danger" @click="removeManual(item.relation_id)">
                移除
              </button>
            </div>
            <p v-if="detail.manual.length === 0" class="empty-text">还没有手动关联</p>
          </section>

          <section v-if="detail.unresolved.length" class="relation-block">
            <h3>未解析链接 ({{ detail.unresolved.length }})</h3>
            <div v-for="item in detail.unresolved" :key="item.occurrence_id" class="unresolved-item">
              <code>{{ item.raw_token }}</code>
              <small>{{ item.status === 'ambiguous' ? '标题重名，请手动选择' : '目标不存在或已删除' }}</small>
            </div>
          </section>
        </template>

        <template v-else-if="detailKind === 'edge' && selectedEdge">
          <header class="detail-header">
            <h2>{{ selectedEdge.type === 'manual_relation' ? '手动关联' : '正文链接' }}</h2>
            <button class="icon-btn" @click="clearSelection">✕</button>
          </header>
          <p class="edge-path">
            {{ nodeTitle(selectedEdge.source) }} → {{ nodeTitle(selectedEdge.target) }}
          </p>
          <p class="detail-meta">
            {{ selectedEdge.type === 'manual_relation' ? '用户手动建立' : `正文引用 ${selectedEdge.count} 次` }}
          </p>
          <p v-if="selectedEdge.reason" class="edge-reason">{{ selectedEdge.reason }}</p>
        </template>

        <div v-else class="panel-empty">
          <p>单击节点查看出链、反链和手动关联。</p>
          <p class="hint">双击节点直接打开笔记；拖动节点可临时固定。</p>
          <div v-if="edges.length" class="edge-list-panel">
            <h3>关系 ({{ edges.length }})</h3>
            <button
              v-for="edge in edges"
              :key="'list-' + edge.id"
              class="edge-list-item"
              :class="{ selected: selectedEdgeId === edge.id }"
              @click="selectEdge(edge)"
            >
              <span>{{ nodeTitle(edge.source) }} → {{ nodeTitle(edge.target) }}</span>
              <small>{{ edge.type === 'manual_relation' ? '手动关联' : `正文 ×${edge.count}` }}</small>
            </button>
          </div>
        </div>
      </aside>
    </div>

    <div v-if="pickerVisible" class="modal-overlay" @click="closePicker">
      <div class="modal" @click.stop>
        <header class="modal-head">
          <h3>添加手动关联</h3>
          <button class="icon-btn" @click="closePicker">✕</button>
        </header>
        <input
          v-model="pickerQuery"
          class="filter-input"
          placeholder="搜索要关联的笔记标题…"
          @input="searchCandidates"
        />
        <div class="candidate-list">
          <button
            v-for="candidate in pickerCandidates"
            :key="candidate.id"
            class="candidate-item"
            @click="addManual(candidate.id)"
          >
            <span>{{ candidate.title }}</span>
            <small v-if="candidate.duplicate_title">同名笔记 · 请确认</small>
            <small v-else>{{ candidate.category || '未分类' }}</small>
          </button>
          <p v-if="pickerCandidates.length === 0" class="empty-text">没有可关联的笔记</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import { noteGraphApi } from '../../services/noteGraphApi'
import { useUserStore } from '../../store/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const loading = ref(false)
const errorMessage = ref('')
const nodes = ref([])
const edges = ref([])
const counts = ref({})
const query = ref('')
const tag = ref('')
const category = ref('')
const edgeType = ref('all')
const includeOrphans = ref(true)
const showList = ref(false)

const selectedId = ref('')
const selectedEdgeId = ref('')
const selectedEdge = ref(null)
const detailKind = ref('')
const detail = ref(null)
const hoveredId = ref('')

const localNoteId = ref('')
const localDepth = ref(1)

const view = ref({ x: 80, y: 80, zoom: 1 })
const viewportRef = ref(null)
const positions = ref({})

const pickerVisible = ref(false)
const pickerSourceId = ref('')
const pickerQuery = ref('')
const pickerCandidates = ref([])

let panStart = { x: 0, y: 0 }
let candidatesTimer = null
let candidateRequest = 0
let restoredView = null
const fixedNodes = new Set()

const userKey = computed(() => {
  const info = userStore.getUserInfo || {}
  return info.uuid || info.id || info.user_id || 'local'
})
const storageKey = computed(() => `notebook:note-graph:${userKey.value}`)
const viewOwnerKey = storageKey.value
const viewAuthEpoch = userStore.authEpoch

const adjacency = computed(() => {
  const map = {}
  for (const edge of edges.value) {
    ;(map[edge.source] ||= new Set()).add(edge.target)
    ;(map[edge.target] ||= new Set()).add(edge.source)
  }
  return map
})

const nodeMap = computed(() => {
  const map = {}
  for (const node of nodes.value) map[node.id] = node
  return map
})

const positionedNodes = computed(() => nodes.value.map((node) => {
  const position = positions.value[node.id] || { x: 0, y: 0 }
  return { ...node, x: position.x, y: position.y, radius: 10 + Math.min(14, node.degree * 2) }
}))

const positionedEdges = computed(() => {
  const map = {}
  for (const node of positionedNodes.value) map[node.id] = node
  return edges.value.map((edge) => {
    const source = map[edge.source]
    const target = map[edge.target]
    if (!source || !target) return null
    return { ...edge, x1: source.x, y1: source.y, x2: target.x, y2: target.y }
  }).filter(Boolean)
})

function truncate(text, limit) {
  const value = String(text || '')
  return value.length > limit ? `${value.slice(0, limit)}…` : value
}

function categoryText(value) {
  return { work: '工作', study: '学习', life: '生活', project: '项目' }[value] || value
}

function nodeTitle(noteId) {
  return nodeMap.value[noteId]?.title || '未知笔记'
}

function occurrencePreview(occurrences) {
  return occurrences?.[0]?.context_text || ''
}

function isNodeDimmed(nodeId) {
  if (!hoveredId.value) return false
  return nodeId !== hoveredId.value && !adjacency.value[hoveredId.value]?.has(nodeId)
}

function isEdgeDimmed(edge) {
  if (!hoveredId.value) return false
  return edge.source !== hoveredId.value && edge.target !== hoveredId.value
}

function seedPositions() {
  const existing = positions.value
  const next = {}
  const count = nodes.value.length || 1
  nodes.value.forEach((node, index) => {
    if (existing[node.id]) {
      next[node.id] = existing[node.id]
      return
    }
    const angle = (2 * Math.PI * index) / count
    next[node.id] = {
      x: 420 + Math.cos(angle) * (180 + (index % 5) * 18),
      y: 320 + Math.sin(angle) * (160 + (index % 7) * 14),
    }
  })
  positions.value = next
}

let simulationFrame = null
let simulationVelocities = {}
let simulationSteps = 0

function stopSimulation() {
  if (simulationFrame) {
    window.cancelAnimationFrame(simulationFrame)
    simulationFrame = null
  }
}

function simulationStep() {
  const ids = nodes.value.map((node) => node.id)
  if (ids.length === 0) return 0
  const next = { ...positions.value }
  const velocity = simulationVelocities
  const count = ids.length
  const repulsion = 1500
  const spring = 0.015
  const gravity = 0.002

  for (let i = 0; i < count; i += 1) {
    const a = ids[i]
    for (let j = i + 1; j < count; j += 1) {
      const b = ids[j]
      let dx = next[a].x - next[b].x
      let dy = next[a].y - next[b].y
      let distance = Math.sqrt(dx * dx + dy * dy) || 0.01
      if (distance > 600) continue
      const force = distance < 120
        ? (120 - distance) * 0.18
        : repulsion / (distance * distance)
      dx /= distance
      dy /= distance
      velocity[a].x += dx * force
      velocity[a].y += dy * force
      velocity[b].x -= dx * force
      velocity[b].y -= dy * force
    }
  }
  for (const edge of edges.value) {
    if (!next[edge.source] || !next[edge.target]) continue
    const dx = next[edge.target].x - next[edge.source].x
    const dy = next[edge.target].y - next[edge.source].y
    velocity[edge.source].x += dx * spring
    velocity[edge.source].y += dy * spring
    velocity[edge.target].x -= dx * spring
    velocity[edge.target].y -= dy * spring
  }

  let movement = 0
  for (const id of ids) {
    const point = next[id]
    velocity[id].x += (520 - point.x) * gravity
    velocity[id].y += (320 - point.y) * gravity
    if (fixedNodes.has(id)) {
      velocity[id].x = 0
      velocity[id].y = 0
      continue
    }
    velocity[id].x *= 0.82
    velocity[id].y *= 0.82
    const moveX = Math.max(-24, Math.min(24, velocity[id].x))
    const moveY = Math.max(-24, Math.min(24, velocity[id].y))
    point.x += moveX
    point.y += moveY
    movement += Math.abs(moveX) + Math.abs(moveY)
  }
  positions.value = next
  return movement
}

function startSimulation(fitAtEnd = true) {
  stopSimulation()
  simulationVelocities = {}
  simulationSteps = 0
  for (const node of nodes.value) {
    simulationVelocities[node.id] = { x: 0, y: 0 }
  }
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
  if (reduced) {
    for (let step = 0; step < 120; step += 1) simulationStep()
    if (fitAtEnd && !viewTouched) fitView()
    return
  }
  const animate = () => {
    const movement = simulationStep()
    simulationSteps += 1
    if (simulationSteps < 120 && movement > 0.3) {
      simulationFrame = window.requestAnimationFrame(animate)
    } else {
      simulationFrame = null
      if (fitAtEnd && !viewTouched) fitView()
    }
  }
  simulationFrame = window.requestAnimationFrame(animate)
}

function fitView() {
  if (nodes.value.length === 0 || !viewportRef.value) return
  const rect = viewportRef.value.getBoundingClientRect()
  const xs = Object.values(positions.value).map((point) => point.x)
  const ys = Object.values(positions.value).map((point) => point.y)
  const minX = Math.min(...xs) - 80
  const maxX = Math.max(...xs) + 80
  const minY = Math.min(...ys) - 60
  const maxY = Math.max(...ys) + 60
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const maxZoom = nodes.value.length <= 10 ? 1.8 : 1.4
  const zoom = Math.max(0.25, Math.min(maxZoom, Math.min(rect.width / width, rect.height / height)))
  view.value = {
    zoom,
    x: (rect.width - width * zoom) / 2 - minX * zoom,
    y: (rect.height - height * zoom) / 2 - minY * zoom,
  }
}

function resetView() {
  viewTouched = true
  view.value = { x: 80, y: 80, zoom: 1 }
}

function zoomBy(factor) {
  viewTouched = true
  const rect = viewportRef.value?.getBoundingClientRect()
  if (!rect) return
  const nextZoom = Math.max(0.2, Math.min(3, view.value.zoom * factor))
  const cx = rect.width / 2
  const cy = rect.height / 2
  const worldX = (cx - view.value.x) / view.value.zoom
  const worldY = (cy - view.value.y) / view.value.zoom
  view.value = {
    zoom: nextZoom,
    x: cx - worldX * nextZoom,
    y: cy - worldY * nextZoom,
  }
}

function onWheel(event) {
  viewTouched = true
  const rect = viewportRef.value?.getBoundingClientRect()
  if (!rect) return
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextZoom = Math.max(0.2, Math.min(3, view.value.zoom * factor))
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  const worldX = (px - view.value.x) / view.value.zoom
  const worldY = (py - view.value.y) / view.value.zoom
  view.value = { zoom: nextZoom, x: px - worldX * nextZoom, y: py - worldY * nextZoom }
}

let panning = false
let dragState = null
let suppressClickUntil = 0
let detailRequest = 0
let viewTouched = false

function startPan(event) {
  if (event.button !== 0) return
  viewTouched = true
  if (event.target.closest('.graph-node')) return
  panning = true
  panStart = { x: event.clientX - view.value.x, y: event.clientY - view.value.y }
  window.addEventListener('pointermove', movePan)
  window.addEventListener('pointerup', endPan)
}

function movePan(event) {
  if (!panning) return
  view.value = { ...view.value, x: event.clientX - panStart.x, y: event.clientY - panStart.y }
}

function endPan() {
  panning = false
  window.removeEventListener('pointermove', movePan)
  window.removeEventListener('pointerup', endPan)
}

function cancelInteractions() {
  endPan()
  if (dragState) {
    dragState = null
    window.removeEventListener('pointermove', dragNode)
    window.removeEventListener('pointerup', endDrag)
  }
}

function startDrag(event, node) {
  if (event.button !== 0) return
  stopSimulation()
  dragState = {
    id: node.id,
    startX: event.clientX,
    startY: event.clientY,
    originX: positions.value[node.id].x,
    originY: positions.value[node.id].y,
    moved: false,
  }
  window.addEventListener('pointermove', dragNode)
  window.addEventListener('pointerup', endDrag)
}

function dragNode(event) {
  if (!dragState) return
  const dx = (event.clientX - dragState.startX) / view.value.zoom
  const dy = (event.clientY - dragState.startY) / view.value.zoom
  if (Math.abs(dx) + Math.abs(dy) > 3) dragState.moved = true
  if (!dragState.moved) return
  positions.value = {
    ...positions.value,
    [dragState.id]: {
      x: dragState.originX + dx,
      y: dragState.originY + dy,
    },
  }
}

function endDrag() {
  if (!dragState) return
  if (dragState.moved) {
    fixedNodes.add(dragState.id)
    suppressClickUntil = Date.now() + 260
  }
  dragState = null
  window.removeEventListener('pointermove', dragNode)
  window.removeEventListener('pointerup', endDrag)
}

function onNodeClick(node) {
  if (Date.now() < suppressClickUntil) return
  selectNode(node)
}

function onNodeDoubleClick(node) {
  if (Date.now() < suppressClickUntil) return
  openNote(node.id)
}

function unlockLayout() {
  fixedNodes.clear()
  startSimulation()
}

function toggleList() {
  showList.value = !showList.value
}

let graphRequest = 0
async function loadGraph() {
  const request = ++graphRequest
  const epoch = userStore.authEpoch
  stopSimulation()
  cancelInteractions()
  loading.value = true
  errorMessage.value = ''
  try {
    const params = {
      q: query.value.trim() || undefined,
      tag: tag.value.trim() || undefined,
      category: category.value || undefined,
      edge_type: edgeType.value,
      include_orphans: includeOrphans.value,
      limit: 300,
    }
    const data = localNoteId.value
      ? await noteGraphApi.localGraph(localNoteId.value, localDepth.value)
      : await noteGraphApi.graph(params)
    if (request !== graphRequest || epoch !== userStore.authEpoch) return
    nodes.value = data.nodes || []
    edges.value = data.edges || []
    counts.value = data.counts || {}
    if (!nodes.value.some((node) => node.id === selectedId.value)) {
      clearSelection()
    }
    seedPositions()
    viewTouched = false
    const restored = restoredView
    restoredView = null
    startSimulation(!restored)
    if (restored) view.value = restored
  } catch (error) {
    if (request !== graphRequest || epoch !== userStore.authEpoch) return
    errorMessage.value = error.response?.data?.detail || '图谱加载失败，请稍后重试'
  } finally {
    if (request === graphRequest && epoch === userStore.authEpoch) loading.value = false
  }
}

function clearSelection() {
  detailRequest += 1
  selectedId.value = ''
  selectedEdgeId.value = ''
  selectedEdge.value = null
  detailKind.value = ''
  detail.value = null
}

async function selectNode(node) {
  const token = ++detailRequest
  selectedId.value = node.id
  selectedEdgeId.value = ''
  selectedEdge.value = null
  detailKind.value = 'node'
  try {
    const data = await noteGraphApi.links(node.id)
    if (token !== detailRequest) return
    detail.value = data
  } catch (error) {
    if (token !== detailRequest) return
    showToast(error.response?.data?.detail || '关系详情加载失败')
  }
}

function selectEdge(edge) {
  detailRequest += 1
  selectedEdge.value = edge
  selectedEdgeId.value = edge.id
  selectedId.value = ''
  detailKind.value = 'edge'
  detail.value = null
}

function openNote(noteId) {
  router.push({ path: `/notes/${noteId}`, query: { from: 'graph' } })
}

function enterLocal(noteId) {
  localNoteId.value = noteId
  localDepth.value = 1
  loadGraph()
}

function setDepth(depth) {
  localDepth.value = depth
  loadGraph()
}

function backToGlobal() {
  localNoteId.value = ''
  localDepth.value = 1
  loadGraph()
}

function openPicker(noteId) {
  pickerSourceId.value = noteId
  pickerQuery.value = ''
  pickerCandidates.value = []
  pickerVisible.value = true
  searchCandidates()
}

function closePicker() {
  ++candidateRequest
  if (candidatesTimer) clearTimeout(candidatesTimer)
  pickerVisible.value = false
}

function searchCandidates() {
  const request = ++candidateRequest
  const epoch = userStore.authEpoch
  if (candidatesTimer) clearTimeout(candidatesTimer)
  candidatesTimer = setTimeout(async () => {
    try {
      const data = await noteGraphApi.candidates(
        pickerQuery.value.trim(), pickerSourceId.value)
      if (request !== candidateRequest || epoch !== userStore.authEpoch) return
      pickerCandidates.value = data.candidates || []
    } catch (_) {
      if (request !== candidateRequest || epoch !== userStore.authEpoch) return
      pickerCandidates.value = []
    }
  }, 180)
}

async function addManual(targetId) {
  try {
    await noteGraphApi.addManual(pickerSourceId.value, targetId)
    pickerVisible.value = false
    showToast('已添加手动关联')
    await loadGraph()
    if (selectedId.value) {
      const node = nodeMap.value[selectedId.value]
      if (node) await selectNode(node)
    }
  } catch (error) {
    showToast(error.response?.data?.detail || '添加失败')
  }
}

async function removeManual(relationId) {
  try {
    await showConfirmDialog({ title: '移除关联', message: '只移除手动关联，不会修改正文链接。' })
  } catch (_) {
    return
  }
  try {
    await noteGraphApi.removeManual(selectedId.value, relationId)
    showToast('已移除')
    await loadGraph()
    const node = nodeMap.value[selectedId.value]
    if (node) await selectNode(node)
  } catch (error) {
    showToast(error.response?.data?.detail || '移除失败')
  }
}

function saveViewState() {
  if (viewAuthEpoch !== userStore.authEpoch) return
  try {
    sessionStorage.setItem(viewOwnerKey, JSON.stringify({
      view: view.value,
      selectedId: selectedId.value,
      localNoteId: localNoteId.value,
      localDepth: localDepth.value,
      query: query.value,
      tag: tag.value,
      category: category.value,
      edgeType: edgeType.value,
      includeOrphans: includeOrphans.value,
    }))
  } catch (_) { /* storage unavailable */ }
}

function restoreViewState() {
  try {
    const raw = sessionStorage.getItem(viewOwnerKey)
    if (!raw) return false
    const saved = JSON.parse(raw)
    if (saved.view) { view.value = saved.view; restoredView = saved.view }
    if (saved.selectedId) selectedId.value = saved.selectedId
    if (saved.query) query.value = saved.query
    if (saved.tag) tag.value = saved.tag
    if (saved.category) category.value = saved.category
    if (saved.edgeType) edgeType.value = saved.edgeType
    if (typeof saved.includeOrphans === 'boolean') includeOrphans.value = saved.includeOrphans
    if (saved.localNoteId) {
      localNoteId.value = saved.localNoteId
      localDepth.value = saved.localDepth || 1
    }
    return true
  } catch (_) {
    return false
  }
}

function handleViewportResize() {
  nextTick(fitView)
}

onMounted(async () => {
  window.addEventListener('resize', handleViewportResize)
  const focus = route.query.focus
  const depth = Number(route.query.depth || 1)
  if (focus) {
    localNoteId.value = String(focus)
    localDepth.value = depth >= 2 ? 2 : 1
  } else {
    restoreViewState()
  }
  await loadGraph()
  if (route.query.focus) {
    // 局部图加载后选中中心节点，直接展示详情
    const center = nodes.value.find((node) => node.id === String(route.query.focus))
    if (center) await selectNode(center)
  } else if (selectedId.value) {
    const restoredNode = nodes.value.find((node) => node.id === selectedId.value)
    if (restoredNode) await selectNode(restoredNode)
  }
})

watch(() => userStore.authEpoch, () => {
  try { sessionStorage.removeItem(viewOwnerKey) } catch (_) { /* storage unavailable */ }
  ++graphRequest
  closePicker()
  stopSimulation()
  cancelInteractions()
  clearSelection()
  nodes.value = []
  edges.value = []
  counts.value = {}
  pickerCandidates.value = []
  pickerVisible.value = false
  localNoteId.value = ''
  query.value = ''
  tag.value = ''
  category.value = ''
  restoredView = null
  loading.value = false
  errorMessage.value = '登录状态已变化，请重新打开图谱。'
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportResize)
  ++graphRequest
  closePicker()
  saveViewState()
  stopSimulation()
  cancelInteractions()
})
</script>

<style scoped>
.filter-input:disabled, .checkbox-label:has(input:disabled) { opacity: .5; cursor: not-allowed; }
.local-filter-hint { color: var(--color-text-light); font-size: 12px; margin: 0 0 12px; }
.note-graph-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: calc(100vh - 120px);
  color: var(--color-text);
}
.graph-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.header-left h1 { margin: 0; font-size: 22px; }
.graph-subtitle { font-size: 12px; color: var(--color-text-light); }
.header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.primary-btn {
  border: none; border-radius: 9px; background: var(--color-primary);
  color: #fff; padding: 8px 14px; font-size: 13px; cursor: pointer;
}
.primary-btn:disabled { opacity: .6; }
.ghost-btn {
  border: 1px solid var(--color-border); background: transparent;
  color: var(--color-text); border-radius: 9px; padding: 7px 12px;
  font-size: 13px; cursor: pointer;
}
.ghost-btn.active { border-color: var(--color-primary); color: var(--color-primary); background: color-mix(in srgb, var(--color-primary) 8%, transparent); }
.graph-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.filter-input {
  border: 1px solid var(--color-border); border-radius: 8px; padding: 7px 10px;
  background: var(--color-card); color: var(--color-text); font-size: 13px;
}
.filter-input.search { min-width: 200px; }
.checkbox-label { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-text-light); }
.counts { margin-left: auto; font-size: 12px; color: var(--color-text-light); }
.truncated { color: #b45309; margin-left: 8px; }
.graph-layout { display: grid; grid-template-columns: minmax(0, 1fr) 330px; gap: 12px; flex: 1; min-height: 0; }
.canvas-shell {
  position: relative; border: 1px solid var(--color-border); border-radius: 12px;
  background: var(--color-card); min-height: 560px; overflow: hidden;
}
.graph-canvas { width: 100%; height: 100%; min-height: 560px; cursor: grab; }
.graph-canvas svg { width: 100%; height: 100%; display: block; }
.graph-canvas:active { cursor: grabbing; }
.graph-node { cursor: pointer; transition: opacity .2s ease; }
.node-visual { animation: graph-node-in .45s ease both; }
@keyframes graph-node-in { from { opacity: 0; } to { opacity: 1; } }
.node-hit { fill: transparent !important; stroke: none !important; }
.edge-hit { stroke: transparent; stroke-width: 12; cursor: pointer; }
.graph-node circle { fill: var(--color-primary-light); stroke: var(--color-primary); stroke-width: 1.5; transition: fill .18s ease, stroke .18s ease, stroke-width .18s ease; }
.graph-node.selected circle { fill: var(--color-primary); stroke: var(--color-primary-active); stroke-width: 2.5; }
.graph-node.hovered circle { stroke-width: 3; }
.graph-node.dimmed { opacity: .22; }
.graph-node.orphan circle { stroke-dasharray: 3 3; fill: color-mix(in srgb, var(--color-primary) 18%, var(--color-card)); }
.graph-node text { font-size: 12px; fill: var(--color-text); pointer-events: none; }
.graph-edge { pointer-events: none; stroke: color-mix(in srgb, var(--color-text-light) 65%, transparent); transition: opacity .2s ease, stroke .18s ease; }
.graph-edge.manual_relation { stroke: color-mix(in srgb, var(--color-primary) 70%, transparent); }
.graph-edge.selected { stroke: var(--color-primary); }
.graph-edge.dimmed { opacity: .12; }
.canvas-footer {
  position: absolute; left: 10px; right: 10px; bottom: 10px;
  display: flex; justify-content: space-between; align-items: center; pointer-events: none;
}
.canvas-footer > * { pointer-events: auto; }
.zoom-controls { display: flex; gap: 4px; background: var(--glass-bg); border: 1px solid var(--color-border); border-radius: 8px; padding: 3px; }
.zoom-controls button { border: none; background: transparent; color: var(--color-text); cursor: pointer; padding: 4px 8px; font-size: 12px; border-radius: 6px; }
.zoom-controls button:hover { background: var(--color-surface); }
.empty-edges {
  position: absolute; top: 12px; left: 12px; right: 12px; z-index: 2;
  border: 1px dashed var(--color-border); border-radius: 8px; padding: 8px 12px;
  font-size: 12px; color: var(--color-text-light); background: var(--glass-bg);
}
.graph-list { padding: 10px; display: flex; flex-direction: column; gap: 6px; max-height: 680px; overflow-y: auto; }
.graph-list-item {
  display: flex; align-items: center; gap: 10px; border: 1px solid var(--color-border);
  border-radius: 8px; background: transparent; color: var(--color-text);
  padding: 10px 12px; cursor: pointer; text-align: left; font-size: 13px;
}
.graph-list-item.selected { border-color: var(--color-primary); }
.list-title { flex: 1; }
.list-meta { color: var(--color-text-light); font-size: 12px; }
.list-orphan { color: #b45309; font-size: 11px; }
.detail-panel {
  border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-card);
  padding: 14px; max-height: calc(100vh - 220px); overflow-y: auto;
}
.detail-header { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.detail-header h2 { font-size: 16px; margin: 0; }
.icon-btn { border: none; background: transparent; color: var(--color-text-light); cursor: pointer; font-size: 14px; }
.detail-meta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 12px; color: var(--color-text-light); margin: 6px 0; }
.tag { border: 1px solid var(--color-border); border-radius: 999px; padding: 1px 8px; }
.detail-actions { display: flex; gap: 6px; flex-wrap: wrap; margin: 10px 0 14px; }
.relation-block { border-top: 1px solid var(--color-border-light); padding-top: 10px; margin-top: 10px; }
.relation-block h3 { font-size: 13px; margin: 0 0 6px; }
.block-head { display: flex; align-items: center; justify-content: space-between; }
.relation-item {
  display: flex; flex-direction: column; gap: 2px; width: 100%; border: none;
  background: transparent; color: var(--color-text); text-align: left;
  padding: 7px 8px; border-radius: 7px; cursor: pointer; font-size: 13px;
}
.relation-item:hover { background: var(--color-surface); }
.relation-item small { color: var(--color-text-light); font-size: 11px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical; }
.relation-item.manual { flex-direction: row; align-items: center; justify-content: space-between; }
.relation-open { display: flex; flex-direction: column; border: none; background: transparent; color: var(--color-text); text-align: left; cursor: pointer; padding: 0; }
.relation-count { color: var(--color-primary); font-size: 12px; }
.link-btn { border: none; background: transparent; color: var(--color-primary); font-size: 12px; cursor: pointer; }
.link-btn.danger { color: var(--color-error); }
.empty-text { font-size: 12px; color: var(--color-text-light); margin: 4px 0; }
.unresolved-item { display: flex; flex-direction: column; gap: 2px; font-size: 12px; padding: 6px 0; }
.unresolved-item small { color: var(--color-text-light); }
.edge-path { font-size: 14px; font-weight: 600; }
.edge-reason { font-size: 13px; color: var(--color-text-light); }
.panel-empty { color: var(--color-text-light); font-size: 13px; line-height: 1.8; padding: 20px 6px; }
.hint { font-size: 12px; }
.edge-list-panel { margin-top: 14px; border-top: 1px solid var(--color-border-light); padding-top: 10px; text-align: left; }
.edge-list-panel h3 { font-size: 12px; margin: 0 0 6px; color: var(--color-text); }
.edge-list-item { display: flex; flex-direction: column; gap: 2px; width: 100%; border: none; background: transparent; color: var(--color-text); text-align: left; padding: 6px 7px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.edge-list-item:hover, .edge-list-item.selected { background: color-mix(in srgb, var(--color-primary) 9%, transparent); }
.edge-list-item small { color: var(--color-text-light); font-size: 11px; }
.state-card { border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-card); padding: 36px 20px; text-align: center; }
.state-card.error { color: var(--color-error); }
.modal-overlay { position: fixed; inset: 0; background: rgba(12, 20, 35, .45); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { width: min(460px, 92vw); background: var(--color-card); border-radius: 12px; padding: 16px; }
.modal-head { display: flex; justify-content: space-between; align-items: center; }
.modal-head h3 { margin: 0; }
.candidate-list { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; max-height: 320px; overflow-y: auto; }
.candidate-item { display: flex; justify-content: space-between; align-items: center; border: 1px solid var(--color-border); border-radius: 8px; background: transparent; color: var(--color-text); padding: 9px 10px; cursor: pointer; font-size: 13px; text-align: left; }
.candidate-item small { color: var(--color-text-light); font-size: 11px; }
@media (max-width: 900px) {
  .graph-layout { grid-template-columns: 1fr; }
  .detail-panel { max-height: none; }
}
</style>
