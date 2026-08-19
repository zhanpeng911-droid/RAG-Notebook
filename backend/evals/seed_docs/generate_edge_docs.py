"""
生成边界测试文档：
1. 超长文档（>100KB，测试分块和检索极限）
2. 跨文档关联（信息分散在多个文档，需关联才能回答）
3. 模糊主题（多义性主题文档）
4. 新旧冲突（同一主题两篇内容冲突的文档）

用法: cd backend && uv run python -m evals.seed_docs.generate_edge_docs
"""
import os

OUTPUT_DIR = os.path.dirname(__file__)

def write(filename, content):
    with open(os.path.join(OUTPUT_DIR, filename), 'w', encoding='utf-8') as f:
        f.write(content)

# ===== 1. 超长文档：MySQL 完整架构手册（约 100KB）=====
def gen_long_mysql_doc():
    sections = []
    # 60 个章节，每章节展开多段，总长 100KB+
    topics = [
        ("架构总览", "MySQL 的架构分为三层：连接层、服务层、存储引擎层。连接层负责客户端连接管理、认证、权限校验。服务层包含连接器、查询缓存、分析器、优化器、执行器。存储引擎层负责数据的存储和读取，插件式架构支持多种存储引擎。"),
        ("连接管理", "MySQL 使用线程池管理客户端连接。每个连接对应一个线程。连接超时由 wait_timeout 控制，默认 8 小时。max_connections 限制最大连接数，默认 151。连接认证包括用户名密码校验和 SSL 加密连接。"),
        ("查询缓存", "MySQL 8.0 之前有查询缓存，相同查询直接返回缓存结果。但查询缓存命中率低且维护成本高，MySQL 8.0 已移除查询缓存功能。"),
        ("分析器", "分析器对 SQL 进行词法分析和语法分析，生成语法树。如果 SQL 语法错误，在分析阶段就会报错。"),
        ("优化器", "优化器决定 SQL 的执行计划。包括选择索引、决定连接顺序、选择执行算法（Nested Loop Join、Hash Join 等）。"),
        ("执行器", "执行器根据执行计划调用存储引擎接口执行 SQL。执行器会先判断用户是否有权限访问表。"),
        ("InnoDB 存储引擎", "InnoDB 是 MySQL 默认存储引擎。支持事务、行级锁、外键、崩溃恢复。数据存储在表空间中，使用 B+ 树索引。"),
        ("MyISAM 存储引擎", "MyISAM 是 MySQL 5.5 之前的默认存储引擎。不支持事务和外键，只支持表级锁。适合读多写少的场景。"),
        ("Memory 存储引擎", "Memory 存储引擎将数据存储在内存中，访问速度极快。但服务重启后数据丢失，只支持表级锁。"),
        ("索引类型", "MySQL 索引分为 B+ 树索引和哈希索引。B+ 树索引支持范围查询，哈希索引只支持等值查询。全文索引用于全文搜索。"),
        ("主键选择", "InnoDB 建议使用自增主键。UUID 主键会导致页分裂，降低写入性能。主键长度应尽量短。"),
        ("唯一索引", "唯一索引保证列值唯一。创建唯一索引时，已存在的重复值会导致创建失败。唯一索引允许 NULL 值，且 NULL 可以重复。"),
        ("普通索引", "普通索引不要求唯一性。可以加速查询但会增加写操作开销。"),
        ("联合索引", "联合索引是多个列的复合索引，遵循最左前缀原则。索引列顺序影响查询效率，区分度高的列放前面。"),
        ("覆盖索引", "覆盖索引指查询的列都在索引中，无需回表。可以通过 EXPLAIN 的 Extra 字段看到 Using index。"),
        ("索引下推", "索引下推（ICP）将 where 条件下推到存储引擎层过滤，减少回表次数。MySQL 5.6+ 默认开启。"),
        ("回表查询", "二级索引查询需要回表：先在二级索引找到主键，再到聚簇索引找完整行。回表次数多时性能差。"),
        ("事务隔离", "MySQL 事务隔离级别：读未提交、读已提交、可重复读、串行化。InnoDB 默认可重复读。"),
        ("事务传播", "事务传播行为决定事务边界。REQUIRED 表示当前存在事务则加入，否则新建。REQUIRES_NEW 总是新建事务。"),
        ("锁机制", "InnoDB 锁包括共享锁、排他锁、记录锁、间隙锁、临键锁。死锁时 InnoDB 自动检测并回滚一个事务。"),
        ("MVCC 机制", "MVCC 通过 undo log 版本链和 ReadView 实现非阻塞读。可重复读下 ReadView 在事务开始时创建。"),
        ("redo log", "redo log 是 InnoDB 的物理日志，记录数据页的修改。先写日志再写数据（WAL）。两阶段提交保证和 binlog 一致。"),
        ("undo log", "undo log 记录数据修改前的旧值。用于事务回滚和 MVCC 版本链。purge 线程清理无用的 undo log。"),
        ("binlog", "binlog 是 Server 层的逻辑日志，记录所有 DDL 和 DML。用于主从复制和数据恢复。"),
        ("主从复制", "主从复制基于 binlog。主库写 binlog，从库 IO 线程拉取写入 relay log，SQL 线程重放。"),
        ("半同步复制", "半同步复制要求至少一个从库确认收到 binlog 才提交。保证不丢数据但增加延迟。"),
        ("组复制", "组复制（MGR）基于 Paxos 协议实现多主复制。任一节点写入都会被组内所有节点复制。"),
        ("慢查询优化", "慢查询日志记录执行时间超过 long_query_time 的 SQL。通过 EXPLAIN 分析执行计划。"),
        ("分库分表", "分库分表解决单库单表数据量过大的问题。常见策略：水平分表、垂直分表、读写分离。"),
        ("分区表", "分区表将一张表的数据分布在多个分区。支持 range、list、hash、key 分区。"),
        ("连接池", "数据库连接池复用连接减少握手开销。常见参数：最小空闲连接、最大连接、超时时间。"),
        ("读写分离", "读写分离将读操作分发到从库，写操作到主库。需要考虑主从延迟导致的读到旧数据问题。"),
        ("数据备份", "MySQL 备份包括逻辑备份（mysqldump）和物理备份（xtrabackup）。binlog 可用于增量备份和时间点恢复。"),
        ("参数调优", "关键参数：innodb_buffer_pool_size（缓冲池大小，建议物理内存 70%）、max_connections、innodb_flush_log_at_trx_commit。"),
        ("性能监控", "通过 performance_schema 和 sys 库监控 MySQL 运行状态。关注慢查询数、锁等待、缓冲池命中率。"),
        ("SQL 优化", "SQL 优化原则：避免 SELECT *、避免索引列运算、避免隐式类型转换、小表驱动大表。"),
        ("统计信息", "优化器依赖统计信息选择执行计划。ANALYZE TABLE 更新统计信息。统计信息不准确会导致执行计划偏差。"),
        ("缓冲池", "innodb_buffer_pool 缓存数据和索引页。命中率高则查询快。通过 show engine innodb status 查看命中率。"),
        ("日志管理", "错误日志记录启动关闭和异常。通用查询日志记录所有 SQL。binlog 有 Statement、Row、Mixed 三种格式。"),
        ("高可用架构", "常见高可用方案：MHA（主备切换）、Orchestrator、MGR（组复制）、MySQL Router（读写路由）。"),
    ]
    # 每章节扩展为多段（每段重复展开核心概念），让文档达到 100KB+
    for i, (title, body) in enumerate(topics, 1):
        parts = [f"# 第{i}章 {title}\n\n{body}\n"]
        # 每个章节展开 3 个详细小节
        for j in range(3):
            parts.append(
                f"## {i}.{j+1} {title}深入讨论\n\n"
                f"本节详细讨论{title}相关的{body[:100]}。\n"
                f"在实际生产环境中，{title}的正确配置和使用直接影响数据库性能。"
                f"需要结合具体的业务场景、数据量、并发量进行综合评估。"
                f"常见的性能问题包括：参数配置不当、索引缺失、SQL 写法低效、"
                f"锁竞争激烈、主从延迟等。针对每个问题都有对应的排查方法和优化手段。\n\n"
                f"进一步地，{title}还需要考虑与周边组件（如连接池、缓存、消息队列）的协同。"
                f"在分布式架构中，数据库的性能瓶颈往往不是单点问题，而是整个链路的问题。\n\n"
            )
        sections.append("\n".join(parts))

    content = f"""# MySQL 完整架构与优化手册（超长文档边界测试）

本文档是超长文档边界测试用例，总长度超过 100KB，包含 40 个章节，
覆盖 MySQL 架构、索引、事务、锁、复制、优化等全部主题。
用于验证 RAG 系统在超长文档下的分块、检索、引用能力。

{"".join(sections)}
## 附录：常用命令速查

```sql
-- 查看版本
SELECT VERSION();
-- 查看执行计划
EXPLAIN SELECT * FROM users WHERE id = 1;
-- 查看连接
SHOW PROCESSLIST;
-- 查看索引
SHOW INDEX FROM users;
-- 开启慢查询日志
SET GLOBAL slow_query_log = ON;
SET GLOBAL long_query_time = 1;
-- 查看缓冲池命中率
SHOW STATUS LIKE 'Innodb_buffer_pool_read%';
-- 查看锁等待
SELECT * FROM information_schema.INNODB_LOCKS;
```
"""
    write("edge_long_mysql_manual.txt", content)

