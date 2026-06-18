<template>
  <div class="aboutus-container">
    <div class="aboutus-content">
      <h2 class="project-title">RAG-Notebook</h2>
      <p class="project-intro">
        基于 LangChain + FastAPI + Django 的 RAG 智能笔记与知识库管理平台，为 LLM 应用提供开箱即用的检索增强能力，适配智能问答、企业知识库等场景。
      </p>

      <h3 class="section-title">技术栈</h3>
      <div class="tech-list">LangChain · FastAPI · Django · Vue 3 · Vant 4 · DeepSeek</div>

      <h3 class="section-title">功能特点</h3>
      <van-cell-group inset>
        <van-cell title="智能笔记" value="Markdown 编辑器，AI 续写/扩写/摘要，自动标签" />
        <van-cell title="AI 对话" value="基于笔记与知识库的 RAG 智能问答，流式输出" />
        <van-cell title="知识库管理" value="支持文档上传、自动语义切片与智能检索" />
        <van-cell title="每日回顾" value="间隔重复复习，强化记忆" />
        <van-cell title="多模型支持" value="DeepSeek / OpenAI / Ollama 等，可自由切换" />
        <van-cell title="主题定制" value="浅色/深色主题，支持个性化切换" />
      </van-cell-group>
    </div>
  </div>
</template>

<style scoped>
.aboutus-container {
  min-height: 100vh;
  background-color: var(--color-bg);
  color: var(--color-text);
  padding-top: 46px;
  padding-bottom: 20px;
}

.aboutus-content {
  padding: 20px 16px;
}

.project-title {
  font-size: 18px;
  font-weight: 600;
  font-family: var(--font-heading);
  margin: 0 0 12px;
  text-align: center;
  color: var(--color-text);
}

.project-intro {
  font-size: 14px;
  color: var(--color-text-light);
  line-height: 1.6;
  margin: 0 0 24px;
  text-align: center;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  font-family: var(--font-heading);
  margin: 0 0 12px;
  color: var(--color-text-lighter);
}

.tech-list {
  font-size: 14px;
  color: var(--color-text);
  margin-bottom: 24px;
  padding: 0 4px;
}
</style>
