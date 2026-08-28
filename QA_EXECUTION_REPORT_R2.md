# RAG-Notebook 质量保障执行报告 · 第二轮（R2）

- 报告日期：2026-08-27
- 方案依据：`QUALITY_ASSURANCE_PLAN_R2.md`（含修订意见）
- 执行方式：每任务独立提交（`ci:` / `test:` / `fix:` / `chore:` / `docs:`），全程测试常绿
- 最终状态：**CI 5 门禁全绿**，后端 520 passed / 6 skipped，覆盖率 60%

---

## 一、核心结论

第二轮质量保障方案按修订版全部执行完毕，后端总行覆盖率从 **31% 提升至 60%**（+29pp，超方案预测 55±5），并完成 mypy 门禁化、覆盖率底线落地、pre-commit 与 CI 硬化。

| 指标 | 执行前 | 执行后 |
|---|---|---|
| 后端测试 | 287 passed / 6 skipped | 520 passed / 6 skipped |
| 总覆盖率 | 31% | **60%** |
| CI 覆盖率底线 | 无 | **57%**（`--cov-fail-under=57`） |
| mypy（首批四目录） | 43 errors | **0**，转正为门禁 |
| 必需检查 | 4 个 | **5 个**（含 Backend type check） |

---

## 二、各批次执行明细

### ① 收尾（`ci:` ×2）
- CI 四处 `uv sync` 统一为 `--locked` 并移除 `pip install` 回退分支（防止锁漂移被静默掩盖）。
- `front/package.json` 声明 `engines: node>=22`，新增 `front/.npmrc` `engine-strict=true`，防 Node 版本事故复发。
- 根目录新增 `.pre-commit-config.yaml`：backend 改动触发 ruff、front 改动触发 eslint；`pre-commit` 入 dev extras，README 附启用说明。
- README 增补两条长效规范：覆盖率底线递推、环境版本三元组同步。
- branch protection 落实为 5 个必需检查（见第四节）。

### ② B1 —— app/rag 数据面（`test:` 共 8 提交）
| 文件 | 覆盖前 | 覆盖后 |
|---|---|---|
| text_spliter.py | 0% | **100%** |
| task_queue.py | 0% | **100%** |
| reranker.py | 0% | **100%** |
| retrievers/hybrid_retriever.py | 0% | **100%** |
| md5_manager/md5_store.py | 0% | **91%** |
| vector_store.py | 29% | **85%** |
| retrieval_service.py | 34% | **80%** |
| document_handler/processor.py | 0% | **94%** |

要点：向量库/BM25 用桩与记录型假类替代（真实 rank_bm25/numpy 在覆盖率追踪下的线程内首导会触发 numpy C 扩展重复加载错误，已规避）；过滤下推（user_id / `$and` space）、候选 k、重排与双路降级均有断言。

### ③ B2 —— utils 文件处理链路（`test+fix:` ×2）
| 文件 | 覆盖前 | 覆盖后 | mypy |
|---|---|---|---|
| file_handler.py | 0% | **95%** | 清零 |
| image_extractor.py | 0% | **90%** | — |
| pdf_multimodal_loader.py | 0% | **74%** | 清零 |
| vision_service.py | 0% | **91%** | 清零 |

DOCX 用真实 python-docx 验证段落+表格提取；PDF 图片提取走真实 PyMuPDF 构造含嵌入 PNG 的 PDF 全流程；视觉模型服务整体为可编程假实现（含双后端分派与批量响应三级容错解析）。

### ④ mypy 清零并转门禁（`fix:` + `ci:`）
- 首批四目录（app/core、app/schemas、app/utils、app/config）43 → 0。
- 关键修复：factory 可选依赖回退用 `Any` 别名承载、模型构造统一经 `_new` 垫片规避第三方签名桩滞后误报、runtime_config 数值边界缺省以 int64 极值兜底、SQLAlchemy 声明式 Base 加注明理由的精准 ignore。
- 顺手修正 `[tool.mypy]` 段区错位（吞掉了 optional-dependencies 的 `full` extra）。
- CI typecheck job 移除 `continue-on-error` 转正，branch protection 加入必需检查。

### ⑤ B3/B4 —— services / tasks / repositories / agentic（`test:` 共 6 提交）
| 文件 | 覆盖前 | 覆盖后 |
|---|---|---|
| repositories/document_index_repository.py | 0% | **100%** |
| repositories/agent_run_repository.py | 0% | **98%** |
| services/document_index_service.py | 20% | **83%** |
| tasks/index_task.py | 16% | **84%** |
| services/review_service.py | 22% | **88%** |
| agentic/tools.py | 0% | **60%** |
| agentic/answer_generator.py | 21% | **90%** |

仓储与状态机均以真实 SQLite 内存库驱动 ORM，验证 user_id 过滤确实进入 SQL；`(user_id, original_filename)` 唯一约束被测试实证。agentic 工具与答案生成器覆盖拒答、隔离透传、超时/异常降级与 LLM-as-judge 容错。

### ⑥ 新基线落盘（`ci+docs:`）
- 实测总覆盖率 60%，CI 加 `--cov-fail-under=57`（实测−3，只升不降）。
- `QUALITY_ASSURANCE_PLAN_R2.md` 新增第六章「执行结果」记录验收对照与遗留登记。

---

## 三、执行中发现并登记的真实问题

1. **`listdir_allowed_type` docstring 与实现不符**：声明"仅返回文件"，实现只按扩展名过滤，目录名匹配扩展名会混入结果（登记，R3 修复并收紧测试断言）。
2. **顶层恢复 langchain 栈会跨文件污染**：最初在 document_index_service 测试顶层恢复 LANGCHAIN_STACK 导致 text_spliter 全量挂掉；已收敛为"零全局副作用"注入模式（MD5 注入 hashlib、VectorStoreService 经 `sys.modules` mock 模块挂桩）。
3. **覆盖率追踪 × numpy 线程内首导**：真实 rank_bm25/numpy 在工作线程首次导入会报 "cannot load module more than once per process"，以 BM25 桩规避（已记录原因）。

## 四、CI 与分支保护现状

- **5 个必需检查**：Backend tests（Ruff + pytest + 覆盖率 57% 底线）、Django user/file tests、Frontend build（ESLint + Vitest + build）、Frontend E2E（mocked）、Backend type check（mypy）。
- **2 个 advisory**：Dependency audit（pip-audit / npm audit，含存量 CVE 基线，消化后转门禁）。
- **分支保护**：禁 force push、禁删除分支、检查不通过禁止合并。

## 五、遗留事项（转 R3）

1. router 层鉴权负路径：`org_router`(0%)、`note_router`(0%)、`agent_router`(0%)、`space_router`(31%) 等 401/403 分支未测（依赖完整 FastAPI 启动栈）。
2. `note_service`(35%) CRUD 分支补漏（权限隔离核心场景已有 `test_note_user_isolation` 覆盖）。
3. 前端 P2：Vitest 单测扩至 ~50、coverage 报表接入。
4. security P3.2：security job 拆分、五依赖升级 triage（starlette 需连带 fastapi 大版本，预计豁免登记）、Django ruff 接入。

---

*本报告由质量保障执行过程自动生成，与 `QUALITY_ASSURANCE_PLAN_R2.md` 第六章互为印证。*
