/**
 * Note editor state helpers (extracted from NoteEditorPage).
 */
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

export function useNoteEditor() {
  const route = useRoute()
  const router = useRouter()

  const markdownEditorRef = ref(null)
  const editorBodyRef = ref(null)
  const title = ref('')
  const content = ref('')
  const tags = ref([])
  const newTag = ref('')
  const category = ref('')
  const saving = ref(false)
  const noteId = ref('')
  const autoSaved = ref(false)
  const completionContext = ref('')
  const cursorPosition = ref({ top: 0, left: 0 })
  const sidebarVisible = ref(false)
  const relatedItems = ref([])
  const loadingRelated = ref(false)
  const expandedNote = ref(null)
  const detailLoading = ref(false)
  const renderedExpandedContent = ref('')

  const isNew = computed(
    () => route.name === 'NoteNew' || route.params.id === 'new'
  )
  const categoryMap = {
    work: '工作',
    study: '学习',
    life: '生活',
    project: '项目',
  }

  function addTag() {
    const t = newTag.value.trim().replace(/,/g, '')
    if (t && !tags.value.includes(t)) tags.value.push(t)
    newTag.value = ''
  }
  function removeTag(index) {
    tags.value.splice(index, 1)
  }
  function toggleSidebar() {
    sidebarVisible.value = !sidebarVisible.value
  }
  function goBack() {
    router.push('/notes')
  }

  return {
    route,
    router,
    markdownEditorRef,
    editorBodyRef,
    title,
    content,
    tags,
    newTag,
    category,
    saving,
    noteId,
    autoSaved,
    completionContext,
    cursorPosition,
    sidebarVisible,
    relatedItems,
    loadingRelated,
    expandedNote,
    detailLoading,
    renderedExpandedContent,
    isNew,
    categoryMap,
    addTag,
    removeTag,
    toggleSidebar,
    goBack,
  }
}
