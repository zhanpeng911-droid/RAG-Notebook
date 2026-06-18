from fastapi import HTTPException
from fastapi.routing import APIRouter
from fastapi.responses import JSONResponse
import asyncio
import os

from app.core.success_response import success_response
from app.db.db_config import check_mysql_connection
from app.db.redis_config import check_redis_connection

health_router = APIRouter(prefix="/health")


async def _safe_check_redis(timeout: float = 1.0) -> bool:
    """带超时的 Redis 检查，防止卡住"""
    try:
        return await asyncio.wait_for(check_redis_connection(), timeout=timeout)
    except asyncio.TimeoutError:
        return False


@health_router.get("/live", tags=["健康检查"], summary="存活检查")
async def get_health_application_status():
    """健康检查-存活：服务进程是否在运行"""
    return success_response(
        message="health application status",
        data={
            "status": "ok"
        }
    )


@health_router.get("/ready", tags=["健康检查"], summary="就绪检查")
async def get_health_readiness():
    """健康检查-就绪：MySQL + Redis 是否可用（Redis 1秒超时）"""
    mysql_status = await check_mysql_connection()
    redis_status = await _safe_check_redis(timeout=1.0)
    if mysql_status and redis_status:
        return success_response(
            message="health readiness status",
            data={
                "status": "ok",
                "mysql": "ok",
                "redis": "ok"
            }
        )
    else:
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "message": "health readiness degraded",
                "data": {
                    "status": "degraded",
                    "mysql": "ok" if mysql_status else "unavailable",
                    "redis": "ok" if redis_status else "unavailable"
                }
            },
        )


@health_router.get("/db", tags=["健康检查"], summary="数据库检查")
async def get_health_mysql():
    """健康检查-MySQL：数据库连接是否正常"""
    status = await check_mysql_connection()
    if status:
        return success_response(
            message="MySQL connection OK",
            data={"status": "ok", "component": "mysql"}
        )
    else:
        raise HTTPException(status_code=503, detail="MySQL connection failed")


@health_router.get("/redis", tags=["健康检查"], summary="Redis检查")
async def get_health_redis():
    """健康检查-Redis：缓存服务是否正常（1秒超时）"""
    status = await _safe_check_redis(timeout=1.0)
    if status:
        return success_response(
            message="Redis connection OK",
            data={"status": "ok", "component": "redis"}
        )
    else:
        raise HTTPException(status_code=503, detail="Redis connection failed")


@health_router.get("/vector-store", tags=["健康检查"], summary="向量库检查")
async def get_health_vector_store():
    """健康检查-向量库：ChromaDB 是否可访问"""
    try:
        from app.rag.vector_store import VectorStoreService
        store = VectorStoreService()
        collection = store.vectors_store._collection
        count = collection.count()
        return success_response(
            message="Vector store OK",
            data={"status": "ok", "component": "chroma", "document_count": count}
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Vector store check failed: {str(e)}")


@health_router.get("/model", tags=["健康检查"], summary="模型检查")
async def get_health_model():
    """健康检查-模型：Embedding 服务是否可调用"""
    try:
        from app.utils.factory import embed_model
        result = embed_model.embed_query("health check")
        if result and len(result) > 0:
            return success_response(
                message="Embedding model OK",
                data={"status": "ok", "component": "embedding_model", "dimensions": len(result)}
            )
        else:
            raise HTTPException(status_code=503, detail="Embedding model returned empty result")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Model check failed: {str(e)}")


@health_router.get("/chat-model", tags=["健康检查"], summary="Chat模型检查")
async def get_health_chat_model():
    """健康检查-Chat模型：LLM 服务是否可调用"""
    try:
        from app.utils.factory import chat_model
        from langchain_core.messages import HumanMessage
        # 用简单文本测试 chat 模型是否可用
        result = chat_model.invoke([HumanMessage(content="hi")])
        if result and result.content:
            return success_response(
                message="Chat model OK",
                data={
                    "status": "ok",
                    "component": "chat_model",
                    "llm_type": os.getenv("LLM_TYPE", "UNKNOWN"),
                    "model": os.getenv("CHAT_MODEL_NAME", "UNKNOWN"),
                }
            )
        else:
            raise HTTPException(status_code=503, detail="Chat model returned empty result")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Chat model check failed: {str(e)}")

