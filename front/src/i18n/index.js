import { createI18n } from 'vue-i18n';
import zhCN from './locales/zh-CN.js';

function toRuntimeMessages(messages) {
  return Object.fromEntries(
    Object.entries(messages).map(([key, value]) => {
      if (typeof value === 'string') {
        return [key, (ctx) => value.replace(/\{(\w+)\}/g, (_, name) => ctx.named(name))];
      }
      return [key, toRuntimeMessages(value)];
    })
  );
}

// 创建i18n实例
export function setupI18n() {
  const i18n = createI18n({
    legacy: false, // 使用组合式API
    locale: 'zh-CN',
    fallbackLocale: 'zh-CN',
    messages: {
      'zh-CN': toRuntimeMessages(zhCN)
    }
  });
  
  return i18n;
}
