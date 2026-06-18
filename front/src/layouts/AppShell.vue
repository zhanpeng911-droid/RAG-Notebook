<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <!-- 左侧导航栏 -->
    <Sidebar v-model:collapsed="sidebarCollapsed" />

    <!-- 右侧主区域 -->
    <div class="app-main">
      <!-- 顶部工具栏 -->
      <TopBar />

      <!-- 页面内容区：使用 slot 接收 App.vue 传递的页面组件 -->
      <div class="app-content">
        <slot />
      </div>
    </div>

    <!-- 移动端底部导航（< 768px 显示） -->
    <TabBar v-if="showTabBar" class="app-tabbar" />
  </div>
</template>

<script setup>
/**
 * AppShell — 全局布局壳
 * 桌面端：Sidebar(240px/64px) + TopBar(48px) + Content
 * 移动端：< 768px 隐藏 Sidebar，显示底部 TabBar
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import Sidebar from './Sidebar.vue'
import TopBar from './TopBar.vue'
import TabBar from '../components/TabBar.vue'

const sidebarCollapsed = ref(false)
const windowWidth = ref(window.innerWidth)

// 移动端显示 TabBar
const showTabBar = computed(() => windowWidth.value < 768)

// 响应式：小屏自动折叠侧边栏
function handleResize() {
  windowWidth.value = window.innerWidth
  if (windowWidth.value < 768) {
    sidebarCollapsed.value = true
  } else if (windowWidth.value < 1024) {
    sidebarCollapsed.value = true
  } else {
    sidebarCollapsed.value = false
  }
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--color-bg);
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  margin-left: var(--sidebar-width);
  transition: margin-left 0.2s ease;
}

.sidebar-collapsed .app-main {
  margin-left: var(--sidebar-collapsed-width);
}

.app-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-lg);
  padding-top: calc(var(--topbar-height) + var(--space-lg));
}

.app-tabbar {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: var(--z-tabbar);
}

/* 移动端：隐藏 Sidebar，内容区无左边距 */
@media (max-width: 767px) {
  .app-main {
    margin-left: 0;
    padding-bottom: var(--tabbar-height);
  }

  .app-content {
    padding-top: var(--space-md);
  }
}

/* 平板：折叠 Sidebar */
@media (min-width: 768px) and (max-width: 1023px) {
  .app-main {
    margin-left: var(--sidebar-collapsed-width);
  }
}
</style>
