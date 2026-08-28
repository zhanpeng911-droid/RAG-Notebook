# R3 质量保障 · 交接文档（HANDOFF）

> 生成时间：2026-08-27（本会话上下文接近极限时生成）
> 用途：供下一个会话无缝接手 R3 方案剩余工作
> 方案依据：`QUALITY_ASSURANCE_PLAN_R3.md`（已核实事实，仅 1 处修正见下）

## 一、接手时第一件事

1. 推送待推提交（当前 `main` 领先 `origin/main` 1 个提交）：
   ```bash
   git push origin main
   ```
2. 跑一次全量基线确认环境正常：
   ```bash
   cd backend && uv sync --extra dev && uv run pytest tests -q --no-header
   # 预期：593 passed / 6 skipped
   ```
3. 读 `QUALITY_ASSURANCE_PLAN_R3.md` 全文（任务清单在第二节）。

## 二、R3 方案的关键修正

方案 P0-A.1 声称"branch protection 的 force-push/deletions 实际未配置、R2 报告失实"——**该指控不成立**。实测 GitHub API：
```
force_push: false   deletions: false   （均已禁用，R2 配置正确）
```
→ P0-A.1 只做"API 复核留档"，无需 PUT，无需"修正 R2 报告"。

## 三、已完成清单（本会话交付）

| 批次 | 内容 | 提交 | 覆盖 |
|---|---|---|---|
| P0-A.2 | utcnow 弃用归零（`now(UTC).replace(tzinfo=None)` 保持 naive 语义） | c9ad86e | — |
| P0-A.3 | 七个 0% 小文件清零（sse_models/llm_cache/path_tool/config×2/failed_response×2 全 ≥96%）+ **修复 mask_sensitive_info 安全缺陷**（password/api_key 正则错位致明文泄漏） | ed15ce7 | 96–100% |
| P1-B3 | `app/agent/agent.py`（工厂/非流式/SSE 流式/异常降级） | c18fda5 | 24%→74% |
| P1-B3 | `app/agent/agent_tools.py`（ContextVar 生命周期/无身份拒绝/JWT 解析/富结果格式化） | 300ea65 | 25%→74% |
| P1-B1 | `app/router/note_router.py`（鉴权负路径+CRUD+隔离 404+stub 端点） | cd4dcdb（已推） | 0%→73% |
| P1-B1 | `app/router/org_router.py`（owner 正路径+外部成员越权 403） | 1940081（**待推**） | 0%→37% |

全量测试：**593 passed / 6 skipped** 常绿。

## 四、未完成清单（接手顺序）

按方案执行顺序，从下一步接着做：

1. **P1-B1 剩余 10 个 router**（各 ≥70%）：
   `space_router`(31%)、`agent_router`(0%)、`knowledge_router`(32%)、`knowledge_service`(26%)、`health`(0%)、`audit`(0%)、`chat`(0%)、`review`(0%)、`runtime_config_router`(0%)、`user`(0%)
   - 优先高风险：agent_router（会话隔离）、space_router、knowledge_router
2. **P1-B2**：`note_service`(35%)、`database_session_manager`(21%)、`note_vector_index`(45%)、`celery_app`(37%)
3. **P1-B4**：`core/audit`(45%)、`db/redis_config`(25%)、`db/db_config`(48%)、`core/runtime_config`(51%)、`utils/auth_utils`(58%)
4. **新基线落盘**：实测总覆盖，`--cov-fail-under`=实测−3，更新 README/附录
5. **P2 前端**：Vitest 16→~50，coverage 报表进 CI
6. **P3 security**：依赖 triage（starlette 预计连带 fastapi 大版本→豁免登记）、pip/npm audit 门禁化、Django ruff
7. **P4 功能验收**：`tests/functional/` 真实 LLM 套件 → `docs/functional-report.md`

## 五、可复用测试模式（router）

```python
# 1) 独立 app 挂 router，避免 main.py 的 lifespan/中间件
from fastapi import FastAPI
from app.router.xxx_router import xxx_router
app = FastAPI()
app.include_router(xxx_router, prefix="/api/v1")
from app.core.failed_response_register import register_exception_handlers
register_exception_handlers(app)   # 必须：否则业务异常变 500

# 2) override get_db → 内存 SQLite
app.dependency_overrides[get_db] = override_get_db

# 3) 短路限流
monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

# 4) 真实 JWT 鉴权（token 用 jose 编码 HS256 + SECRET_KEY）
from jose import jwt as jose_jwt
tok = jose_jwt.encode({"user_id": U, "user_name": "u"}, SECRET, "HS256")

# 5) httpx AsyncClient + ASGITransport
transport = httpx.ASGITransport(app=app)
async with httpx.AsyncClient(transport=transport, base_url="http://test") as c: ...
```

**注意**：pytest-asyncio 是 STRICT 模式——每个 async 测试必须显式 `@pytest.mark.asyncio`。

## 六、踩坑记录（本会话实证，避免重蹈）

1. **`import a.b as x` 绑定父包属性而非模块**：`app/tasks/__init__.py` 的 `from app.tasks.celery_app import celery_app` 把父包命名空间 `celery_app` 覆盖成 Celery 实例，导致 `import app.tasks.celery_app as m` 拿到实例（无任务属性）。**解法**：`importlib.import_module("app.tasks.celery_app")` 取真实模块。
2. **langsmith `@traceable` 在 conftest mock 下崩**（读 `langchain_core.__version__` 撞 MagicMock）：给 mock 补 `__version__` + 注册兄弟子模块假条目；后者必须用 `monkeypatch.setitem(sys.modules,...)` 作用域化，否则跨文件污染（text_spliter 等会挂）。
3. **测试文件顶层持久改 sys.modules 会跨文件污染**：restore langchain 栈后，被缓存的旧模块（如 agent_tools 在 mock 下被 agent.py 导入过）不会重载——需 `importlib.reload`。
4. **conftest 把 sse_models/llm_cache/path_tool/config 等 mock 掉**：小文件用 `importlib.spec_from_file_location` 直载（注册真实名以测覆盖率）。
5. **StructuredTool 不可直接调用**：真实 `@tool` 装饰后须 `await tool.ainvoke({...})`。
6. **FastAPI dependency override 函数不能带多余参数**：`async def fake_user_info(credentials=...)` 会被当 query 参数→422；应无参。
7. **validation_exception_handler 把 422 规范化为 400**（已注册 handler 时断言 400）。

## 七、待推送

- `1940081` org_router（网络恢复即 `git push origin main`）
- `QUALITY_ASSURANCE_PLAN_R3.md`（untracked，方案文档，建议随下一次提交入库）
- 本文件 `HANDOFF_R3.md` 建议入库

## 八、当前分支与保护

- main，5 个必需检查（Backend/Django/Frontend build/Frontend E2E/Backend type check）+ force-push/deletions 禁用
- CI `--cov-fail-under=57`（R2 基线 60%−3；R3 完成后应更新为实测−3）
