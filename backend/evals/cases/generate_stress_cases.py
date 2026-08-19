"""
生成 66 题评估集，覆盖 6 类意图 + 拒答。
用法: cd backend && uv run python -m evals.cases.generate_stress_cases
"""
import json
import os

OUTPUT = os.path.join(os.path.dirname(__file__), "stress_test_cases.jsonl")

CASES = []

def add(cid, intent, query, keywords, refusal=False):
    CASES.append({
        "id": cid,
        "intent": intent,
        "query": query,
        "expected_keywords": keywords,
        "expected_refusal": refusal,
    })

# ===== SIMPLE (10) - 短查询 ≤20字 + 事实关键词 =====
add("s-01", "simple", "Redis默认fsync策略是什么", ["everysec", "默认"])
add("s-02", "simple", "MySQL默认隔离级别", ["可重复读", "Repeatable", "RR"])
add("s-03", "simple", "Docker和虚拟机的区别", ["共享", "内核", "启动"])
add("s-04", "simple", "Python的GIL是什么", ["全局解释器锁", "互斥锁", "线程"])
add("s-05", "simple", "TCP三次握手过程", ["SYN", "ACK", "连接"])
add("s-06", "simple", "Kafka的ISR是什么", ["同步", "副本", "Leader"])
add("s-07", "simple", "Go的goroutine是什么", ["轻量级", "线程", "协程"])
add("s-08", "simple", "B+树的特点", ["叶子", "链表", "数据"])
add("s-09", "simple", "RabbitMQ Exchange类型", ["Direct", "Fanout", "Topic"])
add("s-10", "simple", "HTTP状态码422", ["校验", "Unprocessable", "验证"])

# ===== FACTUAL (10) - "什么是X"/"X是什么" =====
add("f-01", "factual", "什么是MySQL的MVCC？", ["多版本", "并发", "undo", "ReadView"])
add("f-02", "factual", "Docker的多阶段构建是什么？", ["FROM", "阶段", "镜像", "体积"])
add("f-03", "factual", "Python的装饰器是什么？", ["高阶函数", "wrapper", "功能"])
add("f-04", "factual", "Kubernetes的ConfigMap是什么？", ["配置", "环境变量", "挂载"])
add("f-05", "factual", "什么是Redis的混合持久化？", ["RDB", "AOF", "重写", "4.0"])
add("f-06", "factual", "Go语言的channel是什么？", ["通道", "通信", "goroutine"])
add("f-07", "factual", "什么是Linux的inode？", ["元数据", "文件", "索引"])
add("f-08", "factual", "什么是CDN内容分发网络？", ["边缘", "缓存", "加速"])
add("f-09", "factual", "什么是Kafka的消费者组？", ["Partition", "消费", "offset"])
add("f-10", "factual", "什么是HTTPS的TLS握手？", ["证书", "密钥", "加密", "握手"])

# ===== EXPLANATORY (10) - "为什么X"/"X的原理" =====
add("e-01", "explanatory", "为什么TCP需要三次握手而不是两次？", ["历史", "连接", "SYN", "确认"])
add("e-02", "explanatory", "为什么Python有GIL？它的原理是什么？", ["引用计数", "线程安全", "内存"])
add("e-03", "explanatory", "MySQL的redo log原理是什么？为什么需要它？", ["持久性", "物理日志", "WAL", "崩溃"])
add("e-04", "explanatory", "为什么Docker容器启动比虚拟机快？", ["共享", "内核", "进程", "秒级"])
add("e-05", "explanatory", "Kubernetes HPA自动扩缩容的原理是什么？", ["指标", "CPU", "副本", "Metrics"])
add("e-06", "explanatory", "为什么TCP的TIME_WAIT要等待2MSL？", ["ACK", "报文", "消失", "重发"])
add("e-07", "explanatory", "Redis的RDB持久化原理是什么？", ["快照", "fork", "子进程", "二进制"])
add("e-08", "explanatory", "Go的GMP调度模型原理是什么？", ["Goroutine", "Machine", "Processor", "调度"])
add("e-09", "explanatory", "MySQL MVCC实现原理是什么？", ["undo", "版本链", "ReadView", "事务ID"])
add("e-10", "explanatory", "为什么Kafka能保证高吞吐？", ["顺序", "零拷贝", "分区", "批量"])

