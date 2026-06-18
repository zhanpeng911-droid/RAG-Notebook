from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document


class EmptyRetriever(BaseRetriever):
    """始终返回空结果的检索器。

    用于 RAG 流程中不需要向量检索的场景（例如纯对话模式），
    作为占位检索器插入 LangChain 链中，避免因检索器缺失而报错。
    该检索器不连接任何向量数据库，每次调用均直接返回空列表。
    """

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """异步检索接口，始终返回空列表。

        :param query: 用户查询文本（本检索器不使用该参数）。
        :param run_manager: LangChain 回调管理器（可选），用于追踪检索过程。
        :return: 空列表，表示无任何检索结果。
        """
        return []

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun | None = None,
    ) -> list[Document]:
        """同步检索接口，始终返回空列表。

        :param query: 用户查询文本（本检索器不使用该参数）。
        :param run_manager: LangChain 回调管理器（可选），用于追踪检索过程。
        :return: 空列表，表示无任何检索结果。
        """
        return []