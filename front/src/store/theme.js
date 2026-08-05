import { defineStore } from 'pinia';

export const useThemeStore = defineStore('theme', {
  state: () => ({
    currentTheme: localStorage.getItem('theme') || 'light',
    themes: {
      light: {
        name: '浅色·纸感',
        bg: '#ede9e0',
        surface: '#f5f4ee',
        card: '#ffffff',
        text: '#141413',
        textLight: '#3a3a36',
        textLighter: '#6b6a63',
        textLightest: '#9c9b91',
        primary: '#1e40af',
        primaryHover: '#1e3a8a',
        tabbarActive: '#1e40af',
        border: '#ebe9e0',
        borderLight: '#f0eee6',
        divider: '#ebe9e0',
        shadow: 'rgba(20, 20, 19, 0.06)',
        success: '#15803d',
        error: '#b91c1c',
        warning: '#b45309',
      },
      dark: {
        name: '深色·纸感',
        bg: '#1a1816',
        surface: '#242220',
        card: '#2a2725',
        text: '#e8e6e0',
        textLight: '#b8b5ad',
        textLighter: '#8a8780',
        textLightest: '#5c5a54',
        primary: '#3b82f6',
        primaryHover: '#60a5fa',
        tabbarActive: '#3b82f6',
        border: '#3a3735',
        borderLight: '#302e2c',
        divider: '#3a3735',
        shadow: 'rgba(0, 0, 0, 0.20)',
        success: '#4ade80',
        error: '#f87171',
        warning: '#fbbf24',
      },
    },
  }),

  getters: {
    getCurrentTheme: (state) => state.currentTheme,
    getThemeConfig: (state) => state.themes[state.currentTheme],
    getAllThemes: (state) =>
      Object.keys(state.themes).map((key) => ({
        id: key,
        name: state.themes[key].name,
        primaryColor: state.themes[key].primary,
        bgColor: state.themes[key].bg,
      })),
  },

  actions: {
    setTheme(themeName) {
      if (this.themes[themeName]) {
        this.currentTheme = themeName;
        localStorage.setItem('theme', themeName);
        this.applyTheme();
      }
    },

    applyTheme() {
      const t = this.themes[this.currentTheme];
      const dark = this.currentTheme === 'dark';
      const root = document.documentElement;
      root.setAttribute('data-theme', this.currentTheme);
      root.style.setProperty('--color-bg', t.bg);
      root.style.setProperty('--color-surface', t.surface);
      root.style.setProperty('--color-card', t.card);
      root.style.setProperty('--color-sidebar', t.surface);
      root.style.setProperty('--color-text', t.text);
      root.style.setProperty('--color-text-light', t.textLight);
      root.style.setProperty('--color-text-lighter', t.textLighter);
      root.style.setProperty('--color-text-lightest', t.textLightest);
      root.style.setProperty('--color-primary', t.primary);
      root.style.setProperty('--color-primary-hover', t.primaryHover || t.primary);
      root.style.setProperty('--color-primary-active', t.primary);
      root.style.setProperty('--color-primary-light', dark ? 'rgba(59,130,246,0.12)' : 'rgba(30,64,175,0.10)');
      root.style.setProperty('--color-primary-softer', dark ? 'rgba(59,130,246,0.06)' : 'rgba(30,64,175,0.05)');
      root.style.setProperty('--color-success', t.success);
      root.style.setProperty('--color-error', t.error);
      root.style.setProperty('--color-warning', t.warning);
      root.style.setProperty('--color-border', t.border);
      root.style.setProperty('--color-border-light', t.borderLight);
      root.style.setProperty('--color-divider', t.divider);
      root.style.setProperty('--color-shadow', t.shadow);
      root.style.setProperty('--color-shadow-strong', dark ? 'rgba(0,0,0,0.35)' : 'rgba(0,0,0,0.08)');
      root.style.setProperty('--van-tabbar-item-active-color', t.tabbarActive);

      // 语义状态色
      if (dark) {
        root.style.setProperty('--status-success-bg', '#052e16');
        root.style.setProperty('--status-success-text', '#4ade80');
        root.style.setProperty('--status-warning-bg', '#422006');
        root.style.setProperty('--status-warning-text', '#fbbf24');
        root.style.setProperty('--status-error-bg', '#450a0a');
        root.style.setProperty('--status-error-text', '#f87171');
        root.style.setProperty('--status-info-bg', '#172554');
        root.style.setProperty('--status-info-text', '#60a5fa');
        root.style.setProperty('--status-neutral-bg', '#27272a');
        root.style.setProperty('--status-neutral-text', '#a1a1aa');
      } else {
        root.style.setProperty('--status-success-bg', '#f0fdf4');
        root.style.setProperty('--status-success-text', '#15803d');
        root.style.setProperty('--status-warning-bg', '#fffbeb');
        root.style.setProperty('--status-warning-text', '#b45309');
        root.style.setProperty('--status-error-bg', '#fef2f2');
        root.style.setProperty('--status-error-text', '#b91c1c');
        root.style.setProperty('--status-info-bg', '#eff6ff');
        root.style.setProperty('--status-info-text', '#1e40af');
        root.style.setProperty('--status-neutral-bg', '#f5f5f5');
        root.style.setProperty('--status-neutral-text', '#737373');
      }
    },

    initTheme() {
      this.applyTheme();
    },
  },
});
