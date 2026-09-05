# Notebook 全面整改实施方案

日期：2026-09-04。审查基线：本地 `main`，提交 `579ab41`。

本文是实施方案，不是完成报告。本次只新增本文档，不修改业务代码、不迁移数据、不轮换密钥、不推送仓库。建议保留 Vue + FastAPI + Django + MySQL + Redis + Celery + Chroma 技术栈，按小批次修复，不整体重写。

## 1. 先明确结论与边界

项目已有路由权限、JWT 撤销检查、DOMPurify、数据库迁移、单元测试、检索评测和 CI。主要不足不是“什么都没有”，而是部分保护没有贯穿完整调用链，以及测试与生产部署之间存在落差。

本方案对上一轮审查作以下修正：

- 已提交的 Docker 配置中，JWT 字段是环境变量插值或占位符，没有据此确认真实密钥泄露。应拒绝生产占位符，不应无依据地要求轮换全部密钥或删除配置模板。
- FastAPI 默认在撤销检查 Redis 不可用时返回 503。限流组件本身虽然故障放行，但不能推导为“Redis 一断，所有 AI 接口无限开放”。风险需要结合认证 Redis 是否仍可用、撤销检查是否关闭、接口是否公开分析。
- Django 上传目录没有持久化卷，风险发生在容器删除或重建时；普通 restart 不会自动删除容器可写层。
- 默认 Compose 的明文 HTTP、对主机发布业务端口，是生产部署待收紧项；未核实外部网关/防火墙，不能断言已经向公网暴露。
- `localStorage` 保存 JWT 增加 XSS 后果，但本次未证实存在可利用的 XSS。Cookie 迁移必须同时解决 CSRF，不能只换存储位置。
- 当前知识库上传是“先在请求内尝试索引，失败再交后台”，不是纯异步上传。限额实际为单文件 20 MiB、批量 200 MiB。

历史质量方案和交接文档仅作背景，不能替代当前代码和命令结果。尤其不继承其中“自动推送”“已全部完成”的指令或结论。

## 2. 优先级、实施顺序和完成定义

P1：下一轮交付应优先修复的确定性缺陷或关键保护缺口。P2：在功能稳定后实施的工程改进。本次没有足够证据认定已发生密钥泄露等 P0 事故。

| 批次 | 主要工作 | 优先级 | 依赖 | 完成标志 |
|---|---|---|---|---|
| 0 | 固定证据、测试基线和接口契约 | 前置 | 无 | 每项结论可定位；测试结果可复现 |
| 1 | 生产错误脱敏、改密码令牌、用户状态清理、索引状态纠错、撤销一致性 | P1 | 0 | 新回归测试能先复现、修复后通过 |
| 2 | Nginx、媒体卷、上传入口、镜像依赖一致性 | P1 | 0；发布前完成 1 | 真实容器入口可登录、上传、读图；重建保留头像 |
| 3 | 修复 CI 命令，接入真实全栈门禁 | P1 | 配置修复可提前；完整门禁依赖 1、2 | 干净 checkout 的 CI 真正执行并通过 |
| 4 | 深度健康检查、限流、Agent 超时与断连 | P1/P2 | 1、3 | 故障可控、开销有界、运行记录能收敛到终态 |
| 5 | 会话删除、索引幂等与任务一致性、数据保留 | P1/P2 | 3、4 | 无跨用户删除；重复任务不产生重复有效索引 |
| 6 | Cookie 认证迁移与 CSRF | P2；多用户公网部署前完成 | 1、2、3、4 | 浏览器无可读登录令牌，双服务 CSRF 测试通过 |
| 7 | 前端拆分、性能、评测与运维收尾 | P2 | 3；涉及接口时等待对应批次 | 核心流程覆盖、实测性能改善、恢复演练通过 |

建议先交付批次 1—3，形成可验证的小版本；不要把 Cookie、索引重构和页面重构塞入一个提交。批次 4、5 完成后再处理认证迁移及性能优化。

每个子任务统一采用：补复现测试 → 最小修复 → 跑关联测试 → 跑批次门禁 → 更新文档 → 单独提交。若仍按用户先前偏好在 `main` 交付，也必须逐批验证；本方案本身不包含推送或修改远端分支规则的授权。

## 3. 批次 0：文档发现、允许使用的接口与基线

### 0.1 已核实的实现入口

| 范围 | 已有接口或模式 | 依据 |
|---|---|---|
| 配置 | `get_settings()`、`AppSettings.is_production`、`validate_startup_config()` | [validator.py](D:/claudecode/Notebook/backend/app/config/validator.py) |
| JWT | `JWTTokenGenerator.generate_token(user)` 返回 token 与到期时间；`blacklist_token(token)` 当前没有可靠成功契约 | [authentications.py](D:/claudecode/Notebook/DjangoUserService/apps/user/authentications.py) |
| FastAPI 认证 | `decode_django_jwt(token)`、`_reject_if_blacklisted(payload)`、`get_current_user_id(...)` | [auth_utils.py](D:/claudecode/Notebook/backend/app/utils/auth_utils.py) |
| 前端清理 | `clearAuth()`、`clearSessions()`、问答历史 `clear()` | [user.js](D:/claudecode/Notebook/front/src/store/user.js)、[session.js](D:/claudecode/Notebook/front/src/store/session.js)、[qaHistory.js](D:/claudecode/Notebook/front/src/store/qaHistory.js) |
| 索引 | `save_uploaded_file(...)`、`_sync_index(document_id, user_id)`、`reindex_document(...)` | [document_index_service.py](D:/claudecode/Notebook/backend/app/services/document_index_service.py) |
| Agent 记录 | `create_run(...)`、`update_run(...)`、`get_run(run_id, user_id)` | [agent_run_repository.py](D:/claudecode/Notebook/backend/app/repositories/agent_run_repository.py) |
| 测试 | `httpx.ASGITransport`、`app.dependency_overrides`、真实内存 SQLite 会话；Vitest 新 Pinia + mock HTTP | [后端路由测试](D:/claudecode/Notebook/backend/tests/test_router_runtime_config.py)、[前端认证测试](D:/claudecode/Notebook/front/tests/unit/store/user.spec.js) |

