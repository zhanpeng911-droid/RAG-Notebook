"""
Agent 路由 —— 提供 Agentic RAG 的 API 接口。

接口列表：
- POST /chat/agent/query/stream  —— Agent 流式查询
- POST /chat/agent/query         —— Agent 非流式查询
- GET  /chat/agent/runs/{run_id} —— 查询 Agent 运行记录
- POST /chat/agent/feedback      —— 提交答案反馈
"""
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
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60))
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

    logger.info(f"【Agent流式】user_id={user_id}, query={request.query[:50]}, llm_config={request.llm_config}")

    # 如果没有 session_id，自动创建会话
    session_id = request.session_id
    if not session_id:
        import uuid as _uuid
        from app.models.chat_history import ChatSession
        session_id = str(_uuid.uuid4())
        new_session = ChatSession(id=session_id, user_id=user_id, title=request.query[:30])
        db.add(new_session)
        await db.commit()

    repo = AgentRunRepository(db)
    run = await repo.create_run(
        user_id=user_id,
        query=request.query,
        session_id=session_id,
        space_id=request.space_id,
        model_config=request.llm_config,
    )
    await db.commit()

    async def event_generator():
        import json
        start_time = time.time()

        async for event in run_agent_stream(
            query=request.query,
            user_id=user_id,
            space_id=request.space_id,
            session_id=session_id,
            llm_config=request.llm_config,
        ):
            # 更新运行记录
            event_type = event.get("type")
            if event_type == "completed":
                total_ms = int((time.time() - start_time) * 1000)
                await repo.update_run(
                    run.id,
                    status="completed",
                    answer=event.get("answer"),
                    total_time_ms=total_ms,
                    citation_count=len(event.get("citations", [])),
                )
                # 把问答写入会话历史
                try:
                    from app.services import session_manager as sm
                    await sm.session_manager.add_message(session_id, user_id, request.query, event.get("answer", ""))
                except Exception as msg_err:
                    logger.warning(f"写入会话历史失败: {msg_err}")
                await db.commit()
            elif event_type == "error":
                total_ms = int((time.time() - start_time) * 1000)
                await repo.update_run(
                    run.id,
                    status="failed",
                    error_message=event.get("error"),
                    total_time_ms=total_ms,
                )
                await db.commit()

            # 添加 run_id 和 session_id 到事件
            event["run_id"] = run.id
            event["session_id"] = session_id
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        }
    )


@agent_router.post("/query")
async def agent_query(
        request: AgentQueryRequest,
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
        _: None = Depends(rate_limit(limit=10, window=60))
):
    """
    Agent 非流式查询 —— 等待 Agent 完成后返回完整结果。
    """
    from app.agentic.graph import run_agent
    from app.repositories.agent_run_repository import AgentRunRepository

    repo = AgentRunRepository(db)
    run = await repo.create_run(
        user_id=user_id,
        query=request.query,
        session_id=request.session_id,
        space_id=request.space_id,
        model_config=request.llm_config,
    )
    await db.commit()

    start_time = time.time()
    from app.config.validator import get_settings
    _s = get_settings()
    logger.info(f"【Agent非流式】OPENAI_API_KEY={str(_s.OPENAI_API_KEY)[:12]}... CHAT_MODEL={_s.CHAT_MODEL_NAME} LLM_TYPE={_s.LLM_TYPE}")
    result = await run_agent(
        query=request.query,
        user_id=user_id,
        space_id=request.space_id,
        session_id=request.session_id,
        llm_config=request.llm_config,
    )

    total_ms = int((time.time() - start_time) * 1000)
    logger.info(f"【Agent非流式】result answer preview: {str(result.get('answer',''))[:100]}")
    await repo.update_run(
        run.id,
        status="completed" if not result.get("error") else "failed",
        answer=result.get("answer"),
        error_message=result.get("error"),
        total_time_ms=total_ms,
        citation_count=len(result.get("citations", [])),
    )
    await db.commit()

    result["run_id"] = run.id
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
