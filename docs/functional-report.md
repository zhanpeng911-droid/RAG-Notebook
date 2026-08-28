# Notebook 功能验收报告

> 生成：2026-08-28（R3-P4）
> 目的：回答「产品功能真实可用」，与覆盖率互补。套件位于 `backend/tests/functional/`，
> 标记 `real_api`，CI `--ignore`，发布前手动执行；缺 LLM key 的用例显式跳过。

## 执行方式

```bash
cd backend
# Part A（无外部依赖，全自动）
uv run pytest tests/functional -q --no-header
# Part B（真实 LLM，需配置 DEEPSEEK_API_KEY / OPENAI_API_KEY / DASHSCOPE_API_KEY）
uv run pytest tests/functional -m real_api -q --no-header
```

## 结果汇总（本次执行）

| 项 | 计数 |
|---|---|
| PASS | 14 |
| FAIL | 0 |
| 部分 | 0 |
| 跳过 | 0（已配置 LLM Key，real_api 全部执行） |

## Part A —— 无外部依赖（TestClient + 内存 SQLite）

| 验收项 | 用例 | 结果 |
|---|---|---|
| JWT 鉴权 | 无 token → 401；合法 token → 200 | PASS |
| JWT 鉴权 | 伪造 token → 401 | PASS |
| 笔记 CRUD | 创建 / 读取 / 更新 / 删除 | PASS |
| 用户隔离 | 越权读/更新他人笔记 → 404 | PASS |
| 笔记列表 | 分页 total_count | PASS |
| 运行时配置热更新 | PUT 后即时生效、读取点生效、reset 恢复默认 | PASS |
| 知识库索引状态机 | v2 上传 pending → index-status indexed → reindex 成功 | PASS |
| 限流 | note/create 连打 12 次出现 429 | PASS |
| SSE 事件流 | agent 流式查询返回 text/event-stream + started/completed + run_id | PASS |
| Prompt Injection 防护 | 注入查询经 Guardrails 净化（ignore previous instructions/system 移除） | PASS |
| Guardrails 校验 | user_id 校验 | PASS |

## Part B —— 真实 LLM（发布前手动执行，缺 key 跳过）

| 验收项 | 用例 | 结果 |
|---|---|---|
| Agentic RAG 端到端 | run_agent 返回答案；有引用时含 [n] 标记 | PASS（真实 LLM） |
| Agentic RAG 拒答路径 | 无关问题不强答 | PASS（真实 LLM） |
| IR 评测基线 | Recall@K / MRR 纯函数（不依赖 key） | PASS |

## 缺陷登记

| # | 现象 | 状态 |
|---|---|---|
| （无） | 本次验收未发现功能缺陷 | — |

## 说明

- Part A 覆盖：鉴权负路径、笔记 CRUD 与用户隔离、知识库索引状态机、SSE 事件流、
  运行时配置热更新、限流 429、Prompt Injection 防护拦截。
- Part B 本次已在配置 LLM Key 的环境执行并全部 PASS（真实 LLM 调用）。
- 功能套件与单元测试分离（CI `--ignore=tests/functional`），避免真实 LLM 依赖拖慢 CI。
