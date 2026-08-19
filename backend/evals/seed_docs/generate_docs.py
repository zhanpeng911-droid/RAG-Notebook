"""
生成 110 篇技术测试文档，覆盖 10 个领域。
用法: cd backend && uv run python -m evals.seed_docs.generate_docs
"""
import os
import json

OUTPUT_DIR = os.path.dirname(__file__)

# 每篇文档: (filename, title, content)
DOCS = []

def add(domain, title, content):
    filename = f"{domain}_{title}.txt"
    DOCS.append((filename, title, content))

# ===== Redis (12) =====
add("redis", "01_persistence",
"""# Redis 持久化机制

## AOF（Append Only File）
AOF 记录所有写操作命令，追加到文件中。

### fsync 策略
1. always：每次写操作都同步到磁盘，数据安全性最高，性能最差。
2. everysec：每秒同步一次，是 Redis 的默认策略，最多丢失 1 秒数据。
3. no：由操作系统决定何时同步，性能最好，数据安全性最低。

## RDB（Redis Database）
RDB 是快照持久化，将某个时间点的所有数据保存到二进制文件。
- save：阻塞式快照。
- bgsave：后台 fork 子进程执行快照。

## 混合持久化（Redis 4.0+）
AOF 重写时先以 RDB 格式写入快照，再将增量命令以 AOF 格式追加。配置 aof-use-rdb-preamble yes 开启。
""")

add("redis", "02_cluster",
"""# Redis 集群

Redis Cluster 通过分片将数据分布到多个节点。

## 数据分片
使用 16384 个哈希槽（hash slot），每个节点负责一部分槽。
键的哈希槽 = CRC16(key) % 16384。

## 节点通信
使用 Gossip 协议，节点间交换集群状态信息。

## 故障转移
- 节点通过心跳检测其他节点状态。
- 主节点下线后，其从节点升级为新主节点。
- 集群需要至少 3 主 3 从共 6 个节点才能保证高可用。
""")

add("redis", "03_sentinel",
"""# Redis 哨兵（Sentinel）

Sentinel 是 Redis 的高可用方案，独立于 Redis 实例运行。

## 功能
1. 监控：检查主从节点是否正常运行。
2. 通知：通知运维人员或客户端故障情况。
3. 自动故障转移：主节点宕机时自动将从节点提升为主节点。
4. 配置提供：客户端连接 Sentinel 获取当前主节点地址。

## 仲裁机制
需要至少 3 个 Sentinel 节点（奇数），故障转移需要达到 quorum（法定人数）投票。
""")

add("redis", "04_cache_strategy",
"""# Redis 缓存策略

## 缓存穿透
查询不存在的数据，缓存和数据库都没有。
解决：布隆过滤器拦截、缓存空值。

## 缓存击穿
热点 key 过期瞬间，大量请求打到数据库。
解决：互斥锁、热点 key 永不过期。

## 缓存雪崩
大量 key 同时过期，或 Redis 宕机。
解决：过期时间加随机值、多级缓存、高可用集群。

## 读写策略
- Cache Aside：先读缓存，未命中读数据库并回写缓存。更新时先更新数据库再删除缓存。
- Write Through：写数据时同时写缓存和数据库。
- Write Behind：先写缓存，异步写数据库。
""")

add("redis", "05_expire",
"""# Redis 过期机制

## 过期策略
1. 定期删除：Redis 默认每秒 10 次随机抽查过期 key 并删除。
2. 惰性删除：访问 key 时检查是否过期，过期则删除。

## 内存淘汰策略（maxmemory-policy）
- noeviction：不淘汰，写入报错（默认）。
- allkeys-lru：所有 key 中淘汰最近最少使用的。
- volatile-lru：设了过期的 key 中淘汰 LU。
- allkeys-lfu：所有 key 中淘汰最不经常使用的。
- volatile-ttl：淘汰即将过期的 key。
""")

add("redis", "06_pipeline",
"""# Redis 管道（Pipeline）

管道允许客户端一次发送多个命令，不等待单个命令的响应，减少网络往返时间（RTT）。

## 原理
- 普通：发送命令 -> 等待响应 -> 发送下一条（N 次 RTT）
- 管道：发送命令1,2,3... -> 一次性接收所有响应（1 次 RTT）

## 注意事项
- 管道不是原子的，中间可能插入其他客户端命令。
- 数据量过大时会阻塞 Redis（单线程）。
- 与事务（MULTI/EXEC）不同：事务保证原子性，管道不保证。
""")

add("redis", "07_pubsub",
"""# Redis 发布订阅（Pub/Sub）

Redis Pub/Sub 是消息通信模式。

## 模型
- 发布者（Publisher）将消息发送到频道（Channel）。
- 订阅者（Subscriber）订阅感兴趣的频道。
- 消息是即时推送的，不持久化。

## 命令
- PUBLISH channel message：发布消息。
- SUBSCRIBE channel：订阅频道。
- PSUBSCRIBE pattern：按模式订阅。

## 局限性
- 消息不持久化：订阅者离线时丢失消息。
- 没有 ACK 机制：不保证消息送达。
- 需要持久化和可靠投递时应使用 Stream。
""")

add("redis", "08_data_types",
"""# Redis 数据类型

## 基本类型
1. String：字符串，最大 512MB。可存数字、文本、二进制。
2. List：有序列表，支持从两端插入/弹出。底层是快速列表（quicklist）。
3. Hash：哈希表，存储字段-值对。适合存储对象。
4. Set：无序集合，元素唯一。支持交集、并集、差集。
5. ZSet（Sorted Set）：有序集合，每个元素带分数（score），按分数排序。

## 特殊类型
- Bitmap：位图，适合统计签到等布尔状态。
- HyperLogLog：基数估算，适合 UV 统计，误差 0.81%。
- Stream：消息流，支持消费者组，可持久化。
- Geo：地理位置，支持距离计算和范围查询。
""")

add("redis", "09_transaction",
"""# Redis 事务

Redis 事务通过 MULTI/EXEC 实现。

## 命令
- MULTI：开启事务。
- 命令入队但不执行。
- EXEC：原子执行所有入队命令。
- DISCARD：取消事务。
- WATCH key：乐观锁，监视 key，如果被修改则事务失败。

## 特点
- Redis 事务不支持回滚：如果某条命令执行出错（语法错误除外），后续命令仍会执行。
- WATCH 实现乐观锁：EXEC 前如果被监视的 key 被其他客户端修改，整个事务放弃。
""")

add("redis", "10_memory",
"""# Redis 内存管理

## 内存分配
- Redis 所有数据存在内存中，通过 maxmemory 配置上限。
- 超出上限时触发内存淘汰策略。

## 内存优化
- 使用ziplist（压缩列表）存储小的 Hash/List/ZSet，减少内存碎片。
- Redis 7.0 使用 listpack 替代 ziplist。
- 使用对象共享池：小整数（0-9999）共享同一个对象。
- 设置合理的过期时间，避免无用数据堆积。

## 内存碎片
- used_memory_rss / used_memory 比值表示碎片率。
- 碎片率 > 1.5 时需要关注，可通过 activedefrag 自动碎片整理。
""")

add("redis", "11_stream",
"""# Redis Stream

Redis Stream 是 5.0 引入的持久化消息队列数据类型。

## 特点
- 消息持久化：消息存储在内存中，不会因重启丢失（配合持久化）。
- 消费者组：支持多消费者分组消费。
- 消息确认（ACK）：消费者处理完后需要 XACK 确认。
- 消息回溯：可以按 ID 读取历史消息。

## 命令
- XADD：添加消息。
- XREAD：读取消息。
- XGROUP：创建消费者组。
- XREADGROUP：消费者组读取。
- XACK：确认消息。
- XPENDING：查看待确认消息。
- XCLAIM：转移超时消息给其他消费者。
""")

add("redis", "12_distributed_lock",
"""# Redis 分布式锁

## 基本实现
使用 SET key value NX PX timeout 命令实现分布式锁。
- NX：key 不存在才设置（互斥）。
- PX：设置过期时间（防死锁）。
- value：设为唯一标识（UUID），用于安全释放锁。

## 释放锁
释放锁需要先判断 value 是否匹配，再删除。必须用 Lua 脚本保证原子性：
```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

## Redisson
Redisson 框架提供更完善的分布式锁：
- 可重入锁：基于 Hash 结构记录重入次数。
- 看门狗（Watchdog）：自动续期，避免业务未完成锁就过期。
- 公平锁、读写锁、信号量等。
""")

# ===== MySQL (12) =====
add("mysql", "01_index",
"""# MySQL InnoDB 索引

## 聚簇索引
数据和索引存储在一起，叶子节点包含完整行数据。InnoDB 主键就是聚簇索引，每张表只有一个。

## 二级索引
叶子节点存储主键值而非行数据。查询需要回表：先查二级索引得到主键，再查聚簇索引得到行数据。

## 覆盖索引
查询的列都在索引中，不需要回表。

## B+ 树
InnoDB 索引使用 B+ 树：所有数据在叶子节点，非叶子节点只存索引，叶子节点通过双向链表连接。
""")

