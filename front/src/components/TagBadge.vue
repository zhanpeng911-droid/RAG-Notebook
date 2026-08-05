<template>
  <span class="tag-badge" :class="[`tag-badge--${color}`]" :style="{ fontSize: size + 'px' }">
    {{ tag }}
  </span>
</template>

<script setup>
defineProps({
  tag: { type: String, required: true },
  color: { type: String, default: 'default' },
  size: { type: Number, default: 12 },
})
</script>

<style scoped>
.tag-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
}
.tag-badge--default {
  background: var(--status-info-bg);
  color: var(--status-info-text);
}
.tag-badge--work {
  background: var(--status-info-bg);
  color: var(--status-info-text);
}
.tag-badge--study {
  background: var(--status-error-bg);
  color: var(--status-error-text);
}
.tag-badge--life {
  background: var(--status-success-bg);
  color: var(--status-success-text);
}
.tag-badge--project {
  background: var(--status-warning-bg);
  color: var(--status-warning-text);
}
</style>