# ===== COMPARATIVE (10) - "X和Y的区别" =====
add("c-01", "comparative", "Docker和虚拟机有什么区别？", ["共享", "内核", "启动", "资源"])
add("c-02", "comparative", "MySQL InnoDB和MyISAM有什么区别？", ["事务", "行锁", "表锁", "聚簇"])
add("c-03", "comparative", "TCP和UDP有什么区别？", ["连接", "可靠", "头部", "顺序"])
add("c-04", "comparative", "Redis RDB和AOF有什么区别？", ["快照", "命令", "二进制", "追加"])
add("c-05", "comparative", "正向代理和反向代理有什么区别？", ["客户端", "服务端", "目标"])
add("c-06", "comparative", "Kubernetes Deployment和StatefulSet有什么区别？", ["有序", "标识", "存储", "状态"])
add("c-07", "comparative", "Go的Mutex和RWMutex有什么区别？", ["读锁", "写锁", "共享", "排他"])
add("c-08", "comparative", "Kafka和RabbitMQ有什么区别？", ["吞吐", "分区", "Exchange", "顺序"])
add("c-09", "comparative", "Python多线程和多进程有什么区别？", ["GIL", "内存", "并行", "进程"])
add("c-10", "comparative", "Docker的CMD和ENTRYPOINT有什么区别？", ["覆盖", "追加", "默认", "入口"])

# ===== PROCEDURAL (10) - "如何X"/"怎么X" =====
add("p-01", "procedural", "如何配置Nginx负载均衡？", ["upstream", "轮询", "权重", "server"])
add("p-02", "procedural", "怎么优化MySQL慢查询？", ["EXPLAIN", "索引", "扫描", "优化"])
add("p-03", "procedural", "如何实现Redis分布式锁？", ["SET", "NX", "PX", "Lua"])
add("p-04", "procedural", "怎么配置Kubernetes的Ingress？", ["rules", "host", "path", "Service"])
add("p-05", "procedural", "如何使用Docker多阶段构建减小镜像体积？", ["FROM", "AS", "COPY", "alpine"])
add("p-06", "procedural", "怎么在Python中实现异步编程？", ["async", "await", "事件循环", "协程"])
add("p-07", "procedural", "如何配置Linux的crontab定时任务？", ["分", "时", "日", "月", "周"])
add("p-08", "procedural", "怎么使用Go的context实现超时控制？", ["WithTimeout", "cancel", "Done", "select"])
add("p-09", "procedural", "如何配置Docker Compose的多容器应用？", ["services", "ports", "depends_on", "image"])
add("p-10", "procedural", "怎么在MySQL中创建联合索引？", ["INDEX", "最左前缀", "联合", "列"])

# ===== EXPLORATORY (10) - 宽泛探索 =====
add("x-01", "exploratory", "关于Redis持久化机制的详细信息", ["AOF", "RDB", "fsync", "混合"])
add("x-02", "exploratory", "关于MySQL索引优化的相关知识", ["聚簇", "二级", "回表", "覆盖"])
add("x-03", "exploratory", "关于Kubernetes调度机制的介绍", ["过滤", "打分", "绑定", "节点"])
add("x-04", "exploratory", "关于Python垃圾回收机制的信息", ["引用计数", "分代", "标记清除", "循环"])
add("x-05", "exploratory", "关于Docker网络模式的信息", ["bridge", "host", "none", "端口"])
add("x-06", "exploratory", "关于Go语言并发模型的介绍", ["goroutine", "channel", "GMP", "调度"])
add("x-07", "exploratory", "关于计算机网络负载均衡的知识", ["L4", "L7", "轮询", "算法"])
add("x-08", "exploratory", "关于Kafka消息顺序性保证的信息", ["分区", "有序", "Key", "跨分区"])
add("x-09", "exploratory", "关于Linux进程管理的信息", ["状态", "ps", "kill", "信号"])
add("x-10", "exploratory", "关于HTTPS和TLS加密的知识", ["证书", "握手", "对称", "非对称"])

# ===== OUT OF SCOPE (6) - 知识库中不存在的主题 =====
add("o-01", "out_of_scope", "量子计算的量子比特原理是什么？", [], refusal=True)
add("o-02", "out_of_scope", "区块链智能合约的Gas费用怎么计算？", [], refusal=True)
add("o-03", "out_of_scope", "React的Fiber架构原理是什么？", [], refusal=True)
add("o-04", "out_of_scope", "天文学中黑洞的事件视界是什么？", [], refusal=True)
add("o-05", "out_of_scope", "经济学中的菲利普斯曲线是什么？", [], refusal=True)
add("o-06", "out_of_scope", "生物学中CRISPR基因编辑的原理是什么？", [], refusal=True)

def main():
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        for case in CASES:
            f.write(json.dumps(case, ensure_ascii=False) + '\n')

    # 统计
    intents = {}
    for c in CASES:
        intents[c["intent"]] = intents.get(c["intent"], 0) + 1

    print(f"Generated {len(CASES)} test cases in {OUTPUT}")
    for intent, count in sorted(intents.items()):
        print(f"  {intent}: {count} cases")

if __name__ == "__main__":
    main()
