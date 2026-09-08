# 下一轮整改实施方案：恢复闭环、读取统一与存量兼容

日期：2026-09-08。本文可直接交给后续实施者执行，不限定模型、工具或协作方式。本文是待实施方案，不是实施记录，也不表示批准操作业务数据库、提交或推送。

## 1. 本轮目标与基线

下一轮先解决“执行结果不明后如何安全恢复”，再完成 P2 读取统一、P3 存量兼容，最后补 P4 真实集成证据。不重做已经通过的取消、迟到写入清理和发布确认单点，也不开始无边界架构重构。

当前基线是 **HEAD `b0c3ec6` 加现有未提交工作区**，不是单独的 `b0c3ec6`。工作区已有上轮三个生产文件、两个测试文件和交付记录改动，实施者必须保留。不能只 checkout 这个 SHA 后认为拿到了已修版本。

已取得的上轮证据：两次完成的后端全量均为 **1087 passed / 9 skipped，无 xfail**；第一轮覆盖率 **86.61%**，通过 85% 门槛；Ruff、mypy 门禁通过；本地 functional **11 passed / 3 real_api deselected**。这是上轮记录，本次编写方案没有重新执行测试。中断且无法取回结果的运行不计通过。

依据：

- [总实施记录](D:/claudecode/Notebook/docs/REMEDIATION_DELIVERY.md)末尾“P1 组件补齐与发布提交恢复”。
- [原收口返修方案](D:/claudecode/Notebook/docs/REMEDIATION_CLOSURE_REWORK_PLAN.md:265) §11。其安全不变量继续有效；本方案细化剩余工作的顺序和停点，不把历史未完成项自动划勾。

“提交未确认后文档可能停在 indexing”目前有源码依据，但上轮没有完成该恢复链的失败复现实验。N1 必须先将它变成明确的行为证据，再决定最小修法。

## 2. 实施范围与约束

- 按本方案的批次、状态约束和验收标准实施；先取得有效失败证据，再做最小修复。不得顺带改动无关状态机、依赖、鉴权策略或产品语义。
- 每批记录实际修改文件、复用接口、失败原因、测试命令和结果。保留已有工作区改动；多人协作时避免同时覆盖同一文件。
- 当前批次验收通过后可继续下一批，不依赖某个特定 agent 的指令，也不要求每一步重复询问用户。业务数据写入、破坏性清理、外部发布和 Git 提交/推送仍须有对应授权。
- 只有经本批需求证明必要才新增迁移；编号以实施时本地迁移链为准。当前仓库可见 `013_execution_lease.py`，**不等于已经查询或确认任何真实数据库的 head**。
- 禁止 `git add -A` 收入临时脚本，禁止重置工作区，禁止通过 skip/xfail、吞异常、放宽权限或删断言获得全绿。

## 3. 阶段 0：资料发现与允许复用的接口

实施开始先确认 HEAD、工作区差异、配置来源及测试隔离。为本轮保存 SHA、变更文件清单和 diff 摘要；最终 CI 必须对应实际提交后的同一版本，不能以旧 SHA 的结果替代工作区验证。

