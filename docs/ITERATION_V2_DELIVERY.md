# Notebook 产品迭代 V2 交付记录

日期：2026-10-21。依据：[NOTEBOOK_PRODUCT_TASKBOOK_V2.md](NOTEBOOK_PRODUCT_TASKBOOK_V2.md)。
基线：`8f44d1f` 加工作区既有整改/洞察改动（未提交）。本文记录实际实现与验证结果，不替代任务书。

## 1. 完成状态

| 批次 | 内容 | 状态 |
|---|---|---|
| P0 | 主导航、分析/图谱入口、路由、更多页、分析首页 | 已实现；lint/build 通过 |
| P1 | 分析工作台分屏、原文定位、任务恢复、保存为笔记、导出 | 已实现；后端 API/组件测试通过，未跑浏览器 E2E |
| P2 | 可平移缩放导图、折叠、搜索、编辑、同级排序、导出、修订持久化 | 已实现；后端修订/冲突测试通过，未跑 100 节点浏览器验收 |
| P3 | 笔记内部链接、反向链接、手动关联、迁移 017 | 已实现；12 个后端用例通过；迁移已应用到本地 MySQL |
| P4 | 笔记全局/局部图、筛选、详情、`[[` 候选、编辑器链接侧栏 | 已实现；后端 12 个用例通过；lint/build 通过 |
| P5 | 全量门禁、文档、验收矩阵 | 后端全量、前端门禁完成；Playwright/截图/录屏/性能未执行 |

## 2. 信息架构与入口

桌面 Sidebar 顺序：**笔记 / 文档分析 / 知识图谱 / 知识库 / AI 对话 / 每日回顾**。
移动端 TabBar：**笔记 / 分析 / 图谱 / 知识库 / 更多**；“更多”页保留 AI 对话、每日回顾、个人资料、设置、组织空间。

路由：

| 路由 | 页面 |
|---|---|
| `/analysis` | 分析首页：选择笔记/文档、搜索、最近分析 |
| `/analysis/note/:id` | 笔记分析工作台 |
| `/analysis/document/:id` | 文档分析工作台 |
| `/knowledge/:id/insight` | 兼容旧入口，渲染同一分析工作台 |
| `/notes/:id/insight` | 兼容旧入口，渲染同一分析工作台 |
| `/graph` | 笔记知识图谱（默认） |
| `/graph/concepts` | 原 AI 概念图，保留为实验功能 |
| `/more` | 移动端“更多” |

知识库每行提供文字“分析”；笔记列表提供“知识图谱”文字按钮；笔记编辑页提供“分析笔记”和“局部图谱”。

## 3. 分析工作台

- 桌面 44/56 分屏，分隔条可拖动；窄屏切换“原文 / 总结 / 导图”。
- 原文来自 `/api/v1/insights/sources/{type}/{id}/text`：
  - 按真实页码或章节分组展示提取文本；
  - 点击结论证据后滚动到对应片段并高亮；
  - PDF 可按需加载受保护原文件预览并跳到真实页码；
  - DOCX/PPTX/TXT 显示规范化提取文本，不伪造页码。
- 分析状态从 `/sources/{type}/{id}/state` 恢复：未生成、排队、分析中、可用、部分结果、失败、过期。刷新页面会继续轮询服务端 job，而不是依赖页面内 setTimeout。
- 路由从 A 切到 B 时使用 load token 丢弃迟到响应。
- 结果支持“保存为笔记”（预览后创建新 Note，保留来源链接）与 Markdown 导出。
- 长内容：超过单次预算时按章节/页码分块调用模型，再聚合并核验；结果 `meta.coverage` 记录总块数、已分析块、缺失块和 `partial`，前端显示“部分内容已分析”。

## 4. 思维导图

- 主视图为 SVG 层级画布：根主题 → 章节 → 要点。
- 支持空白拖动平移、滚轮/按钮缩放、适应/重置/全屏。
- 分支展开/折叠；搜索节点并展开命中路径、定位高亮。
- 单击选中查看来源与操作；双击/编辑按钮修改标题；上移/下移调整同级顺序。
- 用户修改写入 `knowledge_mindmap_edits`，与 AI 产物分离：
  - 读取时叠加标题/顺序，节点标记 `user_edited`；
  - PATCH 使用 `expected_revision` 乐观并发，冲突返回 409；
  - 重新生成不覆盖用户修订。