add("mysql", "02_composite_index",
"""# MySQL 联合索引与最左前缀原则

联合索引 (a, b, c) 遵循最左前缀原则：
- WHERE a = 1 可以用索引。
- WHERE a = 1 AND b = 2 可以用索引。
- WHERE a = 1 AND b = 2 AND c = 3 可以用索引。
- WHERE b = 2 不可以用索引（跳过了 a）。
- WHERE a = 1 AND b > 5 AND c = 3 只有 a 和 b 能用索引，c 用不到（范围查询中断）。
""")

add("mysql", "03_transaction",
"""# MySQL 事务

## ACID 特性
- 原子性（Atomicity）：事务要么全部成功，要么全部回滚。通过 undo log 实现。
- 一致性（Consistency）：事务执行前后数据保持一致状态。
- 隔离性（Isolation）：并发事务之间互不干扰。通过锁和 MVCC 实现。
- 持久性（Durability）：事务提交后数据永久保存。通过 redo log 实现。

## 隔离级别
1. 读未提交（Read Uncommitted）：可能脏读。
2. 读已提交（Read Committed）：可能不可重复读。Oracle 默认。
3. 可重复读（Repeatable Read）：可能幻读。MySQL InnoDB 默认。
4. 串行化（Serializable）：完全串行，无并发问题。
""")

add("mysql", "04_mvcc",
"""# MySQL MVCC（多版本并发控制）

MVCC 通过保存数据的历史版本实现非阻塞读。

## 实现
- 每行数据有两个隐藏字段：DB_TRX_ID（事务ID）和 DB_ROLL_PTR（回滚指针）。
- undo log 记录旧版本数据，通过回滚指针形成版本链。
- ReadView 决定当前事务能看到哪个版本。

## ReadView 规则
- 创建 ReadView 时记录当前活跃事务列表。
- 如果数据的事务ID < 最小活跃事务ID，可见。
- 如果数据的事务ID > 最大事务ID，不可见。
- 如果在活跃列表中，不可见（沿版本链找更旧版本）。

## RC vs RR
- RC：每次 SELECT 都创建新 ReadView（所以能读到最新提交的数据）。
- RR：事务开始时创建一次 ReadView（所以可重复读）。
""")

add("mysql", "05_lock",
"""# MySQL InnoDB 锁

## 行锁类型
- 共享锁（S锁）：读锁，SELECT ... LOCK IN SHARE MODE。
- 排他锁（X锁）：写锁，SELECT ... FOR UPDATE、UPDATE、DELETE。
- 记录锁（Record Lock）：锁住索引记录。
- 间隙锁（Gap Lock）：锁住索引记录之间的间隙，防止幻读。
- 临键锁（Next-Key Lock）：记录锁 + 间隙锁，是 RR 隔离级别的默认行锁。

## 表锁
- 意向锁（IS/IX）：行锁前先加表级意向锁，提高锁冲突检测效率。
- AUTO-INC 锁：自增列插入时使用。

## 死锁
InnoDB 自动检测死锁并回滚代价较小的事务。
""")

add("mysql", "06_replication",
"""# MySQL 主从复制

## 原理
1. 主库（Master）执行写操作，记录到 binlog（二进制日志）。
2. 从库（I/O Thread）连接主库，读取 binlog，写入本地 relay log（中继日志）。
3. 从库（SQL Thread）读取 relay log，重放 SQL 语句。

## 复制方式
- 异步复制：主库不等从库确认（默认）。
- 半同步复制：主库等待至少一个从库确认收到 binlog。
- 组复制（MGR）：基于 Paxos 的多主复制。

## 延迟问题
从库单线程重放 SQL 可能导致延迟。MySQL 5.7+ 支持并行复制（基于组提交）。
""")

add("mysql", "07_slow_query",
"""# MySQL 慢查询优化

## 定位慢查询
- 开启 slow_query_log，设置 long_query_time 阈值。
- 使用 EXPLAIN 分析执行计划。

## EXPLAIN 关键字段
- type：访问类型，从好到差：const > eq_ref > ref > range > index > ALL。
- key：实际使用的索引。
- rows：预估扫描行数。
- Extra：Using index（覆盖索引）、Using filesort（文件排序）、Using temporary（临时表）。

## 优化手段
- 添加合适索引（遵循最左前缀原则）。
- 避免 SELECT *，只查需要的列。
- 避免索引失效：不在索引列上做运算、函数、类型转换。
- 大分页用延迟关联：先查主键再关联查询。
""")

add("mysql", "08_binlog",
"""# MySQL binlog（二进制日志）

binlog 记录所有DDL和DML语句（不含SELECT），用于主从复制和数据恢复。

## 格式
1. Statement：记录 SQL 语句（可能导致函数结果不一致，如 NOW()）。
2. Row：记录每行数据的变更（日志量大但精确）。
3. Mixed：混合模式，默认用 Statement，不安全的用 Row。

## 相关命令
- SHOW BINLOG EVENTS：查看 binlog 事件。
- mysqlbinlog 工具：解析 binlog 文件。
- binlog_format 参数：设置格式。

## 与 redo log 区别
- redo log 是 InnoDB 引擎层的物理日志，循环写入。
- binlog 是 Server 层的逻辑日志，追加写入。
- 两阶段提交：先写 redo log（prepare），再写 binlog，再写 redo log（commit）。
""")

add("mysql", "09_redolog",
"""# MySQL redo log（重做日志）

redo log 是 InnoDB 引擎层的物理日志，记录数据页的物理修改。

## 作用
- 保证事务的持久性（Durability）：事务提交后即使宕机也能恢复。
- Write-Ahead Log（WAL）：先写日志再写数据页，提升性能。

## 机制
- redo log 是循环写入的固定大小文件（ib_logfile0, ib_logfile1）。
- write position 追加写入，check point 表示已刷盘位置。
- 当 write position 追上 check point 时，需要强制刷盘推进 check point。

## 两阶段提交
1. InnoDB 写 redo log（prepare 状态）。
2. Server 层写 binlog。
3. InnoDB 写 redo log（commit 状态）。
保证 redo log 和 binlog 数据一致性。
""")

add("mysql", "10_undo_log",
"""# MySQL undo log（回滚日志）

undo log 记录数据修改前的旧值，用于事务回滚和 MVCC。

## 作用
1. 事务回滚：根据 undo log 恢复数据到修改前状态。
2. MVCC：通过 undo log 构建数据的历史版本，实现非阻塞读。

## 类型
- insert undo log：INSERT 操作的回滚日志，事务提交后可直接删除。
- update undo log：UPDATE/DELETE 操作的回滚日志，需要支持 MVCC，不能立即删除。

## purge 线程
当没有活跃事务需要读旧版本时，purge 线程清理对应的 undo log。
""")

add("mysql", "11_storage_engine",
"""# MySQL 存储引擎

## InnoDB（默认）
- 支持事务、行锁、外键。
- 聚簇索引。
- 支持 MVCC。
- 适合高并发读写场景。

## MyISAM
- 不支持事务和行锁，只有表锁。
- 非聚簇索引（数据和索引分离）。
- 全文索引速度快。
- 适合以读为主的场景。

## Memory
- 数据存在内存中，重启后丢失。
- 表级锁。
- 适合临时表和缓存。

## 区别
InnoDB 支持事务和行锁，MyISAM 不支持。InnoDB 适合高并发写入，MyISAM 适合只读场景。
""")

add("mysql", "12_connection_pool",
"""# MySQL 连接池

## 为什么需要连接池
- 每次创建数据库连接需要 TCP 握手 + 认证，耗时 10-50ms。
- 连接池复用连接，避免频繁创建和销毁。

## 核心参数
- 最小空闲连接数：池中保持的最少连接。
- 最大连接数：池允许的最大连接数。
- 连接超时时间：获取连接的等待时间。
- 空闲连接超时：空闲连接的回收时间。
- 最大生命周期：连接的最大存活时间，防止长期使用老连接。

## 常见实现
- Java：HikariCP、Druid。
- Python：DBUtils、SQLAlchemy 内置池。
- Go：database/sql 内置池。
""")

# ===== Docker (10) =====
add("docker", "01_basics",
"""# Docker 容器基础

## Docker vs 虚拟机
- Docker 共享宿主机内核，启动秒级，资源占用 MB 级。
- 虚拟机有独立内核，启动分钟级，资源占用 GB 级。
- Docker 进程级隔离，虚拟机完全隔离。

## 核心概念
- 镜像（Image）：只读模板，包含运行环境和应用代码。
- 容器（Container）：镜像的运行实例，可读写。
- 仓库（Registry）：存储和分发镜像（如 Docker Hub）。

## 镜像分层
Docker 镜像采用联合文件系统，每条 Dockerfile 指令创建一层。只读层 + 可写层，修改时 Copy-on-Write。
""")

