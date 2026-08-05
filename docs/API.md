# 后端 API 文档

## 概述

后端使用 FastAPI 框架，所有 API 统一使用 `/api/v1` 前缀。
完整接口请以 FastAPI Swagger 为准：`http://127.0.0.1:8002/api/v1/docs`

## 鉴权

所有需要鉴权的接口使用 JWT Bearer Token，由 DjangoUserService 签发。
请求头需携带：`Authorization: Bearer <token>`

## 模块索引

| 模块 | 路由前缀 | 说明 |
|------|----------|------|
| Agent | `/api/v1/chat/agent` | Agentic RAG 问答 |
| Chat | `/api/v1/chat` | 会话管理 |
| Knowledge | `/api/v1/knowledge` | 知识库文档管理 |
| Note | `/api/v1/note` | 笔记管理 |
| Review | `/api/v1/review` | 间隔复习 |
| Org | `/api/v1/org` | 组织管理 |
| Space | `/api/v1/space` | 空间管理 |
| Audit | `/api/v1/audit` | 审计日志 |
| Health | `/api/v1/health` | 健康检查 |

## Agent 问答

### POST /api/v1/chat/agent/query
非流式 Agentic RAG 查询，等待完成后返回完整结果。

**请求体：**
```json
{
  "query": "Redis 的 AOF 持久化有哪几种策略？",
  "session_id": "可选，会话ID",
  "space_id": "可选，空间ID",
  "llm_config": "可选，LLM配置"
}
```

**返回：**
```json
{
  "code": 200,
  "data": {
    "answer": "答案文本",
    "citations": [{"index": 1, "title": "来源文件名", "content_preview": "..."}],
    "quality_scores": {
      "faithfulness_score": 0.9,
      "completeness_score": 0.8,
      "relevance_score": 0.9,
      "overall_score": 0.85,
      "issues": [],
      "suggestions": []
    },
    "phases": [...]
  }
}
```

### POST /api/v1/chat/agent/query/stream
流式 Agentic RAG 查询（SSE），实时返回 Agent 执行过程和答案。

**SSE 事件类型：** started / planning / retrieving / retrieval_completed / grading_evidence / rewriting_query / generating_answer / citation / completed / error

## 知识库

### POST /api/v1/knowledge/add/single/v2
上传单个文件（解耦版本，自动索引）。

**请求：** `multipart/form-data`
- `file`: 文件（支持 .pdf/.txt/.md/.pptx/.docx）
- `space_id`: 可选 query 参数

**返回：**
```json
{
  "code": 200,
  "data": {
    "document_id": "uuid",
    "filename": "原始文件名",
    "status": "indexed",
    "message": "文件上传成功"
  }
}
```

**冲突返回（409）：** 文件名已存在
```json
{"detail": "文件名「xxx.txt」已存在，请重命名后重新上传"}
```

### DELETE /api/v1/knowledge/documents/{document_id}
删除指定文档（含向量数据和物理文件）。

### GET /api/v1/knowledge/index-status
查询当前用户所有文档的索引进度。

## 笔记

### POST /api/v1/note/create
创建笔记。

### GET /api/v1/note/list
获取笔记列表（分页）。

### PUT /api/v1/note/{note_id}
更新笔记内容。

### DELETE /api/v1/note/{note_id}
删除笔记。

## 会话管理

### GET /api/v1/chat/sessions
获取当前用户的所有会话ID。

### GET /api/v1/chat/session/{session_id}
获取指定会话的历史记录。

### DELETE /api/v1/chat/session/{session_id}
删除指定会话。

## 健康检查

### GET /api/v1/health
基础健康检查。

### GET /api/v1/health/db
数据库连通性检查。

### GET /api/v1/health/redis
Redis 连通性检查。