| 源码/资料 | 已有接口或模式 | 用法与限制 |
|---|---|---|
| [索引执行服务](D:/claudecode/Notebook/backend/app/services/document_index_service.py) | `execute_index_document`、`confirm_publish_commit`、`reindex_document`、`_ExecutionRepository` | 复用完整发布事实确认与执行身份；不能把确认查询无结果等同于远端已停止 |
| [索引仓库](D:/claudecode/Notebook/backend/app/repositories/document_index_repository.py) | `claim_for_indexing`、`claim_upload_generation`、`publish_indexed`、`mark_index_failed_if_indexing`、`get_indexed_md5s` | 条件领取和代次写回；MD5 字典不是最终资源身份接口 |
| [重试任务](D:/claudecode/Notebook/backend/app/tasks/index_task.py:46) | `_index_document_async`、`_batch_index_pending_async`；仓库 `reset_to_pending_if_failed` / `reset_to_pending_if_retryable` | 前者仅重置 index_failed，后者仅接受 index_failed/pending_index；批量补偿不扫描 indexing |
| [知识入口](D:/claudecode/Notebook/backend/app/router/knowledge_service.py) | v1 allocate/publish/fail、`_process_slice_results` | 保留取消传播、409 确定拒绝、503 提交未确认及 SSE 终态语义 |
| [执行协调](D:/claudecode/Notebook/backend/app/tasks/agent_maintenance_task.py) | `coordinate_index_executions_task`、`_coordinate_executions_async` | 现有协调只把过期 writing 标为 unknown，不等于恢复文档状态或证明线程停止 |
| [可见性入口](D:/claudecode/Notebook/backend/app/rag/vector_store.py) | `get_user_documents`、`get_document_detail`、`get_document_chunks` | 记录原返回结构；先解决资源身份再取正文，不能任取同 MD5 资源 |
| [检索](D:/claudecode/Notebook/backend/app/rag/retrieval_service.py)、[chunk 直读](D:/claudecode/Notebook/backend/app/agentic/tools.py)、[笔记服务](D:/claudecode/Notebook/backend/app/services/note_service.py) | `_filter_active_documents`、`get_document_chunk`、`get_related_notes` | 汇总普通检索、关键词兜底和推荐的实际调用链；文档与笔记不能套同一资源模型 |
| [清理仓库](D:/claudecode/Notebook/backend/app/repositories/cleanup_task_repository.py) | 领取、延期、令牌条件回写 | 继续精确目标和持久任务；不恢复 user+MD5 宽泛删除 |
| [生命周期测试](D:/claudecode/Notebook/backend/tests/test_closure_rework.py)、[提交恢复测试](D:/claudecode/Notebook/backend/tests/test_v1_publication_recovery.py) | 有状态向量替身、内存库、线程门栓、publication commit 探针 | 扩展真实服务行为；不把空返回 MagicMock 当作残留清理证明 |
| [Compose](D:/claudecode/Notebook/docker-compose.yml)、[CI](D:/claudecode/Notebook/.github/workflows/ci.yml)、[全栈测试](D:/claudecode/Notebook/front/tests/e2e-full/auth-and-notes-fullstack.spec.js) | 既有容器、健康检查、账号和 Playwright 接线 | 原 Compose 有固定容器名/端口；现有 fullstack 主要覆盖登录和笔记，不证明上传索引全链 |

下文新增模块名、接口名、命令参数和迁移字段均为**拟新增设计**，不是宣称仓库已有 API。实现者须先核对实际封装和锁定依赖版本；涉及 Chroma 能力时读实际依赖源码/官方版本文档，不能假设它支持 SQL 跨库事务或服务器端写入 fencing。

本次资料发现结论：尚未发现持久 legacy mapping 或 backfill/inventory/apply 工具；现有 MD5Store 的旧文本兼容不是资源映射。目标后端读取路径也未发现列表/详情/分块/RAG 响应缓存，现有 `llm_cache` 用于笔记标签/autocomplete，Chroma 客户端实例缓存不等于结果缓存。这些是仓库范围的只读结论，不代表已经盘点真实存量或部署环境。

## 4. 批次与依赖

| 批次 | 交付结果 | 前置条件 | 主要允许改动范围 |
|---|---|---|---|
| N1 | 提交不确定后的受控恢复链 | 阶段 0；先取得红证据 | 索引服务/仓库、协调任务，必要的运维命令与行为测试 |
| N2 | 一份可见性规则，新协议入口全部接线 | N1 核心恢复门通过 | 可见性服务/仓库、知识读取、检索、推荐和必要前端契约测试 |
| N3 | 存量盘点、映射、可恢复 apply | N2 的资源身份契约冻结 | 存量映射模型/仓库/工具、专用迁移和回填测试 |
| N4 | 新旧资源联合验收、缓存及分页闭环 | N2/N3 通过 | 读取接线收尾、前端回归、兼容切换与说明 |
| N5 | 隔离真实服务及同版本集成证据 | N1—N4 的组件验收通过 | 专用集成环境、真实服务测试、CI 接线和交付证据 |

