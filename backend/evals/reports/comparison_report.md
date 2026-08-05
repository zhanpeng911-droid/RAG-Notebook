# Agentic RAG vs 传统 RAG 准确率对比报告

| 项目 | 内容 |
|------|------|
| **报告日期** | 2026-07-27 |
| **测试环境** | Windows / Python 3.13 / DeepSeek-v4-flash / qwen3.7-text-embedding |
| **测试文档** | 12 篇技术文档（Redis/GIL/TCP/Docker/Kafka/MySQL/Git/FastAPI/Nginx/RabbitMQ/ES/Prometheus） |
| **测试题目** | 27 道（24 道事实题 + 3 道超出范围拒答题） |
| **测试用户** | eyaVCedfBTNbbdTTeFMu7G |

---

## 1. 两条链路说明

### 传统 RAG（即项目前一代设计）

| 环节 | 实现 |
|------|------|
| 文本切分 | RecursiveCharacterTextSplitter，chunk_size=500, chunk_overlap=60 |
| Query 改写 | HyDE（生成假设性文档用于检索） |
| 检索 | 混合检索：向量（ChromaDB）+ BM25，动态权重（长查询 7:3，短查询 3:7） |
| 重排 | reorder_service 精排，top10 → top5 |
| 生成 | 分批摘要：对 top3 文档分别 LLM 总结 → 合并摘要 → 最终总结（3 次 LLM 调用） |
| 引用 | 无结构化引用，仅在摘要中标注来源 |

### Agentic RAG（当前设计）

| 环节 | 实现 |
|------|------|
| 文本切分 | 同上（共用 VectorStoreService） |
| 检索规划 | Planner 按查询类型（factual/explanatory/comparative/procedural）动态调整 scope/top_k/HyDE |
| 检索 | RetrievalService 统一检索（知识库+笔记），Evidence 结构化 |
| 证据评分 | EvidenceGrader 基于相关性分数评估证据是否充分（阈值 0.3/0.4） |
| Query 改写 | 证据不足时自动改写查询并重新检索（最多 2 轮） |
| 生成 | AnswerGenerator 基于全部证据一次性生成，带结构化引用 [n] |
| 防护 | Guardrails：查询清洗（防注入）、超时控制（45s）、轮数限制 |

---

## 2. 核心对比结果

| 维度 | 传统 RAG | Agentic RAG | 提升 |
|------|----------|-------------|------|
| **事实正确率** | 83.3% (20/24) | **100.0%** (24/24) | **+16.7pp** |
| **拒答正确率** | 100.0% (3/3) | 100.0% (3/3) | 0 |
| **检索召回率** | 95.8% (23/24) | **100.0%** (24/24) | **+4.2pp** |
| **综合正确率** | 85.2% (23/27) | **100.0%** (27/27) | **+14.8pp** |
| 平均耗时(秒) | 19.6s | 13.4s | Agentic 快 31.6% |
| 平均检索轮数 | 1.0 | 1.0 | - |

> pp = percentage points（百分点）

---

## 3. 逐题详情

| ID | 问题 | 传统 | Agentic | 传统耗时 | Agentic耗时 |
|----|------|:----:|:-------:|--------:|-----------:|
| cmp-001 | Redis AOF fsync 策略 | ✓ | ✓ | 10.6s | 12.5s |
| cmp-002 | Redis 4.0 混合持久化 | ✓ | ✓ | 18.0s | 13.6s |
| cmp-003 | Python GIL 原理 | ✓ | ✓ | 21.2s | 14.2s |
| cmp-004 | 绕过 GIL 的方法 | ✓ | ✓ | 26.6s | 2.1s |
| cmp-005 | TCP 三次握手 | ✓ | ✓ | 17.6s | 15.0s |
| cmp-006 | TIME_WAIT 等待 2MSL | ✓ | ✓ | 19.2s | 19.4s |
| cmp-007 | Docker vs 虚拟机 | ✓ | ✓ | 23.6s | 15.4s |
| cmp-008 | CMD vs ENTRYPOINT | ✓ | ✓ | 19.5s | 13.1s |
| cmp-009 | Kafka ISR | ✓ | ✓ | 19.3s | 18.0s |
| cmp-010 | Kafka 消息顺序性 | **✗** | ✓ | 20.9s | 2.5s |
| cmp-011 | MySQL 聚簇索引 vs 二级索引 | **✗** | ✓ | 20.0s | 20.9s |
| cmp-012 | MySQL 最左前缀原则 | ✓ | ✓ | 21.0s | 20.0s |
| cmp-013 | Git rebase vs merge | ✓ | ✓ | 24.5s | 14.7s |
| cmp-014 | Git rebase 黄金法则 | ✓ | ✓ | 19.7s | 14.4s |
| cmp-015 | FastAPI async vs def | **✗** | ✓ | 23.6s | 19.3s |
| cmp-016 | FastAPI 422 状态码 | ✓ | ✓ | 15.6s | 12.0s |
| cmp-017 | Nginx 负载均衡策略 | ✓ | ✓ | 19.8s | 18.4s |
| cmp-018 | Nginx max_fails/fail_timeout | ✓ | ✓ | 21.2s | 24.8s |
| cmp-019 | RabbitMQ Exchange 类型 | **✗** | ✓ | 16.3s | 14.3s |
| cmp-020 | RabbitMQ 死信队列 | ✓ | ✓ | 14.7s | 11.3s |
| cmp-021 | ES 倒排索引 | ✓ | ✓ | 24.0s | 3.4s |
| cmp-022 | ES BM25 评分 | ✓ | ✓ | 10.9s | 10.5s |
| cmp-023 | Prometheus 指标类型 | ✓ | ✓ | 19.9s | 13.4s |
| cmp-024 | Prometheus 采集方式 | ✓ | ✓ | 12.9s | 8.3s |
| cmp-025 | K8s Pod 调度（超出范围） | ✓ | ✓ | 24.3s | 2.1s |
| cmp-026 | Rust 所有权机制（超出范围） | ✓ | ✓ | 17.3s | 7.5s |
| cmp-027 | PBFT vs Raft（超出范围） | ✓ | ✓ | 25.6s | 19.4s |