add("docker", "02_dockerfile",
"""# Dockerfile 指令

## CMD vs ENTRYPOINT
- CMD：容器默认启动命令，可被 docker run 参数覆盖。
- ENTRYPOINT：容器入口命令，docker run 参数作为追加参数。
- 组合使用：ENTRYPOINT 指定程序，CMD 指定默认参数。

## 常用指令
- FROM：基础镜像。
- RUN：构建时执行命令。
- COPY/ADD：复制文件到镜像。
- ENV：设置环境变量。
- EXPOSE：声明端口。
- WORKDIR：设置工作目录。
- VOLUME：声明挂载点。

## 多阶段构建
用多个 FROM 阶段，最终镜像只保留最后阶段的文件，减小镜像体积。
""")

add("docker", "03_network",
"""# Docker 网络模式

1. bridge（默认）：Docker 创建的虚拟网桥，容器通过网桥通信，支持端口映射。
2. host：容器直接使用宿主机网络栈，无网络隔离。
3. none：无网络，容器完全隔离。
4. container：容器共享另一个容器的网络栈。
5. 自定义网络：用户创建的 bridge 网络，支持容器间 DNS 解析（通过容器名访问）。

## 端口映射
- docker run -p 8080:80：宿主机 8080 映射到容器 80。
- docker run -P：随机映射端口。
""")

add("docker", "04_volume",
"""# Docker 数据卷（Volume）

## 为什么需要数据卷
容器删除后数据丢失，数据卷将数据持久化到宿主机。

## 三种挂载方式
1. volume：由 Docker 管理的命名卷，存储在 /var/lib/docker/volumes/。
2. bind mount：直接挂载宿主机目录，docker run -v /host/path:/container/path。
3. tmpfs：数据存在内存中，容器停止后消失。

## 特点
- volume 可以在容器间共享。
- volume 独立于容器生命周期。
- bind mount 适合开发环境（挂载源代码）。
""")

add("docker", "05_compose",
"""# Docker Compose

Docker Compose 用于定义和运行多容器应用。

## docker-compose.yml
```yaml
services:
  web:
    build: .
    ports:
      - "8080:80"
    depends_on:
      - db
  db:
    image: mysql:8
    environment:
      MYSQL_ROOT_PASSWORD: secret
```

## 常用命令
- docker-compose up -d：启动所有服务。
- docker-compose down：停止并删除容器。
- docker-compose logs：查看日志。
- docker-compose exec：进入容器。
""")

add("docker", "06_multistage",
"""# Docker 多阶段构建

多阶段构建用一个 Dockerfile 产出精简镜像。

## 示例
```dockerfile
# 阶段1：构建
FROM golang:1.21 AS builder
WORKDIR /app
COPY . .
RUN go build -o myapp

# 阶段2：运行
FROM alpine:latest
COPY --from=builder /app/myapp /usr/local/bin/
CMD ["myapp"]
```

## 优势
- 最终镜像不含编译器和源码，体积小。
- 安全性更好：攻击面更小。
- 无需单独的构建脚本。
""")

add("docker", "07_registry",
"""# Docker 镜像仓库（Registry）

## Docker Hub
- 公共镜像仓库，官方镜像和用户镜像。
- 支持自动构建（GitHub 集成）。

## 私有仓库
- Docker Registry：官方开源的私有仓库。
- Harbor：企业级仓库，支持权限管理、镜像扫描、复制。
- 云厂商：ACR（阿里云）、ECR（AWS）。

## 镜像标签
- latest：默认标签，不保证是最新的。
- 语义化版本：1.2.3。
- digest：不可变标识，sha256:xxx。
""")

add("docker", "08_best_practices",
"""# Docker 最佳实践

## 镜像优化
- 使用精简基础镜像（alpine、slim）。
- 合并 RUN 指令减少层数。
- 使用 .dockerignore 排除不必要文件。
- 合理利用构建缓存：将不常变化的指令放前面。

## 安全
- 不在镜像中硬编码密钥，使用环境变量或 secrets。
- 使用非 root 用户运行应用。
- 定期扫描镜像漏洞（Trivy、Clair）。

## 运行
- 设置容器重启策略（--restart=always）。
- 限制资源（--memory、--cpus）。
- 日志使用 JSON File 驱动或远程日志收集。
""")

add("docker", "09_containerd",
"""# containerd 与 Docker 的关系

## 架构
Docker 实际上是多层架构：
- Docker CLI：命令行工具。
- dockerd：Docker 守护进程。
- containerd：容器运行时管理。
- runc：OCI 标准的容器运行时。

## containerd
- containerd 是从 Docker 中拆分出的核心容器运行时。
- Kubernetes 1.24+ 默认使用 containerd 而非 Docker（弃用 dockershim）。
- containerd 的 CLI 是 ctr，Kubernetes 使用 crictl。

## CRI 标准
容器运行时接口（CRI）是 Kubernetes 的标准接口，containerd 和 CRI-O 都实现了 CRI。
""")

add("docker", "10_healthcheck",
"""# Docker 健康检查

## HEALTHCHECK 指令
```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \\
  CMD curl -f http://localhost:8080/health || exit 1
```

## 状态
- starting：初始状态，正在执行首次健康检查。
- healthy：健康检查通过。
- unhealthy：连续 retries 次检查失败。

## 参数
- interval：检查间隔（默认 30s）。
- timeout：单次检查超时（默认 30s）。
- start-period：启动宽限期（默认 0s）。
- retries：连续失败次数（默认 3）。

## 与编排工具配合
Kubernetes 有独立的 liveness/readiness probe，不依赖 Docker HEALTHCHECK。
""")

# ===== Kubernetes (10) =====
add("k8s", "01_pod_service",
"""# Kubernetes Pod 和 Service

## Pod
- Pod 是 K8s 最小调度单元，包含一个或多个容器。
- 同一 Pod 内容器共享网络和存储。
- Pod 是临时的，IP 会变化。

## Service
- Service 提供稳定的网络端点，负载均衡到后端 Pod。
- 类型：ClusterIP（集群内部）、NodePort（节点端口）、LoadBalancer（云负载均衡）、ExternalName。

## 标签选择器
- Service 通过 selector 匹配 Pod 的 labels。
- 流量通过 kube-proxy 的 iptables/IPVS 规则转发到 Pod。
""")

add("k8s", "02_deployment",
"""# Kubernetes Deployment

Deployment 管理 Pod 的副本集（ReplicaSet），支持滚动更新和回滚。

## 核心功能
- 副本控制：确保指定数量的 Pod 运行。
- 滚动更新：逐步替换旧版本 Pod。
- 回滚：回退到之前的版本。
- 扩缩容：修改 replicas 数量。

## 滚动更新策略
- RollingUpdate（默认）：逐步创建新 Pod，删除旧 Pod。
- Recreate：先删除所有旧 Pod，再创建新 Pod。

## maxSurge 和 maxUnavailable
- maxSurge：滚动更新时最多超出期望副本数的数量。
- maxUnavailable：滚动更新时最多不可用的副本数。
""")

add("k8s", "03_scheduling",
"""# Kubernetes 调度机制

## 调度过程
1. 过滤（Filter）：排除不满足条件的节点（资源不足、端口冲突等）。
2. 打分（Score）：对剩余节点打分（资源均衡、亲和性等）。
3. 绑定（Bind）：将 Pod 绑定到得分最高的节点。

## 调度约束
- nodeSelector：简单标签匹配。
- nodeAffinity：更灵活的节点亲和性（硬性/软性）。
- podAffinity/podAntiAffinity：Pod 间亲和/反亲和。
- Taints 和 Tolerations：节点污点和容忍度。

## 资源请求
- requests：调度依据，保证最小资源。
- limits：上限，超过会被限制或驱逐。
""")

add("k8s", "04_configmap",
"""# Kubernetes ConfigMap 和 Secret

## ConfigMap
- 存储非敏感配置数据（键值对）。
- 可以作为环境变量或文件挂载到 Pod。
- 修改 ConfigMap 不会自动重启 Pod（需要手动触发或使用工具）。

## Secret
- 存储敏感数据（密码、密钥、证书）。
- Base64 编码（不是加密，需要配合 RBAC 控制访问）。
- 类型：Opaque（通用）、dockerconfigjson（镜像仓库认证）、tls（TLS 证书）。

## 使用方式
1. 环境变量：envFrom 或 env.valueFrom。
2. 文件挂载：volumeMounts 挂载为文件。
3. 拉取私有镜像：imagePullSecrets。
""")

add("k8s", "05_ingress",
"""# Kubernetes Ingress

Ingress 提供 HTTP/HTTPS 七层路由，将外部流量路由到集群内 Service。

## Ingress Controller
- 需要部署 Ingress Controller（如 nginx-ingress、traefik）。
- Controller 监听 Ingress 资源变化并更新负载均衡配置。

## 路由规则
```yaml
spec:
  rules:
  - host: api.example.com
    http:
      paths:
      - path: /v1
        backend:
          service:
            name: api-v1-service
            port:
              number: 80
```

## TLS
通过 Secret 存储 TLS 证书，Ingress 配置 tls 字段启用 HTTPS。
""")

