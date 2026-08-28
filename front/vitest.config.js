import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 与 vite.config.js 分离：单测不加载 Vite 代理/端口配置，避免互相干扰
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.spec.js'],
    coverage: {
      provider: 'v8',
      include: ['src/store/**', 'src/config/**', 'src/services/**'],
      // P2 基线（include 范围内实测 68/61/47/74，取余量），只升不降；低于阈值 CI 失败
      thresholds: {
        lines: 70,
        functions: 40,
        statements: 64,
        branches: 55,
      },
    },
  },
})
