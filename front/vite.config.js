import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vite.dev/config/
// 本地默认 8002，避免与已占用的 8000 冲突；可用环境变量覆盖
const BACKEND_TARGET = process.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8002'
const USER_TARGET = process.env.VITE_USER_TARGET || 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    // Keep the strict CSP: use the i18n build that does not compile messages in the browser.
    alias: {
      'vue-i18n': 'vue-i18n/dist/vue-i18n.runtime.esm-bundler.js'
    }
  },
  server: {
    port: 3076,
    host: true, // 允许局域网访问
    proxy: {
      // /api/v1 统一代理到 backend（API 版本管理后所有后端端点统一前缀）
      '/api/v1': {
        target: BACKEND_TARGET,
        changeOrigin: true,
        ws: true
      },
      // 健康检查（不走 /api/v1 前缀）
      '/health': {
        target: BACKEND_TARGET,
        changeOrigin: true
      },
      // 用户相关接口代理到 Django
      '/user': {
        target: USER_TARGET,
        changeOrigin: true
      },
      '/file': {
        target: USER_TARGET,
        changeOrigin: true
      }
    }
  }
})