add("k8s", "06_hpa",
"""# Kubernetes HPA（水平自动扩缩容）

HPA 根据 Pod 资源使用率自动调整 Deployment 副本数。

## 工作原理
1. HPA 通过 Metrics Server 获取 Pod 的 CPU/内存使用率。
2. 计算期望副本数 = ceil(当前副本数 × 当前指标 / 目标指标)。
3. 调整 Deployment 的 replicas。

## 扩缩容策略
- 扩容快速（60秒内可扩容）。
- 缩容缓慢（默认 5 分钟稳定窗口，防止抖动）。

## 自定义指标
支持基于自定义指标（QPS、队列长度等）扩缩容，需要部署 Custom Metrics API。
""")

add("k8s", "07_statefulset",
"""# Kubernetes StatefulSet

StatefulSet 管理有状态应用，每个 Pod 有唯一标识和持久化存储。

## 特点
- 有序部署：Pod 按顺序创建（0, 1, 2...）。
- 有序删除：逆序删除。
- 稳定网络标识：Pod 名为 {statefulset}-{序号}。
- 持久存储：每个 Pod 绑定独立的 PVC，删除 Pod 不删 PVC。

## 适用场景
- 数据库（MySQL、PostgreSQL）。
- 消息队列（Kafka、RabbitMQ）。
- 分布式存储（Redis Cluster、Elasticsearch）。

## 与 Deployment 区别
Deployment Pod 是无序随机的，StatefulSet Pod 有序且有稳定标识。
""")

add("k8s", "08_rbac",
"""# Kubernetes RBAC（基于角色的访问控制）

## 核心概念
- Role：命名空间内的权限规则。
- ClusterRole：集群级权限规则。
- RoleBinding：将 Role 绑定到用户/组/服务账户。
- ClusterRoleBinding：将 ClusterRole 绑定到主体。

## ServiceAccount
- Pod 默认使用 default ServiceAccount。
- 可以创建自定义 ServiceAccount 并绑定角色。
- Pod 通过 ServiceAccount 的 token 访问 K8s API。

## 示例
创建只读角色并绑定到 ServiceAccount：
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: default
  name: pod-reader
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
```
""")

add("k8s", "09_pv_pvc",
"""# Kubernetes 持久化存储

## PV 和 PVC
- PV（PersistentVolume）：集群管理员创建的存储资源。
- PVC（PersistentVolumeClaim）：用户对存储的请求。
- PVC 匹配 PV 的容量和访问模式。

## 访问模式
- ReadWriteOnce（RWO）：单节点读写。
- ReadOnlyMany（ROX）：多节点只读。
- ReadWriteMany（RWX）：多节点读写（需要 NFS 等共享存储）。

## StorageClass
- 动态供给：PVC 创建时自动创建 PV。
- 支持多种 provisioner（NFS、Ceph、云盘等）。
- 可以设置默认 StorageClass。
""")

add("k8s", "10_probe",
"""# Kubernetes 健康探针

## 三种探针
1. livenessProbe：存活探针，失败时重启容器。
2. readinessProbe：就绪探针，失败时从 Service 端点移除（不重启）。
3. startupProbe：启动探针，成功后才执行 liveness/readiness（适合慢启动应用）。

## 探测方式
- HTTP GET：检查 HTTP 响应码（2xx/3xx 为成功）。
- TCP Socket：检查 TCP 端口是否可连接。
- Exec：执行容器内命令，退出码 0 为成功。

## 参数
- initialDelaySeconds：容器启动后等待多久开始探测。
- periodSeconds：探测间隔。
- timeoutSeconds：探测超时。
- failureThreshold：连续失败次数。
""")

# ===== Python (12) =====
add("python", "01_gil",
"""# Python GIL（全局解释器锁）

GIL 是 CPython 解释器中的互斥锁，确保同一时刻只有一个线程执行 Python 字节码。

## 存在原因
1. 内存管理非线程安全：CPython 使用引用计数，多线程同时修改会导致计数错误。
2. 简化 C 扩展开发：不需要自己处理线程安全。

## 影响
- CPU 密集型任务：多线程无法利用多核，甚至比单线程慢。
- I/O 密集型任务：影响小，I/O 操作会释放 GIL。

## 绕过 GIL
- multiprocessing：多进程，每个进程独立 GIL。
- C 扩展：在 C 代码中手动释放 GIL（如 NumPy）。
- asyncio：协程替代多线程处理 I/O 并发。
""")

add("python", "02_asyncio",
"""# Python asyncio 异步编程

asyncio 是 Python 的异步 I/O 框架，基于事件循环（Event Loop）。

## 核心概念
- async def：定义协程函数。
- await：等待协程完成，交出控制权。
- asyncio.create_task：调度协程并发执行。
- asyncio.gather：并发运行多个协程。

## 事件循环
- 事件循环监控 I/O 事件，就绪时恢复对应协程。
- 单线程内通过协程切换实现并发，不需要多线程。

## 与多线程区别
- asyncio：单线程协程切换，无锁问题，适合 I/O 密集型。
- 多线程：受 GIL 限制，有线程切换开销。
- 多进程：真正的并行，适合 CPU 密集型。
""")

add("python", "03_decorator",
"""# Python 装饰器

装饰器是一种高阶函数，在不修改原函数的情况下为其添加新功能。

## 基本语法
```python
def log(func):
    def wrapper(*args, **kwargs):
        print(f"调用 {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

@log
def hello():
    print("hello")
```

## 带参数的装饰器
需要三层嵌套：外层接收参数，中层接收函数，内层执行。

## functools.wraps
使用 @functools.wraps(func) 保留原函数的元信息（__name__、__doc__）。

## 类装饰器
通过 __call__ 方法实现，可以在 __init__ 中接收参数。
""")

add("python", "04_metaclass",
"""# Python 元类（Metaclass）

元类是创建类的类。普通类通过 type 创建，元类可以自定义类的创建过程。

## type
type 是 Python 的默认元类。type('Foo', (object,), {'attr': 1}) 创建一个类。

## 自定义元类
```python
class MyMeta(type):
    def __new__(mcs, name, bases, attrs):
        attrs['custom'] = True
        return super().__new__(mcs, name, bases, attrs)

class Foo(metaclass=MyMeta):
    pass
```

## __init_subclass__
Python 3.6+ 的 __init_subclass__ 可以替代大多数元类场景，更简单。
""")

add("python", "05_gc",
"""# Python 垃圾回收（GC）

## 引用计数
- 每个对象有引用计数，引用时 +1，删除时 -1。
- 计数为 0 时立即回收。
- 优点：即时回收，简单。
- 缺点：无法处理循环引用。

## 分代回收
- 对象分为三代（0/1/2），新对象在第 0 代。
- GC 定期扫描第 0 代，存活的对象晋升到下一代。
- 老对象扫描频率更低（越老越可能是长期存活对象）。

## 标记清除
- 用于处理循环引用。
- 从根对象出发标记可达对象，清除不可达对象。
""")

add("python", "06_venv",
"""# Python 虚拟环境

## 为什么需要虚拟环境
- 不同项目可能需要不同版本的依赖包。
- 虚拟环境隔离各项目的依赖，避免冲突。

## 常用工具
1. venv（内置）：python -m venv .venv
2. virtualenv：第三方，比 venv 更快，支持更多功能。
3. conda：适合科学计算，可以管理非 Python 依赖。
4. uv：Rust 编写，极快的包管理和虚拟环境工具。

## 依赖管理
- requirements.txt：pip freeze > requirements.txt。
- pyproject.toml：现代 Python 项目标准（PEP 621）。
- uv.lock / poetry.lock：锁定文件，保证可复现安装。
""")

add("python", "07_type_hints",
"""# Python 类型提示（Type Hints）

类型提示是 Python 3.5+ 的功能，用于标注变量和函数的类型。

## 基本用法
```python
def greet(name: str) -> str:
    return f"Hello, {name}"

x: int = 10
names: list[str] = []
```

## typing 模块
- Optional[str]：等价于 str | None。
- Union[int, str]：int 或 str。
- Callable[[int], str]：接受 int 返回 str 的可调用对象。
- TypedDict：带类型的字典。

## 运行时不强制
类型提示不影响运行时行为，但可以被 mypy/pyright 等工具静态检查。
""")

add("python", "08_generator",
"""# Python 生成器（Generator）

生成器是一种特殊的迭代器，使用 yield 暂停函数执行并返回值。

## 基本用法
```python
def count_up(n):
    i = 0
    while i < n:
        yield i
        i += 1
```

## 优势
- 惰性求值：按需生成值，不占用大量内存。
- 适合处理大数据流（读取大文件、数据库游标）。

## 生成器表达式
(x**2 for x in range(1000000)) 比 [x**2 for x in range(1000000)] 节省内存。

## send 和 throw
- gen.send(value)：向生成器发送值，yield 表达式接收。
- gen.throw(Exception)：在 yield 处抛出异常。
""")

