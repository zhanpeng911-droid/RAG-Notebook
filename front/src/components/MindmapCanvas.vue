<template>
  <div ref="wrapper" class="mindmap-canvas">
    <details v-if="mindmap.edit_conflicts?.length" class="mm-conflicts">
      <summary>{{ mindmap.edit_conflicts.length }} 条旧修改无法映射到当前主题，已保留且未自动套用</summary>
      <p>原文或主题已变化。请核对以下旧标题，再选择正确节点手动编辑。</p>
      <ul><li v-for="edit in mindmap.edit_conflicts" :key="edit.node_id">{{ edit.label || '旧排序修改' }}</li></ul>
    </details>
    <div class="mm-toolbar">
      <input v-model="query" class="mm-search" placeholder="搜索节点…" @input="onSearch" />
      <button class="mm-btn" @click="expandAll">展开全部</button>
      <button class="mm-btn" @click="collapseAll">折叠</button>
      <span class="mm-spacer"></span>
      <button class="mm-btn" title="放大" @click="zoomBy(1.15)">＋</button>
      <button class="mm-btn" title="缩小" @click="zoomBy(1 / 1.15)">－</button>
      <button class="mm-btn" @click="fit">适应</button>
      <button class="mm-btn" @click="reset">重置</button>
      <button class="mm-btn" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</button>
      <button class="mm-btn" @click="exportSvg">SVG</button>
      <button class="mm-btn" @click="exportPng">PNG</button>
      <button class="mm-btn" @click="exportMarkdown">Markdown</button>
      <small class="mm-export-hint">SVG/PNG：当前展开分支 · Markdown：完整导图</small>
    </div>

    <div
      ref="viewport"
      class="mm-viewport"
      @pointerdown="startPan"
      @pointermove="movePan"
      @pointerup="endPan"
      @pointerleave="endPan"
      @wheel.prevent="onWheel"
    >
      <svg ref="svgRef" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg">
        <g :transform="`translate(${view.x},${view.y}) scale(${view.zoom})`">
          <path
            v-for="edge in layout.edges"
            :key="edge.id"
            :d="edge.path"
            class="mm-edge"
          />
          <g
            v-for="node in layout.nodes"
            :key="node.id"
            :transform="`translate(${node.x},${node.y})`"
            class="mm-node-group"
          >
            <rect
              :width="node.width"
              :height="node.height"
              rx="10"
              class="mm-node"
              :class="{ selected: selectedId === node.id, matched: matches.has(node.id), dimmed: query && !matches.has(node.id) }"
              @click.stop="selectNode(node)"
              @dblclick.stop="startEdit(node)"
            />
            <text class="mm-label" x="12" y="18">
              <tspan v-for="(line, index) in node.lines" :key="index" x="12" :dy="index === 0 ? 0 : 17">
                {{ line }}
              </tspan>
            </text>
            <circle v-if="node.user_edited" class="mm-edited" :cx="node.width - 10" cy="10" r="4" />
            <g
              v-if="node.hasChildren"
              class="mm-toggle"
              :transform="`translate(${node.width},${node.height / 2})`"
              @click.stop="toggleCollapse(node.id)"
            >
              <circle r="9" />
              <text text-anchor="middle" dy="4">{{ collapsed.has(node.id) ? '+' : '−' }}</text>
            </g>
          </g>
        </g>
      </svg>
    </div>

    <div v-if="selected" class="mm-selection">
      <div class="mm-selection-text">
        <strong>{{ selected.label }}</strong>
        <span>{{ selected.anchor_ids?.length || 0 }} 条来源</span>
      </div>
      <div class="mm-selection-actions">
        <button v-if="selected.anchor_ids?.length" class="mm-btn" @click="$emit('anchor', selected.anchor_ids[0])">查看来源</button>
        <button class="mm-btn" @click="startEdit(selected)">编辑标题</button>
        <button class="mm-btn" :disabled="!canMove(-1)" @click="moveSibling(-1)">上移</button>
        <button class="mm-btn" :disabled="!canMove(1)" @click="moveSibling(1)">下移</button>
      </div>
    </div>

    <div v-if="editing" class="mm-modal-overlay" @click="editing = null">
      <div class="mm-modal" @click.stop>
        <h3>编辑节点标题</h3>
        <input v-model="editLabel" class="mm-search" maxlength="500" @keyup.enter="saveLabel" />
        <div class="mm-modal-actions">
          <button class="mm-btn" @click="editing = null">取消</button>
          <button class="mm-btn primary" :disabled="savingEdit" @click="saveLabel">
            {{ savingEdit ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { showToast } from 'vant'
import { insightApi } from '../services/insightApi'

const props = defineProps({
  mindmap: { type: Object, required: true },
  revision: { type: String, default: 'base' },
  sourceType: { type: String, required: true },
  sourceId: { type: String, required: true },
})
const emit = defineEmits(['select', 'anchor', 'saved'])

const wrapper = ref(null)
const viewport = ref(null)
const svgRef = ref(null)
const query = ref('')
const selectedId = ref('')
const collapsed = ref(new Set())
const view = ref({ x: 60, y: 60, zoom: 1 })
const editing = ref(null)
const editLabel = ref('')
const savingEdit = ref(false)
let editRequest = 0
const isFullscreen = ref(false)

let panning = false
let panStart = { x: 0, y: 0 }

const rawNodeMap = computed(() => {
  const map = {}
  const parentMap = {}
  function walk(node, parent) {
    map[node.id] = node
    if (parent) parentMap[node.id] = parent.id
    for (const child of node.children || []) walk(child, node)
  }
  walk(props.mindmap.root, null)
  return { map, parentMap }
})

const rawParentMap = computed(() => rawNodeMap.value.parentMap)

const selected = computed(() => rawNodeMap.value.map[selectedId.value] || null)

const selectedSiblings = computed(() => {
  if (!selected.value) return []
  const parentId = rawParentMap.value[selectedId.value]
  if (!parentId) return []
  return rawNodeMap.value.map[parentId]?.children || []
})

const matches = computed(() => {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return new Set()
  return new Set(Object.values(rawNodeMap.value.map)
    .filter((node) => String(node.label || '').toLowerCase().includes(needle))
    .map((node) => node.id))
})

const layout = computed(() => {
  const nodes = []
  const pairs = []
  let leafIndex = 0

  function wrap(text) {
    const value = String(text || '')
    const lines = []
    for (let i = 0; i < value.length && lines.length < 4; i += 15) {
      lines.push(value.slice(i, i + 15))
    }
    if (value.length > 60 && lines.length) {
      lines[lines.length - 1] = `${lines[lines.length - 1].slice(0, 14)}…`
    }
    return lines.length ? lines : ['未命名']
  }

  function visit(node, depth, parent) {
    const children = collapsed.value.has(node.id) ? [] : (node.children || [])
    const childYs = children.map((child) => visit(child, depth + 1, node))
    let y
    if (childYs.length === 0) {
      y = leafIndex * 78
      leafIndex += 1
    } else {
      y = (Math.min(...childYs) + Math.max(...childYs)) / 2
    }
    const lines = wrap(node.label)
    const height = Math.max(46, lines.length * 17 + 20)
    nodes.push({
      id: node.id,
      label: node.label,
      lines,
      x: depth * 270,
      y,
      width: 220,
      height,
      hasChildren: (node.children || []).length > 0,
      anchor_ids: node.anchor_ids || [],
      user_edited: !!node.user_edited,
    })
    if (parent) pairs.push([parent.id, node.id])
    return y
  }

  visit(props.mindmap.root, 0, null)
  const nodeMap = Object.fromEntries(nodes.map((node) => [node.id, node]))
  const edges = pairs.map(([parentId, childId]) => {
    const parent = nodeMap[parentId]
    const child = nodeMap[childId]
    const parentX = parent.x + parent.width
    const parentY = parent.y + parent.height / 2
    const childX = child.x
    const childY = child.y + child.height / 2
    const mid = (parentX + childX) / 2
    return {
      id: `${parentId}->${childId}`,
      path: `M ${parentX} ${parentY} C ${mid} ${parentY}, ${mid} ${childY}, ${childX} ${childY}`,
    }
  })
  return { nodes, edges }
})

const canvasWidth = computed(() => Math.max(900, ...layout.value.nodes.map((n) => n.x + 300)))
const canvasHeight = computed(() => Math.max(560, ...layout.value.nodes.map((n) => n.y + 160)))

function collectDefaultCollapsed() {
  const next = new Set()
  function walk(node, depth) {
    if (depth >= 2 && (node.children || []).length) next.add(node.id)
    for (const child of node.children || []) walk(child, depth + 1)
  }
  walk(props.mindmap.root, 0)
  collapsed.value = next
}

function toggleCollapse(nodeId) {
  const next = new Set(collapsed.value)
  if (next.has(nodeId)) next.delete(nodeId)
  else next.add(nodeId)
  collapsed.value = next
}

function expandAll() {
  collapsed.value = new Set()
  nextTick(fit)
}

function collapseAll() {
  const next = new Set()
  function walk(node, depth) {
    if (depth >= 1 && (node.children || []).length) next.add(node.id)
    for (const child of node.children || []) walk(child, depth + 1)
  }
  walk(props.mindmap.root, 0)
  collapsed.value = next
}

function selectNode(node) {
  selectedId.value = node.id
  emit('select', node)
}

function startEdit(node) {
  editing.value = node
  editLabel.value = node.label || ''
}

async function saveLabel() {
  if (!editing.value || savingEdit.value) return
  const label = editLabel.value.trim()
  if (!label) {
    showToast('标题不能为空')
    return
  }
  savingEdit.value = true
  const request = ++editRequest
  try {
    const result = await insightApi.patchMindmap(
      props.sourceType, props.sourceId, props.revision,
      [{ node_id: editing.value.id, label }])
    if (request !== editRequest) return
    editing.value = null
    emit('saved', result)
    showToast('标题已保存')
  } catch (error) {
    if (request !== editRequest) return
    showToast(error.response?.status === 409
      ? '导图已被其他修改更新，请刷新后重试'
      : (error.response?.data?.detail || '保存失败'))
  } finally {
    if (request === editRequest) savingEdit.value = false
  }
}

function canMove(direction) {
  const siblings = selectedSiblings.value
  if (!selected.value || siblings.length < 2) return false
  const index = siblings.findIndex((item) => item.id === selectedId.value)
  return index >= 0 && index + direction >= 0 && index + direction < siblings.length
}

async function moveSibling(direction) {
  const siblings = [...selectedSiblings.value]
  const index = siblings.findIndex((item) => item.id === selectedId.value)
  const target = index + direction
  if (index < 0 || target < 0 || target >= siblings.length) return
  ;[siblings[index], siblings[target]] = [siblings[target], siblings[index]]
  const request = ++editRequest
  try {
    const result = await insightApi.patchMindmap(
      props.sourceType, props.sourceId, props.revision,
      siblings.map((item, order) => ({ node_id: item.id, sort_order: order })))
    if (request !== editRequest) return
    emit('saved', result)
    showToast('顺序已保存')
  } catch (error) {
    showToast(error.response?.status === 409
      ? '导图已被其他修改更新，请刷新后重试'
      : (error.response?.data?.detail || '保存失败'))
  }
}

function onSearch() {
  const needle = query.value.trim().toLowerCase()
  if (!needle) return
  const hit = Object.values(rawNodeMap.value.map)
    .find((node) => String(node.label || '').toLowerCase().includes(needle))
  if (!hit) return
  let parentId = rawParentMap.value[hit.id]
  const next = new Set(collapsed.value)
  while (parentId) {
    next.delete(parentId)
    parentId = rawParentMap.value[parentId]
  }
  collapsed.value = next
  selectedId.value = hit.id
  emit('select', hit)
  nextTick(() => focusNode(hit.id))
}

function focusNode(nodeId) {
  const node = layout.value.nodes.find((item) => item.id === nodeId)
  const container = viewport.value
  if (!node || !container) return
  const rect = container.getBoundingClientRect()
  view.value = {
    zoom: Math.max(view.value.zoom, 0.9),
    x: rect.width / 2 - node.x * view.value.zoom,
    y: rect.height / 2 - node.y * view.value.zoom,
  }
}

function fit() {
  const container = viewport.value
  if (!container || layout.value.nodes.length === 0) return
  const rect = container.getBoundingClientRect()
  const xs = layout.value.nodes.map((node) => node.x)
  const ys = layout.value.nodes.map((node) => node.y)
  const minX = Math.min(...xs) - 40
  const maxX = Math.max(...xs) + 240
  const minY = Math.min(...ys) - 40
  const maxY = Math.max(...ys) + 120
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  const zoom = Math.max(0.25, Math.min(1.2, Math.min(rect.width / width, rect.height / height)))
  view.value = {
    zoom,
    x: (rect.width - width * zoom) / 2 - minX * zoom,
    y: (rect.height - height * zoom) / 2 - minY * zoom,
  }
}

function reset() {
  view.value = { x: 60, y: 60, zoom: 1 }
}

function zoomBy(factor) {
  const rect = viewport.value?.getBoundingClientRect()
  if (!rect) return
  const nextZoom = Math.max(0.2, Math.min(2.5, view.value.zoom * factor))
  const cx = rect.width / 2
  const cy = rect.height / 2
  const worldX = (cx - view.value.x) / view.value.zoom
  const worldY = (cy - view.value.y) / view.value.zoom
  view.value = { zoom: nextZoom, x: cx - worldX * nextZoom, y: cy - worldY * nextZoom }
}

function onWheel(event) {
  const rect = viewport.value?.getBoundingClientRect()
  if (!rect) return
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextZoom = Math.max(0.2, Math.min(2.5, view.value.zoom * factor))
  const px = event.clientX - rect.left
  const py = event.clientY - rect.top
  const worldX = (px - view.value.x) / view.value.zoom
  const worldY = (py - view.value.y) / view.value.zoom
  view.value = { zoom: nextZoom, x: px - worldX * nextZoom, y: py - worldY * nextZoom }
}

function startPan(event) {
  if (event.button !== 0) return
  if (event.target.closest('.mm-node-group')) return
  panning = true
  panStart = { x: event.clientX - view.value.x, y: event.clientY - view.value.y }
}

function movePan(event) {
  if (!panning) return
  view.value = { ...view.value, x: event.clientX - panStart.x, y: event.clientY - panStart.y }
}

function endPan() {
  panning = false
}

async function toggleFullscreen() {
  if (!wrapper.value) return
  if (document.fullscreenElement) await document.exitFullscreen()
  else await wrapper.value.requestFullscreen?.()
  setTimeout(fit, 120)
}

function onFullscreenChange() {
  isFullscreen.value = !!document.fullscreenElement
  nextTick(() => setTimeout(fit, 80))
}

function download(filename, content, type) {
  const blob = content instanceof window.Blob ? content : new window.Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

function styledSvgClone() {
  const clone = svgRef.value.cloneNode(true)
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  clone.setAttribute('width', String(canvasWidth.value))
  clone.setAttribute('height', String(canvasHeight.value))
  clone.setAttribute('viewBox', `0 0 ${canvasWidth.value} ${canvasHeight.value}`)
  const group = clone.querySelector('g')
  if (group) group.setAttribute('transform', 'translate(40,40)')
  const styles = getComputedStyle(wrapper.value)
  const card = styles.getPropertyValue('--color-card').trim() || '#ffffff'
  const text = styles.getPropertyValue('--color-text').trim() || '#0f172a'
  const border = styles.getPropertyValue('--color-border').trim() || '#cbd5e1'
  const primary = styles.getPropertyValue('--color-primary').trim() || '#1e40af'
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
  style.textContent = `
    .mm-node { fill: ${card}; stroke: ${border}; }
    .mm-node.selected { stroke: ${primary}; stroke-width: 2.4; }
    .mm-label { fill: ${text}; font: 12px sans-serif; }
    .mm-edge { fill: none; stroke: ${border}; stroke-width: 1.4; }
    .mm-toggle circle { fill: ${card}; stroke: ${border}; }
    .mm-toggle text { fill: ${text}; font: 11px sans-serif; }
    .mm-edited { fill: ${primary}; }
  `
  clone.insertBefore(style, clone.firstChild)
  return clone
}

function exportSvg() {
  const clone = styledSvgClone()
  download(`mindmap-${Date.now()}.svg`, new window.XMLSerializer().serializeToString(clone), 'image/svg+xml')
}

async function exportPng() {
  const clone = styledSvgClone()
  const data = new window.XMLSerializer().serializeToString(clone)
  const image = new window.Image()
  const svgBlob = new window.Blob([data], { type: 'image/svg+xml;charset=utf-8' })
  const url = URL.createObjectURL(svgBlob)
  try {
    await new Promise((resolve, reject) => {
      image.onload = resolve
      image.onerror = reject
      image.src = url
    })
    const canvas = document.createElement('canvas')
    canvas.width = canvasWidth.value
    canvas.height = canvasHeight.value
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = getComputedStyle(wrapper.value).getPropertyValue('--color-card').trim() || '#fff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(image, 0, 0)
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'))
    download(`mindmap-${Date.now()}.png`, blob, 'image/png')
  } catch (_) {
    showToast('PNG 导出失败，请改用 SVG')
  } finally {
    URL.revokeObjectURL(url)
  }
}

function exportMarkdown() {
  const lines = ['<!-- 导出范围：完整导图，含折叠分支 -->', '']
  function walk(node, depth) {
    const sources = (node.anchor_ids || []).map((id, index) =>
      `[来源 ${index + 1}](/analysis/${encodeURIComponent(props.sourceType)}/${encodeURIComponent(props.sourceId)}?anchor=${encodeURIComponent(id)})`).join(' ')
    lines.push(`${'  '.repeat(depth)}- ${node.label}${sources ? ` ${sources}` : ''}`)
    for (const child of node.children || []) walk(child, depth + 1)
  }
  walk(props.mindmap.root, 0)
  download(`mindmap-${Date.now()}.md`, lines.join('\n'), 'text/markdown;charset=utf-8')
}

watch(() => props.mindmap, () => {
  collectDefaultCollapsed()
  selectedId.value = ''
  nextTick(fit)
}, { deep: false })

function handleViewportResize() {
  nextTick(fit)
}

onMounted(() => {
  window.addEventListener('resize', handleViewportResize)
  collectDefaultCollapsed()
  document.addEventListener('fullscreenchange', onFullscreenChange)
  isFullscreen.value = !!document.fullscreenElement
  nextTick(fit)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleViewportResize)
  ++editRequest
  document.removeEventListener('fullscreenchange', onFullscreenChange)
  if (document.fullscreenElement) document.exitFullscreen?.()
})
</script>

<style scoped>
.mm-conflicts { padding: 10px 14px; background: color-mix(in srgb, var(--color-primary) 9%, var(--color-card)); color: var(--color-text); font-size: 12px; }
.mm-conflicts summary { cursor: pointer; }
.mindmap-canvas {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 520px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-card);
  overflow: hidden;
  position: relative;
}
.mm-toolbar {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 8px 10px; border-bottom: 1px solid var(--color-border-light);
}
.mm-search {
  border: 1px solid var(--color-border); border-radius: 7px; padding: 6px 9px;
  background: var(--color-bg); color: var(--color-text); font-size: 12px; min-width: 160px;
}
.mm-btn {
  border: 1px solid var(--color-border); background: transparent; color: var(--color-text);
  border-radius: 7px; padding: 5px 8px; font-size: 12px; cursor: pointer;
}
.mm-btn:hover { background: var(--color-surface); }
.mm-btn.primary { background: var(--color-primary); border-color: var(--color-primary); color: #fff; }
.mm-btn:disabled { opacity: .4; cursor: default; }
.mm-spacer { flex: 1; }
.mm-export-hint { width: 100%; color: var(--color-text-light); font-size: 11px; }
.mm-viewport { flex: 1; min-height: 0; overflow: hidden; cursor: grab; background: var(--color-bg); }
.mindmap-canvas:fullscreen { width: 100vw; height: 100vh; min-height: 100vh; border-radius: 0; background: var(--color-bg); }
.mindmap-canvas:fullscreen .mm-viewport { min-height: 0; background: var(--color-bg); }
.mm-viewport:active { cursor: grabbing; }
.mm-node { fill: var(--color-card); stroke: var(--color-border); stroke-width: 1.2; cursor: pointer; }
.mm-node.selected { stroke: var(--color-primary); stroke-width: 2.4; }
.mm-node.matched { stroke: #f59e0b; stroke-width: 2.2; }
.mm-node.dimmed { opacity: .3; }
.mm-label { fill: var(--color-text); font-size: 12px; pointer-events: none; }
.mm-edge { fill: none; stroke: var(--color-border); stroke-width: 1.4; }
.mm-toggle { cursor: pointer; }
.mm-toggle circle { fill: var(--color-card); stroke: var(--color-border); }
.mm-toggle text { fill: var(--color-text-light); font-size: 11px; }
.mm-edited { fill: var(--color-primary); }
.mm-selection {
  position: absolute; left: 10px; right: 10px; bottom: 10px;
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  background: var(--glass-bg); border: 1px solid var(--color-border);
  border-radius: 9px; padding: 8px 10px; font-size: 12px;
}
.mm-selection-text { display: flex; flex-direction: column; gap: 2px; }
.mm-selection-text span { color: var(--color-text-light); font-size: 11px; }
.mm-selection-actions { display: flex; gap: 5px; flex-wrap: wrap; }
.mm-modal-overlay { position: fixed; inset: 0; background: rgba(12,20,35,.45); display: flex; align-items: center; justify-content: center; z-index: 60; }
.mm-modal { width: min(420px, 92vw); background: var(--color-card); border-radius: 12px; padding: 16px; }
.mm-modal h3 { margin: 0 0 10px; font-size: 15px; }
.mm-modal .mm-search { width: 100%; box-sizing: border-box; }
.mm-modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
</style>