可以并行做 N3 的只读盘点工具设计、N5 的隔离环境配置草案；不得在 N2 契约未稳定前 apply 映射，也不得拿提前跑的环境冒烟代替最终集成验收。

**从 N1 的失败用例和最小修复开始，满足该批验收条件后继续 N2—N5。** 可以在同一实施过程连续完成，不要求人为拆成多个会话；不要越过尚未验证的依赖批次。

## 5. N1：提交不确定后的恢复，不只是关闭 execution

### 5.1 先证明缺口

复用提交探针构造：领取已提交 → 发布提交未落库并抛错 → 独立确认失败/未确认 → execution 为 unknown、文档仍 indexing → 调用真实重试入口。分别覆盖 v2/Celery、v1 单传、多传包装、SSE；共享实现的组件矩阵可以参数化，但不能只测仓库函数。

还要读取原文件前提：v1/SSE 的登记路径会创建 `file_path=""` 的行。不能承诺这类行直接交给依赖本地文件的 v2 执行器便能重新索引。必须区分“可使用已验证原文件重试”和“需要重新上传”，不猜文件路径、不生成虚假文件。

源码证据已经细化：`execute_index_document` 在未确认分支返回 `INDEX_FAILED / retryable=True`，但只更新 execution；数据库文档可能仍为 indexing。Celery 随后仅尝试 `index_failed → pending_index`，零行便退出；批量补偿不扫描 indexing。`reindex_document` 接受 failed/pending 且要求原文件存在；前端 [KnowledgeBasePage.vue](D:/claudecode/Notebook/front/src/pages/knowledge/KnowledgeBasePage.vue) 的重试按钮同样仅针对这两种状态。该链路解释了为何仅新增 `resolve_unknown` 还不等于用户可恢复使用。

### 5.2 恢复判定表

拟新增公共恢复编排 `reconcile_index_execution(...)`，名称可随现有风格调整；不再各入口复制一套恢复 SQL。输入至少为 owner、document_id、generation、execution token/ID；人工处置另带操作者、原因和核验凭据。

| 持久事实 | 允许动作 | 禁止动作 |
|---|---|---|
| 共享确认函数证明本次已发布 | 返回已发布，保留当前向量与清理任务 | 重置 pending、删除新代次、重复分配代次 |
| writing 仍活动，或 unknown 未证明停止 | 保留执行依据，返回等待/需核验状态 | 因超时或重试耗尽伪造 closed |
| 原写入者已确定停止，确认未发布，文档仍为本次 indexing | 在受控事务中将本次失败状态恢复到既有可重试协议 | 直接放宽所有 claim 让 indexing 可领取 |
| 文档 deleting/不存在，或已推进到其他代次 | 不恢复文档；仅处理自身执行及既有精确清理任务 | 复活删除记录、覆盖新代次 |
| 确认查询失败、缺少执行归属证据 | 保持不确定并输出可观测失败 | 把“查不到/连接失败”当作已回滚 |

### 5.3 实现要求

1. 先复用 `confirm_publish_commit`；任何未知的 SQL 提交结果都必须在原事务释放后协调，不使用 rollback 后的 ORM 属性。
2. `resolve_unknown` 只证明受控核验后可关闭执行，不应隐式伪报文档重试成功。运维入口先 dry-run；apply 要求精确执行身份、非空原因、操作者与证据位置。它不能绕过“请求/线程已结束”的人工前提。
   普通用户不能靠填写任意 reason 解除 unknown；先使用受控运维入口，不新增匿名恢复接口。
