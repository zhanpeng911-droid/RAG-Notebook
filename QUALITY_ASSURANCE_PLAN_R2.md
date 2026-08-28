# Notebook 质量保障改进方案 · 第二轮（R2）

> 前置：第一轮方案（`QUALITY_ASSURANCE_PLAN.md`，P0–P2）已于 2026-08-26 全量落地并经第三方复核属实（CI 四门禁全绿 / ruff 清零 / Vitest+E2E 进 CI / mypy 与 security 为 advisory 基线）。本文件是**下一轮**工作说明书。执行原则同上轮：每任务独立提交（`test:` / `ci:` / `fix:` / `chore:` 前缀），不顺手重构业务代码，新发现问题记入文末「遗留问题登记」。
> 执行环境：Windows + Git Bash + uv（Python 侧）/ npm（Node ≥22）。

---

## 一、起点快照（2026-08-26 复核实测，无需重新测量）

- 后端：`uv sync --extra dev && uv run pytest tests -q --cov=app` → **287 passed / 6 skipped**，约 50 秒。
- 总覆盖率 **31%**（6551 语句 / 4541 未覆盖）。已测得好的部分：agentic/planner 90%、state 95%、graph 71%，models 与 schemas 100%，note_repository 82%，knowledge 相关 service 83–100%。
- 前端：Vitest **16 个单测**（sseClient 9 + http 7）；ESLint **0 error / 4 个 v-html warning**；Playwright mocked E2E 运行时 **39 用例**在 CI（chromium）。
- mypy advisory 基线：**43 errors / 12 files**（分布见下表）。
- CI 门禁：backend（ruff+pytest+cov）、django、frontend（lint+vitest+build）、frontend-e2e、compose；advisory：mypy、security（pip-audit + npm audit）。

### 本轮主攻依据：盲区不是均匀分布的，集中在两片

**① app/rag 数据面在全量套件下接近裸奔（这是 RAG 项目的地基）：**

| 文件 | 语句数 | 覆盖 |
|---|---|---|
| `app/rag/vector_store.py` | 283 | **29%** |
| `app/rag/document_handler/processor.py` | 140 | **0%** |
| `app/rag/md5_manager/md5_store.py` | 138 | **0%** |
| `app/rag/text_spliter.py` | 75 | **0%** |
| `app/rag/retrievers/hybrid_retriever.py` | 70 | **0%** |
| `app/rag/task_queue.py` | 41 | **0%** |
| `app/rag/reranker.py` | 38 | **0%** |
| `app/rag/retrieval_service.py` | 210 | 34% |

**② utils 文件处理链路三个整文件 0%（同时也是 mypy 错误聚集地）：**
`utils/pdf_multimodal_loader.py`（239 句，0%）、`utils/vision_service.py`（193 句，0%）、`utils/file_handler.py`（188 句，0%）、`utils/image_extractor.py`（61 句，0%）。

其余已知低覆盖：`services/document_index_service.py` 20%（265 句）、`services/note_service.py` 35%、`services/review_service.py` 22%、`tasks/index_task.py` 16%、`repositories/agent_run_repository.py` 0%（61 句）、`repositories/document_index_repository.py` 0%（64 句）、`router/space_router.py` 31%、`router/user.py` 0%、`agentic/answer_generator.py` 21%、`agentic/tools.py` 0%。

### mypy 43 错误分布（清零任务用）

`utils/factory.py` ×15、`core/runtime_config.py` ×6、`utils/vision_service.py` ×4、`utils/pdf_multimodal_loader.py` ×4、`utils/file_handler.py` ×4、`models/chat_history.py` ×4、`utils/auth_utils.py` ×1、`models/document_index.py` ×1、`core/{rate_limit,permission,logger_handler,failed_response}.py` 各 ×1。

### 遗留安全债（上轮 advisory 扫出的已知 CVE，待升级）

starlette、pyasn1、pypdf、requests、pydantic-settings、langchain-classic 存在可用升级版本，多数属依赖锁未跟进而非代码问题。

---

## 二、任务清单

### P0-A：小收尾（半天内）

1. **CI 同步纪律**：所有 `uv sync` 步骤改为 `uv sync --locked` 并去掉 `|| pip install ...` 回退分支（回退会静默掩盖锁漂移，与第一轮 Node 版本事故同类风险）。若 `--locked` 在 CI 报锁不一致，先本地 `uv lock` 更新并单独提交。
2. **Node 版本防复发**：`front/package.json` 增加 `"engines": { "node": ">=22" }`；今后升级 Node 必须同步改 CI 三处 + engines 字段（写进 README 规范）。
3. **branch protection 落实确认**（GitHub 后台管理员操作）：四个门禁 job 设为必需检查；两个 advisory 暂不勾选必需。截图留档即可。
4. **pre-commit hooks**：`.pre-commit-config.yaml` 挂 ruff（backend 目录）与 eslint（front 目录）；README 开发章节补一行启用说明。

