# Notebook 性能压测方案（QPS 基线 · R1）

> 生成：2026-08-28。
> 决策记录：① 发压一步到位（docker compose 全栈 + 宿主机 locust）；② 真实 LLM 层（L4）执行压测；③ 写路径压完清理。
> 执行规范：沿用项目惯例——每任务独立提交（`test:`/`chore:`/`docs:` 前缀），不动业务代码，发现的问题只登记。
> 状态：**方案待确认，未开始执行**。

---

## 一、目标与非目标

**目标**：测出各层真实 QPS 与延迟分布（P50/P95/P99），定位当前瓶颈（单进程 vs MySQL vs ChromaDB vs LLM），产出 `docs/performance-report.md` 供容量规划与优化排序。

**非目标**：不做高可用/故障注入；不承诺生产级绝对值（单机环境的数字只对层间相对差值与拐点下结论）；不升级 uvicorn / 不改启动方式（只登记建议）。

## 二、起点快照（已核实）

| 事实 | 对方案的影响 |
|---|---|
| Docker CMD `uvicorn main:app`（无 `--workers`） | 单进程；worker 扫描实验是本方案第一优先实验 |
| uvicorn `==0.21.1` 钉死 | 版本老，登记附带发现，本轮不升级 |
| compose 全栈：mysql / redis / backend / celery-worker / celery-beat / django / frontend | 一步到位可行；压测期间停 celery 两服务防噪声 |
| backend 端口映射 `8000:8000` | 宿主机 locust 直打 `http://localhost:8000` |
| `.env.docker`：`RATE_LIMIT_ENABLED=true`、JWT 黑名单走 Redis | loadtest compose 覆盖为 false（限流不关会压出假 429） |
| LLM = DeepSeek（`OPENAI_API_BASE=https://api.deepseek.com`） | L3 用本地 mock LLM 容器剥离外部依赖；L4 才打真实 API |
| MySQL 连接池 `pool_size=10 + max_overflow=20` / worker | worker 扫描到 4 时峰值 120 连接，逼近 MySQL 默认 `max_connections=151`，见风险 R3 |

## 三、拓扑（一步到位）

```
宿主机 locust ──► http://localhost:8000 ──► [backend 容器 (uvicorn N worker)]
                                                ├─► mysql 容器
                                                ├─► redis 容器（限流/黑名单已关，仅健康检查触达）
                                                ├─► (L3) mock-llm 容器（OpenAI 协议假服务）
                                                └─► (L4) api.deepseek.com（真实）
django / frontend 容器照常起（不作为压测对象）
```

## 四、工具与脚本（`backend/loadtests/`，全部新增，不碰业务代码）

| 文件 | 职责 |
|---|---|
| `locustfile.py` | 分层场景：按 tag 划分 L0~L5，每层独立 user class；自动携带真实 JWT（HS256 按 Django 契约签发） |
| `mock_llm.py` | OpenAI 协议假服务（FastAPI，`POST /v1/chat/completions` 返回固定 completion，支持 stream 格式）——L3 用它剥离 LLM 等待 |
| `seed_and_cleanup.py` | 种子：1 压测用户 × 500 笔记；清理：按记录的 note id 删笔记 + ReviewRecord（幂等，可重复跑） |
| `docker-compose.loadtest.yml` | compose 覆盖文件：backend 注入四个关闭开关 + `OPENAI_API_BASE=http://mock-llm:9999/v1`（L3 轮）；附 worker 扫描的 command 覆盖示例 |
| `README.md` | runbook：逐条命令（启动/分层压测/清理/收尾） |

工具：`locust` 入 backend dev extras（`uv add --dev locust`）。

## 五、分层场景

| 层 | 端点 | 依赖 | 并发梯度 | 回答的问题 |
|---|---|---|---|---|
| L0 框架基线 | `GET /api/v1/health/live` | 无 | 1→50→200→500 | FastAPI+uvicorn 单进程物理上限 |
| L1 读路径 | `GET /api/v1/note/list` | JWT + MySQL | 同上 | 与 L0 差值 = ORM/MySQL 读开销 |
| L2 写路径 | `POST /api/v1/note/create` | MySQL 写（同事务 ReviewRecord） | 1→20→100 | 写吞吐；压完清理 |
| L3 编排层 | `POST /chat/agent/query`（mock LLM） | MySQL + ChromaDB | 1→10→50 | 除去 LLM 后服务自身吞吐 |
| L4 全链路 | `POST /chat/agent/query`（真实 DeepSeek） | + 外网 | 固定 5/10 两档，限时 3 分钟 | 用户体验真相；预期个位数 QPS |
| L5 SSE | `POST /chat/agent/query/stream` | 长连接 | 50/100/200 连接保持 | 并发连接数、首字延迟（**指标不是 QPS**） |

