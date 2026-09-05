"""
Agent 运行心跳（C2）——独立于 SSE 事件的租约维护。

- 覆盖流式与非流式运行：运行开始即启动，按固定间隔更新 heartbeat_at；
  provider 长时间不产出 SSE 事件时心跳仍在，不会被回收任务误杀。
- 每次心跳使用独立短会话，不跨任务共享 AsyncSession；
  只更新仍处于 started 的运行（终态提交/取消后心跳自然无效）。
- 生命周期：start() 创建后台任务；stop() 取消并 await（在 finally 中调用）。
  进程崩溃即心跳停止，超过租约（AGENT_RUN_STALE_AFTER_MINUTES）后由
  回收任务收敛——租约期限与心跳周期独立配置，不等于运行总时限。
"""
import asyncio

from app.core.logger_handler import logger


class RunHeartbeat:
    """单个 Agent 运行的心跳任务句柄。"""

    def __init__(self, run_id: str, interval_seconds: float):
        self.run_id = run_id
        self.interval = max(1.0, float(interval_seconds))
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._beat_loop(), name=f"agent-heartbeat-{self.run_id}")

    async def _beat_loop(self) -> None:
        from app.db.db_config import AsyncSessionLocal
        from app.repositories.agent_run_repository import AgentRunRepository

        try:
            while True:
                await asyncio.sleep(self.interval)
                try:
                    # 独立短会话：一次心跳一个连接生命周期
                    async with AsyncSessionLocal() as db:
                        await AgentRunRepository(db).touch_run_heartbeat(self.run_id)
                        await db.commit()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # 心跳失败不中断运行；由回收租约兜底
                    logger.warning(f"【Agent心跳】更新失败（运行继续）: run={self.run_id}, error={e}")
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """取消心跳任务并等待退出（finally 中调用，防泄漏）。"""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
