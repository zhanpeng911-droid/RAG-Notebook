# Notebook 质量保障改进方案 · 第三轮（R3）

> 前置：R2 已落地（520 测试 / 60% 覆盖 / mypy 门禁 / CI 5 必需检查），经第三方复核属实（覆盖率多处实际高于报告，唯 branch protection 的 force-push/deletions 实际未配置）。本文件是**下一轮**工作说明书。
> 内容来源：R2 报告「遗留事项转 R3」+ R3 前实测的完整低覆盖清单 + 复核中发现的零碎问题。
> 执行原则同前：每任务独立提交（`ci:`/`test:`/`fix:`/`chore:` 前缀），不顺手重构业务代码；新问题记入文末「遗留问题登记」。

---

## 一、起点快照（2026-08-27 复核实测，无需重新测量）

- 后端：`uv sync --extra dev && uv run pytest tests -q --cov=app` → **520 passed / 6 skipped**，约 70 秒。
- 总覆盖率 **60%**（6560 语句 / 2621 未覆盖），CI 底线 `--cov-fail-under=57`。
- mypy 首批四目录（app/core、app/schemas、app/utils、app/config）**0 errors**，已转门禁。
- 前端：Vitest **16 个单测**；`@vitest/coverage-v8` 已装、`test:unit:coverage` 脚本已存在（R2 预留，尚未用）；ESLint 0 error / 4 warning。
- CI：4 处 `uv sync --locked` 无回退；branch protection 5 个必需检查（Backend / Django / Frontend build / Frontend E2E / Backend type check），**force-push 与 deletions 未禁用**（需补）。

### 本轮要处理的低覆盖文件（R3 前实测，精确到当前值）

| 文件 | 当前覆盖 | 备注 |
|---|---|---|
| **12 个 router 文件**（org/note/agent/audit/chat/health/review/runtime_config/user/knowledge_service/knowledge_router/space_router） | **0–32%** | R2 遗留 #1，鉴权负路径全空 |
| `services/note_service.py` | 35% | R2 遗留 #2 |
| `services/database_session_manager.py` | 21% | 会话生命周期 |
| `services/note_vector_index.py` | 45% | 向量索引服务 |
| `tasks/celery_app.py` | 37% | Celery 应用 |
| **`agent/agent.py` + `agent/agent_tools.py`** | **24% / 25%** | Agentic RAG 编排核心，此前从未被测 |
| `core/audit.py`、`db/redis_config.py`、`db/db_config.py`、`core/runtime_config.py`、`utils/auth_utils.py` | 45/25/48/51/58% | 杂项 |
| `core/failed_response.py`(+register)、`utils/config.py`、`config_handler.py`、`utils/path_tool.py`、`rag/sse_models.py`、`cache/llm_cache.py` | 全 0% | 小文件，可一次清 |

### 复核中发现的零碎问题（并入本轮）

- **branch protection 失实**：R2 报告称“禁 force push、禁删除分支”，实测 GitHub API 两项均为 `null`（未配置）。需补配。
- **`datetime.utcnow()` 弃用告警 89 个**：集中在 repositories 层新测试触发，Python 未来版本移除该 API，应改 timezone-aware。
- R2 遗留 #3（前端单测 ~50）与 #4（security 升级 triage + Django ruff）未做，明确转本轮。

---

## 二、任务清单

### P0-A：收尾（半天）

1. **分支保护补配**：`gh api -X PUT repos/zhanpeng911-droid/RAG-Notebook/branches/main/protection` 追加 `allow_force_pushes_enabled=false`、`allow_deletions_enabled=false`（保持 5 必需检查不变）。完成后用 `gh api .../protection` 复核 JSON 并留档。同步修正 R2 报告第四节该句表述。
2. **`utcnow()` 弃用清理**：全库 `grep -rn "utcnow" app tests`，改为 timezone-aware（`datetime.now(timezone.utc)` 或 `datetime.UTC`）；目标全量跑 `--disable-warnings` 后告警归零。
3. **小文件清零（一次提交）**：`failed_response.py`/`failed_response_register.py`、`utils/config.py`/`config_handler.py`/`path_tool.py`、`rag/sse_models.py`、`cache/llm_cache.py` 七个 0% 小文件补到 ≥80%。

### P1：后端覆盖深水区（核心，B1→B4 顺序逐批提交）

**B1 —— Router 层鉴权负路径（R2 遗留 #1，12 个文件 0–32% → ≥70%）**

- 方法：`httpx.AsyncClient` + `ASGITransport` 挂完整 FastAPI app，或 `TestClient`；DB 依赖用依赖注入 override / 内存 SQLite（R2 的 repositories 层已用真实 SQLite 内存库，模式可复用）。
- 每个路由文件至少覆盖：未带 token → 401；错误 token / 过期 → 401；普通用户访问他人资源 / 越权操作 → 403；合法请求 → 200 且行为正确；非法参数 → 422/400。
- 重点：`org_router`（组织隔离）、`note_router`（笔记归属）、`agent_router`（Agent 会话隔离）、`chat.py`（SSE 鉴权）。`space_router` 从 31% 往 ≥80% 推。

**B2 —— Services 补漏（R2 遗留 #2 + 实测发现）**

- `note_service.py` 35% → ≥80%：CRUD 分支、归属校验、笔记保存时序（权限隔离核心已有 `test_note_user_isolation`，补其余分支）。
- `database_session_manager.py` 21% → ≥80%：会话获取/归还、连接失效重建、并发下的事务边界。
- `note_vector_index.py` 45% → ≥80%：索引写入/删除/重建、失败回滚。
- `tasks/celery_app.py` 37% → ≥80%：broker 用假/内存传输（Celery eager 或 mock），测任务注册、重试、结果回写。

