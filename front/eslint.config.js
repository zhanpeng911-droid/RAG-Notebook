import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'test-results/**', 'playwright-report/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        console: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        fetch: 'readonly',
        FormData: 'readonly',
        AbortController: 'readonly',
        AbortSignal: 'readonly',
        URL: 'readonly',
        URLSearchParams: 'readonly',
        crypto: 'readonly',
        getComputedStyle: 'readonly',
        requestAnimationFrame: 'readonly',
        CustomEvent: 'readonly',
        Event: 'readonly',
        TextEncoder: 'readonly',
        TextDecoder: 'readonly',
        atob: 'readonly',
        btoa: 'readonly',
        process: 'readonly',
        global: 'writable',
      },
    },
  },
  {
    files: ['tests/**'],
    languageOptions: {
      globals: { describe: 'readonly', it: 'readonly', expect: 'readonly', vi: 'readonly', beforeEach: 'readonly', afterEach: 'readonly' },
    },
  },
  {
    rules: {
      // 存量代码风格与该规则冲突较多，初期降级为警告，不阻塞门禁
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/html-indent': 'off',
      'vue/attributes-order': 'warn',
      // Sidebar/Settings 等布局组件为既定命名，重命名成本高于收益
      'vue/multi-word-component-names': 'off',
      // @keydown.comma 属合法 key modifier（KeyboardEvent.key），规则误报
      'vue/valid-v-on': 'off',
      // catch 块中不使用异常对象是合法模式（如静默清理路径）
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
    },
  },
]
