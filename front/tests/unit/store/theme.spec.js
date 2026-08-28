import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useThemeStore } from '../../../src/store/theme'

describe('theme store', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('默认浅色主题', () => {
    const store = useThemeStore()
    expect(store.currentTheme).toBe('light')
    expect(store.getThemeConfig.name).toBe('浅色·湛蓝')
  })

  it('从 localStorage 恢复主题', () => {
    localStorage.setItem('theme', 'dark')
    const store = useThemeStore()
    expect(store.currentTheme).toBe('dark')
  })

  it('getAllThemes 返回全部主题摘要', () => {
    const store = useThemeStore()
    const themes = store.getAllThemes
    expect(themes).toHaveLength(2)
    expect(themes[0]).toHaveProperty('id')
    expect(themes[0]).toHaveProperty('primaryColor')
  })

  it('setTheme 切换并持久化 + 应用 CSS 变量', () => {
    const store = useThemeStore()
    store.setTheme('dark')
    expect(store.currentTheme).toBe('dark')
    expect(localStorage.getItem('theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.style.getPropertyValue('--color-bg')).toBeTruthy()
  })

  it('setTheme 忽略未知主题', () => {
    const store = useThemeStore()
    store.setTheme('neon')
    expect(store.currentTheme).toBe('light')
  })

  it('applyTheme 浅色不设 data-theme=dark 分支变量', () => {
    const store = useThemeStore()
    store.applyTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })

  it('initTheme 应用当前主题', () => {
    localStorage.setItem('theme', 'dark')
    const store = useThemeStore()  // 建 store 前设 localStorage，currentTheme 为 dark
    store.initTheme()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
  })
})
