import { defineStore } from 'pinia';

export const useThemeStore = defineStore('theme', {
  state: () => ({
    currentTheme: localStorage.getItem('theme') || 'light',
    themes: {
      light: {
        name: '浅色·深蓝',
        bg: '#e7eef7',
        surface: '#d9e5f2',
        card: '#f8fbff',
        text: '#102033',
        textLight: '#38506f',
        textLighter: '#647b97',
        textLightest: '#8aa0b8',
        primary: '#3478df',
        tabbarActive: '#3478df',
        border: '#b2c7de',
        borderLight: '#c9d8e8',
        divider: '#d5e1ee',
        shadow: 'rgba(31, 60, 96, 0.12)',
      },
      dark: {
        name: '深色·深蓝',
        bg: '#0c1622',
        surface: '#131f30',
        card: '#1a2a3e',
        text: '#d4e2f0',
        textLight: '#8ea8c4',
        textLighter: '#5e7d9e',
        textLightest: '#3e5878',
        primary: '#5ea8ff',
        tabbarActive: '#5ea8ff',
        border: '#263c58',
        borderLight: '#1e3048',
        divider: '#1a2a3e',
        shadow: 'rgba(0, 0, 0, 0.30)',
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
      const root = document.documentElement;
      root.setAttribute('data-theme', this.currentTheme);
      root.style.setProperty('--color-bg', t.bg);
      root.style.setProperty('--color-surface', t.surface);
      root.style.setProperty('--color-card', t.card);
      root.style.setProperty('--color-text', t.text);
      root.style.setProperty('--color-text-light', t.textLight);
      root.style.setProperty('--color-text-lighter', t.textLighter);
      root.style.setProperty('--color-text-lightest', t.textLightest);
      root.style.setProperty('--color-primary', t.primary);
      root.style.setProperty('--color-primary-hover', t.primaryHover || t.primary);
      root.style.setProperty('--color-primary-light', this.currentTheme === 'dark' ? 'rgba(94,168,255,0.12)' : 'rgba(52,120,223,0.12)');
      root.style.setProperty('--color-primary-softer', this.currentTheme === 'dark' ? 'rgba(94,168,255,0.06)' : 'rgba(52,120,223,0.07)');
      root.style.setProperty('--color-success', t.success || '#22a060');
      root.style.setProperty('--color-error', t.error || '#d93025');
      root.style.setProperty('--color-warning', t.warning || '#e6940a');
      root.style.setProperty('--color-border', t.border);
      root.style.setProperty('--color-border-light', t.borderLight);
      root.style.setProperty('--color-divider', t.divider);
      root.style.setProperty('--van-tabbar-item-active-color', t.tabbarActive);
      root.style.setProperty('--color-shadow', t.shadow);
    },

    initTheme() {
      this.applyTheme();
    },
  },
});