3. 关闭证明与文档可重试变更在统一锁顺序下复核并提交：owner、document_id、预期 generation、允许源状态都必须匹配。删除或新代次获胜时返回冲突，不重写它们的状态。
4. 明确恢复事务自身 commit 确认丢失的重复调用语义。不得二次增加代次、重复关闭不同执行或重复删除向量；可以从持久状态重建操作结果。
5. 有可用原文件才走现有重试编排；无文件返回明确 `needs_reupload` 类结果（拟新增语义），在执行停止得到证明后允许现有上传协议重新领取。正常成功/失败响应结构尽量不变，确需字段时补前端契约说明。
6. 消息发布必须在状态提交后。若 Celery 入队失败，保留可再次调度的持久依据，并返回真实失败或已验证的同步降级结果；不能只返回“任务已提交”。本批不默认引入新消息系统。
   内部执行结果须区分“普通失败可重试”和“需要协调/人工核验”，worker 按明确分类处理；不能继续用 `retryable=True` 掩盖数据库仍 indexing，也不能为迎合返回值强制把未知执行改为普通失败。
7. unknown 不使用 `closed_at` 冒充停止时间；核对现有写回语义，必要时最小修正，确定关闭时间只在 closed 写入。已有审计记录不直接批量改写。
8. 领取提交结果不明、取消、进程退出应列在分支矩阵。若本批尚未证明其恢复能力，继续登记未验证，不因 N1 重试缺口修复就宣称 P1 全部结束。

### 5.4 硬性回归

- N1-01：发布前 commit 失败 → unknown/indexing → 未核验时不得重试或完成清理 → 停止证明/恢复 → 新代次只领取一次并成功发布。
- N1-02：真实 commit 后抛错 → 确认已发布 → 恢复调用幂等，不重试、不删向量。
- N1-03：恢复与删除交错，及恢复与另一代次发布交错 → 条件更新失败，不复活、不覆盖。
- N1-04：两个恢复者同时操作 → 最多一方推进状态；重放不会再次分配代次。
- N1-05：确认查询失败、恢复 commit 结果不明、队列不可用 → 保留依据，不返回虚假成功。
- N1-06：v1/SSE 无有效原文件 → 明确需重传；实际重传后可收敛，不能只断言状态变成 pending。
- N1-07：未完成写线程/请求时人工解决被流程阻挡；释放门栓并核验后才恢复，最终 execution closed、目标清理 done、迟到残留为空。

停点：恢复端到端用例通过，原 T01—T21 相关安全断言无退化，才进入 N2。多连接 SQLite 组件测试不得标注为真实 MySQL 并发证明。

## 6. N2：统一读取身份与全入口接线

### 6.1 公共规则

拟新增 `document_visibility_service.py` 和按 document_id 批量查询的仓库方法。其结果必须区分“允许”“明确拒绝”“查询失败/无法验证”，不能用空字典混淆服务故障与合法空结果。

知识正文的默认可见条件：请求者具备现有 owner/空间权限，SQL 资源可读，向量 `user_id + document_id + generation` 与该资源当前 `active_generation` 相符。保持现有授权产品语义，不趁机开放共享权限或重定义空间同名规则。

MD5 只作为内容指纹和存量发现线索；不得继续用 `MD5 → generation` 字典区分同内容的不同文档。批量取 SQL 身份，禁止每个 chunk 发一条 SQL。

**区分管理状态与正文可见性**：`get_user_index_status` 这类状态列表仍须让本人看到 pending/indexing/index_failed 及错误原因；不能套用“仅 indexed”过滤后把上传任务藏起来。内容列表/详情/检索不得因此读取失败或旧代次正文。

### 6.2 入口矩阵

| 入口 | 修改方式 | 验收重点 |
|---|---|---|
| 知识内容列表 | 从有权限的 SQL 资源范围建立一文档一项的结果，再批量取摘要 | total 与分页同范围，不按向量代次重复，不加载全库正文 |
| 文件名详情 | 先按现有 owner/空间规则解析到唯一 document_id | 无候选按既有错误契约；歧义明确冲突，不取第一个 MD5 命中 |
| 分块列表 | 精确资源/active 代次过滤、稳定排序和分页 | 当前代次缺块时不退回旧代次；分页不能跨资源 |
| chunk ID 直读 | metadata 与 SQL 公共复核 | 缺 owner、缺身份、旧代次、越权均不返回正文 |
| Agent/RAG 普通检索及关键词兜底 | 返回正文给 rerank/模型前统一过滤 | 兜底不能绕过；低召回时有限补召回，不无限拉全库 |
| Agent `list_user_documents` | 明确是管理状态发现还是可检索资源候选，并复用对应规则 | 管理用途可保留带状态记录；用于正文检索时只提供可读资源，不混淆两者 |
| 相关知识/笔记推荐 | 文档候选走文档可见性；笔记继续其自身权限/删除规则 | 原资源类型、返回字段和笔记功能不退化 |
| 图片、批量图片及元数据旁路 | 盘点 `/image/{md5}/{filename}`、`/images/all/{md5}`、MD5 记录和 debug metadata 的资源解析 | 文件存在或 user+MD5 命中不是资源仍可读的证明；附属内容校验归属，诊断信息与用户正文接口明确区分 |
| 命中缓存的读取 | 缓存命中后仍有权限/身份最终核验，或使用已证明等价的失效协议 | 删除、代次切换、成员撤销后旧缓存不泄漏 |

