"""
Agent 路由 —— 提供 Agentic RAG 的 API 接口。

接口列表：
- POST /chat/agent/query/stream  —— Agent 流式查询
- POST /chat/agent/query         —— Agent 非流式查询
- GET  /chat/agent/runs/{run_id} —— 查询 Agent 运行记录
- POST /chat/agent/feedback      —— 提交答案反馈
"""
import asyncio
import uuid
import time
from typing import Optional

from fastapi.routing import APIRouter
from fastapi import Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.auth_utils import get_current_user_id
from app.db.db_config import get_db
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.core.logger_handler import logger


def _redact_llm_config(config: Optional[dict]) -> Optional[dict]:
    """Remove secrets before writing request configuration to logs or MySQL."""
    if not isinstance(config, dict):
        return None
    redacted = dict(config)
    if "api_key" in redacted:
        redacted["api_key"] = "[REDACTED]"
    return redacted


def _ensure_session_service_ready() -> None:
    """S01：会话服务未就绪时拒绝进入处理（503）。

    不得在调用 provider 之后才发现会话管理器缺失（历史写入会静默丢失）。
    """
    from app.services import get_session_manager
    try:
        get_session_manager()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="会话服务未就绪，请稍后重试")


agent_router = APIRouter(prefix="/chat/agent", tags=["agent"])


class AgentQueryRequest(BaseModel):
    """Agent 查询请求"""
    query: str
    session_id: Optional[str] = None
    space_id: Optional[str] = None
    llm_config: Optional[dict] = None


class AgentFeedbackRequest(BaseModel):
    """Agent 答案反馈请求"""
    run_id: str
    rating: int  # 1-5
    comment: Optional[str] = None


