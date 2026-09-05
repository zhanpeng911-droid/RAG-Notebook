# REMEDIATION_PLAN 实施记录

日期：2026-09-04。基线 `579ab41`，按 `docs/REMEDIATION_PLAN.md` 逐批实施。
每批独立提交，测试常绿；未验证事项明确登记，不谎称完成。

## 总览

| 批次 | Commit | 内容 | 测试 | 状态 |
|---|---|---|---|---|
| 1A | `94bed89` | 生产错误脱敏 + 配置统一 | 后端全绿 | ✅ |
| 1B | `55ada92` | 改密码接管新 token + 用户数据统一清理 | 前端 69 绿 | ✅ |
| 1C | `afcf573` | 上传响应与数据库索引状态一致 | 后端全绿 | ✅ |
| 1D | `e7625d6` | 撤销失败可识别 + 双服务撤销一致 | Django 21 绿 | ✅ |
| 3A | `807ff34` | CI 命令修复 + 独立 Django 审计 + 依赖升级 | 后端/Django 绿 | ✅ |
| 2 | `8156067` | Nginx 代理/媒体卷/uv locked 镜像/env 对齐 | compose config 绿 | ⚠️ 构建待 CI |
| 3B | `abf32b1` | 真实全栈门禁（Playwright full + Docker job） | mocked e2e 39 绿 | ⚠️ Docker job 在 CI |
| 4A/4B | `ded57b8` | 深度健康检查隔离 + 限流加固 | 后端全绿 | ✅ |
| 4C | `d25ccc0` | Agent 统一 deadline/结构化超时/取消/终态 | 后端全绿 | ✅ |
| 5A/5B | `543fe10` | 会话级联删除 + 索引幂等/原子 claim/删除状态/路径安全 | 后端全绿 | ✅ |
| 6A | 本批 | 浏览器会话 ADR | — | 📄 设计批准，6B 待 HTTPS |
| 7D | 本批 | FastAPI docs 路径修正 | — | ✅ |

## 各批明细

### 批次 1A —— 生产错误脱敏与配置统一（`94bed89`）
- 删除 failed_response 重复 Settings，统一中央 AppSettings；生产（prod/production，含大小写）
  强制关闭调试响应；异常日志关联 request_id。
- 配置加载失败只列字段名，不打印含输入值的异常。
- 生产拒绝空/占位符/过短 JWT 密钥（后端 + Django 同一来源；DJANGO_ENV 区分生产）。
- MySQL URL 字段构造（URL.create）；Alembic 用 `mysql_url_string` 保留真实密码。
- 复现→修复：`test_failed_response.py` env×debug 矩阵、`test_config_validation.py`（新）、
  特殊字符密码连接串、生产密钥约束。后端 965 passed / 88% 覆盖率。
- 附修：mypy 门禁 2 处历史遗留（database_session_manager Column 误报）清零。

### 批次 1B —— 改密码 token + 用户数据清理（`55ada92`）
- `updatePassword` 复用 `applyAuthSession` 接管 Django 返回的新 token（旧 token 已撤销）。
- `authEpoch` 代次：clearAuth/登出/401 递增，SSE 迟到写回按代次丢弃。
- clearAuth 统一清理会话/问答历史（动态 import 避免循环依赖），不清 localStorage 全量。
- 草稿改 `noteDraft:{userId}:{noteId}` 键并保存 owner；旧无所有者草稿不归属新用户。
- 测试：改密码后新 token 生效、clearAuth 清理 + epoch、草稿用户隔离。

### 批次 1C —— 上传响应与 DB 状态一致（`afcf573`）
- `_sync_index` 返回落库状态；`save_uploaded_file` 响应 status 与 DB 一致
  （同步失败落库 index_failed，不再伪报 pending_index）。
- 空文档/空切片不可自动重试；provider 错误只进脱敏日志；Celery 不可用不伪装入队。
- 前端按后端状态展示（已完成索引/正在索引/索引失败/上传失败），进度 100% ≠ indexed。
- 测试：各失败分支断言响应/DB 一致。

