# 知识工作台迭代交付（N0–N4）

日期：2026-10-20。基线：`8f44d1f` 加工作区既有整改改动（未提交）。
本文记录本轮“可验证来源的自动知识结构化与知识图谱”迭代的实际实现、验证结果与未完成项，不替代整改记录。

## 1. 交付状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| N0 | 来源版本、原文锚点、证据核验、版本失效 | 已实现并通过组件/HTTP 测试 |
| N1 | 结构化摘要任务、证据过滤、任务状态与重试 | 已实现并通过组件/HTTP 测试 |
| N2 | 文档级思维导图、文档洞察前端页 | 已实现；前端 lint/build/unit 通过，未跑浏览器 E2E |
| N3 | 跨文档概念/关系图谱、证据面板、确认/拒绝 | 已实现；组件/HTTP 测试通过，未跑浏览器 E2E |
| N4 | 文档、README、验收矩阵、门禁 | 已完成本地门禁；**迁移 016 未应用到业务库** |

明确未验证/未执行：

- **未对业务 MySQL 执行迁移 016**（无迁移授权）。已用 Alembic 离线模式生成并核查 MySQL DDL。
- **未调用真实付费 LLM**。生成链路使用注入的假模型验证结构、证据过滤和状态机；真实模型质量未评估。
- **未在运行中的 MySQL + Chroma + Celery + 前端上执行完整上传→洞察→图谱 E2E**。
- 未实现“上传后自动生成”开关；当前为手动触发（文档行按钮 / 笔记编辑页按钮）。
- PPTX 页码依赖加载器提供的 `slide_number/page_number`；缺失时锚点降级为片段，不伪造页码。
- 图谱接口有上限（概念 200、关系 500，查询参数可调至 500），暂未做游标分页。

## 2. 数据模型

迁移：`backend/alembic/versions/016_knowledge_insights.py`（`015_legacy_mappings -> 016_knowledge_insights`）。

| 表 | 作用 | 关键约束 |
|---|---|---|
| `knowledge_source_versions` | 源资源在某个代次/版本下的身份 | 唯一 `(source_type, source_id, generation)` |
| `source_anchors` | 可核验原文片段 | 唯一 `(source_version_id, chunk_index)`；owner/source/generation 冗余授权字段 |
| `knowledge_generation_jobs` | 洞察生成任务 | 唯一 `(source_version_id, artifact_type)` |
| `knowledge_artifacts` | 摘要/导图结构化产物 | 唯一 `(source_version_id, artifact_type)` |
| `knowledge_evidence` | 结论/概念/关系 → 锚点证据 | 唯一 `(owner_user_id, target_kind, target_id, anchor_id)` |
| `knowledge_concepts` | owner + space 归一概念 | 唯一 `(owner_user_id, space_key, normalized_name)` |
| `knowledge_relations` | 概念关系 | 唯一 `(owner_user_id, space_key, source, target, relation_type)` |

### 2.1 锚点定位规则

- PDF：`page_number`/`slide_number` 按 1 起；LangChain `page` 按 0 起转 1 起；两者都没有则 `page_no=NULL`。
- Markdown：`#` 标题生成章节路径；TXT/PPTX 无标题结构时不生成章节。
- DOCX：读取 `python-docx` 的真实 `Heading N`/`标题 N` 样式并记录字符偏移；不猜测正文里的“像标题”的行。
- 所有格式：切片在源文本中定位成功才写 `char_start/char_end`；定位失败保留真实引文但偏移为空，`locator_kind` 降级为 `page/section/chunk`。
- 每个锚点保存规范化引文的 SHA256（`quote_hash`），用于证据核对。

### 2.2 证据与失效

- 摘要中的每条事实性结论必须引用本版本锚点；模型给出的不存在的锚点 ID、找不到的引文都会被剔除。
- 生成期间源文档被删除/换代：任务置 `stale`，不发布旧代次产物。
- 文档重新发布：同一事务激活新来源版本，旧版本、旧产物、旧证据置 `stale`。
- 文档删除：版本置 `invalid`，产物/证据 `stale`，排队/运行中的任务 `stale`；锚点保留审计但不可读。
- 笔记保存：生成新版本并激活；旧版本/旧产物/旧证据失效。笔记删除：版本 `invalid`。
- 图谱接口只返回 `verification_status=verified` 且来源版本仍 active 的证据；没有有效证据的边隐藏。

## 3. 接入点

| 入口 | 接线 |
|---|---|
| v2 统一索引执行器 `execute_index_document` | 切片后记录锚点（pending）；发布事务内激活版本 |
| v1 单文件上传 | `DocumentProcessor.get_document` 返回本次真实切片的 source/split/chunk_ids；上传成功后记录锚点，发布事务内激活 |
| v1 SSE 上传 | `SliceResult` 携带 source_documents；写入前记录锚点；发布事务内激活 |
| 文档删除 `_delete_single_record` | 删除事务内失效来源版本/产物/证据 |
| 笔记创建/更新/删除 | `note_service` 同步来源版本并失效旧产物 |
| 旧文档/索引失败文档 | 首次生成洞察时从原文件按需重建锚点（不依赖 embedding） |

锚点写入失败不会阻断既有上传/索引主链路，只记录告警；洞察生成时会按需重建。

## 4. API

