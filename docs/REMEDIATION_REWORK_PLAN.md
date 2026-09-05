# Notebook 验收返修实施方案

日期：2026-09-04。返修基线：`main@6e161cf653e035d02c6ca261922c04993a6b553d`。

状态：**待实施、待复验的方案，不是完成报告或上线批准。**

本文承接 [原整改方案](D:/claudecode/Notebook/docs/REMEDIATION_PLAN.md) 和 [第一轮交付记录](D:/claudecode/Notebook/docs/REMEDIATION_DELIVERY.md)。原方案仍是完整范围的验收依据；本文把本次验收发现的问题拆成可执行的返修批次，不改写过去的提交和测试结果，也不把登记延期自动等同于获准免验。

本次只新增方案文档。没有修业务代码、执行迁移、清理业务数据、轮换密钥、提交或推送 Git，也没有批准修改线上部署拓扑。

## 1. 目标、证据和完成口径

### 1.1 本轮要交付什么

1. 消除已复现的数据库迁移、删除重试、数据复活、索引重试和超时协议问题。
2. 补齐用户切换过程的异步隔离；保留改密码后接管新 token 的已有效修复。
3. 建立能真实验证 MySQL、Redis、Chroma、Celery、双服务认证和 Nginx 的门禁。
4. 每个“通过”都绑定具体提交、用例、环境、退出码和产物，而不只写“后端全绿”。

两个里程碑分别验收：

- **M1：返修闭环。** 本文 R0—R9 的必需检查通过，关键缺陷关闭；原方案尚未实施的独立事项明确列出，不能宣称全面整改完成。
- **M2：原方案完整闭环。** 再完成第 12 节列出的剩余工作，或由用户明确批准范围变更。Cookie 等带条件的阶段按其适用条件验收。

### 1.2 验收基线，不是本方案执行结果

| 检查 | 上一轮验收实测 | 如何解读 |
|---|---|---|
| 前端单测 | 69 通过 | 未覆盖组合式函数和页面的跨账号竞态 |
| 前端 lint / build | 通过；4 条 lint 警告及大包提示 | 不能替代真实浏览器/容器链路 |
| Django 用户/文件测试 | 21 通过 | 不等于双服务共享 Redis 契约通过 |
| 后端 Ruff / 指定范围 mypy | 通过 | 类型检查范围仍为现有四个目录 |
| 后端全量测试 | 987 通过、2 失败、9 跳过；覆盖率 87.42% | 退出码 1，不能记为全绿 |
| 视觉服务相关测试单独运行 | 与认证相关用例合计 36 通过 | 全量失败、单独通过，需排查导入顺序/测试隔离；尚未证明是本轮新增回归 |
| 当前 SHA 的 Actions | 验收时未查到运行记录 | 不能以旧 SHA 的结果或工作流文件存在替代 |
| 真实全栈/头像重建/恢复演练 | 未完成 | 不登记为通过 |

首轮后端隔离运行另外有两项认证失败，与显式设置不可达黑名单 Redis URL 导致 mock 未命中有关；修正测试环境后不再出现。返修记录应保留这一归因，不把四项失败都归责于业务改动。

### 1.3 问题到任务的映射

| 编号 | 问题和证据 | 返修任务 |
|---|---|---|
| F01 / P1 | `deleting` 未进入 MySQL ENUM 迁移；按旧枚举定义写入复现 ERROR 1265 | R1 |
| F02 / P1 | 向量服务持续失败，两次删除仅调用一次清理，第二次成功且 SQL 被删 | R2A、R2D |
| F03 / P1 | 会话删除后迟到写入重建会话并生成两条消息 | R4 |
| F04 / P1 | 已领取 worker 遇到 `deleting` 仍写向量并改回 `indexed` | R2B—R2D |
| F05 / P1 | SSE 完成回调在 await 后不再检查身份，可能向新账号全局历史写入旧数据 | R5 |
| F06 / P1 | 总预算 30ms 的隔离用例约 203ms 才返回；检索未受整体超时约束 | R6A |
| F07 / P1 | 全栈健康检查默认 8002，Compose 发布 8000；缺配置全量 skip 仍退出 0 | R8 |
| F08 / P2 | `timeout` SSE 事件仅触发 finally，未通知前端失败 | R6B |
| F09 / P2 | 两次索引投递只执行一次；失败状态无法再次 claim | R3 |
| F10 / P2 | HTTP 429 的 `Retry-After` 在异常处理器中丢失 | R7 |
| F11 / 验证缺口 | 真实双服务撤销未覆盖、后端测试不稳定、原批次完成标记过宽 | R0、R8、R9 |

上表的隔离复现不代表已对真实用户数据观察到事故；它证明当前实现不能满足相应验收条件。

## 2. R0：资料发现、测试隔离和复现基线

依赖：无。先做；允许与纯前端用例准备并行。建议分为测试基线、基础设施测试脚手架两个提交。

### 2.1 实施前必须读取的已有模式

实施者先读本节引用的源码与测试，确认签名；以下“拟新增”名称均不是当前已有接口。

| 模块 | 已有接口/模式 | 复制或扩展的位置 |
|---|---|---|
| 文档索引仓库 | `claim_for_indexing(doc_id) -> bool`；`update_status(doc_id, status, error_message=None, chunk_count=None)`；`delete_by_id(doc_id, user_id)` | [仓库](D:/claudecode/Notebook/backend/app/repositories/document_index_repository.py:98) |
| 索引服务 | `save_uploaded_file(file, user_id, space_id="")`；`_sync_index(document_id, user_id)`；`delete_index_record(document_id, user_id, delete_file=True)`；`clean_user_index_records(user_id, space_id=None)` | [服务](D:/claudecode/Notebook/backend/app/services/document_index_service.py:145) |
| 后台执行 | `_index_document_async(task, document_id, user_id)`；`index_document_task.delay(document_id, user_id)` | [任务](D:/claudecode/Notebook/backend/app/tasks/index_task.py:27) |
| 会话 | `add_message(session_id, user_id, user_message, assistant_message)`；`clear_session(session_id, user_id)` | [管理器](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:82) |
| 运行记录 | `update_run_terminal(run_id, status, answer=None, error_message=None, citation_count=None, total_time_ms=None) -> bool` | [运行仓库](D:/claudecode/Notebook/backend/app/repositories/agent_run_repository.py:92) |
| 统一检索 | `RetrievalService.retrieve(query, scope="all", top_k=8, use_hyde=True, use_rerank=True)` | [检索服务](D:/claudecode/Notebook/backend/app/rag/retrieval_service.py:83) |
| 流式传输 | `createSSEStream(url, body, handlers)` 返回 abort 函数；`onCompleted` 可以异步，传输层会 await | [SSE 客户端](D:/claudecode/Notebook/front/src/services/sseClient.js:25) |
| 前端认证 | `applyAuthSession(data={})`、`clearAuth()`、`login(userData)`、`getUserInfoDetail()`、`updatePassword(...)` | [用户 store](D:/claudecode/Notebook/front/src/store/user.js:40) |
| 草稿 | `saveDraft(userId, noteId, data)`、`loadDraft(userId, noteId)`、`clearDraft(userId, noteId)` | [草稿服务](D:/claudecode/Notebook/front/src/services/noteDraft.js:21) |
| Django 撤销 | `blacklist_token(token: str) -> bool`；`is_blacklisted(jti) -> bool`；`RevocationUnavailable` | [黑名单模块](D:/claudecode/Notebook/DjangoUserService/apps/user/blacklist.py:56) |
| FastAPI 撤销 | `decode_django_jwt(token)`；异步 `_reject_if_blacklisted(payload)`；用户认证依赖 | [认证模块](D:/claudecode/Notebook/backend/app/utils/auth_utils.py:37) |

