# Notebook V2 独立验收记录

日期：2026-10-21。验收依据：[产品任务书 V2](NOTEBOOK_PRODUCT_TASKBOOK_V2.md)。

## 1. 验收结论

**部分功能可以使用；拒绝整体验收通过。**

本次新增的导航、双来源分析工作台、图形导图和笔记图谱已有可用基础，不再是上一版的概念圆周图/缩进树。但独立验收复现了 6 条失败行为，归并为 5 类必须修复的问题：笔记链接错误重绑、导图用户修改套到错误主题、重生成任务刷新丢失、部分分析误报完整、标签筛选漏项。

不能用已通过的 1160 项历史全量覆盖这些问题。此次独立运行的是 52 项相关后端测试、71 项前端单测、前端 lint/build，以及 6 条新验收反例；未独立重跑后端全仓覆盖率与全量测试。

## 2. 基线、范围与运行环境

- 检查对象：`D:\claudecode\Notebook` 当前工作树。HEAD=`078549c`，V2 与既有整改内容主要未提交。交付文档中的 `8f44d1f` 不能替代本次实际工作树。
- 实际浏览器：前端 `http://127.0.0.1:3076`，后端配置指向 `8002`，Django 登录服务 `8001`；通过用户提供的专用验收账号登录。账号密码未写入本文或测试文件。
- 本次浏览器仅操作既有数据的查看、筛选、图谱、来源定位、导图折叠/搜索/全屏；未修改或删除现有笔记、未新上传业务文档、未提交新的付费模型生成任务。
- 新反例使用内存 SQLite、有状态生产服务和注入假模型；MySQL/Redis/provider 使用不可达地址和假凭据，不拿业务库做破坏性实验。
- 本次不执行修复、迁移、Git 提交或推送。仅增加本报告、证据截图和独立复现文件；复现文件放在 docs/evidence，不混入默认后端测试收集范围。

## 3. 已独立验证可用的部分

| 能力 | 实际检查 | 结论与边界 |
|---|---|---|
| 登录与主导航 | 真实登录后，从 Sidebar 点击文档分析、知识图谱、笔记 | 桌面入口可发现，可以进入对应页面 |
| 分析来源选择 | 分析首页显示笔记与知识库文档两个来源，以及最近分析 | 双来源入口可用；不代表两类新生成任务均已实际执行 |
| 已有文档分析结果 | 从最近分析打开已有 Markdown 文档，读取概览、要点、章节、概念 | 实际后端返回并显示结果；本次未证明该结果的真实模型质量 |
| 来源定位 | 点击第二条要点的证据按钮 | 原文面板定位/高亮当前锚点；当前样本只含一个大锚点，未覆盖跨页 PDF 或多锚点精度 |
| 导图主画布 | 切思维导图、全屏、折叠，再搜索折叠分支中的具体句子 | 真实 SVG 画布可显示层级、全屏、折叠和搜索展开；明显不同于旧版缩进树 |
| 笔记图谱 | 读取两篇现有笔记和一条现有手动关系；切列表、选节点、进入局部图、打开真实笔记 | 节点已是 Note，且不要求先生成摘要；本次未修改用户关系来演示新增/删除 |
| 图谱画布选中 | 适应视图后点击实际圆形节点 | 详情随节点切换；通过列表也可访问节点 |
| 返回图谱 | 从笔记和分析页返回图谱 | 在当前会话保留了局部图/选中节点状态；未验证跨账号/所有路由迟到结果 |
| 相关后端回归 | note_graph、knowledge_insights、真实文件 anchor 三组 | 52 passed；含解析、权限、基本图谱/生成等，仍缺少本报告反例 |

截图为本次真实运行页面：

- [分析工作台](evidence/v2-review-20261021/analysis-workspace.png)
- [导图全屏](evidence/v2-review-20261021/mindmap-fullscreen.png)
- [笔记图谱与节点详情](evidence/v2-review-20261021/note-graph.png)

正常工作区截图约 1280×720，全屏使用当前浏览器实际全屏尺寸；未冒充任务书指定的全部尺寸与浅深主题样张。

