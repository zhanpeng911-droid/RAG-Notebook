# 知识工作台迭代验收矩阵（N0–N4）

日期：2026-10-20。对应实现说明见 [ITERATION_KNOWLEDGE_WORKBENCH.md](ITERATION_KNOWLEDGE_WORKBENCH.md)。
状态口径：**通过** = 本地已执行且有断言；**部分** = 组件通过但缺少真实服务/浏览器证据；**未验证** = 本轮未执行。

| ID | 要求 | 生产入口/实现 | 测试 node ID / 证据 | 层级 | 状态 |
|---|---|---|---|---|---|
| N0-01 | PDF/Markdown 锚点含真实页码、章节、字符偏移、引文指纹 | `source_anchor_service.build_anchor_candidates` | `tests/test_knowledge_insights.py::test_anchor_candidates_page_heading_and_offsets` | 纯函数 | 通过 |
| N0-01b | 真实 PDF/MD/TXT 文件的切片可回到原文 | 真实加载器 + 锚点服务 | `tests/test_knowledge_anchor_real_files.py::test_real_pdf_anchor_uses_real_page_numbers`、`::test_real_markdown_anchor_keeps_heading_path`、`::test_real_txt_anchor_maps_to_original_characters` | 真实文件 | 通过 |
| N0-02 | 无页码/无偏移格式不伪造位置 | `page_from_metadata`、chunk-only 降级 | `::test_page_from_metadata_never_fabricates_page`、`::test_anchor_candidates_without_source_do_not_claim_page_or_offsets` | 纯函数 | 通过 |
| N0-03 | DOCX 使用真实 Heading 样式偏移 | `file_handler._load_docx` | `tests/test_file_handler.py::test_word_loader_records_real_heading_offsets` | 真实 python-docx | 通过 |
| N0-04 | 笔记锚点无伪造页码 | `build_note_anchor_candidates` | `::test_note_anchors_have_offsets_but_no_page` | 纯函数 | 通过 |
| N0-05 | v2 索引发布后来源版本 active、锚点与 chunk_id 对应 | `execute_index_document` | `::test_execute_index_document_records_and_activates_anchors` | 内存库 + 假向量 | 通过 |
| N0-06 | 证据核验复核 owner、active 版本、active_generation、引文 | `verify_anchor` | `::test_verify_anchor_owner_generation_and_quote`、`::test_source_anchor_edge_cases` | 内存库 | 通过 |
| N0-07 | 重新发布使旧版本/旧产物失效 | `activate_version` | `::test_new_generation_activation_invalidates_old_artifact` | 内存库 | 通过 |
| N0-08 | 删除使版本/产物/证据/排队任务失效 | `invalidate_document`、`_delete_single_record` 接线 | `::test_graph_hides_relations_after_source_invalidation`、`::test_invalidate_document_marks_queued_jobs_stale` | 内存库 | 通过 |
| N0-09 | 笔记保存生成新版本并失效旧版本 | `note_service._sync_note_anchors` | `::test_note_versioning_and_invalidation` | 内存库 | 通过 |
| N0-10 | v1 单传/SSE 锚点接线 | `DocumentProcessor.get_document` 返回结果、`SliceResult.source_documents` | 既有 `tests/test_processor.py`、`tests/test_knowledge_service.py` 通过；未直接断言锚点行 | 组件 | **部分** |
| N1-01 | 结构化摘要只保留有可核验锚点的结论 | `_build_verified_payload` | `::test_generation_filters_unverified_and_builds_mindmap_and_graph` | 内存库 + 假模型 | 通过 |
| N1-02 | 无证据时明确 `insufficient_evidence`，不伪造事实 | `_build_verified_payload` | `::test_generation_marks_insufficient_evidence_instead_of_faking` | 内存库 | 通过 |
| N1-03 | LLM 输出非法时任务落 failed | `run_summary_job`、`_mark_job_failed` | `::test_generation_failure_is_recorded` | 内存库 | 通过 |
| N1-04 | worker 崩溃遗留任务可回收 | `reclaim_stale_insight_jobs_task` | `::test_reclaim_stale_insight_jobs` | 内存库 | 通过 |
| N1-05 | 重复投递不并行执行 | `claim_and_run_job` 原子领取 | 代码路径 + `::test_generation_failure_is_recorded`；未构造双 worker 真并发 | 组件 | **部分** |
| N1-06 | 任务只允许 owner 操作 | `insight_router` 全部端点 | `::test_generate_endpoint_owner_check_and_dispatch`、`::test_api_remaining_branches`、`::test_graph_is_scoped_by_owner` | HTTP | 通过 |
| N1-07 | 删除/换代期间不发布旧代次产物 | `run_summary_job` 二次复核 | 代码路径；未在 LLM 调用中途注入删除 | 组件 | **部分** |
| N1-08 | 旧文档/索引失败文档可按原文件补建锚点 | `ensure_source_version` | `::test_legacy_document_anchors_are_built_on_demand` | 内存库 + 假解析 | 通过 |
| N2-01 | 导图有节点上限、深度上限、节点保留锚点 | `build_mindmap` | `::test_generation_filters_unverified_and_builds_mindmap_and_graph`、`::test_generation_helper_branches` | 纯函数 | 通过 |
| N2-02 | 文档/笔记洞察页、证据抽屉、导图渲染 | `DocumentInsightPage.vue`、`MindmapTree.vue` | `npm run lint`、`npm run build`、`npm run test:unit` | 构建/单测 | **部分**（未浏览器 E2E） |
| N3-01 | 概念/关系按 owner+space 归一，关系带证据 | `_persist_graph`、`/insights/graph` | `::test_generation_filters_unverified_and_builds_mindmap_and_graph`、`::test_api_remaining_branches` | HTTP + 内存库 | 通过 |
| N3-02 | 来源失效后无证据的边隐藏 | `_active_evidence` | `::test_graph_hides_relations_after_source_invalidation` | HTTP | 通过 |
| N3-03 | 用户确认/拒绝概念与关系 | `PATCH /graph/...` | `::test_graph_hides_relations_after_source_invalidation`、`::test_router_functions_direct` | HTTP/直调 | 通过 |
| N3-04 | 图谱页可探索节点、关系与证据 | `KnowledgeGraphPage.vue` | lint/build/unit | 构建/单测 | **部分**（未浏览器 E2E） |
| N4-01 | 迁移链可生成 MySQL DDL | `016_knowledge_insights` | `alembic heads` = 016；`alembic upgrade 015:016 --sql` 生成 7 张表 | 离线 DDL | 通过（**未应用业务库**） |
| N4-02 | 后端全量回归与覆盖率门禁 | 全仓库 | 1140 passed / 9 skipped；coverage 85.71%；ruff/mypy 通过 | 本地 | 通过 |
| N4-03 | 前端 lint/build/unit | `front` | 0 errors；build 成功；71 passed | 本地 | 通过 |
| N4-04 | 真实 MySQL/Chroma/Celery/浏览器 E2E | 运行环境 | 未执行 | 集成 | **未验证** |
| N4-05 | 真实 LLM 摘要质量评估 | 运行环境 | 未调用付费模型 | 人工/评测 | **未验证** |

## 未验证项说明

1. 迁移 016 只做了 Alembic 离线 DDL 生成，没有连接或修改业务 MySQL；上线前需在目标库执行 `alembic upgrade head`。
2. 真实 LLM 未调用；所有生成测试使用注入假模型，只证明结构、证据过滤和状态机。
3. 未在运行中的完整栈上执行“上传 → 索引 → 生成 → 图谱 → 删除”E2E；也未跑 Playwright。
4. v1 单传/SSE 的锚点落库只确认接线且既有上传测试不回归，没有直接断言 `source_anchors` 行；建议在隔离集成环境补一条真实上传断言。
5. `claim_and_run_job` 的原子领取只在单进程测试；双 worker 真并发留给隔离 MySQL/Redis 集成。