### 批次 1D —— 撤销失败可识别 + 双服务一致（`e7625d6`）
- Django 生产强制共享 Redis（REDIS_CACHE_URL 必填），移除启动失败永久降级 LocMem。
- 新增 `apps/user/blacklist.py`：共享 Redis 失败关闭；无共享 Redis（测试/开发 LocMem）
  走 Django cache。认证/刷新/注销/改密码/资料更新撤销失败 → 503，不伪报成功。
- 键契约 `blacklist:{jti}`，过渡期兼容 `:1:blacklist:{jti}`。
- 双服务共享真实 Redis 契约测试 `test_jwt_contract.py`（REDIS_TEST_URL，CI 注入）。
- JWT_CONTRACT.md 更新失败关闭语义 + 修复失效链接。

### 批次 3A —— CI 命令修复 + 独立 Django 审计（`807ff34`）
- compose job 注入测试 JWT（`.env.docker` 的 `:?` 必填插值）。
- pip-audit 删除不存在的 `--fail-level=high`；豁免逐条 `--ignore-vuln`；`-o` 存 JSON。
- 新增 security-django job；升级 Django 依赖修复已知漏洞：django 5.2.17、DRF 3.17.2、
  simplejwt 5.5.1、pillow 12.3.0、pyjwt/pymysql/python-dotenv/setuptools/sqlparse/click；
  pip-audit 现报 "No known vulnerabilities found"。

### 批次 2 —— Nginx / 媒体卷 / 镜像 / env（`8156067`）
- Nginx：`^~ /api/v1/` 优先于静态正则（修复 .png 私有图片被截获）；知识库上传 210MiB、
  头像 2MiB 限额；静态 location 移除局部 add_header（防覆盖 server 安全头）；
  清理已无调用方的旧裸路径代理；新增 `/media/` alias。
- 修复 useAuthImage 裸路径 bug（`/knowledge/images/all` → `/api/v1/...`）。
- `django_media` 命名卷：Django 写 `/app/media`，Nginx 只读挂载 `/srv/notebook-media`。
- Dockerfile 改 uv locked 安装（固定 uv 0.12.1，--no-dev）；删除 frontend 不被消费的
  build args；backend/django 端口回环绑定；backend SECRET_KEY 改 `:?` 必填；
  Django 补 `DJANGO_ENV=production`。
- ⚠️ 待运行验证：镜像构建与真实容器验证。本机无法访问 Docker registry，`docker compose
  config` 通过；完整构建/启动/迁移在 GitHub Actions fullstack job 执行。

### 批次 3B —— 真实全栈门禁（`abf32b1`）
- fullstack spec 修正 `/api/v1` 路径 + 端口 8002；新增 `playwright.full.config.js`
  （E2E_BASE_URL 支持、Docker Nginx 不启 Vite）；package.json 脚本改 `--config`。
- CI 新增 fullstack job：Docker 起全栈 + 经 Nginx 统一入口跑真实 E2E；失败转储日志与 trace。
- ⚠️ Docker 集成 job 在 GitHub Actions 执行（本机无 registry）。

### 批次 4A/4B —— 健康检查与限流（`ded57b8`）
- `/ready` 整体 4s 超时；`/model`、`/vector-store` 深度检查改运维身份 + 30s 短缓存 +
  并发合并 + 限流；匿名不能触发收费调用；异常不泄露原始文本。
- 限流按已验证 user_id + 路由模板分桶；匿名只信任 TRUSTED_PROXY_IPS 的 X-Forwarded-For；
  INCR+EXPIRE 原子 Lua（含 TTL 自愈）；高成本接口 fail_closed（限流不可用 503）；
  429 带 Retry-After。

### 批次 4C —— Agent deadline/取消/终态（`d25ccc0`）
- Guardrails 单调时钟 + 统一 deadline + remaining_budget；graph 生成前检查预算，
  耗尽发 timeout 事件（修复“超时仍生成答案并记为 completed”）。
