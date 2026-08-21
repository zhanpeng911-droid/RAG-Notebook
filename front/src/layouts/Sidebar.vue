<template>
  <aside class="sidebar" :class="{ collapsed: collapsed, 'is-mobile': isMobile }">
    <!-- 品牌标识 -->
    <div class="sidebar-brand">
      <div class="brand-logo">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
          <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
        </svg>
      </div>
      <span v-if="!collapsed" class="brand-text">Notebook</span>
    </div>

    <!-- 主导航 -->
    <nav class="sidebar-nav">
      <router-link
        v-for="item in mainNavItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
        :title="item.label"
      >
        <span class="nav-icon" v-html="item.icon"></span>
        <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
        <span v-if="!collapsed && item.path === '/review' && reviewDueCount > 0" class="nav-badge">{{ reviewDueCount > 99 ? '99+' : reviewDueCount }}</span>
      </router-link>
    </nav>

    <!-- 分割线 -->
    <div class="sidebar-divider"></div>

    <!-- 次要导航 -->
    <nav class="sidebar-nav sidebar-nav-secondary">
      <router-link
        v-for="item in secondaryNavItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
        :title="item.label"
      >
        <span class="nav-icon" v-html="item.icon"></span>
        <span v-if="!collapsed" class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- 企业功能 -->
    <nav class="sidebar-nav sidebar-nav-enterprise" v-if="!collapsed">
      <div class="nav-section-title">企业功能</div>
      <router-link to="/org" class="nav-item" active-class="active">
        <span class="nav-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <polyline points="9 22 9 12 15 12 15 22"/>
          </svg>
        </span>
        <span class="nav-label">组织/空间</span>
      </router-link>
      <router-link to="/org/permissions" class="nav-item" active-class="active">
        <span class="nav-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
            <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
          </svg>
        </span>
        <span class="nav-label">权限管理</span>
      </router-link>
      <router-link to="/org/audit" class="nav-item" active-class="active">
        <span class="nav-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
            <line x1="16" y1="13" x2="8" y2="13"/>
            <line x1="16" y1="17" x2="8" y2="17"/>
          </svg>
        </span>
        <span class="nav-label">审计日志</span>
      </router-link>
    </nav>

    <!-- 退出登录 -->
    <div class="sidebar-footer">
      <button class="nav-item logout-btn" @click="handleLogout" title="退出登录">
        <span class="nav-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
            <polyline points="16 17 21 12 16 7"/>
            <line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
        </span>
        <span v-if="!collapsed" class="nav-label">退出登录</span>
      </button>
    </div>

    <!-- 折叠按钮 -->
    <button class="sidebar-toggle" @click="toggleCollapse" :title="collapsed ? '展开侧边栏' : '折叠侧边栏'">
      <svg
        width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
        :style="{ transform: collapsed ? 'rotate(180deg)' : '' }"
      >
        <polyline points="15 18 9 12 15 6"/>
      </svg>
    </button>
  </aside>

  <!-- 移动端遮罩 -->
  <div
    v-if="isMobile && !collapsed"
    class="sidebar-overlay"
    @click="toggleCollapse"
  ></div>
</template>

<script setup>
/**
 * Sidebar — 桌面端左侧导航栏
 * 240px 展开态，64px 折叠态
 * 移动端隐藏，通过 AppShell 控制显隐
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '../store/user'
import { reviewApi } from '../services/reviewApi'

const props = defineProps({
  collapsed: Boolean
})

const emit = defineEmits(['update:collapsed'])

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

// 响应式 isMobile
const isMobile = ref(window.innerWidth < 768)
const reviewDueCount = ref(0)

function handleResize() {
  isMobile.value = window.innerWidth < 768
}

onMounted(async () => {
  window.addEventListener('resize', handleResize)
  if (!localStorage.getItem('jwt_token')) {
    return
  }
  try {
    const res = await reviewApi.dueCount()
    reviewDueCount.value = res?.data?.due_count ?? res?.due_count ?? 0
  } catch (_) {
    /* optional badge: keep the page usable when unavailable */
  }
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
})

// 主导航项
const mainNavItems = [
  {
    path: '/notes',
    label: '笔记',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'
  },
  {
    path: '/chat',
    label: 'AI 对话',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>'
  },
  {
    path: '/knowledge',
    label: '知识库',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>'
  },
  {
    path: '/review',
    label: '每日回顾',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>'
  },
]