**B3 —— Agent 编排层（此前从未被测，优先级最高）**

- `agent/agent.py` 24%、`agent/agent_tools.py` 25% → **≥70%**：这是 Agentic RAG 的核心执行路径（工具路由、证据分级、CRAG 纠错、SSE 事件发射、超时与异常降级）。用 `_fakes` 风格的可编程 LLM 桩驱动真实 `create_agent_app` 走完整对话轮。
- 已有 `tests/test_agentic_rag_regressions.py` 覆盖了部分回归场景，本轮把执行路径的剩余分支补齐（拒答、无证据、多轮工具循环、热更新参数生效）。

**B4 —— 杂项（一次一轮）**

- `core/audit.py`（45）、`db/redis_config.py`（25）、`db/db_config.py`（48）、`core/runtime_config.py`（51）、`utils/auth_utils.py`（58，JWT 相关，重要）→ 均 ≥70%。

**B1–B4 完成后**：实测新基线（预计 68–72%），CI `--cov-fail-under` 更新为 实测−3（只升不降），同步更新 `QUALITY_ASSURANCE_PLAN_R2.md` 第六章与 README。

### P2：前端单测第二批（R2 遗留 #3）

1. `@vitest/coverage-v8` 已装，直接用；`npm run test:unit:coverage` 接入 CI（先观察不卡线，或设低底线 35% 起步）。
2. 按 R1 方案既定优先级 3–7 补：Pinia stores（`session`/`qaHistory`/`model`/`user`/`theme`）、`router/index.js` 守卫（未登录重定向 / 登录后受限页）、`RetrievalTrace.vue`/`QaHistoryPanel.vue` 引用 `[n]` 归一化与置信度徽标、`useChatWorkspace.js`/`useKnowledgeBase.js` 可提纯逻辑、`config/features.js`/`api.js` 断言。
3. 目标：单测 **16 → ~50**，`services/store/router/composables` 四类目录不再有 0% 文件。

### P3：security 与工具链（R2 遗留 #4）

1. **依赖升级 triage（六个）**：starlette（预计连带 fastapi 大版本——如属实，登记豁免并写明风险与替代方案）、pyasn1、pypdf、requests、pydantic-settings、langchain-classic。逐个在分支上升级 + 跑全量回归（SSE/multipart/检索链路重点回归）；可升级的合入，不可升级的登记豁免理由。
2. **门禁化**：`npm audit --audit-level=critical` 与 `pip-audit --fail-level=high` 从 advisory 转门禁（critical/high 阻断，medium 以下仅报告）；security job 按 backend/frontend 拆分以便独立判断。
3. **DjangoUserService 接入 ruff**：目前完全无 lint（仅 2 个测试文件）。`pyproject.toml` 加 ruff 配置（默认规则集），django job 加 lint 步骤，存量单独清理提交。

### P4：功能验收（延续 NovaMind R3 的方法论，本轮后半段）

> 目的：回答「产品功能真实可用」，与覆盖率互补。产出 `docs/functional-report.md`。

- **Part A（无外部依赖，TestClient 全自动）**：登录/注册/JWT 鉴权、笔记 CRUD 与用户隔离、知识库索引状态机（pending→indexed/failed→reindex）、SSE 事件流、运行时配置热更新生效、限流与 Prompt Injection 防护拦截。
- **Part B（真实 LLM，需 key，缺 key 显式跳过）**：Agentic RAG 端到端（带 `[n]` 引用的回答、Evidence Grader 置信度分级、CRAG 纠错回路、拒答路径）、LLM-as-judge 四维评分合理性、IR 评测（Recall@K/MRR）跑一轮记录基线。项目已有 `evals/` 工具与 `tests/test_agentic_rag_regressions.py`，脚本化复用。
- 新套件统一放 `tests/functional/`，`real_api` 标记，CI `--ignore`，发布前手动执行；每项 PASS/FAIL/部分/跳过，缺陷进登记。

---

## 三、长效规范（延续 + 更新一条）

- 延续 README 已有规范 + R2 两条（底线递推、版本三元组）。
- **更新**：真实 LLM 功能验收与 IR 评测纳入「发布前必跑清单」，上线前由人工执行，结果留档 `docs/functional-report.md`。

## 四、执行顺序与验收总清单

建议顺序：P0-A → P1-B3（agent 层最关键）→ P1-B1（router）→ P1-B2/B4 → 新基线落盘 → P2（前端，可与 P1 并行）→ P3 → P4 功能验收。

- [ ] P0-A：force-push/deletions 已禁用（API 复核留档）；告警归零；七个小文件 ≥80%
- [ ] P1-B1：12 个 router 文件 ≥70%（鉴权负路径 401/403 全覆盖）
- [ ] P1-B3：agent/agent_tools ≥70%
- [ ] P1-B2/B4：所列 services/杂项 ≥70%（celery_app ≥80%）
- [ ] 新基线落盘：`--cov-fail-under` = 实测−3，README/附录同步
- [ ] P2：前端单测 ~50，coverage 报表进 CI
- [ ] P3：依赖 triage 完成（升级或豁免登记）；pip/npm audit 门禁化；Django ruff 接入
- [ ] P4：`docs/functional-report.md` 产出，PASS/FAIL/跳过计数 + 缺陷登记完整

## 五、遗留问题登记（本轮执行中发现的新问题记在这里）

| # | 现象 | 归属 | 处置 |
|---|---|---|---|
|  |  |  |  |
