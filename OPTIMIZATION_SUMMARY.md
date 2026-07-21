# Notebook Optimization Summary

## 本次优化概览

本次优化重点集中在以下几个方面：

- 部署链路稳定性
- 异步任务可执行性
- 认证与请求处理一致性
- 后端默认模型初始化安全性
- 限流策略准确性
- 配置读取安全性
- 回归测试可验证性

## 优化提升明细

| 序号 | 优化项 | 具体改动 | 提升了什么 | 验证结果 |
|---|---|---|---|---|
| 1 | `Django` 容器启动链路 | 调整 `DjangoUserService/Dockerfile`，改为先复制源码再安装，并从 `WSGI` 切到 `ASGI` 启动 | 提升 Docker 构建稳定性和服务启动规范性，减少镜像构建成功但服务启动异常的风险 | `docker compose config` 通过 |
| 2 | `Celery` 异步任务编排 | 在 `docker-compose.yml` 中新增 `celery-worker` 服务 | 提升自动打标签、异步回顾等后台任务的可执行性，避免任务只入队不消费 | `docker compose config` 通过 |
| 3 | JWT 黑名单与 Redis 配置一致性 | 在 `backend/.env.docker` 中补齐 `REDIS_CACHE_URL`、`JWT_BLACKLIST_REDIS_URL` | 提升 Django 与 FastAPI 的 token 撤销判断一致性，降低认证行为不一致的风险 | 配置静态校验通过 |
| 4 | 前端认证请求处理 | 清理无效 `CSRF` 头逻辑，登录/注册请求不再误带鉴权副作用，401 跳转逻辑更准确 | 提升登录注册稳定性和前端请求一致性，减少误跳登录页问题 | `npm run build` 通过 |
| 5 | 后端默认模型初始化 | 将 `backend/app/utils/factory.py` 中默认模型改为懒加载，避免模块导入时直接创建真实模型 | 提升可测试性、可维护性和启动安全性，避免导入期副作用导致测试或服务初始化失败 | 后端测试第 2 轮全部通过 |
| 6 | 限流粒度优化 | 将限流策略从“按 IP 单桶”调整为“按路由 + token/IP 分桶” | 提升限流准确性，减少 NAT、代理、多用户共用 IP 时互相误伤的问题 | 新增并通过 `test_rate_limit.py` |
| 7 | YAML 读取安全性 | 将 `yaml.load` 改为 `yaml.safe_load` | 提升配置读取安全性，降低不安全反序列化风险 | 后端测试通过 |
| 8 | 测试覆盖补强 | 新增限流相关测试，并修复后端导入链测试的运行前提 | 提升本次优化的可验证性，后续改动更容易防回归 | `38 passed` |

## 整体收益

| 维度 | 提升效果 |
|---|---|
| 部署稳定性 | 更容易在 Docker 环境下稳定启动，服务职责更清晰 |
| 异步任务可靠性 | `Celery` 不再停留在“代码里写了但实际没跑”的状态 |
| 认证一致性 | 前后端认证行为更统一，黑名单检查链路更完整 |
| 安全性 | 配置加载和请求处理更稳，减少无效或危险逻辑 |
| 可测试性 | 后端核心模块不再因导入副作用导致测试链断掉 |
| 可维护性 | 默认模型、限流、容器启动方式更符合长期维护要求 |

## 本次验证结果

| 验证项 | 结果 |
|---|---|
| `pytest tests/test_rate_limit.py tests/test_import_smoke.py tests/test_factory_config.py` | 通过，`38 passed` |
| `npm run build` | 通过 |
| `docker compose -f docker-compose.yml config` | 通过 |

## 修复迭代记录

| 轮次 | 情况 |
|---|---|
| 第 1 轮 | 前端构建通过，后端测试暴露 `factory.py` 导入期模型初始化副作用 |
| 第 2 轮 | 将默认模型初始化改为懒加载后，后端测试全部通过 |

## 后续仍建议继续优化

| 项目 | 说明 |
|---|---|
| 数据库迁移体系 | 当前 FastAPI 侧仍建议补充 `Alembic`，替代启动期 `create_all` |
| Django 自动化测试 | `apps/user` 和 `apps/file` 仍需补齐集成测试 |
| Pydantic 配置警告 | `backend/app/config/validator.py` 仍有 `Config` 弃用 warning，可后续改为 `ConfigDict` |


## P0 稳定性改造（2026-07-09）

| 项 | 结果 |
|---|---|
| Alembic 替代 create_all | backend/alembic/ + init_db 仅连通性检查 |
| Chroma 禁止失败自动删库 | reset_chroma_db_explicit；is_degraded |
| 依赖 slim | 默认无 torch；local-embed / docs-heavy optional |
| 工程卫生 | front name=notebook-front；移除 pnpm-lock；gitignore |
| 启动 migrate | backend/Django Dockerfile 启动前迁移 |
| 测试 | 175 passed, 6 skipped |

## R2 安全与鉴权（2026-07-09）

| 项 | 结果 |
|---|---|
| JWT 契约 | docs/JWT_CONTRACT.md；decode leeway 30s；跨服务 payload 测试 |
| Token 加固 | index.html + nginx CSP；localStorage 短期保留 |
| 生产禁客户端 key | ALLOW_CLIENT_LLM_KEY；sanitize_client_llm_config |
| 基础设施 | Redis requirepass；MySQL/Redis 仅绑 127.0.0.1；启动脚本拒占位密钥 |
| GET _t 清理 | http.js 改为 Cache-Control: no-store |
| 测试 | 187 passed, 6 skipped |

## R3 主路径体验（2026-07-09）

| 项 | 结果 |
|---|---|
| 大页面拆分 | useChatWorkspace.js；ChatWorkspacePage 脚本瘦身；Knowledge/Note composable 预留 |
| RAG 参数 | chunk_size=500 / overlap=60 / k=6；chunk_by_extension |
| 用户隔离 | 仓库层 user_id 契约测试保留 |
| 回顾闭环 | create_note 立即 ensure_review_record；/review/due-count；侧栏角标 |
| Prompt 版本 | prompt.yaml versions+paths；load_prompt_with_version |
| 测试 | 197 passed, 6 skipped |

## R4 产品边界 + CI + 可观测（2026-07-09）

| 项 | 结果 |
|---|---|
| Org feature flag | VITE_FEATURE_ORG / FEATURE_ORG 默认 false；侧栏与路由隐藏；后端不挂 org/space/audit |
| CI | .github/workflows/ci.yml：backend/django/front build/compose |
| Django file 测试 | 鉴权/上传/非法扩展名/超大文件 4 测通过 |
| Request-Id | front 注入 X-Request-Id；backend 回写 + contextvar + 日志 rid= |
| 开发体验 | scripts/dev.ps1 |
| 验证 | backend 207 passed；Django 10 OK；front build OK；compose OK |