- answer_generator 用剩余预算；超时返回结构化 timed_out（不再伪装“抱歉”答案）；
  预算不足跳过非必要 judge。
- AgentPhase/AgentRunStatus 增 TIMEOUT/CANCELLED；路由 timeout 正确落库；
  客户端断开保存 cancelled 终态后传播；终态条件写（迟到事件不覆盖完成结果）；
  completed_at 对所有终态赋值。

### 批次 5A/5B —— 会话删除与索引一致性（`543fe10`）
- clear_session 同事务显式删除 AgentFeedback/AgentStep/AgentRun/ChatMessage/ChatSession。
- 索引：DELETING 状态（向量清理失败保留记录可重试）；原子抢占 claim_for_indexing
  （仅 pending_index 可进入，两 worker 只有一个）；稳定 chunk ID（upsert 幂等）；
  物理删除改 commonpath + 仅删普通文件（修复同前缀兄弟目录/目录本身/跨盘符）。

## 仍未验证事项

1. **批次 2 镜像构建/启动/迁移/媒体卷重建**：本机无法访问 Docker registry；
   `compose config` 通过。由批次 3B 的 GitHub Actions fullstack job 与部署前手动验证覆盖。
2. **批次 3B 全栈 job**：在 GitHub Actions 执行（含 REDIS_TEST_URL 契约测试）。
3. **批次 6B Cookie 迁移**：待真实 HTTPS 统一入口就绪后实施（ADR 已批准，见
   `docs/adr/0001-browser-session.md`）。
4. **批次 7 前端拆分/性能/评测**：未实施。前端页面拆分与组件化是纯 P2 重构，
   建议作为独立里程碑；性能优化需先建立基线数据再比较（REMEDIATION_PLAN 7B 口径）。
5. **5A 分页**：会话/历史消息有上限分页与 ID 游标未实施（涉及前端“加载更多”改造，
   属 P2，登记待做）。
6. **心跳/超时回收**：4C 采用终态条件写防止覆盖；长时间未更新的运行自动回收
   （心跳/lease）未实施，登记待做（与 5B 删除清理任务一起）。

---

## 返修复验记录（2026-09-04，按 docs/REMEDIATION_REWORK_PLAN.md）

承接验收返修方案（`REMEDIATION_REWORK_PLAN.md`），基线 `6e161cf`。以下为返修批次与对第一轮交付的状态更正。

### 返修提交

| 返修项 | Commit | 内容 | 状态 |
|---|---|---|---|
| R1/F01 | `ca028fb` | Alembic 006 迁移 ENUM 扩到含 `deleting`；真实 MySQL 8.0.12 验证空库 upgrade、旧值保留、deleting 可写、危险 downgrade 被拒 | ✅ |
| R2A/F02 | `ca028fb` | 删除重试始终尝试向量清理（含 DELETING/INDEX_FAILED）；批量入口对齐 | ✅ |
| F04/R2D | `ca028fb` | `publish_indexed`/`mark_index_failed_if_indexing` 原子条件写；worker 在 deleting 后不得改回 indexed | ✅ |
| R4/F03 | `97c6196` | ChatSession tombstone（deleted_at）+ 迁移 007；add_message 不再自动重建已删会话；列表/详情排除已删 | ✅ |
| F10/R7 | `97c6196` | http_exception_handler 保留 Retry-After / WWW-Authenticate 响应头 | ✅ |
| F05/R5 | `44c9b57` | 前端 onCompleted await 后重新核对 authEpoch | ✅ |
| F06/R6A | `44c9b57` | 检索纳入整体预算（asyncio.wait_for 剩余预算） | ✅ |
| F08/R6B | `44c9b57` | sseClient 处理 timeout/cancelled → onError（可见失败） | ✅ |
| F09/R3 | `44c9b57` | Celery retry 前重置 pending_index（失败可再 claim）；耗尽才保持 index_failed | ✅ |
| F07/R8 | `da3225f` | 全栈健康检查/API 走 E2E_BASE_URL 统一入口；缺账号直接 fail 不 skip | ✅ |