add("python", "09_context_manager",
"""# Python 上下文管理器（Context Manager）

上下文管理器通过 with 语句管理资源的获取和释放。

## 使用方式
```python
with open("file.txt") as f:
    content = f.read()
# 文件自动关闭
```

## 实现方式
1. 类实现：定义 __enter__ 和 __exit__ 方法。
2. contextlib.contextmanager 装饰器：用生成器实现。

## __exit__ 处理异常
__exit__ 方法接收异常信息，返回 True 则抑制异常，返回 False 则传播异常。

## async with
异步上下文管理器，使用 __aenter__ 和 __aexit__，配合 async with 使用。
""")

add("python", "10_dataclass",
"""# Python dataclass

dataclass 是 Python 3.7+ 的装饰器，自动生成类的样板代码。

```python
from dataclasses import dataclass, field

@dataclass
class User:
    name: str
    age: int = 0
    tags: list = field(default_factory=list)
```

## 自动生成的方法
- __init__：构造函数。
- __repr__：字符串表示。
- __eq__：相等比较。
- __hash__（frozen=True 时生成）。

## 参数
- frozen=True：不可变（类似 NamedTuple）。
- slots=True：使用 __slots__ 节省内存（3.10+）。
- order=True：生成比较方法（__lt__ 等）。
""")

add("python", "11_iterable",
"""# Python 迭代器协议

## 可迭代对象（Iterable）
实现了 __iter__ 方法的对象，返回一个迭代器。
常见可迭代对象：list、dict、set、str、tuple、generator。

## 迭代器（Iterator）
实现了 __iter__ 和 __next__ 方法的对象。
- __iter__ 返回自身。
- __next__ 返回下一个值，没有值时抛出 StopIteration。

## for 循环原理
```python
for x in iterable:
    pass
# 等价于
it = iter(iterable)
while True:
    try:
        x = next(it)
    except StopIteration:
        break
```
""")

add("python", "12_walrus",
"""# Python 海象运算符（:=）

海象运算符（walrus operator）是 Python 3.8 引入的赋值表达式。

## 基本用法
在表达式中赋值：
```python
if (n := len(data)) > 10:
    print(f"数据太长: {n}")
```

## 常见场景
1. while 循环读取输入：
```python
while chunk := f.read(8192):
    process(chunk)
```

2. 列表推导中的条件过滤：
```python
results = [y for x in data if (y := f(x)) is not None]
```

3. 避免重复计算：
```python
if (match := pattern.search(text)):
    print(match.group())
```
""")

# ===== Linux (10) =====
add("linux", "01_process",
"""# Linux 进程管理

## 进程状态
- R（Running）：运行中。
- S（Sleeping）：可中断睡眠。
- D（Disk Sleep）：不可中断睡眠（等待 I/O）。
- Z（Zombie）：僵尸进程，已终止但父进程未回收。
- T（Stopped）：停止/暂停。

## 常用命令
- ps aux：查看所有进程。
- top/htop：实时进程监控。
- kill PID：发送 SIGTERM。
- kill -9 PID：发送 SIGKILL（强制终止）。
- jobs：查看后台任务。
- nohup：终端关闭后继续运行。
""")

add("linux", "02_permissions",
"""# Linux 文件权限

## 权限模型
每个文件有三组权限：所有者（u）、所属组（g）、其他用户（o）。
每组三个权限：读（r=4）、写（w=2）、执行（x=1）。

## chmod
- 数字模式：chmod 755 file（rwxr-xr-x）。
- 符号模式：chmod u+x file、chmod g-w file。

## chown
- chown user:group file：修改所有者和组。

## 特殊权限
- SUID（4xxx）：以文件所有者身份执行。
- SGID（2xxx）：以文件所属组身份执行。
- Sticky Bit（1xxx）：只有所有者能删除（/tmp 目录）。
""")

add("linux", "03_shell",
"""# Linux Shell 脚本

## 基本语法
```bash
#!/bin/bash
# 变量
name="world"
echo "hello $name"
# 条件
if [ "$1" == "test" ]; then
    echo "test mode"
fi
# 循环
for i in 1 2 3; do
    echo $i
done
```

## 常用技巧
- $(command)：命令替换。
- 管道：command1 | command2。
- 重定向：> 覆盖，>> 追加，2> 错误重定向。
- 变量：$0 脚本名，$1-$9 参数，$# 参数个数，$? 上一条命令退出码。
""")

add("linux", "04_systemd",
"""# Linux systemd 服务管理

systemd 是现代 Linux 的初始化系统和服务管理器。

## 常用命令
- systemctl start/stop/restart service：管理服务。
- systemctl enable/disable service：开机自启。
- systemctl status service：查看状态。
- journalctl -u service：查看服务日志。

## 服务文件
```ini
[Unit]
Description=My App

[Service]
ExecStart=/usr/bin/myapp
Restart=always
User=app

[Install]
WantedBy=multi-user.target
```
放在 /etc/systemd/system/ 下，执行 systemctl daemon-reload 生效。
""")

add("linux", "05_iptables",
"""# Linux iptables 防火墙

iptables 是 Linux 内核的包过滤防火墙。

## 四表五链
- 表：filter（过滤）、nat（地址转换）、mangle（修改）、raw。
- 链：INPUT、OUTPUT、FORWARD、PREROUTING、POSTROUTING。

## 常用规则
```bash
# 允许 SSH
iptables -A INPUT -p tcp --dport 22 -j ACCEPT
# 允许 HTTP/HTTPS
iptables -A INPUT -p tcp --dport 80 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
# 拒绝其他入站
iptables -A INPUT -j DROP
# 保存规则
iptables-save > /etc/iptables/rules.v4
```

## 动作
- ACCEPT：放行。
- DROP：丢弃。
- REJECT：拒绝并返回错误。
""")

add("linux", "06_cron",
"""# Linux cron 定时任务

cron 是 Linux 的定时任务调度器。

## crontab 格式
```
分 时 日 月 周 命令
*  *  *  *  *  command
```
- 分：0-59
- 时：0-23
- 日：1-31
- 月：1-12
- 周：0-7（0和7都是周日）

## 示例
```bash
# 每天凌晨3点备份
0 3 * * * /usr/bin/backup.sh
# 每5分钟检查
*/5 * * * * /usr/bin/check.sh
# 每周一上午9点
0 9 * * 1 /usr/bin/weekly.sh
```

## 命令
- crontab -e：编辑当前用户的定时任务。
- crontab -l：列出定时任务。
""")

add("linux", "07_ssh",
"""# Linux SSH 远程连接

## 基本用法
- ssh user@host：远程登录。
- ssh -p 2222 user@host：指定端口。
- ssh-keygen：生成密钥对。
- ssh-copy-id user@host：安装公钥到远程主机。

## SSH 密钥认证
1. 客户端生成密钥对（~/.ssh/id_rsa 和 id_rsa.pub）。
2. 公钥复制到远程主机的 ~/.ssh/authorized_keys。
3. 连接时用私钥认证，无需密码。

## SSH 隧道
- 本地端口转发：ssh -L 8080:remote:80 user@host。
- 远程端口转发：ssh -R 8080:local:80 user@host。
- 动态端口转发（SOCKS 代理）：ssh -D 1080 user@host。
""")

add("linux", "08_disk",
"""# Linux 磁盘管理

## 常用命令
- df -h：查看磁盘使用情况。
- du -sh /path：查看目录大小。
- lsblk：列出块设备。
- fdisk -l：查看磁盘分区。
- mount/unmount：挂载/卸载。

## LVM（逻辑卷管理）
- PV（物理卷）-> VG（卷组）-> LV（逻辑卷）。
- 可以动态调整分区大小。
- 支持快照。

## inode
- inode 存储文件元数据（权限、大小、块位置）。
- 文件名存在目录中，通过 inode 号关联文件数据。
- df -i 查看 inode 使用情况。
""")

add("linux", "09_network",
"""# Linux 网络配置

## 常用命令
- ip addr：查看 IP 地址。
- ip route：查看路由表。
- ping host：测试连通性。
- netstat -tlnp / ss -tlnp：查看监听端口。
- curl：HTTP 请求。
- wget：下载文件。

## 网络配置
- /etc/hosts：主机名映射。
- /etc/resolv.conf：DNS 配置。
- /etc/network/interfaces 或 netplan：网络接口配置。

## 排查网络问题
1. ping 网关：检查局域网连通性。
2. ping 8.8.8.8：检查外网连通性。
3. ping google.com：检查 DNS 解析。
4. telnet host port / nc -zv host port：检查端口连通性。
""")

add("linux", "10_log",
"""# Linux 日志管理

## syslog
- /var/log/messages 或 /var/log/syslog：系统日志。
- /var/log/auth.log：认证日志。
- /var/log/nginx/：Nginx 日志。
- /var/log/docker：Docker 日志。

## journalctl
systemd 的日志管理工具：
- journalctl -u service：查看服务日志。
- journalctl --since today：今天的日志。
- journalctl -f：实时跟踪日志。
- journalctl -p err：只看错误级别。

## logrotate
日志轮转工具，自动压缩和清理旧日志：
- 配置文件在 /etc/logrotate.d/。
- 支持按大小或时间轮转。
- 保留指定数量的历史日志。
""")