---

## 4. 传统 RAG 失败题目分析

### cmp-010：Kafka 如何保证消息顺序性？

- **期望关键词**：分区、有序、跨分区
- **传统 RAG 答案**：提到了"分区"和"有序"，但**遗漏了"跨分区不保证顺序"**这一关键事实点
- **失败原因**：分批摘要策略中，单文档摘要时丢失了原文中"跨分区不保证顺序"的细节
- **Agentic RAG**：基于完整证据上下文一次性生成，保留了全部关键信息

### cmp-011：MySQL 聚簇索引和二级索引区别？

- **期望关键词**：聚簇索引、二级索引、回表、主键
- **传统 RAG 答案**：*"没有关于MySQL InnoDB聚簇索引与二级索引区别的内容，因此无法回答"*
- **失败原因**：检索**召回成功**（retrieval_hit=true），但分批摘要时 LLM 误判文档内容不相关而拒答
- **Agentic RAG**：证据评分机制确认证据充分后直接生成，正确包含全部关键词

### cmp-015：FastAPI async def 和普通 def 区别？

- **期望关键词**：async、线程池、事件循环、阻塞
- **传统 RAG 答案**：*"不包含关于 FastAPI 中 async def 与普通 def 路由函数区别的信息"*
- **失败原因**：**检索召回失败**（retrieval_hit=false），HyDE 生成的假设性文档与 FastAPI 文档语义不匹配
- **Agentic RAG**：Planner 将查询分类为 explanatory，调整检索参数成功召回

### cmp-019：RabbitMQ 有哪几种 Exchange 类型？

- **期望关键词**：Direct、Fanout、Topic、Headers
- **传统 RAG 答案**：*"未包含RabbitMQ Exchange类型的相关信息，无法回答"*
- **失败原因**：检索**召回成功**（retrieval_hit=true），但分批摘要时丢失了 4 种类型的具体名称
- **Agentic RAG**：一次性基于完整证据生成，4 种类型全部正确列出

---

## 5. 关键发现

### 5.1 事实正确率：Agentic +16.7pp

传统 RAG 的 4 次失败中，有 3 次（cmp-011/015/019）是**检索到了文档但生成阶段丢失信息**，1 次（cmp-010）是摘要遗漏关键细节。根本原因是传统 RAG 的"分批摘要→合并"策略：

```
传统 RAG：文档1→LLM摘要  文档2→LLM摘要  文档3→LLM摘要  →  合并摘要  →  最终总结
                                    ↑ 每步都可能丢失信息，累计放大
```

```
Agentic RAG：全部证据 → 一次性生成答案（带引用）
                        ↑ 信息无损
```

### 5.2 拒答正确率：两者持平（100%）

3 道超出知识库范围的题（K8s/Rust/区块链），两条链路都正确拒答。但行为有差异：
- 传统 RAG 的 HyDE 会先生成大段假设性文档（编造内容），只是最终摘要时没采纳
- Agentic RAG 检索到证据后，证据评分判定不充分直接拒答，更干净

### 5.3 效率：Agentic 反而更快（13.4s vs 19.6s）

反直觉但合理：
- 传统 RAG 固定 3 次 LLM 调用（3 个文档分别摘要）+ 1 次合并 = 4 次 LLM 调用
- Agentic RAG 证据充足时只需 1 次检索 + 1 次生成 = 1 次 LLM 调用（本测试 24/24 题第一轮就通过，平均检索轮数=1.0）

### 5.4 检索召回率：Agentic +4.2pp

传统 RAG 在 cmp-015（FastAPI）上检索召回失败。原因是 HyDE 生成的假设性文档与目标文档语义偏移。Agentic 的 Planner 根据查询类型动态调整检索参数（scope/top_k），适应性更强。

---

## 6. 结论

在相同文档库（12 篇）、相同题目（27 道）、相同 LLM（DeepSeek-v4-flash）、相同嵌入模型（qwen3.7-text-embedding）的条件下：

| 指标 | 结果 |
|------|------|
| 综合正确率提升 | **+14.8 个百分点**（85.2% → 100%） |
| 事实正确率提升 | **+16.7 个百分点**（83.3% → 100%） |
| 检索召回率提升 | **+4.2 个百分点**（95.8% → 100%） |
| 平均响应时间 | **降低 31.6%**（19.6s → 13.4s） |

Agentic RAG 相比前一代传统 RAG 设计，在准确率和效率上均有提升，核心优势在于：
1. **证据评分机制**避免"检索到了但生成时丢失信息"的问题
2. **一次性生成**替代分批摘要，消除信息损耗累计
3. **动态检索规划**比固定 HyDE 适应性更强
4. **结构化引用**提升答案可信度

---

## 附录：测试数据与脚本

| 文件 | 说明 |
|------|------|
| `backend/evals/seed_docs/*.txt` | 12 篇测试文档 |
| `backend/evals/cases/comparison_cases.jsonl` | 27 道测试题 |
| `backend/evals/runners/run_comparison.py` | 对比测试脚本 |
| `backend/evals/reports/comparison_report.json` | 机器可读报告（含逐题完整答案） |

复现命令：
```bash
cd backend
uv run python -m evals.runners.run_comparison
```
