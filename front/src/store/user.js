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
    // 认证代次：登出/401/切换账号时递增，用于丢弃迟到请求写回旧用户数据
    authEpoch: 0,
    userBio: '这是我的个人简介'
  }),

  getters: {
    getUserInfo: (state) => state.userInfo,
    getToken: (state) => state.token,
    getLoginStatus: (state) => state.isLogin,
    getUserBio: (state) => state.userInfo?.bio || state.userBio
  },

  actions: {
    // 统一认证会话更新：登录/注册/刷新/资料修改/密码修改后复用。
    // 返回是否拿到 token；user 可选（改密码只返回新 token）。
    applyAuthSession(data = {}) {
      const token = data.token || data.access || data.data?.token || data.data?.access;
      const userInfo = data.user || data.data?.user || null;
      if (userInfo) this.userInfo = userInfo;
      if (token) {
        this.token = token;
        this.isLogin = true;
        localStorage.setItem('jwt_token', token);
      }
      return Boolean(token);
    },

    // 登录
    async login(userData) {
      try {
        const response = await http.post(apiConfig.endpoints.login, {
          username: userData.username,
          password: userData.password
        });

        if (response.status === 200) {
          if (!this.applyAuthSession(response.data)) {
            return { success: false, message: response.data.message || '登录失败：未返回 token' };
          }
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
        await this.clearAuth();
      }
    },

    // 统一清理用户级数据（会话列表、问答历史等）。
    // 动态 import 避免 user → session → http → user 的静态循环依赖。
    async clearUserScopedData() {
      const [{ useSessionStore }, { useQaHistoryStore }] = await Promise.all([
        import('./session'),
        import('./qaHistory'),
      ]);
      useSessionStore().clearSessions();
      useQaHistoryStore().clear();
    },

    // 清除认证状态 —— 登出/401/切换账号统一入口。
    // 递增 authEpoch 使在途 SSE/fetch 的迟到写回被丢弃；不清 localStorage 全量，
    // 保留主题等非敏感偏好。
    async clearAuth() {
      this.authEpoch += 1;
      this.userInfo = null;
      this.token = '';
      this.isLogin = false;
      localStorage.removeItem('jwt_token');
      await this.clearUserScopedData();
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
          this.applyAuthSession(response.data);
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
          // Django 端已撤销旧 token 并签发新 token（见 views.py ResetPasswordView）
          // 必须立即接管新 token，否则后续请求仍用已撤销的旧 token
          this.applyAuthSession(response.data);
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
          this.applyAuthSession(response.data);
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
