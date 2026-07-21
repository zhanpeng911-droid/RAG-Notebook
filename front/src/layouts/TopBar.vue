<template>
  <header class="topbar">
    <!-- 左侧：页面标题 -->
    <div class="topbar-left">
      <h1 class="topbar-title">{{ pageTitle }}</h1>
    </div>

    <!-- 中间：全局搜索占位 -->
    <div class="topbar-center">
      <div class="search-box">
        <svg class="search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8"/>
          <line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        <input
          type="text"
          class="search-input"
          placeholder="搜索笔记..."
          v-model="searchQuery"
          @keydown.enter="handleSearch"
        />
        <kbd class="search-shortcut">⌘K</kbd>
      </div>
    </div>

    <!-- 右侧：工具按钮 + 用户菜单 -->
    <div class="topbar-right">
      <!-- 模型状态占位 -->
      <div class="model-status" title="AI 模型状态">
        <span class="status-dot"></span>
        <span class="status-text">{{ modelStore.displayName }}</span>
      </div>

      <!-- 通知按钮占位 -->
      <button class="topbar-btn" title="通知">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
          <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
        </svg>
      </button>

      <!-- 用户菜单 -->
      <div class="user-menu" @click="toggleUserMenu" ref="userMenuRef">
        <div class="user-avatar">
          <svg v-if="!userStore.getUserInfo?.avatar" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
            <circle cx="12" cy="7" r="4"/>
          </svg>
          <img v-else :src="userStore.getUserInfo.avatar" class="user-avatar-img" />
        </div>

        <!-- 下拉菜单 -->
        <div v-if="showUserMenu" class="user-dropdown">
          <div class="dropdown-header">
            <div class="dropdown-name">{{ userStore.getUserInfo?.username || '用户' }}</div>
            <div class="dropdown-email">{{ userStore.getUserInfo?.email || '' }}</div>
          </div>
          <div class="dropdown-divider"></div>
          <button class="dropdown-item" @click.stop="goToMyPage">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
              <circle cx="12" cy="7" r="4"/>
            </svg>
            个人信息
          </button>
          <button class="dropdown-item" @click.stop="goToSettings">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
            设置
          </button>
          <div class="dropdown-divider"></div>
          <button class="dropdown-item dropdown-item-danger" @click.stop="handleLogout">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
              <polyline points="16 17 21 12 16 7"/>
              <line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            退出登录
          </button>
        </div>
      </div>
    </div>
  </header>
</template>

<script setup>
/**
 * TopBar — 顶部工具栏
 * 48px 固定高度，包含全局搜索、模型状态、用户菜单
 */
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUserStore } from '../store/user'
import { useModelStore } from '../store/model'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const modelStore = useModelStore()

const searchQuery = ref('')
const showUserMenu = ref(false)
const userMenuRef = ref(null)

// 页面标题映射
const pageTitleMap = {
  '/notes': '笔记',
  '/chat': 'AI 对话',
  '/knowledge': '知识库管理',
  '/review': '每日回顾',
  '/sessions': '会话管理',
  '/settings': '设置',
  '/my': '我的',
  '/profile': '个人信息',
  '/aboutus': '关于我们',
}

const pageTitle = computed(() => {
  return pageTitleMap[route.path] || route.meta?.title || 'Notebook'
})

// 搜索
function handleSearch() {
  const q = searchQuery.value.trim()
  if (!q) return
  router.push({ path: '/notes', query: { q } })
  searchQuery.value = ''
}

// 用户菜单
function toggleUserMenu() {
  showUserMenu.value = !showUserMenu.value
}

function goToMyPage() {
  showUserMenu.value = false
  router.push('/my')
}

function goToSettings() {
  showUserMenu.value = false
  router.push('/settings')
}

async function handleLogout() {
  showUserMenu.value = false
  await userStore.logout()
  router.push('/login')
}

// 点击外部关闭菜单
function handleClickOutside(event) {
  if (userMenuRef.value && !userMenuRef.value.contains(event.target)) {
    showUserMenu.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
})
</script>

<style scoped>
.topbar {
  position: fixed;
  top: 0;
  left: var(--sidebar-width);
  right: 0;
  height: var(--topbar-height);
  background: var(--color-card);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  padding: 0 var(--space-lg);
  z-index: var(--z-topbar);
  transition: left 0.2s ease;
}

.sidebar-collapsed .topbar {
  left: var(--sidebar-collapsed-width);
}

/* 左侧标题 */
.topbar-left {
  flex-shrink: 0;
  min-width: 120px;
}

.topbar-title {
  font-family: var(--font-body);
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
}

/* 中间搜索框 */
.topbar-center {
  flex: 1;
  display: flex;
  justify-content: center;
  padding: 0 var(--space-xl);
  max-width: 560px;
  margin: 0 auto;
}

.search-box {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  width: 100%;
  max-width: 480px;
  height: 36px;
  padding: 0 var(--space-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  transition: all 0.2s ease;
}

.search-box:focus-within {
  border-color: var(--color-primary);
  background: var(--color-card);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}

.search-icon {
  color: var(--color-text-lightest);
  flex-shrink: 0;
}

.search-input {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 13px;
  color: var(--color-text);
  outline: none;
}

.search-input::placeholder {
  color: var(--color-text-lightest);
}

.search-shortcut {
  display: inline-flex;
  align-items: center;
  padding: 2px 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--color-text-lightest);
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

/* 右侧工具区 */
.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-shrink: 0;
}

/* 模型状态 */
.model-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--color-surface);
  border-radius: var(--radius-full);
  font-size: 12px;
  color: var(--color-text);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-success);
}

.status-text {
  white-space: nowrap;
}

/* 按钮 */
.topbar-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  color: var(--color-text-light);
  cursor: pointer;
  border-radius: var(--radius-md);
  transition: all 0.15s ease;
}

.topbar-btn:hover {
  background: var(--color-surface);
  color: var(--color-text);
}

/* 用户头像 */
.user-menu {
  cursor: pointer;
  position: relative;
}

.user-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--color-primary-light);
  color: var(--color-primary);
  transition: all 0.15s ease;
  box-shadow: 0 1px 3px var(--color-shadow);
}

.user-avatar:hover {
  background: var(--color-primary);
  color: white;
}

.user-avatar-img {
  width: 100%;
  height: 100%;
  border-radius: 50%;
  object-fit: cover;
}

/* 用户下拉菜单 */
.user-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: var(--space-sm);
  width: 200px;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 4px 12px var(--color-shadow-strong);
  z-index: 1000;
  overflow: hidden;
}

.dropdown-header {
  padding: var(--space-md);
  background: var(--color-surface);
}

.dropdown-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 2px;
}

.dropdown-email {
  font-size: 12px;
  color: var(--color-text-lighter);
}

.dropdown-divider {
  height: 1px;
  background: var(--color-border-light);
}

.dropdown-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: none;
  background: transparent;
  color: var(--color-text);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.dropdown-item:hover {
  background: var(--color-surface);
}

.dropdown-item-danger {
  color: var(--color-error);
}

.dropdown-item-danger:hover {
  background: rgba(199, 91, 91, 0.1);
}

/* 移动端适配 */
@media (max-width: 767px) {
  .topbar {
    left: 0;
    padding: 0 var(--space-md);
  }

  .topbar-center {
    padding: 0 var(--space-sm);
  }

  .search-shortcut {
    display: none;
  }

  .model-status {
    display: none;
  }
}

/* 平板适配 */
@media (min-width: 768px) and (max-width: 1023px) {
  .topbar {
    left: var(--sidebar-collapsed-width);
  }
}
</style>