仅修改实际承载知识内容的缓存，不把无关 autocomplete/纯模型响应缓存一并重构。缓存键至少区分主体、范围和资源代次；“缓存尚未过期”不是授权证明。
若实施时确认某入口没有结果缓存，则登记“不适用”，不为满足矩阵额外引入缓存。前端本地状态和图片缓存也先查实际使用，再确定必要的失效回归。

已有旁路事实：`knowledge_router.py` 的 debug metadata 直接按 user_id 查询向量；单图按用户/MD5 文件目录返回，批量图同样以 MD5 入参；`get_related_notes` 的 knowledge_docs 分支按 user_id 直接检索后返回正文。这些也要进读取清单，不能只改三个列表方法。图片若无法可靠关联可见资源，应明确拒绝并登记兼容问题，不能假定路径中存在代次；N3 的归属盘点需包含这类附属数据。原路径穿越防护不得移除。

### 6.3 兼容与测试

无 document_id 或 generation 的存量不得猜归属；交给 N3 的显式映射。N2 可以先对专用测试数据/已完成映射的范围验收，但全量兼容切换必须等 N4。不能静默丢掉历史数据后宣称“兼容已完成”。

为每个实际入口覆盖：本人/他人、同 MD5 的 A/B、不同空间、active/旧代次、deleting/index_failed、缺 metadata、数据库错误、删除或撤权与读取交错。同时断言拒绝的正文不进入模型上下文，不只是最终 JSON 少了一项。

保留现有响应结构，分页/身份参数有变化时补 `front` 调用和单元测试。验证查询次数/批量上限，禁止无界读取和逐 chunk SQL；阈值根据真实接口建立，不编造性能数字。
当前知识 `/list` 没有分页参数；不能偷偷限制前 100 条就算“分页完成”。若引入用户可见分页，须明确请求/响应及前端翻页契约并成组交付，否则记录该项仍未完成。分块现有模糊子串匹配需改为唯一资源解析，不能作为新规则的 fallback 保留。

跨 SQL/Chroma 不宣称原子快照：删除提交后的新请求不得返回资源；已发出的响应字节无法撤回。在返回前做最终核验的交错用例，明确一致性边界。

## 7. N3：存量盘点与安全映射

### 7.1 先盘点，后写入

先在专用库/collection 的代表性样本验证工具，再对经授权的只读数据源盘点。分类包含旧随机 ID、历史固定 ID、旧 MD5 代次 ID、新 document_id 代次 ID，以及 metadata 损坏/缺失；依据实际数据，不从 ID 外观推断唯一归属。

分类结果为唯一可证明映射、多候选冲突、无归属、明确属于其他资源、格式损坏。**同用户仅找到一个相同 MD5 候选也不是充分归属证明**，必须保留可核验的来源关联；证据不足继续 unresolved，不自动删除或任取候选。

### 7.2 工具与持久模型（拟新增）

拟新增工具支持 `inventory`、`dry-run`、`apply`、`status`、`resume`，按仓库目录习惯放置。默认不写；apply 必须指定审核 manifest、精确数据库/collection 标识、owner/space 范围和执行批次。

