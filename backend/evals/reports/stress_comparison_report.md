# Agentic RAG vs 传统 RAG 压力测试报告

## 测试环境
- 文档数：102 篇 / 10 个技术领域
- 评估集：66 题 / 6 类意图 + 拒答
- LLM：deepseek-v4-flash
- Embedding：qwen3.7-text-embedding

## 核心对比

| 维度 | 传统 RAG | Agentic RAG | 提升 |
|------|----------|-------------|------|
| **综合正确率** | 61/66 (92.4%) | 63/66 (95.5%) | **+3.0pp** |
| **平均耗时** | 23.9s | 21.3s | 快 11.2% |

## 分意图对比

| 意图 | 题数 | 传统正确率 | Agentic正确率 | 传统耗时 | Agentic耗时 |
|------|------|-----------|-------------|---------|------------|
| simple | 10 | 9/10 (90%) | 9/10 (90%) | 18.3s | 17.9s |
| factual | 10 | 10/10 (100%) | 10/10 (100%) | 17.6s | 9.3s |
| explanatory | 10 | 8/10 (80%) | 9/10 (90%) | 29.1s | 14.8s |
| comparative | 10 | 10/10 (100%) | 10/10 (100%) | 23.6s | 26.9s |
| procedural | 10 | 8/10 (80%) | 9/10 (90%) | 23.0s | 27.4s |
| exploratory | 10 | 10/10 (100%) | 10/10 (100%) | 33.0s | 36.4s |
| out_of_scope | 6 | 6/6 (100%) | 6/6 (100%) | 22.3s | 12.9s |

## 逐题明细

