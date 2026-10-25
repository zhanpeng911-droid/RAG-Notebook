<template>
  <span v-if="items && items.length" class="evidence-chips">
    <button
      v-for="item in items"
      :key="item.anchor_id"
      type="button"
      class="evidence-chip"
      :title="item.quote || '查看原文证据'"
      @click.stop="$emit('open', item.anchor_id)"
    >
      {{ label(item) }}
    </button>
  </span>
</template>

<script setup>
defineProps({
  items: { type: Array, default: () => [] },
})
defineEmits(['open'])

function label(item) {
  if (item.page_no) return `第 ${item.page_no} 页`
  if (item.heading_path) return item.heading_path.split('>').pop().trim()
  return '原文片段'
}
</script>

<style scoped>
.evidence-chips {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-left: 6px;
  vertical-align: middle;
}
.evidence-chip {
  border: 1px solid rgba(56, 189, 248, 0.35);
  background: rgba(56, 189, 248, 0.08);
  color: #38bdf8;
  border-radius: 999px;
  padding: 2px 9px;
  font-size: 12px;
  cursor: pointer;
  line-height: 1.4;
}
.evidence-chip:hover {
  background: rgba(56, 189, 248, 0.18);
}
</style>