# ===== 2. 跨文档关联：分布式事务（信息分散在 3 个文档）=====
write("edge_distributed_tx_1_mysql.txt",
"""# 分布式事务 - MySQL 侧

## MySQL 的两阶段提交（XA）

MySQL 支持 XA 分布式事务协议，通过 PREPARE 和 COMMIT 两个阶段实现跨库事务一致性。

### 阶段一：准备（Prepare）
- 事务协调者（TC）向所有参与节点发送 PREPARE 请求
- 各参与节点执行事务但暂不提交，写入 redo log 的 prepare 状态
- 各节点回复"已准备就绪"或"准备失败"

### 阶段二：提交/回滚（Commit/Rollback）
- 如果所有节点都准备成功，TC 发送 COMMIT 请求
- 如果任一节点准备失败，TC 发送 ROLLBACK 请求
- 各节点根据指令提交或回滚事务

### 问题
- XA 是同步阻塞协议，prepare 阶段持有锁，事务较长时影响性能
- 如果 TC 在 COMMIT 阶段宕机，各节点无法确定最终状态，需要人工介入
""")

write("edge_distributed_tx_2_kafka.txt",
"""# 分布式事务 - Kafka 侧

## Kafka 事务机制

Kafka 从 0.11 版本开始支持跨分区、跨主题的原子事务。

### 事务性生产者
- 通过 transaction.id 开启事务
- initTransactions -> beginTransaction -> 发送消息 -> commitTransaction
- 事务消息通过 transaction marker 标记提交或中止

### 隔离级别
- read_committed：消费者只读已提交的事务消息（默认推荐）
- read_uncommitted：消费者读取所有消息包括未提交的

### 与外部系统的事务协调
Kafka 本身不提供跨系统事务，需要通过**事务消息 + 本地消息表**或
**Outbox Pattern** 模式实现分布式事务的最终一致性。
""")

