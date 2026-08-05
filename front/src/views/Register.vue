<template>
  <div class="register-page">
    <van-nav-bar
      title="用户注册"
      left-arrow
      @click-left="onClickLeft"
      fixed
    />
    
    <div class="register-container">
      <section class="register-hero">
        <div class="logo-mark">
          <van-icon name="records-o" />
        </div>
        <div>
          <h2>创建账号</h2>
          <p>开始整理你的笔记和知识库</p>
        </div>
      </section>

      <van-form class="register-form" @submit="handleRegister">
        <van-cell-group inset>
          <van-field
            v-model="form.username"
            name="username"
            label="用户名"
            placeholder="请输入用户名"
            :rules="usernameRules"
            required
            clearable
            left-icon="user-o"
            @blur="validateUsername"
          />
          
          <van-field
            v-model="form.email"
            name="email"
            label="邮箱"
            placeholder="请输入邮箱地址"
            :rules="emailRules"
            required
            type="email"
            clearable
            left-icon="envelop-o"
            @blur="validateEmail"
          />
          
          <van-field
            v-model="form.telephone"
            name="telephone"
            label="手机"
            placeholder="请输入手机号码"
            type="tel"
            left-icon="phone"
            maxlength="11"
            clearable
          />
          
          <van-field
            v-model="form.password"
            name="password"
            label="密码"
            placeholder="请输入密码（6-20位）"
            :rules="passwordRules"
            required
            type="password"
            clearable
            left-icon="lock"
            @blur="validatePassword"
          />
          
          <van-field
            v-model="form.confirm_password"
            name="confirm_password"
            label="确认密码"
            placeholder="请确认密码"
            :rules="confirmPasswordRules"
            required
            type="password"
            clearable
            left-icon="lock"
            @blur="validateConfirmPassword"
          />
        </van-cell-group>
        
        <div class="register-btn-container">
          <van-button
            type="primary"
            block
            round
            size="large"
            :loading="loading"
            native-type="submit"
          >
            {{ loading ? '注册中...' : '注册' }}
          </van-button>
        </div>
      </van-form>
      
      <div class="login-link">
        已有账号？<span @click="goToLogin">去登录</span>
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

const form = reactive({
  username: '',
  email: '',
  telephone: '',
  password: '',
  confirm_password: ''
});

const usernameRules = [
  { required: true, message: '请输入用户名' }
];

const emailRules = [
  { required: true, message: '请输入邮箱地址' },
  { pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/, message: '请输入正确的邮箱地址' }
];

const passwordRules = [
  { required: true, message: '请输入密码' },
  { pattern: /^.{6,20}$/, message: '密码长度应为6-20位' }
];

const confirmPasswordRules = [
  { required: true, message: '请确认密码' }
];

const validateUsername = () => {
  if (!form.username) {
    showToast('请输入用户名');
    return false;
  }
  return true;
};

const validateEmail = () => {
  if (!form.email) {
    showToast('请输入邮箱地址');
    return false;
  }
  const emailRegex = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;
  if (!emailRegex.test(form.email)) {
    showToast('请输入正确的邮箱地址');
    return false;
  }
  return true;
};

const validatePassword = () => {
  if (!form.password) {
    showToast('请输入密码');
    return false;
  }
  if (form.password.length < 6 || form.password.length > 20) {
    showToast('密码长度应为6-20位');
    return false;
  }
  return true;
};

const validateConfirmPassword = () => {
  if (!form.confirm_password) {
    showToast('请确认密码');
    return false;
  }
  if (form.password !== form.confirm_password) {
    showToast('两次输入的密码不一致');
    return false;
  }
  return true;
};

const validateForm = () => {
  return validateUsername() && validateEmail() && validatePassword() && validateConfirmPassword();
};

const handleRegister = async () => {
  if (!validateForm()) {
    return;
  }

  loading.value = true;

  try {
    const result = await userStore.register(form);

    if (result.success) {
      showToast({
        message: result.message,
        position: 'top'
      });
      
      // 注册成功后跳转到笔记页
      setTimeout(() => {
        router.push('/notes');
      }, 1500);
    } else {
      showToast({
        message: result.message,
        position: 'top',
        type: 'fail'
      });
    }
  } catch (error) {
    console.error('注册失败:', error);
    showToast({
      message: '注册失败，请稍后重试',
      position: 'top',
      type: 'fail'
    });
  } finally {
    loading.value = false;
  }
};

const onClickLeft = () => {
  router.back();
};

const goToLogin = () => {
  router.push('/login');
};
</script>

<style scoped>
.register-page {
  min-height: 100vh;
  background: var(--color-bg);
}

.register-container {
  width: min(100%, 440px);
  margin: 0 auto;
  padding: 74px 16px 28px;
  box-sizing: border-box;
}

.register-hero {
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
  border-radius: var(--radius-md);
  background: var(--color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

.logo-mark :deep(.van-icon) {
  font-size: 30px;
}

.register-hero h2 {
  font-family: var(--font-heading);
  font-size: 24px;
  line-height: 1.2;
  color: var(--color-text);
  font-weight: 600;
  margin: 0;
}

.register-hero p {
  margin: 7px 0 0;
  color: var(--color-text-lighter);
  font-size: 14px;
}

.register-form {
  background: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 6px 0 18px;
  overflow: hidden;
}

.register-form :deep(.van-cell-group) {
  background: transparent;
  border-radius: 0;
  margin: 0;
}

.register-form :deep(.van-cell) {
  padding: 15px 18px;
  background: transparent;
}

.register-form :deep(.van-cell::after) {
  border-color: var(--color-divider);
  left: 18px;
  right: 18px;
}

.register-form :deep(.van-field__label) {
  color: var(--color-text-light);
}

.register-form :deep(.van-field__left-icon) {
  color: var(--color-text-lighter);
}

.register-btn-container {
  margin-top: 18px;
  padding: 0 18px;
}

.login-link {
  text-align: center;
  margin-top: 20px;
  color: var(--color-text-lighter);
  font-size: 14px;
}

.login-link span {
  color: var(--color-primary);
  font-weight: 500;
  cursor: pointer;
}

@media (max-width: 360px) {
  .register-container {
    padding-left: 12px;
    padding-right: 12px;
  }

  .register-hero h2 {
    font-size: 22px;
  }

  .register-hero p {
    font-size: 13px;
  }
}
</style>