# ===== 计算机网络 (10) =====
add("network", "01_tcp",
"""# TCP 协议

## 三次握手
1. 客户端发送 SYN（seq=x）。
2. 服务端回复 SYN+ACK（seq=y, ack=x+1）。
3. 客户端发送 ACK（ack=y+1）。
连接建立。

## 四次挥手
1. 主动方发送 FIN。
2. 被动方回复 ACK。
3. 被动方发送 FIN。
4. 主动方回复 ACK，进入 TIME_WAIT 等待 2MSL。

## TIME_WAIT 等待 2MSL 的原因
1. 保证最后一个 ACK 能到达对端（如果丢失，被动方会重发 FIN）。
2. 防止旧连接报文干扰新连接。
""")

add("network", "02_udp",
"""# UDP 协议

UDP 是无连接的传输层协议。

## 特点
- 无连接：不需要握手。
- 不可靠：不保证送达、不保证顺序、无流量控制。
- 高效：头部仅 8 字节（TCP 20 字节）。
- 支持广播和多播。

## 应用场景
- DNS 查询（53 端口）。
- DHCP。
- 视频直播/语音通话（容忍丢包，要求低延迟）。
- SNMP 网络管理。

## TCP vs UDP
- TCP：可靠、有序、面向连接，适合文件传输、Web。
- UDP：快速、无连接，适合实时通信、广播。
""")

add("network", "03_http",
"""# HTTP 协议

## 请求方法
- GET：获取资源。
- POST：提交数据。
- PUT：更新资源（幂等）。
- DELETE：删除资源（幂等）。
- PATCH：部分更新。
- HEAD：只获取响应头。

## 状态码
- 1xx：信息（100 Continue）。
- 2xx：成功（200 OK, 201 Created, 204 No Content）。
- 3xx：重定向（301 永久, 302 临时, 304 Not Modified）。
- 4xx：客户端错误（400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 422 Unprocessable Entity）。
- 5xx：服务端错误（500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable）。
""")

add("network", "04_https",
"""# HTTPS 协议

HTTPS = HTTP + TLS/SSL，在 HTTP 和 TCP 之间加了加密层。

## TLS 握手过程
1. ClientHello：客户端发送支持的 TLS 版本和加密套件。
2. ServerHello：服务端选择加密套件，发送证书。
3. 客户端验证证书（CA 签名、域名匹配、有效期）。
4. 密钥交换：生成对称加密的会话密钥。
5. 后续通信用对称加密。

## 对称 vs 非对称加密
- 非对称加密（RSA/ECC）：用于握手阶段交换密钥，慢但安全。
- 对称加密（AES）：用于数据传输，快且安全。

## 证书
- 由 CA（证书颁发机构）签发。
- 包含域名、公钥、有效期、CA 签名。
""")

add("network", "05_dns",
"""# DNS 域名系统

DNS 将域名解析为 IP 地址。

## 解析过程
1. 浏览器缓存 -> 操作系统缓存 -> hosts 文件。
2. 本地 DNS 服务器（递归查询）。
3. 根 DNS 服务器 -> 顶级域服务器 -> 权威 DNS 服务器。
4. 返回 IP 地址，缓存到各级。

## 记录类型
- A：域名 -> IPv4。
- AAAA：域名 -> IPv6。
- CNAME：别名指向另一个域名。
- MX：邮件服务器。
- TXT：文本记录（SPF、验证）。
- NS：域名服务器。

## TTL
DNS 记录的生存时间，控制缓存时长。TTL 越短，变更生效越快。
""")

add("network", "06_cdn",
"""# CDN（内容分发网络）

CDN 将内容缓存到全球各地的边缘节点，用户就近访问。

## 工作原理
1. 用户请求域名，DNS 返回最近的 CDN 节点 IP。
2. 用户请求 CDN 节点。
3. CDN 节点有缓存则直接返回，无缓存则回源获取并缓存。

## 核心功能
- 加速：就近访问，降低延迟。
- 负载均衡：分散源站压力。
- 安全：隐藏源站 IP，DDoS 防护。

## 缓存策略
- 静态资源（图片/CSS/JS）：长缓存。
- 动态内容：不缓存或短缓存。
- 通过 Cache-Control 和 Expires 头控制。
""")

add("network", "07_websocket",
"""# WebSocket 协议

WebSocket 提供全双工通信，客户端和服务端可以互相推送消息。

## 握手过程
1. 客户端发送 HTTP 请求，带 Upgrade: websocket 头。
2. 服务端返回 101 Switching Protocols。
3. 连接升级为 WebSocket，后续通信用 WebSocket 帧协议。

## 特点
- 全双工：双向实时通信。
- 低延迟：一次握手后保持长连接。
- 少开销：不像 HTTP 每次请求带完整头。
- 支持二进制和文本帧。

## 应用场景
- 实时聊天、推送通知。
- 在线协作编辑。
- 实时游戏。
- 股票行情。
""")

add("network", "08_tls",
"""# TLS 1.3 协议

TLS 1.3 是最新的传输层安全协议，相比 TLS 1.2 有显著改进。

## 改进
1. 握手简化为 1-RTT（TLS 1.2 需要 2-RTT），甚至支持 0-RTT 恢复。
2. 废弃不安全算法（RSA 密钥交换、CBC 模式、MD5、SHA-1）。
3. 只保留 AEAD 加密（AES-GCM、ChaCha20-Poly1305）。
4. 前向安全：每次连接使用临时密钥，即使长期密钥泄露也不影响历史通信。

## 密钥交换
TLS 1.3 只支持 ECDHE/DHE，不支持 RSA 密钥交换，保证前向安全。

## 0-RTT
客户端在第一个包中就携带应用数据，利用之前会话的密钥材料。有重放攻击风险，只适合幂等请求。
""")

add("network", "09_load_balancing",
"""# 负载均衡

## 四层负载均衡（L4）
- 基于 IP + 端口转发，不关心应用层协议。
- 代表：LVS、Nginx stream、HAProxy（TCP 模式）。
- 性能高，功能简单。

## 七层负载均衡（L7）
- 基于 HTTP/HTTPS 协议内容转发（URL、Header、Cookie）。
- 代表：Nginx、HAProxy、Envoy。
- 灵活，支持内容路由、SSL 终止。

## 算法
- 轮询（Round Robin）：按顺序分配。
- 加权轮询：按权重分配。
- 最少连接：分配给连接数最少的服务器。
- IP 哈希：相同 IP 固定到同一服务器。
- 一致性哈希：节点增减时最小化缓存失效。
""")

add("network", "10_proxy",
"""# 代理服务器

## 正向代理
- 客户端配置代理，代理替客户端访问服务端。
- 服务端不知道真实客户端 IP。
- 用途：翻墙、缓存、访问控制。
- 代表：Squid、Shadowsocks。

## 反向代理
- 服务端配置代理，代理替服务端接收客户端请求。
- 客户端不知道真实服务端 IP。
- 用途：负载均衡、SSL 终止、缓存、安全防护。
- 代表：Nginx、HAProxy、CDN。

## 区别
- 正向代理代理客户端，反向代理代理服务端。
- 正向代理客户端知道目标，反向代理客户端不知道目标。
""")

# ===== 消息队列 (10) =====
add("mq", "01_kafka_partition",
"""# Kafka 分区（Partition）

## 分区机制
- 每个 Topic 分为多个 Partition。
- Partition 是并行度的基本单位。
- 每个 Partition 内消息有序，跨 Partition 不保证顺序。

## 消费者组
- 同一 Consumer Group 内的消费者共同消费所有 Partition。
- 每个 Partition 只能被组内一个消费者消费。
- 消费者数超过 Partition 数时，多余消费者空闲。

## 分区分配策略
- Range：按主题分配连续分区。
- RoundRobin：轮询分配所有主题的分区。
- Sticky：尽量保持原有分配，减少重平衡开销。
""")

add("mq", "02_kafka_isr",
"""# Kafka ISR（In-Sync Replicas）

ISR 是与 Leader 保持同步的副本集合。

## 工作原理
- Follower 从 Leader 拉取数据同步。
- 落后超过 replica.lag.time.max.ms 的 Follower 被移出 ISR。
- Leader 宕机时从 ISR 中选举新 Leader。

## acks 配置
- acks=0：不等确认，最快但可能丢数据。
- acks=1：Leader 写入即确认（默认）。
- acks=all：ISR 所有副本确认才返回，最安全。

## min.insync.replicas
配合 acks=all 使用，设置最小同步副本数。如果 ISR 中的副本数低于此值，拒绝写入以防止数据丢失。
""")