## 4. 必须返修的问题（均有独立失败断言）

### F01 / P1：标题链接在重命名、删除后会错误重绑

对应任务书：§6.2 第 3/5 项、GRAPH-04。

重现一：A 正文是 `[[Target]]`，已经绑定笔记 B；把 B 改名，再创建另一篇标题为 Target 的 C；只在 A 追加一段无关文字并保存。期望仍绑定 B，实际绑定到 C。

重现二：删除原 Target 后，同名新建 C；再保存 A。期望保留目标失效状态，实际又指向 C。

根因：`sync_note_links` 每次删除全部 occurrence，然后按当前标题重新解析，丢掉已确定的目标身份和删除失效历史。对 `[[note:uuid|文字]]` 的现有改名测试通过，不能证明 `[[标题]]` 安全。

位置：[链接重建](../backend/app/services/note_link_service.py:162)。

修复要求：匹配仍存在的链接 occurrence 并保留已绑定 target_id；正文中明确修改链接或用户重新选择目标时才重新绑定。已失效链接保留失效依据。需要处理编辑导致偏移改变，不仅按旧 char_start 生搬。两条反例均需转绿，并补实际保存入口测试。

### F02 / P1：重生成导图会把用户修改套在另一主题上

对应任务书：§4.4、§5.1 第 5 项、MAP-02。

重现：旧导图第一个章节为 Security，用户把它改成 My security annotation；重新生成时章节顺序变成 Caching、Security。当前节点 ID 是 `sec-0`、`sec-1` 等位置编号，旧修改被直接叠加到新的 `sec-0`，Caching 被错误改成安全章节的文字。

根因：节点身份按位置构造；读取编辑层仅按 owner/source 查全部 edits，没有按产物修订隔离或验证内容身份。表里虽有 `base_source_version_id`，叠加时未形成可靠的版本冲突协议；同源版本重生成也可能改变节点语义。

位置：[导图节点 ID](../backend/app/services/insight_generation_service.py:645)、[编辑层叠加](../backend/app/services/insight_generation_service.py:1039)、[编辑查询](../backend/app/repositories/knowledge_insight_repository.py:507)。

修复要求：引入明确产物 revision 与稳定节点身份，或者保留旧修订并要求人工映射冲突。不能仅以数组下标相同认定是同一节点。跨版本无法映射时保留原修改并提示，不自动转移到另一段内容。

### F03 / P2：重新生成后刷新，任务状态和轮询丢失

对应任务书：ANALYSIS-04、§4.4。

重现：已有有效分析结果 → force 重新生成，数据库 job 为 queued → 调用来源 state 接口，实际顶层 status=ready，而 job.status=queued。

根因：state 端点优先命中旧 active artifact 后直接判 ready；前端只在顶层 status 为 queued/running 时恢复轮询。因此刷新会显示旧结果已生成，无法正常跟踪新的在途生成。

位置：[状态优先级](../backend/app/router/insight_router.py:248)、[页面恢复轮询](../front/src/pages/knowledge/AnalysisWorkspacePage.vue:377)。

修复要求：旧结果可读状态与当前生成任务分开表达；或者顶层明确在途状态且同时返回旧产物。刷新后继续轮询，不因保留旧结果而吞掉新任务，也不能因重生成清掉仍有效的旧结果。

### F04 / P2：覆盖不全仍返回 ready

对应任务书：ANALYSIS-06、§4.2 长文覆盖要求。

重现：当前产物 `meta.coverage={total_blocks:3, analyzed_blocks:1, partial:true, ...}`，但 trust_status=verified；state 接口返回 ready，预期为 partial 或等价明确状态。

根因：state 只依据 trust_status 判 partial，忽略 coverage.partial。引文核验通过不代表已经分析全文。界面底部块数提示不能替代顶层清晰状态。

位置：[来源状态计算](../backend/app/router/insight_router.py:252)。

修复要求：把证据有效性、内容覆盖度、当前任务状态分开建模；分析首页、最近分析与工作台一致显示部分结果和缺失范围。