- 导出 SVG、PNG、Markdown；导出时注入当前主题颜色，避免深色文字/背景反转。
- 现有 `MindmapTree` 保留为“大纲”视图，作为键盘/无障碍替代。

## 5. 笔记知识图谱

### 5.1 数据模型（迁移 017）

| 表 | 作用 |
|---|---|
| `note_link_occurrences` | 一处 `[[...]]` 的出现位置、原文片段、来源 revision、目标 note_id、解析状态 |
| `note_manual_relations` | 用户手动关联，独立于正文，可添加/移除 |
| `knowledge_mindmap_edits` | 导图用户标题/同级顺序修订 |

迁移已应用到本地 MySQL 8.0.12，`alembic current` = `017_note_graph`；三张表已验证存在。

### 5.2 链接解析

- 支持 `[[note:<uuid>|显示文字]]`、`[[标题]]`、`[[标题|别名]]`。
- 跳过代码围栏、行内代码、转义 `\[[`。
- `[[标题]]` 只在当前用户可读范围内唯一匹配；重名标记 `ambiguous`，不任取第一条。
- 每次保存幂等替换该笔记全部 occurrence；同一目标多处引用聚合为一条边并保留 count。
- 目标改名不影响稳定 ID 链接；目标删除后来源 occurrence 标记 `target_deleted`，不自动重绑同名新笔记。
- 删除手动关联不修改正文；删除正文链接不影响手动关联。

### 5.3 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/notes/graph` | 全局图：q/tag/category/edge_type/include_orphans/limit |
| `GET` | `/api/v1/notes/{id}/local-graph?depth=1\|2` | 局部图 |
| `GET` | `/api/v1/notes/{id}/links` | 出链、反链、手动关联、未解析链接与来源段落 |
| `GET` | `/api/v1/notes/link-candidates` | `[[` 候选，owner 内搜索，同名标记 |
| `POST` | `/api/v1/notes/{id}/manual-relations` | 添加手动关联 |
| `DELETE` | `/api/v1/notes/{id}/manual-relations/{relation_id}` | 移除手动关联 |

概念图 `/api/v1/insights/graph` 语义保持不变，不与笔记图混用。

### 5.4 前端图谱

- 力导向布局，稳定后停止；不是固定圆周。
- 支持平移、缩放、拖动固定/解锁、适应视图、搜索、悬停邻居高亮。
- 单击预览详情（出链/反链/手动关联/来源段落），双击或“打开笔记”进入实际笔记。
- 全局/局部切换，局部 1/2 跳；从笔记返回时用 sessionStorage 恢复视野与筛选（按用户隔离）。
- 筛选：标题、标签、分类、关系类型、孤立节点；节点大小按连接度，不按 AI 证据数。
- 空状态区分：无笔记、有节点无边、无匹配、服务错误；不再提示“先生成摘要才有图谱”。
- 列表视图作为键盘可访问替代。

## 6. 本轮新增/修改的主要文件

后端：

- `backend/app/models/note_graph.py`
- `backend/alembic/versions/017_note_graph.py`
- `backend/app/services/note_link_service.py`
- `backend/app/services/note_graph_service.py`
- `backend/app/router/note_graph_router.py`
- `backend/app/services/insight_generation_service.py`（分层分析、导图修订）
- `backend/app/router/insight_router.py`（来源搜索/状态/原文、导图 PATCH）
- `backend/app/router/knowledge_router.py`（受保护原文件）
- `backend/app/repositories/knowledge_insight_repository.py`（导图修订）
- `backend/tests/test_note_graph.py`、`backend/tests/test_knowledge_insights.py`

前端：

- `front/src/pages/knowledge/AnalysisHomePage.vue`
- `front/src/pages/knowledge/AnalysisWorkspacePage.vue`
- `front/src/pages/knowledge/NoteGraphPage.vue`
- `front/src/pages/knowledge/ConceptGraphPage.vue`
- `front/src/pages/knowledge/MorePage.vue`
- `front/src/components/MindmapCanvas.vue`
- `front/src/services/noteGraphApi.js`
- `front/src/services/insightApi.js`
- `front/src/router/index.js`、`Sidebar.vue`、`TabBar.vue`
- `front/src/pages/notes/NotesPage.vue`、`NoteEditorPage.vue`
- `front/src/pages/knowledge/KnowledgeBasePage.vue`

## 7. 设计选择

