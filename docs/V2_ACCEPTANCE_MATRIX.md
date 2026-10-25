# Notebook V2 验收矩阵

日期：2026-10-21。对应交付说明：[ITERATION_V2_DELIVERY.md](ITERATION_V2_DELIVERY.md)。

状态口径：
- **通过**：本地已执行且有断言/产物；
- **部分**：代码与自动化测试通过，但任务书要求的真实浏览器/截图/性能证据未取得；
- **未验证**：本轮未执行。

| ID | 场景 | 实现入口 | 自动化证据 | 状态 |
|---|---|---|---|---|
| ENTRY-01 | 从导航进入分析/图谱 | Sidebar/TabBar/router | 路由与组件构建通过；未跑浏览器 | **部分** |
| ANALYSIS-01 | 笔记与文档分别生成分析 | `/analysis/{type}/{id}`、`/insights/sources/*` | `test_analysis_source_endpoints`、`test_note_insight_generation` | 通过（组件/HTTP） |
| ANALYSIS-02 | 点第 2 条来源定位原文 | 原文锚点分组 + `openAnchor` + PDF 页码 | 前端实现；`test_analysis_source_endpoints` 返回多片段；未跑浏览器高亮 | **部分** |
| ANALYSIS-03 | 保存失败不跳转、成功后分析目标版本 | `handleSave` 返回 `{ok,id}`；`handleInsight` 仅在成功时跳转 | 前端代码检查；后端笔记保存测试 | **部分**（未浏览器回归） |
| ANALYSIS-04 | 刷新/快切/退出恢复任务 | `/sources/{type}/{id}/state` + 轮询 + load token | `test_analysis_source_endpoints`；前端实现；未浏览器 | **部分** |
| ANALYSIS-05 | 生成期间删除/改源、缺证据、恶意输出 | 版本复核、证据过滤、引文核验 | `test_generation_filters_unverified_and_builds_mindmap_and_graph`、`test_generation_marks_insufficient_evidence_instead_of_faking`、`test_graph_hides_relations_after_source_invalidation` | 通过（组件） |
| ANALYSIS-06 | 超长文/预算不足不冒充完整总结 | `_build_analysis_blocks` + `_run_layered_analysis` + `meta.coverage` | `test_build_analysis_blocks_splits_long_content`、`test_layered_generation_reports_real_coverage` | 通过（组件） |
| MAP-01 | 100 节点导图平移缩放/搜索/折叠 | `MindmapCanvas.vue` | 浏览器烟测：全屏 1440×900、SVG 1438×847、节点文字不越框；100 节点/性能未测 | **部分（全屏/越框已修复）** |
| MAP-02 | 修改→刷新→重生成→导出 | `knowledge_mindmap_edits` + PATCH + 导出 | `test_mindmap_edits_persist_and_conflict`（含重新生成保留修订、409、未知节点、越权） | 通过（后端）；导出未浏览器验证 → **部分** |
| GRAPH-01 | 无 AI/向量也能用笔记图谱 | `/notes/graph`、`NoteGraphPage` | `test_global_graph_and_filters`、`test_local_graph_depth_and_links`；浏览器烟测节点选择/拖动后仍可选/连线详情；完整 A→B→C 流程未跑 | **部分（核心交互已修复）** |
| GRAPH-02 | 同一 A→B 两处引用聚合与逐个删除 | occurrence 聚合 | `test_sync_occurrences_is_idempotent_and_updates`（2→1→0）、`test_global_graph_and_filters`（count=2） | 通过 |
| GRAPH-03 | `[[` 候选、同名、代码/转义伪链接 | 解析器 + candidates API + 编辑器候选 | `test_extract_links_forms_and_offsets`、`test_extract_links_ignores_code_and_escaped`、`test_duplicate_title_is_ambiguous`、`test_link_candidates_and_manual_api` | 通过（后端）；前端候选 **部分** |
| GRAPH-04 | 改名/删除/同名重建、旧任务迟到 | 稳定 ID + target_deleted | `test_rename_target_keeps_stable_id_link`、`test_target_delete_keeps_source_occurrence_unresolved` | 通过（组件）；旧异步任务不存在（同步解析） |
| GRAPH-05 | A→B→C 局部 1/2 跳 | `/notes/{id}/local-graph` | `test_local_graph_depth_and_links` | 通过 |
| GRAPH-06 | 手动关联与正文链接共存/互不误删 | manual relations + explicit links | `test_manual_relation_add_remove_and_permissions`、`test_manual_removal_keeps_explicit_links` | 通过 |
| GRAPH-07 | 第二用户/猜 note_id/隐藏端点 | owner 过滤、404 | `test_global_graph_and_filters`、`test_local_graph_depth_and_links`、`test_link_candidates_and_manual_api`、`test_generation_requires_owner` | 通过（HTTP） |
| UI-01 | 三种页面浅/深、多尺寸、错误/空/加载 | 主题变量、响应式 CSS | lint/build/unit 通过；已存 1440×900 图谱/导图截图；未覆盖全部状态 | **部分** |
| UI-02 | 实际导航/生成/图谱打开返回 | 路由与 sessionStorage | 路由/构建通过；未浏览器流程 | **部分** |
| BROWSER-SMOKE | 导图全屏/文字不越框、图谱选择/拖动/连线 | Playwright Chromium 1440×900 | `docs/evidence/v2/*.png`；节点选择→拖动→再选、连线详情、全屏尺寸与文字边界断言；控制台无错误 | 通过（烟测） |
| MIGRATION | 017 可升级、表存在 | `017_note_graph` | `alembic current`=017；information_schema 三表存在 | 通过（本地 MySQL 8.0.12） |
| PERF | 导图 100 节点、图谱 300/1000 | 自定义布局 | 未测量 | **未验证** |
| E2E | 真实登录 + 两来源 + 笔记网络 | Playwright | 未执行 | **未验证** |
| MODEL | 真实模型摘要质量 | 分层生成 | 未调用真实模型 | **未验证** |

## 全量门禁

```text
backend: 1160 passed, 9 skipped
ruff: All checks passed
mypy: Success: no issues found in 26 source files
frontend lint: 0 errors
frontend build: success
frontend unit: 71 passed
coverage: 85.57% (--cov-fail-under=85 passed)
```

## 明确未通过/未验证

1. 任务书要求的 1440×900/390×844 截图、浅深色样张、两条录屏未生成。
2. 300 节点/1000 边性能目标未测量；导图 100 节点交互未做浏览器验收。
3. 真实模型质量抽样、真实全栈两来源分析未执行。
4. 笔记图谱前端未做真实浏览器点击/返回视野恢复验证；后端 API 与构建通过。
