# 第二轮复验问题与补充实施方案

日期：2026-09-04。审查基线：`288f8fd`，覆盖 `ca028fb`、`97c6196`、`44c9b57`、`da3225f` 及交付记录提交。

本文是**审查结果与待执行方案，不是修复交付记录**。本次仅新增本文；没有修改业务代码、执行迁移、操作真实 MySQL/Chroma 数据或提交 Git。

承接文件：[原整改方案](D:/claudecode/Notebook/docs/REMEDIATION_PLAN.md)、[返修方案](D:/claudecode/Notebook/docs/REMEDIATION_REWORK_PLAN.md)、[实施记录](D:/claudecode/Notebook/docs/REMEDIATION_DELIVERY.md)。原 R0—R9 编号及未完成要求继续有效；本文 S01—S07 是本次复验发现，不另起一套替代验收标准。

## 1. 结论与证据边界

结论：本轮确实补上了若干单点防护，但**不能接受“P1 F01—F07 全部修复”这一总体表述**。删除、会话写入和总预算仍有可复现的缺口；其中还包括 F09 与删除逻辑组合产生的新回归。下一步不能只做 R2C-1 而略过这些基础问题。

可以保留的成果：006 增加 `deleting` 且有危险回退检查；007 增加 tombstone；单条删除重试重新尝试向量清理；发布/失败写回增加状态条件；F05 增加 await 后身份复核；SSE 接收 timeout/cancelled；异常响应头保留；fullstack 缺账号从 skip 改成失败。这些成果不等于所属整个 R 项已验收。

### 1.1 本次实际执行

| 检查 | 本次结果 | 能证明什么 / 不能证明什么 |
|---|---|---|
| 后端 6 个定向测试文件 | **95 passed**，约 5 秒；一条 pytest 缓存目录写权限警告 | 覆盖现有断言；不能证明缺失的并发与启动接线场景 |
| 前端 `npm run test:unit` | **71 passed**，9 个文件 | 现有前端单测通过；不是 composable 级竞态或真实浏览器验收 |
| 索引故障注入 | 5 个负向场景均观察到缺陷，详见下表 | 实际仓库/服务/worker 异步函数 + SQLite 内存库，向量及 MD5 服务为桩；不是 MySQL/Chroma 实测 |
| 会话初始化探针 | 模块实例已初始化，但路由使用的代理仍为 `None` | 实际初始化函数和代理；没有启动完整应用或连接用户数据库 |
| 会话及预算隔离探针 | 已删 ID 仍进入两种 Agent 路由；迟到消息残留；检索和 judge 均可越过预算 | 路由函数直接调用、内存库与可控假 provider；不是 HTTP 全栈或真实 MySQL 并发 |
| 用户报告的 MySQL 8.0.12 / 后端 993 / Django / Ruff / mypy | **保留为本轮交付方报告，本文未重新执行** | 不否定报告，也不把转述写成本次独立复验结果 |
| 当前 SHA 的 Actions / Compose / 真实 Chroma 并发 | **本次未执行、未核验运行记录** | 不能标绿；已有 workflow 不等于对应 SHA 运行通过 |

后端定向命令：在 `backend` 中运行 `.venv/Scripts/python.exe -m pytest tests/test_index_task.py tests/test_document_index_service.py tests/test_database_session_manager.py tests/test_agent_timeout_cancel.py tests/test_agentic_rag_regressions.py tests/test_failed_response.py -q --no-cov`。运行前显式设置测试 ENV、测试密钥、不可达的外部数据库/Redis 地址与本地模型类型；各测试自行使用内存数据库和桩。此处 `--no-cov` 只用于定向诊断，不改变全量 CI 覆盖率门禁。

隔离探针没有作为业务测试提交。本方案要求实施时把下述场景固化为常驻回归，并保存修复前失败、修复后通过的证据。探针展示的是可触发的逻辑缺陷，不代表真实用户数据已经发生相同事故。

### 1.2 问题清单

