"""
引用规范化器 —— 处理和规范化引用格式。

职责：
- 从证据中提取引用信息
- 生成标准化的引用格式
- 确保引用可追溯
"""
from typing import List, Dict, Any
from dataclasses import dataclass, asdict

from app.rag.retrieval_service import Evidence, SourceType


@dataclass
class Citation:
    """引用"""
    index: int  # 引用序号
    source_type: str  # knowledge | note
    source_id: str  # 文档或笔记 ID
    title: str  # 文件名或笔记标题
    content_preview: str  # 内容预览
    score: float  # 相关性分数

    def to_dict(self) -> dict:
        return asdict(self)


class CitationManager:
    """引用管理器"""

    def create_citations(self, evidences: List[Evidence]) -> List[Citation]:
        """
        从证据列表创建引用。

        :param evidences: 证据列表
        :return: 引用列表
        """
        citations = []
        for i, ev in enumerate(evidences, 1):
            citation = Citation(
                index=i,
                source_type=ev.source_type,
                source_id=ev.source_id,
                title=ev.title,
                content_preview=ev.content[:100] + "..." if len(ev.content) > 100 else ev.content,
                score=ev.score,
            )
            citations.append(citation)

        return citations

    def format_citations_for_prompt(self, citations: List[Citation]) -> str:
        """
        格式化引用用于提示词。

        :param citations: 引用列表
        :return: 格式化的引用文本
        """
        if not citations:
            return ""

        lines = ["引用来源："]
        for c in citations:
            if c.source_type == SourceType.KNOWLEDGE:
                lines.append(f"[{c.index}] 知识库《{c.title}》- {c.content_preview}")
            else:
                lines.append(f"[{c.index}] 笔记《{c.title}》- {c.content_preview}")

        return "\n".join(lines)

    def format_citations_for_display(self, citations: List[Citation]) -> List[Dict[str, Any]]:
        """
        格式化引用用于前端显示。

        :param citations: 引用列表
        :return: 引用字典列表
        """
        return [c.to_dict() for c in citations]


# 全局实例
citation_manager = CitationManager()
