/**
 * Knowledge base upload / list / detail logic (extracted from KnowledgeBasePage).
 */
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import { useUserStore } from '../store/user'
import { useAuthImage } from './useAuthImage'
import http from '../services/http'
import { apiConfig } from '../config/api'

export function useKnowledgeBase() {
  const router = useRouter()
  const userStore = useUserStore()
  const { getAllImages, resolveImageUrls } = useAuthImage()

  function classifyError(error) {
    const status = error?.response?.status
    if (status === 401) return '登录已过期，请重新登录'
    if (status === 413) return '文件过大'
    if (status >= 500) return '服务暂时不可用'
    return error?.response?.data?.message || error?.message || '请求失败'
  }

  const fileInput = ref(null)
  const selectedFiles = ref([])
  const isDragOver = ref(false)
  const uploading = ref(false)
  const uploadProgressList = ref([])
  const uploadComplete = ref(false)
  const successCount = ref(0)
  const failedCount = ref(0)

  const documents = ref([])
  const loadingDocuments = ref(false)
  const documentError = ref('')
  const searchQuery = ref('')

  const spaces = ref([])
  const selectedSpaceId = ref('')

  const showDetail = ref(false)
  const currentDocument = ref(null)
  const detailTab = ref('content')
  const loadingDetail = ref(false)
  const detailPageImages = ref([])
  const loadingChunks = ref(false)
  const chunks = ref([])

  const filteredDocuments = computed(() => {
    if (!searchQuery.value.trim()) return documents.value
    const q = searchQuery.value.toLowerCase()
    return documents.value.filter(
      (d) =>
        (d.filename || d.original_filename || '').toLowerCase().includes(q) ||
        (d.preview || '').toLowerCase().includes(q)
    )
  })

  const totalChunks = computed(() =>
    documents.value.reduce((sum, d) => sum + (d.chunk_count || 0), 0)
  )
  const documentsWithImages = computed(() =>
    documents.value.filter((d) => (d.image_count || 0) > 0).length
  )

  function openFilePicker() {
    fileInput.value?.click()
  }
  function handleFileSelect(event) {
    const files = Array.from(event.target.files || [])
    selectedFiles.value = [...selectedFiles.value, ...files]
    event.target.value = ''
  }
  function handleDrop(event) {
    isDragOver.value = false
    const files = Array.from(event.dataTransfer?.files || [])
    selectedFiles.value = [...selectedFiles.value, ...files]
  }
  function removeFile(index) {
    selectedFiles.value.splice(index, 1)
  }
  function formatFileSize(bytes) {
    if (!bytes) return '0 B'
    const units = ['B', 'KB', 'MB', 'GB']
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`
  }
  function getFileType(filename) {
    const ext = (filename || '').split('.').pop()?.toLowerCase()
    return ext || 'file'
  }

  return {
    router,
    userStore,
    getAllImages,
    resolveImageUrls,
    classifyError,
    fileInput,
    selectedFiles,
    isDragOver,
    uploading,
    uploadProgressList,
    uploadComplete,
    successCount,
    failedCount,
    documents,
    loadingDocuments,
    documentError,
    searchQuery,
    spaces,
    selectedSpaceId,
    showDetail,
    currentDocument,
    detailTab,
    loadingDetail,
    detailPageImages,
    loadingChunks,
    chunks,
    filteredDocuments,
    totalChunks,
    documentsWithImages,
    openFilePicker,
    handleFileSelect,
    handleDrop,
    removeFile,
    formatFileSize,
    getFileType,
    showToast,
    showConfirmDialog,
    http,
    apiConfig,
  }
}