| 编号 / 级别 | 当前问题与观察 | 归属与性质 |
|---|---|---|
| S01 / P1 | 初始化后 `module_initialized=true`，但 `proxy_is_none=true`；正常会话读写接线失效 | R4 前置问题；本次新发现的旧缺陷，并非这 5 个提交引入 |
| S02 / P1 | worker 领取后暂停 → 删除置 `deleting` → worker 抛错 → 最终变为 `pending_index`；另一个探针确认缺文件分支把 `deleting` 改为 `index_failed` | F04/F09、R2/R3；新重试路径回归 + 旧旁路 |
| S03 / P1 | 部分向量写入失败后回 pending，单删返回成功、SQL 无行、向量删除调用 **0 次**、向量桩仍残留 1 条；批删向量失败也返回成功且删 SQL | F02、R2A/R2D；组合回归 + 批量契约未完成 |
| S04 / P2 | `_sync_index` 发布被拒后返回 `indexed`，数据库实际为 `deleting` | 条件发布改造的响应契约回归；R2C/R3 |
| S05 / P1 | tombstone ID 仍能创建运行并调用 provider；写入先读、删除先提交、写入后提交时仍留下 2 条消息与标题；重复删除未清掉残留 | F03/R4；防复活只修了顺序调用，未完成原子隔离 |
| S06 / P1 | 检索预算 30ms 实际 500ms 才 timeout；另总 deadline 3.1 秒，主回答立即返回、judge 用时 3.5 秒，最终仍 `timed_out=false` | F06/R6A；检索已加等待上限但扩大剩余预算，judge 也未闭环 |
| S07 / P2 | 007 的 downgrade 无条件删除 `deleted_at`，会丢失 tombstone 判定依据 | R4 回退安全；源码确认，未执行真实 downgrade |

S03 的向量残留计数来自桩，不冒充真实 Chroma 检索结果。S05 的路由探针为了单独验证 tombstone，先将 S01 的代理正确接到管理器；两个缺陷需要分别修复，不能因为一个先报错就认定另一个不可触发。

## 2. 阶段 0：资料发现与失败用例固化

本次已按计划技能分工核对索引和会话路径，主审完成源码复核与方案整合。下一次实施开始时先确认 HEAD 与工作区变化；如果基线变化，重跑相关负向场景，不直接套用本文行号。

### 2.1 实施者必须阅读的已有接口

| 已有接口或模式 | 源码与可复制的测试位置 |
|---|---|
| `init_database_session_manager()`；`SessionManagerProxy.session_manager` | [初始化](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:231)、[代理](D:/claudecode/Notebook/backend/app/services/__init__.py:9)、[lifespan](D:/claudecode/Notebook/backend/main.py:45) |
| `claim_for_indexing(doc_id) -> bool`；`publish_indexed(doc_id, chunk_count) -> bool`；`mark_index_failed_if_indexing(doc_id, error_message) -> bool` | [仓库](D:/claudecode/Notebook/backend/app/repositories/document_index_repository.py:121)、[worker fixture](D:/claudecode/Notebook/backend/tests/test_index_task.py:36) |
| `_sync_index(document_id, user_id) -> DocumentIndexStatus`；`delete_index_record(document_id, user_id, delete_file=True)`；`clean_user_index_records(user_id, space_id=None)` | [索引服务](D:/claudecode/Notebook/backend/app/services/document_index_service.py:309)、[服务 fixture](D:/claudecode/Notebook/backend/tests/test_document_index_service.py:23) |
| `add_message(session_id, user_id, user_message, assistant_message)`；`clear_session(session_id, user_id)` | [会话管理器](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:92)、[现有顺序删除测试](D:/claudecode/Notebook/backend/tests/test_database_session_manager.py:141) |
| `AgentRunRepository.create_run(...)`；`update_run_terminal(...) -> bool`；`add_step(...)`；`add_feedback(...)` | [运行仓库](D:/claudecode/Notebook/backend/app/repositories/agent_run_repository.py:29)、[路由 HTTP fixture](D:/claudecode/Notebook/backend/tests/test_router_agent.py:38) |
| `AnswerGenerator.generate(query, evidences, include_citations=True, deadline=None)`；`_judge_answer(query, context, answer)` | [生成器](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py:50)、[超时测试](D:/claudecode/Notebook/backend/tests/test_agent_timeout_cancel.py:19) |
| 旧文件名删除、按用户/空间清理 | [知识路由](D:/claudecode/Notebook/backend/app/router/knowledge_router.py:130)、[路由测试](D:/claudecode/Notebook/backend/tests/test_router_knowledge.py:74) |