最小持久映射包含：数据源/collection 身份、旧向量 ID、owner、document_id、兼容代次、有效状态、来源批次和证据摘要。唯一键必须包含数据源身份，不能跨 collection 仅按旧 ID 去重。

优先只登记 SQL 映射，不搬迁/覆盖原向量 ID；这样先把跨库写入风险限制住。`generation=0` 是经过验证的存量版本，不是缺字段的通配符，也不能让旧向量混入已发布的新代次。

若确实需要更新/复制 Chroma 数据，先明确该批的前后状态、幂等键和故障恢复流程，并验证后再实施；业务数据操作仍需对应授权。不把 SQL 与向量写入说成同一事务。

### 7.3 执行和回退

- dry-run 只输出脱敏 manifest、数量对账、拟改动和冲突，不写业务 SQL/Chroma；报告文件写入不冒充数据源零副作用之外的权限。
- apply 前复核 manifest 摘要和源 metadata；写入时源数据已变化则标冲突，不用旧审核覆盖新事实。
- 以页/项持久记录完成状态；中断续跑和重复 apply 不创建重复文档/映射。分页期间源数据变化必须有冻结范围或稳定扫描策略，不能仅保存 offset 就宣称不会漏项。
- 记录必要 before/after 和操作者；回退只撤销本批仍未被后续操作改变的映射，不能盲目恢复或删除新数据。
- blocked 清理任务只能按已核实映射逐项重新评估；不批量改 pending，不重新引入 MD5 宽泛删除。

测试：dry-run 零数据源写入、重复 apply、页中断/进程退出后 resume、源变化冲突、同 MD5 多资源、不同 collection 同 ID、历史块数大于当前 chunk_count、映射撤销。所有歧义项保持原数据。

## 8. N4：兼容切换与联合验收

按可审计范围切换，不引入可在生产绕过权限的测试开关。切换前完成该范围的盘点、映射覆盖对账、未解析清单及回退准备；未解析项要么处理完成，要么由负责人明确接受其不可用状态，不能静默消失。

联合路径：新上传、旧资源、同内容 A/B、跨空间资源分别走列表 → 详情 → 分块 → 检索/推荐 → 重传 → 删除 → 再查询。验证旧资源没有因为严格过滤被整体清空，新资源也不会看到存量混入。

管理状态列表保留失败/待恢复提示；必要时前端展示需重传、待核验、存量未解析等状态，不把错误包装成“没有文档”。不得泄漏其他用户资源是否存在。

退出条件：N2 全入口矩阵、N3 映射/冲突矩阵、缓存撤权和前端契约通过；可以登记“P2/P3 组件验收通过”，但仍不能替代 N5。

## 9. N5：真实集成与最终版本证据

### 9.1 独立环境

拟新增专用集成 Compose，明确 project、端口、卷、测试账号、Redis namespace、Chroma 目录/collection、provider 和超时。禁止挂载业务数据、默认加载业务 `.env` 或仅用 `-p` 假装避开原固定 `container_name`。

项目已有 `celery-beat` 服务，但当前 CI fullstack 启动清单未包含它。集成要显式覆盖 Beat 接线或直接触发对应任务，并分别说明“调度器验证”和“任务函数验证”。

Chroma 按现有嵌入式持久化模式验证，不凭空新增 HTTP Chroma 依赖。确定性 provider 仅证明功能与竞态，不代表真实模型质量；生产配置不能静默启用测试 provider。

启动前校验最终 Compose 配置和目标隔离；失败保留脱敏日志。回收时只删除本次创建且核对过标识的临时资源，不碰业务库、既有容器或用户卷。

### 9.2 真实场景

