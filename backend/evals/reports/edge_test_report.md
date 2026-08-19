# 边界情况测试报告

## 测试环境
- 边界文档：10 篇（超长 1 篇 99KB / 跨文档 3 篇 / 模糊 2 篇 / 新旧冲突 4 篇）
- 评估集：26 题
- LLM：deepseek-v4-flash / 重排序：qwen3-vl-rerank

## 汇总

| 指标 | 值 |
|------|-----|
| 总题数 | 26 |
| 通过 | 24/26 (92.3%) |
| 平均耗时 | 39.8s |

## 分边界类型

| 类型 | 题数 | 通过 | 通过率 | 平均耗时 |
|------|------|------|--------|---------|
| long_doc | 8 | 8/8 | 100% | 19.8s |
| cross_doc | 6 | 5/6 | 83% | 27.4s |
| ambiguous | 6 | 6/6 | 100% | 8.1s |
| conflict | 6 | 5/6 | 83% | 36.3s |

## 逐题明细

| ID | 类型 | 结果 | 耗时 | 关键词命中 | 问题 |
|----|------|:----:|-----:|-----------|------|
| ld-01 | long_doc | ✓ | 19.82s | 3 | MySQL 的架构分为哪三层？ |
| ld-02 | long_doc | ✓ | 64.47s | 1 | MySQL 8.0 移除了什么功能？ |
| ld-03 | long_doc | ✓ | 29.87s | 1 | innodb_buffer_pool_size 建议设为物理 |
| ld-04 | long_doc | ✓ | 5.42s | 2 | MySQL 主从复制中 relay log 的作用是什么？ |
| ld-05 | long_doc | ✓ | 55.63s | 4 | MySQL 高可用方案有哪些？ |
| ld-06 | long_doc | ✓ | 6.78s | 4 | 什么是索引下推？ |
| ld-07 | long_doc | ✓ | 94.11s | 3 | MySQL 半同步复制要求什么？ |
| ld-08 | long_doc | ✓ | 4.29s | 4 | 什么是分区表？支持哪些分区方式？ |
| cd-01 | cross_doc | ✓ | 27.42s | 3 | 分布式事务中 MySQL 的 XA 两阶段提交是怎样的？ |
| cd-02 | cross_doc | ✓ | 63.0s | 4 | Kafka 如何配合实现分布式事务的最终一致性？ |
| cd-03 | cross_doc | ✓ | 19.0s | 4 | Outbox Pattern 发件箱模式的完整流程是什么？ |
| cd-04 | cross_doc | ✗ | 77.3s | 0 | Redis 分布式锁如何与数据库事务配合实现并发安全？ |
| cd-05 | cross_doc | ✓ | 154.31s | 4 | 分布式事务有哪些常见的实现方案？ |
| cd-06 | cross_doc | ✓ | 17.83s | 4 | 什么是事务消息？它如何保证分布式一致性？ |
| am-01 | ambiguous | ✓ | 8.12s | 2 | Spring 的 IoC 是什么？ |
| am-02 | ambiguous | ✓ | 31.0s | 4 | Spring 的事务传播行为有哪些？ |
| am-03 | ambiguous | ✓ | 23.21s | 4 | Spring Watch 是怎么工作的？ |
| am-04 | ambiguous | ✓ | 54.92s | 2 | 数据库连接池 maxPoolSize 应该设多少？ |
| am-05 | ambiguous | ✓ | 13.54s | 2 | 连接池的最大连接数原则是什么？ |
| am-06 | ambiguous | ✓ | 38.87s | 3 | Spring 框架支持哪些依赖注入方式？ |
| cf-01 | conflict | ✗ | 36.28s | 1 | MySQL 应该建多少个索引合适？ |
| cf-02 | conflict | ✓ | 42.37s | 3 | text 大字段应该怎么建索引？ |
| cf-03 | conflict | ✓ | 35.94s | 3 | 小表应该走索引还是全表扫描？ |
| cf-04 | conflict | ✓ | 55.27s | 1 | 数据库连接池 maxPoolSize 推荐值是多少？ |
| cf-05 | conflict | ✓ | 30.68s | 2 | 索引区分度低的列应该建索引吗？ |
| cf-06 | conflict | ✓ | 25.7s | 3 | 冗余索引应该怎么处理？ |