### 第一轮交付状态更正（11.3 要求）

- **5A/5B**：删除一致性原交付未覆盖"deleting 未入 MySQL ENUM"（F01）、"删除重试不清理"（F02）、"在途 worker 改回 indexed"（F04）——本次经 R1/R2A/F04 返修；5B 统一执行/有界上传/代际发布仍属未完成（纳入 R2C/R3，见下）。
- **4C**：总预算未覆盖检索（F06）、SSE timeout 不可见（F08）——本次经 R6A/R6B 返修；心跳/过期运行回收仍未实施（R6C，登记延期）。
- **1B**：SSE 完成回调 await 后未复核身份（F05）——本次返修；前端统一清理的异步竞态进一步加固项见 R5（本轮未实施 composable 级回归 fixture）。
- **3B**：当前候选 SHA 的 Actions 运行记录缺失——不登记为已通过；fullstack job 需在 CI 对当前 SHA 重跑。
- **7D**：只承认 FastAPI docs 路径修正；前端拆分/性能/评测（7A/7B/7C）未实施。

### 仍未完成（R2C/R3/R6C/R5/R8 后续）

1. **R2C 统一执行服务 + 代际发布 + 读取可见性过滤**：稳定 chunk ID 仍是 `v1` 固定版本，未实现 generation 切换、读取端过滤 active_generation、N→M 旧尾部清理。删除一致性是部分闭环，不能宣称完整。
2. **R3 有界上传 / 生产后台索引**：`await file.read()` 全量读入仍未改为分块；生产默认后台索引未切换。
3. **R6C 心跳/过期运行回收**：未实施。
4. **R5 composable 级回归**：前端 store/HTTP 加固 + 交错竞态测试未实施（本轮只修了 F05）。
5. **R8A 隔离 Compose / 确定性 provider / 迁移种子采集脚本**：fullstack job 依赖现有 compose（固定容器名/端口），未用独立 project + 专用端口；真实 MySQL 迁移/Chroma 并发/头像重建/恢复演练待 CI。
6. **R0 conftest 全局 sys.modules mock 加固**：当前全量连续多次通过（989/991/993），视觉单跑 19 通过且全跑一致；按 fixture 作用域管理的重构登记为后续优化。

---

## 第二轮返修记录（2026-09-05，按 docs/REMEDIATION_SECOND_REVIEW_PLAN.md）

审查基线 `288f8fd`，复验方案提出 S01–S07 七组问题。本轮按方案 §9.1 批次逐项修复，
每个实现提交附常驻回归；S01–S07 负向用例在修复前运行，25 failed / 12 passed
（含既有用例），失败清单存档于实施会话（pytest 输出，见下方"修复前证据"）。

### 返修提交

| 阶段 / 问题 | Commit | 内容 |
|---|---|---|
| A / S01 | `5340b0c` | 会话服务接线：`get_session_manager()` 每次读取当前实例、未初始化明确 RuntimeError；包 `__init__` 不再导入与子模块同名变量（消除遮蔽）；agent 路由就绪前置检查（未就绪 503）；wiring 测试含独立子进程端到端 |
| B / S02,S04 | `7617c64` | 索引状态条件迁移：worker 缺文件检查移到 claim 后；失败写回被拒即退出不重试；重试前条件重置（仅 index_failed）+ 提交成功才投递；人工重试/Beat 补偿条件化（先提交后投递）；`_sync_index` 发布被拒返回数据库真实状态；上传响应与落库一致 |
| C / S03 | `a033ec8` | 统一删除编排 `_delete_single_record`：单删/批删共用，逐项 state=deleted/deleting/failed；向量/MD5/文件任一失败保留记录不删 SQL；pending 凭 md5 一律尝试向量清理；`/clean`、`/documents/{id}` 202/200/500 区分；`/delete/filename` 查询失败与删除失败不回退 v1；前端"删除中"状态与可重试提示 |
| D1 / S05 | `03bee7b` | 会话删除原子屏障：`finalize_agent_run` 同一事务条件写终态 + completed 时父会话行锁内追加历史；Agent 两入口 tombstone 404 + 会话行锁与删除同屏障；add_message 校验与写入同事务加锁；clear_session 幂等重删清理竞态残留；run 详情/列表排除已删会话关联运行 |
| D2 / S07 | `3aa4739` | 007 downgrade 存在 tombstone 时 RuntimeError 拒绝（照 006 模式）；注释写明 MySQL DDL 隐式提交的受控维护窗口约束 |
| E / S06 | `9790a27` | 检索等待上限=剩余预算（删除 0.5s 下限）；judge 等待上限=min(剩余预算,15s)、不足 3s 跳过；provider 异常结构化失败（answer=None + error=generation_failed），graph 发 error 不发 completed |

