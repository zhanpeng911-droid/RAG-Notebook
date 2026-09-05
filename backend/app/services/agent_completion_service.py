"""
Agent 运行终态共享提交服务（R4 / S05）。

同一事务内完成：条件写运行终态 → completed 时在父会话行锁保护下追加问答历史。
禁止"先提交 completed，再独立调用会话管理器并吞掉失败"——
迟到的写入、并发删除与终态覆盖全部由这一个事务边界保证：

- 运行终态条件写（update_run_terminal）：迟到的取消/超时不得覆盖已完成结果；
- 父会话 SELECT ... FOR UPDATE：与 clear_session（删除）持同一把行锁，
  删除先获胜则历史不落库，写入先获胜则删除在提交后才能拿到锁并清理。
"""
from sqlalchemy import select

from app.core.logger_handler import logger


async def finalize_agent_run(
    run_id: str,
    user_id: str,
    *,
    status: str,
    answer=None,
    error_message=None,
    total_time_ms=None,
    citation_count=None,
    session_id=None,
    user_message=None,
    assistant_message=None,
) -> dict:
    """提交 Agent 运行终态；completed 且会话活跃时在同一事务追加问答历史。

    返回 {"run_updated": bool, "messages_added": bool}：
    - run_updated=False：终态已被其它写者占用（条件写被拒），不重复追加历史；
    - messages_added=False：completed 但会话已删除/不属于该用户，历史不落库。
    """
    from app.db.db_config import AsyncSessionLocal
    from app.repositories.agent_run_repository import AgentRunRepository
    from app.models.chat_history import ChatSession, ChatMessage

    async with AsyncSessionLocal() as db:
        repo = AgentRunRepository(db)
        run_updated = await repo.update_run_terminal(
            run_id,
            status,
            answer=answer,
            error_message=error_message,
            total_time_ms=total_time_ms,
            citation_count=citation_count,
        )

        messages_added = False
        if run_updated and status == "completed" and session_id:
            # 父会话行锁：与删除方（clear_session）同一屏障（S05）
            sess = (await db.execute(
                select(ChatSession)
                .where(ChatSession.id == session_id)
                .with_for_update()
            )).scalar_one_or_none()
            if sess and sess.user_id == user_id and sess.deleted_at is None:
                db.add(ChatMessage(session_id=session_id, role="user",
                                   content=user_message or ""))
                db.add(ChatMessage(session_id=session_id, role="assistant",
                                   content=assistant_message or ""))
                messages_added = True

        await db.commit()

    if run_updated and status == "completed" and session_id and not messages_added:
        logger.warning(
            f"【Agent终态】会话已删除或不属于该用户，历史未落库: session={session_id}"
        )
    return {"run_updated": run_updated, "messages_added": messages_added}