add("mq", "03_kafka_ordering",
"""# Kafka 消息顺序性

## 分区内有序
同一 Partition 内消息按写入顺序消费，保证有序。

## 跨分区不保证
不同 Partition 之间没有顺序保证。

## 保证全局顺序
只能使用单个 Partition，但牺牲并行度。

## 部分顺序方案
按业务 Key 分配 Partition：
- 相同 Key 的消息发送到同一 Partition。
- 例如：订单 ID 作为 Key，同一订单的创建、支付、发货消息有序。
- 不同订单之间可以并行处理。
""")

add("mq", "04_rabbitmq_exchange",
"""# RabbitMQ Exchange 类型

## Direct Exchange
- 根据 routing key 精确匹配。
- 消息 routing key 与 binding key 完全一致才路由。

## Fanout Exchange
- 忽略 routing key，广播到所有绑定的队列。
- 每个队列都收到完整消息。

## Topic Exchange
- 根据 routing key 模式匹配。
- * 匹配一个单词，# 匹配零或多个单词。
- 例如 order.*.created 匹配 order.payment.created。

## Headers Exchange
- 根据消息头（headers）匹配，不使用 routing key。
- x-match: all（全部匹配）或 any（任一匹配）。
""")

add("mq", "05_rabbitmq_dead_letter",
"""# RabbitMQ 死信队列

死信队列存储无法正常消费的消息。

## 产生死信的情况
1. 消息被拒绝（NACK/Reject）且 requeue=false。
2. 消息 TTL 过期。
3. 队列达到最大长度（x-max-length）。

## 死信路由
通过队列属性指定死信 Exchange：
- x-dead-letter-exchange：死信发送到哪个 Exchange。
- x-dead-letter-routing-key：死信的 routing key。

## 应用场景
- 延迟队列：设置消息 TTL，过期后进入死信队列消费。
- 异常处理：消费失败的消息转入死信队列人工处理。
""")

add("mq", "06_kafka_consumer_group",
"""# Kafka 消费者组

## 核心概念
- 消费者组是一组协同消费 Topic 的消费者。
- 组内每个消费者负责不同的 Partition。
- 不同消费者组互不影响，各自消费完整数据。

## Offset 管理
- 消费者记录消费位置（offset）。
- 自动提交：enable.auto.commit=true（默认每 5 秒提交）。
- 手动提交：commitSync() / commitAsync()，更精确但需要自行管理。
- __consumer_offsets 主题存储所有消费者组的 offset。

## Rebalance（重平衡）
- 消费者加入/退出时触发 Rebalance。
- 分区重新分配给消费者。
- Rebalance 期间消费者无法消费（Stop the World）。
""")

add("mq", "07_kafka_delivery_semantics",
"""# Kafka 消息投递语义

## 三种语义
1. At Most Once（最多一次）：消息可能丢失但不会重复。
   - 先提交 offset 再处理消息。
2. At Least Once（至少一次）：消息不会丢失但可能重复。
   - 先处理消息再提交 offset（默认）。
3. Exactly Once（精确一次）：不丢失不重复。
   - 事务性生产者 + 幂等消费者。

## 幂等生产者
enable.idempotence=true：Kafka 为每条消息分配序列号，Broker 去重。

## 事务
Kafka 事务支持跨 Partition 的原子写入：
- initTransactions, beginTransaction, commitTransaction。
- 消费端设置 isolation.level=read_committed 只读已提交消息。
""")

add("mq", "08_kafka_retention",
"""# Kafka 消息保留策略

## 基于时间
- log.retention.hours：保留时间（默认 168 小时 = 7 天）。
- 超过时间的消息被删除。

## 基于大小
- log.retention.bytes：每个 Partition 的最大大小（默认 -1 即无限）。
- 超过大小从最旧的消息开始删除。

## 日志压缩（Log Compaction）
- 对于相同 key 的消息，只保留最新值。
- 适合状态变更日志（如用户信息更新）。
- 通过 cleanup.policy=compact 开启。

## Segment
- 日志文件分为多个 Segment（默认 1GB）。
- 只有 inactive segment 才会被清理。
""")

add("mq", "09_rabbitmq_mq",
"""# RabbitMQ 消息模型

## 核心组件
- Producer：生产者，发送消息到 Exchange。
- Exchange：交换机，根据规则路由消息到队列。
- Queue：队列，存储消息。
- Binding：绑定，连接 Exchange 和 Queue 的规则。
- Consumer：消费者，从队列消费消息。

## 消息确认
- 自动确认（autoAck=true）：消息发出即确认，可能丢失。
- 手动确认（autoAck=false）：消费者处理完后调用 basic.ack 确认。
- 拒绝（basic.nack/basic.reject）：可以选择重入队列或变为死信。

## QoS 预取
prefetch_count：消费者未确认消息的最大数量，防止消费者被压垮。
""")

add("mq", "10_redis_stream_mq",
"""# Redis Stream 作为消息队列

## 与 Kafka/RabbitMQ 对比
- Redis Stream 轻量级，适合小规模场景。
- Kafka 适合高吞吐、大数据量场景。
- RabbitMQ 适合复杂路由场景。

## 消费者组
- XGROUP CREATE：创建消费者组。
- XREADGROUP：消费者组读取消息。
- XACK：确认消息（消息必须确认，否则留在 PEL 中）。
- XPENDING：查看待确认消息。
- XCLAIM：将超时消息转移给其他消费者。

## 与 Pub/Sub 区别
- Stream 持久化消息，Pub/Sub 不持久化。
- Stream 支持消费者组和 ACK，Pub/Sub 没有。
- Stream 支持消息回溯，Pub/Sub 不支持。
""")

# ===== Go 语言 (8) =====
add("go", "01_goroutine",
"""# Go goroutine

goroutine 是 Go 语言的轻量级线程。

## 特点
- 初始栈仅 2KB（线程通常 1-8MB）。
- 由 Go 运行时调度，不依赖操作系统线程。
- M:N 调度——M 个 goroutine 映射到 N 个 OS 线程。

## 创建
```go
go func() {
    fmt.Println("hello from goroutine")
}()
```

## 与线程区别
- goroutine 更轻量，可以创建数十万个。
- goroutine 通信通过 channel，不推荐共享内存。
- goroutine 由 Go 运行时调度，切换成本远低于 OS 线程。
""")

add("go", "02_channel",
"""# Go channel（通道）

channel 是 goroutine 间通信的管道。

## 基本用法
```go
ch := make(chan int)     // 无缓冲通道
ch := make(chan int, 10) // 有缓冲通道

ch <- 42    // 发送
v := <-ch   // 接收
close(ch)   // 关闭
```

## 无缓冲 vs 有缓冲
- 无缓冲：发送和接收必须同时就绪（同步）。
- 有缓冲：缓冲区满前发送不阻塞（异步）。

## 方向
- chan int：双向。
- chan<- int：只发送。
- <-chan int：只接收。

## select
```go
select {
case v := <-ch1:
    fmt.Println(v)
case ch2 <- 42:
    fmt.Println("sent")
default:
    fmt.Println("no activity")
}
```
""")

add("go", "03_gmp",
"""# Go GMP 调度模型

## 三个核心概念
- G（Goroutine）：用户协程。
- M（Machine）：操作系统线程。
- P（Processor）：逻辑处理器，持有可运行 G 的本地队列。

## 调度过程
1. M 绑定一个 P，从 P 的本地队列取 G 执行。
2. 本地队列为空时，从全局队列偷取 G。
3. 全局队列也为空时，从其他 P 偷取一半 G（work stealing）。

## GOMAXPROCS
- 控制 P 的数量（默认等于 CPU 核数）。
- P 数量决定了并行度（同时执行的 goroutine 数）。

## 阻塞处理
- goroutine 发生系统调用时，M 释放 P 给其他 M 使用。
- goroutine 发生网络 I/O 时，由 netpoller 异步管理。
""")

add("go", "04_interface",
"""# Go interface（接口）

Go 的接口是隐式实现的——不需要显式声明 implements。

## 定义
```go
type Reader interface {
    Read(p []byte) (n int, err error)
}
```

## 隐式实现
任何类型只要实现了 Read 方法，就满足 Reader 接口，无需声明。

## 空接口
interface{}（Go 1.18+ 可写为 any）可以接受任意类型。

## 类型断言
```go
v, ok := i.(Reader)
```

## 类型开关
```go
switch v := i.(type) {
case int:
    fmt.Println("int", v)
case string:
    fmt.Println("string", v)
}
```
""")

add("go", "05_error",
"""# Go 错误处理

Go 不使用 try/catch，通过返回值传递错误。

## 基本模式
```go
result, err := doSomething()
if err != nil {
    return err
}
```

## 自定义错误
```go
errors.New("something went wrong")
fmt.Errorf("failed: %v", err)
```

## 错误包装（Go 1.13+）
```go
// 包装
err = fmt.Errorf("operation failed: %w", originalErr)

// 解包
unwrapped := errors.Unwrap(err)

// 判断
errors.Is(err, targetErr)
errors.As(err, &targetType)
```

## panic 和 recover
- panic：不可恢复的错误，程序退出。
- recover：在 defer 中捕获 panic，恢复执行。
- 只在严重错误时使用 panic（如数组越界、nil 指针）。
""")

