import { defineStore } from 'pinia';

export const useThemeStore = defineStore('theme', {
  state: () => ({
    currentTheme: localStorage.getItem('theme') || 'light',
    themes: {
      light: {
        name: '浅色·湛蓝',
        bg: '#eef2f8',
        surface: '#f6f9fd',
        card: '#ffffff',
        text: '#0f1e3d',
        textLight: '#33436b',
        textLighter: '#5b6b8f',
        textLightest: '#8b99b8',
        primary: '#1e40af',
        primaryHover: '#1e3a8a',
        tabbarActive: '#1e40af',
        border: '#dbe4f0',
        borderLight: '#e6edf6',
        divider: '#dbe4f0',
        shadow: 'rgba(15, 30, 61, 0.08)',
        success: '#059669',
        error: '#dc2626',
        warning: '#d97706',
      },
      dark: {
        name: '深色·深蓝',
        bg: '#0c1222',
        surface: '#141d33',
        card: '#1a2540',
        text: '#e6ecf8',
        textLight: '#b6c2dc',
        textLighter: '#8494b5',
        textLightest: '#5a6a8c',
        primary: '#3b82f6',
        primaryHover: '#60a5fa',
        tabbarActive: '#3b82f6',
        border: '#26334f',
        borderLight: '#1f2b44',
        divider: '#26334f',
        shadow: 'rgba(0, 0, 0, 0.35)',
        success: '#34d399',
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
      root.style.setProperty('--color-shadow-strong', dark ? 'rgba(0,0,0,0.45)' : 'rgba(15,30,61,0.14)');
      root.style.setProperty('--van-tabbar-item-active-color', t.tabbarActive);

      // 玻璃拟态变量（DocMind 风格核心）
      if (dark) {
        root.style.setProperty('--glass-bg', 'rgba(20, 29, 51, 0.62)');
        root.style.setProperty('--glass-bg-strong', 'rgba(26, 37, 64, 0.82)');
        root.style.setProperty('--glass-border', 'rgba(148, 175, 226, 0.14)');
        root.style.setProperty('--glass-shadow', '0 8px 32px rgba(0, 0, 0, 0.38)');
        root.style.setProperty('--grid-line', 'rgba(96, 165, 250, 0.055)');
      } else {
        root.style.setProperty('--glass-bg', 'rgba(255, 255, 255, 0.62)');
        root.style.setProperty('--glass-bg-strong', 'rgba(255, 255, 255, 0.84)');
        root.style.setProperty('--glass-border', 'rgba(255, 255, 255, 0.65)');
        root.style.setProperty('--glass-shadow', '0 8px 32px rgba(15, 30, 61, 0.10)');
        root.style.setProperty('--grid-line', 'rgba(30, 64, 175, 0.05)');
      }

      // 语义状态色
      if (dark) {
        root.style.setProperty('--status-success-bg', '#052e16');
        root.style.setProperty('--status-success-text', '#34d399');
        root.style.setProperty('--status-warning-bg', '#422006');
        root.style.setProperty('--status-warning-text', '#fbbf24');
        root.style.setProperty('--status-error-bg', '#450a0a');
        root.style.setProperty('--status-error-text', '#f87171');
        root.style.setProperty('--status-info-bg', '#172554');
        root.style.setProperty('--status-info-text', '#60a5fa');
        root.style.setProperty('--status-neutral-bg', '#1f2b44');
        root.style.setProperty('--status-neutral-text', '#a1a1aa');
      } else {
        root.style.setProperty('--status-success-bg', '#ecfdf5');
        root.style.setProperty('--status-success-text', '#059669');
        root.style.setProperty('--status-warning-bg', '#fffbeb');
        root.style.setProperty('--status-warning-text', '#d97706');
        root.style.setProperty('--status-error-bg', '#fef2f2');
        root.style.setProperty('--status-error-text', '#dc2626');
        root.style.setProperty('--status-info-bg', '#eff6ff');
        root.style.setProperty('--status-info-text', '#1e40af');
        root.style.setProperty('--status-neutral-bg', '#f1f5fb');
        root.style.setProperty('--status-neutral-text', '#5b6b8f');
      }
    },

    initTheme() {
      this.applyTheme();
    },
  },
});