### 修复前证据

阶段 0 固化的负向用例（`test_services_wiring.py`、`test_index_state_guards.py`、
`test_index_delete_orchestration.py`、`test_session_barriers.py`、
`test_migrations_guards.py`、`test_agent_timeout_cancel.py` 新增段）在修复前运行：
**25 failed, 12 passed in 9.72s**，失败项与 S01–S07 一一对应（worker 重试覆盖 deleting、
缺文件旁路写终态、pending 跳过向量清理、批删失败仍删 SQL、tombstone 会话可启动 Agent、
检索 30ms 预算实际 500ms、judge 越过 deadline、007 无守卫等）。

### 验证结果（候选 SHA `9790a27`）

| 检查 | 命令 / 环境 | 结果 |
|---|---|---|
| 后端 CI 范围全量（连续 2 次） | `pytest tests/ --ignore=tests/functional` | **1031 passed, 9 skipped** |
| functional 套件（发布前手动） | `pytest tests/functional` | **14 passed** |
| 前端单测 | `npm run test:unit` | **71 passed（9 文件）** |
| 前端 lint | `npm run lint` | 0 errors（4 个既有 v-html warning） |
| Django | `manage.py test apps.user apps.file`（CI 等效环境：`.env` 被 gitignore，CI 无共享 Redis → `REDIS_CACHE_URL` 为空走进程内 cache） | **21 passed** |
| mypy（CI 门禁范围） | `mypy app/core app/schemas app/utils app/config` | **0 错误** |
| ruff | `ruff check app tests` | 通过（每个提交经 pre-commit 钩子） |

环境边界说明：Django 21 例中 7 例（认证/上传链路）在"配置了 REDIS_CACHE_URL 但共享
Redis 不可用"的环境下按设计 fail-closed 503 —— 属预期行为而非缺陷；CI 与本轮验证均
在未配置共享 Redis 的等效环境下执行。第二轮复验方案 §1.1 的隔离探针（索引故障注入、
会话初始化、并发交错、预算）已全部固化为上述常驻回归并通过。

### 交付记录更正（方案 §9.3）

- **R2A/F02**：单删顺序重试已修（第一轮）；本轮补齐批量/旧入口/重试后 pending 残余
  （S03）——统一编排 + 逐项结果 + 失败保留，向量清理不再凭状态推断。R2D 的
  generation/manifest/清理记录仍未实施，"仅清理一次向量"不等于 R2D 闭环。
- **F04/F09**：成功发布条件化（第一轮）；本轮补齐失败、重试与旁路（S02）——所有入口
  （worker 异常、缺文件、人工重试、Beat）均条件迁移，deleting 不可被改回，提交与投递
  顺序一致。R3 仍剩有界上传/生产后台索引/统一重试预算。
- **R4/F03**：tombstone 与顺序追加拒绝（第一轮）；本轮补齐服务接线（S01）、跨入口
  屏障与并发提交（S05）、幂等重删、run 可见性（S07 回退保护）。真实 MySQL 双连接锁序
  交错验证仍按 R8A 登记（SQLite 无法证明 MySQL 锁语义，本轮以行锁代码路径 + 逻辑交错
  用例覆盖）。
