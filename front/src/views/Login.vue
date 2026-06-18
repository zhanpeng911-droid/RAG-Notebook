<template>
  <div class="login-page">
    <van-nav-bar
      title="用户登录"
      left-arrow
      @click-left="onClickLeft"
      fixed
    />

    <div class="login-container">
      <section class="login-hero">
        <div class="logo-mark">
          <van-icon name="records-o" />
        </div>
        <div>
          <h2>欢迎回来</h2>
          <p>继续管理你的笔记和知识库</p>
        </div>
      </section>

      <van-form @submit="onSubmit" class="login-form">
        <van-cell-group inset>
          <van-field
            v-model="username"
            name="username"
            label="用户名"
            placeholder="请输入用户名"
            left-icon="user-o"
            clearable
            :rules="[{ required: true, message: '请填写用户名' }]"
          />
          <van-field
            v-model="password"
            type="password"
            name="password"
            label="密码"
            placeholder="请输入密码"
            left-icon="lock"
            clearable
            :rules="[{ required: true, message: '请填写密码' }]"
          />
        </van-cell-group>

        <div class="submit-btn">
          <van-button round block type="primary" native-type="submit" size="large" :loading="loading">
            登录
          </van-button>
        </div>

        <div class="register-link">
          还没有账号？<span @click="goToRegister">去注册</span>
        </div>
      </van-form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';
import { showToast } from 'vant';
import { useUserStore } from '../store/user';

const router = useRouter();
const userStore = useUserStore();

const username = ref('');
const password = ref('');
const loading = ref(false);

const onSubmit = async (values) => {
  if (loading.value) return;
  loading.value = true;

  showToast({
    type: 'loading',
    message: '登录中...',
    forbidClick: true,
    duration: 0
  });

  try {
    const result = await userStore.login({
      username: values.username,
      password: values.password
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

const onClickLeft = () => {
  router.back();
};

const goToRegister = () => {
  router.push('/register');
};
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  background:
    linear-gradient(180deg, var(--color-surface) 0%, var(--color-bg) 38%, var(--color-bg) 100%);
}

.login-container {
  width: min(100%, 440px);
  margin: 0 auto;
  padding: 74px 16px 28px;
  box-sizing: border-box;
}

.login-hero {
  display: flex;
  align-items: center;
  gap: 14px;
  margin: 10px 0 22px;
  padding: 0 2px;
}

.logo-mark {
  width: 58px;
  height: 58px;
  flex: 0 0 58px;
  border-radius: 8px;
  background: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 10px 24px rgba(52, 120, 223, 0.26);
}

.logo-mark :deep(.van-icon) {
  font-size: 30px;
}

.login-hero h2 {
  font-family: var(--font-heading);
  font-size: 24px;
  line-height: 1.2;
  color: var(--color-text);
  font-weight: 600;
  margin: 0;
}

.login-hero p {
  margin: 7px 0 0;
  color: var(--color-text-lighter);
  font-size: 14px;
}

.login-form {
  background: var(--color-card);
  border: 1px solid var(--color-border-light);
  border-radius: 8px;
  box-shadow: 0 14px 34px var(--color-shadow);
  padding: 6px 0 18px;
  overflow: hidden;
}

.login-form :deep(.van-cell-group) {
  background: transparent;
  border-radius: 0;
  margin: 0;
}

.login-form :deep(.van-cell) {
  padding: 15px 18px;
  background: transparent;
}

.login-form :deep(.van-cell::after) {
  border-color: var(--color-divider);
  left: 18px;
  right: 18px;
}

.login-form :deep(.van-field__label) {
  color: var(--color-text-light);
}

.login-form :deep(.van-field__left-icon) {
  color: var(--color-text-lighter);
}

.submit-btn {
  margin: 18px 18px 0;
}

.submit-btn :deep(.van-button) {
  box-shadow: 0 8px 18px rgba(52, 120, 223, 0.22);
}

.register-link {
  text-align: center;
  margin-top: 20px;
  color: var(--color-text-lighter);
  font-size: 14px;
}

.register-link span {
  color: var(--color-primary);
  font-weight: 500;
  cursor: pointer;
}

@media (max-width: 360px) {
  .login-container {
    padding-left: 12px;
    padding-right: 12px;
  }

  .login-hero h2 {
    font-size: 22px;
  }

  .login-hero p {
    font-size: 13px;
  }
}
</style>
