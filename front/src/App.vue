<template>
  <div id="app">
    <!-- 根据路由 meta.layout 选择布局 -->
    <router-view v-slot="{ Component, route }">
      <!-- 认证布局（全屏，无导航） -->
      <component
        :is="Component"
        v-if="route.meta.layout === 'auth'"
      />
      <!-- 默认 AppShell 布局：将 Component 传递给 AppShell 的 slot -->
      <AppShell v-else>
        <component :is="Component" />
      </AppShell>
    </router-view>
  </div>
</template>

<script setup>
/**
 * App.vue — 根组件
 * 根据路由 meta.layout 切换布局壳
 * - auth: 全屏无导航（登录/注册）
 * - 默认: AppShell（Sidebar + TopBar + Content）
 */
import AppShell from './layouts/AppShell.vue'
</script>

<style>
/* 全局基础样式已在 global.css 中定义 */
#app {
  min-height: 100vh;
}
</style>
