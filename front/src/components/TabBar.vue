<template>
  <van-tabbar v-model="active" route class="glass-tabbar">
    <van-tabbar-item to="/notes">
      <span>笔记</span>
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" />
          <line x1="16" y1="17" x2="8" y2="17" />
        </svg>
      </template>
    </van-tabbar-item>
    <van-tabbar-item to="/analysis">
      <span>分析</span>
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3l1.8 4.2L18 9l-4.2 1.8L12 15l-1.8-4.2L6 9l4.2-1.8z" />
          <path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z" />
        </svg>
      </template>
    </van-tabbar-item>
    <van-tabbar-item to="/graph">
      <span>图谱</span>
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="6" cy="6" r="3" />
          <circle cx="18" cy="8" r="3" />
          <circle cx="12" cy="18" r="3" />
          <line x1="8.5" y1="7.5" x2="15.5" y2="7.2" />
          <line x1="7.5" y1="8.7" x2="10.5" y2="15.2" />
          <line x1="16.4" y1="10.7" x2="13.2" y2="15.5" />
        </svg>
      </template>
    </van-tabbar-item>
    <van-tabbar-item to="/knowledge">
      <span>知识库</span>
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
        </svg>
      </template>
    </van-tabbar-item>
    <van-tabbar-item to="/more">
      <span>更多</span>
      <template #icon>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="5" cy="12" r="1.5" />
          <circle cx="12" cy="12" r="1.5" />
          <circle cx="19" cy="12" r="1.5" />
        </svg>
      </template>
    </van-tabbar-item>
  </van-tabbar>
</template>

<script setup>
/**
 * TabBar 底部导航栏 —— 笔记 / 分析 / 图谱 / 知识库 / 更多。
 * 旧功能（AI 对话、回顾、个人、设置）收进“更多”页面，不从产品中移除。
 */
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const active = ref(0)

function setActiveTab() {
  const path = route.path
  if (path.startsWith('/analysis') || path.endsWith('/insight')) {
    active.value = 1
  } else if (path.startsWith('/graph')) {
    active.value = 2
  } else if (path.startsWith('/knowledge') && !path.endsWith('/insight')) {
    active.value = 3
  } else if (path === '/notes' || (path.startsWith('/notes/') && !path.endsWith('/insight'))) {
    active.value = 0
  } else {
    active.value = 4
  }
}

setActiveTab()
watch(() => route.path, setActiveTab)
</script>

<style scoped>
.glass-tabbar.van-tabbar {
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-top: 1px solid var(--glass-border);
}
</style>
