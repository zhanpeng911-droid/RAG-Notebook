<template>
  <div v-if="hasTrace" class="retrieval-trace">
    <!-- 阶段进度条 -->
    <div class="trace-stages">
      <div
        v-for="stage in stages"
        :key="stage.key"
        class="trace-stage"
        :class="{ active: stage.reached, current: stage.key === currentStage }"
      >
        <span class="trace-stage-dot"></span>
        <span class="trace-stage-name">{{ stage.label }}</span>
      </div>
      <div v-if="cragTriggered" class="trace-stage crag" title="置信度过低，已触发 CRAG 纠错回路">
        <span class="trace-stage-dot"></span>
        <span class="trace-stage-name">纠错回路</span>
      </div>
    </div>

    <!-- 过程摘要 -->
    <div class="trace-summary">
      <span v-if="planInfo" class="trace-chip">
        {{ queryTypeLabel }} · top {{ planInfo.top_k }}
      </span>
      <span v-if="retrievalInfo" class="trace-chip">
        召回 {{ retrievalInfo.evidence_count }} 条
      </span>
      <span v-if="gradingInfo" class="trace-chip" :class="`conf-${gradingInfo.confidence_level}`">
        置信度 {{ (gradingInfo.confidence * 100).toFixed(0) }}%
      </span>
    </div>
  </div>
</template>

<script setup>
/**
 * RetrievalTrace — Agentic RAG 检索链路可视化摘要
 *
 * 从消息 thinking steps 的 details 中提取后端透传的过程数据
 * （plan / retrieval / grading / rewrite），渲染阶段进度条与关键指标。
 * 无结构化数据（旧消息或非 Agentic 会话）时不渲染任何内容。
 */
import { computed } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
})

const QUERY_TYPE_LABELS = {
  simple: '简单',
  factual: '事实',
  explanatory: '解释',
  comparative: '对比',
  procedural: '步骤',
  exploratory: '探索',
}

const planInfo = computed(() => {
  const step = props.steps.find((s) => s.details?.plan)
  return step?.details?.plan || null
})

const retrievalInfo = computed(() => {
  const step = props.steps.find((s) => s.details?.retrieval)
  return step?.details?.retrieval || null
})

const gradingInfo = computed(() => {
  const step = props.steps.find((s) => s.details?.grading)
  return step?.details?.grading || null
})

const rewriteInfo = computed(() => {
  const step = props.steps.find((s) => s.details?.rewrite)
  return step?.details?.rewrite || null
})

const cragTriggered = computed(() => Boolean(rewriteInfo.value?.crag_triggered))

const queryTypeLabel = computed(() => {
  const type = planInfo.value?.query_type
  return QUERY_TYPE_LABELS[type] || type || '检索'
})

const reachedStages = computed(() => new Set(props.steps.map((s) => s.stage)))

// CRAG 二轮检索会重复 emitting retrieving 阶段；取最后到达的阶段作为当前阶段
const currentStage = computed(() => {
  const order = ['planning', 'retrieving', 'retrieval_completed', 'grading_evidence', 'rewriting_query', 'generating_answer', 'citation']
  for (let i = order.length - 1; i >= 0; i--) {
    if (reachedStages.value.has(order[i])) return order[i]
  }
  return null
})

const stages = computed(() => [
  { key: 'planning', label: '规划', reached: reachedStages.value.has('planning') },
  { key: 'retrieving', label: '检索', reached: reachedStages.value.has('retrieving') },
  { key: 'grading_evidence', label: '评估', reached: reachedStages.value.has('grading_evidence') },
  { key: 'generating_answer', label: '生成', reached: reachedStages.value.has('generating_answer') },
])

const hasTrace = computed(() => Boolean(planInfo.value || retrievalInfo.value || gradingInfo.value))
</script>

<style scoped>
.retrieval-trace {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 6px 8px;
  margin-bottom: var(--space-sm);
  background-color: var(--color-primary-softer);
  border-radius: var(--radius-md);
}

.trace-stages {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-wrap: wrap;
}

.trace-stage {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--color-text-lightest);
}

.trace-stage.active {
  color: var(--color-text-light);
}

.trace-stage.current .trace-stage-name {
  font-weight: 600;
  color: var(--color-primary);
}

.trace-stage-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-border);
}

.trace-stage.active .trace-stage-dot {
  background: var(--color-primary);
}

.trace-stage.crag {
  color: var(--color-warning, #d97706);
  margin-left: 4px;
}

.trace-stage.crag .trace-stage-dot {
  background: var(--color-warning, #d97706);
}

.trace-stage:not(:last-child)::after {
  content: '—';
  margin: 0 4px;
  color: var(--color-border);
}

.trace-summary {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.trace-chip {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: var(--radius-full);
  background: var(--color-card);
  border: 1px solid var(--color-border-light);
  color: var(--color-text-lighter);
  white-space: nowrap;
}

.trace-chip.conf-high {
  color: var(--color-success, #059669);
  border-color: var(--color-success, #059669);
}

.trace-chip.conf-medium {
  color: var(--color-primary);
  border-color: var(--color-primary);
}

.trace-chip.conf-low,
.trace-chip.conf-none {
  color: var(--color-warning, #d97706);
  border-color: var(--color-warning, #d97706);
}
</style>