每层 3 轮取中位数；梯度爬坡到 QPS 不再上升（拐点）即停。

## 六、worker 扫描实验（第一优先）

compose 覆盖 backend command 为 `uvicorn main:app --workers N`，N ∈ {1, 2, 4}，各跑一轮 L0+L1。
判定：QPS 随 worker 近似线性 → 生产 Dockerfile 加 `--workers` 是零代码收益（登记为优化建议）；
不线性 → 进程模型/资源瓶颈，报告给出证据。每轮记录容器 CPU（`docker stats`）。

## 七、数据准备与清理（决策③：压完清理）

1. `seed_and_cleanup.py seed`：签发压测用户 token，建 500 笔记，**导出创建的 id 清单到 `loadtest_artifacts/seed_ids.json`**。
2. L2 压测期间新创建的笔记由 locust task 记录 id，跑完追加进清单。
3. `seed_and_cleanup.py cleanup`：按清单 DELETE 笔记与关联 ReviewRecord，删压测用户，输出清理前后 count 对比作为清理凭证。
4. ChromaDB 侧：向量索引开关已关，不会产生向量残留，无需清理。

## 八、指标与判定标准

- 每轮记录：QPS、P50/P95/P99、错误率、服务端容器 CPU/内存（`docker stats --no-stream` 采样）。
- 错误率 > 0.1% 的轮次作废重跑；判定瓶颈：L1 显著低于 L0 → DB；worker 不线性 → 进程模型；L4 ≈ 并发数/LLM延迟 → 外部依赖主导（预期，不算缺陷）。
- 服务端环境变量断言（脚本启动时自检，防止压出假数据）：`RATE_LIMIT_ENABLED=false`、`JWT_BLACKLIST_CHECK_ENABLED=false`、`NOTE_VECTOR_INDEX_ENABLED=false`、`NOTE_AUTO_TAG_ENABLED=false`。

## 九、L4 真实 LLM 预算与中止条件（决策②：压）

- 并发：仅 5 / 10 两档；每档限时 3 分钟或 150 请求（先到为准），总请求 ≤ 300。
- 预估调用量：300 次 agent 调用 × 每次 ~3–8k token ≈ 1–2.5M token，费用以 DeepSeek 实际计价为准（个位数人民币量级）。
- 中止条件：出现限速 4xx / 错误率 > 5% / 耗时 P99 > 60s → 立即停该档，记录现状即可。
- L4 期间 `mock-llm` 服务停用（`OPENAI_API_BASE` 指回真实地址）。

## 十、任务清单与验收标准

| 批次 | 内容 | 验收标准 |
|---|---|---|
| P0 | locust 入 dev extras；`backend/loadtests/` 五件套；compose 栈一键起停脚本 | `docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d` 后健康检查通过；seed/cleanup 各跑通一次 |
| P1 | L0/L1 基线 + worker 扫描（1/2/4） | 得到 3×2 组有效数据（错误率达标）+ 扫描结论 |
| P2 | L2 写路径 + 清理凭证 | 写吞吐数据 + `cleanup` 后 count 归零输出 |
| P3 | L3 mock 层 + L4 真实（按预算）+ L5 SSE | 三层各自结论（L4 允许"受外部限速"结论） |
| P4 | `docs/performance-report.md`：数据表 + 瓶颈归因 + 优化建议排序（worker 配置、uvicorn 版本、连接池等） | 报告入库；优化建议不直接实施，另行决策 |

预计耗时：P0 半天、P1 1–2 小时、P2 1 小时、P3 2–3 小时（含 L4 限时时长）、P4 半小时。

## 十一、风险与限制

| # | 风险 | 处置 |
|---|---|---|
| R1 | 宿主机发压与 Docker 抢资源，绝对值偏低 | 报告只对层间差值/拐点下结论，绝对值标注环境 |
| R2 | `.env.docker` 被误改 | 一律用 compose override 注入，不改原文件 |
| R3 | worker=4 时 MySQL 连接峰值 120，逼近 `max_connections=151` | 扫描前 `SHOW VARIABLES LIKE 'max_connections'` 确认；必要时该轮把 `pool_size` 降为 5（compose env 覆盖） |
| R4 | celery-beat 每 5 分钟的补偿任务干扰读数 | 压测期间 `docker compose stop celery-worker celery-beat` |
| R5 | ChromaDB 多进程（多 worker）写冲突 | 本轮无向量写入（开关已关），不受影响；登记为多 worker 上生产的注意项 |

## 十二、遗留问题登记

| # | 现象 | 处置 |
|---|---|---|
|  |  |  |
