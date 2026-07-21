"""
RAG 服务层 —— 协调 HyDE、混合检索、文档总结的完整流程。

流程：
1. HyDE 生成假设性文档（让 LLM 先"猜"一个答案，提升检索质量）
2. 混合检索（向量 + BM25）获取相关文档
3. 同时检索笔记库和知识库
4. 分批总结（并行处理多个文档）
5. 合并生成最终回答
"""
import asyncio
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from app.rag.vector_store import VectorStoreService
from app.rag.reorder_service import reorder_service
from app.utils.factory import get_default_chat_model
from app.utils.prompt_loader import load_prompt
from app.core.logger_handler import logger
from app.services.note_service import note_service


class RagService:
    """
    RAG 服务 —— 协调 HyDE、混合检索、文档总结的完整流程。

    核心流程（get_documents_and_summary）：
    1. HyDE 生成假设性文档（让 LLM 先"猜"一个答案）
    2. 混合检索（向量 + BM25）获取相关文档
    3. 同时检索笔记库和知识库
    4. 分批总结（并行处理多个文档）
    5. 合并生成最终回答
    """

    def __init__(self, user_id: str = None, thinking_callback=None, llm_config: dict = None):
        """
        初始化 RAG 服务。

        :param user_id: 用户ID（用于过滤该用户的数据）
        :param thinking_callback: 思考过程回调函数（用于 SSE 实时推送）
        :param llm_config: 前端传入的 LLM 配置（优先于 .env）
        """
        self.vector_store = VectorStoreService()      # 向量数据库服务（单例）
        self.note_service = note_service              # 笔记服务
        self.retriever = None                         # 混合检索器（延迟初始化）
        self.user_id = user_id                        # 当前用户ID

        # 加载 RAG 摘要提示词模板
        self.prompt_text = load_prompt(prompt_type="rag_summary_prompt")
        self.prompt_template = PromptTemplate.from_template(self.prompt_text)

        # 创建聊天模型：优先使用前端配置，否则用 .env 默认配置
        from app.utils.factory import (
            create_chat_model_from_config,
            llm_config_is_usable,
            sanitize_client_llm_config,
        )
        llm_config = sanitize_client_llm_config(llm_config)
        if llm_config_is_usable(llm_config):
            self.chat_model = create_chat_model_from_config(llm_config)
        else:
            self.chat_model = get_default_chat_model()

        # 构建 LangChain 链：提示词 → 模型 → 输出解析
        self.chain = self._init_chain()

        # HyDE 提示词模板：生成假设性文档用于检索
        self.hyde_prompt_template = PromptTemplate.from_template(
            "基于以下问题，生成一个详细的假设性回答，我会根据你的这个假设性回答在向量数据库里检索文档：\n\n问题：{query}\n\n假设性回答："
        )
        self.thinking_callback = thinking_callback    # 思考过程回调

    async def initialize_retriever(self, query: str = None):
        """
        初始化混合检索器 —— 组合向量检索和 BM25 检索。

        动态权重策略：
        - 长查询（>50字符）：向量权重 0.7，BM25 权重 0.3
        - 短查询（<20字符）：向量权重 0.3，BM25 权重 0.7
        - 中等查询：各 0.5

        :param query: 查询语句（用于动态调整权重）
        """
        if self.retriever is None:
            # 获取动态权重信息
            weights = await self.vector_store.get_dynamic_weights(query)
            
            if self.thinking_callback:
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "retrieval",
                    "content": f"初始化检索器（向量权重: {weights[0]:.1f}, BM25权重: {weights[1]:.1f}）",
                    "details": {
                        "vector_weight": weights[0],
                        "bm25_weight": weights[1]
                    }
                })
            
            self.retriever = await self.vector_store.get_retriever(query, self.user_id)


    def _init_chain(self):
        """
        初始化 LangChain 链 —— 用于文档摘要生成。

        链结构：提示词模板 → 聊天模型 → 字符串输出解析器
        用途：将检索到的文档内容交给 LLM 生成摘要
        """
        chain = (
                self.prompt_template
                | self.chat_model
                | StrOutputParser()
        )
        return chain

    async def generate_hypothetical_document(self, query: str) -> str:
        """
        HyDE（Hypothetical Document Embeddings）—— 生成假设性文档。

        原理：
        1. 让 LLM 先根据查询"猜"一个答案
        2. 用这个假设性文档去向量数据库检索
        3. 比直接用查询文本检索效果更好（因为假设性文档与真实文档语义更接近）

        :param query: 用户查询文本
        :return: 假设性文档内容
        """
        try:
            hyde_chain = (
                self.hyde_prompt_template
                | self.chat_model
                | StrOutputParser()
            )
            hypothetical_doc = await hyde_chain.ainvoke({"query": query})
            logger.info(f"【HyDE】生成的假设性文档:\n{hypothetical_doc}")
            return hypothetical_doc
        except Exception as e:
            logger.error(f"【HyDE】生成假设性文档失败: {e}")
            return query

    async def retrieve_document(self, query: str) -> list:
        """
        混合检索 + 重排序 —— 同时从知识库和笔记库检索相关文档，然后精排。

        执行流程：
        1. 确保检索器已初始化（混合检索器：向量 + BM25）
        2. 使用 HyDE 生成假设性文档
        3. 用假设性文档从知识库检索（ChromaDB）—— top 10
        4. 同时从笔记库检索（ChromaDB notes_collection）—— top 5
        5. 合并结果，标记来源类型
        6. 使用 DashScope 重排序模型精排 —— 返回 top 5

        :param query: 用户查询文本
        :return: 重排序后的文档列表（包含 metadata 标记来源和分数）
        """
        if not self.user_id:
            logger.warning(f"【HyDE】user_id为空，不进行任何检索")
            return []
        
        try:
            # 确保检索器已初始化，传递query参数
            if self.retriever is None:
                await self.initialize_retriever(query)
            
            # 使用HyDE技术生成假设性文档
            logger.info(f"【HyDE】开始处理查询: {query}")
            
            if self.thinking_callback:
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "hyde",
                    "content": f"正在基于查询「{query}」生成假设性文档..."
                })
            
            hypothetical_doc = await self.generate_hypothetical_document(query)
            
            if self.thinking_callback:
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "hyde",
                    "content": f"假设性文档生成完成",
                    "details": {
                        "hypothetical_doc_preview": hypothetical_doc[:200] + "..." if len(hypothetical_doc) > 200 else hypothetical_doc
                    }
                })
            
            # 使用假设性文档进行检索
            logger.info(f"【HyDE】使用假设性文档进行检索")
            
            if self.thinking_callback:
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "retrieval",
                    "content": "正在向量数据库中检索相关文档..."
                })
            
            documents = await self.retriever.ainvoke(hypothetical_doc)

            # 同时检索笔记库
            note_docs = []
            try:
                note_docs = await asyncio.to_thread(
                    self.note_service.notes_store.similarity_search,
                    hypothetical_doc, k=3,
                    filter={"user_id": self.user_id}
                )
            except Exception as e:
                logger.error(f"【RAG】检索笔记失败: {e}")

            # 标记来源并合并（笔记在前，知识库在后）
            for doc in documents:
                doc.metadata["source_type"] = "knowledge_base"
            for doc in note_docs:
                doc.metadata["source_type"] = "note"
            all_documents = note_docs + documents

            logger.info(f"【HyDE】检索到 {len(documents)} 个知识库文档, {len(note_docs)} 个笔记文档")

            # 重排序：使用 DashScope API 精排，返回 top 5
            if self.thinking_callback:
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "rerank",
                    "content": f"正在对 {len(all_documents)} 个候选文档进行重排序精排..."
                })

            reranked_documents = await reorder_service.rerank(
                query=query,
                documents=all_documents,
                top_k=5
            )

            logger.info(f"【重排序】精排完成，返回 {len(reranked_documents)} 个文档")

            if self.thinking_callback:
                doc_previews = []
                for i, doc in enumerate(reranked_documents, 1):
                    preview = doc.page_content[:150] + "..." if len(doc.page_content) > 150 else doc.page_content
                    if doc.metadata.get("source_type") == "note":
                        source = f"笔记《{doc.metadata.get('title', '无标题')}》"
                    else:
                        source = doc.metadata.get("original_filename", doc.metadata.get("source", "unknown"))
                    # 使用重排序分数
                    score = doc.metadata.get('rerank_score', 0.8)
                    doc_previews.append({
                        "index": i,
                        "preview": preview,
                        "source": source,
                        "score": round(score, 4),
                    })
                await self.thinking_callback({
                    "type": "thinking",
                    "stage": "rerank",
                    "content": f"重排序完成，精选 {len(reranked_documents)} 个最相关文档",
                    "details": {
                        "documents": doc_previews
                    }
                })

            return reranked_documents
        except Exception as e:
            logger.error(f"【HyDE】检索文档失败: {e}")
            return []

    async def get_documents_and_summary(self, query: str) -> dict:
        """
        完整 RAG 流程 —— 检索 + 总结。

        执行流程：
        1. 调用 retrieve_document() 检索相关文档
        2. 格式化文档内容（附上来源标记）
        3. 并行总结前 3 个最相关的文档（asyncio.gather）
        4. 如果有多个文档，合并摘要生成最终回答
        5. 设置 30 秒超时，防止 LLM 调用卡死

        :param query: 用户查询文本
        :return: {"documents": [文档列表], "summary": "摘要文本"}
        """
        if not self.user_id:
            logger.warning(f"【RAG】user_id为空，不返回任何文档")
            return {
                "documents": [],
                "summary": "抱歉，我没有找到相关的信息。"
            }
        
        try:
            documents = await self.retrieve_document(query)

            # 提取文档内容列表，附上来源标记供 LLM 引用
            def _format_doc(doc):
                if doc.metadata.get("source_type") == "note":
                    title = doc.metadata.get("title", "无标题")
                    return f"[来源：笔记《{title}》]\n{doc.page_content}"
                else:
                    filename = doc.metadata.get("original_filename", "知识库文档")
                    return f"[来源：知识库《{filename}》]\n{doc.page_content}"

            document_contents = [_format_doc(doc) for doc in documents]

            # 如果没有检索到文档
            if not document_contents:
                return {
                    "documents": [],
                    "summary": "抱歉，我没有找到相关的信息。"
                }

            # 使用分批总结策略
            try:
                # 对每个文档单独总结（使用线程池并发处理）
                individual_summaries = []
                max_documents = 3  # 使用前3个最相关的文档
                
                if self.thinking_callback:
                    await self.thinking_callback({
                        "type": "thinking",
                        "stage": "summarize",
                        "content": f"正在对前 {min(max_documents, len(document_contents))} 个最相关文档进行总结..."
                    })
                
                # 定义单个文档总结函数
                async def summarize_document(i, doc):
                    logger.info(f"【RAG】正在总结第{i}个文档")
                    if self.thinking_callback:
                        await self.thinking_callback({
                            "type": "thinking",
                            "stage": "summarize",
                            "content": f"正在总结第 {i} 个文档..."
                        })
                    # 为单个文档构建上下文
                    single_context = f"【参考资料{i}】:{doc}\n"
                    # 生成单个文档的摘要
                    import time
                    start_time = time.time()
                    single_summary = await asyncio.wait_for(
                        self.chain.ainvoke({"input": query, "context": single_context}),
                        timeout=30.0  # 单个文档总结超时时间
                    )
                    end_time = time.time()
                    logger.info(f"【RAG】第{i}个文档总结耗时: {end_time - start_time:.2f}秒")
                    return single_summary
                
                # 使用线程池并发处理文档总结
                tasks = []
                for i, doc in enumerate(document_contents[:max_documents], 1):
                    tasks.append(summarize_document(i, doc))
                
                # 并发执行所有总结任务，最多5个线程
                import time
                start_time = time.time()
                individual_summaries = await asyncio.gather(*tasks)
                end_time = time.time()
                logger.info(f"【RAG】所有文档总结完成，总耗时: {end_time - start_time:.2f}秒")

                # 如果只有一个文档，直接返回其摘要
                if len(individual_summaries) == 1:
                    logger.info(f"【RAG】生成摘要成功")
                    return {
                        "documents": document_contents,
                        "summary": individual_summaries[0]
                    }

                # 合并多个文档的摘要，生成最终总结
                combined_context = "以下是多个文档的摘要，请综合这些信息生成最终的回答：\n\n"
                for i, summary in enumerate(individual_summaries, 1):
                    combined_context += f"【文档{i}摘要】:{summary}\n\n"

                logger.info(f"【RAG】合并摘要完成，开始生成最终总结")
                
                if self.thinking_callback:
                    await self.thinking_callback({
                        "type": "thinking",
                        "stage": "summarize",
                        "content": "正在综合多个文档生成最终回答..."
                    })
                
                # 生成最终总结
                final_summary = await asyncio.wait_for(
                    self.chain.ainvoke({"input": query, "context": combined_context}),
                    timeout=30.0  # 最终总结超时时间
                )
                
                logger.info(f"【RAG】生成摘要成功")
                return {
                    "documents": document_contents,
                    "summary": final_summary
                }
            except asyncio.TimeoutError:
                logger.error(f"【RAG】生成摘要超时")
                return {
                    "documents": document_contents,
                    "summary": "抱歉，生成摘要超时，请稍后再试。"
                }
        except Exception as e:
            logger.error(f"【RAG】生成摘要失败: {e}", exc_info=True)
            return {
                "documents": [],
                "summary": "抱歉，处理您的请求时出现了错误。"
            }

    async def rag_summary(self, query: str) -> str:
        """
        RAG 摘要 —— 简化接口，只返回摘要文本。

        :param query: 用户查询文本
        :return: 摘要文本
        """
        result = await self.get_documents_and_summary(query)
        return result.get("summary", "抱歉，处理您的请求时出现了错误。")
