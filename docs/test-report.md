# RAG-Notebook 测试报告（整合版）

> 本报告整合原 `TEST_REPORT.md`（R1 基线）、`QA_EXECUTION_REPORT_R2.md`（R2 执行）、
> `docs/functional-report.md`（R3 功能验收）三份报告，汇总当前全量状态与质量保障三轮演进。
> 生成：2026-08-28（R3 收官实测）。

| 项目 | 内容 |
|------|------|
| **仓库** | https://github.com/zhanpeng911-droid/RAG-Notebook |
| **分支** | `main`（5 必需检查 + force-push/deletions 禁用） |
| **环境** | Windows / Python 3.12 (uv) / Node 22 / Vitest 4 / Playwright Chromium |

---

## 1. 当前全量状态（R3 收官实测）

| 套件 | 通过 | 失败 | 跳过 | 结果 | 备注 |
|------|-----:|-----:|-----:|:----:|------|
| Backend pytest（`--ignore=tests/functional`） | 943 | 0 | 6 | ✅ | 覆盖率 **88%**，CI 底线 85% |
| Django user/file | 12 | 0 | 0 | ✅ | ruff 0 error（已接入） |
| Frontend Vitest 单测 | 61 | 0 | 0 | ✅ | 覆盖率门禁进 CI（lines70/funcs40/stmts64/branches55） |
| 功能验收 `tests/functional` | 14 | 0 | 0 | ✅ | 含 2 个真实 LLM 用例（已配置 Key 执行） |
| **合计（可复现）** | **1030** | **0** | **6** | **✅** | 不含历史 E2E 基线 |

> 6 个跳过均为 `test_knowledge_file_validator.py` 的 MIME 用例：Windows 上
> `python-magic` 会 segfault，`sys.platform == "win32"` 时跳过；**CI（ubuntu）会执行**。

### 覆盖率演进（后端 app 全量）

| 阶段 | 语句覆盖 | 测试数 | CI 底线 |
|---|---|---|---|
| R1 基线（2026-07-21） | 31% | 235 + 6 skip | 无 |
| R2 收官（2026-08-27） | **60%** | 520 + 6 skip | `--cov-fail-under=57` |
| **R3 收官（2026-08-28）** | **88%** | 943 + 6 skip | `--cov-fail-under=85`（88%−3） |

---

## 2. Backend pytest

### 2.1 执行命令

```bash
cd backend
uv sync --extra dev
uv run pytest tests -q --no-header --ignore=tests/functional --cov=app --cov-report=term-missing
```

### 2.2 结果

```text
943 passed, 6 skipped, 2 warnings in ~160s
TOTAL: 6560 语句 / 796 未覆盖 / 88%
```

### 2.3 R3 新增覆盖（三轮累计）

| 批次 | 内容 | 代表文件（覆盖率） |
|---|---|---|
| P0-A | utcnow 弃用清零；七个小文件 ≥96% + mask_sensitive_info 安全修复 | sse_models / llm_cache / config×2 / failed_response×2 |
| P1-B1 | **12 个 router 全部 ≥70%**（鉴权负路径 401/403 + 越权隔离） | agent 70 / space 97 / org 87 / knowledge_router 77 / knowledge_service 96 / health·chat·runtime_config·user 100 / audit 84 / review 96 / note 73 |
| P1-B2 | services/tasks ≥80% | note_service 90 / database_session_manager 91 / note_vector_index 98 / celery_app 100 |
| P1-B4 | 杂项 ≥70% | core/audit 100 / auth_utils 95 / db_config 100 / redis_config 84 / runtime_config 94 |
| P1-B3（前轮） | agent 编排层 ≥70% | agent.py 74 / agent_tools.py 74 |

### 2.4 关键覆盖明细（R3 实测）

| 文件 | 覆盖 | 文件 | 覆盖 |
|---|---:|---|---:|
| rag/reranker | 100% | agentic/state | 95% |
| rag/processor | 94% | agentic/citation | 94% |
| rag/md5_store | 92% | agentic/answer_generator | 90% |
| rag/retrieval_service | 83% | agentic/planner | 90% |
| agentic/tools | 88% | agentic/retrieval_grader | 80% |
| agentic/graph | 71% | agentic/guardrails | 79% |

### 2.5 跳过（6）