@agent_router.post("/query/stream")
async def agent_query_stream(
        request: AgentQueryRequest,
        user_id: str = Depends(get_current_user_id),
        _: None = Depends(rate_limit(limit=10, window=60, fail_closed=True))
):
    """
    Agent 流式查询 —— 以 SSE 事件流的形式返回 Agent 执行过程和答案。

    SSE 事件类型：
    - started: Agent 开始执行
    - planning: 正在规划检索策略
    - retrieving: 正在检索
    - retrieval_completed: 检索完成
    - grading_evidence: 正在评估证据
    - rewriting_query: 正在改写查询
    - generating_answer: 正在生成答案
    - citation: 引用信息
    - completed: 执行完成
    - error: 执行出错
    """
    from app.agentic.graph import run_agent_stream
    from app.repositories.agent_run_repository import AgentRunRepository
    from app.db.db_config import AsyncSessionLocal
    from app.models.chat_history import ChatSession
    from sqlalchemy import select

    _ensure_session_service_ready()

    logger.info(
        f"【Agent流式】user_id={user_id}, query={request.query[:50]}, "
        f"llm_config={_redact_llm_config(request.llm_config)}"
    )

    # 在返回 StreamingResponse 前完成会话和 run 创建，释放初始化会话。
    # S05：会话校验（含 tombstone 拒绝）与 run 创建在同一短事务内，
    # 且对父会话行加锁，与删除方（clear_session）持同一把锁。
    session_id = request.session_id or str(uuid.uuid4())
    async with AsyncSessionLocal() as setup_db:
        if request.session_id:
            existing = (await setup_db.execute(
                select(ChatSession)
                .where(ChatSession.id == request.session_id)
                .with_for_update()
            )).scalar_one_or_none()
            if existing is None:
                raise HTTPException(status_code=404, detail="会话不存在")
            if existing.user_id != user_id:
                raise HTTPException(status_code=403, detail="当前会话不属于你")
            if existing.deleted_at is not None:
                raise HTTPException(status_code=404, detail="会话不存在或已删除")
        else:
            setup_db.add(ChatSession(id=session_id, user_id=user_id, title=request.query[:30]))
            await setup_db.flush()

        repo = AgentRunRepository(setup_db)
        run = await repo.create_run(
            user_id=user_id,
            query=request.query,
            session_id=session_id,
            space_id=request.space_id,
            model_config=_redact_llm_config(request.llm_config),
        )
        await setup_db.commit()

    async def event_generator():
        import json
        start_time = time.time()

        try:
            async for event in run_agent_stream(
                query=request.query,
                user_id=user_id,
                space_id=request.space_id,
                session_id=session_id,
                llm_config=request.llm_config,
            ):
                # 更新运行记录（终态一律条件写，避免迟到事件覆盖已完成结果）
                event_type = event.get("type")
                if event_type == "completed":
                    total_ms = int((time.time() - start_time) * 1000)
                    # S05：终态提交 + 历史追加在同一事务（会话行锁保护）。
                    # 条件写被拒或事务失败时不发 completed，不返回成功快照。
                    try:
                        from app.services.agent_completion_service import finalize_agent_run
                        outcome = await finalize_agent_run(
                            run.id,
                            user_id,
                            status="completed",
                            answer=event.get("answer"),
                            total_time_ms=total_ms,
                            citation_count=len(event.get("citations", [])),
                            session_id=session_id,
                            user_message=request.query,
                            assistant_message=event.get("answer", ""),
                        )
                    except Exception as fin_err:
                        logger.error(f"【Agent】completed 终态提交失败: {fin_err}")
                        event = {"type": "error", "error": "结果落库失败，请重试"}
                    else:
                        if not outcome["run_updated"]:
                            logger.warning(
                                f"【Agent】终态已被其它结果占用，completed 不再发出: run={run.id}"
                            )
                            event = {"type": "error", "error": "运行结果已被其它终态占用"}
                elif event_type == "error":
                    total_ms = int((time.time() - start_time) * 1000)
                    async with AsyncSessionLocal() as event_db:
                        event_repo = AgentRunRepository(event_db)
                        await event_repo.update_run_terminal(
                            run.id,
                            "failed",
                            error_message=event.get("error"),
                            total_time_ms=total_ms,
                        )
                        await event_db.commit()
                elif event_type == "timeout":
                    # 结构化超时：正确地落库为 timeout，不再记为 completed
                    total_ms = int((time.time() - start_time) * 1000)
                    async with AsyncSessionLocal() as event_db:
                        event_repo = AgentRunRepository(event_db)
                        await event_repo.update_run_terminal(
                            run.id,
                            "timeout",
                            error_message=event.get("error", "处理超时"),
                            total_time_ms=total_ms,
                        )
                        await event_db.commit()

                # 添加 run_id 和 session_id 到事件
                event["run_id"] = run.id
                event["session_id"] = session_id
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开：短暂、受限地保存终态（cancelled）后传播取消
            total_ms = int((time.time() - start_time) * 1000)
            try:
                async with AsyncSessionLocal() as cancel_db:
                    await AgentRunRepository(cancel_db).update_run_terminal(
                        run.id,
                        "cancelled",
                        error_message="客户端取消",
                        total_time_ms=total_ms,
                    )
                    await cancel_db.commit()
            except Exception as cancel_err:
                logger.warning(f"【Agent】取消后写入终态失败: {cancel_err}")
            raise

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@agent_router.post("/query")
async def agent_query(
        request: AgentQueryRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60, fail_closed=True))
):
    """
    Agent 非流式查询 —— 等待 Agent 完成后返回完整结果。
    """
    from app.agentic.graph import run_agent
    from app.repositories.agent_run_repository import AgentRunRepository
    from app.models.chat_history import ChatSession
    from sqlalchemy import select

    _ensure_session_service_ready()

    session_id = request.session_id or str(uuid.uuid4())
    if request.session_id:
        # S05：tombstone 会话拒绝 + 父会话行锁（与删除方同一屏障）
        existing = (await db.execute(
            select(ChatSession)
            .where(ChatSession.id == request.session_id)
            .with_for_update()
        )).scalar_one_or_none()
        if existing is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if existing.user_id != user_id:
            raise HTTPException(status_code=403, detail="当前会话不属于你")
        if existing.deleted_at is not None:
            raise HTTPException(status_code=404, detail="会话不存在或已删除")
    else:
        db.add(ChatSession(id=session_id, user_id=user_id, title=request.query[:30]))
        await db.flush()

    repo = AgentRunRepository(db)
    run = await repo.create_run(
        user_id=user_id,
        query=request.query,
        session_id=session_id,
        space_id=request.space_id,
        model_config=_redact_llm_config(request.llm_config),
    )
    await db.commit()

    start_time = time.time()
    result = await run_agent(
        query=request.query,
        user_id=user_id,
        space_id=request.space_id,
        session_id=session_id,
        llm_config=request.llm_config,
    )

    total_ms = int((time.time() - start_time) * 1000)
    logger.info(f"【Agent非流式】result answer preview: {str(result.get('answer',''))[:100]}")
    # 结构化超时正确地落库为 timeout，不计为 completed
    if result.get("timed_out"):
        final_status = "timeout"
    elif result.get("error"):
        final_status = "failed"
    else:
        final_status = "completed"

    # S05：终态提交 + 历史追加在同一事务（会话行锁保护）。
    # 提交失败或条件写被拒时不返回成功快照。
    run_updated = False
    try:
        from app.services.agent_completion_service import finalize_agent_run
        outcome = await finalize_agent_run(
            run.id,
            user_id,
            status=final_status,
            answer=result.get("answer"),
            error_message=result.get("error"),
            total_time_ms=total_ms,
            citation_count=len(result.get("citations", [])),
            session_id=session_id if final_status == "completed" else None,
            user_message=request.query,
            assistant_message=result.get("answer", ""),
        )
        run_updated = outcome["run_updated"]
    except Exception as fin_err:
        logger.error(f"【Agent非流式】终态提交失败: {fin_err}")

    if not run_updated and final_status == "completed":
        result["error"] = "运行结果落库失败，请重试"
        final_status = "failed"

    result["run_id"] = run.id
    result["session_id"] = session_id
    return success_response(data=result)


