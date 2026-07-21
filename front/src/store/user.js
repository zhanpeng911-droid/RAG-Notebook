import { defineStore } from 'pinia';
import http from '../services/http';
import { apiConfig } from '../config/api';

const REGISTER_FIELD_LABELS = {
  username: '用户名',
  email: '邮箱',
  telephone: '手机号',
  password: '密码',
  confirm_password: '确认密码',
  non_field_errors: ''
};

function flattenApiErrors(value, path = []) {
  if (Array.isArray(value)) {
    return value.flatMap((item) => flattenApiErrors(item, path));
  }
  if (value && typeof value === 'object') {
    return Object.entries(value).flatMap(([key, child]) => flattenApiErrors(child, [...path, key]));
  }
  if (value === null || value === undefined) {
    return [];
  }

  const field = path[path.length - 1];
  const label = REGISTER_FIELD_LABELS[field] ?? field;
  return [label ? `${label}：${String(value)}` : String(value)];
}

function formatApiError(data, fallback) {
  const detail = data?.detail ?? data?.message ?? data;
  if (typeof detail === 'string') {
    return detail;
  }

  const messages = flattenApiErrors(detail);
  return messages.length ? messages.join('；') : fallback;
}

export const useUserStore = defineStore('user', {
  state: () => ({
    userInfo: null,
    token: '',
    isLogin: false,
    userBio: '这是我的个人简介'
  }),

  getters: {
    getUserInfo: (state) => state.userInfo,
    getToken: (state) => state.token,
    getLoginStatus: (state) => state.isLogin,
    getUserBio: (state) => state.userInfo?.bio || state.userBio
  },

  actions: {
    // 登录
    async login(userData) {
      try {
        const response = await http.post(apiConfig.endpoints.login, {
          username: userData.username,
          password: userData.password
        });

        if (response.status === 200) {
          const token = response.data.token
            || response.data.access
            || response.data.data?.token
            || response.data.data?.access;

          const userInfo = response.data.user || response.data.data?.user || null;

          if (!token) {
            return { success: false, message: response.data.message || '登录失败：未返回 token' };
          }

          localStorage.setItem('jwt_token', token);
          this.token = token;
          this.userInfo = userInfo;
          this.isLogin = true;

          return { success: true, message: response.data.message || '登录成功' };
        } else {
          return { success: false, message: response.data.message || response.data.detail || '登录失败' };
        }
      } catch (error) {
        console.error('登录请求失败:', error);
        const msg = error.response?.data?.message
          || error.response?.data?.detail
          || error.response?.data?.non_field_errors?.[0]
          || '登录请求失败，请稍后再试';
        return { success: false, message: msg };
      }
    },

    // 注销
    async logout() {
      try {
        await http.post(apiConfig.endpoints.logout, {});
      } catch (error) {
        console.error('注销请求失败:', error);
      } finally {
        this.clearAuth();
      }
    },

    // 清除认证状态
    clearAuth() {
      this.userInfo = null;
      this.token = '';
      this.isLogin = false;
      localStorage.removeItem('jwt_token');
    },

    // 获取用户信息
    async getUserInfoDetail() {
      try {
        const token = localStorage.getItem('jwt_token') || this.token;
        if (!token) {
          return { success: false, message: '未登录' };
        }

        const response = await http.get(apiConfig.endpoints.profile);

        if (response.status === 200) {
          this.userInfo = response.data.data;
          return { success: true, message: response.data.message, data: response.data.data };
        } else {
          return { success: false, message: response.data.detail || '获取用户信息失败' };
        }
      } catch (error) {
        console.error('获取用户信息请求失败:', error);
        return { success: false, message: error.response?.data?.detail || '获取用户信息失败，请稍后再试' };
      }
    },

    // 更新用户信息
    async updateUserInfo(userData) {
      try {
        const token = localStorage.getItem('jwt_token') || this.token;
        if (!token) {
          return { success: false, message: '未登录' };
        }

        const response = await http.put('/user/update/', userData);

        if (response.status === 200) {
          this.userInfo = response.data.user;
          if (response.data.token) {
            this.token = response.data.token;
            localStorage.setItem('jwt_token', response.data.token);
          }
          return { success: true, message: response.data.message };
        } else {
          return { success: false, message: response.data.detail || '更新用户信息失败' };
        }
      } catch (error) {
        console.error('更新用户信息请求失败:', error);
        return {
          success: false,
          message: error.response?.data?.message || error.response?.data?.detail || '更新用户信息失败，请稍后再试'
        };
      }
    },

    // 更新密码
    async updatePassword(oldPassword, newPassword) {
      try {
        const token = localStorage.getItem('jwt_token') || this.token;
        if (!token) {
          return { success: false, message: '未登录' };
        }

        const response = await http.post('/user/reset-password/', {
          old_password: oldPassword,
          new_password: newPassword,
          confirm_password: newPassword
        });

        if (response.status === 200) {
          return { success: true, message: response.data.message };
        } else {
          return { success: false, message: response.data.detail || '更新密码失败' };
        }
      } catch (error) {
        console.error('更新密码请求失败:', error);
        return { success: false, message: error.response?.data?.detail || '更新密码失败，请稍后再试' };
      }
    },

    // 用户注册
    async register(userData) {
      try {
        const telephone = userData.telephone?.trim();
        const payload = {
          username: userData.username,
          email: userData.email,
          password: userData.password,
          confirm_password: userData.confirm_password,
          ...(telephone ? { telephone } : {})
        };
        const response = await http.post('/user/register/', payload);

        if (response.data.status === 201 && response.data.token) {
          const token = response.data.token;
          const userInfo = response.data.user;

          localStorage.setItem('jwt_token', token);
          this.userInfo = userInfo;
          this.token = token;
          this.isLogin = true;

          return { success: true, message: response.data.message || '注册成功' };
        } else {
          return { success: false, message: response.data.message || '注册失败' };
        }
      } catch (error) {
        console.error('注册请求异常:', error);
        return {
          success: false,
          message: formatApiError(error.response?.data, '注册失败，请稍后重试')
        };
      }
    }
  },

  // 持久化配置
  persist: {
    enabled: true,
    strategies: [
      {
        key: 'user-store',
        storage: localStorage
      }
    ]
  }
});
