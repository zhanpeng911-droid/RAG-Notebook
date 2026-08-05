<template>
  <div class="daily-review-page">
    <div class="review-content">
      <!-- 加载状态 -->
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <p>加载中...</p>
      </div>

      <!-- 错误状态 -->
      <div v-else-if="error" class="error-state">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="8" x2="12" y2="12"/>
          <line x1="12" y1="16" x2="12.01" y2="16"/>
        </svg>
        <p>{{ error }}</p>
        <button class="btn-retry" @click="loadReviews">重试</button>
      </div>

      <!-- 空状态 -->
      <div v-else-if="reviews.length === 0" class="empty-state">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round" stroke-linejoin="round">
          <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>
          <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>
        </svg>
        <h3>今天没有需要回顾的笔记</h3>
        <p>太棒了！继续加油写笔记吧</p>
      </div>

      <!-- 回顾列表 -->
      <div v-else class="review-list">
        <div class="review-header-bar">
          <span class="review-count">共 {{ reviews.length }} 篇待回顾</span>
          <span class="review-progress">{{ doneCount }} / {{ reviews.length }}</span>
        </div>

        <ReviewCard
          v-for="item in reviews"
          :key="item.note_id"
          :title="item.title"
          :question="getQuestion(item.note_id)"
          :tags="item.tags"
          :category="item.category"
          :review-count="item.review_count"
          :done="doneMap[item.note_id]"
          @click="goToNote(item.note_id)"
          @done="handleDone(item.note_id)"
          @skip="handleSkip(item.note_id)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * DailyReview 每日回顾页面 —— 展示待回顾笔记列表，使用艾宾浩斯曲线算法。
 * 用户滑动浏览卡片，标记已回顾或跳过。
 */
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import { reviewApi } from '../services/reviewApi'
import ReviewCard from '../components/ReviewCard.vue'

const router = useRouter()

const loading = ref(false)
const error = ref('')
const reviews = ref([])
const questions = reactive({})
const doneMap = reactive({})
const doneCount = ref(0)

/** 获取某个笔记的回顾问题（带缓存） */
function getQuestion(noteId) {
  return questions[noteId] || '请回顾这篇笔记的主要内容'
}

/** 加载今日回顾列表 */
async function loadReviews() {
  loading.value = true
  error.value = ''
  try {
    const result = await reviewApi.getToday()
    if (result.code === 200) {
      reviews.value = result.data?.reviews || []
    } else {
      error.value = result.message || '加载失败'
    }
  } catch (e) {
    console.error('加载回顾失败:', e)
    error.value = '网络错误，请稍后重试'
  } finally {
    loading.value = false
  }
}

/** 标记已回顾 */
async function handleDone(noteId) {
  try {
    const result = await reviewApi.markDone(noteId)
    if (result.code === 200) {
      doneMap[noteId] = true
      doneCount.value++
      showToast('已标记回顾')
    } else {
      showToast(result.message || '操作失败')
    }
  } catch (e) {
    console.error('标记回顾失败:', e)
    showToast('操作失败')
  }
}

/** 跳过 */
async function handleSkip(noteId) {
  try {
    await reviewApi.markDone(noteId)
  } catch (e) {
    // 即使 API 失败也标记为已跳过，避免反复弹出
  }
  doneMap[noteId] = true
  doneCount.value++
}

onMounted(() => {
  loadReviews()
})

/** 跳转到笔记编辑页 */
function goToNote(noteId) {
  router.push('/notes/' + noteId)
}
</script>

<style scoped>
.daily-review-page {
  min-height: 100%;
  background: var(--color-bg);
}

.review-content {
  padding: 0;
}

/* 加载状态 */
.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: calc(var(--space-2xl) * 2);
  color: var(--color-text-lighter);
}

.loading-state p {
  margin-top: var(--space-md);
  font-size: 14px;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* 错误状态 */
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: calc(var(--space-2xl) * 2);
  color: var(--color-error);
}

.error-state p {
  margin: var(--space-md) 0;
  font-size: 14px;
  color: var(--color-text-lighter);
}

.btn-retry {
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.btn-retry:hover {
  background: var(--color-primary-hover);
}

/* 空状态 */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: calc(var(--space-2xl) * 2);
  text-align: center;
}

.empty-state svg {
  color: var(--color-text-lightest);
  margin-bottom: var(--space-lg);
}

.empty-state h3 {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-sm);
}

.empty-state p {
  font-size: 14px;
  color: var(--color-text-lighter);
  margin: 0;
}

/* 回顾列表 */
.review-list {
  padding: var(--space-lg);
}

.review-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-lg);
}

.review-count {
  font-size: 14px;
  color: var(--color-text-light);
}

.review-progress {
  font-size: 14px;
  color: var(--color-primary);
  font-weight: 600;
}
</style>