### P1：后端覆盖率深水区（核心工作量，按 B1→B4 顺序逐批提交）

约定：外部依赖一律 mock——ChromaDB 用假 collection/fake embedding（定长随机向量即可），LLM/DashScope 用 monkeypatch，Celery 用 eager 模式或直接调任务函数；每批完成跑全量保持常绿。

**B1 —— app/rag 数据面（最高优先：RAG 地基，~800 条语句几乎零覆盖）**

- `text_spliter.py`：分块边界（超长段落、中英文混排、表格）、重叠窗口正确性、空文档容错。纯函数，最容易写也最该先写。
- `md5_manager/md5_store.py`：去重命中/未命中、哈希冲突路径、存储损坏恢复。
- `retrievers/hybrid_retriever.py` + `bm25_tokenizer.py`：向量路与 BM25 路结果融合排序、单路为空时的退化、jieba 缺失回退分词。
- `reranker.py`：重排分数单调性、rerank 服务不可用时跳过重排的降级路径。
- `vector_store.py`（最大头）：增删查基本环、元数据过滤、collection 不存在初始化、批量写入部分失败的处置。
- `task_queue.py` + `processor.py`：解析 PDF/docx 用仓库内小型测试 fixture 文件（放 `tests/fixtures/`），断言提取文本非空与元数据完整。
- `retrieval_service.py` 从 34% 往 70%+ 推：重点是 CRAG 纠错回路（置信度 none 时改写查询 + top_k+3 二轮）与用户/空间隔离过滤。

**B2 —— utils 文件处理链路（补测试与补类型同一个 PR 做，联动 mypy 清零）**

- `pdf_multimodal_loader.py`、`vision_service.py`、`file_handler.py`、`image_extractor.py` 四个 0% 文件：外部 API（视觉模型调用）mock 掉，测文件类型分发、大小/类型校验拒绝分支、异常包装。
- 同时清掉这四个文件的 mypy 错误（共 12 个），隐式 Optional 直接改签名，不要用 `# type: ignore` 糊。

**B3 —— services + tasks 业务层**

- `document_index_service.py`（20%）：索引状态机流转（pending_index → indexed / failed → reindex）、文件名唯一性冲突、异步任务提交失败补偿。
- `note_service.py`（35%）与 `review_service.py`（22%）：权限隔离负路径（A 用户读 B 用户笔记必须失败）、回顾调度边界。
- `tasks/index_task.py`（16%）：任务重试次数上限、Beat 定时补偿触发条件。
- `database_session_manager.py`（21%）：会话获取/归还、引擎失效重建。

**B4 —— 边角收尾**

- `repositories/agent_run_repository.py`、`document_index_repository.py`（各 0%）：CRUD + 用户过滤条件确实进了 SQL（对照生成的语句或行为断言）。
- `router/space_router.py`（31%）、`router/user.py`（0%）：httpx AsyncClient/AStarlette TestClient 打接口，测鉴权失败 401/403 分支。
- `agentic/tools.py`（0%）、`answer_generator.py`（21%）：工具注册表完整性、答案生成对无证据输入的拒答路径。

**B1–B4 完成后**：实测新基线（预计 55%±5），在 CI backend job 的 pytest 命令加 `--cov-fail-under=<实测值−3>`，并把新基线数字更新到 QUALITY_ASSURANCE_PLAN 第五章附录。

### P2：前端单测第二批（第一轮优先级清单的 3–7 号未完成项）

1. 安装 `@vitest/coverage-v8`，`npm run test:unit` 出报表（先观察不卡线）。
2. 按 第一轮方案的既定优先级继续：
   - Pinia stores：`session.js`、`qaHistory.js`、`model.js`、`user.js`、`theme.js`（状态流转 + persist 行为）；
   - `src/router/index.js` 路由守卫（未登录重定向 / 登录后访问受限页）；
   - `RetrievalTrace.vue`、`QaHistoryPanel.vue` 的引用 `[n]` 归一化与置信度徽标映射；
   - `useChatWorkspace.js` / `useKnowledgeBase.js` 中可提纯的逻辑函数；
   - `config/features.js`、`config/api.js` 配置断言。
3. 目标：单测从 16 个扩到 **50 个左右**，前端 `services/store/router/composables` 四类目录不再有 0% 文件。

### P3：advisory 收紧路线图（两件事都要有明确完成判据）

1. **mypy 43 → 0 → 强制**：
   - 按上文分布表逐文件清零（`factory.py` 15 个是大头，多为隐式 Optional 与第三方缺标注，配合 pydantic/fastapi 已带类型可解大半）；
   - 全部清零后，把 CI typecheck job 的 `continue-on-error: true` 移除并入必需检查；
   - 再扩第二批目录：`app/agentic`、`app/services`，重复"advisory 观察 → 清零 → 强制"节奏。
