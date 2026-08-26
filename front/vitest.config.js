import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 与 vite.config.js 分离：单测不加载 Vite 代理/端口配置，避免互相干扰
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.spec.js'],
  },
})
