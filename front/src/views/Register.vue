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

        <h1 class="brand-title">开启你的知识库</h1>
        <p class="brand-sub">把碎片信息沉淀为个人知识资产</p>
        <p class="brand-desc">注册即可获得智能笔记、知识库问答与间隔回顾能力，让每一次记录都可检索、可追溯。</p>

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

        <div class="brand-pills">
          <span class="brand-pill">语义检索</span>
          <span class="brand-pill">流式输出</span>
          <span class="brand-pill">来源引用</span>
        </div>
      </div>

      <!-- ===== 右侧：表单交互区 ===== -->
      <div class="form-pane">
        <div class="form-header">
          <h2>创建账号</h2>
          <p>开始整理你的笔记和知识库</p>
        </div>

        <form class="auth-form" @submit.prevent="handleRegister" novalidate>
          <div class="field" :class="{ 'field-error-box': errors.username }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
            </span>
            <input v-model="form.username" type="text" placeholder="请输入用户名" autocomplete="username" @input="errors.username = ''" />
          </div>
          <p v-if="errors.username" class="field-error">{{ errors.username }}</p>

          <div class="field" :class="{ 'field-error-box': errors.email }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" /><polyline points="22,6 12,13 2,6" /></svg>
            </span>
            <input v-model="form.email" type="email" placeholder="请输入邮箱地址" autocomplete="email" @input="errors.email = ''" />
          </div>
          <p v-if="errors.email" class="field-error">{{ errors.email }}</p>

          <div class="field">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" /></svg>
            </span>
            <input v-model="form.telephone" type="tel" placeholder="请输入手机号码（选填）" maxlength="11" autocomplete="tel" />
          </div>

          <div class="field" :class="{ 'field-error-box': errors.password }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
            </span>
            <input v-model="form.password" type="password" placeholder="请输入密码（6-20位）" autocomplete="new-password" @input="errors.password = ''" />
          </div>
          <p v-if="errors.password" class="field-error">{{ errors.password }}</p>

          <div class="field" :class="{ 'field-error-box': errors.confirm_password }">
            <span class="field-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
            </span>
            <input v-model="form.confirm_password" type="password" placeholder="请确认密码" autocomplete="new-password" @input="errors.confirm_password = ''" />
          </div>
          <p v-if="errors.confirm_password" class="field-error">{{ errors.confirm_password }}</p>

          <button class="submit-btn" type="submit" :disabled="loading">
            <span v-if="!loading">注册</span>
            <span v-else class="btn-loading"><span class="spinner-tiny"></span>注册中...</span>
          </button>
        </form>

        <div class="form-footer">
          已有账号？<span class="link" @click="goToLogin">去登录</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { showToast } from 'vant';
import { useUserStore } from '../store/user';

const router = useRouter();
const userStore = useUserStore();

const loading = ref(false);
const errors = reactive({
  username: '',
  email: '',
  password: '',
  confirm_password: '',
});

const form = reactive({
  username: '',
  email: '',
  telephone: '',
  password: '',
  confirm_password: '',
});

const EMAIL_REGEX = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

const validateForm = () => {
  errors.username = form.username.trim() ? '' : '请输入用户名';
  errors.email = !form.email.trim()
    ? '请输入邮箱地址'
    : (!EMAIL_REGEX.test(form.email.trim()) ? '请输入正确的邮箱地址' : '');
  errors.password = !form.password
    ? '请输入密码'
    : (form.password.length < 6 || form.password.length > 20 ? '密码长度应为6-20位' : '');
  errors.confirm_password = !form.confirm_password
    ? '请确认密码'
    : (form.password !== form.confirm_password ? '两次输入的密码不一致' : '');

  return !(errors.username || errors.email || errors.password || errors.confirm_password);
};

const handleRegister = async () => {
  if (!validateForm()) return;

  loading.value = true;

  try {
    const result = await userStore.register(form);

    if (result.success) {
      showToast({
        message: result.message,
        position: 'top',
      });

      // 注册成功后跳转到笔记页
      setTimeout(() => {
        router.push('/notes');
      }, 1500);
    } else {
      showToast({
        message: result.message,
        position: 'top',
        type: 'fail',
      });
    }
  } catch (error) {
    console.error('注册失败:', error);
    showToast({
      message: '注册失败，请稍后重试',
      position: 'top',
      type: 'fail',
    });
  } finally {
    loading.value = false;
  }
};

const goToLogin = () => {
  router.push('/login');
};
</script>

<style scoped>
/* ===== 页面：网格科技背景 + 居中悬浮卡片（与登录页同构） ===== */
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
  min-height: 580px;
  background: var(--color-card);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(15, 30, 61, 0.14), 0 4px 16px rgba(15, 30, 61, 0.06);
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
  padding: 40px 40px 28px;
  background: var(--color-card);
  overflow-y: auto;
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
  margin: 0 0 24px;
}

.auth-form {
  display: flex;
  flex-direction: column;
}

.field {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 46px;
  padding: 0 14px;
  margin-bottom: 6px;
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
  box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.10);
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
  margin: 0 2px 8px;
}

.submit-btn {
  width: 100%;
  height: 46px;
  margin-top: 10px;
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

.form-footer {
  text-align: center;
  margin-top: 20px;
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
    max-width: 460px;
    min-height: auto;
  }

  .form-pane {
    padding: 36px 26px 26px;
  }
}

@media (max-width: 400px) {
  .auth-page {
    padding: 16px 12px;
  }

  .form-pane {
    padding: 30px 20px 22px;
  }
}

:global([data-theme='dark']) .auth-card {
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.45);
}
</style>