本文后续出现的 `get_session_manager`、执行结果类型、条件重试接口、共享完成服务均是**拟新增接口**，不是已有函数。优先扩展上述 fixture 和事务模式，不伪造 SDK 方法。

事务边界参考 [SQLAlchemy 2.0 事务说明](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)：统一提交或整体回滚必须由明确的事务边界控制。重试投递参考 [Celery 5.6 任务重试说明](https://docs.celeryq.dev/en/stable/userguide/tasks.html#retrying)：`retry()` 重新投递任务，不替应用维护数据库状态。仍须核对项目锁定版本；不因在线文档版本变化顺带升级依赖。

### 2.2 测试隔离规则

1. 按 R0 固定测试配置。启动接线测试使用独立进程，避免其它测试预先赋值导致 S01 被掩盖；不要把 `app.services` 或目标代理整体 mock 掉。
2. unit 先复制现有 SQLite fixture；并发交错用 Event/barrier/deferred Promise，不靠随机 sleep 撞时序。超时用例允许可控延迟，但需有合理调度容差。
3. MySQL/Chroma 集成测试按原 R8A 放在独立进程与隔离环境；写入前核验解析后的数据库名、端口、容器 project 和集合标识属于测试环境。不要直接使用开发 `.env`，也不要复用用户数据库。
4. “有条件 UPDATE”的源码字符串检查只能作为辅助；必须实际调用完整 worker/service/router，检查状态、投递、残余资源及客户端结果。

验收：S01—S06 至少各有稳定失败用例；S07 有升级/降级保护测试设计。缺少依赖时登记阻塞，不能让必需集成 job 全部 skip 后标绿。

## 3. 阶段 A：修复正常会话服务接线（S01）

优先级最高，可单独实施，不依赖 generation 迁移。

### 修改方法

1. 先读 [services 导出](D:/claudecode/Notebook/backend/app/services/__init__.py:9)：当前 `from ... import database_session_manager` 捕获初始化前的 `None`；后续初始化仅重新绑定定义模块的变量，代理返回的旧绑定不会自动更新。
2. 在定义模块中提供拟新增 `get_session_manager()`，每次读取该模块当前实例；未初始化时给明确异常，不返回 `None`。代理只调用 getter，不再保存实例值的导入快照。也可统一改为依赖注入，但本批不为此重构所有服务。
3. 检查 [普通聊天服务](D:/claudecode/Notebook/backend/app/router/chat_service.py:17)、[Agent 写历史](D:/claudecode/Notebook/backend/app/router/agent_router.py:139) 和旧 Agent 调用者，统一获取方式；启动完成前必须验证实际消费者拿到同一实例。
4. 不把 `None.add_message` 或数据库持久化失败继续吞成正常回答。具体终态契约按阶段 D 实施；至少服务未就绪时拒绝进入处理，而不是已调用 provider 后才发现管理器缺失。

### 验收与反模式

- 新进程：导入代理 → 执行真实初始化函数 → 从消费者获取实例，断言非空且身份一致；测试不得手动给包级别变量赋值“修复”接线。
- 正常会话创建、问答、历史读取、列表、删除走真实服务组合；确认两条 ChatMessage 真实落入隔离数据库，而非只断言 HTTP 200。
- 增加 lifespan smoke：允许替换外部基础设施，但保留真正的管理器初始化和代理。已有 [Agent 路由成功测试](D:/claudecode/Notebook/backend/tests/test_router_agent.py:157) 扩展持久化断言。
- 不删除历史写入功能来消除异常；不把未初始化当作可以静默运行的降级模式。

## 4. 阶段 B：收紧索引状态迁移与结果契约（S02、S04）

先完成可独立验证的防覆盖修补，再衔接 R2B 的执行身份。不把仅检查 status 称为完整代际隔离。

### B1：所有入口都服从条件迁移

1. 从现有 `claim_for_indexing` 的条件 UPDATE/rowcount 模式扩展重试状态迁移。错误、重试次数、下一状态必须在一个短事务内由仍持有执行权的一方更新。
2. 修复 [worker 异常路径](D:/claudecode/Notebook/backend/app/tasks/index_task.py:152)：不得忽略失败写回的 False，随后又无条件改 pending；失去执行权、已删除或 deleting 时直接退出，不投递重试。提交失败先 rollback，未确认状态提交成功不能继续发送任务。
3. 修复 [缺文件分支](D:/claudecode/Notebook/backend/app/tasks/index_task.py:70)：先取得执行权，再条件写失败；迟到或重复任务不得在 claim 前写终态。
4. 同步审计 [人工重试](D:/claudecode/Notebook/backend/app/services/document_index_service.py:413) 与 [Beat 补偿](D:/claudecode/Notebook/backend/app/tasks/index_task.py:246)。二者目前也是先读状态再无条件改写。条件更新成功并提交之后才能投递；提交成功但 broker 失败由数据库补偿发现，不能反向先投递后提交。
5. 按 R3 统一持久化重试预算，区分执行失败计数与重复消息。后续引入 `attempt_id/generation` 后，将本批条件从“预期状态”升级为“owner + 文档 + 预期状态 + 执行身份”；防止旧 worker 把新 worker 的 indexing 当成自己的。

验收：worker 已领取后删除获胜，再让 loader / 向量写 / MD5 写失败，始终不得离开 deleting；缺文件迟到消息同样不覆盖；人工重试与 Beat 各做删除交错；实际投递第二次任务并执行成功，不能只证明仓库再次 claim 返回 True。数据库提交失败不投递，broker 失败仍可补偿，重试上限不因新 Celery 消息重置。

### B2：条件发布结果必须传播到调用者

1. [同步执行](D:/claudecode/Notebook/backend/app/services/document_index_service.py:375) 当前 publish False 后仍返回 INDEXED。改成仅在 publish 成功且提交成功后返回已发布结果；False 时通过新事务读取或显式刷新获取当前状态，避免复用 identity map 中的旧对象。
2. 可先修正返回值，随后与 R2C 统一成拟新增执行结果：`published / stale / deleting / not_found / failed`。上传与人工重试适配这个结果；失去发布权时不得提示“已完成索引”。异步 worker 的日志、指标也只把真正提交的发布记为成功。
3. 空文档、空切片和异常失败写回同样检查条件更新结果。不要仅让成功分支正确、失败分支继续掩盖状态冲突。

验收：执行器写向量前后分别插入删除；publish 返回 False 时服务、上传和 reindex 响应都不包含 indexed 成功。数据库中的状态、响应、日志一致。现有 [仓库发布测试](D:/claudecode/Notebook/backend/tests/test_document_index_service.py:460) 保留，再增加执行完整 `_sync_index` 的测试。

## 5. 阶段 C：真正统一删除入口与残余清理（S03）

### 修改方法

1. 不再用“当前是 pending”推断“从未写过向量”。F09 之后 pending 可能已有部分向量或完整向量但 MD5/SQL 失败。短期至少对具备资源标识的 pending 同样清理；只有能够证明没有产物、没有在途写者时才允许跳过。长期由 R2B/R2D 的 manifest、generation、清理记录提供依据。
2. 单删、批删、按文件名、MD5 和空间入口调用同一删除编排。批删只负责枚举和汇总，不再拥有一套“捕获异常然后删 SQL”的实现；具体边界继续遵守原 R2A。
3. [批删](D:/claudecode/Notebook/backend/app/services/document_index_service.py:623) 中 VectorStoreService 初始化失败、向量删除失败、MD5 清理失败、文件删除失败均保留可恢复依据；“资源原本不存在”才可视为幂等成功。文件 helper 返回可判定的结果，不以写日志代表成功。
4. 每条记录独立提交状态与清理结果，返回 `deleted / deleting / failed` 及计数、可重试的稳定错误码。只把确认清理完成的条目算入 deleted_count；剩余项可重试。不可让一条失败造成其它项已经被删除但整批状态丢失。
5. [清理路由](D:/claudecode/Notebook/backend/app/router/knowledge_router.py:130) 必须传播逐项结果；接受删除但仍清理中与已删除完成按原方案区分 202/200。前端保留清理中的条目和重试提示，不能看到统一 success 就全部移除。
6. [旧文件名入口](D:/claudecode/Notebook/backend/app/router/knowledge_router.py:215) 一旦找到 v2，删除失败必须返回该失败/进行中状态，不能继续走 v1；查询本身失败也不能当“没有 v2”。保留 `delete_documents=False` 原合同，不能误删文档来实现只删除 MD5 记录。
7. 对仍可能写入的 worker，删除请求可先隐藏文档并保留 deleting；在无法证明旧写者结束前，不得物理删除最后一份清理依据或宣称清理完成。仅清理一次向量不等于 R2D 已闭环。

### 验收与反模式

- 重用 [单删失败恢复测试](D:/claudecode/Notebook/backend/tests/test_document_index_service.py:426)，增加“部分 upsert 后异常”和“全部 upsert 成功、MD5 失败”两种 pending 残余场景；删除确实调用向量清理，失败时 SQL/清理任务仍在。
- 批量两个条目，一成功一失败：返回逐项结果及正确计数；只重试失败项，恢复后真正收敛。分别注入初始化、向量、MD5、文件和 SQL 提交失败。
- 文件名、MD5、用户级清理、空间级清理均走路由回归；验证失败不回退、空间与用户隔离、`delete_documents=False` 不改变含义。
- 真实 Chroma 另验在途写与删除交错、查询不可见和最终无残余。不能用桩的 delete 调用次数代替真实清理证据，也不能只在枚举里再加一个 PENDING 就标记完整关闭。

## 6. 阶段 D：会话删除的原子屏障与回退保护（S05、S07）

依赖：阶段 A；并发 SQL 结论最终由隔离 MySQL 验证。现有 tombstone 列继续复用，不再重复加列。

### D1：创建、写入、完成、读取一起收紧

1. 两个 [Agent 查询入口](D:/claudecode/Notebook/backend/app/router/agent_router.py:91) 都补 deleted_at 校验，但不能止于新增一条 if。创建 run 与验证 active 会话必须在同一短事务内，以“父会话 → run → 子记录”的固定顺序锁定，删除方采用同一规则。
2. [add_message](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:100) 当前先无锁读取，再另行 commit；tombstone 并不能阻止检查之后发生的删除。把校验和消息/标题写入放进同一父会话锁保护的事务。跨进程不能靠当前未使用的 `asyncio.Lock` 实现隔离。
3. 按 R4 新增共享“提交运行终态并追加历史”事务服务：同一事务检查会话 active、运行归属和可写终态；条件写成功才追加消息；统一 commit。禁止先提交 completed，再独立调用会话管理器并吞掉失败。
4. `create_run`、`add_step`、`add_feedback`、终态提交均进入同一屏障。运行详情/列表也排除关联已删除会话，避免通过 run 接口读回已删内容。明确现存 `session_id=None` 的运行策略，不无意改变其访问合同。
5. 删除已是 tombstone 的会话也应能幂等清理残留子记录与敏感字段；当前 [clear_session](D:/claudecode/Notebook/backend/app/services/database_session_manager.py:170) 直接跳过，使历史竞态残留无法再次清除。最小 tombstone 不保存标题、消息正文或 metadata。
6. 共享服务返回明确提交结果；流式/非流式按结果决定最终响应。删除、取消或超时先获胜时不能继续发 completed、追加历史或返回成功快照。对在途计算发取消通知只是节省工作量，最终屏障仍由数据库保证。

### D2：007 的安全回退

1. [007 downgrade](D:/claudecode/Notebook/backend/alembic/versions/007_chat_session_deleted_at.py:29) 不能在已有 tombstone 时直接丢弃列。优先采用保留列的应用回滚/前向修复，不回到会自动重建旧 session_id 的应用版本。
2. 参照 [006 的拒绝模式](D:/claudecode/Notebook/backend/alembic/versions/006_document_index_deleting.py:43)，为 007 的 downgrade 增加存在 tombstone 时拒绝执行的检查；不改已执行的 upgrade、revision 或历史数据。单独记录该安全修订。如果迁移文件受不可变校验约束，则明确禁止执行该 downgrade，先解决发布/校验策略；不能只增加 008 就宣称旧 007 downgrade 已安全。
3. 降级检查与 DDL 必须处于受控维护窗口：阻断所有相关写入口，停止旧应用/worker 并排空在途事务，再检查 tombstone 和执行 DDL。MySQL 的相关 DDL 会隐式提交，普通事务中的先 SELECT 后 DROP 不能保证这段检查原子性；无法证明已停写时禁止降级。006 的同类数据检查也适用这一运维约束。依据：[MySQL 隐式提交语句](https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html)。
4. 有无 tombstone、回滚应用兼容性分别验证；有 tombstone 时失败须发生在 DDL 前，不能自动删行、清标记或重新激活会话来“通过回退”。不在本轮顺带批量清理真实历史数据。

### 验收矩阵

- 正常启动、正常新会话、正常写历史通过；不是依赖测试手动替换代理才可运行。
- 已删 ID 请求 stream/query：HTTP 404，provider 调用数为 0，不新增 run；未删除的跨用户会话仍拒绝访问。
- 删除获胜：写方取得父会话锁之前暂停，删除提交后恢复写方；写方重新校验并拒绝写入，消息、标题、run、step、feedback 均不残留。
- 写方获胜：写方已持父锁时暂停，确认删除等待该锁；释放写方、允许提交，再让删除完成，最终所有内容清空。不能让测试一边持写方锁，一边等待删除提交后才放行写方，制造测试自身死锁。
- run 创建、feedback、step、终态各有独立交错测试；重复 completed 仅产生一组消息。运行更新 False 或事务失败时 HTTP/SSE 不报已完成。
- 重复删除 tombstone 能清理人工种入的测试残留；正常列表、详情、run 读取都不可见。
- 真实 MySQL 使用两条独立连接测试锁顺序、回滚与死锁重试；不以 SQLite 探针证明 MySQL 锁语义。

## 7. 阶段 E：总预算最后一段与失败终态（S06）

### 修改方法

1. [检索等待](D:/claudecode/Notebook/backend/app/agentic/graph.py:148) 使用 `max(remaining, 0.5)`，会把不足 0.5 秒的剩余预算放大。删除这个下限；剩余耗尽直接退出，正剩余严格作为等待上限。本次真实 graph 加挂起检索桩：30ms 预算约 500ms 才 timeout，证明仅增加 wait_for 还不够。
2. [judge 入口](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py:108) 只检查剩余至少 3 秒，但 [实际调用](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py:169) 仍固定允许 15 秒。给 judge 显式传递共同 deadline，等待上限不得超过剩余预算与自身上限的较小值；预算不足时跳过可选评分。
3. 按原 R6A，整个执行链有外层业务 deadline，而不是每阶段各自重新获得时间。覆盖 planner、retrieve、改写、主生成、judge 和完成前检查。同步 SDK/线程任务另设自身超时；取消等待不等于远端请求或后台线程已经停止。
4. 建议维持原验收口径：整个业务执行超过 deadline 后不再提交 completed；提前跳过 judge 可以在期限内正常完成。若产品要改成“答案按时完成、评分异步”，必须明确新合同并独立设计异步评分，不继续阻塞响应再称为按时完成。
5. [生成异常分支](D:/claudecode/Notebook/backend/app/agentic/answer_generator.py:128) 当前将普通 provider 异常转换成道歉文本且 `timed_out=false`；[graph](D:/claudecode/Notebook/backend/app/agentic/graph.py:241) 随后可能发 completed。补结构化 failed/error，展示文案与业务成功标志分离；此项为原 R6 未完成内容，不是本次新增提交造成。
6. R6B 的终态唯一性、断流与取消收尾继续实施；本轮 timeout/cancelled 映射 onError 只完成可见性，不等于整个协议闭环。业务预算与有界清理预算分开记录，不在取消后无限等待 SQL。

### 验收与反模式

- 主生成很快、judge 挂起或超过剩余预算：在规定容差内超时/取消，不出现晚到 completed。该测试必须进入 judge，不能只用小于 3 秒的预算导致评分被跳过。
- 检索预算分别取小于 0.5 秒与正常值，断言等待参数不超过实际 remaining、实际耗时不超过预算加明确调度容差。当前测试给 0.5 秒预算却只断言 `elapsed < 5`，应收紧；不能用十倍预算的容差证明整体上限。
- 分别让检索、规划、改写、生成、评分停顿；两种路由都遵守同一 deadline。使用注入时钟和事件辅助断言，并保留一项真实计时集成测试。
- provider 抛错产生 failed 而非“道歉答案成功”；timeout/cancelled/error/completed 不重复互相覆盖，前端不保存失败为成功历史。
- 按原 R6 复用 producer/事件队列与 AnyIO 有界收尾方案；不靠放大总预算、调整测试阈值或只新增另一个局部 wait_for 关闭整体问题。

## 8. 阶段 F：原方案剩余工程，不得缩减为几个可选优化

前述阶段修复立即可触发的问题，但不替代下面的完整工程。尤其 R2B 应在 R2C 执行身份改造之前完成；不能跳过代际/租约设计直接宣称统一服务已经保证删除一致性。

| 原任务 | 仍需实施的内容 | 必须看到的验收证据 |
|---|---|---|
| R2B—R2D | 执行 generation/attempt、发布指针、manifest、持久清理状态；统一所有执行路径和所有读取路径 | 重复/迟到 worker、N→M 旧尾部、删除后在途写、同名重传；只读 active generation；旧产物最终可清理 |
| R3 | 数据库统一重试预算、提交后调度与失败补偿、有界分块上传、生产后台索引 | 大文件内存界限、消息丢失/重复、worker 崩溃、重试耗尽；不能只验证第二次 claim |
| R5 | store/HTTP/草稿/composable 身份代次与凭据轮换区分、旧 401 和迟到响应隔离、撤销失败可见语义 | A→退出→B 的可控交错，旧响应不清掉 B、不写入 B 历史/草稿；F05 单点之外的完整 fixture |
| R6B/R6C | SSE 协议闭环、取消收尾、心跳/过期运行回收 | 断流与断进程后最终收敛，无永久 running，无迟到成功覆盖 |
| R7 | 除响应头外的单文件/批量代理上传边界 | 经 Nginx 的实际限制与响应，不能仅验证后端校验 |
| R8 | 独立 project/端口/测试库/集合、确定性 provider、真实依赖与双服务撤销矩阵 | 当前 SHA 的迁移、并发、头像重建、恢复演练与产物；Django 等探活也核对统一入口/直连模式的边界 |
| R0/R9 | 导入/mock 作用域加固、当前 SHA 证据、完成标记纠正 | 连续全量一致；真实集成不继承 unit 的模块桩；测试命令、退出码、跳过原因与 artifact 绑定提交 |

完整设计沿用 [返修方案 R2—R9](D:/claudecode/Notebook/docs/REMEDIATION_REWORK_PLAN.md:121)。原方案中的 Cookie/CSRF、分页、性能、评测、前端拆分仍按原里程碑登记；需要产品或部署决策的项目保留确认点，不混入本轮安全修补。

## 9. 提交顺序、最终验证与交付口径

### 9.1 建议批次

1. 失败回归与隔离配置（阶段 0），随后独立修复会话服务接线（A）。
2. 索引条件迁移（B1）与执行结果传播（B2）；分别有完整执行路径测试。
3. 删除编排及旧入口/前端响应适配（C）。只在有最终清理证明时标“已删除”；其余保留进行中。
4. 会话事务屏障/运行完成（D1），回退保护单独提交（D2）。
5. 总 deadline、失败结果与终态协议（E）。
6. R2B → R2C 各子批 → R2D → R3，完成执行身份、读取可见性和恢复闭环；前面的修补必须接到新统一服务，删除重复实现。
7. R5、R6C、R7 剩余项和 R8 全矩阵；最后 R9 当前候选 SHA 复验。

会话与索引可由不同实施者并行，但 schema revision、共享终态合同与最终门禁需统一协调。每个实现提交范围尽量单一；不将十个问题和证据文档全部压成一批。

### 9.2 完成清单

- [ ] S01：真实启动接线有效，普通历史 CRUD 与 Agent 历史落库通过。
- [ ] S02：全部索引状态入口不覆盖 deleting；重试与执行身份、事务提交和投递保持一致。
- [ ] S03：所有删除入口失败保留、pending 残余清理、部分结果与恢复闭环通过。
- [ ] S04：实际发布失败不返回 indexed，上传/人工重试/worker 的结果合同一致。
- [ ] S05：所有会话写入和读取入口采用同一屏障，真实 MySQL 双连接交错验证通过。
- [ ] S06：整个执行链与最终评分受共同预算控制，provider 错误不成功落库。
- [ ] S07：有 tombstone 时危险回退在 DDL 前被拒；安全回滚路径写明并测试。
- [ ] 原 R2B—R2D、R3、R5、R6B/R6C、R7、R8 未完成内容分别验收，不以本表替代。
- [ ] 后端原覆盖率门禁、连续两次全量、视觉单跑/全跑、前端单测/lint/build、Django、Ruff、既有 mypy 门禁全部通过。
- [ ] 当前候选 SHA 的必需 CI 和真实集成产物齐全；零用例/缺配置/必需用例跳过均不视为通过。
- [ ] 已部署旧应用/旧 worker 的兼容与停用步骤明确；迁移、回滚、数据恢复没有未授权的生产操作。

### 9.3 交付记录如何更正

不要抹掉原始实施记录。下一轮在末尾追加带日期及候选 SHA 的复验段：

- R2A/F02：改为“单删顺序重试已修；批量/旧入口/重试后 pending 残余尚未闭环”，对应 S03。
- F04/F09：改为“成功发布增加条件；失败、重试和旁路仍可覆盖删除状态”，对应 S02；R3 不仅剩上传。
- R4/F03：改为“tombstone 与顺序追加拒绝已实现；服务接线、跨入口及并发提交待修”，对应 S01/S05/S07。
- F06/R6A：改为“检索 wait_for 已实现；judge/全链 deadline 与失败语义仍需补齐”，对应 S06；不是只剩心跳。
- F05/F08/F10/F07：保留已验证的具体单点，不将其自动升级为 R5/R6B/R7/R8 全部完成。

每批记录：改动 commit、对应 S/R/F、复制/扩展的模式、修复前失败证据、修复后命令与退出码、真实依赖版本、数据隔离标识、产物位置、未验证项和回退条件。最终结论必须区分“代码已改”“unit 已过”“真实集成通过”“仍未完成”。

下一步建议：**先固定 S01—S06 负向用例，优先修会话接线与索引状态/清理，再完成会话原子屏障和整体预算；随后继续代际发布工程。** 不能因为现有单测全绿，就跳过已经复现的基础状态与数据一致性缺陷。