write("edge_distributed_tx_3_redis.txt",
"""# 分布式事务 - Redis 侧

## Redis 与分布式事务

Redis 本身不提供分布式事务，但可以通过以下方案辅助实现：

### Redis 分布式锁 + 本地事务
1. 获取 Redis 分布式锁（SET NX PX）
2. 执行本地数据库事务
3. 释放锁
这种方式保证并发安全，但不能保证跨系统的原子性。

### 事务消息 + 消息队列的最终一致性
这是目前最常用的分布式事务解决方案：
1. 业务方先写本地事务表，标记"待发送"
2. 通过定时任务或事务消息将消息发送到 MQ
3. 消费者消费消息执行下游业务
4. 通过状态机保证最终一致

### Outbox Pattern（发件箱模式）
1. 业务数据和 outbox 表在同一本地事务中写入
2. 后台任务读取 outbox 表，将事件发布到 MQ
3. 发布成功后删除 outbox 记录
4. 如果发布失败，重试直到成功
""")

# ===== 3. 模糊主题：多义性文档 =====
write("edge_ambiguous_spring.txt",
"""# Spring 框架基础

Spring 是 Java 生态最流行的应用开发框架。

## Spring Core
- IoC（控制反转）：对象的创建和管理交给 Spring 容器
- DI（依赖注入）：通过构造函数、setter、字段注入依赖
- Bean 生命周期：实例化、属性填充、初始化、使用、销毁

## Spring Boot
- 自动配置：根据 classpath 自动配置组件
- Starter 依赖：简化依赖管理
- 内嵌服务器：内嵌 Tomcat/Jetty

## Spring 事务
- @Transactional 注解声明式事务管理
- 传播行为：REQUIRED、REQUIRES_NEW、NESTED 等
""")

