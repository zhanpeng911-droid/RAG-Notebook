"""健康检查路由。

- `/live`、`/ready`：无认证、最小响应；`/ready` 对 MySQL+Redis 整体设超时，
  内部重试不会拖过探针时限。
- `/db`、`/redis`：单项组件检查（仍轻量）。
- `/model`、`/vector-store`：**深度检查**，会触发外部 embedding 调用或访问
  Chroma 集合 —— 必须由运维身份（RUNTIME_CONFIG_ADMIN_USER_IDS）调用，
  带短缓存、并发合并与限流；对外只返回组件状态，不返回原始异常文本。
  Docker readiness 只调 `/live`、`/ready`，不会触发收费模型。
"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.config.validator import get_settings
from app.core.rate_limit import rate_limit
from app.core.success_response import success_response
from app.db.db_config import check_mysql_connection
from app.db.redis_config import check_redis_connection
from app.utils.auth_utils import get_current_user_id

health_router = APIRouter(prefix="/health")

# 深度检查缓存：短 TTL + 并发合并（同时只跑一次真实检查）
_DEEP_CHECK_TTL = 30
_model_cache = {"value": None, "at": 0.0}
_vector_cache = {"value": None, "at": 0.0}
_model_lock = asyncio.Lock()
_vector_lock = asyncio.Lock()


def _is_ops_user(user_id: str) -> bool:
    admin_ids = {
        v.strip()
        for v in get_settings().RUNTIME_CONFIG_ADMIN_USER_IDS.split(",")
        if v.strip()
    }
    return user_id in admin_ids


def _require_ops(user_id: str) -> None:
    if not _is_ops_user(user_id):
        raise HTTPException(status_code=403, detail="无权限访问深度健康检查")


async def _safe_check_redis(timeout: float = 1.0) -> bool:
    """带超时的 Redis 检查，防止卡住"""
    try:
        return await asyncio.wait_for(check_redis_connection(), timeout=timeout)
    except asyncio.TimeoutError:
        return False


async def _check_readiness():
    """并行检查 MySQL + Redis，供整体超时包裹。"""
    mysql, redis_ok = await asyncio.gather(
        check_mysql_connection(),
        _safe_check_redis(timeout=1.0),
    )
    return mysql, redis_ok


@health_router.get("/live", tags=["健康检查"], summary="存活检查")
async def get_health_application_status():
    """健康检查-存活：服务进程是否在运行（无外部依赖，永远 200）"""
    return success_response(
        message="health application status",
        data={"status": "ok"},
    )


@health_router.get("/ready", tags=["健康检查"], summary="就绪检查")
async def get_health_readiness():
    """健康检查-就绪：MySQL + Redis 是否可用；整体 4 秒超时，内部重试不拖过探针时限"""
    try:
        mysql_status, redis_status = await asyncio.wait_for(
            _check_readiness(), timeout=4.0
        )
    except asyncio.TimeoutError:
        mysql_status = redis_status = False

    if mysql_status and redis_status:
        return success_response(
            message="health readiness status",
            data={"status": "ok", "mysql": "ok", "redis": "ok"},
        )
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "message": "health readiness degraded",
            "data": {
                "status": "degraded",
                "mysql": "ok" if mysql_status else "unavailable",
                "redis": "ok" if redis_status else "unavailable",
            },
        },
    )


@health_router.get("/db", tags=["健康检查"], summary="数据库检查")
async def get_health_mysql():
    """健康检查-MySQL：数据库连接是否正常"""
    status = await check_mysql_connection()
    if status:
        return success_response(
            message="MySQL connection OK",
            data={"status": "ok", "component": "mysql"},
        )
    raise HTTPException(status_code=503, detail="MySQL connection failed")


@health_router.get("/redis", tags=["健康检查"], summary="Redis检查")
async def get_health_redis():
    """健康检查-Redis：缓存服务是否正常（1秒超时）"""
    status = await _safe_check_redis(timeout=1.0)
    if status:
        return success_response(
            message="Redis connection OK",
            data={"status": "ok", "component": "redis"},
        )
    raise HTTPException(status_code=503, detail="Redis connection failed")


# ==================== 深度检查（运维身份 + 缓存 + 限流） ====================

async def _cached_deep_check(cache, lock, do_check) -> dict:
    """短 TTL 缓存 + 并发合并：同一时刻只执行一次真实检查。"""
    now = time.time()
    if cache["value"] is not None and now - cache["at"] < _DEEP_CHECK_TTL:
        return cache["value"]
    async with lock:
        if cache["value"] is not None and time.time() - cache["at"] < _DEEP_CHECK_TTL:
            return cache["value"]
        result = await do_check()
        cache["value"] = result
        cache["at"] = time.time()
        return result


async def _do_model_check() -> dict:
    """真实 embedding 调用，带 provider 侧超时；失败只返回稳定标识。"""
    try:
        from app.utils.factory import embed_model

        result = await asyncio.wait_for(
            asyncio.to_thread(embed_model.embed_query, "health check"), timeout=5.0
        )
        if result and len(result) > 0:
            return {"status": "ok", "component": "embedding_model", "dimensions": len(result)}
        return {"status": "degraded", "component": "embedding_model"}
    except Exception:
        # 原始 provider 异常只进日志，不返回给客户端
        return {"status": "degraded", "component": "embedding_model"}


async def _do_vector_check() -> dict:
    """真实 Chroma 访问，带超时；失败只返回稳定标识。"""
    try:
        from app.rag.vector_store import VectorStoreService

        if VectorStoreService.is_degraded():
            return {"status": "degraded", "component": "chroma"}

        store = VectorStoreService()

        count = await asyncio.wait_for(
            asyncio.to_thread(store.vectors_store._collection.count), timeout=5.0
        )
        return {"status": "ok", "component": "chroma", "document_count": count}
    except Exception:
        return {"status": "degraded", "component": "chroma"}


def _deep_response(result: dict):
    ok = result.get("status") == "ok"
    status_code = 200 if ok else 503
    return JSONResponse(
        status_code=status_code,
        content={
            "success": ok,
            "message": "deep health check",
            "data": result,
        },
    )


@health_router.get("/vector-store", tags=["健康检查"], summary="向量库检查（运维）")
async def get_health_vector_store(
    user_id: str = Depends(get_current_user_id),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """深度检查-向量库：需运维身份；短缓存 + 并发合并；异常不泄露原始信息。"""
    _require_ops(user_id)
    result = await _cached_deep_check(_vector_cache, _vector_lock, _do_vector_check)
    return _deep_response(result)


@health_router.get("/model", tags=["健康检查"], summary="模型检查（运维）")
async def get_health_model(
    user_id: str = Depends(get_current_user_id),
    _: None = Depends(rate_limit(limit=10, window=60)),
):
    """深度检查-模型：需运维身份；短缓存 + 并发合并；匿名不能触发收费调用。"""
    _require_ops(user_id)
    result = await _cached_deep_check(_model_cache, _model_lock, _do_model_check)
    return _deep_response(result)