add("go", "06_context",
"""# Go context（上下文）

context 用于在 goroutine 间传递截止时间、取消信号和请求范围的值。

## 四个核心函数
- context.Background()：根 context。
- context.WithCancel()：手动取消。
- context.WithTimeout()：超时自动取消。
- context.WithValue()：传递请求范围的值。

## 使用模式
```go
ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
defer cancel()

select {
case <-ctx.Done():
    return ctx.Err()
case result := <-ch:
    return result
}
```

## 规范
- context 作为函数第一个参数传递。
- 不要存储 context 在结构体中。
- WithValue 只传请求范围的元数据，不传业务参数。
""")

add("go", "07_defer",
"""# Go defer（延迟执行）

defer 语句在函数返回前执行，常用于资源释放。

## 执行顺序
多个 defer 按 LIFO（后进先出）顺序执行。

## 常见用途
```go
// 文件关闭
f, err := os.Open("file")
defer f.Close()

// 锁释放
mu.Lock()
defer mu.Unlock()
```

## 参数立即求值
defer 的参数在声明时求值，不是在执行时：
```go
i := 1
defer fmt.Println(i) // 输出 1
i = 2
```

## 性能
Go 1.14+ 大幅优化了 defer 的性能，几乎零开销。
""")

add("go", "08_sync",
"""# Go sync 包（同步原语）

## Mutex（互斥锁）
```go
var mu sync.Mutex
mu.Lock()
// 临界区
mu.Unlock()
```

## RWMutex（读写锁）
- 允许多个读同时进行。
- 写时排他。
```go
var rw sync.RWMutex
rw.RLock() / rw.RUnlock()  // 读锁
rw.Lock() / rw.Unlock()    // 写锁
```

## WaitGroup（等待组）
```go
var wg sync.WaitGroup
wg.Add(3)
for i := 0; i < 3; i++ {
    go func() {
        defer wg.Done()
        // work
    }()
}
wg.Wait()
```

## Once（一次性执行）
```go
var once sync.Once
once.Do(func() {
    // 只执行一次
})
```
""")

# ===== 算法与数据结构 (8) =====
add("algo", "01_sort",
"""# 排序算法

## 常见排序算法

| 算法 | 时间复杂度(平均) | 空间复杂度 | 稳定性 |
|------|-----------------|-----------|--------|
| 冒泡排序 | O(n²) | O(1) | 稳定 |
| 选择排序 | O(n²) | O(1) | 不稳定 |
| 插入排序 | O(n²) | O(1) | 稳定 |
| 快速排序 | O(n log n) | O(log n) | 不稳定 |
| 归并排序 | O(n log n) | O(n) | 稳定 |
| 堆排序 | O(n log n) | O(1) | 不稳定 |

## 稳定性
稳定性指相等元素排序后保持原有相对顺序。稳定排序在多关键字排序中有用。

## 快速排序原理
1. 选择基准（pivot）。
2. 将小于基准的放左边，大于的放右边。
3. 递归排序左右两部分。
""")

add("algo", "02_btree",
"""# B+ 树

B+ 树是数据库索引的核心数据结构。

## 特点
- 所有数据都在叶子节点。
- 非叶子节点只存储索引（指针）。
- 叶子节点通过双向链表连接。
- 每个节点可以有多个子节点（多路搜索树）。

## 为什么数据库用 B+ 树
1. 磁盘 I/O 友好：每个节点大小等于一个页（4KB/16KB），一次 I/O 读一个节点。
2. 范围查询高效：叶子节点链表连接，顺序扫描。
3. 树高很低：3-4 层即可存储千万级数据，I/O 次数少。

## B 树 vs B+ 树
- B 树：非叶子节点也存数据。
- B+ 树：只有叶子节点存数据，非叶子节点更多空间存索引，树更矮。
""")

add("algo", "03_hash",
"""# 哈希表（Hash Table）

哈希表通过哈希函数将 key 映射到数组下标，实现 O(1) 平均查找。

## 哈希冲突解决
1. 链地址法（拉链法）：冲突位置用链表存储。Java HashMap 用此方法。
2. 开放地址法：冲突后找下一个空位（线性探测、二次探测、双重哈希）。

## 扩容
- 负载因子 = 元素数 / 数组长度。
- 超过阈值（如 0.75）时扩容（通常翻倍），重新哈希。
- 扩容是 O(n) 操作，但均摊到每次插入是 O(1)。

## 一致性哈希
- 用于分布式缓存（如 Redis 集群分片）。
- 节点增减时只影响相邻区间的数据，最小化缓存失效。
- 使用虚拟节点解决数据倾斜问题。
""")

add("algo", "04_dp",
"""# 动态规划（Dynamic Programming）

动态规划将问题分解为子问题，存储子问题的解避免重复计算。

## 核心要素
1. 最优子结构：问题的最优解包含子问题的最优解。
2. 重叠子问题：子问题会被重复计算。

## 实现方式
1. 自顶向下 + 记忆化：递归 + 缓存。
2. 自底向上：迭代填表。

## 经典问题
- 斐波那契数列：dp[i] = dp[i-1] + dp[i-2]。
- 最长公共子序列（LCS）。
- 0-1 背包问题。
- 编辑距离。
- 爬楼梯：dp[i] = dp[i-1] + dp[i-2]。

## 状态转移方程
描述如何从子问题的解推导当前问题的解，是 DP 的核心。
""")

add("algo", "05_graph",
"""# 图算法

## 图的表示
1. 邻接矩阵：二维数组，O(V²) 空间，适合稠密图。
2. 邻接表：每个顶点存相邻顶点列表，O(V+E) 空间，适合稀疏图。

## 遍历
- BFS（广度优先）：队列，按层遍历，适合最短路径（无权图）。
- DFS（深度优先）：栈/递归，深入到底再回溯，适合连通性判断。

## 最短路径
- Dijkstra：非负权重图，单源最短路径，O((V+E)log V)。
- Bellman-Ford：可处理负权重，O(VE)。
- Floyd-Warshall：所有顶点对最短路径，O(V³)。

## 最小生成树
- Kruskal：按边权排序，贪心加入不形成环的边。
- Prim：从一个顶点开始，贪心选择最小权边扩展。
""")

add("algo", "06_binary_search",
"""# 二分查找

二分查找要求数组有序，时间复杂度 O(log n)。

## 基本模板
```python
def binary_search(nums, target):
    lo, hi = 0, len(nums) - 1
    while lo <= hi:
        mid = lo + (hi - lo) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
```

## 变体
- 查找第一个等于 target 的位置。
- 查找最后一个等于 target 的位置。
- 查找第一个大于 target 的位置。
- 查找最后一个小于 target 的位置。

## 适用条件
- 数组有序（或部分有序）。
- 支持随机访问（数组，非链表）。
""")

add("algo", "07_linked_list",
"""# 链表

## 类型
1. 单链表：每个节点有 next 指针。
2. 双链表：每个节点有 prev 和 next 指针。
3. 循环链表：尾节点指向头节点。

## 与数组对比
| 维度 | 数组 | 链表 |
|------|------|------|
| 随机访问 | O(1) | O(n) |
| 头部插入 | O(n) | O(1) |
| 内存 | 连续 | 不连续 |
| 缓存友好 | 是 | 否 |

## 常见操作
- 反转链表：迭代或递归。
- 检测环：快慢指针（Floyd 判圈）。
- 合并有序链表。
- 找中点：快慢指针。
""")

add("algo", "08_tree_traversal",
"""# 二叉树遍历

## 四种遍历方式
1. 前序遍历（Pre-order）：根 -> 左 -> 右。
2. 中序遍历（In-order）：左 -> 根 -> 右。BST 中序遍历得到有序序列。
3. 后序遍历（Post-order）：左 -> 右 -> 根。
4. 层序遍历（Level-order / BFS）：按层从上到下、从左到右。

## 递归实现
```python
def preorder(node):
    if not node: return
    visit(node)
    preorder(node.left)
    preorder(node.right)
```

## 非递归实现
- 前序/中序：用栈。
- 层序：用队列。

## 二叉搜索树（BST）
- 左子树所有值 < 根 < 右子树所有值。
- 中序遍历得到升序序列。
- 查找/插入/删除平均 O(log n)，最坏 O(n)（退化为链表）。
""")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # 清除旧文件
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith('.txt'):
            os.remove(os.path.join(OUTPUT_DIR, f))

    for filename, title, content in DOCS:
        filepath = os.path.join(OUTPUT_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

    print(f"Generated {len(DOCS)} documents in {OUTPUT_DIR}")

    # 按领域统计
    domains = {}
    for filename, _, _ in DOCS:
        domain = filename.split('_')[0]
        domains[domain] = domains.get(domain, 0) + 1

    for domain, count in sorted(domains.items()):
        print(f"  {domain}: {count} docs")

if __name__ == "__main__":
    main()