### F05 / P2：标签筛选会漏掉确实存在的笔记

对应任务书：GRAPH-01、UI-02、图谱筛选和范围统计。

重现：一篇较早笔记带 wanted 标签，另有五篇较新笔记不含该标签；请求 tag=wanted、limit=1，实际 nodes=[]。

根因：先按最近更新时间截取 `limit*3` 篇，再在 Python 中过滤标签。匹配笔记在预取窗口之外就消失；默认 limit=300 时同样可发生在更大的库中。计数也基于未应用标签的范围。

位置：[先截取再筛选](../backend/app/services/note_graph_service.py:161)。

修复要求：先在数据源应用筛选再分页，或采用可证明完整、有界且带 cursor 的扫描策略。结果计数/截断提示与筛选范围一致，不用固定扩大几倍窗口作为正确性保证。

## 5. 其他已确认缺口与交付边界

- **连接数显示不一致**：真实图谱列表显示一条连接，选中详情顶部却为 0 条连接，下方又显示手动关联 (1)。`get_note_links` 用不带 degree 的 `_node_from_row` 构造详情，前端直接显示其默认 0；应统一统计或不展示未计算值。位置：`note_graph_service.get_note_links`、`NoteGraphPage.vue:163`。
- **局部图中的筛选控件不生效**：页面在局部图仍提供标题/标签/分类/关系筛选，但 `loadGraph` 的 local 分支只传 note_id/depth，未传或本地应用这些条件。应支持同等筛选或在局部模式明确禁用，避免用户以为已筛选。位置：`NoteGraphPage.vue:668`。
- **Markdown 导出缺少逐结论来源**：`buildMarkdown` 只在开头挂一条分析页链接，各 claim 未导出对应锚点/位置。需要把交互中的来源信息保留到导出产物，才能满足任务书的可追溯导出要求。位置：`AnalysisWorkspacePage.vue:510`。本次未下载导出文件，结论来自序列化实现检查。
- **交付文档矛盾**：交付记录 §8.1 已登记浏览器烟测与截图，§9 却仍说 Playwright 和截图没有执行；矩阵末尾也有旧表述。应区分已做烟测、尚欠完整 E2E、实际图片/视频范围。该问题不替代功能缺陷，但会妨碍后续验收。

## 6. 任务书逐项状态

| 任务 ID | 本次独立状态 | 说明 |
|---|---|---|
| ENTRY-01 | 桌面通过；移动未验证 | 已从真实主导航进入，无须猜 URL |
| ANALYSIS-01 | 部分通过 | 实际已有文档结果可读；笔记/文档新生成完整全栈未执行 |
| ANALYSIS-02 | 当前 Markdown 样本通过；完整定位部分 | 点击证据高亮可用；跨页 PDF、第 2 个独立锚点、精确文本范围未覆盖 |
| ANALYSIS-03 | 部分 | 保存失败前端逻辑有改进，未对业务笔记注入保存失败 |
| ANALYSIS-04 | 不通过 | F03；刷新重生成任务不能正确恢复 |
| ANALYSIS-05 | 组件通过，真实交错未验证 | 已运行相关后端测试；未用付费模型/真实 worker 中途删源 |
| ANALYSIS-06 | 不通过 | F04；覆盖不足状态误报 ready |
| MAP-01 | 小样本交互通过；规模未验证 | 真画布、全屏、折叠、搜索展开可用；100 节点未实测 |
| MAP-02 | 不通过 | F02；跨语义重生成错配；完整 PNG/SVG/Markdown 导出及用户编辑浏览器写回未验证 |
| GRAPH-01 | 部分通过 | 真笔记节点与现有手动关系可读，基础 API 不依赖模型；未停业务服务做完整保存流程 |
| GRAPH-02 | 现有组件测试通过 | 已独立运行 2→1→0 等相关测试；真实浏览器新增/删除未执行 |
| GRAPH-03 | 组件通过；编辑器交互部分 | 已运行解析/候选/权限测试；复杂 Markdown 边界与实际链接阅读尚需浏览器证据 |
| GRAPH-04 | 不通过 | F01；UUID 链接通过不覆盖标题链接重绑 |
| GRAPH-05 | 组件通过，浏览器部分 | A→B→C 的 1/2 跳测试通过；真实账号当前样本为两篇、一条手动关系 |
| GRAPH-06 | 组件通过，浏览器部分 | 添加移除/互不误删组件通过；实际已有关系与详情可读 |
| GRAPH-07 | 组件通过；完整会话隔离未验证 | owner/猜 ID 的现有 API 测试通过，不替代账号切换与缓存撤权浏览器测试 |
| UI-01 | 部分通过 | 当前桌面布局能用；浅深主题、390×844、全部状态未全面验证 |
| UI-02 | 部分通过 | 已测导航、图谱打开笔记与返回；错误/刷新场景见 F03/F05 |

