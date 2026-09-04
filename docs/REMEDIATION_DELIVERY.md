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
