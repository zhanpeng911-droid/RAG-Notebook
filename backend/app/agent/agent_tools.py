"""
Agent 工具定义 —— 为 LangChain Agent 提供可调用的工具函数。

每个工具都使用 @tool 装饰器定义，Agent 会根据用户意图自动选择合适的工具。

上下文传递机制：
使用 ContextVar 在请求间传递用户ID、回调函数、LLM配置，
避免参数层层传递，保持工具函数签名简洁。
"""
from typing import Optional, Callable
from contextvars import ContextVar

from langchain_core.tools import tool

from app.core.logger_handler import logger
from app.rag.rag_service import RagService
from app.utils.auth_utils import decode_django_jwt
from app.services.note_service import note_service
from app.services.review_service import review_service
from app.db.db_config import AsyncSessionLocal

import datetime

# 上下文变量 —— 用于在请求生命周期内传递状态
current_user_id_var: ContextVar[Optional[str]] = ContextVar('current_user_id', default=None)
thinking_callback_var: ContextVar[Optional[Callable]] = ContextVar('thinking_callback', default=None)
llm_config_var: ContextVar[Optional[dict]] = ContextVar('llm_config', default=None)

def set_current_user_id(user_id: str):
    """设置当前用户ID到上下文"""
    current_user_id_var.set(user_id)

def reset_current_user_id():
    """重置当前用户ID（请求结束后调用，防止泄漏）"""
    current_user_id_var.set(None)

def get_current_user_id_from_context() -> Optional[str]:
    """从上下文获取当前用户ID"""
    return current_user_id_var.get()

def set_thinking_callback(callback):
    """设置思考过程回调到上下文"""
    thinking_callback_var.set(callback)

def get_thinking_callback_from_context():
    """从上下文获取思考过程回调"""
    return thinking_callback_var.get()

def set_llm_config(config: Optional[dict]):
    """设置 LLM 配置到上下文"""
    llm_config_var.set(config)

def reset_llm_config():
    """重置 LLM 配置"""
    llm_config_var.set(None)

def get_llm_config_from_context() -> Optional[dict]:
    """从上下文获取 LLM 配置"""
    return llm_config_var.get()

@tool(description="用于从向量数据库里检索文档并生成摘要，返回包含文档列表和摘要的结果。返回格式为：'摘要: [摘要内容]\n\n检索到的文档列表:\n1. [文档1内容]\n2. [文档2内容]\n...'。注意：文档已经过自动重排序，无需再调用重排序工具")
async def rag_summary_tools(query: str, user_id: str = None) -> str:
    """
    RAG 摘要工具 —— Agent 的核心工具。

    功能：
    1. 调用 RagService 执行完整的 RAG 流程（HyDE → 混合检索 → 总结）
    2. 返回格式化的结果（摘要 + 文档列表）

    :param query: 用户查询文本
    :param user_id: 用户ID（可选，优先从上下文获取）
    :return: 格式化的摘要和文档列表
    """
    effective_user_id = user_id or get_current_user_id_from_context()
    if not effective_user_id:
        return "错误: 无法确定用户身份，请提供有效的user_id"

    thinking_callback = get_thinking_callback_from_context()
    llm_config = get_llm_config_from_context()
    result = await RagService(effective_user_id, thinking_callback=thinking_callback, llm_config=llm_config).get_documents_and_summary(query)
    documents = result.get("documents", [])
    summary = result.get("summary", "")

    formatted_result = f"摘要: {summary}\n\n"
    formatted_result += "检索到的文档列表（已重排序）:\n"
    for i, doc in enumerate(documents, 1):
        formatted_result += f"{i}. {doc}\n"

    return formatted_result

@tool(description="当用户明确问自己的ID和用户名时，从JWT中获取当前用户ID和用户名，参数为完整的JWT token字符串")
async def get_user_info_tools(token: str) -> str:
    """
    获取用户信息工具 —— 解析 JWT token 提取用户ID和用户名。

    :param token: JWT token 字符串
    :return: 用户信息（ID、用户名）
    """
    payload = decode_django_jwt(token)
    if payload:
        user_id = payload.get("user_id", "未知")
        user_name = payload.get("user_name", "未知")
        return f"用户信息：\n- 用户ID: {user_id}\n- 用户名: {user_name}"
    else:
        return "无法解析JWT token，无法获取用户信息"

@tool(description="用于获取当前年月日时分的工具")
async def what_time_is_now() -> str:
    """获取当前时间工具 —— 返回格式：2024-01-01 12:00"""
    return f"当前时间是：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"

@tool(description="语义搜索用户的笔记，根据关键词返回最相关的笔记列表。参数 query 为搜索关键词，top_k 为返回结果数量（默认5）。")
async def search_notes_tool(query: str, top_k: int = 5) -> str:
    """
    语义搜索笔记工具 —— 基于向量嵌入的语义搜索。

    :param query: 搜索关键词
    :param top_k: 返回结果数量（默认5）
    :return: 相关笔记列表（标题、分类、标签、内容预览）
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            results = await note_service.search_notes(db, user_id, query, top_k=top_k)
            if not results:
                return "未找到相关笔记"
            lines = [f"找到 {len(results)} 篇相关笔记：\n"]
            for i, note in enumerate(results, 1):
                lines.append(f"{i}. **{note.title}**")
                if note.category:
                    lines.append(f"   分类: {note.category}")
                if note.tags:
                    lines.append(f"   标签: {', '.join(note.tags)}")
                lines.append(f"   内容预览: {note.content[:200]}...\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"搜索笔记失败: {e}")
            return f"搜索笔记时出错: {str(e)}"

@tool(description="获取用户的笔记统计信息，包括笔记总数、各分类（工作/学习/生活/项目）的笔记数量。")
async def get_note_stats_tool() -> str:
    """
    笔记统计工具 —— 获取用户笔记的分类统计。

    返回示例：
    📊 笔记统计
    总笔记数: 10
    各分类:
      💼 work: 3 篇
      📖 study: 5 篇
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            stats = await note_service.get_category_stats(db, user_id)
            lines = [f"📊 笔记统计\n", f"总笔记数: {stats['total']}\n", "各分类:"]
            for cat in stats['categories']:
                emoji = {'work': '💼', 'study': '📖', 'life': '🏠', 'project': '🚀'}.get(cat['category'], '📄')
                lines.append(f"  {emoji} {cat['category']}: {cat['count']} 篇")
            if stats['uncategorized'] > 0:
                lines.append(f"  📄 未分类: {stats['uncategorized']} 篇")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取笔记统计失败: {e}")
            return f"获取笔记统计时出错: {str(e)}"

