<template>
  <div class="auth-page">
    <div class="auth-card">
      <!-- ===== 左侧：品牌展示区（移动端隐藏） ===== -->
      <div class="brand-pane">
        <div class="brand-logo">
          <span class="brand-logo-icon">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          </span>
          <span class="brand-name">Notebook</span>
        </div>

        <h1 class="brand-title">智能知识工作台</h1>
        <p class="brand-sub">继续管理你的笔记和知识库</p>
        <p class="brand-desc">Agentic RAG 架构，融合混合检索与大语言模型，让每一次提问都有据可查、有源可溯。</p>

        <!-- 四大能力卡片（2x2 玻璃拟态） -->
        <div class="brand-features">
          <div class="feature-card">
            <span class="feature-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            </span>
            <div class="feature-text">
              <div class="feature-name">混合检索</div>
              <div class="feature-desc">向量 + BM25 双路召回</div>
            </div>
          </div>
          <div class="feature-card">
            <span class="feature-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 2l1.9 5.8L20 9.7l-5 3.9 1.7 6.1L12 16.3l-4.7 3.4L9 13.6l-5-3.9 6.1-1.9z" /></svg>
            </span>
            <div class="feature-text">
              <div class="feature-name">智能重排</div>
              <div class="feature-desc">Cross-Encoder 精排</div>
            </div>
          </div>
          <div class="feature-card">
            <span class="feature-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
            </span>
            <div class="feature-text">
              <div class="feature-name">来源可溯</div>
              <div class="feature-desc">答案带引用标注</div>
            </div>
          </div>
          <div class="feature-card">
            <span class="feature-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></svg>
            </span>
            <div class="feature-text">
              <div class="feature-name">AI 问答</div>
              <div class="feature-desc">流式输出思考过程</div>
            </div>
          </div>
        </div>

        <!-- 底部特性胶囊 -->
        <div class="brand-pills">
          <span class="brand-pill">语义检索</span>
          <span class="brand-pill">流式输出</span>
          <span class="brand-pill">来源引用</span>
        </div>
      </div>

      <!-- ===== 右侧：表单交互区 ===== -->
      <div class="form-pane">
        <div class="form-header">
          <h2>欢迎回来</h2>
          <p>登录后继续你的知识工作流</p>
        </div>

        <form class="auth-form" novalidate @submit.prevent="onSubmit">
          <div class="field" :class="{ 'field-error-box': errors.username }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
            </span>
            <input
              v-model="username"
              type="text"
              placeholder="请输入用户名"
              autocomplete="username"
              @input="errors.username = ''"
            />
          </div>
          <p v-if="errors.username" class="field-error">{{ errors.username }}</p>

          <div class="field" :class="{ 'field-error-box': errors.password }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
            </span>
            <input
              v-model="password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
              @input="errors.password = ''"
            />
          </div>
          <p v-if="errors.password" class="field-error">{{ errors.password }}</p>

          <div class="form-aux">
            <label class="remember-me">
              <input v-model="remember" type="checkbox" />
              <span>记住我</span>
            </label>
            <span class="forgot-link" @click="onForgot">忘记密码?</span>
          </div>

          <button class="submit-btn" type="submit" :disabled="loading">
            <span v-if="!loading">立即登录</span>
            <span v-else class="btn-loading"><span class="spinner-tiny"></span>登录中...</span>
          </button>
        </form>

        <div class="form-footer">
          还没有账号？<span class="link" @click="goToRegister">去注册</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { showToast } from 'vant';
import { useUserStore } from '../store/user';

const router = useRouter();
const userStore = useUserStore();

const username = ref('');
const password = ref('');
const remember = ref(false);
const loading = ref(false);
const errors = ref({ username: '', password: '' });

const REMEMBER_KEY = 'notebook_remember_username';

onMounted(() => {
  const saved = localStorage.getItem(REMEMBER_KEY);
  if (saved) {
    username.value = saved;
    remember.value = true;
  }
});

const onSubmit = async () => {
  errors.value.username = username.value.trim() ? '' : '请填写用户名';
  errors.value.password = password.value ? '' : '请填写密码';
  if (errors.value.username || errors.value.password) return;

  if (loading.value) return;
  loading.value = true;

  if (remember.value) {
    localStorage.setItem(REMEMBER_KEY, username.value.trim());
  } else {
    localStorage.removeItem(REMEMBER_KEY);
  }

  showToast({ type: 'loading', message: '登录中...', forbidClick: true, duration: 0 });

  try {
    const result = await userStore.login({
      username: username.value.trim(),
      password: password.value,
    });

    if (result.success) {
      showToast({ type: 'success', message: result.message });
      // 登录成功跳转：优先回到被拦截前的页面，否则去笔记页
      const redirect = router.currentRoute.value.query.redirect || '/notes';
      router.push(redirect);
    } else {
      showToast({ type: 'fail', message: result.message });
    }
  } catch (error) {
    showToast({ type: 'fail', message: '登录失败，请稍后再试' });
  } finally {
    loading.value = false;
  }
};

const onForgot = () => {
  showToast('请联系管理员重置密码');
};

const goToRegister = () => {
  router.push('/register');
};
</script>

<style scoped>
/* ===== 页面：浅色网格科技背景 + 居中悬浮卡片 ===== */
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  box-sizing: border-box;
  background-color: var(--color-bg);
  background-image:
    linear-gradient(var(--grid-line) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
  background-size: 28px 28px;
}

.auth-card {
  display: flex;
  width: min(100%, 860px);
  min-height: 560px;
  background: var(--color-card);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(20, 20, 19, 0.14), 0 4px 16px rgba(20, 20, 19, 0.06);
}