| ID | 意图 | 传统 | Agentic | 传统耗时 | Agentic耗时 | 问题 |
|----|------|:----:|:-------:|--------:|-----------:|------|
| s-01 | simple | ✓ | ✓ | 17.15s | 4.16s | Redis默认fsync策略是什么 |
| s-02 | simple | ✓ | ✓ | 16.68s | 23.17s | MySQL默认隔离级别 |
| s-03 | simple | ✓ | ✓ | 16.72s | 21.61s | Docker和虚拟机的区别 |
| s-04 | simple | ✓ | ✓ | 15.51s | 12.26s | Python的GIL是什么 |
| s-05 | simple | ✓ | ✓ | 22.83s | 28.68s | TCP三次握手过程 |
| s-06 | simple | ✓ | ✓ | 18.9s | 4.68s | Kafka的ISR是什么 |
| s-07 | simple | ✓ | ✓ | 17.04s | 5.72s | Go的goroutine是什么 |
| s-08 | simple | ✓ | ✓ | 17.29s | 28.41s | B+树的特点 |
| s-09 | simple | ✓ | ✓ | 20.63s | 30.91s | RabbitMQ Exchange类型 |
| s-10 | simple | ✗ | ✗ | 20.52s | 19.3s | HTTP状态码422 |
| f-01 | factual | ✓ | ✓ | 27.96s | 6.61s | 什么是MySQL的MVCC？ |
| f-02 | factual | ✓ | ✓ | 24.59s | 24.52s | Docker的多阶段构建是什么？ |
| f-03 | factual | ✓ | ✓ | 14.6s | 23.59s | Python的装饰器是什么？ |
| f-04 | factual | ✓ | ✓ | 12.29s | 4.98s | Kubernetes的ConfigMap是什么？ |
| f-05 | factual | ✓ | ✓ | 26.88s | 4.07s | 什么是Redis的混合持久化？ |
| f-06 | factual | ✓ | ✓ | 10.42s | 5.3s | Go语言的channel是什么？ |
| f-07 | factual | ✓ | ✓ | 8.78s | 6.4s | 什么是Linux的inode？ |
| f-08 | factual | ✓ | ✓ | 12.51s | 5.56s | 什么是CDN内容分发网络？ |
| f-09 | factual | ✓ | ✓ | 25.53s | 4.91s | 什么是Kafka的消费者组？ |
| f-10 | factual | ✓ | ✓ | 12.74s | 7.56s | 什么是HTTPS的TLS握手？ |
| e-01 | explanatory | ✗ | ✗ | 29.35s | 33.21s | 为什么TCP需要三次握手而不是两次？ |
| e-02 | explanatory | ✓ | ✓ | 21.68s | 6.39s | 为什么Python有GIL？它的原理是什么？ |
| e-03 | explanatory | ✓ | ✓ | 29.94s | 5.98s | MySQL的redo log原理是什么？为什么需要它？ |
| e-04 | explanatory | ✓ | ✓ | 23.81s | 22.29s | 为什么Docker容器启动比虚拟机快？ |
| e-05 | explanatory | ✓ | ✓ | 21.04s | 11.71s | Kubernetes HPA自动扩缩容的原理是什么？ |
| e-06 | explanatory | ✗ | ✓ | 60.0s | 19.33s | 为什么TCP的TIME_WAIT要等待2MSL？ |
| e-07 | explanatory | ✓ | ✓ | 23.29s | 6.5s | Redis的RDB持久化原理是什么？ |
| e-08 | explanatory | ✓ | ✓ | 31.82s | 5.89s | Go的GMP调度模型原理是什么？ |
| e-09 | explanatory | ✓ | ✓ | 32.27s | 7.31s | MySQL MVCC实现原理是什么？ |
| e-10 | explanatory | ✓ | ✓ | 17.45s | 29.2s | 为什么Kafka能保证高吞吐？ |
| c-01 | comparative | ✓ | ✓ | 21.52s | 25.68s | Docker和虚拟机有什么区别？ |
| c-02 | comparative | ✓ | ✓ | 34.31s | 34.29s | MySQL InnoDB和MyISAM有什么区别？ |
| c-03 | comparative | ✓ | ✓ | 12.38s | 22.28s | TCP和UDP有什么区别？ |
| c-04 | comparative | ✓ | ✓ | 44.75s | 31.54s | Redis RDB和AOF有什么区别？ |
| c-05 | comparative | ✓ | ✓ | 14.96s | 30.0s | 正向代理和反向代理有什么区别？ |
| c-06 | comparative | ✓ | ✓ | 21.18s | 20.91s | Kubernetes Deployment和Stateful |
| c-07 | comparative | ✓ | ✓ | 17.39s | 18.71s | Go的Mutex和RWMutex有什么区别？ |
| c-08 | comparative | ✓ | ✓ | 37.03s | 42.8s | Kafka和RabbitMQ有什么区别？ |
| c-09 | comparative | ✓ | ✓ | 13.64s | 18.34s | Python多线程和多进程有什么区别？ |
| c-10 | comparative | ✓ | ✓ | 19.09s | 24.11s | Docker的CMD和ENTRYPOINT有什么区别？ |
| p-01 | procedural | ✗ | ✗ | 17.85s | 36.74s | 如何配置Nginx负载均衡？ |
| p-02 | procedural | ✓ | ✓ | 30.04s | 40.35s | 怎么优化MySQL慢查询？ |
| p-03 | procedural | ✓ | ✓ | 25.91s | 25.15s | 如何实现Redis分布式锁？ |
| p-04 | procedural | ✓ | ✓ | 23.48s | 27.04s | 怎么配置Kubernetes的Ingress？ |
| p-05 | procedural | ✓ | ✓ | 32.7s | 23.57s | 如何使用Docker多阶段构建减小镜像体积？ |
| p-06 | procedural | ✓ | ✓ | 18.26s | 26.73s | 怎么在Python中实现异步编程？ |
| p-07 | procedural | ✓ | ✓ | 21.52s | 20.63s | 如何配置Linux的crontab定时任务？ |
| p-08 | procedural | ✓ | ✓ | 18.19s | 25.32s | 怎么使用Go的context实现超时控制？ |
| p-09 | procedural | ✓ | ✓ | 27.52s | 18.71s | 如何配置Docker Compose的多容器应用？ |
| p-10 | procedural | ✗ | ✓ | 14.54s | 29.41s | 怎么在MySQL中创建联合索引？ |
| x-01 | exploratory | ✓ | ✓ | 27.69s | 31.02s | 关于Redis持久化机制的详细信息 |
| x-02 | exploratory | ✓ | ✓ | 39.95s | 37.3s | 关于MySQL索引优化的相关知识 |
| x-03 | exploratory | ✓ | ✓ | 29.15s | 38.11s | 关于Kubernetes调度机制的介绍 |
| x-04 | exploratory | ✓ | ✓ | 27.19s | 18.03s | 关于Python垃圾回收机制的信息 |
| x-05 | exploratory | ✓ | ✓ | 29.99s | 36.22s | 关于Docker网络模式的信息 |
| x-06 | exploratory | ✓ | ✓ | 29.56s | 41.63s | 关于Go语言并发模型的介绍 |
| x-07 | exploratory | ✓ | ✓ | 23.5s | 24.63s | 关于计算机网络负载均衡的知识 |
| x-08 | exploratory | ✓ | ✓ | 44.98s | 77.26s | 关于Kafka消息顺序性保证的信息 |
| x-09 | exploratory | ✓ | ✓ | 46.86s | 27.97s | 关于Linux进程管理的信息 |
| x-10 | exploratory | ✓ | ✓ | 30.75s | 31.85s | 关于HTTPS和TLS加密的知识 |
| o-01 | out_of_scope | ✓ | ✓ | 15.51s | 9.62s | 量子计算的量子比特原理是什么？ |
| o-02 | out_of_scope | ✓ | ✓ | 27.87s | 29.72s | 区块链智能合约的Gas费用怎么计算？ |
| o-03 | out_of_scope | ✓ | ✓ | 32.32s | 9.63s | React的Fiber架构原理是什么？ |
| o-04 | out_of_scope | ✓ | ✓ | 22.79s | 9.42s | 天文学中黑洞的事件视界是什么？ |
| o-05 | out_of_scope | ✓ | ✓ | 20.71s | 13.97s | 经济学中的菲利普斯曲线是什么？ |
| o-06 | out_of_scope | ✓ | ✓ | 14.74s | 4.96s | 生物学中CRISPR基因编辑的原理是什么？ |

