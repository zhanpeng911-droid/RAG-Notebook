import { createI18n } from 'vue-i18n';
import zhCN from './locales/zh-CN.js';

// 创建i18n实例
export function setupI18n() {
  const i18n = createI18n({
    legacy: false, // 使用组合式API
    locale: 'zh-CN',
    fallbackLocale: 'zh-CN',
    messages: {
      'zh-CN': zhCN
    }
  });
  
  return i18n;
}
