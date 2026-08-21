import { defineStore } from 'pinia';

/**
 * 问答记录 store —— 右栏"问答记录"面板的持久化数据源。
 *
 * 之前 qaHistory 存在 useChatWorkspace composable 的 ref 中，
 * 离开对话页（路由切换）组件销毁即丢失。改为 Pinia + sessionStorage：
 * - 跨路由保留：去笔记页/知识库再回来，记录仍在
 * - 刷新页面保留：sessionStorage 兜底（关闭标签页才清空）
 */
const STORAGE_KEY = 'qa-history-store';
const QA_HISTORY_LIMIT = 10;

function loadFromStorage() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const data = JSON.parse(raw);
    if (!Array.isArray(data)) return [];
    // 过滤无效条目并限制数量
    return data
      .filter((item) => item && item.id && typeof item.question === 'string')
      .slice(0, QA_HISTORY_LIMIT);
  } catch {
    return [];
  }
}

function saveToStorage(history) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(history.slice(0, QA_HISTORY_LIMIT)));
  } catch {
    // 存储满等异常静默忽略（记录仅是辅助信息）
  }
}

export const useQaHistoryStore = defineStore('qaHistory', {
  state: () => ({
    history: loadFromStorage(),
    expandedId: null,
  }),

  getters: {
    limit: () => QA_HISTORY_LIMIT,
  },

  actions: {
    push({ question, thinking = [], citations = [], relatedNotes = [] }) {
      const snapshot = {
        id: `qa-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        question,
        thinking,
        citations,
        relatedNotes,
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      };
      this.history.unshift(snapshot);
      if (this.history.length > QA_HISTORY_LIMIT) {
        this.history.length = QA_HISTORY_LIMIT;
      }
      this.expandedId = snapshot.id;
      saveToStorage(this.history);
      return snapshot;
    },

    toggleExpand(id) {
      this.expandedId = this.expandedId === id ? null : id;
    },

    clear() {
      this.history = [];
      this.expandedId = null;
      try {
        sessionStorage.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
    },
  },
});