锁文件基线：SQLAlchemy 2.0.51、Alembic 1.18.5、Celery 5.6.3、Chroma 1.5.9、langchain-chroma 1.1.0、AnyIO 4.14.1，Python 要求不低于 3.12。版本来自 [backend/uv.lock](D:/claudecode/Notebook/backend/uv.lock)，实施时重新核对；官方在线文档可能更新，不能把页面最新版当作项目安装版。

`add_documents(..., ids=...)` 的当前锁定实现会使用 upsert；相同 ID 可以覆盖，但不会自动删除新一批未提供的旧 ID。`delete(ids=None, **kwargs)` 支持向集合转发 `where`。实施前再核对锁定包源码与真实集合测试，不发明 `delete_document_version()` 等 SDK 方法。

### 2.2 修正测试基线

1. 从 [视觉测试](D:/claudecode/Notebook/backend/tests/test_vision_service.py:49)、[全局 mock](D:/claudecode/Notebook/backend/tests/conftest.py:1)、[模块恢复辅助](D:/claudecode/Notebook/backend/tests/helpers/unmock.py:1)、[模型工厂](D:/claudecode/Notebook/backend/app/utils/factory.py:1) 排查全量与单跑的差异。先记录实际导入来源/类型和测试顺序，不预先认定某个生产依赖损坏。
2. 编码/哈希等与模型无关的测试注入明确 fake model，避免无关默认模型初始化；模型工厂自身保留独立测试，不因此丢失“真实依赖可导入”和“缺依赖明确报错”的覆盖。
3. 测试按 fixture 作用域管理 mock。不要靠重新排列文件名、全局 `sys.modules` 永久替换或忽略失败获得绿灯。若发现生产缺依赖分支把 `Any` 当构造函数，另补明确异常和测试，单列改动依据。
4. unit 环境显式设置测试 ENV、数据库地址、Redis URL、模型类型，避免开发 `.env` 改变分支；将配置 fixture 放在导入之前。真实服务测试另进程执行，不能继承 unit 的重依赖 mock。
5. 新建测试目录建议为 `backend/integration_tests/`（拟新增，与 `tests/` 同级），显式运行，避免自动加载 `tests/conftest.py`。本地可选择不运行；必需 CI job 缺环境或零用例必须失败。

### 2.3 保存复现，不只保存源码搜索

复用 [索引服务 fixture](D:/claudecode/Notebook/backend/tests/test_document_index_service.py:22)、[任务 fixture](D:/claudecode/Notebook/backend/tests/test_index_task.py:35)、[Agent 路由 fixture](D:/claudecode/Notebook/backend/tests/test_router_agent.py:38)、[SSE 响应 helper](D:/claudecode/Notebook/front/tests/unit/services/sseClient.spec.js:7)。

为 F01—F10 保存复现记录与测试设计，在各对应修复提交中先演示用例失败、再实现修复并转为常驻回归。R0 的“基线常绿”指现有套件消除测试隔离问题，不要求尚未修复的 F01—F10 在 R0 已通过，也不靠 skip/xfail 隐藏它们。竞态使用 Event、barrier、deferred Promise 控制交错位置；不要依赖“睡几秒大概撞上”。MySQL 迁移与 Chroma 清理至少各有真实依赖测试，SQLite/mock 只负责快速验证。

验收：相同隔离配置下后端全套连续运行两次无失败；视觉用例单跑与全跑一致；integration 进程实际加载真实数据库/向量客户端；每个失败场景有稳定的复现用例。

禁止：本轮降低覆盖率阈值、把失败改为 skip/xfail、借用真实用户库或真实模型账号补环境。

## 3. R1：补齐 MySQL 枚举迁移

依赖：R0 的 MySQL 测试环境。范围：模型、一个增量迁移、迁移集成测试。

### 3.1 修改方法

