"""
回顾服务层 —— 艾宾浩斯间隔重复算法 + 回顾问题生成。

艾宾浩斯遗忘曲线：
- 第 1 次回顾：1 天后
- 第 2 次回顾：2 天后
- 第 3 次回顾：4 天后
- 第 4 次回顾：7 天后
- 第 5 次回顾：15 天后
- 第 6 次及以后：30 天后

核心功能：
- 查询今日待回顾的笔记
- 标记笔记已回顾（自动计算下次回顾时间）
- 创建回顾记录
"""
from datetime import datetime, timedelta
from typing import List

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.note import Note
from app.models.review_record import ReviewRecord
from app.core.logger_handler import logger

# 艾宾浩斯间隔重复数组（天）
INTERVALS = [1, 2, 4, 7, 15, 30]


def get_next_interval(review_count: int) -> int:
    """
    根据回顾次数返回下一次回顾间隔天数。
    超出预定义数组后固定使用 30 天间隔。
    """
    if review_count < len(INTERVALS):
        return INTERVALS[review_count]
    return INTERVALS[-1]


class ReviewService:
    """
    回顾服务 —— 管理笔记的间隔重复回顾。

    艾宾浩斯遗忘曲线：
    - 第 1 次回顾：1 天后
    - 第 2 次回顾：2 天后
    - 第 3 次回顾：4 天后
    - 第 4 次回顾：7 天后
    - 第 5 次回顾：15 天后
    - 第 6 次及以后：30 天后
    """

    async def get_today_reviews(self, db: AsyncSession, user_id: str) -> List[dict]:
        """
        获取今日待回顾的笔记列表。

        查询条件：next_review_at <= 当前时间
        返回：笔记标题、内容预览、回顾次数、间隔天数

        :param db: 数据库会话
        :param user_id: 用户ID
        :return: 待回顾笔记列表
        """
        now = datetime.now()

        # 查询待回顾的记录，关联笔记表获取标题和内容
        stmt = (
            select(ReviewRecord, Note.title, Note.content, Note.tags, Note.category)
            .join(Note, ReviewRecord.note_id == Note.id)
            .where(
                ReviewRecord.user_id == user_id,
                ReviewRecord.next_review_at <= now,
            )
            .order_by(ReviewRecord.next_review_at.asc())
        )
        result = await db.execute(stmt)
        rows = result.all()

        reviews = []
        for record, title, content, tags, category in rows:
            reviews.append({
                "review_id": record.id,
                "note_id": record.note_id,
                "title": title,
                "content_preview": content[:200] if content else "",
                "tags": tags,
                "category": category,
                "review_count": record.review_count,
                "last_reviewed_at": str(record.last_reviewed_at) if record.last_reviewed_at else None,
                "interval_days": record.interval_days,
            })

        return reviews


    async def count_due_reviews(self, db: AsyncSession, user_id: str) -> int:
        """今日（到期）待回顾数量。"""
        from sqlalchemy import func
        now = datetime.now()
        stmt = (
            select(func.count())
            .select_from(ReviewRecord)
            .where(
                ReviewRecord.user_id == user_id,
                ReviewRecord.next_review_at <= now,
            )
        )
        result = await db.execute(stmt)
        return int(result.scalar_one() or 0)

    async def mark_reviewed(self, db: AsyncSession, note_id: str, user_id: str) -> dict:
        """
        标记笔记已回顾 —— 更新回顾状态。

        更新内容：
        - review_count += 1
        - interval_days = 下一个间隔天数
        - last_reviewed_at = 当前时间
        - next_review_at = 当前时间 + 下一个间隔天数

        :param db: 数据库会话
        :param note_id: 笔记ID
        :param user_id: 用户ID
        :return: {"success": True/False, "review_count": N, "interval_days": N}
        """
        # 查询当前记录
        stmt = select(ReviewRecord).where(
            ReviewRecord.note_id == note_id,
            ReviewRecord.user_id == user_id,
        )
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()

        if not record:
            return {"success": False, "message": "回顾记录不存在"}

        now = datetime.now()
        new_count = record.review_count + 1
        next_interval = get_next_interval(new_count)
        next_at = now + timedelta(days=next_interval)

        stmt = (
            update(ReviewRecord)
            .where(ReviewRecord.id == record.id)
            .values(
                review_count=new_count,
                interval_days=next_interval,
                last_reviewed_at=now,
                next_review_at=next_at,
            )
        )
        await db.execute(stmt)
        await db.commit()

        logger.info(f"标记回顾完成 note_id={note_id}, 第{new_count}次回顾, 下次间隔{next_interval}天")

        return {
            "success": True,
            "message": "已标记回顾",
            "review_count": new_count,
            "interval_days": next_interval,
            "next_review_at": str(next_at),
        }

    async def generate_review_question(self, content: str, llm_config: dict = None) -> str:
        """
        生成回顾问题 —— 调用 LLM 分析笔记内容。

        优化策略：
        - 优先读取 Redis 缓存，命中则跳过 LLM 调用
        - 缓存未命中时调用 LLM 生成，并写入缓存

        :param content: 笔记内容（截取前 2000 字）
        :param llm_config: 前端 LLM 配置
        :return: 回顾问题文本
        """
        try:
            from app.utils.prompt_loader import load_prompt
            from langchain_core.messages import HumanMessage
            from app.cache.llm_cache import get_cached_llm_response, set_cached_llm_response

            from app.utils.factory import create_chat_model_from_config, llm_config_is_usable, sanitize_client_llm_config
            llm_config = sanitize_client_llm_config(llm_config)
            if llm_config_is_usable(llm_config):
                model = create_chat_model_from_config(llm_config)
                model_name = llm_config.get("model", "custom")
            else:
                from app.utils.factory import chat_model
                model = chat_model
                import os
                model_name = os.getenv("CHAT_MODEL_NAME", "default")

            prompt_template = load_prompt("review_question_prompt")
            prompt = prompt_template.format(content=content[:2000])

            # 查缓存
            cached = await get_cached_llm_response(prompt, model_name)
            if cached:
                return cached

            response = await model.ainvoke([HumanMessage(content=prompt)])
            result = response.content.strip()

            # 写缓存
            await set_cached_llm_response(prompt, model_name, result)
            return result
        except Exception as e:
            logger.error(f"生成回顾问题失败: {e}")
            return "请回顾这篇笔记的主要内容"


review_service = ReviewService()


def get_review_service() -> ReviewService:
    """依赖注入工厂函数。"""
    return review_service