Windows 上 `python-magic` 可能 segfault，以下 MIME 相关用例仅在非 Windows 执行（CI ubuntu 全跑）：
`test_detect_file_type_returns_string`、`test_allowed_extensions`、`test_disallowed_extension`、`test_disallowed_script`、`test_validate_file_type_passes_for_allowed`、`test_validate_file_type_fails_for_disallowed`。

---

## 3. Django 用户服务测试

### 3.1 执行命令

```bash
cd DjangoUserService
uv sync --extra dev
uv run ruff check .
JWT_SECRET_KEY=ci-test-secret DEBUG=true DJANGO_SETTINGS_MODULE=DjangoUserService.test_settings \
  uv run python manage.py test apps.user apps.file --settings=DjangoUserService.test_settings -v1
```

### 3.2 结果

```text
Found 12 test(s). Ran 12 tests in ~0.4s. OK
ruff check .: All checks passed（0 error）
```

### 3.3 覆盖

- 用户注册 / 登录 / JWT；文件相关接口（`DjangoUserService.test_settings` + SQLite 测试库）
- R3 接入 ruff：存量 49 错误清零（自动修复 29 + 手工修 DTZ005/RUF059），`ignore=["N999","BLE001","RUF012"]` 豁免 Django 惯用法

---

## 4. Frontend

### 4.1 Vitest 单测（R3-P2，16 → 61）

```bash
cd front && npm run test:unit:coverage
```

| 范围 | 结果 |
|---|---|
| 单测 | **61 passed**（8 文件） |
| 覆盖率（include store/config/services） | lines 74% / funcs 47% / stmts 68% / branches 61% |
| CI 门禁 | `npm run test:unit:coverage`，阈值 lines70/funcs40/stmts64/branches55 |

新增用例：session / qaHistory / model / user / theme 五个 Pinia store + `config/api`、`config/features`。

### 4.2 Playwright E2E（历史基线，2026-07-21）

```text
39 passed（6 workers，~50s）
```
Spec：auth-pages / protected-routes / notes-mocked / settings-theme-ui / theme / routing-smoke。
全栈 E2E（`tests/e2e-full`，需真实后端 + `E2E_FULL_STACK=true`）未纳入 CI。

---

## 5. 功能验收（R3-P4，`tests/functional`）

> CI `--ignore=tests/functional`，发布前手动执行；`real_api` 标记，缺 LLM Key 显式跳过。

```bash
uv run pytest tests/functional -q --no-header        # Part A 全自动
uv run pytest tests/functional -m real_api -q --no-header  # Part B 真实 LLM
```

### 5.1 结果：**14 PASS / 0 FAIL / 0 跳过**

| 验收项 | 用例 | 结果 |
|---|---|---|
| JWT 鉴权 | 无 token 401 / 合法 200 / 伪造 401 | PASS |
| 笔记 CRUD 与隔离 | 创建/读/改/删 + 跨用户 404 | PASS |
| 知识库索引状态机 | pending → indexed → reindex | PASS |
| SSE 事件流 | agent 流式返回 started/completed/run_id | PASS |
| 运行时配置热更新 | PUT 即时生效 → reset 恢复默认 | PASS |
| 限流 | 连打超限 → 429 | PASS |
| Prompt Injection 防护 | Guardrails 净化注入指令 | PASS |
| Agentic RAG 端到端 | run_agent 答案 + 引用（真实 LLM） | PASS |
| Agentic RAG 拒答路径 | 无关问题不强答（真实 LLM） | PASS |
| IR 评测基线 | Recall@K / MRR | PASS |

### 5.2 功能验收关键坑（已记入交接）

- 功能套件需禁用 Celery 触发与 JWT 黑名单：无 Redis 时 `.delay()` 会挂死、黑名单校验 503。
- LLM Key 在 `backend/.env`（pydantic-settings 加载），`real_api` 检测须同时查 settings 与 `os.getenv`。

---

## 6. 质量保障三轮演进

### R1（框架重构回归，2026-07-21）
- 首次全量 221 passed / 14 failed（测试未跟上重构），修复后 **235 passed / 6 skipped**。
- 接入 pytest-cov，记录覆盖率基线 **31%**（6551 语句 / 4541 未覆盖）。

### R2（质量保障第二轮，2026-08-27）
- **31% → 60%**（+29pp），520 passed / 6 skipped。
- B1 rag 数据面 8 文件脱离 0% → 80–100%；B2 utils 4 文件 → 74–95%；
  B3/B4 services/tasks/repositories/agentic 7 文件 → 60–100%。
