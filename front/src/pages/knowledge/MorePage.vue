<template>
  <div class="more-page">
    <header class="more-header">
      <h1>更多</h1>
      <p>对话、回顾、个人资料与设置。</p>
    </header>
    <div class="more-grid">
      <button v-for="item in items" :key="item.path" class="more-card" @click="router.push(item.path)">
        <span class="more-icon" v-html="item.icon"></span>
        <strong>{{ item.label }}</strong>
        <small>{{ item.desc }}</small>
      </button>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
const items = [
  { path: '/chat', label: 'AI 对话', desc: '基于知识库和笔记提问', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>' },
  { path: '/review', label: '每日回顾', desc: '按遗忘曲线复习笔记', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>' },
  { path: '/profile', label: '个人资料', desc: '账号与个人信息', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' },
  { path: '/settings', label: '设置', desc: '模型、主题与偏好', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.14.34.4.62.73.78"/></svg>' },
  { path: '/org', label: '组织 / 空间', desc: '共享知识库与成员权限', icon: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg>' },
]
</script>

<style scoped>
.more-page { max-width: 900px; margin: 0 auto; color: var(--color-text); }
.more-header h1 { margin: 0 0 4px; font-size: 24px; }
.more-header p { margin: 0 0 20px; color: var(--color-text-light); font-size: 13px; }
.more-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; }
.more-card { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-card); color: var(--color-text); padding: 16px; cursor: pointer; text-align: left; }
.more-card:hover { border-color: var(--color-primary); }
.more-icon { color: var(--color-primary); }
.more-card small { color: var(--color-text-light); font-size: 12px; }
</style>