统一前缀 `/api/v1/insights`，全部要求 JWT；资源不存在与无权限统一 404。

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/documents/{document_id}/generate` | 提交文档摘要任务；`force=true` 重新生成 |
| `POST` | `/notes/{note_id}/generate` | 提交笔记摘要任务 |
| `GET` | `/jobs/{job_id}` | 任务状态/错误/尝试次数 |
| `GET` | `/documents/{document_id}` | 当前 active 摘要 + 分组证据 |
| `GET` | `/documents/{document_id}/mindmap` | 当前 active 思维导图 |
| `GET` | `/notes/{note_id}` / `/notes/{note_id}/mindmap` | 笔记洞察 |
| `GET` | `/anchors/{anchor_id}?quote=` | 读取证据；可选引文复核 |
| `GET` | `/graph?space_id=&q=&limit=` | 概念/关系子图 |
| `GET` | `/graph/nodes/{concept_id}` | 概念详情 + 证据 |
| `GET` | `/graph/relations/{relation_id}` | 关系详情 + 证据 |
| `PATCH` | `/graph/nodes/{concept_id}` | 确认/拒绝概念 |
| `PATCH` | `/graph/relations/{relation_id}` | 确认/拒绝关系 |

摘要 payload：

```json
{
  "schema_version": "1.0",
  "trust_status": "verified | insufficient_evidence",
  "overview": {"text": "...", "anchor_ids": ["..."]},
  "key_points": [{"id": "kp-0", "text": "...", "anchor_ids": ["..."]}],
  "sections": [{"id": "sec-0", "title": "...", "summary": "...",
                "anchor_ids": ["..."], "claims": [{"id": "sec-0-claim-0", "text": "...", "anchor_ids": ["..."]}]}],
  "terms": [{"id": "term-0", "name": "...", "description": "...", "anchor_ids": ["..."], "aliases": []}],
  "open_questions": [{"id": "oq-0", "text": "...", "anchor_ids": ["..."]}],
  "relations": [{"id": "rel-0", "source": "...", "target": "...", "relation_type": "depends_on", "anchor_ids": ["..."]}],
  "meta": {"prompt_version": "insight-v1", "anchor_count": 12,
           "verified_targets": 8, "dropped_unverified": 2}
}
```

`insufficient_evidence` 表示没有任何事实性结论通过核验：`overview.text` 为空、未核验结论被剔除，前端显示明确警告。

## 5. 前端

| 页面/组件 | 说明 |
|---|---|
| `front/src/pages/knowledge/DocumentInsightPage.vue` | 摘要 + 章节/概念/待确认 + 证据抽屉 + 思维导图；文档与笔记共用 |
| `front/src/pages/knowledge/KnowledgeGraphPage.vue` | 概念/关系圆形布局、筛选、证据面板、确认/拒绝 |
| `front/src/components/EvidenceChips.vue` | 结论旁的证据入口 |
| `front/src/components/MindmapTree.vue` | 递归导图树 |
| `front/src/services/insightApi.js` | 洞察 API 封装 |

路由：

- `/knowledge/:id/insight`：文档洞察；
- `/notes/:id/insight`：笔记洞察（笔记编辑页“AI 洞察”先保存再跳转）；
- `/graph`：知识图谱。

## 6. 运行与迁移

```powershell
cd backend
# 迁移 016（需自行确认目标库；本轮未对业务库执行）
alembic upgrade head
```

Celery：

- `celery_app.conf.include` 已加入 `app.tasks.insight_task`；
- `generate_insight_task` 执行摘要；
- Beat 每 10 分钟执行 `reclaim_stale_insight_jobs_task`，把 running 超过 30 分钟的任务置 `failed/execution_timeout`；
- 重复投递安全：worker 只原子领取 `queued` 任务。

生成失败/重新生成：

1. `GET /insights/jobs/{job_id}` 查看 `error`；
2. 常见错误：`llm_output_invalid_json`、`llm_output_schema_invalid`、`no_source_anchors`、`source_file_missing`（v1 旧上传无持久文件，需要重新上传）；
3. 重新生成：`POST .../generate`，`{"force": true}`。

## 7. 验证结果

后端（`backend/.venv`）：

```text
python -m pytest tests --ignore=tests/functional -q --runxfail --no-cov -p no:cacheprovider
1140 passed, 9 skipped

python -m pytest tests --ignore=tests/functional -q --runxfail -p no:cacheprovider \
  --cov=app --cov-report=term --cov-fail-under=85
1140 passed, 9 skipped, coverage 85.71%

python -m ruff check app tests
All checks passed

python -m mypy app/core app/schemas app/utils app/config
Success: no issues found in 26 source files
```

前端：

```text
npm run lint        # 0 errors（剩余 4 个既有 v-html 警告）
npm run build       # 成功
npm run test:unit   # 71 passed
```

本轮新增测试：`backend/tests/test_knowledge_insights.py`（26 个用例）、`backend/tests/test_knowledge_anchor_real_files.py`（6 个真实文件用例），并扩展 `test_file_handler.py`、`test_celery_app.py`。
逐项映射见 [ITERATION_ACCEPTANCE_MATRIX.md](ITERATION_ACCEPTANCE_MATRIX.md)。

## 8. 已知边界与后续工作

1. **真实模型质量未评估**：本轮只证明结构、证据和状态机；摘要正确率需要真实模型 + 人工抽样。
2. **真实数据库/Chroma 集成未跑**：迁移仅离线生成 SQL；未执行 MySQL 双连接、Chroma 迟到写入、进程退出等集成场景。
3. **PPTX 页码**：取决于加载器是否提供页码；没有时明确降级，不伪造。
4. **v1 旧上传无持久文件**：`file_path=""` 的文档无法生成洞察，返回 `source_file_missing`，需要重新上传；符合“没有原文件不猜路径”的约束。
5. **图谱分页**：当前上限内返回；更大规模需要游标分页。
6. **自动生成**：未实现上传后自动触发；可在后续通过显式配置开启，并复用同一持久任务表。
7. **补做 v1/SSE 锚点落库断言**：接线已完成且既有上传测试通过，但本轮没有对 v1/SSE 路径直接断言锚点行；列入后续集成测试。
