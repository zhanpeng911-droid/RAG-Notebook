# 整改验收矩阵（REMEDIATION_ACCEPTANCE_MATRIX）

日期：2026-09-08。基线 HEAD `078549c` + 本轮统一方案 A 批工作区改动。
状态：通过 / 失败 / 未验证 / 不适用。层级：组件（内存库/假向量）/ 真实（MySQL/Chroma/进程/Compose）。

## A 批：恢复、接管、运维审计与生命周期收口

| 需求 | 入口 | 测试 node | 窗口 | 硬断言 | 层级 | 结果 | 产物 |
|---|---|---|---|---|---|---|---|
| A-01 恢复链 | execute_index_document→reconcile→重试 | test_closure_rework.py::test_n1_01_reconcile_recovers_stuck_indexing | 无 | waiting→resolve→recovered→新代次 g2 仅领取一次 indexed | 组件 | 通过 | 全量 1096/9 |
| A-02 commit 确认丢失 | reconcile / publish commit 注入 | test_closure_rework.py::test_n1_02b_reconcile_commit_confirm_lost_idempotent | commit 后抛错 | 结构化事实重建 recovered；不误报 conflict | 组件 | 通过 | 同上 |
| A-03 删除/新代次交错 | reconcile | test_n1_03_reconcile_respects_delete_and_new_generation | 调用前已变化 | conflict/gone 不覆盖 | 组件 | 通过（读取-更新窗口屏障留 E） | 同上 |
| A-04 双恢复者/ABA | reconcile 两次 | test_n1_04_two_reconcilers_single_progress | 顺序条件更新 | 幂等重放 recovered，不二次分配代次 | 组件 | 通过（真实并发留 E） | 同上 |
| A-05 接管资格 | save_uploaded_file 同名/MD5 接管 | test_n1_06b_needs_reupload_takeover_loop | 缺文件+closed | 接管收敛 indexed；新加 open-execution 防护 | 组件 | 通过 | 同上 |
| A-06 恢复身份/文案非凭据 | reconcile 结构化字段 | 迁移 014 recovery_outcome/recovered_at；replay/confirm 读结构化 | 重放 | 文案不再作凭据 | 组件 | 通过 | 迁移 014 |
| A-07 HTTP 运维链路 | resolve/reconcile 端点 | test_n1_07_ops_endpoint_full_chain / test_n1_07b_resolve_writing_rejected | HTTP | dry-run 零写；resolve unknown→closed；writing 拒 409 | 组件 | 通过 | 同上 |
| A-08 接管 commit 确认丢失 | takeover 后 execute | 继承 n1_06b；commit 注入由 n1_02b 模式覆盖 | — | 不误删有效文件 | 组件 | 部分（文件-SQL 非原子留 E） | 同上 |
| A-09 v1/SSE 取消 | knowledge_service | test_t20_v1_cancel / test_v1_publication_recovery（4 红转绿） | 取消传播 | 不伪 closed/completed | 组件 | 通过 | 同上 |
| A-10 清理租约 | cleanup worker | test_t18_crash_recovery / waiting_writer 不计预算 | 租约过期 | 旧持有者不能收尾 | 组件 | 通过 | 同上 |

## A 批确认的结构化修正（本轮新增）

- 迁移 014：`document_index_executions.recovery_outcome / recovered_at`（已应用真实 MySQL head=014）。
- `close_execution`：status=unknown 时 `closed_at` 置空（确定关闭才记录停止时间）；writer 持令牌 writing/unknown→closed 仍允许。
- reconcile：恢复动作与结构化执行恢复事实同事务写入；重放/确认读取结构化字段（不再依赖 error_message 子串）；幂等重放返回 recovered。
- 接管防护（A-05）：index_failed+缺文件行若存在 writing/unknown 未关闭执行 → 409 拒绝自动接管，须先经受控核验。

## 未验证/后续批次（不宣称完成）

- B：读取统一（可见性服务+全入口矩阵）——未实施。
- C：存量映射与回填工具——未实施。
- D：前端与联合验收——未实施。
- E：真实 MySQL 双连接/Chroma/进程/Compose——未实施（A-03/A-04 的真实并发窗口、A-08 文件-SQL 非原子、迁移真实库 head 均属 E）。
- F：最终门禁/CI 证据——远程 CI 需授权推送。
