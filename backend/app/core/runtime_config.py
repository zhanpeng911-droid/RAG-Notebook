"""
运行时配置 —— 检索参数热更新（无需重启服务）。

设计：
- 参数 schema 用 Pydantic 定义（key / 类型 / 默认值 / 范围 / 说明）
- 持久化：MySQL runtime_config 表（只存覆盖值，未覆盖的 key 用代码默认值）
- 读取：进程内存缓存，同步 get() 永不阻塞、永不抛错（DB 不可用回退默认值）
- 刷新：启动时加载 + set/reset 后立即刷新（单实例部署足够；
  多实例部署需自行加定期刷新或订阅失效广播）

明确不纳入运行时配置的参数：
- chunk_size / chunk_overlap / separators（chroma.yaml）——索引期参数，
  对已索引文档无效，必须重新索引才生效，放进来会误导运维。
"""
import json
import threading
from typing import Any, Optional

from pydantic import BaseModel

from app.core.logger_handler import logger


class ParamDef(BaseModel):
    """单个运行时参数定义。"""
    key: str
    value_type: str  # "int" | "float" | "bool"
    default: Any
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    description: str


# ==================== 参数注册表（第一批：检索期参数） ====================

PARAM_DEFS: dict[str, ParamDef] = {
    p.key: p
    for p in [
        ParamDef(
            key="retrieval.top_k_baseline",
            value_type="int", default=5, min_value=3, max_value=15,
            description="检索 top_k 基准值（SIMPLE/FACTUAL 类查询；其他类型在此基础上按策略偏移）",
        ),
        ParamDef(
            key="retrieval.chroma_k",
            value_type="int", default=6, min_value=3, max_value=20,
            description="向量检索召回数量（每路候选）",
        ),
        ParamDef(
            key="retrieval.rerank_candidate_multiplier",
            value_type="int", default=3, min_value=2, max_value=5,
            description="重排候选集倍数（候选 = top_k × 该值，重排后取 top_k）",
        ),
        ParamDef(
            key="retrieval.rerank_enabled",
            value_type="bool", default=True,
            description="是否启用 Cross-Encoder 重排序",
        ),
        ParamDef(
            key="grader.min_relevance",
            value_type="float", default=0.3, min_value=0.1, max_value=0.5,
            description="证据最低相关性分数阈值（低于此值的证据被视为不相关）",
        ),
        ParamDef(
            key="grader.confidence_high",
            value_type="float", default=0.7, min_value=0.5, max_value=0.95,
            description="置信度 high 分级阈值（≥ 此值为 high）",
        ),
        ParamDef(
            key="grader.confidence_medium",
            value_type="float", default=0.4, min_value=0.2, max_value=0.7,
            description="置信度 medium 分级阈值（≥ 此值为 medium）",
        ),
        ParamDef(
            key="grader.confidence_low",
            value_type="float", default=0.1, min_value=0.0, max_value=0.4,
            description="置信度 low 分级阈值（≥ 此值为 low，否则 none 触发 CRAG）",
        ),
    ]
}

# ==================== 内存缓存 ====================

# 只存 DB 中的覆盖值；get() 时回退到参数默认值
_overrides: dict[str, Any] = {}
_lock = threading.Lock()


def get(key: str) -> Any:
    """
    同步读取参数当前值（线程安全，永不阻塞/抛错）。

    优先返回 DB 覆盖值，否则返回默认值。未注册的 key 抛 KeyError（编码错误，应尽早暴露）。
    """
    with _lock:
        if key in _overrides:
            return _overrides[key]
    if key not in PARAM_DEFS:
        raise KeyError(f"未注册的运行时配置参数: {key}")
    return PARAM_DEFS[key].default


def get_all() -> list[dict]:
    """返回全部参数的完整视图（当前值 + 默认值 + 是否覆盖）。"""
    with _lock:
        overrides_snapshot = dict(_overrides)
    result = []
    for key, definition in PARAM_DEFS.items():
        overridden = key in overrides_snapshot
        result.append({
            "key": key,
            "value": overrides_snapshot.get(key, definition.default),
            "default": definition.default,
            "value_type": definition.value_type,
            "min_value": definition.min_value,
            "max_value": definition.max_value,
            "description": definition.description,
            "overridden": overridden,
        })
    return result