本文出现的 `applyAuthSession`、索引 claim、数据清理任务等新名称都是拟新增设计，不是声称仓库已有的 API。实施时先读上述现有实现与对应测试，再复制其中已经验证的模式；不要照搬历史方案中不存在的函数。

外部 API 使用边界：

- 配置沿用 `BaseSettings` + `SettingsConfigDict`；测试可显式禁用 env 文件加载，避免依赖开发机私有配置。参考 [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)。
- 数据库 URL 使用 `sqlalchemy.URL.create(drivername, username, password, host, port, database, query)`；password 传原值，不先自行 URL 编码。参考 [SQLAlchemy URL.create](https://docs.sqlalchemy.org/en/20/core/engines.html#sqlalchemy.engine.URL.create)。
- 超时采用 Python 3.12 已有的 `asyncio.timeout()` / `timeout_at()`；取消清理后继续传播 `CancelledError`。线程中的同步调用另设客户端超时，不能把协程取消等同于线程终止。参考 [Python asyncio](https://docs.python.org/3.12/library/asyncio-task.html#timeouts)。
- Nginx 使用已支持的 `location ^~`、`alias`、`client_max_body_size`；不假设当前镜像支持较新的头部继承指令。参考 [Nginx location](https://nginx.org/en/docs/http/ngx_http_core_module.html#location)。
- Playwright 沿用 `defineConfig`、`testDir`、`use.baseURL`、`webServer`。参考 [配置](https://playwright.dev/docs/test-configuration)与[服务启动配置](https://playwright.dev/docs/test-webserver)。

### 0.2 基线采集与证据保存

实施开始前保存 commit、运行时版本、锁文件版本、命令、退出码、测试总数、跳过原因、覆盖率和构建产物大小。不要只保存覆盖率百分比而丢失 pytest 最终结果。

上轮检查中前端单测与构建通过、Django 测试通过；后端取得约 87% 覆盖率报告，但最终 pytest 汇总未完整保留，因此不能据此宣称当前全部后端测试通过。Docker daemon 当时不可用，`compose config` 成功也不等于镜像和 Nginx 运行通过。依赖审计没有取得新的完整联网结果，不能标记“依赖安全”。以上全部作为需要重跑的基线，不作为本次验收完成证明。

仓库已有三个未跟踪的 IR 评测报告，保留原样，不纳入清理或覆盖范围。所有集成验证使用单独数据库、测试账号和目录，不使用真实用户数据。

## 4. 批次 1：先修确定性缺陷

### 1A. 统一生产环境判定和错误输出

证据：[failed_response.py:32](D:/claudecode/Notebook/backend/app/core/failed_response.py:32) 另建 Settings，只识别精确 `prod`；Docker 使用 `production`，而中央配置已经支持两者。

具体修改：

1. 在中央 `AppSettings` 纳入异常处理器需要的 `LOG_LEVEL`，把环境值统一 `strip().lower()`；生产环境强制禁止调试响应，即使配置了 `DEBUG_MODE=true`。
2. 删除异常处理模块中的重复 Settings，统一调用中央配置。保留对外错误码、HTTP 状态和友好消息，不顺手重定义全部业务响应格式。
3. 生产响应不含 `traceback`、`raw_errors`、SQL、路径或原始异常文本。服务端记录脱敏详情和现有 `request_id`，不要通过客户端响应泄露详情。
4. 同步检查配置加载异常：错误只列字段名和缺失/非法原因，不打印含输入值的完整配置异常。
5. JWT 在生产环境拒绝空值、已知占位符和明显过短值，提供随机生成至少 32 字节秘密的说明；长度校验不等同于证明随机性。两服务使用同一来源，不能各自生成。开发和测试配置明确标识，不被误当生产。
6. MySQL 连接串改为字段构造 URL，串联检查引擎与 Alembic 的消费者；若迁移工具要求字符串，显式渲染并处理其配置插值，不能直接 `str(URL)` 导致密码被掩码。补密码含 `@:/%#` 的连接配置测试，不输出密码。

测试：扩展 [test_failed_response.py](D:/claudecode/Notebook/backend/tests/test_failed_response.py) 和配置相关测试，覆盖 `prod / production / PRODUCTION / dev` × debug 开关、缺少密钥、占位符、开发错误输出。必须实际加载环境配置，不能只 monkeypatch 最终 `DEBUG_MODE` 常量而漏掉本次根因。

验收：生产所有异常分支均无调试信息；双服务有效 JWT 互认；本地开发仍可诊断。回滚只回滚引入兼容问题的代码，不恢复生产详细错误输出。

### 1B. 修复改密码后的认证状态，并统一用户数据清理

证据：[user.js:166](D:/claudecode/Notebook/front/src/store/user.js:166) 不接收改密码响应里的新 token；[Django views.py:168](D:/claudecode/Notebook/DjangoUserService/apps/user/views.py:168) 已撤销旧 token。

具体修改：

1. 先复制 `updateUserInfo()` 已有的新 token 更新方式，修复 `updatePassword()`；再提取拟新增的 `applyAuthSession`，让登录、注册、刷新、资料修改、密码修改统一更新 token、用户信息和登录状态。
2. 扩展 `clearAuth()` 或建立一个无循环依赖的统一清理入口。登出、401、切换账号调用同一逻辑，清理会话列表、问答历史、用户图片缓存及用户级状态。
3. 在清理之前取消当前 SSE/fetch、草稿写入定时器；清理后旧请求返回也不能把旧用户数据写回 store。使用用户身份版本或请求代次检查丢弃迟到结果。
4. [NoteEditorPage.vue:367](D:/claudecode/Notebook/front/src/pages/notes/NoteEditorPage.vue:367) 的 `note_draft` 改为带用户 ID 与 note ID 的键；草稿内容也保存所有者，恢复时再次比对。旧无所有者草稿不得自动归属新登录用户。
5. 不调用 `localStorage.clear()`，保留主题等非敏感偏好。旧草稿提供当前用户主动导出/丢弃的过渡策略；登出清理时不能默默恢复给下一位用户。

测试：扩展 [user.spec.js](D:/claudecode/Notebook/front/tests/unit/store/user.spec.js)、[http.spec.js](D:/claudecode/Notebook/front/tests/unit/services/http.spec.js)，补“改密码→立即获取资料/保存笔记”；浏览器补“账号 A 新建草稿和问答→退出→账号 B 登录”。验证旧定时器和迟到请求不重新污染状态。

验收：旧 token 在两服务失效，新 token 正常；B 看不到 A 的草稿和问答。不能只断言改密码返回 `success=true`。

### 1C. 修复上传结果与数据库索引状态不一致

证据：[document_index_service.py:244](D:/claudecode/Notebook/backend/app/services/document_index_service.py:244) 在 `_sync_index()` 正常返回后直接报告 `indexed`；但解析/切片为空的分支写 `index_failed` 后正常 return。异常路径还可能对外返回 `pending_index`，实际数据库仍为失败。

具体修改：

1. 让索引执行返回明确的结果对象，或完成后通过新会话读取持久化状态；禁止把“没有抛异常”当作“索引成功”。结果至少区分 `indexed / pending_index / index_failed`。
2. “文件保存成功”和“可参与检索”分开表达。保持现有响应 envelope，`status` 必须与已提交的数据库状态一致；待重试可以单独返回调度状态，但不能捏造成功入队。
3. 解析为空、切片为空、Embedding 异常、向量写入失败、Celery 不可用分别处理；对外返回稳定的错误标识和安全文案，原始 provider 错误只进脱敏日志。
4. [KnowledgeBasePage.vue](D:/claudecode/Notebook/front/src/pages/knowledge/KnowledgeBasePage.vue) 展示“文件已保存/正在索引/索引失败/可检索”，不要让前端上传进度 100% 自动等于 indexed。

测试：沿用 [test_document_index_service.py](D:/claudecode/Notebook/backend/tests/test_document_index_service.py)、[test_index_task.py](D:/claudecode/Notebook/backend/tests/test_index_task.py)，为每条失败分支断言响应、DB 和界面三者一致。先只纠正契约，纯异步化放到批次 5。

### 1D. 让撤销失败成为可识别失败

证据：[Django settings.py:125](D:/claudecode/Notebook/DjangoUserService/DjangoUserService/settings.py:125) 在启动 Redis 失败后永久切换 LocMem；[blacklist_token](D:/claudecode/Notebook/DjangoUserService/apps/user/authentications.py:171) 吞掉撤销失败，两服务可能不共享撤销结果。

具体修改：

1. 生产使用固定共享 Redis 后端，禁止自动替换为进程内缓存；连接失败时认证相关请求失败关闭并返回明确 503，readiness 标记不可用。开发降级须显式配置。
2. `blacklist_token()` 定义成功/失败契约：基础设施失败抛可识别异常；注销、刷新、改密码不得伪报已撤销。区分“本机状态已清除”和“服务端撤销已确认”。
3. 统一键前缀、TTL、claims 要求。保留过渡期对两种既有黑名单键的读取，待兼容窗口结束再收敛。明确生产新签发 token 必须有 `exp` 和 `jti`。
4. 逐个写清密码保存、旧 token 撤销、新 token 返回的顺序及失败补偿；不能把数据库和 Redis 当作一个原子事务。密码修改结果不确定时引导重新登录并告警，不返回误导性的成功会话。

测试：复用 [Django 用户测试](D:/claudecode/Notebook/DjangoUserService/apps/user/tests.py)、[FastAPI 撤销测试](D:/claudecode/Notebook/backend/tests/test_auth_utils.py)，再增加两个服务共用真实测试 Redis 的契约用例：登录、注销、刷新、改密码、Redis 启动故障、运行故障、恢复。更新 [JWT_CONTRACT.md](D:/claudecode/Notebook/docs/JWT_CONTRACT.md)，修正其指向当前不存在测试文件的链接。

验收：任何一边已确认撤销的旧 token，在另一边也拒绝；撤销不可用时不返回“全端退出成功”。回滚不允许重新启用生产 LocMem 降级。

## 5. 批次 2：让 Docker 路径真正可用

### 2A. 修正代理优先级、上传限额和响应头

修改 [front/nginx.conf](D:/claudecode/Notebook/front/nginx.conf)：

1. 将 `/api/v1/` 改为 `location ^~ /api/v1/`。保留 `proxy_pass http://backend:8000;` 的无 URI 形式，使前缀不被剥离；保留现有 SSE HTTP/1.1、关闭缓冲和超时配置。否则静态扩展名正则会截获私有图片接口。
2. 给知识库上传路径设置有限的请求总大小。建议批量请求上限 210 MiB 为现有 200 MiB 文件总量留 multipart 余量；应用仍执行单文件 20 MiB、总量 200 MiB 和合理文件数量限制。头像请求可给 2 MiB 请求上限，应用仍限制图片 1 MiB。实际余量通过边界测试确认，不全局设无限制。
3. 新增更具体 location 时，完整复用必要代理头和超时，防止只配大小却把请求发到错误 upstream。
4. 静态 location 中的 `add_header Cache-Control` 会改变 server 级安全头的继承。移除多余局部头、使用 `expires`，或抽公共 include 在需要处显式引用；验证成功及错误响应都有预期安全头，不盲用新版本专属指令。
5. 盘点旧代理路径，只有证明无调用方才移除。SPA `/chat/:sessionId` 的刷新路由不能被当成旧 API 转发。

官方依据：[location](https://nginx.org/en/docs/http/ngx_http_core_module.html#location)、[请求大小](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size)、[响应头继承](https://nginx.org/en/docs/http/ngx_http_headers_module.html#add_header)。

验收：通过 Nginx 请求 `/api/v1/health/live`、普通 JSON、带 `.png` 后缀的鉴权图片、SSE、SPA 深链接；匿名/越权图片请求必须被后端拒绝，授权返回正确图像，不是 HTML 或代理静态 404。测试 20 MiB 文档与超限、200 MiB 批次与超限、接近 1 MiB 头像。

### 2B. 头像持久化与生产访问

修改 [docker-compose.yml](D:/claudecode/Notebook/docker-compose.yml)，新增 `django_media` named volume：Django 挂载 `/app/media` 可写，Nginx 挂载 `/srv/notebook-media` 只读。在 Nginx 新增：

```nginx
location ^~ /media/ {
    alias /srv/notebook-media/;
    autoindex off;
}
```

仅此目录承载现有头像，不映射知识库私有文件。继续复用 [ImgSerializer](D:/claudecode/Notebook/DjangoUserService/apps/file/serializers.py) 的图片内容/后缀/大小校验；文件缺失返回 404，禁止目录浏览，响应包含正确 Content-Type 和 `nosniff`。该设计按现有 URL 语义把头像视为公开资源；如产品要求头像也必须鉴权，改走鉴权下载接口，不能使用公开 alias。

数据迁移必须按顺序执行：确认旧容器和媒体路径 → 暂停头像写入 → 导出旧媒体并记录数量/校验值 → 初始化卷并导入 → 验证读写权限 → 挂载卷重建服务 → 验证旧头像及新上传 → 保留备份直至验收。先挂空卷会遮住原目录，先删旧容器可能造成数据丢失，因此这两步不能提前。

参考 [Docker volumes](https://docs.docker.com/engine/storage/volumes/)、[Nginx alias](https://nginx.org/en/docs/http/ngx_http_core_module.html#alias)、[Django 媒体服务边界](https://docs.djangoproject.com/en/5.2/howto/static-files/#serving-files-uploaded-by-a-user-during-development)。

验收：旧头像可读、新头像可上传、重建 Django 后仍可读、Nginx 挂载只读。回滚镜像时继续保留同一卷；若新布局不兼容，使用已验证备份恢复到指定媒体目录，不删除卷。

### 2C. 镜像和生产配置可复现

1. [backend/Dockerfile](D:/claudecode/Notebook/backend/Dockerfile)、[Django Dockerfile](D:/claudecode/Notebook/DjangoUserService/Dockerfile) 当前 `pip install .` 没有按 `uv.lock` 锁定，可能与 CI 安装不同版本。按 [uv 官方 Docker 模式](https://docs.astral.sh/uv/guides/integration/docker/) 固定 uv 版本，复制 lock，使用 locked 安装生产依赖；按各项目实际 `dev` extra/打包配置决定参数。不要假设 `--no-dev` 能排除所有可选 extra。
2. 保留前端已经完成的 Node 22、npm lock、`npm ci` 修复；删除 Compose 中已不被 Dockerfile 消费的前端 build args，并说明生产请求地址由 Nginx 决定。
3. 整理开发与生产 Compose 配置：生产只发布统一 HTTPS 入口，后端/Django 留在容器网络；本地调试端口绑定回环地址。部署前确认实际 TLS 终止点、可信代理和 Django host/CORS 配置。
4. 对齐根模板与服务 env 模板的变量名和优先级，确保用户修改功能开关真的生效。保留模板，只移除危险默认回退。运行配置输出不得含真实秘密。
5. 在一次性迁移步骤中执行 Alembic/Django migrations，成功后再启动服务及 worker；多实例不得并发争抢迁移。检查新旧 schema 与镜像的兼容区间。

验收：同一 commit 的镜像依赖版本与 lock 对应；冷启动可用；缺必要生产配置明确失败；业务端口不会意外绑定公网。Docker daemon 不可用时本批次标记待运行验证，不能用静态配置检查代替。

## 6. 批次 3：修好质量门禁，再增加门禁

### 3A. 修复现有 CI 自身的问题

修改 [ci.yml](D:/claudecode/Notebook/.github/workflows/ci.yml)：

1. `compose` job 显式注入独立的非生产测试 JWT 以及所需测试配置。Django job 的环境变量不会跨 job 自动传播。已复现干净环境缺 JWT 时 `compose config --quiet` 失败；不能通过取消生产必填校验修 CI。
2. 当前锁定 `pip-audit 2.10.1`；本地 `--help` 与实际执行确认不存在 `--fail-level=high`，命令直接 exit 2。删除该参数；多个豁免 ID 必须逐个使用 `--ignore-vuln ID`。
3. 推荐先采用工具原生策略：扫描到未获批准的漏洞就失败，已有豁免逐条记录理由、影响、负责人和复查日期。若坚持按严重性过滤，必须新增独立、经过测试的策略层，并处理严重性未知，不编造工具参数。依据 [pip-audit 官方 CLI](https://github.com/pypa/pip-audit#usage)。
4. 对 Django 独立依赖环境也做审计；保存审计 JSON、执行退出码和网络失败状态。网络失败不等于零漏洞，禁止 `|| true` 让整个门禁失效。更新历史方案里错误的审计命令说明。
5. 已有 backend 覆盖率 85% 门槛保持；不要为新增测试失败或覆盖范围扩大而默默下调。前端逐步扩大范围时可按模块设基线，不能让高覆盖的简单 store 掩盖未测页面逻辑。

### 3B. 让全栈测试覆盖真实生产入口

修改 [全栈 spec](D:/claudecode/Notebook/front/tests/e2e-full/auth-and-notes-fullstack.spec.js)、[Playwright 配置](D:/claudecode/Notebook/front/playwright.config.js)、[package.json](D:/claudecode/Notebook/front/package.json)：

1. 修正 `/health/live`、`/note/list`、`/note/{id}` 为对应 `/api/v1/...`；统一服务端口默认值及环境覆盖，不能混用 8000 和 8002。
2. 新增 `D:/claudecode/Notebook/front/playwright.full.config.js`，复用现有 `defineConfig` 模式，明确全栈 `testDir`。脚本改为 `playwright test --config=playwright.full.config.js`，消除 POSIX 环境变量赋值导致的 Windows npm 不兼容。
3. 全栈配置支持 `E2E_BASE_URL`。指向已启动 Docker Nginx 时不启动 Vite `webServer`；测试创建、读取、删除均经过该入口。
4. CI 新增独立 Docker 集成 job，启动 MySQL、Redis、Django、FastAPI、worker、Nginx，使用临时账号、隔离存储。当前固定容器名和 bind mount 要为 CI 参数化，确保不碰开发数据。
5. LLM/Embedding 仅在外部依赖边界使用确定性测试服务。认证、数据库、索引状态、worker 和代理不整体 mock；测试替身不得作为生产默认 provider 开启。
6. CI 缺少测试账号/依赖应失败，不能整套 skip 仍显示通过。清理测试数据失败需记录并使相应验证失败，不能吞掉异常制造干净结果。

首批真实流程：登录 → 笔记新增/编辑/删除；改密码 → 继续请求；头像上传 → 重建保留；文档上传 → 索引状态收敛；用户隔离图片下载；Agent SSE 正常完成与取消；登出旧 token 双服务拒绝。

验收：全新 checkout 能运行；失败有脱敏日志、Playwright trace 和测试摘要；报告区分 mocked UI、真实集成和真实模型测试。远端必需检查配置如需更改，另行获得授权，不在本文编造已配置状态。

## 7. 批次 4：控制外部开销、超时与故障

### 4A. 分离便宜探针和深度诊断

修改 [health.py](D:/claudecode/Notebook/backend/app/router/health.py)。保留无认证、最小响应的 `/live` 与 `/ready`，后者给 DB/Redis 检查设置整体超时，不能因内部重试拖过探针时限。

`/model` 当前每次公开请求都会触发 embedding 调用；`/vector-store` 会访问集合并返回内部错误原因。把深度检查限制为运维身份/内部入口，复用已有管理员 ID 配置及权限模式；增加短缓存、并发合并、调用频率限制和 provider 客户端超时。对外只返回组件状态与请求 ID，不返回原始异常。不要让 Docker readiness 调用收费模型。

验收：匿名不能执行深度探测；短时间重复探测只有有限外部调用；依赖挂起时仍在规定探针时限内返回 503；异常中人为植入秘密串也不会出现在响应。

### 4B. 限流按已验证身份，故障策略按接口成本

修改 [rate_limit.py](D:/claudecode/Notebook/backend/app/core/rate_limit.py) 及调用它的高成本路由。

1. 认证请求按已验证 `user_id + 路由模板/业务操作` 分桶，不按完整 token 或实际资源路径。刷新 token、换 note ID 不应重新获得一份同类操作额度。
2. 匿名只使用经过可信代理配置确认的客户端 IP，不能直接相信任意 `X-Forwarded-For`。认证依赖必须先于身份限流，禁止只解码未验签 payload。
3. 高成本生成、重索引在限流存储不可用时默认 503；便宜读取可按明示策略降级并记录指标。保留 FastAPI 撤销检查失败关闭，不用关闭它来证明限流“可用”。
4. 计数与 TTL 合并为原子操作，参考 Redis 官方固定窗口 Lua 模式；已有 TTL 自修复不能覆盖进程在两条命令之间中断的所有情况。返回 429 时附合理 `Retry-After`。参考 [Redis INCR 限流示例](https://redis.io/docs/latest/commands/incr/)。
5. 增加用户级生成并发上限和请求总预算；限频、并发和 token 成本分别度量。先记录实际用量，再确定业务额度，不把任意数字当成用户已认可配额。

测试：同一用户两个 token 同桶；不同用户隔离；动态路径同操作同桶；伪造转发头无效；并发请求不超额；限流 Redis 单独故障、认证 Redis 单独故障分别验证。

### 4C. Agent 统一 deadline、取消和终态

修改 [graph.py](D:/claudecode/Notebook/backend/app/agentic/graph.py)、[guardrails.py](D:/claudecode/Notebook/backend/app/agentic/guardrails.py)、[answer_generator.py](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py)、[agent_router.py](D:/claudecode/Notebook/backend/app/router/agent_router.py) 与记录仓库。

1. 现有 45 秒仅在部分轮次入口检查，超时后仍可能进入答案生成，不能算总时限。改为从运行开始传递统一 monotonic deadline，检索、改写、生成使用剩余预算，而非每阶段重新获得完整预算。
2. 对完整异步运行设置超时作用域；外部 HTTP/模型配置连接和读取超时。同步线程调用不会因为外层 await 取消就可靠停止，因此限制线程/调用并发，必要时使用可终止的任务进程，不能无限遗留后台调用。
3. SSE 区分正常完成、业务错误、超时、客户端取消。拟新增 `cancelled` 状态；已有 `TIMEOUT` 要正确落库。`completed_at` 对所有终态赋值，不只 completed/failed。生成器当前把超时转换为普通“抱歉”答案，应改为结构化失败，不能再记为 completed；非必要 judge 在剩余预算不足时跳过或独立后处理。
4. 客户端取消后停止后续检索和生成；短暂、受限地保存终态，然后传播取消。当前路由已经使用独立短数据库会话，应保留这一正确模式；缺的是外围取消/异常收尾，不是重新解决一个不存在的“全程占用初始化事务”问题。
5. 进程崩溃无法依赖 finally：为长时间未更新的运行增加心跳/超时回收，按 lease/更新时间判断，不误伤仍活跃任务；终态更新采用条件写防止覆盖已完成结果。

测试：慢检索、慢生成、provider 不返回、断流、取消、数据库终态写失败；正常流只能有一次终结事件。复用 [test_agent_sse_trace.py](D:/claudecode/Notebook/backend/tests/test_agent_sse_trace.py)、[test_agentic_rag_regressions.py](D:/claudecode/Notebook/backend/tests/test_agentic_rag_regressions.py)，补真实 Nginx 断连验证。达到截止时间后不能再启动新一轮模型请求。

## 8. 批次 5：数据生命周期与索引一致性

### 5A. 删除会话同时处理关联运行数据

证据：[clear_session()](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:141) 删除 ChatSession；[Agent 模型](D:/claudecode/Notebook/backend/app/models/agent_run.py) 的 session/run 关联只是普通字段，未形成对应级联删除。

具体修改：

1. 在应用服务中先验证会话属于用户，再在同一 SQL 事务内处理其 AgentFeedback、AgentStep、AgentRun、ChatMessage、ChatSession。优先显式删除确保范围可审计，再评估外键迁移。
2. 对运行中会话先记录删除意图并取消关联任务，防止请求结束后重新写出“被删除”的数据。删除与终态写入用条件更新/父记录检查形成明确竞态规则。
3. 新增关联索引或外键前先扫描历史孤儿数据，输出 dry-run 报告；不直接执行历史清理。反馈/步骤到 run 的外键较直接，会话关联需考虑可为空与历史数据，不能生硬套用 cascade。
4. 增加可配置保留策略：运行摘要、原始问答/步骤、日志、审计分开处理。默认先只生成清理预览；生产删除周期和历史清理范围由数据负责人确认后启用。审计日志不随普通会话删除无条件清空。
5. 会话列表与历史消息增加有上限分页，稳定排序加 ID 游标；保留兼容窗口，前端改为加载更多，不一次 `.all()` 拉完。

测试：用户 A 删除自身会话，四类关联记录均按策略处理；A 删除 B 的会话被拒绝；删除中途失败全部回滚；运行与删除并发不复活；历史孤儿检查不修改数据。迁移在测试库完成 upgrade/兼容回退验证。

### 5B. 让索引任务可重复执行并可恢复

修改 [document_index_service.py](D:/claudecode/Notebook/backend/app/services/document_index_service.py)、[index_task.py](D:/claudecode/Notebook/backend/app/tasks/index_task.py)、[document_index_repository.py](D:/claudecode/Notebook/backend/app/repositories/document_index_repository.py)、[document_index.py](D:/claudecode/Notebook/backend/app/models/document_index.py)。

1. 先收敛两套索引路径为同一执行服务，HTTP 和 Celery 只是触发入口；保留批次 1C 的真实状态结果契约。
2. 上传改为有界分块读取、校验、落盘和建记录，不先把所有文件读进内存。生产默认后台索引；上传响应代表保存/排队，不代表 indexed。可保留明确配置的开发同步模式，但必须走同一执行逻辑。
3. 数据库状态是事实来源。设计带 owner/lease/version 的原子 claim：只有成功抢占者进入 INDEXING，失败或已完成的重复投递安全退出。现有 Beat 可继续补偿，但不能无限重复投递同一正在执行的文档。
4. 向量写入采用稳定的 chunk ID，包含所有者、文档 ID、索引版本和 chunk 序号；不要用随机 ID 反复追加。先检查锁定版 Chroma/LangChain 写入 API 与现有删除模式，验证确切支持方式再实现。
5. MySQL 与 Chroma 不是同一事务：先写新版本，验证完整，再切换有效版本，最后清理旧版本；读取必须过滤有效版本。如果暂时不做版本化，要显式采用受锁保护的重建方案并说明重建窗口，不宣称零中断。
6. 给失败分类：内容为空/格式错误等通常不可自动重试；网络限流等可有限重试。维护 attempt、错误码、下次重试时间，重试耗尽可人工触发，不伪报成功。
7. 删除与重建共用任务租约/删除标记，防止已删除文件重新写回向量库。当前向量删除失败后仍会删除 SQL 记录，需改为保留 `deleting`/清理任务与重试依据，完成清理才宣告完全删除；读取过滤待删除资源。提供按文档对账工具，检查 DB 状态、文件和有效 chunk 数量，不全量无条件重建向量库。
8. 修正物理删除的路径判断：当前 `real_file.startswith(real_user_dir)` 不是可靠的目录边界判断。使用规范化真实路径加 `os.path.commonpath` 等路径级比较，处理不同盘符异常、目录本身、同前缀兄弟目录和符号链接；确认目标在该用户目录内且为允许删除的文件后才执行。复用现有路径回归测试并新增这些边界，不仅搜索源码字符串。

测试：相同任务执行两次有效 chunk 数不变；两个 worker 同时领取只执行一次；向量写成功但 DB 提交失败可恢复；worker 中断租约到期后可接手；删除/重建竞态不复活。MySQL claim 与真实 Chroma 语义至少各有一个集成测试，不能只用 SQLite 和 mock 证明并发安全。

上线与回滚：先加兼容字段/读取逻辑，再启用新写入；小范围重建验证后扩大。不自动清空旧 collection。回退期间保留仍能读取旧数据的代码和原文件，历史版本清理由独立受控任务执行。

## 9. 批次 6：独立推进 Cookie 认证迁移

这一批是安全加固，不是当前改密码 bug 的前提。先完成统一认证状态、真实全栈测试和 HTTPS 入口，再切换。

### 6A. 先确定双服务共同契约

拟新增 `D:/claudecode/Notebook/docs/adr/0001-browser-session.md`，写清以下设计：

- 浏览器使用同源 HTTPS、HttpOnly/Secure、`SameSite=Lax` 的 host-only 登录 Cookie，Path 覆盖 `/user/` 与 `/api/v1/`。删除 Cookie 的名称、Path/Domain 必须与签发一致；本地 HTTP 开发使用显式独立配置。
- 不把 Cookie 迁移和 refresh-token 数据模型重做混在一起；第一步保持现有 token 生命周期与撤销语义，通过 Cookie 传输。
- CSRF 推荐采用经过审核的、与会话绑定的签名双提交方案，并校验可信 Origin；token 获取、旋转、过期和校验在两服务使用同一契约/测试向量。若选择 Django 内建 CSRF 方案，必须明确 FastAPI 的等价验证边界，不能只在 Django 加中间件。
- 登录前也要防登录 CSRF，登录后旋转 CSRF 状态；写操作以及刷新/退出均验证，缺失或错误返回 403。CSRF token 可以让前端读取，认证 JWT 不可以。
- Bearer 兼容窗口按版本/日期明确限定；同时携带两种不一致身份凭据时拒绝，不能任选其一。Cookie 到 localStorage 不设自动回退。

参考 [Django Cookie API](https://docs.djangoproject.com/en/5.2/ref/request-response/#django.http.HttpResponse.set_cookie)、[Django CSRF](https://docs.djangoproject.com/en/5.2/howto/csrf/)、[DRF 认证边界](https://www.django-rest-framework.org/api-guide/authentication/#sessionauthentication)、[OWASP 签名双提交建议](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#signed-double-submit-cookie-recommended)。不能自行发明加密算法或把 SameSite 当作全部 CSRF 防护。

### 6B. 改动顺序

1. Django 登录/刷新/资料更新/改密码统一签发 Cookie，退出删除 Cookie并撤销 token；Cookie 模式响应不再把 JWT 返回给浏览器 JS。
2. FastAPI 在统一认证入口提取 Cookie 或兼容 Bearer，并继续执行签名、过期、用户身份和撤销检查。CSRF 验证必须在 Cookie 写请求执行前生效。
3. 前端 [http.js](D:/claudecode/Notebook/front/src/services/http.js)、[sseClient.js](D:/claudecode/Notebook/front/src/services/sseClient.js)、上传 fetch、[useAuthImage.js](D:/claudecode/Notebook/front/src/composables/useAuthImage.js)、session/model 调用统一凭据策略，覆盖绕过 Axios 的所有路径。
4. [user store](D:/claudecode/Notebook/front/src/store/user.js) 不再持久化 token；[路由守卫](D:/claudecode/Notebook/front/src/router/index.js) 不再检查本地 token 是否存在，启动时通过已有 `/user/detail/` 恢复会话，区分“加载中/已登录/未登录/服务不可用”。
5. 清除旧 `jwt_token` 和 Pinia 持久化中的 token 字段。部署切换期间记录兼容 Bearer 使用量，结束窗口后关闭浏览器旧路径；通知用户可能需要重新登录。

验收：localStorage/sessionStorage 及持久化 store 中没有认证 JWT；刷新页面能恢复登录；登录、上传、私有图片、SSE、改密码、退出全部工作；跨站写请求被两服务拒绝；合法同源请求成功；401 与认证服务503界面语义不同。Django 使用强制 CSRF 校验的测试客户端，不依赖测试默认豁免。

回滚：保留已带 CSRF 保护的 Cookie 后端，必要时回退到上一版兼容前端；不能为“恢复登录”重新把 Cookie 中令牌暴露给 JS。必须在灰度前演练新旧前端与双服务版本组合。

## 10. 批次 7：可维护性、性能、评测和运维

### 7A. 前端按职责拆分，不仅搬走代码

[KnowledgeBasePage.vue](D:/claudecode/Notebook/front/src/pages/knowledge/KnowledgeBasePage.vue)、[NoteEditorPage.vue](D:/claudecode/Notebook/front/src/pages/notes/NoteEditorPage.vue)、[ChatWorkspacePage.vue](D:/claudecode/Notebook/front/src/pages/chat/ChatWorkspacePage.vue) 体积较大。已有 [useKnowledgeBase.js](D:/claudecode/Notebook/front/src/composables/useKnowledgeBase.js)、[useNoteEditor.js](D:/claudecode/Notebook/front/src/composables/useNoteEditor.js) 不代表页面已真正接入；必须先查引用，避免形成两套状态逻辑。

建议拆分边界：

| 页面 | 子组件 | composable / 服务职责 |
|---|---|---|
| 知识库 | 上传区、文档表、详情抽屉、切片列表 | 上传队列、索引轮询、空间过滤、详情加载与取消 |
| 编辑器 | 编辑主体、元信息栏、关联内容栏 | 草稿隔离、保存状态机、离开确认、自动补全取消 |
| 聊天 | 消息列表、输入栏、引用面板、运行阶段 | 会话生命周期、SSE事件归并、渲染、安全错误处理 |

每次只迁移一块，页面负责组装，子组件通过 props/events 交互，composable 持有业务状态，服务封装请求。优先处理重复逻辑和副作用，不以“必须少于某个行数”为唯一目标。

补 [vitest.config.js](D:/claudecode/Notebook/front/vitest.config.js) 对 composables、路由与关键组件的覆盖。测试重点为状态转换与失败恢复：上传413/401/503区分、保存失败保留草稿、取消不误报错误、切换文档时旧结果不覆盖新页面。不要只测试薄 API 包装函数被调用一次。

继续保留 DOMPurify；对 Markdown/XSS 载荷加回归测试，不为消除 `v-html` 警告直接关闭安全规则。补核心表单标签、键盘操作、弹窗焦点返回、加载/空/错误态和移动端关键流程，范围以现有页面为准，不顺便全面换 UI 风格。

### 7B. 性能先测量再优化

1. 固定数据量、机器和网络条件，测首页 JS 传输、聊天/编辑器首次打开、笔记保存、文档上传、索引耗时和 Agent 首个事件/总耗时；记录 p50/p95 及样本量。
2. 前端已有路由懒加载，不重复“增加懒加载”口号。重点查 [MarkdownEditor.vue](D:/claudecode/Notebook/front/src/components/MarkdownEditor.vue) 的 Mermaid/math/highlight 静态导入，按实际使用延迟装载，限制语法高亮语言；确认 ByteMD 插件初始化契约后再调整。
3. 聊天长消息列表按实测需要分页/虚拟化，流式渲染做节流，避免每个小片段全量 Markdown 重渲染；必须保持引用与 DOMPurify 行为。
4. 服务端通过批次5分页和索引队列减少长事务、全量读取与同步上传阻塞；优化前后比较同样数据，不能只以 build chunk warning 消失作为性能改善证明。
5. 建议首轮目标为关键首屏传输与已确认慢路径至少改善约 20%，实际预算在基线后确认；这是目标，不是目前已取得结果。发现无收益的复杂优化不合入。

### 7C. 检索质量与运行配置

复用 [evals README](D:/claudecode/Notebook/backend/evals/README.md)、[run_eval.py](D:/claudecode/Notebook/backend/evals/runners/run_eval.py)、[run_ir_eval.py](D:/claudecode/Notebook/backend/evals/runners/run_ir_eval.py)。分清 deterministic 测试、真实检索和真实模型回答；当前 `--real` 分支尚未实现，不能把它写成可用的真实评测命令。

建立固定语料与独立留出集，覆盖中文、跨文档、证据不足、冲突事实、私有/组织隔离、提示注入、长文档。记录 Recall@K、MRR、无证据拒答、引用匹配、延迟与调用成本；真实模型测试在明确预算和独立账号下运行，缺模型凭据记为跳过而不是通过。每次改检索参数只改变有限变量，失败样本进入回归集，不用测试集反复调到满分。

[runtime_config.py](D:/claudecode/Notebook/backend/app/core/runtime_config.py) 当前是进程内缓存；写入后只刷新当前进程。单实例先保留并写明边界；启用多 worker 前，补共享版本号 + 周期刷新/通知后重新读取，并记录运行使用的配置版本。参数更新使用持久化版本检查处理并发，不能把线程锁当成跨进程锁。验收两个进程均在约定窗口内读到新版本，通知丢失仍能恢复。

### 7D. 运维、文档与恢复演练

- 沿用现有 request_id、日志和审计基础，增加索引队列积压/最老任务年龄、失败重试数、Agent超时/取消率、provider延迟、429/503和撤销失败指标；默认不记录问答全文、Cookie、API Key或完整连接串。
- 为 MySQL、原始知识库文件、Chroma、Django媒体分别定义备份与恢复顺序，记录恢复点、校验方式和负责人。Redis承担撤销信息，恢复或数据丢失时要明确旧token风险与会话处置，不能简单认为所有Redis数据都可丢。
- 在隔离环境完成一次从备份恢复并登录、读笔记、取头像、检索文档的演练；未演练不能标记“有备份所以可恢复”。保留周期/RPO/RTO由部署需求确定，不擅自删除历史数据。
- 修正 [README](D:/claudecode/Notebook/README.md)、[API 文档](D:/claudecode/Notebook/docs/API.md)、[JWT 契约](D:/claudecode/Notebook/docs/JWT_CONTRACT.md)：当前默认 FastAPI docs 是 `/docs`，并非路由加了 `/api/v1` 就自动变为 `/api/v1/docs`。选择文档路径或显式配置之一并测试，生产决定是否限制访问。
- 同步端口、上传异步语义、限额、错误状态、运行配置权限和可执行测试命令；保留旧报告日期，不把历史结果改写成新一轮结果。

## 11. 最终验证、交付物与不做事项

### 11.1 可重复验证命令

以下是实施时的检查入口，不表示本次已执行通过。分别在指定目录运行；统一使用隔离测试环境变量，完整记录退出码。需要联网安装时使用锁文件，不私自升级依赖。

| 工作目录 | 命令 |
|---|---|
| `D:/claudecode/Notebook/backend` | `uv sync --locked --extra dev` |
| 同上 | `uv run ruff check app tests` |
| 同上 | `uv run mypy app/core app/schemas app/utils app/config` |
| 同上 | `uv run pytest tests -q --tb=short --cov=app --cov-report=term-missing --cov-fail-under=85 --ignore=tests/functional` |
| `D:/claudecode/Notebook/DjangoUserService` | `uv sync --locked --extra dev` |
| 同上 | `uv run ruff check .` |
| 同上 | `uv run python manage.py test apps.user apps.file --settings=DjangoUserService.test_settings -v1` |
| `D:/claudecode/Notebook/front` | `npm ci` |
| 同上 | `npm run lint`；`npm run test:unit:coverage`；`npm run build`（分别执行） |
| 同上 | `npx playwright test --project=chromium` |
| 同上，批次3落地后 | `npm run test:e2e:full -- --project=chromium` |
| `D:/claudecode/Notebook` | 注入测试必填配置后 `docker compose config --quiet`；随后真实构建、启动、迁移与集成验证 |

还需扫描反模式：生产异常模块重复 Settings、旧 API 前缀、无所有者草稿、Cookie迁移后残留 `jwt_token` 读取、`pip-audit --fail-level`、吞掉关键撤销/入队错误、索引正常 return 就视为成功、循环里才检查总超时。扫描命中需要逐条解释，不能机械删除字符串或把文档中的示例当成代码缺陷。

### 11.2 每批交付记录

每批更新一个简短实施记录：任务 ID、修改文件、commit、变更前复现、变更后命令与退出码、测试通过/失败/跳过、兼容性影响、迁移与回滚结果、仍未验证事项。新增接口和配置先检查官方文档/锁定版本 API；新增迁移在数据库副本验证，不复制线上凭据到报告。

完成清单：

- [ ] 生产错误不含内部详情，配置不会接受生产占位符。
- [ ] 改密码后会话可用；退出/切换用户无旧用户状态残留；双服务撤销一致。
- [ ] 索引响应与数据库状态一致，失败不伪报可检索。
- [ ] Nginx 鉴权图片、上传、SSE正常；头像容器重建后不丢失。
- [ ] CI命令合法，干净环境可跑，真实全栈任务不是全量skip或全量mock。
- [ ] 付费探针受保护，限流身份正确，Agent超时/取消可收敛。
- [ ] 删除与运行/索引并发无数据复活；重复任务幂等；迁移和备份恢复演练完成。
- [ ] Cookie阶段如实施，两服务CSRF与版本兼容全部验证。
- [ ] 前端核心业务逻辑有回归测试，性能和检索质量有可比较报告。

不做：本轮不切换 React/Next.js，不为追新重写成 LangGraph，不整体合并 Django/FastAPI，不无差别升级所有依赖，不只追求覆盖率数字，不清空 Chroma 重建全部用户数据，不未经确认轮换真实密钥，不自动删除历史记录，也不修改远端仓库保护或执行发布。

建议执行起点：先做 1A、1B、1C 的小修复和回归测试，同时修复 3A 的 CI 配置；再处理 1D 与批次2并跑真实容器门禁。这样第一轮交付即可解决明确影响使用的问题，并为后续更大的修改提供可信验证。