- mypy 首批四目录 43 → 0 转门禁；pre-commit（ruff+eslint）接入；CI 全 `--locked`；
  覆盖率底线落地 `--cov-fail-under=57`；branch protection 5 必需检查。
- 登记真实问题：`listdir_allowed_type` docstring 与实现不符（R3 修复）；
  顶层恢复 langchain 栈跨文件污染（收敛为"零全局副作用"注入）；numpy 线程内首导冲突（桩规避）。

### R3（质量保障第三轮，2026-08-28，本轮）
- **60% → 88%**，943 passed / 6 skipped。
- P0-A 收尾（utcnow 清零、七小文件清零 + mask_sensitive_info 安全修复）。
- P1-B1 12 router ≥70%（鉴权负路径/越权隔离）；P1-B2 services/tasks ≥80%；
  P1-B4 杂项 ≥70%。覆盖基线递推至 **85**。
- P2 前端单测 16 → 61 + coverage 门禁进 CI。
- P3 security：8 依赖升级（pyasn1/pypdf/requests/pydantic-settings/langchain-classic/
  langchain 1.3.18/aiohttp 3.14.3/cryptography 50.0.1），pip-audit 仅剩
  starlette（fastapi 锁版本）/chromadb/ecdsa（无修复）豁免；audit 门禁化；Django ruff 接入。
- P4 功能验收：`tests/functional` 14 PASS + `docs/functional-report.md`。

---

## 7. 安全状态（pip-audit / npm audit，R3 收官）

| 工具 | 门禁 | 当前 | 豁免 |
|---|---|---|---|
| pip-audit | `--fail-level=high` | 仅 3 项豁免 | starlette（fastapi~=0.123 锁 <0.51，需连带大版本）、chromadb/ecdsa（无修复版本，持续跟踪） |
| npm audit | `--audit-level=critical` | 0 critical（9 high 均为 vite dev 依赖） | 无 |

---

## 8. 已知限制 / 待办

- [ ] 全栈 E2E（`tests/e2e-full`，需 MySQL/Redis/后端/Django 同时启动）未纳入 CI
- [ ] 真实 LLM Agent Eval runner（`evals/runners/run_eval.py`）按需在发布前跑基线
- [ ] starlette 随 fastapi 大版本升级时清除豁免；chromadb/ecdsa 跟踪官方修复
- [ ] `tests/functional` 发布前手动执行并回填 PASS/FAIL 计数

---

## 9. 相关提交（R3 收官，自 R2 后主要）

| Commit | 说明 |
|--------|------|
| `1940081` / `8f44d1f` | org_router 测试补足；R3 交接 + 方案入库 |
| `e4ac388` | P1-B1：12 个 router ≥70%（总覆盖 60%→82%） |
| `2a81243` | P1-B2：services/tasks 四文件 ≥80%（→86%） |
| `134aa8e` / `f81780f` | 新基线落盘：cov-fail-under 57→83→85 |
| `acc29f8` | P1-B4：杂项五文件 ≥70%（→88%） |
| `e1dcb42` | P2：前端单测 16→61 + coverage 门禁进 CI |
| `532b804` | DjangoUserService 接入 ruff（存量 49 错误清零） |
| `b70338c` | P3：依赖升级 5 项 + audit 门禁化 + starlette 豁免 |
| `19b2a49` | P4：功能验收套件 14 PASS + functional-report |
| `f40f9d1` | P3 遗留清零：langchain 1.3.18 / aiohttp 3.14.3 / cryptography 50.0.1 |

---

## 10. 结论

1. **三轮质量保障闭环**：后端覆盖率 31% → 60% → **88%**，测试 235 → 520 → **943**，全部常绿。
2. **门禁齐备**：后端 85% 底线、前端 coverage 阈值、pip/npm audit、Django ruff、mypy 全部接入 CI；branch protection 5 必需检查。
3. **功能真实可用**：`tests/functional` 14 PASS，含真实 LLM 的 Agentic RAG 端到端、拒答路径、IR 评测。
4. **已知豁免均有理由与跟踪**：6 个 Windows MIME 跳过（CI 执行）、3 项 pip-audit 豁免（版本锁定/无修复）。
