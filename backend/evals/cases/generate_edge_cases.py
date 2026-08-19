"""
生成边界测试用例集，覆盖 4 类边界情况：
1. 超长文档（long_doc）
2. 跨文档关联（cross_doc）
3. 模糊查询（ambiguous）
4. 最新vs过时冲突（conflict）

用法: cd backend && uv run python -m evals.cases.generate_edge_cases
"""
import json
import os

OUTPUT = os.path.join(os.path.dirname(__file__), "edge_test_cases.jsonl")

CASES = []

def add(cid, edge_type, query, keywords, refusal=False, note=""):
    CASES.append({
        "id": cid,
        "edge_type": edge_type,
        "query": query,
        "expected_keywords": keywords,
        "expected_refusal": refusal,
        "note": note,
    })

# ===== 1. 超长文档（long_doc）=====
add("ld-01", "long_doc", "MySQL 的架构分为哪三层？", ["连接层", "服务层", "存储引擎"])
add("ld-02", "long_doc", "MySQL 8.0 移除了什么功能？", ["查询缓存"])
add("ld-03", "long_doc", "innodb_buffer_pool_size 建议设为物理内存的多少？", ["70%", "缓冲池"])
add("ld-04", "long_doc", "MySQL 主从复制中 relay log 的作用是什么？", ["relay log", "中继", "IO线程"])
add("ld-05", "long_doc", "MySQL 高可用方案有哪些？", ["MHA", "MGR", "Orchestrator", "Router"])
add("ld-06", "long_doc", "什么是索引下推？", ["ICP", "下推", "存储引擎", "5.6"])
add("ld-07", "long_doc", "MySQL 半同步复制要求什么？", ["从库", "确认", "binlog"])
add("ld-08", "long_doc", "什么是分区表？支持哪些分区方式？", ["range", "list", "hash", "key"])

# ===== 2. 跨文档关联（cross_doc）=====
add("cd-01", "cross_doc", "分布式事务中 MySQL 的 XA 两阶段提交是怎样的？", ["PREPARE", "COMMIT", "协调者"])
add("cd-02", "cross_doc", "Kafka 如何配合实现分布式事务的最终一致性？", ["事务", "Outbox", "本地消息表", "最终一致"])
add("cd-03", "cross_doc", "Outbox Pattern 发件箱模式的完整流程是什么？", ["outbox", "本地事务", "MQ", "重试"])
add("cd-04", "cross_doc", "Redis 分布式锁如何与数据库事务配合实现并发安全？", ["SET", "NX", "PX", "锁"])
add("cd-05", "cross_doc", "分布式事务有哪些常见的实现方案？", ["XA", "最终一致", "消息", "Outbox"])
add("cd-06", "cross_doc", "什么是事务消息？它如何保证分布式一致性？", ["消息", "本地", "一致性", "事务"])

# ===== 3. 模糊查询（ambiguous）=====
add("am-01", "ambiguous", "Spring 的 IoC 是什么？", ["控制反转", "容器", "依赖"])
add("am-02", "ambiguous", "Spring 的事务传播行为有哪些？", ["REQUIRED", "REQUIRES_NEW", "NESTED", "传播"])
add("am-03", "ambiguous", "Spring Watch 是怎么工作的？", ["发条", "擒纵", "齿轮", "摆轮"])
add("am-04", "ambiguous", "数据库连接池 maxPoolSize 应该设多少？", ["10", "小池子", "连接"])
add("am-05", "ambiguous", "连接池的最大连接数原则是什么？", ["核心数", "小池子", "压测"])
add("am-06", "ambiguous", "Spring 框架支持哪些依赖注入方式？", ["构造函数", "setter", "字段"])

# ===== 4. 最新vs过时冲突（conflict）=====
add("cf-01", "conflict", "MySQL 应该建多少个索引合适？", ["5-6", "不是越多越好", "写开销"])
add("cf-02", "conflict", "text 大字段应该怎么建索引？", ["前缀索引", "前N个字符", "前缀"])
add("cf-03", "conflict", "小表应该走索引还是全表扫描？", ["全表扫描", "优化器", "小表"])
add("cf-04", "conflict", "数据库连接池 maxPoolSize 推荐值是多少？", ["10", "小池子"])
add("cf-05", "conflict", "索引区分度低的列应该建索引吗？", ["区分度", "收益低", "不建"])
add("cf-06", "conflict", "冗余索引应该怎么处理？", ["删除", "联合索引", "替代"])

def main():
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        for case in CASES:
            f.write(json.dumps(case, ensure_ascii=False) + '\n')

    types = {}
    for c in CASES:
        types[c["edge_type"]] = types.get(c["edge_type"], 0) + 1

    print(f"Generated {len(CASES)} edge test cases in {OUTPUT}")
    for t, count in sorted(types.items()):
        print(f"  {t}: {count} cases")

if __name__ == "__main__":
    main()