| 层次 | 必交证据 |
|---|---|
| MySQL | 记录实际版本；专用空库和存量样本迁移；至少两个独立连接，用屏障控制领取/删除/恢复/发布交错，证明锁释放与条件更新；危险 downgrade 拒绝 |
| Chroma | 实际 add/get/delete；取消后迟到写；unknown 未解决不得完成清理；关闭后精准删除；A=g2/B=g1 同 MD5 不互伤；存量映射读取 |
| 进程恢复 | 隔离子进程在领取后/写入后/发布边界退出，重启能发现依据；区分 commit 响应丢失的组件注入与真实数据库断连实验 |
| HTTP/浏览器 | v1 单/多/SSE 与 v2/worker 上传，列表/详情/分块/检索/推荐、删除和权限撤销；不能仅复用登录+笔记 CRUD 就称上传链通过 |
| 部署门禁 | 最终代码的后端两次全量、覆盖率、Ruff/mypy、functional；涉及前端时跑 unit/lint/build/fullstack，Django 按最终部署版本验证；当前最终 SHA 的 CI 产物 |

本地真实集成**不依赖推送**。远程 CI 才需要用户授权提交/推送后的 SHA；缺 CI 就登记缺失，不能拿本地结果伪称远程通过。

## 10. 验证命令与证据口径

每批先定向测试，再完整门禁。执行前确认 fixture、模型替身和数据库地址；不得因项目里存在 MySQL 就默认可用它跑测试。

上轮 Windows 隔离配置有两点必须沿用/明确调整：

1. `REDIS_CACHE_URL` 和 `JWT_BLACKLIST_REDIS_URL` 需在 Python 进程 `os.environ` 显式设为空，走测试替换的默认连接；PowerShell 删除变量可能让 `.env` 回填。保持真实连接地址不可达、鉴权 fail-closed 不变。
2. 视觉测试会构造默认模型；上轮使用 `VISION_MODEL_TYPE=OPENAI`、假 key 与 `http://127.0.0.1:1/v1`，不访问模型服务。不能因测试启动失败去改业务权限或接入真实付费 provider。

后端工作目录为 `backend`，使用现有虚拟环境，参考命令：

```text
python -m pytest <本批行为测试> -q --runxfail --no-cov -p no:cacheprovider
python -m pytest tests --ignore=tests/functional -q --runxfail -p no:cacheprovider --cov=app --cov-report=term --cov-fail-under=85
python -m pytest tests --ignore=tests/functional -q --runxfail --no-cov -p no:cacheprovider
python -m pytest tests/functional -m "not real_api" -q --no-cov -p no:cacheprovider
python -m ruff check app tests
python -m mypy app/core app/schemas app/utils app/config
```

这些命令不能代替前述环境设置。真实集成使用专用配置，不能继承不可达占位配置后通过 skip 伪装通过。真实 API 测试另记结果和授权，不把 deselected 算通过。

前端在 `front` 使用既有 `npm run test:unit`、`npm run lint`、`npm run build`、`npm run test:e2e:full`。没有前端改动的纯后端小批不重复跑浏览器门禁，但 N4/N5 必须验证实际接口接线。

每批向 DELIVERY 追加：基线/变更摘要、修改前失败证据、修复内容、实际命令/环境/结果、未验证项、必要脱敏产物位置。文件新增但没执行、测试名字存在但关键分支未命中，都不算验收。

## 11. 最终完成与安全停点

- [ ] N1：未确认执行可按明确证据恢复；不会无限卡死、误报成功、复活删除或抢占活动写入者。
- [ ] N2：所有知识读取和相关缓存使用同一资源身份规则；管理状态接口不被误过滤。
- [ ] N3：存量盘点和映射可审计、默认 dry-run、apply 幂等可恢复；歧义不猜测、不盲删。
- [ ] N4：新旧资源、分页、权限、缓存及前端联合验收通过，切换与未解析项处置有记录。
- [ ] N5：真实服务、跨进程和最终版本门禁产物齐全；未执行/跳过项明确登记。
- [ ] 原 R/C/K/P 清单逐条核对，没有被本轮新命名掩盖的未完成项。

允许按批次交付，但表述必须为“N1 已验收，N2—N5 未完成”等明确状态。不再用测试总数增长、一次全绿或工作区干净代替整体验收。

实施过程中出现新风险时先判断是否属于当前批次；属于则固化行为证据并最小修复，不属于则登记，涉及实质范围变化时再向用户确认。中断时保存已完成改动、验证结果和剩余事项，不写虚假通过，也不让后续实施者从旧 SHA 覆盖现有工作。
