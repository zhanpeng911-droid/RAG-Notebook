/**
 * Chat workspace session + messaging logic (extracted from ChatWorkspacePage).
 */
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { showToast } from 'vant'
import { marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js'
import { chatApi } from '../services/chatApi'
import { sessionApi } from '../services/sessionApi'
import { noteApi } from '../services/noteApi'
import { useUserStore } from '../store/user'
import { useModelStore } from '../store/model'
import { useSessionStore } from '../store/session'
import { useQaHistoryStore } from '../store/qaHistory'

const stageConfig = {
  retrieval: { label: '检索', color: '#3f8cff' },
  hyde: { label: 'HyDE', color: '#5ea8ff' },
  reorder: { label: '重排序', color: '#2a78f0' },
  summarize: { label: '总结', color: '#22a060' },
}

const WELCOME_MSG = {
  role: 'assistant',
  content: '你好！我是智能笔记助手，帮你整理笔记、优化内容、回答关于笔记的问题。',
}

export function useChatWorkspace() {
  const router = useRouter()
  const route = useRoute()
  const userStore = useUserStore()
  const sessionStore = useSessionStore()
  const modelStore = useModelStore()

  const messages = ref([{ ...WELCOME_MSG }])
  const userInput = ref('')
  const messagesContainer = ref(null)
  const isLoading = ref(false)
  const isSessionsLoading = ref(false)
  const sessionId = ref('')
  const autoCollapseTimer = ref(null)
  let abortStream = null

  const sessionPanelCollapsed = ref(false)
  const referencePanelCollapsed = ref(false)
  const sessionSearchQuery = ref('')
  const activeReferenceTab = ref('references')

  const relatedNotes = ref([])
  const relatedLoading = ref(false)
  const relatedQuery = ref('')
  const sessions = ref([])
  const relatedCache = ref(new Map())

  const showWelcome = computed(
    () => messages.value.length === 1 && messages.value[0].role === 'assistant'
  )
  const currentSessionId = computed(
    () => sessionId.value || route.params.sessionId || ''
  )
  const filteredSessions = computed(() => {
    if (!sessionSearchQuery.value.trim()) return sessions.value
    const query = sessionSearchQuery.value.toLowerCase()
    return sessions.value.filter((s) => (s.title || '').toLowerCase().includes(query))
  })
  const currentThinkingSteps = computed(() => {
    const lastAssistant = [...messages.value].reverse().find((m) => m.role === 'assistant')
    return lastAssistant?.thinking || []
  })
  const referenceDocuments = computed(() => {
    const docs = []
    for (const step of currentThinkingSteps.value) {
      if (step.details?.documents) docs.push(...step.details.documents)
    }
    return docs
  })

  // ===== 右栏问答记录栈：Pinia store 持久化（跨路由/刷新保留最近 N 组） =====
  // 数据源迁移至 store/qaHistory.js：离开对话页再回来记录不丢
  const qaHistoryStore = useQaHistoryStore()
  const qaHistory = computed(() => qaHistoryStore.history)
  const expandedQaId = computed(() => qaHistoryStore.expandedId)
  const QA_HISTORY_LIMIT = qaHistoryStore.limit

  function pushQaSnapshot({ question, thinking = [], citations = [], relatedNotes = [] }) {
    return qaHistoryStore.push({ question, thinking, citations, relatedNotes })
  }

  function toggleQaExpand(id) {
    qaHistoryStore.toggleExpand(id)
  }

  function clearQaHistory() {
    qaHistoryStore.clear()
  }

  const quickQuestions = [
    '帮我整理笔记要点',
    '如何写出更好的笔记？',
    '总结这篇笔记的核心内容',
    '为我的笔记添加标签建议',
  ]
  const referenceTabs = [
    { key: 'references', label: '引用来源' },
    { key: 'retrieval', label: '检索过程' },
    { key: 'notes', label: '相关笔记' },
  ]

  marked.use(
    markedHighlight({
      langPrefix: 'hljs language-',
      highlight(code, lang) {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext'
        return hljs.highlight(code, { language }).value
      },
    })
  )

  function getStageLabel(stage) {
    return stageConfig[stage]?.label || stage || '处理中'
  }
  function getStageColor(stage) {
    return stageConfig[stage]?.color || '#999'
  }
  function truncateText(text, maxLen) {
    if (!text) return ''
    return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
  }
  function formatMessage(content) {
    if (!content) return ''
    try {
      const parsed = marked(content, {
        breaks: true,
        gfm: true,
        headerIds: false,
        mangle: false,
      })
      return DOMPurify.sanitize(parsed)
    } catch (error) {
      console.error('Markdown解析错误:', error)
      // 兜底也必须消毒：该返回值直接进入 v-html
      return DOMPurify.sanitize(content)
    }
  }
  function toggleThinking(message) {
    message.thinkingCollapsed = !message.thinkingCollapsed
    if (autoCollapseTimer.value) {
      clearTimeout(autoCollapseTimer.value)
      autoCollapseTimer.value = null
    }
  }
  function scrollToBottom() {
    if (messagesContainer.value) {
      messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
    }
  }
  function formatSessionTime(dateStr) {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    const now = new Date()
    const diff = now - d
    if (diff < 60000) return '刚刚'
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
    if (diff < 604800000) return `${Math.floor(diff / 86400000)} 天前`
    return `${d.getMonth() + 1}/${d.getDate()}`
  }

  async function loadSessions() {
    const userId = userStore.getUserInfo?.uuid || userStore.getUserInfo?.id || userStore.getUserInfo?.user_id
    if (!userId) {
      console.warn('loadSessions: userId 为空，跳过')
      return
    }
    isSessionsLoading.value = true
    try {
      const result = await sessionApi.getUserSessions(userId)
      if (result.code === 200 && result.data?.sessions) {
        sessions.value = result.data.sessions.map((s) => ({
          session_id: s.id,
          title: s.title,
          created_at: s.created_at,
          updated_at: s.updated_at,
        }))
        sessions.value.sort((a, b) => {
          const dateA = new Date(a.updated_at || a.created_at)
          const dateB = new Date(b.updated_at || b.created_at)
          return dateB - dateA
        })
        // 同步到 sessionStore，让 Sessions.vue 也能显示
        sessionStore.sessions = sessions.value
      }
    } catch (error) {
      console.error('加载会话列表失败:', error)
    } finally {
      isSessionsLoading.value = false
    }
  }

  async function selectSession(session) {
    if (currentSessionId.value === session.session_id) return
    // 中止在途流：防止旧回答写入新会话、以及 onDone 把用户拉回旧会话
    if (typeof abortStream === 'function') {
      abortStream()
      abortStream = null
      isLoading.value = false
    }
    try {
      const result = await sessionApi.getSession(session.session_id)
      if (result.code === 200 && result.data) {
        loadSessionHistory(result.data)
        router.push(`/chat/${session.session_id}`)
      }
    } catch (error) {
      console.error('加载会话失败:', error)
      showToast('加载会话失败')
    }
  }

  async function deleteSession(sid) {
    if (typeof abortStream === 'function') {
      abortStream()
      abortStream = null
      isLoading.value = false
    }
    try {
      await sessionApi.deleteSession(sid)
      sessions.value = sessions.value.filter((s) => s.session_id !== sid)
      if (currentSessionId.value === sid) {
        messages.value = [{ ...WELCOME_MSG }]
        sessionId.value = ''
        router.push('/chat')
      }
      showToast('会话已删除')
    } catch (error) {
      console.error('删除会话失败:', error)
      showToast('删除会话失败')
    }
  }

  function createNewSession() {
    if (typeof abortStream === 'function') {
      abortStream()
      abortStream = null
      isLoading.value = false
    }
    messages.value = [{ ...WELCOME_MSG }]
    sessionId.value = ''
    router.push('/chat')
  }

  function loadSessionHistory(session) {
    sessionId.value = session.session_id || sessionId.value
    if (session.history && session.history.length > 0) {
      messages.value = []
      session.history.forEach(([userMsg, aiMsg]) => {
        messages.value.push({ role: 'user', content: userMsg })
        messages.value.push({
          role: 'assistant',
          content: aiMsg,
          thinking: [],
          thinkingCollapsed: true,
          thinkingAutoCollapsed: true,
        })
      })
    }
  }

  function sendQuickQuestion(question) {
    userInput.value = question
    sendMessage()
  }

  function handleKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  async function sendMessage() {
    if (!userInput.value.trim() || isLoading.value) return
    if (!userStore.getLoginStatus) {
      showToast('请先登录')
      return
    }
    if (!modelStore.isConfigured && !modelStore.apiKey) {
      showToast('请先在设置中配置 AI 模型')
      return
    }

    const userMessage = userInput.value.trim()
    messages.value.push({ role: 'user', content: userMessage })
    userInput.value = ''
    messages.value.push({
      role: 'assistant',
      content: '',
      thinking: [],
      thinkingCollapsed: false,
      thinkingAutoCollapsed: false,
    })
    await nextTick()
    scrollToBottom()
    isLoading.value = true
    let aiResponse = ''
    let completedCitations = []
    const relatedNotesPromise = fetchRelatedNotes(userMessage)

    try {
      abortStream = chatApi.queryStream(
        {
          query: userMessage,
          sessionId: sessionId.value || undefined,
          llmConfig: modelStore.isConfigured ? modelStore.config : undefined,
        },
        {
          onThinking(json) {
            const idx = messages.value.length - 1
            if (messages.value[idx].role === 'assistant') {
              const newStep = {
                stage: json.stage || '',
                content: json.content || '',
                details: json.details || null,
              }
              messages.value[idx] = {
                ...messages.value[idx],
                thinking: [...messages.value[idx].thinking, newStep],
              }
              nextTick(() => scrollToBottom())
            }
          },
          async onCompleted(json) {
            // Agent 完成事件：等待相关笔记请求后再入栈，避免快照丢失异步结果
            completedCitations = Array.isArray(json.citations) ? json.citations : []
            await relatedNotesPromise
            const lastMsg = messages.value[messages.value.length - 1]
            if (lastMsg?.role === 'assistant') {
              pushQaSnapshot({
                question: userMessage,
                thinking: lastMsg.thinking || [],
                citations: completedCitations,
                relatedNotes: relatedNotes.value,
              })
            }
          },
          onResponse(json) {
            const lastMsg = messages.value[messages.value.length - 1]
            if (!lastMsg.thinkingAutoCollapsed && lastMsg.thinking.length > 0) {
              lastMsg.thinkingAutoCollapsed = true
              if (autoCollapseTimer.value) clearTimeout(autoCollapseTimer.value)
              autoCollapseTimer.value = setTimeout(() => {
                lastMsg.thinkingCollapsed = true
                autoCollapseTimer.value = null
              }, 1500)
            }
            const content = json.content || ''
            if (content) {
              aiResponse += content
              lastMsg.content = aiResponse
              nextTick(() => scrollToBottom())
            }
            if (json.session_id && typeof json.session_id === 'string' && json.session_id.trim()) {
              sessionId.value = json.session_id
            }
          },
          onDone(json) {
            const sid = json.session_id
            if (sid && typeof sid === 'string' && sid.trim()) {
              sessionId.value = sid
              if (!route.params.sessionId) router.push(`/chat/${sid}`)
              loadSessions()
            }
          },
          onError(err) {
            console.error('SSE error:', err)
            messages.value[messages.value.length - 1].content = `发生错误: ${
              err.message || '请检查网络连接和API设置'
            }`
          },
          async onFinally() {
            isLoading.value = false
            await nextTick()
            scrollToBottom()
          },
        }
      )
    } catch (error) {
      console.error('Error:', error)
      messages.value[messages.value.length - 1].content = `发生错误: ${
        error.message || '请检查网络连接'
      }`
      isLoading.value = false
      await nextTick()
      scrollToBottom()
    }
  }

  async function fetchRelatedNotes(query) {
    if (!query || query.trim().length < 2) return
    if (relatedCache.value.has(query)) {
      relatedNotes.value = relatedCache.value.get(query)
      return
    }
    relatedLoading.value = true
    try {
      const result = await noteApi.getRelatedNotes(query, 5)
      if (result.code === 200 && result.data) {
        const items = Array.isArray(result.data)
          ? result.data
          : result.data.notes || result.data.items || []
        relatedNotes.value = items
        relatedCache.value.set(query, items)
        if (relatedCache.value.size > 20) {
          const firstKey = relatedCache.value.keys().next().value
          relatedCache.value.delete(firstKey)
        }
      } else {
        relatedNotes.value = []
      }
    } catch (error) {
      console.error('获取相关笔记失败:', error)
      relatedNotes.value = []
    } finally {
      relatedLoading.value = false
    }
  }

  function truncatePreview(text, maxLen = 100) {
    if (!text) return ''
    return text.length > maxLen ? text.slice(0, maxLen) + '...' : text
  }

  function goToNote(noteId) {
    if (noteId) router.push(`/notes/${noteId}`)
  }

  watch(
    messages,
    () => {
      nextTick(() => scrollToBottom())
    },
    { deep: true }
  )

  watch(
    () => route.params.sessionId,
    async (newSessionId) => {
      if (newSessionId && newSessionId !== sessionId.value) {
        try {
          const result = await sessionApi.getSession(newSessionId)
          if (result.code === 200 && result.data) loadSessionHistory(result.data)
        } catch (error) {
          console.error('加载会话历史失败:', error)
        }
      }
    },
    { immediate: true }
  )

  onMounted(async () => {
    if (userStore.getLoginStatus) await loadSessions()
    const routeSessionId = route.params.sessionId
    if (routeSessionId) {
      try {
        const result = await sessionApi.getSession(routeSessionId)
        if (result.code === 200 && result.data) loadSessionHistory(result.data)
      } catch (error) {
        console.error('加载会话历史失败:', error)
      }
    }
    // 知识库"向 AI 提问"跳转：预填查询
    const docName = route.query.doc
    if (docName) {
      userInput.value = `请基于文档《${docName}》回答：`
    }
    scrollToBottom()
  })

  onUnmounted(() => {
    if (abortStream) abortStream()
    if (autoCollapseTimer.value) clearTimeout(autoCollapseTimer.value)
  })

  return {
    router,
    route,
    userStore,
    sessionStore,
    modelStore,
    messages,
    userInput,
    messagesContainer,
    isLoading,
    isSessionsLoading,
    sessionId,
    sessionPanelCollapsed,
    referencePanelCollapsed,
    sessionSearchQuery,
    activeReferenceTab,
    relatedNotes,
    relatedLoading,
    relatedQuery,
    sessions,
    showWelcome,
    currentSessionId,
    filteredSessions,
    currentThinkingSteps,
    referenceDocuments,
    qaHistory,
    expandedQaId,
    QA_HISTORY_LIMIT,
    pushQaSnapshot,
    toggleQaExpand,
    clearQaHistory,
    quickQuestions,
    referenceTabs,
    getStageLabel,
    getStageColor,
    truncateText,
    formatMessage,
    toggleThinking,
    scrollToBottom,
    formatSessionTime,
    loadSessions,
    selectSession,
    deleteSession,
    createNewSession,
    sendQuickQuestion,
    handleKeydown,
    sendMessage,
    fetchRelatedNotes,
    truncatePreview,
    goToNote,
  }
}
