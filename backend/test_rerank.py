"""
测试重排序服务 —— 验证 DashScope 重排序 API 是否正常工作。
"""
import asyncio
import os
import sys
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
if not api_key:
    raise SystemExit("请先在本地 .env 或环境变量中配置 DASHSCOPE_API_KEY，再运行该脚本。")

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langchain_core.documents import Document
from app.rag.reorder_service import reorder_service


async def test_rerank():
    """测试重排序功能"""
    print("=" * 60)
    print("测试重排序服务")
    print("=" * 60)

    # 模拟检索结果（5 个候选文档）
    query = "什么是 Python 列表推导式？"

    documents = [
        Document(
            page_content="Python 是一种高级编程语言，由 Guido van Rossum 于 1991 年发布。",
            metadata={"source": "python_intro.txt", "source_type": "knowledge_base"}
        ),
        Document(
            page_content="列表推导式是 Python 中创建列表的简洁语法，格式为 [expression for item in iterable if condition]。",
            metadata={"source": "python_list_comp.txt", "source_type": "knowledge_base"}
        ),
        Document(
            page_content="Python 的 for 循环用于遍历序列中的每个元素。",
            metadata={"source": "python_loop.txt", "source_type": "knowledge_base"}
        ),
        Document(
            page_content="列表推导式可以替代传统的 for 循环，使代码更简洁高效。",
            metadata={"source": "my_notes.md", "source_type": "note", "title": "Python 学习笔记"}
        ),
        Document(
            page_content="JavaScript 是一种动态类型的脚本语言，主要用于 Web 开发。",
            metadata={"source": "javascript_intro.txt", "source_type": "knowledge_base"}
        ),
    ]

    print(f"\n查询: {query}")
    print(f"候选文档数: {len(documents)}")
    print("\n候选文档:")
    for i, doc in enumerate(documents, 1):
        print(f"  {i}. [{doc.metadata.get('source_type')}] {doc.page_content[:60]}...")

    # 执行重排序
    print("\n" + "=" * 60)
    print("执行重排序...")
    print("=" * 60)

    reranked_docs = await reorder_service.rerank(
        query=query,
        documents=documents,
        top_k=3
    )

    # 显示结果
    print(f"\n重排序结果 (top 3):")
    print("-" * 60)
    for i, doc in enumerate(reranked_docs, 1):
        score = doc.metadata.get('rerank_score', 'N/A')
        if isinstance(score, (int, float)):
            print(f"  {i}. 分数: {score:.4f}")
        else:
            print(f"  {i}. 分数: {score}")
        print(f"     来源: [{doc.metadata.get('source_type')}] {doc.metadata.get('source')}")
        print(f"     内容: {doc.page_content[:80]}...")
        print()

    print("=" * 60)
    print("✅ 重排序测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_rerank())