async def refresh_cache() -> None:
    """从 DB 重新加载覆盖值到内存缓存（DB 不可用时保持现有缓存不动）。"""
    try:
        from sqlalchemy import select

        from app.db.db_config import AsyncSessionLocal
        from app.models.runtime_config import RuntimeConfig

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(RuntimeConfig))
            rows = result.scalars().all()

        loaded = {}
        for row in rows:
            if row.key in PARAM_DEFS:
                try:
                    loaded[row.key] = json.loads(row.value)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"【运行时配置】{row.key} 的值无法解析，忽略: {row.value!r}")

        with _lock:
            _overrides.clear()
            _overrides.update(loaded)
        logger.info(f"【运行时配置】已加载 {len(loaded)} 个覆盖值")
    except Exception as e:
        # DB 不可用：保持现有缓存（首次启动时即全默认值），不阻断服务
        logger.warning(f"【运行时配置】加载失败，使用当前缓存/默认值: {e}")


def _validate_and_coerce(key: str, raw_value: Any, current_effective: dict) -> Any:
    """校验并转换单个参数值，非法时抛 ValueError。"""
    definition = PARAM_DEFS.get(key)
    if definition is None:
        raise ValueError(f"未知的运行时配置参数: {key}")

    value = raw_value
    if definition.value_type == "bool":
        if not isinstance(value, bool):
            # 兼容 JSON 里的 0/1
            if value in (0, 1):
                value = bool(value)
            else:
                raise ValueError(f"{key} 需要 bool 类型")
    elif definition.value_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} 需要 int 类型")
        if not (definition.min_value <= value <= definition.max_value):
            raise ValueError(f"{key} 超出范围 [{definition.min_value}, {definition.max_value}]: {value}")
    elif definition.value_type == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} 需要 float 类型")
        value = float(value)
        if not (definition.min_value <= value <= definition.max_value):
            raise ValueError(f"{key} 超出范围 [{definition.min_value}, {definition.max_value}]: {value}")

    # 组合校验：置信度阈值必须保持 high > medium > low
    conf_keys = ("grader.confidence_high", "grader.confidence_medium", "grader.confidence_low")
    if key in conf_keys:
        merged = {**current_effective, key: value}
        high, medium, low = (merged[k] for k in conf_keys)
        if not (high > medium > low):
            raise ValueError(
                f"置信度阈值必须满足 high > medium > low（当前: {high} / {medium} / {low}）"
            )

    return value


async def set_values(values: dict, updated_by: str = None) -> dict:
    """
    批量设置参数（校验 + 持久化 + 刷新缓存）。

    :param values: {key: value}
    :param updated_by: 操作者 user_id（审计用）
    :return: {"updated": [...], "values": {key: 生效值}}
    :raises ValueError: 校验失败（未知 key / 类型错误 / 越界 / 阈值耦合）
    """
    from sqlalchemy import select

    from app.db.db_config import AsyncSessionLocal
    from app.models.runtime_config import RuntimeConfig

    with _lock:
        current_effective = {k: _overrides.get(k, PARAM_DEFS[k].default) for k in PARAM_DEFS}

    # 先整体校验（任何一项失败都不落库）
    coerced = {}
    for key, raw in values.items():
        coerced[key] = _validate_and_coerce(key, raw, current_effective)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(RuntimeConfig))
        existing = {row.key: row for row in result.scalars().all()}

        for key, value in coerced.items():
            row = existing.get(key)
            encoded = json.dumps(value)
            if row is None:
                session.add(
                    RuntimeConfig(key=key, value=encoded, updated_by=updated_by)
                )
            else:
                row.value = encoded
                row.updated_by = updated_by

        await session.commit()

    await refresh_cache()

    return {
        "updated": sorted(coerced.keys()),
        "values": {k: get(k) for k in coerced},
    }


async def reset_values(keys: list[str], updated_by: str = None) -> dict:
    """
    重置参数为默认值（删除 DB 覆盖行 + 刷新缓存）。

    :param keys: 要重置的 key 列表；空列表或 ["*"] 表示全部重置
    :return: {"reset": [...], "values": {key: 默认值}}
    """
    from sqlalchemy import delete as sql_delete

    from app.db.db_config import AsyncSessionLocal
    from app.models.runtime_config import RuntimeConfig

    if not keys or keys == ["*"]:
        target_keys = list(PARAM_DEFS.keys())
    else:
        for key in keys:
            if key not in PARAM_DEFS:
                raise ValueError(f"未知的运行时配置参数: {key}")
        target_keys = list(keys)

    async with AsyncSessionLocal() as session:
        await session.execute(
            sql_delete(RuntimeConfig).where(RuntimeConfig.key.in_(target_keys))
        )
        await session.commit()

    await refresh_cache()

    return {
        "reset": sorted(target_keys),
        "values": {k: get(k) for k in target_keys},
    }