/* ===== 左侧品牌区：浅蓝流体渐变 ===== */
.brand-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 44px 40px 32px;
  color: #1e3a8a;
  background: linear-gradient(135deg, #e8f1fe 0%, #d3e5fc 45%, #c7e6fb 100%);
  position: relative;
  overflow: hidden;
}

/* 渐变上的柔和光斑 */
.brand-pane::before {
  content: '';
  position: absolute;
  top: -80px;
  right: -80px;
  width: 260px;
  height: 260px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.55), transparent 70%);
}

.brand-pane::after {
  content: '';
  position: absolute;
  bottom: -60px;
  left: -60px;
  width: 200px;
  height: 200px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.35), transparent 70%);
}

.brand-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 36px;
  position: relative;
  z-index: 1;
}

.brand-logo-icon {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.65);
  backdrop-filter: blur(4px);
  border: 1px solid rgba(30, 58, 138, 0.14);
  color: #1e40af;
}

.brand-name {
  font-family: var(--font-heading);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.5px;
}

.brand-title {
  font-family: var(--font-heading);
  font-size: 28px;
  font-weight: 700;
  margin: 0 0 10px;
  line-height: 1.25;
  color: #1e3a8a;
  position: relative;
  z-index: 1;
}

.brand-sub {
  font-size: 15px;
  font-weight: 500;
  margin: 0 0 6px;
  color: #35507f;
  position: relative;
  z-index: 1;
}

.brand-desc {
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 28px;
  color: #46609b;
  position: relative;
  z-index: 1;
}

/* 2x2 玻璃拟态能力卡片 */
.brand-features {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 26px;
  position: relative;
  z-index: 1;
}

.feature-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 13px 14px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(30, 58, 138, 0.10);
  backdrop-filter: blur(6px);
  transition: background 0.2s ease, transform 0.2s ease;
}

.feature-card:hover {
  background: rgba(255, 255, 255, 0.8);
  transform: translateY(-1px);
}

.feature-icon {
  flex-shrink: 0;
  margin-top: 1px;
  color: #1e40af;
}

.feature-name {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 2px;
  color: #1e3a8a;
}

.feature-desc {
  font-size: 11px;
  color: #46609b;
  line-height: 1.4;
}

/* 底部特性胶囊 */
.brand-pills {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: auto;
  position: relative;
  z-index: 1;
}

.brand-pill {
  font-size: 11px;
  padding: 3px 12px;
  border-radius: 999px;
  color: #1e40af;
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(30, 58, 138, 0.14);
}

/* ===== 右侧表单区 ===== */
.form-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 44px 40px 32px;
  background: var(--color-card);
}

.form-header h2 {
  font-family: var(--font-heading);
  font-size: 24px;
  font-weight: 700;
  color: var(--color-text);
  margin: 0 0 6px;
}

.form-header p {
  font-size: 13px;
  color: var(--color-text-lighter);
  margin: 0 0 28px;
}

/* 输入框 */
.field {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 46px;
  padding: 0 14px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.field:focus-within {
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px var(--color-primary-light);
}

.field-error-box {
  border-color: var(--color-error);
}

.field-error-box:focus-within {
  box-shadow: 0 0 0 3px rgba(185, 28, 28, 0.10);
}

.field-icon {
  flex-shrink: 0;
  color: var(--color-text-lightest);
  display: flex;
  align-items: center;
}

.field input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: 14px;
  color: var(--color-text);
  font-family: var(--font-body);
}

.field input::placeholder {
  color: var(--color-text-lightest);
}

.field-error {
  font-size: 12px;
  color: var(--color-error);
  margin: 6px 2px 0;
}

/* 记住我 / 忘记密码 */
.form-aux {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 16px 2px 20px;
}

.remember-me {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  color: var(--color-text-lighter);
  cursor: pointer;
  user-select: none;
}

.remember-me input {
  width: 15px;
  height: 15px;
  accent-color: var(--color-primary);
  cursor: pointer;
}

.forgot-link {
  font-size: 13px;
  color: var(--color-primary);
  cursor: pointer;
}

.forgot-link:hover {
  text-decoration: underline;
}

/* 提交按钮 */
.submit-btn {
  width: 100%;
  height: 46px;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #1e40af, #2563eb);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  font-family: var(--font-body);
  cursor: pointer;
  transition: transform 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease;
  box-shadow: 0 6px 16px rgba(30, 64, 175, 0.28);
  display: flex;
  align-items: center;
  justify-content: center;
}

.submit-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 8px 20px rgba(30, 64, 175, 0.36);
}

.submit-btn:active:not(:disabled) {
  transform: translateY(0);
}

.submit-btn:disabled {
  opacity: 0.7;
  cursor: not-allowed;
}

.btn-loading {
  display: flex;
  align-items: center;
  gap: 8px;
}

.spinner-tiny {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  border-radius: 50%;
  animation: authSpin 0.8s linear infinite;
}

@keyframes authSpin {
  to { transform: rotate(360deg); }
}

/* 底部链接 */
.form-footer {
  text-align: center;
  margin-top: 22px;
  font-size: 13px;
  color: var(--color-text-lighter);
}

.form-footer .link {
  color: var(--color-primary);
  font-weight: 500;
  cursor: pointer;
}

.form-footer .link:hover {
  text-decoration: underline;
}

/* ===== 响应式 ===== */
@media (max-width: 880px) {
  .brand-pane {
    display: none;
  }

  .auth-card {
    max-width: 440px;
    min-height: auto;
  }

  .form-pane {
    padding: 36px 28px 28px;
  }
}

@media (max-width: 400px) {
  .auth-page {
    padding: 16px 12px;
  }

  .form-pane {
    padding: 30px 22px 24px;
  }
}

/* 深色主题适配：卡片与输入区跟随语义色 */
:global([data-theme='dark']) .auth-card {
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.45);
}
</style>