@agent_router.get("/runs/{run_id}")
async def get_agent_run(
        run_id: str,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=20, window=60))
):
    """获取 Agent 运行记录"""
    from app.repositories.agent_run_repository import AgentRunRepository

    repo = AgentRunRepository(db)
    run = await repo.get_run(run_id, user_id)

    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    steps = await repo.get_run_steps(run_id)

    return success_response(data={
        "run": {
            "id": run.id,
            "query": run.query,
            "query_type": run.query_type,
            "answer": run.answer,
            "status": run.status,
            "error_message": run.error_message,
            "retrieval_rounds": run.retrieval_rounds,
            "evidence_count": run.evidence_count,
            "citation_count": run.citation_count,
            "total_time_ms": run.total_time_ms,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
        "steps": [
            {
                "phase": step.phase,
                "step_data": step.step_data,
                "duration_ms": step.duration_ms,
                "created_at": step.created_at.isoformat() if step.created_at else None,
            }
            for step in steps
        ],
    })


@agent_router.get("/runs")
async def list_agent_runs(
        session_id: Optional[str] = Query(None, description="按会话ID筛选"),
        limit: int = Query(20, description="返回数量"),
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=20, window=60))
):
    """获取用户的 Agent 运行记录列表"""
    from app.repositories.agent_run_repository import AgentRunRepository

    repo = AgentRunRepository(db)
    runs = await repo.get_user_runs(user_id, session_id=session_id, limit=limit)

    return success_response(data={
        "runs": [
            {
                "id": run.id,
                "query": run.query,
                "status": run.status,
                "total_time_ms": run.total_time_ms,
                "created_at": run.created_at.isoformat() if run.created_at else None,
            }
            for run in runs
        ],
        "total_count": len(runs),
    })


@agent_router.post("/feedback")
async def submit_feedback(
        request: AgentFeedbackRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=20, window=60))
):
    """提交答案反馈"""
    from app.repositories.agent_run_repository import AgentRunRepository

    repo = AgentRunRepository(db)

    # 验证运行记录存在
    run = await repo.get_run(request.run_id, user_id)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    # 验证评分范围
    if request.rating < 1 or request.rating > 5:
        raise HTTPException(status_code=400, detail="评分必须在 1-5 之间")

    feedback = await repo.add_feedback(
        run_id=request.run_id,
        user_id=user_id,
        rating=request.rating,
        comment=request.comment,
    )
    await db.commit()

    return success_response(data={
        "feedback_id": feedback.id,
        "run_id": request.run_id,
        "rating": request.rating,
        "message": "感谢您的反馈！",
    })