- **F06/R6A**：检索 wait_for（第一轮）；本轮补齐 judge/全链 deadline 与失败语义（S06）
  ——检索下限移除、judge 受共同预算、provider 错误不发 completed。R6B 终态协议闭环、
  R6C 心跳/回收仍登记。
- **F05/F08/F10/F07**：保留第一轮已验证的单点，不升级为 R5/R6B/R7/R8 全部完成。
- **验证口径更正**：此前"后端 993 连续 4 次全绿"为 CI 范围（`--ignore=tests/functional`）
  口径；手动 functional 套件中 `test_rate_limit_returns_429` 自 `ded57b8`（移除
  `is_redis_available`）起已失败，本轮修复并纳入验证。

### 仍未完成（承接第二轮复验方案 §8，不因本轮缩减）

1. **R2B→R2C→R2D**：执行 generation/attempt、发布指针、manifest、持久清理状态、
   读取侧 active_generation 过滤——本轮修补必须接到统一执行服务，删除重复实现。
2. **R3**：数据库统一重试预算、有界分块上传、生产默认后台索引。
3. **R5**：前端 composable 级身份代次/竞态回归 fixture（本轮只修 F05 单点）。
4. **R6B/R6C**：SSE 协议闭环、取消收尾、心跳/过期运行回收。
5. **R7**：经 Nginx 的单文件/批量代理上传边界实测。
6. **R8**：隔离 Compose（独立 project/专用端口/确定性 provider）、真实 MySQL 双连接
   锁序、真实 Chroma 在途写与删除交错、当前 SHA 的 Actions fullstack 运行记录。
7. **R0/R9**：conftest 全局 sys.modules mock 按 fixture 作用域加固；当前候选 SHA 的
   CI 与真实集成产物采集。

### 第三批：统一执行服务与代次发布（2026-09-05，`6a66f0c`）

| 原任务 | 内容 | 验证 |
|---|---|---|
| R2B 执行身份 | 迁移 008（generation/active_generation）；claim 原子递增代次；发布/失败写回要求代次匹配 | 真实 MySQL 升级 005→008、122 行旧数据保留；test_index_generation.py 代次生命周期用例 |
| R2C 统一执行服务 | `execute_index_document` 唯一实现；HTTP 同步与 Celery worker 共用；worker 退化为重试封装 | `test_worker_and_sync_share_executor`；源码断言 worker 不再自带发布实现 |
| R2D 清理与读取侧 | 发布后双路径回收旧代次向量（元数据过滤 + 旧版 v1 固定 ID）；检索向量路径 `_filter_active_documents`（indexed + 代次匹配，fail-closed） | 过滤器用例：已删/旧代次/无标识/存量无代次分别处理；查询失败全丢弃 |
| R3 有界上传 | 声明大小预检 + 1MB 分块读取 + 20MB 硬上限 | 声明超限零读取拒绝、流超限中止、正常分块成功三用例 |
| R6C 运行回收 | `reclaim_stale_runs` 条件写（仅 started 且超时）+ Beat 每 5 分钟 | 回收/不误收新鲜运行/不触碰终态 + Celery 注册用例 |

仍未完成（登记）：R2D 的 manifest/清理记录持久化、R3 数据库统一重试预算与生产后台索引开关、
R5 composable 级竞态 fixture、R6B SSE 协议闭环、R7 Nginx 上传边界、R8 隔离 Compose 与
真实 Chroma 并发/Actions 产物。

---

## 第二次验收返修记录（2026-09-05，评审结论"暂不通过/Request changes"后）

评审在 `893a7a5` 实测：前端 71 / Django 21 / functional 14 / Ruff / mypy / lint/build 通过；
后端全量 1041 passed、2 failed、9 skipped（导入顺序/测试隔离）。提出 9 项阻断问题。
本批全部修复，提交见下表。