write("edge_ambiguous_spring_watch.txt",
"""# Spring Watch（弹簧表带）

Spring Watch 是一种使用弹簧（发条）机构的机械手表。

## 工作原理
- 发条储存能量：上弦时压缩发条储存弹性势能
- 齿轮传动：发条通过齿轮系统驱动指针
- 擒纵机构：控制齿轮转速，实现精准计时
- 摆轮游丝：通过振动周期控制走时精度

## 动力存储
- 手动上链：每天手动旋转表冠上弦
- 自动上链：通过佩戴者手臂摆动自动上弦
""")

# ===== 4. 新旧冲突：索引优化策略 =====
write("edge_index_old.txt",
"""# 索引优化策略（旧版，2019 年）

## 传统索引优化建议

### 1. 每个查询都建索引
只要是查询中用到的列，都应该建立索引。查询慢就加索引。

### 2. 索引越多越好
索引可以加速所有查询，索引数量不设上限。

### 3. 大字段也可以建索引
对于 text 类型的大字段，直接对整个字段建立索引。

### 4. 全表扫描是禁忌
任何情况下都应该避免全表扫描，全表扫描意味着性能灾难。

### 5. 冗余索引无害
多个索引覆盖相同列没有问题，可以都保留。

## 建议的索引策略
- 建索引数量：无限制
- 索引列选择：所有查询列
- 对大字段：全字段索引
- 全表扫描：绝对禁止
""")

write("edge_index_new.txt",
"""# 索引优化策略（新版，2024 年）

## 现代索引优化建议

### 1. 索引不是越多越好
每个索引都会增加写操作（INSERT/UPDATE/DELETE）的开销，
每张表建议控制在 5-6 个索引以内。

### 2. 高区分度列优先
优先为区分度高的列建索引（如订单号、用户ID），
区分度低的列（如性别、状态）建索引收益很低。

### 3. 大字段用前缀索引
text 类型的大字段应该使用前缀索引（前 N 个字符），
而不是对整个字段建立索引。

### 4. 全表扫描有时更快
当表数据量很小时，全表扫描比走索引更快，
优化器会自动选择全表扫描。

### 5. 冗余索引应该删除
完全重复或高度重叠的索引应该删除，
使用联合索引替代多个单列索引。

## 建议的索引策略
- 建索引数量：5-6 个以内
- 索引列选择：高区分度列
- 对大字段：前缀索引
- 全表扫描：小表允许，优化器自动选择
""")

# ===== 4b. 新旧冲突：连接池配置 =====
write("edge_pool_old.txt",
"""# 数据库连接池配置（旧版建议）

## 连接池参数推荐（2018 年）

### maxPoolSize = 100
连接池最大连接数建议设置为 100，
连接数越多并发处理能力越强。

### minIdle = 20
最小空闲连接数设置为 20，
保证足够的空闲连接应对突发流量。

### connectionTimeout = 5000ms
连接获取超时 5 秒。

### 调优原则
- 连接数越多越好
- 空闲连接越多越好
- 参数一次性设置后不再调整
""")

write("edge_pool_new.txt",
"""# 数据库连接池配置（新版建议）

## 连接池参数推荐（2023 年）

### maxPoolSize = 10
连接池最大连接数建议设置为 10，
因为数据库处理能力有限，过多连接反而导致连接排队和资源浪费。

### minIdle = 2
最小空闲连接数设置为 2，保持轻量。

### connectionTimeout = 30000ms
连接获取超时 30 秒，给慢查询留出时间。

### 调优原则
- 连接数 = 数据库核心数 × 2 + 磁盘数（小池子原则）
- 连接数过多会触发数据库的锁竞争和上下文切换
- 需要压测后根据 QPS 和延迟数据调优
""")

def main():
    # 生成超长文档
    gen_long_mysql_doc()
    # 其他文档已直接写入
    print("Edge test documents generated:")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        if f.startswith("edge_"):
            size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
            print(f"  {f}: {size/1024:.1f} KB")

if __name__ == "__main__":
    main()