## 7. 实际执行结果与复现方法

### 7.1 已有定向测试与前端门禁

backend（内存库与隔离进程环境）：

```text
pytest tests/test_note_graph.py tests/test_knowledge_insights.py \
  tests/test_knowledge_anchor_real_files.py -q --runxfail --no-cov \
  -p no:cacheprovider --tb=short
52 passed in 12.60s
```

front：`npm run test:unit` → 9 files / 71 passed；`npm run lint` → 0 errors / 5 warnings；`npm run build` → 成功（有既有大 chunk 提示）。前端 71 项不等于给新工作台/图谱补齐了交互用例。

未独立运行：1160 项后端全量、85% 覆盖率、Django 全量、真实 MySQL 双连接/迁移、真实模型质量、100 节点导图/300 节点 1000 边性能、390×844 与深色全套截图、完整浏览器新建/生成/删改链路。

### 7.2 新验收反例

文件：[acceptance_probes.py](evidence/v2-review-20261021/acceptance_probes.py)。该脚本自带独立进程隔离配置，复用现有内存 fixture；直接执行即可，不需要业务服务或真实凭据。

从仓库根目录运行：

```powershell
backend/.venv/Scripts/python.exe docs/evidence/v2-review-20261021/acceptance_probes.py
```

在当前版本最终结果为 **6 failed**（已完成收集且失败在目标行为断言，不是启动错误）。这六项是需要修复的证据，不是应当跳过或改成 xfail 的已知成功：

| test node | 实际结果 |
|---|---|
| `test_title_link_binding_survives_rename_and_source_resave` | 目标 B 被改成 C |
| `test_deleted_title_target_does_not_rebind_on_source_resave` | 已删目标被同名 C 接管 |
| `test_regenerated_mindmap_does_not_apply_edit_to_different_topic` | Caching 被安全章节的用户文字覆盖 |
| `test_tag_filter_searches_matching_scope_before_page_limit` | 应返回 B，实际 [] |
| `test_refresh_exposes_inflight_regeneration` | queued 被返回为 ready |
| `test_incomplete_coverage_is_not_reported_as_ready` | partial 被返回为 ready |

复跑时若代码已变化，应保留旧失败记录并报告当前结果；不得为通过调整断言去迎合当前错误行为。

## 8. 返修与再验收顺序

1. 优先 F01、F02，保护笔记关系与用户编辑的语义身份；补服务入口而非只测仓库分支。
2. 合并修 F03、F04，统一结果可读状态、覆盖度和当前任务，覆盖刷新与重新生成失败。
3. 修 F05 和统计/局部筛选/导出来源的一致性；保证在大于默认预取窗口的数据集上测试。
4. 六条失败反例转绿，再执行已有 52 项及受影响测试，不通过 skip/xfail 或重跑到绿掩盖问题。
5. 在隔离测试数据下补真正的两来源生成与笔记 A→B→C 的浏览器流程、编辑→重生成→导出、移动/深色截图及任务书要求规模测试，再申请整体验收。

整体状态应登记为“基础能力可用、V2 验收未通过”，不能登记为 P0—P5 全完成或可直接发布。