@tool(description="获取今日待回顾的笔记列表。返回每篇笔记的标题、内容预览和回顾次数，帮助用户进行间隔重复复习。")
async def get_today_reviews_tool() -> str:
    """
    获取今日回顾列表工具 —— 基于艾宾浩斯遗忘曲线。

    查询条件：next_review_at <= 当前时间
    返回：待回顾笔记的标题、内容预览、回顾次数
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            reviews = await review_service.get_today_reviews(db, user_id)
            if not reviews:
                return "今日没有待回顾的笔记，继续保持！"
            lines = [f"📅 今日待回顾笔记（共 {len(reviews)} 篇）\n"]
            for i, rv in enumerate(reviews, 1):
                lines.append(f"{i}. **{rv['title']}**")
                lines.append(f"   回顾次数: 第 {rv['review_count'] + 1} 次")
                lines.append(f"   内容预览: {rv['content_preview'][:100]}...\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取今日回顾失败: {e}")
            return f"获取今日回顾时出错: {str(e)}"

@tool(description="标记一篇笔记为已回顾。参数 note_id 为笔记ID。调用成功后笔记的下次回顾时间会自动按艾宾浩斯遗忘曲线延后。")
async def mark_reviewed_tool(note_id: str) -> str:
    """
    标记回顾完成工具 —— 更新笔记的回顾状态。

    效果：
    - review_count += 1
    - next_review_at = 当前时间 + 下一个间隔天数
    - 间隔天数按艾宾浩斯曲线递增：[1, 2, 4, 7, 15, 30] 天

    :param note_id: 笔记ID
    :return: 操作结果（成功/失败 + 下次回顾间隔）
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            result = await review_service.mark_reviewed(db, note_id, user_id)
            if result["success"]:
                return f"✅ 已标记回顾完成！第 {result['review_count']} 次回顾，下次回顾间隔 {result['interval_days']} 天。"
            else:
                return f"标记失败: {result['message']}"
        except Exception as e:
            logger.error(f"标记回顾失败: {e}")
            return f"标记回顾时出错: {str(e)}"

@tool(description="创建一篇新笔记。参数 title 为笔记标题，content 为笔记内容（支持Markdown格式，可选，不传则只创建标题）。创建后会自动生成向量索引和智能标签。")
async def create_note_tool(title: str, content: str = "") -> str:
    """
    创建笔记工具 —— 自动完成以下操作：
    1. MySQL 写入笔记
    2. ChromaDB 写入向量（用于语义搜索）
    3. 后台异步任务：LLM 生成标签和分类

    :param title: 笔记标题
    :param content: 笔记内容（支持 Markdown，可选）
    :return: 操作结果（笔记ID + 状态提示）
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    from app.schemas.models import NoteCreate
    async with AsyncSessionLocal() as db:
        try:
            payload = NoteCreate(title=title, content=content)
            note = await note_service.create_note(db, user_id, payload)
            return f"✅ 笔记创建成功！\n- 标题: {note.title}\n- ID: {note.id}\n- 标签和分类正在后台生成中..."
        except Exception as e:
            logger.error(f"创建笔记失败: {e}")
            return f"创建笔记时出错: {str(e)}"

@tool(description="获取某篇笔记的关联推荐，包括语义相似的笔记和知识库文档。参数 note_id 为笔记ID，top_k 为返回数量（默认3）。")
async def get_related_notes_tool(note_id: str, top_k: int = 3) -> str:
    """
    关联笔记推荐工具 —— 同时搜索笔记库和知识库。

    实现逻辑：
    1. 从笔记库中检索语义相似的笔记（ChromaDB 向量检索）
    2. 从知识库中检索相关文档（ChromaDB 向量检索）
    3. 合并结果并按相似度排序

    :param note_id: 笔记ID
    :param top_k: 返回数量（默认3）
    :return: 关联推荐列表（来源、标题、相似度、预览）
    """
    user_id = get_current_user_id_from_context()
    if not user_id:
        return "错误: 无法确定用户身份"
    async with AsyncSessionLocal() as db:
        try:
            related = await note_service.get_related_notes(db, note_id, user_id, top_k=top_k)
            if not related:
                return "未找到关联笔记或知识库文档"
            lines = [f"🔗 关联推荐（共 {len(related)} 项）\n"]
            for i, item in enumerate(related, 1):
                source_label = "📝 笔记" if item['source'] == 'note' else "📚 知识库"
                lines.append(f"{i}. {source_label} — {item['title']}")
                lines.append(f"   相似度: {item['similarity']}")
                lines.append(f"   预览: {item['content_preview'][:100]}...\n")
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"获取关联推荐失败: {e}")
            return f"获取关联推荐时出错: {str(e)}"