---

## 第三轮测试：接入重排序 + 禁用事实类 HyDE

### 改动内容

1. **接入 qwen3-vl-rerank 重排序**（`app/rag/reranker.py`）：
   - 检索时扩大候选集（top_k × 3），再用 DashScope rerank API 重排，取 top_k
   - 计费：¥0.5 / 百万 input tokens，单次请求约 ¥0.0125
2. **SIMPLE/FACTUAL 禁用 HyDE**（planner.py）：
   - 研究证据表明 HyDE 对精确术语查询有害（伪文档稀释关键词匹配）
   - EXPLANATORY/COMPARATIVE/PROCEDURAL/EXPLORATORY 保留 HyDE

### 第三轮结果

| 维度 | 传统 RAG | Agentic RAG（第三轮） | 提升 |
|------|----------|---------------------|------|
| **综合正确率** | 61/66 (92.4%) | **63/66 (95.5%)** | **+3.1pp** |
| **平均耗时** | 23.9s | **21.3s** | 快 10.9% |

### 三轮对比（Agentic RAG）

| 指标 | 第一轮（原始） | 第二轮（参数调整） | 第三轮（+重排序-事实HyDE） |
|------|---------------|-------------------|--------------------------|
| **综合正确率** | 40/66 (60.6%) | 55/66 (83.3%) | **63/66 (95.5%)** |
| **vs 传统 RAG** | -21.2pp（输） | +1.5pp（赢） | **+3.1pp（赢）** |
| **平均耗时** | 22.9s | 28.4s | **21.3s** |
| simple | 40% | 60% | **90%** |
| factual | 10% | 80% | **100%** |
| explanatory | 60% | 80% | **90%** |
| comparative | 90% | 100% | **100%** |
| procedural | 60% | 90% | **90%** |
| exploratory | 80% | 80% | **100%** |
| out_of_scope | 100% | 100% | **100%** |

### 关键发现

1. **simple 从 60% → 90%**：重排序让正确的文档排到 top-k（"Redis默认fsync策略" 正确文档重排后 0.891 分排第一）
2. **factual 从 80% → 100%**：禁用 HyDE + 重排序，精确术语查询不再被伪文档稀释
3. **exploratory 从 80% → 100%**：重排序提升宽泛查询的检索精度
4. **耗时下降**：禁用事实类 HyDE 省了一次 LLM 调用，21.3s 甚至快于传统 RAG 的 23.9s

### 三轮优化总结

| 轮次 | 优化动作 | 综合正确率 | vs 传统 |
|------|---------|-----------|---------|
| 第一轮 | 基线 | 60.6% | -21.2pp |
| 第二轮 | 修复分类 + 参数调整 | 83.3% | +1.5pp |
| 第三轮 | 接入重排序 + 禁用事实类HyDE | **95.5%** | **+3.1pp** |

三轮累计提升 **+34.9pp**（60.6% → 95.5%），且耗时从 22.9s 降至 21.3s（快于传统 RAG）。

### 传统 RAG 也受益于重排序

第三轮传统 RAG 也从 81.8% 提升到 92.4%，因为测试脚本中传统 RAG 模拟同样使用了 `use_rerank=True`（重排序属于检索组件，不是 Agentic 特有）。即便如此，Agentic RAG 仍以 95.5% 领先。