// 次要导航项
const secondaryNavItems = [
  {
    path: '/profile',
    label: '我的',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
  },
  {
    path: '/settings',
    label: '设置',
    icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
  },
]

// 判断导航项是否激活
function isActive(path) {
  if (path === '/notes') {
    return route.path.startsWith('/notes')
  }
  if (path === '/chat') {
    return route.path.startsWith('/chat') || route.path.startsWith('/aichat')
  }
  if (path === '/knowledge') {
    return route.path.startsWith('/knowledge') || route.path.startsWith('/knowledgebase')
  }
  if (path === '/profile') {
    return route.path === '/profile' || route.path === '/my'
  }
  return route.path === path
}

// 切换折叠状态
function toggleCollapse() {
  emit('update:collapsed', !props.collapsed)
}

// 退出登录
async function handleLogout() {
  await userStore.logout()
  router.push('/login')
}
</script>

<style scoped>
.sidebar {
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  width: var(--sidebar-width);
  background: var(--glass-bg);
  -webkit-backdrop-filter: blur(var(--glass-blur));
  backdrop-filter: blur(var(--glass-blur));
  border-right: 1px solid var(--glass-border);
  display: flex;
  flex-direction: column;
  z-index: var(--z-sidebar);
  transition: width 0.2s ease;
  overflow: hidden;
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed-width);
}

/* 品牌标识 */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-lg);
  height: var(--topbar-height);
  border-bottom: 1px solid var(--color-border-light);
}

.brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: white;
  flex-shrink: 0;
  box-shadow: 0 2px 6px rgba(63, 140, 255, 0.35);
}

.brand-text {
  font-family: var(--font-heading);
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
}

/* 导航区域 */
.sidebar-nav {
  padding: var(--space-sm) 0;
  flex: 1;
}

.sidebar-nav-secondary {
  border-top: 1px solid var(--color-border-light);
  flex: none;
}

.sidebar-nav-enterprise {
  border-top: 1px solid var(--color-border-light);
  flex: none;
}

.nav-section-title {
  padding: var(--space-sm) var(--space-lg);
  font-size: 11px;
  font-weight: 500;
  color: var(--color-text-lightest);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

/* 导航项 */
.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-lg);
  margin: 2px var(--space-sm);
  border-radius: var(--radius-md);
  color: var(--color-text-light);
  text-decoration: none;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s ease;
  white-space: nowrap;
}

.nav-item:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

.nav-item.active {
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-weight: 500;
}

.nav-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.nav-item.disabled:hover {
  background: transparent;
  color: var(--color-text-light);
}

.nav-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex-shrink: 0;
}

.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
}

.nav-badge {
  margin-left: auto;
  min-width: 18px;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--color-primary);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
}

/* 折叠态居中 */
.collapsed .nav-item {
  justify-content: center;
  padding: var(--space-sm);
  margin: 2px var(--space-xs);
}

.collapsed .nav-label {
  display: none;
}

/* 分割线 */
.sidebar-divider {
  height: 1px;
  background: var(--color-border-light);
  margin: var(--space-xs) var(--space-lg);
}

/* 折叠按钮 */
.sidebar-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-sm);
  margin: var(--space-sm);
  border: none;
  background: transparent;
  color: var(--color-text-lighter);
  cursor: pointer;
  border-radius: var(--radius-md);
  transition: all 0.15s ease;
}

.sidebar-toggle:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

.sidebar-toggle svg {
  transition: transform 0.2s ease;
}

/* 移动端遮罩 */
.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: calc(var(--z-sidebar) - 1);
}

/* 移动端 */
.is-mobile {
  transform: translateX(-100%);
}

.is-mobile:not(.collapsed) {
  transform: translateX(0);
}

/* 折叠按钮在折叠态时居中 */
.collapsed .sidebar-toggle {
  margin: var(--space-sm) auto;
}

/* 退出登录按钮 */
.sidebar-footer {
  border-top: 1px solid var(--color-border-light);
  padding: var(--space-sm) 0;
}

.logout-btn {
  color: var(--color-text-lighter) !important;
}

.logout-btn:hover {
  color: var(--color-error) !important;
  background: rgba(199, 91, 91, 0.1) !important;
}
</style>
