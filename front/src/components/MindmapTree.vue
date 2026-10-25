<template>
  <div class="mindmap-node">
    <button
      type="button"
      class="mindmap-card"
      :class="{ selected: selectedId === node.id, branch: node.children && node.children.length }"
      @click="$emit('select', node)"
    >
      <span class="mindmap-label">{{ node.label }}</span>
      <span v-if="node.anchor_ids && node.anchor_ids.length" class="anchor-dot" title="该节点有原文证据"></span>
    </button>
    <div v-if="node.children && node.children.length" class="mindmap-children">
      <MindmapTree
        v-for="child in node.children"
        :key="child.id"
        :node="child"
        :selected-id="selectedId"
        @select="$emit('select', $event)"
      />
    </div>
  </div>
</template>

<script setup>
defineOptions({ name: 'MindmapTree' })

defineProps({
  node: { type: Object, required: true },
  selectedId: { type: String, default: '' },
})
defineEmits(['select'])
</script>

<style scoped>
.mindmap-node {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
}
.mindmap-card {
  position: relative;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(15, 23, 42, 0.72);
  color: #e2e8f0;
  border-radius: 10px;
  padding: 8px 12px;
  margin: 4px 0;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  max-width: 280px;
}
.mindmap-card.branch {
  border-color: rgba(56, 189, 248, 0.4);
  background: rgba(8, 47, 73, 0.5);
}
.mindmap-card.selected {
  border-color: #38bdf8;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.25);
}
.mindmap-children {
  display: flex;
  flex-direction: column;
  margin-left: 24px;
  border-left: 1px dashed rgba(148, 163, 184, 0.35);
  padding-left: 12px;
}
.anchor-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #38bdf8;
  margin-left: 8px;
  vertical-align: middle;
}
</style>