| # | 级别 | 问题 | 修复 | 回归 |
|---|---|---|---|---|
| 1 | P0 | 发布未提交 MySQL 先删旧向量，提交失败导致文档不可检索 | `execute_index_document` 改为先提交发布指针、提交成功后才内联清理；新增 `cleanup_superseded_vectors_task`（Beat 每 10 分钟，幂等元数据过滤清理）持久兜底 | `test_publish_commit_precedes_vector_cleanup`（清理时另一连接已见 indexed）+ `test_publish_commit_failure_keeps_old_generation`（提交失败零删除、旧代次保留） |
| 2 | P1 | Agent 未验证 space_id 成员资格 | `_ensure_space_member` 提升为 `app/core/permission.ensure_space_member` 共享实现；Agent 流式/非流式入口在调 provider 前校验（404/403） | `test_agent_rejects_non_member_space` / `test_agent_stream_rejects_non_member_space` / `test_query_non_member_space_404` |
| 3 | P1 | `get_document_chunk` 按 ID 直读无所有权/代次验证 | 强制 metadata.user_id 匹配（缺失即拒）+ 文档 indexed + 代次等于 active_generation（gen-less 残留在已发布代次文档上一并拒绝），任一失败返回 None | `test_get_document_chunk_ownership_and_generation`（本人/他人/旧代次/缺标识）+ `test_get_document_chunk_rejects_other_user` |
| 4 | P1 | 剩余预算 0–3 秒跳过 judge 时 `quality_scores` 未初始化 → 正常答案变 generation_failed | 进入条件分支前初始化 `quality_scores = None`（阶段 E 引入的回归） | `test_generate_with_tiny_remaining_budget_returns_answer` |
| 5 | P1 | 反馈提交绕过会话删除屏障 | 反馈端点在同一事务内对父会话行 `SELECT ... FOR UPDATE` 并复核 tombstone，删除获胜即 404 | `test_feedback_blocked_when_session_deleted_behind_race`（模拟 get_run 与插入之间删除获胜）+ `test_feedback_ok_for_active_session` |
| 6 | P1 | v1 上传/部分读取入口绕过 generation；v1 文档对 Agent 检索不可见 | v1 上传（single/multiple/stream 共用）内容校验通过后登记 document_index 行并分配代次（`kb:{user}:{md5}:g{n}:{i}` 稳定 ID，修复 v1 重传随机 ID 的 N→M 重复），条件发布推进 active_generation；读取侧 gen-less 规则收紧：已发布代次文档的 gen-less 残留不可见（纯存量 active=0 文档放行） | `test_v1_upload_registers_generation` / `test_v1_upload_failure_marks_failed` / `test_retrieval_filter_genless_rule` |
| 7 | P1 | 008 downgrade 直接丢代次依据 | 存在 active_generation > 0 时 RuntimeError 拒绝（照 006/007 模式），注明停写维护窗口要求 | `test_008_downgrade_guard_source` |
| 8 | P1 | 连接池修复漏掉 `sync_note_vector_task` | 该任务 `asyncio.run` 入口补 `dispose_stale_connections()`（评审 #8） | `test_sync_note_vector_disposes_stale_connections` + 本地 worker 实测 |
| 9 | P1 | R6C 仅按 created_at 清扫、limit 未生效 | 迁移 009 加 `agent_runs.heartbeat_at`；流式执行每 10s 心跳；回收改为 lease=COALESCE(heartbeat_at, created_at)，limit 经子查询实际生效（最旧优先） | `test_heartbeat_lease_and_limit`（心跳新鲜不回收/limit=1 只收最旧）+ `test_touch_run_heartbeat` |

测试隔离根因修复（对应评审"后端全量 2 failed"）：`knowledge_service` 的模块级
`from app.rag.vector_store import VectorStoreService` 在首次导入时固化绑定，
全量运行顺序下可能绕过测试桩解析到真实类；改为方法内延迟导入 + `_isolated_db`
autouse fixture 注入默认假实现与内存库，文件级与顺序无关。

### 验证结果（本批候选提交）