- 没有新增前端依赖。导图使用自定义 SVG 横向层级布局；笔记图使用自定义力导向模拟（O(n²) 但带距离截断），在 300 节点目标内运行。避免引入 Vue Flow/D3 带来的包体积和许可证登记成本；现有 `MindmapTree` 保留为大纲/无障碍替代。
- 笔记图只读取笔记元数据，不读取正文做图构建；正文只在详情/反链接口按笔记读取。
- 概念图与笔记图使用不同 API、不同页面、不同节点结构，避免旧客户端误解。

## 8. 本地验证

### 8.1 浏览器烟测与验收修复（2026-10-21）

针对验收反馈修复了两处前端问题，并用 Playwright（Chromium，1440×900）复测：

- **导图全屏**：SVG 改为 100% 填充视口，增加 `:fullscreen` 布局；节点宽度改为 220px、换行 15 字，导出时显式设置内容尺寸。实测全屏元素 1440×900、视口 1438×847、SVG 1438×847，前 6 个节点文字宽度均未超出节点框。
- **笔记图谱交互**：节点增加透明点击热区；拖动/平移改为 window 级 pointer 监听，指针移出节点也不会卡住；选择详情使用请求令牌丢弃迟到响应；连线增加 12px 透明命中线和详情栏关系列表；力导向改为 `requestAnimationFrame` 逐步收敛，并给节点增加错峰淡入；返回图谱恢复视野和选中节点。
- Playwright 烟测：点击节点 1 显示详情 → 拖动节点 1 → 再点节点 2 详情正确切换；点击连线显示“手动关联”详情；控制台无错误。

截图（最终代码、headless Chromium 1440×900）：

- [笔记图谱](evidence/v2/note-graph-1440x900.png)
- [导图全屏](evidence/v2/mindmap-fullscreen-1440x900.png)

### 8.2 自动化门禁

后端：

```text
python -m pytest tests --ignore=tests/functional -q --runxfail --no-cov -p no:cacheprovider
1160 passed, 9 skipped

python -m pytest tests --ignore=tests/functional -q --runxfail -p no:cacheprovider \
  --cov=app --cov-report=term --cov-fail-under=85
1160 passed, 9 skipped, coverage 85.57%

python -m ruff check app tests
All checks passed

python -m mypy app/core app/schemas app/utils app/config
Success: no issues found in 26 source files
```

前端：

```text
npm run lint        # 0 errors（5 个既有/静态图标 v-html 警告）
npm run build       # 成功
npm run test:unit   # 71 passed
```

迁移：

```text
alembic current -> 017_note_graph
MySQL information_schema 验证存在 note_link_occurrences / note_manual_relations / knowledge_mindmap_edits
```

覆盖率门禁：`1160 passed / 9 skipped`，`coverage 85.57%`，通过 `--cov-fail-under=85`。

## 9. 未验证 / 未执行

1. **Playwright/真实浏览器 E2E 未执行**：本轮完成了接口、组件和构建门禁，但没有生成任务书要求的截图/录屏。`ENTRY`、`ANALYSIS`、`MAP`、`GRAPH` 的浏览器级状态仍标记为“部分”。
2. **真实模型质量未评估**：分层分析/摘要测试使用注入假模型；没有调用付费模型做人工抽样。
3. **300 节点/1000 边性能未测**：力导向模拟有距离截断和冷却，但没有记录目标机器的 p50/p95 或帧率。
4. **真实 MySQL 笔记图 E2E 未跑**：迁移已应用，但没有启动完整栈执行“保存 A→B→C → 刷新图谱 → 反链 → 删除”的运行时验证。
5. **PPTX 文本/页码**依赖现有加载器；缺失时降级为片段，不伪造页码。
6. 服务当前已停止；重新启动方式见 README。

## 10. 运行

```powershell
# 1. MySQL / Redis
# 2. 迁移
cd backend
uv run alembic upgrade head

# 3. 后端
uv run uvicorn main:app --host 127.0.0.1 --port 8002

# 4. Celery worker（Windows 加 --pool=solo）
uv run celery -A app.tasks.celery_app:celery_app worker --loglevel=info --pool=solo
uv run celery -A app.tasks.celery_app:celery_app beat --loglevel=info

# 5. 前端
cd front
npm run dev
```

默认地址：前端 `http://127.0.0.1:3076`，Swagger `http://127.0.0.1:8002/api/v1/docs`。