1. 从 [004 迁移结构](D:/claudecode/Notebook/backend/alembic/versions/004_filename_unique.py:1) 复制 revision/down_revision、upgrade/downgrade 组织方式。新迁移衔接实际 `alembic heads`；目前 head 对应 005，但不能硬编码假设执行时仍未变化。
2. 在新迁移中将 `document_index.status` ENUM 扩展为包含 `deleting`，保留原有六个值、NOT NULL、默认值和注释。不要修改已经执行的 [002 迁移](D:/claudecode/Notebook/backend/alembic/versions/002_document_index.py:30)。
3. 使用 `op.alter_column` 并明确旧类型、默认值和可空性。MySQL ALTER 需注意完整列定义，参照 [Alembic 操作说明](https://alembic.sqlalchemy.org/en/latest/ops.html#alembic.operations.Operations.alter_column)，再用锁定版本生成和执行 DDL。
4. 单独检查 `AgentRun.status`：当前是 VARCHAR，不能因为 Python 增加 cancelled 就照搬一份 ENUM 迁移。

### 3.2 部署与回退

- 先做迁移副本测试和备份校验，再在维护步骤中执行 schema 扩展，最后启动写入新状态的代码。
- ENUM 扩展后，旧 Python ORM 仍可能不认识 `deleting`。开始使用新状态前需替换所有旧 web/worker/beat；不能仅以数据库兼容证明应用可混跑。
- 默认回退代码时保留扩展 schema。若确需缩回 ENUM，downgrade 必须先检查 `deleting` 行和清理任务；存在未清理记录则拒绝缩回。不能把它们批量改成 `indexed`，也不能删记录来让 downgrade 成功。

验收：空库 upgrade head 成功；旧库升级后旧值保留且可写 deleting；存在 deleting 时危险 downgrade 被拒绝；清理完成的隔离样本可按已定义步骤回退。记录 MySQL 版本、SQL mode、DDL、锁表耗时和前后行数，不承诺未经实测的无锁迁移。

## 4. R2：索引发布、删除和读取的一致性

依赖：R0、R1。这是返修中风险最高的阶段，拆成 R2A—R2D；任一子项单独完成都不代表“并发安全已验收”。

### 4.1 先确定不变量和设计选择

本文建议采用“**代际发布 + 数据库条件写 + 持久清理依据**”。这是拟新增设计，不是当前能力：

- 每次成功领取生成独立 `write_generation` 和 `lease_token`；同一执行内重发同一批 chunk 复用 ID，租约过期重新领取则分配新 generation。
- 当前可检索版本由 MySQL `active_generation` 决定；新向量写完不等于已发布。
- `deleting` 立即取消文档对新检索的资格；清理暂时失败不重新暴露数据。
- SQL 条件写只能阻止旧任务发布数据库状态，**不能撤销已开始的 Chroma 写入**。旧代际不可见，同时必须留下可再次清理的持久记录。
- 不把租约到期、任务收到取消或一次查询向量为零，等同于“旧写入绝不会再发生”。

不选只增加一个布尔锁：它不能处理进程崩溃、锁过期后旧 worker 恢复和跨存储发布。也不选每次删全库再重建：会影响无关用户，且丢失恢复边界。

### R2A：统一所有删除入口和返回契约

参考 [单条删除](D:/claudecode/Notebook/backend/app/services/document_index_service.py:487)、[批量删除](D:/claudecode/Notebook/backend/app/services/document_index_service.py:597)、[知识库路由](D:/claudecode/Notebook/backend/app/router/knowledge_router.py:114)。建议新增统一删除编排函数，名字和签名在实现提交中确定。

1. 第一小步修复 F02：`deleting` 的重试也要执行向量清理；`index_failed` 不能假设没有残余向量，因为可能发生“向量写成功、SQL 提交失败”。清理要依据资源身份和历史写入记录，不只看原 status。
2. 单条、批量、按文件名、按 MD5、`/clean` 入口走同一编排。某个 v2 入口失败后禁止回退 v1 删除绕过状态机；原有路由继续保留为适配层，确需退役则另列兼容计划。
3. 批量返回逐项结果，不因部分失败给出全量成功。拟增加 `state=deleted|deleting|failed`、`deleted_count`、`pending_count`、`failed_count` 和有限的项目明细；保留既有 envelope，新增字段先兼容前端。
4. 单条已接受但未完成返回 HTTP 202 和 `state=deleting`；所有存储清理确认完成才返回 200 与 `state=deleted`；接受删除意图本身失败用明确非成功响应。前端不得把所有 2xx 一律显示“已彻底删除”。
5. 物理文件删除 helper 改为返回可判断的结果，区分不存在、成功、越界拒绝和 I/O 失败；继续使用现有 commonpath 边界保护。不得吞掉文件删除异常后把最终结果记为完整删除。
6. 对用户+MD5 相同但多个逻辑资源的情况先查归属/引用，不无差别删掉其他文档或笔记。向量新格式优先按 document_id+generation 清理。

兼容参数必须先列成资源矩阵，不能按参数名猜意思：现有 `/md5/clear` 和 `/md5/delete/{md5}` 的 `delete_documents=False` 表示只清 MD5 记录、保留文档；它们不能被改成全资源删除。`delete_index_record(..., delete_file=False)` 则只约束物理文件，不能与前者当作同一操作。按文件名旧路由当前混用这些参数，需分别核对 API 文档、调用方和历史行为，明确其范围后使用内部具名操作（例如“仅清去重记录”“删除索引但保留原文件”“删除全部资源”，这些名称均为拟新增）。有冲突时保留原数据并要求明确操作，不能默认扩大删除范围。每种 true/false 分支都测试保留资源仍在、删除资源不再可用；响应中的 completed/deleted 仅针对这次明确请求的资源集合。

测试：向量连续失败三次时三次都尝试清理且 SQL 依据保留；第四次恢复才完成；文件失败、MD5 清理失败、部分批量失败均可重试；A 的删除不影响 B；旧路由不能绕过。复制现有 fixture 扩展行为断言，而不是只断言第二次 `success=True`。

禁止：仅把 DELETING 加进 if 条件就宣称全部一致性问题已修复。此步骤修的是失败重试，尚未覆盖在途 worker。

### R2B：增加执行代际、租约和清理记录

主要修改 [DocumentIndex](D:/claudecode/Notebook/backend/app/models/document_index.py:25)、[索引仓库](D:/claudecode/Notebook/backend/app/repositories/document_index_repository.py:120)，新增后续迁移与仓库测试。复杂模型和执行接入分开提交。

建议新增字段/表如下；最终字段名需在实现时统一，不能假装下表已经存在：

| 对象 | 拟新增数据 | 用途 |
|---|---|---|
| 文档索引行 | `active_generation`、代际分配计数、`lease_token`、`lease_expires_at`、`delete_requested_at` | 确定可见版本和当前合法写入者 |
| 文档索引行 | `attempt_count`、`last_error_code`、`next_retry_at` | 持久化执行预算及退避时间；与现有 retry_count 迁移语义明确 |
| 代际记录/清理清单 | `(document_id,generation)` 唯一、owner、状态、预期 chunk 数/校验摘要、清理进度和错误码 | SQL 提交失败、worker 崩溃后仍知道清哪些数据 |

领取流程使用短事务的行锁或带条件 UPDATE：校验 owner/状态/到期时间，分配 generation 与 token，记录写入意图后提交，再调用外部服务。完成、失败、续租都携带原 token/generation；状态条件不匹配或 rowcount=0 时停止发布，登记过时代际待清理。

`get_by_id()` 再判断不是并发防护；判断和更新必须由数据库原子执行。时间以数据库一致时间或明确统一的 UTC 策略计算；进程内 monotonic deadline 不能写入数据库当跨机器租约时间。

删除事务先标记 `deleting`、使旧 token 失效、撤下 active generation，并持久化待清理代际，再提交。不要在数据库事务内长时间等待 embedding 或 Chroma。

验收：两个真实 MySQL 连接同时领取只有一个成功；过期任务重新领取获得不同 generation；旧任务不能发布成功/失败/续租；租约或计数更新不丢失；迁移前后已有数据可读。

### R2C：统一执行服务、发布与读取过滤

主要修改 [同步索引](D:/claudecode/Notebook/backend/app/services/document_index_service.py:309)、[Celery 索引](D:/claudecode/Notebook/backend/app/tasks/index_task.py:42)、[检索层](D:/claudecode/Notebook/backend/app/rag/retrieval_service.py:199)、[混合检索](D:/claudecode/Notebook/backend/app/rag/retrievers/hybrid_retriever.py:28)。至少拆为 R2C-1 执行与发布、R2C-2 全部读取入口、R2C-3 旧写入口与 legacy 兼容；每个子项独立测试，全部完成才过该阶段门。

1. 提取唯一索引执行服务，HTTP 同步开发模式和 Celery 都调用它；不维护两份 claim、metadata、成功/失败写回代码。保留已经修好的“响应状态与已提交数据库状态一致”契约。
2. chunk ID 改为所有者+document_id+generation+序号；metadata 同时写明确的资源类型、document_id、generation、user_id、space_id 和原有必要字段。ID 可帮助幂等，但查询过滤不能从字符串格式猜所有权。
3. 外部写完后验证该 generation 的 chunk 数及基本内容/维度；通过带 token/generation 的 SQL 条件更新切换 active generation。验证失败不发布，SQL 提交失败不把生成结果标为成功。
4. 重建 N 个 chunk 变成 M 个时，新代际只暴露 M 个，旧代际纳入清理；不要继续固定 `v1` 并遗留旧尾部。
5. 在候选进入 rerank/模型上下文、缓存和对外返回之前，批量验证文档仍允许访问、未删除且 generation 等于 active generation。Vector、BM25、关键词降级、旧查询入口和引用/切片查看均走相同可见性规则。
6. Chroma where 保留用户/空间过滤；SQL 不可用时知识库结果失败关闭，不能放行未验证候选。后过滤需要有界候选扩展，防止旧版本占据 top-K 导致合法结果全空；记录补取上限和性能基线，不全量加载用户所有文档 ID 到无限长过滤表达式。
7. 显式区分“已发布版本”与“本次任务执行状态”：重建期间若选择保留旧版可读，只读 active generation；删除期间一律不可读。UI 同时显示重建状态，不以暂处 indexing 推导旧版不存在。
8. 检索/回答缓存包含或校验有效版本。命中旧缓存也不能跳过删除检查。查询开始后才发生删除的在途请求，在送入模型/发出结果前重检；已经发给客户端或外部模型的内容不能追溯撤回，应在接口语义中说明时间边界。

必须逐个核对的读取/写入绕行点：

| 已有路径 | 需要接入的规则 |
|---|---|
| [note_service 关联文档](D:/claudecode/Notebook/backend/app/services/note_service.py:269) 直接 similarity_search | 在返回正文/预览前验证所有权、删除状态和有效代际 |
| [get_document_chunk](D:/claudecode/Notebook/backend/app/agentic/tools.py:92) 直接按 chunk ID get | 参数中有 user_id 不代表已经校验；查询或返回前必须核对 metadata 所有权、资源状态和代际 |
| [list_user_documents](D:/claudecode/Notebook/backend/app/agentic/tools.py:131) 与关键词降级 | 用户业务检索排除删除中资源；管理页面展示清理进度与可检索资源列表分开 |
| [旧上传服务](D:/claudecode/Notebook/backend/app/router/knowledge_service.py:156) / document processor | 仍受支持的新上传也转到统一编排，不能持续生成没有代际和清理依据的新向量；返回格式用适配层兼容 |
| [MD5 记录服务](D:/claudecode/Notebook/backend/app/services/knowledge_record_service.py:36) | 文本文件/缓存不是删除与发布的唯一事实来源；其失败不丢数据库清理依据 |

兼容旧数据：先做只读 inventory，区分 v2 文档、v1 MD5 资源和独立 `notes_collection`。新版本过滤不能误套到笔记集合。能唯一映射的旧向量按用户+文档映射为 legacy generation；不能唯一归属的记录列入对账，不静默删除或向所有文档开放。兼容适配先上线再逐文档迁移，迁移失败保持原有效映射或明确隔离，禁止全库清空重建。实施前登记仍受支持的 v1 路由及调用方；暂不能适配的写入入口应有明确维护/退役决定，不能同时宣称全入口闭环。

验收：真实 Chroma 下重复同批写不增加有效 chunk；N→M 后只有 M 个可被检索；过时代际、deleting、A/B 越权候选在所有读取路径不可见；有效旧数据和笔记仍能正常检索；有界补取不会无限循环。

### R2D：补偿、旧 worker 迟到写与部署边界

1. 清理 worker 根据持久代际清单重试，删除某代际后核验实际结果。对已失去租约的旧任务仍保留清理依据，允许它迟到写后再次被清走。
2. 完整删除必须同时满足文件、相关 MD5/映射和向量清理条件；若无法证明在途写入已经停止，就继续报告“逻辑删除完成、物理清理待确认”，而不是返回彻底删除。
3. tombstone/清理记录只保留防复活所需的最小标识，不存原文、回答、token 或原始错误。自动清除 tombstone 的期限和“无旧写入者”的证明规则须另行确定；本轮默认不自动物理移除这些安全依据。不能仅等一个 lease TTL 后删除依据。
4. 发布切换时暂停旧任务领取，等待或终止旧版 web/worker 中可能写入旧格式的任务，确认队列内旧消息处理策略，再启动新执行服务。保留 schema/有效版本，代码回滚不得恢复绕过 tombstone 的旧写入路径。
5. 当前 web/worker 使用嵌入式 Chroma 并共享持久目录，项目还存在客户端缓存自愈代码；不能拿单进程 fake store 证明此拓扑安全。R0 应记录实际进程数和路径。
6. **部署建议、待确认**：需要多进程稳定访问时，采用内网独立 Chroma 服务，由客户端接入同一服务，不把同一持久目录分别嵌入多个业务进程。此项需新增部署配置、存储副本迁移与回滚演练，不能在返修时无声切换；依据 [Chroma client-server 模式](https://docs.trychroma.com/docs/run-chroma/client-server)。若仍保留嵌入式拓扑，必须先限定访问并验证其实际支持边界，多 worker 集成门禁不能因此省略。

同名重新上传也属于删除生命周期：现有 `(user_id, original_filename)` 唯一约束不能被永久保留的业务行一直占用。建议清理未完成时返回明确冲突/删除中提示；确认旧写入者停止且清理完成后，把最小旧 document_id/代际拒绝与清理依据原子转入独立 tombstone，再释放原业务行/文件名。新上传分配新 document_id，旧消息不得创建该旧 ID。旧格式只能按 user+MD5 清理时，必须先解决新旧资源误删问题；在选择器尚不能区分之前，不允许自动清理任务误删同内容的新上传。补“删除后同名/同内容重新上传、旧清理任务迟到”用例。

最小清理状态与调度规则必须在编码前固定：

| 拟新增清理状态 | 进入条件 | 后续扫描/完成条件 |
|---|---|---|
| `waiting_writer` | 删除意图已提交，仍有在途写入或外部结果不明 | 到期持续扫描；即使暂时向量数为零也不能跳过该代际 |
| `retry_pending` | 当前清理失败，或待清数据仍存在 | 按 next_retry_at 有限频率重试；告警后仍保留人工恢复入口 |
| `verifying` | 已确认该代际写入者关闭，清理调用返回 | 再验证目标资源不存在，失败回 retry_pending |
| `completed` | 写入者关闭证据和目标资源验证同时成立 | 可移交独立 tombstone；缺任一证据都不得进入此状态 |

正常路径的“写入者关闭”是该 generation 的所有已登记外部写调用已经结束且结果明确，并在 DB 中记录关闭确认；不能在仅取消 await 的 finally 中抢先确认。发生超时/断网而远端结果未知时继续 waiting_writer。失联进程/无法证明结束的请求进入明确的人工维护流程：停止对应旧写入者、使未决远端写入结束或通过已验证的维护步骤中止，再核验。执行平台没有此能力时维持 pending 并告警，不能靠猜测 TTL 解锁或无限返回假成功。扫描保留 cursor/限额，不全表高频轮询。测试必须证明“一次 get=0 但 writer 未关闭”仍会再次扫描，以及正常 writer 确认关闭后能完成删除，不永久卡住。

并发验收必须覆盖：向量写前删除；写入中删除；向量写完 SQL 未提交时删除；旧 worker 过期后恢复；发布成功后进程崩溃；清理后旧 worker 再写。断言既包括 SQL 状态，也包括检索不可见和后续物理清理，不只检查数据库行数。

## 5. R3：索引重试、后台上传和调度提交顺序

依赖：R2B、R2C 的统一执行和计数契约。建议分为重试状态机、上传入口两个提交。

### 5.1 重试以数据库为依据

参考 [当前失败分支](D:/claudecode/Notebook/backend/app/tasks/index_task.py:162)、[批量投递](D:/claudecode/Notebook/backend/app/tasks/index_task.py:251) 和 [Celery retry 语义](https://docs.celeryq.dev/en/stable/userguide/tasks.html#retrying)。

1. 统一 attempt 定义：只有成功领取并实际开始一次执行才加一；重复消息、领取失败不消耗次数。数据库是预算事实来源，不让 Celery retries 和 SQL retry_count 各自独立放大预算。
2. 可恢复错误提交“待重试状态+next_retry_at+错误码”，到期后才允许领取；格式不支持、空内容等终止为不可自动重试。明确指数退避上限/抖动，具体业务次数作为配置，不冒充已批准配额。
3. 保留 Celery retry 作为投递手段也可以，但必须先提交可领取状态和持久预算，再发布消息。重投仍要校验 due time 和 generation；不能在 index_failed 原地反复调用 retry。
4. Beat 先以短事务确定 due work，提交后发布。发布失败保留可扫描的 pending 记录；进程在提交后发布前死亡由下一轮扫描恢复。重复发布允许发生，重复执行由 claim 挡住。
5. 租约过期恢复遵循 R2 重新分配 generation 的规则；人工重试不能恢复 deleting 或清除删除标记。并发人工重试和 Beat 不能生成两个有效执行者。

验收：第一次外部暂时失败、第二次实际成功；连续失败在预算耗尽后停下；broker 不可用后可恢复；worker 在 DB commit 前收到消息的旧竞态消失；丢消息由扫描补发；所有异常状态都不伪报 indexed。

### 5.2 上传接入同一后台状态机

原方案要求的有界读取、生产后台索引尚未完成，本轮在统一执行服务之后补齐：

- 替换一次性 `await file.read()` 为有上限的分块读取/落盘/摘要，限制单文件、批量总量和并发；超限清理本次临时文件，不能删除其他文档。
- 文件持久化且 SQL 记录提交后才进入排队；生产默认保存并排队，响应明确代表 `pending_index`，不等待完整 embedding。
- 开发同步模式需显式配置，调用同一执行服务并返回其真实结果；禁止重新出现“函数正常返回就视为 indexed”。
- 前端区分上传接收、排队、索引、失败和删除中；轮询有退避、页面/用户切换可取消。原有 20MiB 单文件/200MiB 批量限额继续保留，任何调整另行说明。

验收：大文件读取不造成与批量总文件大小等量的应用内存复制；超限、磁盘失败、DB 失败、队列失败均有一致可恢复状态；上传接口及时返回，索引失败不会变成“上传后可检索”的成功提示。

## 6. R4：会话删除与运行完成的原子隔离

依赖：R0；可独立于知识库返修开发，但迁移编号统一协调。建议拆为会话生命周期、运行写回两个提交。

主要文件：[会话模型](D:/claudecode/Notebook/backend/app/models/chat_history.py:17)、[会话管理器](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:82)、[运行仓库](D:/claudecode/Notebook/backend/app/repositories/agent_run_repository.py:92)、[Agent 路由](D:/claudecode/Notebook/backend/app/router/agent_router.py:110)，以及旧聊天入口的调用方。

### 6.1 设计与改动

1. 建议 ChatSession 增加 `deleted_at`，删除后保留不含标题/正文/metadata 的最小 tombstone；列表、详情和写入入口排除已删除行。不能只保留前端隐藏状态。
2. 显式区分“创建新会话”和“向已有会话追加”。新建由服务端生成新 ID；已有 session_id 不存在或已删除时拒绝，`add_message` 不再替迟到请求自动创建旧 ID。审计旧聊天路径，先补显式创建再收紧追加契约。
3. 删除事务锁定用户所属会话，写入删除标记、删除消息/Agent 内容/步骤/反馈并清空敏感字段后提交；保留必要取消标识，不把审计日志无条件一并删除。
4. 新增共享的“完成运行并追加历史”事务服务：锁定同一父会话、验证 active 和归属、条件写运行终态、仅在写入成功时追加消息，然后统一 commit。流式和非流式路由共用；不能先查 active、事务外再写。
5. `update_run_terminal()` 返回 False 必须被处理；重复 completed、run 已删除或已终态不得再次追加历史。step/feedback 写入也验证父 run 和会话，没有外键时尤其不能产生孤儿。
6. 取消通知用于尽快停止计算，可用本进程任务表加共享通知/轮询；数据库校验才是最终防复活屏障。丢取消消息、跨 worker 执行时仍要满足不变量。

所有携带会话数据的写入都使用同一原子屏障：创建 run、追加 step、写 feedback、写 messages 和提交终态，均在同一短事务内锁定 active 父会话（或采用经过真实数据库测试的等价条件写），按一致的“会话→run→子记录”顺序加锁。不得把“验证父记录”实现成事务外先查再插；创建 run 的初始请求与删除也必须串行化。

共享完成服务应返回实际提交结果，例如拟新增 `committed / already_terminal / session_deleted / persistence_failed`，而不是只返回未解释的 bool。路由依据提交结果输出终态：删除/取消/超时已胜出时不得继续发送 completed 或返回成功答案；重复已完成事件不重复发成功或追加历史；持久化失败/清理预算耗尽时给稳定的失败语义或关闭流并保留恢复依据，不能让前端将未确认提交的结果写为成功快照。流式和非流式保持同一策略。

### 6.2 验收和回滚

- 用 barrier 让 Agent 在生成中暂停，另一连接删除会话，再放行完成：会话保持不可见，正文/run/step/feedback 不复活。
- 分别让 create_run、add_step、add_feedback 在读父记录后暂停并穿插删除，验证正确锁顺序阻止迟到插入；删除或回收先胜出时，HTTP/SSE 和浏览器历史也不能收到迟到成功结果。
- 反向交错：完成先提交，随后删除，所有内容同样被清理；重复完成只写一组消息。
- 新建会话仍可用；A 不能写入/删除 B 的会话；旧接口不偷偷重建 tombstone ID。
- 删除任一步数据库失败必须回滚整个事务。取消通知失败不回滚已完成的用户删除，但记录有限错误并通过其他机制收敛计算。
- 上线前覆盖所有旧写入者；不能回滚到遇不到会话就自动创建的旧管理器。tombstone 的物理清除另经保留期确认，本轮不做历史数据批量清理。

## 7. R5：认证代次、旧响应与草稿隔离

依赖：R0 的前端测试基础；不依赖 Cookie 迁移。建议拆成 store/HTTP、聊天/草稿、注销提示三个提交。

### 7.1 身份会话与凭据轮换分开

修改 [user store](D:/claudecode/Notebook/front/src/store/user.js:64)、[HTTP 拦截器](D:/claudecode/Notebook/front/src/services/http.js:46)。

1. 定义两个不同概念：`authEpoch` 标识逻辑登录会话的清除/替换；凭据版本标识同一主体下的 token 轮换。改密码后接管新 token 不应机械清除该用户所有合法页面状态。
2. 每个用户级请求发起时捕获 epoch、owner 和必要的凭据版本；成功/失败/最终回调写 store 前核对。login/register 增加请求序号，迟到的旧登录结果不得覆盖更新的一次登录。
3. 资料/密码更新响应必须匹配发起主体和适用凭据版本，再接管返回 token。互斥或序列化会轮换 token 的操作，避免较旧响应反向覆盖新 token。
4. 请求拦截器把请求时 epoch/凭据快照放入内部 config 元数据；401 仅在仍属于当前有效凭据时触发 clearAuth。旧账号/旧 token 的 401 不能踢掉新登录。元数据不写入日志或响应，不增加不必要的敏感请求头。
5. clearAuth 先同步失效当前主体并取消关联工作；异步 import 后清 store 时再次确认清理目标，避免清掉已经登录的新用户。必要时用串行身份切换流程，不能用一个全局 setTimeout 猜时序。

### 7.2 聊天回调与草稿

修改 [聊天组合式函数](D:/claudecode/Notebook/front/src/composables/useChatWorkspace.js:284)、[问答历史 store](D:/claudecode/Notebook/front/src/store/qaHistory.js:1)、[笔记编辑页](D:/claudecode/Notebook/front/src/pages/notes/NoteEditorPage.vue:367)。

- 在发起相关笔记请求前捕获上下文；每个 await 之后、每次 pushQaSnapshot/写缓存之前再次检查。AbortSignal 只取消它关联的请求，不会自动取消已经进入 handler 的其他 Promise。
- 用户清理、页面卸载、会话切换时取消旧流和关联请求；finalizer 也携带上下文，不能把新会话的 loading 清掉。
- qaHistory 的持久化按 owner 隔离并校验 owner，不能只依赖退出时清一次全局 key。对无归属旧记录不擅自归给新账号。
- 草稿 debounce 在调度时捕获 owner、noteId、epoch；到执行时核对，不从当前用户读取 owner 来给旧内容换归属。切换用户/文档取消定时器。
- 新建页恢复草稿应先检查/读取，再让用户明确放弃时清除；目前先清后读的路径应另补回归。匿名草稿不自动提升为某个登录用户的私有草稿。

### 7.3 注销撤销失败的可见语义

`logout()` 拟返回结构化结果：本机凭据是否清除、服务端撤销是否确认、稳定错误码。建议用户点击退出后仍清本机数据，但 503 时展示“已退出此设备，服务端撤销未确认”的提示，不伪装为所有会话均已失效。

更新 [TopBar](D:/claudecode/Notebook/front/src/layouts/TopBar.vue:146) 和 [Sidebar](D:/claudecode/Notebook/front/src/layouts/Sidebar.vue:222) 两个调用点。不要为后台重试把待撤销 token 长期写回 localStorage；未来全设备注销另列产品功能。本轮不新增一个并不存在的前端 refresh action 来扩大范围。

### 7.4 测试

复制 [user fixture](D:/claudecode/Notebook/front/tests/unit/store/user.spec.js:1)、[HTTP fixture](D:/claudecode/Notebook/front/tests/unit/services/http.spec.js:1)、[草稿用例](D:/claudecode/Notebook/front/tests/unit/services/noteDraft.spec.js:1)，新增真实使用组合式函数的测试 fixture。当前没有可直接复制的 composable fixture，应明确新增，并纳入 [Vitest 覆盖范围](D:/claudecode/Notebook/front/vitest.config.js)。

必测交错：A 回调进入 completed→等待相关笔记→退出并登录 B→A Promise resolve；A 资料响应/401 在 B 登录后到达；clearAuth import 未结束时新登录；两个 login 响应倒序；改密码新 token 生效后旧请求 401；A 草稿计时器在 B 登录后触发；logout 503 与确认成功显示不同结果。

同账号凭据竞争另设用例：资料更新和改密码重叠、两个轮换响应倒序、轮换操作排队时退出。断言最终采用的 token 在 Django/FastAPI 两服务均有效，旧操作不能覆盖新 token，排队操作在 logout 后不得重新登录或回填用户信息。

验收：B 的 store、sessionStorage/localStorage 和可见页面均没有 A 的问题、引用、笔记、资料或草稿；同一用户正常 token 轮换继续可用；不是只检查 `authEpoch` 数字增加。

## 8. R6：Agent 总预算、取消收尾和 SSE 协议

依赖：R0；运行内容持久化接入 R4。拆成预算执行、协议/UI、崩溃回收三个提交。

### R6A：预算覆盖整个执行链

参考 [guardrails](D:/claudecode/Notebook/backend/app/agentic/guardrails.py:30)、[graph](D:/claudecode/Notebook/backend/app/agentic/graph.py:94)、[answer_generator](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py:93) 和 [Python 3.12 超时/取消文档](https://docs.python.org/3.12/library/asyncio-task.html#timeouts)。

1. 一次运行只建立一个 deadline；保留 45 秒现有默认预算，若变更另说明。检索、HyDE、改写、rerank、生成、judge 都使用剩余预算与各阶段上限的较小值。
2. 在共享执行 runner 的协程边界建立整体超时作用域，而不是只在 while 开头判断。若使用 `asyncio.timeout_at`，统一采用 event loop 的时钟；若保留 monotonic，则给 `asyncio.timeout` 传剩余相对秒数，不混用不同来源的绝对时间。
3. SSE 的执行与事件消费解耦为有界队列/可取消任务，避免把取消作用域跨异步生成器 yield 留给无关消费者；非流式也复用 runner。队列容量、消费者断开、满队列超时和任务回收均测试，不无限积压事件。
4. 为 SDK/HTTP 设置连接、读取和整体调用上限，关闭会突破剩余预算的隐式重试。同步阻塞计算移出事件循环并限制并发；无法中断的线程只代表停止等待，不能宣称底层工作或计费已停止。需要强制终止时另用受控进程边界并验证。
5. judge 使用剩余预算而非固定再等 15 秒；预算不足直接跳过，不拖延用户主结果。provider 失败也返回结构化失败，不把“抱歉”文本记录成 completed。
6. 业务预算耗尽后不得启动新模型请求。终态持久化另设短清理预算（初始建议 2 秒，可配置），与业务 45 秒分别计量，文档不能把两者混称严格同一墙钟时限。
7. 取消收尾在有界 shield 作用域内使用短 DB session，然后传播原取消。ASGI 环境的已取消作用域会继续取消 await，应参照 [AnyIO 收尾说明](https://anyio.readthedocs.io/en/stable/cancellation.html#finalization) 验证当前安装版本；禁止无限 shield 或无界等待数据库恢复。

测试：检索永久 await、生成挂起、judge 超预算、队列背压、同步任务仍运行、终态 DB 挂起、真实 TCP/Nginx 断连。缩短预算做稳定测试，分别断言业务停止时间与清理截止时间，并检查没有后续模型调用。

### R6B：服务端与浏览器共同理解终态

修改 [路由](D:/claudecode/Notebook/backend/app/router/agent_router.py:110)、[SSE 客户端](D:/claudecode/Notebook/front/src/services/sseClient.js:71)、聊天组合式函数及两端测试。

| 事件 | 后端状态 | 前端行为 |
|---|---|---|
| `completed` | completed | 展示答案/引用；成功完成回调只执行一次 |
| `error` | failed | 展示脱敏失败原因，不写成功历史 |
| `timeout` | timeout | 明确显示超时，可重试；不是静默结束 |
| `cancelled`（连接仍在时） | cancelled | 显示取消，不伪装网络错误或成功 |
| 用户主动 abort | cancelled 或待收尾 | 本地退出 loading；不强求已断连接收到事件 |

保留现有 handler API，新增可选终态 handler 时有兼容 fallback；`timeout` 至少映射现有 onError。`onDone` 只表达成功完成，`onFinally` 表达释放资源，不能混用。终态去重；在流自然断开但没有合法终态时报告中断，不自行推断 completed。未知非终态事件可以前向兼容，不把所有扩展字段当错误。

用现有 `sseResponse(chunks)` helper 测跨 chunk、分行、末尾残留、重复终态、timeout 后 completed、abort 时 callback 仍等待；至少一条 composable 测试验证真实界面状态和历史没有被错误写入。

### R6C：进程崩溃后的运行回收

为运行记录新增可回收所需的租约/心跳字段和后续迁移，或明确由共享 runner 管理的等价持久状态。回收条件检查过期且非终态，采用条件写；不能因一次通知丢失把活跃运行结束。

删除会话对应 run 已清理时，回收任务不得重建记录。区分用户取消、预算超时和执行进程丢失的错误码；数据库不可用时保留可观测证据，恢复后扫描收敛。用户明确延期时只能验收 R6A/B，不能声称“所有运行均可收敛”。

## 9. R7：HTTP 响应头与代理边界

依赖：R0。两个小提交即可，避免与索引架构混改。

1. [http_exception_handler](D:/claudecode/Notebook/backend/app/core/failed_response.py:124) 构造 JSONResponse 时保留框架异常的合法响应头，至少覆盖 Retry-After、WWW-Authenticate。消息脱敏与 headers 保留分别测试；不要仅测试抛出的异常对象有 headers。
2. [Nginx](D:/claudecode/Notebook/front/nginx.conf:27) 当前大请求额度只给批量上传。为实际支持的单文件上传入口设置与单文件业务额度及 multipart 余量匹配的上限；头像继续独立限制，不把全站统一放宽为 210MiB。参考 [Nginx client_max_body_size](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size)。
3. 保留 `^~ /api/v1/` 私有图片路由优先级和无缓冲 SSE。经过 Nginx 验证 401/403/413/429/503 的响应体/必要头，不能只直连后端测成功路径。

验收：实际 ASGI HTTP 响应及 Nginx 入口均有合理 Retry-After；合法单文件与批量上传到达业务层；超限返回 413；私有图片不被静态 location 截获。

## 10. R8：真正可重复的全栈验收门禁

依赖：R0 的测试进程隔离；各业务用例最终依赖 R1—R7。基础设施可提前准备，最终 job 不允许提前标绿。

### R8A：隔离环境与测试入口

主要文件：[CI](D:/claudecode/Notebook/.github/workflows/ci.yml:232)、[Compose](D:/claudecode/Notebook/docker-compose.yml)、[全栈配置](D:/claudecode/Notebook/front/playwright.full.config.js)、[全栈 spec](D:/claudecode/Notebook/front/tests/e2e-full/auth-and-notes-fullstack.spec.js:19)。

1. 使用 `E2E_BASE_URL` 作为 Docker 验收的唯一浏览器/API 基址：`${base}/api/v1/health/live` 和 `${base}/user/...`。本地 Vite 模式如需直连，单独定义显式 direct 模式，不让 8002 的默认值悄悄覆盖 Docker 模式。
2. full 配置在启动时校验所需基址、账号、密码；既然选择 full 脚本，缺配置直接失败。普通 mocked UI 测试保持独立目录，不靠全局 skip 控制两者。CI 检查必需用例存在、执行数量非零且无跳过，参照 [Playwright 配置](https://playwright.dev/docs/test-configuration)。
3. 新建独立 `compose.acceptance.yml`（拟新增）或等价可验证配置，使用唯一 project、无固定 container_name、独立网络与命名卷、不挂开发数据。仅改 `-p` 不能解决现有固定容器名和固定 host port 冲突。project 隔离参照 [Docker Compose 文档](https://docs.docker.com/compose/how-tos/project-name/)。
4. Nginx 入口使用专用可配置端口；DB/Redis/Chroma 尽量不发布到主机。需要主机测试访问时使用随机回环端口并把实际地址交给 runner，不能默认使用本机已有 notebook-redis。
5. 启动顺序：基础设施健康→单独运行 MySQL/Alembic 与 Django 迁移并检查退出码→web/worker/beat/Nginx→就绪检查→种子用户→集成测试。worker 不得在迁移前消费新格式任务。
6. 确定性 LLM/Embedding 测试服务只替换外部模型边界。它应支持正常结果、延迟、挂起、错误和调用计数，使用独立测试配置且生产不可默认启用。真实 MySQL、Redis、Chroma、Celery、认证和 Nginx 不整体 mock。
7. 生产镜像不必安装 pytest。另建锁定依赖的验收 runner/测试 target，加载真实应用和测试目录。镜像 build、依赖安装失败就是失败，不用旧本地 venv 冒充干净镜像验证。
8. 若 CI 依赖安全审计通过才算完整交付，最终汇总显式包含 backend/Django 审计结果；JSON 产物用工具实际支持的 JSON 格式选项，不只给文本输出改 `.json` 后缀。

配置隔离与资源命名隔离同样必要：验收环境只加载显式测试配置，不继承开发 env_file、宿主遗留变量或镜像中的私有 `.env`。在任何迁移、种子写入、Redis 故障注入之前，启动检查解析后的 MySQL、缓存/黑名单 Redis、Celery broker/result backend、Chroma 地址及文件/日志目录，确认均属于本次专用环境允许清单。仅输出脱敏主机/资源标识，不打印连接串密码。故意注入一个开发地址或外部目录时检查必须失败；不能因容器名隔离就允许连外部开发数据库。

R8A 建议分三次提交：隔离 Compose 与解析后配置检查；确定性 provider 与锁定测试 runner；迁移/种子/执行/采集/清理脚本。每次都先过自己的隔离和故障用例，最后再串联。

安全清理：结束前先采集脱敏日志和报告，再核对 project 标签、容器/卷名称与本次创建清单，仅清理本次专用资源。不执行无范围 `docker compose down -v` 或系统 prune，不触碰已有开发/用户容器。清理失败单独记录，不隐藏。

### R8B：双服务撤销的真实契约

1. 保留 [test_jwt_contract.py](D:/claudecode/Notebook/backend/tests/test_jwt_contract.py:1) 作为键格式专项测试，但不要称它已经测试 Django 签发/撤销。直接手工写 Redis 不能替代真实 Django 路由。
2. 经 Nginx 注册/登录取得 Django token，先确认 Django 用户详情和 FastAPI 受保护读取都成功；调用 Django logout 后同一 token 在两个服务均为 401。
3. 改密码后旧 token 两服务均拒绝，新 token 两服务均成功；刷新接口若纳入契约，同样经真实服务测试，不假定前端已有 refresh action。
4. Redis 撤销存储故障用独立场景断开/停止本次专用 Redis 或故障代理：期望 503，恢复后重新验证撤销。命中黑名单场景只能接受 401，不能用 `401 or 503` 模糊通过。
5. 校验 TTL 与 token 剩余有效期、新旧键前缀兼容、不同用户不互相撤销。测试 token 不打印进 Actions 日志和 trace，报告只保存脱敏摘要。
6. 专项测试确需 `REDIS_TEST_URL` 时由该 job 明确注入专用地址，禁止依赖不存在的隐式 secret；集成 job 缺地址应失败。

### R8C：必需用例矩阵

| 场景 | 真实组件 | 必须断言 |
|---|---|---|
| 登录与笔记 CRUD | Nginx、Django、FastAPI、MySQL | 刷新后持久化、A/B 隔离 |
| 头像上传并重建应用容器 | Django、Nginx、媒体卷 | 重建后 URL 可访问且内容摘要一致；不是只做 restart |
| 单/批文档上传 | Nginx、文件存储、MySQL、Celery、Chroma | pending 到 indexed；失败真实；有效 chunk 数匹配 |
| 文档删除与重试 | MySQL、Chroma、worker | F02/F04 所有交错、旧入口、残余清理 |
| 私有图片 | Nginx 与真实鉴权 | A 可访问，B/匿名不能访问 |
| SSE 正常、超时、断连 | Nginx、ASGI、确定性 provider | 终态唯一、时限、DB 收敛、UI 提示 |
| 删除运行中的会话 | 两个 HTTP 客户端、MySQL | 后续完成不复活任何正文或孤儿记录 |
| 改密码/退出/Redis 故障 | Django、FastAPI、Redis | 新旧 token 与 401/503 精确语义 |
| schema 升级/兼容 | 实际 MySQL | 旧数据、新状态、危险回退保护 |

真实模型质量/费用评测不混入此门禁；使用真实模型要另有测试账号与预算。确定性 provider 的通过只证明工程链路，不证明检索答案质量。

## 11. R9：最终复验、交付记录和提交顺序

### 11.1 建议顺序与阶段门

| 阶段 | 任务 | 放行条件 |
|---|---|---|
| G0 证据稳定 | R0、R8A 的隔离脚手架 | unit 可重复，真实依赖测试可运行且不碰开发数据 |
| G1 数据安全 | R1、R2A—R2D、R3、R4 | 迁移可用，所有删除/迟到写/重复任务用例通过 |
| G2 用户与执行 | R5、R6、R7 | 身份隔离、总预算、取消和协议全链路通过 |
| G3 完整返修 | R8B/C、R9 | 当前候选 SHA 的全套门禁有真实运行证据 |

R4/R5/R6 的用例和实现可独立准备；R2 的 schema、统一执行、可见性和清理须按依赖集成。迁移编号、user store、SSE client、index service 等共享文件同一时刻由一个负责人修改。提交数由实际小批次决定，不以凑够某个数量作为交付指标。

### 11.2 验证入口

以下是待实施时运行的命令，不表示编写本方案时又执行了一遍测试。工作目录使用绝对路径；先按 R0 注入隔离配置。依赖同步会改变本地环境，应使用独立 checkout/验收环境并保留锁文件。

| 工作目录 | 命令 |
|---|---|
| `D:/claudecode/Notebook/backend` | `uv sync --locked --extra dev` |
| 同上 | `uv run ruff check app tests` |
| 同上 | `uv run mypy app/core app/schemas app/utils app/config` |
| 同上 | `uv run pytest tests -q --tb=short --cov=app --cov-report=term-missing --cov-fail-under=85 --ignore=tests/functional` |
| 同上 | `uv run pytest integration_tests -q --tb=short`（R0 新增目录并配置专用依赖后） |
| `D:/claudecode/Notebook/DjangoUserService` | `uv sync --locked --extra dev` |
| 同上 | `uv run ruff check .` |
| 同上 | `uv run python manage.py test apps.user apps.file --settings=DjangoUserService.test_settings -v1` |
| `D:/claudecode/Notebook/front` | `npm ci` |
| 同上 | `npm run lint` |
| 同上 | `npm run test:unit:coverage` |
| 同上 | `npm run build` |
| 同上 | `npx playwright test --project=chromium`（普通 mocked UI 配置） |
| 同上 | `npm run test:e2e:full -- --project=chromium`（R8 明确配置后） |

新增 integration 目录也要纳入 Ruff 检查；覆盖率阈值不降低。迁移、容器启动和资源清理命令由 R8 脚本读取经校验的专用 project，本文不提供能误删现有卷的通用清理命令。

### 11.3 每批证据模板

每个实施提交至少记录：任务 ID、基线/候选 SHA、修改文件、变更前复现、变更后用例、完整命令、隔离环境类别、退出码、通过/失败/跳过数量、覆盖率范围、日志/trace 位置、兼容/迁移/回滚结果和剩余缺口。

失败与跳过分开记录，禁止以下标记方式：

- `compose config` 成功 → Docker 构建/迁移通过。
- mocked UI 通过 → 全栈通过。
- 写入 Redis 键被拒绝 → Django 与 FastAPI 撤销契约通过。
- SQLite 串行 claim 通过 → MySQL 多 worker 安全。
- 收到 terminal 事件 → 浏览器已显示正确结果。
- schema 增加字段 → 历史数据、旧 worker、回滚兼容均已验证。

对第一轮交付记录追加带日期的复验更正，保留原提交历史：1B、4C、5A/5B 改为部分完成/返修中；3B 没有当前 SHA 证据时写未验证；7D 只承认文档路径修正。不要把“设计文档已写”称为用户已批准设计。

### 11.4 M1 复验清单

- [ ] F01—F10 各有稳定回归，代码及真实依赖测试均符合适用要求。
- [ ] 整套后端两次可重复运行无失败；跳过项逐条解释且不包含必需集成项。
- [ ] 同一候选 SHA 的前端、Django、后端、审计、迁移和全栈 job 均有产物。
- [ ] 删除覆盖单条、批量、文件名/MD5/旧入口及所有读取路径。
- [ ] 旧代际不可见，迟到外部写仍有清理依据；未确认物理清理不伪报完成。
- [ ] 会话删除与完成写入串行化，step/feedback/history 不复活。
- [ ] A/B 切换、旧 401、密码轮换、草稿 debounce、注销 503 全部有行为测试。
- [ ] SSE 超时和取消可见且终态一致，业务预算与清理预算有实测。
- [ ] 旧版进程切换、schema 兼容和必要回退已在副本演练。
- [ ] 专用测试资源已清理，未触碰真实用户数据；剩余工作准确登记。

## 12. 原方案剩余范围与需要用户确认的事项

### 12.1 不得遗漏的原方案工作

| 原任务 | 本文处理 | 完整验收要求 |
|---|---|---|
| 5B 统一执行、有界上传、后台索引、租约/版本 | 纳入 R2/R3 | 不再只交付固定 ID 和布尔 claim |
| 4C 心跳/过期运行回收 | 纳入 R6C | 如延期，明确仅完成超时/取消子项 |
| 5A 会话/历史分页 | 独立后续任务 | 游标稳定、有上限、前端加载更多和兼容验证 |
| 6 Cookie/CSRF | 保留原方案独立阶段 | HTTPS 入口、双服务 CSRF、旧前端兼容；本文不默认实施 |
| 7A 组件拆分与体验 | 仅补本轮故障相关行为测试 | 全面拆分、键盘/焦点/移动端等仍需另交付 |
| 7B 性能 | 记录新过滤/上传的基线与成本 | 原方案关键路径 p50/p95、样本量、前后可比报告仍需完成 |
| 7C 检索质量/多进程运行配置 | 不以确定性 E2E 替代 | 固定评测集、真实模型授权预算、配置版本跨进程收敛 |
| 7D 监控、备份和恢复 | 返修增加必要状态/错误观测 | MySQL、文件、Chroma、媒体及撤销 Redis 的恢复策略和隔离演练 |
| 4B 生成并发和成本预算 | R6 约束实际调用并发 | 用户级并发、模型开销指标和业务额度仍按原方案补齐，不因限频已实现就勾整批 |

7D 后续至少拆为三项：指标与告警（队列最老年龄、重试、超时/取消、清理积压、429/503、撤销失败）；一致恢复步骤；隔离恢复演练。恢复时先恢复 schema/数据库与文件和媒体，再按对应恢复点接入 Chroma并核对版本/数量；Redis 撤销信息丢失需要会话处置策略。记录恢复点、校验、RPO/RTO 和责任人，不能凭“有卷”推断“可恢复”。

### 12.2 默认建议与明确边界

- 本轮建议采用代际发布方案；若选择更简单的受保护停写重建，需另写维护窗口、旧 writer 完全停止证明和不可用期间的 UI，不能同时宣称零中断与多 worker 自动接管。
- 独立 Chroma 服务是部署变更建议，需确认目标环境、容量、迁移窗口；未确认前仍可完成模型/仓库设计、单元与隔离集成测试，但不能标记生产拓扑验收通过。
- tombstone 保留期、历史孤儿数据处理、备份保留周期、RPO/RTO、真实模型费用和公网 Cookie 迁移时间由用户/部署负责人确认。不要为消除待办擅自删除数据或轮换真实密钥。
- 不自动推送 main、改分支保护、建远端 PR、发布、重建全量向量库或操作真实账号。后续“实施此方案”也不自动授权这些额外动作。

## 13. 参考资料与阅读规则

本方案依据当前仓库的实际函数、锁文件与上一轮隔离验收结果编写；外部文档只用于核验库行为。各阶段实施者必须重新阅读所用 API 的锁定版本源码/官方文档，记录差异，不按名称猜参数。

- [Python 3.12 asyncio 任务、取消与超时](https://docs.python.org/3.12/library/asyncio-task.html)：R6 执行预算与收尾。
- [AnyIO 取消与超时](https://anyio.readthedocs.io/en/stable/cancellation.html)：ASGI 已取消作用域内的有界收尾。
- [Alembic 操作参考](https://alembic.sqlalchemy.org/en/latest/ops.html)：R1/MySQL 列变更；在线文档版本可能高于项目 1.18.5。
- [SQLAlchemy 2.0 ORM DML](https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html)：条件 UPDATE/DELETE；数据库真实交错仍须集成测试。
- [Celery 任务与重试](https://docs.celeryq.dev/en/stable/userguide/tasks.html#retrying)：重投不替代应用的数据库状态机。
- [Chroma client-server 模式](https://docs.trychroma.com/docs/run-chroma/client-server)：仅支持部署建议的可行性，不证明本项目已迁移。
- [Playwright 配置](https://playwright.dev/docs/test-configuration)、[Compose project 隔离](https://docs.docker.com/compose/how-tos/project-name/)、[Nginx 请求体限制](https://nginx.org/en/docs/http/ngx_http_core_module.html#client_max_body_size)：R7/R8 验收入口。

下一步实施起点：R0 固定复现与隔离测试，R1 补迁移，R2A 修删除失败重试；继续完成 R2B—R2D 前，不把“删除一致性”重新标成已通过。