- 后端全量（CI 范围）**连续 2 次 1060 passed / 9 skipped**；
- functional **14/14**；mypy 门禁 **0 错误**；ruff 通过；
- 迁移 009 已在真实 MySQL 应用（`alembic current` = 009 head）；
- 前端/Django 本批无改动（此前 71/21 通过维持）。

### 结论（按评审建议口径）

> S01–S07 和第三批的部分实现及单元测试通过；整体验收不通过，Request changes。
> 本批已修复 9 项阻断项；**真实集成证据仍缺**：当前 `main` 领先 `origin/main`
> 多个提交、无对应远程 CI 记录，真实 MySQL 双连接竞态、真实 Chroma 迟到写/清理、
> 隔离 Compose 与当前 SHA 的 Actions 产物待推送后取得。按原约束不自动推送，
> 推送与 CI 复验需项目所有者执行。

---

## 收口方案实施记录（2026-09-05，按 docs/REMEDIATION_CLOSURE_PLAN.md，基线 cde21f4）

修复前失败证据：`tests/test_closure_plan.py` 三用例（C1 复活：v1 上传把 deleting 改回
indexing 并继续写向量；C2 IN(SELECT...LIMIT) 结构；C4 删除无持久清理任务）——修复前
3 failed，修复后 3 passed。

| 项 | 实现 | 状态 |
|---|---|---|
| C1 | 仓库新增 `claim_upload_generation`（仅 uploaded/parsed/pending_index/index_failed 可领取，原子递增代次；deleting/indexing/indexed 拒绝）；v1 上传改走同一领取协议：deleting/indexing → 409 且状态代次不变零向量写入；已发布同内容 → 幂等跳过 | ✅ 代码+单测（`test_v1_upload_on_deleting_doc_is_rejected`）；双 MySQL 连接并发领取待真实验证 |
| C2 | `reclaim_stale_runs` 两阶段：先物化 ID 列表（ORDER BY created_at,id，limit 有界 ≤500，空列表短路），再独立 UPDATE 且复核 status+lease；新增独立心跳服务 `agent_heartbeat.RunHeartbeat`（流式/非流式均覆盖，独立短会话，finally 停止）；心跳周期/租约期限入配置 | ✅ 代码+单测（`test_reclaim_two_phase_structure`、`test_heartbeat_lease_and_limit`、`test_touch_run_heartbeat`）；真实 MySQL 1235 验证待集成 |
| C4 | 新增 `document_cleanup_tasks` 表（迁移 010，已应用真实 MySQL）；发布事务内创建 superseded_generation 任务（与发布同事务，提交失败同回滚）；删除提交后创建 document_deleted 任务（generation=0 哨兵按 user+md5 全量核验）；Beat worker `process_cleanup_tasks_task` 公平领取（next_retry_at,created_at,id 两阶段有界）、清理事务外执行、成功需核验为空、失败指数退避超限 blocked；移除 recent-50 扫描 | ✅ 代码+单测；真实 Chroma 迟到写入验证待集成 |
| C3 | 部分完成：写入侧 v1 single/multiple、v2 同步、Celery worker、重试已统一；读取侧 Agent 检索、chunk_id 直读已统一；删除侧全部入口走公共编排+持久任务。**未完成（点名）**：① `handle_add_vector_multiple_stream`（SSE 流式上传）仍在 `_process_slice_results` 直写向量且不建 document_index 行；② 用户知识列表/文档详情/分块列表三个读取入口未按 active 代次过滤原始 Chroma 结果；③ 存量旧随机 ID/v1 固定 ID 向量的 generation=0 兼容映射与回填命令未实施。 | ⚠️ 部分 |

### 验证（本批提交）

- 后端全量 **连续 2 次 1063 passed / 9 skipped**；functional 14；mypy 门禁 0 错误；ruff 通过；
- 迁移 010 已应用真实 MySQL（head=010）。
- 按方案 §9.3：C1/C2/C4 单测通过、真实集成证据未取得；C3 三项未完成入口已点名；
  整体验收仍以方案 §9.3 双门槛为准。