2. **security advisory → 分级门禁**：
   - 先做升级 triage：六个待升级依赖逐个升 patch/minor 版本 + 跑全量回归（重点回归 starlette 升级后 SSE 与 multipart 行为、langchain-classic 升级后检索链路）；无法升级的记录豁免理由；
   - 之后 npm audit 维持 `--audit-level=critical` 门禁化；pip-audit 改为 `--fail-level=high` 进门禁（critical/high 阻断，medium 以下仅报告）。
   - DjangoUserService 目前完全没有 lint：加 ruff（同样默认规则集）进 django job，存量单独清理。

### P4：可选（时间富余再做，不设验收硬指标）

- black/isort 了断：当前是"声明了但不执行"。要么一次性格式化提交（`black app tests && isort app tests`，单独 commit）之后 CI 加 `--check`，要么从 dev extras 移除声明。不允许维持现状。
- Playwright `mobile-chrome` project 进 CI 或明确注释只保 chromium 的理由。
- `tests/e2e-full/` 全栈版建立发布前手工执行 checklist（写进 README）。

---

## 三、长效规范（新增两条，接续 README 既有四条）

5. **覆盖率底线递推规则**：每轮结束实测一次总覆盖，新底线 = 实测 − 3，只升不降，同步更新 CI 与方案附录。
6. **环境版本三元组同步规则**：任何运行时版本变更（Node/Python/关键依赖）必须同时更新 CI matrix、engines/requires-python 声明与 README，三者不一致视为 broken main。

## 四、执行顺序与验收总清单

建议顺序：P0-A（半天）→ P1-B1 → P1-B2 → P3.1 mypy 清零（与 B2 天然联动）→ P1-B3/B4 → P2 → P3.2 安全升级。

- [ ] P0-A：CI 全部 `--locked` 无回退；engines 字段就位；branch protection 确认；pre-commit 可用
- [ ] P1-B1：app/rag 各文件覆盖率 ≥60%（text_spliter/md5_store ≥80%），retrieval_service ≥70%
- [ ] P1-B2：四个 utils 文件脱离 0%，目标各 ≥60%，且 mypy 该四文件错误清零
- [ ] P1-B3/B4：所列 services/tasks/repositories/router 文件均 ≥60%；新基线写入附录并设 `--cov-fail-under`
- [ ] P2：前端单测 ~50 个，coverage 报表接入
- [ ] P3：mypy 转强制（第一批四目录零错误）；pip-audit 高危门禁化；Django ruff 接入
- [ ] P4：black/isort 有明确结局（二选一）

## 五、遗留问题登记（本轮执行中发现的新问题记在这里）

- （执行方填写）

---

## 六、执行结果（2026-08-27 复核）

方案已按修订版执行，最终状态与验收对照：

| 批次 | 结果 |
|---|---|
| ① 收尾 | ✅ CI 全 `--locked` 无回退；engines>=22 + engine-strict；branch protection 5 必需检查；pre-commit（ruff+eslint）可用 |
| ② B1 | ✅ app/rag 八文件全部脱离低覆盖（94/100/91/100/91/85/94/80%），总覆盖 31%→60% |
| ③ B2 | ✅ utils 四文件 95/90/74/91%；三文件 mypy 清零 |
| ④ mypy | ✅ 43→0，转正为门禁并加入 protection |
| ⑤ B3/B4 | ✅ 双仓储 99/100%、document_index_service 83%、index_task 84%、review_service 88%、tools 60%、answer_generator 90%（router 鉴权与 note_service 补漏留待 R3，见遗留） |
| ⑥ 基线 | ✅ 实测总覆盖 60%，CI `--cov-fail-under=57` |

### 新基线（写入 CI 与长效规范）
- 总覆盖率：**60%**（6560 语句 / 2621 未覆盖）
- CI 底线：57%（实测−3，只升不降）

> R3 更新：P1-B1（12 个 router ≥70%）与 P1-B2（services/tasks ≥80%）落地后，实测总覆盖 **86%**（898 passed / 6 skipped），CI `--cov-fail-under` 已递推至 **83**（86%−3，只升不降）。

### 遗留问题登记
1. `listdir_allowed_type` docstring 称"仅返回文件"，实现未做 isfile 校验（目录名匹配扩展名会混入）——R3 修复并收紧测试断言。
2. router 层整体低覆盖：`org_router`(0%)、`note_router`(0%)、`agent_router`(0%)、`space_router`(31%) 等鉴权负路径未测（依赖完整 FastAPI 启动栈，成本高，转入 R3）。
3. `note_service` 35% 存在 CRUD 分支缺口（权限负路径已由 test_note_user_isolation 覆盖核心场景）。
4. R2-P2（前端单测扩至 ~50）与 R2-P3.2（security 拆 job + 依赖升级）未执行，转入下一轮。
