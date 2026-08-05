import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  // === 认证页面（全屏，无 Sidebar/TopBar） ===
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
    meta: {
      title: '登录',
      layout: 'auth',
      keepAlive: false
    }
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('../views/Register.vue'),
    meta: {
      title: '注册',
      layout: 'auth',
      keepAlive: false
    }
  },

  // === 主布局包裹（Sidebar + TopBar） ===
  // 注意：AppShell 在 App.vue 中统一处理，无需嵌套路由
  {
    path: '/',
    redirect: '/notes'
  },
  {
    path: '/notes',
    name: 'NoteList',
    component: () => import('../pages/notes/NotesPage.vue'),
    meta: {
      title: '笔记',
      keepAlive: true
    }
  },
  {
    path: '/notes/new',
    name: 'NoteNew',
    component: () => import('../pages/notes/NoteEditorPage.vue'),
    meta: {
      title: '新建笔记',
      keepAlive: false
    }
  },
  {
    path: '/notes/:id',
    name: 'NoteEditor',
    component: () => import('../pages/notes/NoteEditorPage.vue'),
    meta: {
      title: '编辑笔记',
      keepAlive: false
    }
  },
  {
    path: '/chat',
    name: 'AIChat',
    component: () => import('../pages/chat/ChatWorkspacePage.vue'),
    meta: {
      title: 'AI 对话',
      keepAlive: true
    }
  },
  {
    path: '/chat/:sessionId',
    name: 'AIChatWithSession',
    component: () => import('../pages/chat/ChatWorkspacePage.vue'),
    meta: {
      title: 'AI 对话',
      keepAlive: true
    }
  },
  // 兼容旧路由
  {
    path: '/aichat',
    redirect: '/chat'
  },
  {
    path: '/aichat/:sessionId',
    redirect: (to) => `/chat/${to.params.sessionId}`
  },
  {
    path: '/knowledge',
    name: 'KnowledgeBase',
    component: () => import('../pages/knowledge/KnowledgeBasePage.vue'),
    meta: {
      title: '知识库管理',
      keepAlive: false
    }
  },
  // 兼容旧路由
  {
    path: '/knowledgebase',
    redirect: '/knowledge'
  },
  {
    path: '/review',
    name: 'DailyReview',
    component: () => import('../views/DailyReview.vue'),
    meta: {
      title: '每日回顾',
      keepAlive: false
    }
  },
  {
    path: '/sessions',
    redirect: '/chat'
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/Settings.vue'),
    meta: {
      title: '设置',
      keepAlive: false
    }
  },
  {
    path: '/my',
    name: 'My',
    component: () => import('../views/My.vue'),
    meta: {
      title: '我的',
      keepAlive: true
    }
  },
  {
    path: '/profile',
    name: 'Profile',
    component: () => import('../views/Profile.vue'),
    meta: {
      title: '个人信息',
      keepAlive: false
    }
  },
  {
    path: '/aboutus',
    name: 'AboutUs',
    component: () => import('../views/AboutUs.vue'),
    meta: {
      title: '关于我们',
      keepAlive: false
    }
  },

  // === 企业功能 ===
  {
    path: '/org',
    name: 'OrgSpace',
    component: () => import('../views/OrgSpace.vue'),
    meta: {
      title: '组织/空间管理',
      keepAlive: false
    }
  },
  {
    path: '/org/permissions',
    name: 'Permissions',
    component: () => import('../views/Permissions.vue'),
    meta: {
      title: '权限管理',
      keepAlive: false
    }
  },
  {
    path: '/org/audit',
    name: 'AuditLog',
    component: () => import('../views/AuditLog.vue'),
    meta: {
      title: '审计日志',
      keepAlive: false
    }
  },

  // 兜底重定向
  {
    path: '/:pathMatch(.*)*',
    redirect: '/notes'
  },
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

// 全局前置守卫
router.beforeEach((to, from, next) => {
  // 设置页面标题
  document.title = to.meta.title || 'AI Second Brain'

  // 认证页面不需要检查 token
  const publicPages = ['Login', 'Register'];
  if (publicPages.includes(to.name)) {
    next();
    return;
  }

  // 从 localStorage 检查是否有 token（Pinia persist 可能未恢复时用此兜底）
  const token = localStorage.getItem('jwt_token');

  if (!token) {
    // 未登录，跳转登录页并记录原目标
    next({ name: 'Login', query: { redirect: to.fullPath } });
  } else {
    next();
  }
})

export default router
