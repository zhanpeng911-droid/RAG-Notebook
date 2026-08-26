<template>
  <div class="markdown-editor-wrapper">
    <Editor
      ref="editorRef"
      :value="modelValue"
      :plugins="plugins"
      :locale="locale"
      :toolbar="['source']"
      @change="handleChange"
    />
  </div>
</template>

<script setup>
/**
 * MarkdownEditor 组件 —— 基于 bytemd 封装。
 * 集成 gfm / highlight / mermaid / math / medium-zoom / frontmatter 插件。
 * 通过 defineExpose 暴露 getEditorCm() 供父组件的 QuickToolbar 调用 CodeMirror API。
 */
import { ref, defineExpose } from 'vue'
import { Editor } from '@bytemd/vue-next'
import gfm from '@bytemd/plugin-gfm'
import highlight from '@bytemd/plugin-highlight'
import mermaid from '@bytemd/plugin-mermaid'
import math from '@bytemd/plugin-math'
import mediumZoom from '@bytemd/plugin-medium-zoom'
import frontmatter from '@bytemd/plugin-frontmatter'
import 'bytemd/dist/index.css'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.css'

defineProps({
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

/** bytemd Editor 组件 ref */
const editorRef = ref(null)

/** 插件列表 */
const plugins = [gfm(), highlight(), mermaid(), math(), mediumZoom(), frontmatter()]

/** 中文 locale 配置 */
const locale = {
  h1: '一级标题',
  h2: '二级标题',
  h3: '三级标题',
  bold: '加粗',
  italic: '斜体',
  quote: '引用',
  link: '链接',
  image: '图片',
  code: '代码',
  list: '列表',
  orderedList: '有序列表',
  help: '帮助',
  toc: '目录',
  fullscreen: '全屏',
  exitFullscreen: '退出全屏',
  source: '源码',
  preview: '预览',
  write: '编辑',
  sideBySide: '分屏',
}

function handleChange(v) {
  emit('update:modelValue', v)
}

/**
 * 获取 CodeMirror 编辑器实例，供父组件 QuickToolbar 调用。
 * 优先通过 bytemd 组件 ref，兜底走 DOM 查询。
 */
function getEditorCm() {
  // 尝试通过组件的 $editor 内部引用获取
  if (editorRef.value?.$editor?.codemirror) {
    return editorRef.value.$editor.codemirror
  }

  // 兜底：DOM 查询 CodeMirror 实例（CodeMirror 将实例挂在 DOM 元素上）
  const el = document.querySelector('.markdown-editor-wrapper .CodeMirror')
  return el?.CodeMirror || null
}

/**
 * 获取光标上下文：光标前文本 + 光标在页面中的坐标
 */
function getCursorContext() {
  const cm = getEditorCm()
  if (!cm) return null
  try {
    const cursor = cm.getCursor()
    const textBeforeCursor = cm.getRange({ line: 0, ch: 0 }, cursor)
    const coords = cm.cursorCoords(true, 'window')
    return {
      textBeforeCursor,
      cursorCoords: coords,
      line: cursor.line,
      ch: cursor.ch
    }
  } catch (e) {
    return null
  }
}

defineExpose({ getEditorCm, getCursorContext })
</script>

<style>
/** 全局样式覆盖 bytemd 默认样式 */
.markdown-editor-wrapper {
  min-height: 100%;
}
.markdown-editor-wrapper .bytemd {
  min-height: 100%;
  height: auto !important;
  border: none !important;
  font-family: var(--font-body);
  background: var(--color-card);
}
.markdown-editor-wrapper .bytemd-toolbar {
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
/* 编辑器正文区域 */
.markdown-editor-wrapper .CodeMirror {
  font-family: var(--font-mono);
  font-size: 15px;
  line-height: 1.7;
  background: var(--color-card);
}
/* 预览区域 */
.markdown-editor-wrapper .markdown-body {
  font-family: 'Noto Serif SC', serif;
  font-size: 15px;
  line-height: 1.8;
  background: var(--color-card);
  padding: 20px 24px;
}
/* 代码块：底色高亮 */
.markdown-editor-wrapper .markdown-body pre {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}
.markdown-editor-wrapper .markdown-body code {
  background: var(--color-surface);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-family: var(--font-mono);
  font-size: 13px;
}
.markdown-editor-wrapper .markdown-body pre code {
  background: transparent;
  padding: 0;
}
/* 编辑区高亮匹配 markdown-body */
.markdown-editor-wrapper .cm-s-default .cm-comment {
  color: var(--color-text-lighter);
  background: transparent;
}

/* 隐藏 bottom status bar 中的按钮 */
.markdown-editor-wrapper .bytemd-status-left .bytemd-toolbar-icon,
.markdown-editor-wrapper .bytemd-status-right .bytemd-toolbar-icon {
  display: none !important;
}
/* 隐藏 bytemd-toolbar-right 中的全屏(4)和About/Logo(5)按钮 */
.markdown-editor-wrapper .bytemd-toolbar-right > [bytemd-tippy-path="4"],
.markdown-editor-wrapper .bytemd-toolbar-right > [bytemd-tippy-path="5"] {
  display: none !important;
}
</style>
