from pydantic import BaseModel
from typing import List, Tuple, Optional


class LLMConfig(BaseModel):
    """LLM 配置（由前端设置页面传入）。

    定义调用大语言模型所需的连接参数，支持多家 LLM 服务商。
    前端用户在设置页面配置后，随每次请求一起提交。

    属性:
        provider: 服务商标识，支持 deepseek / openai / anthropic / ollama / custom。
        model: 具体模型名称，如 "deepseek-chat"、"gpt-4" 等。
        api_key: 服务商 API 密钥。
        base_url: 自定义 API 基础地址，用于私有部署或代理场景。
        protocol: 协议类型，"openai" 表示 OpenAI 兼容接口，"anthropic" 表示原生 Anthropic 接口。
    """
    provider: str = "deepseek"  # deepseek / openai / anthropic / ollama / custom
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    protocol: str = "openai"  # openai / anthropic


class QueryRequest(BaseModel):
    """查询请求模型。

    用户发起对话查询时的请求体，支持关联已有会话或创建新会话。

    属性:
        session_id: 会话 ID，为 None 时创建新会话。
        query: 用户输入的查询文本。
        llm_config: LLM 配置，为 None 时使用系统默认配置。
    """
    session_id: Optional[str] = None
    query: str
    llm_config: Optional[LLMConfig] = None


class RAGRequest(BaseModel):
    """RAG 检索请求模型。

    用于知识库检索场景，用户输入查询文本后，系统从向量数据库中
    检索相关文档并结合 LLM 生成回答。

    属性:
        query: 用户输入的查询文本。
        llm_config: LLM 配置，为 None 时使用系统默认配置。
    """
    query: str
    llm_config: Optional[LLMConfig] = None


class SessionResponse(BaseModel):
    """会话响应模型。

    返回会话信息及历史对话记录，供前端渲染聊天界面。

    属性:
        session_id: 会话唯一标识。
        history: 对话历史列表，每个元素为 (用户消息, AI 回复) 的元组。
    """
    session_id: str
    history: List[Tuple[str, str]]


class AgentStep(BaseModel):
    """Agent 执行步骤模型。

    记录 Agent（智能体）在一次推理过程中的单个执行步骤，
    包括思考过程、使用的工具及工具的输入输出。

    属性:
        thought: Agent 的推理思考内容。
        tool: 使用的工具名称。
        tool_input: 传递给工具的参数字典。
        tool_output: 工具返回的执行结果。
    """
    thought: Optional[str] = None
    tool: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_output: Optional[str] = None


class AgentResponse(BaseModel):
    """Agent 响应模型。

    包含 Agent 的最终回复文本、会话 ID 以及可选的执行步骤链，
    前端可据此展示 Agent 的推理过程。

    属性:
        response: Agent 生成的回复文本。
        session_id: 关联的会话 ID。
        steps: 执行步骤列表，为 None 表示未记录步骤详情。
    """
    response: str
    session_id: str
    steps: Optional[List[AgentStep]] = None


class RAGResponse(BaseModel):
    """RAG 检索响应模型。

    返回 RAG 检索后由 LLM 生成的最终回答文本。

    属性:
        response: LLM 基于检索结果生成的回答内容。
    """
    response: str


class KnowledgeDocument(BaseModel):
    """知识库文档信息模型。

    表示知识库中一个已索引文档的摘要信息，用于列表页展示。

    属性:
        id: 文档唯一标识。
        filename: 系统存储的文件名。
        original_filename: 用户上传时的原始文件名。
        user_id: 文档所属用户 ID，为 None 表示公共知识库文档。
        chunk_count: 文档被切分成的片段数量。
        image_count: 文档中包含的图片数量。
        preview: 文档内容预览文本（通常为前 N 个字符）。
        created_at: 文档创建时间的 ISO 格式字符串。
    """
    id: str
    filename: str
    original_filename: Optional[str] = None
    user_id: Optional[str] = None
    chunk_count: int
    image_count: int = 0
    preview: str
    created_at: Optional[str] = None


class KnowledgeListResponse(BaseModel):
    """知识库文档列表响应模型。

    包含文档列表和总数，用于分页展示知识库文档。

    属性:
        documents: 文档信息列表。
        total_count: 符合条件的文档总数。
    """
    documents: List[KnowledgeDocument]
    total_count: int


class ChunkDetail(BaseModel):
    """文档切片详情模型（含对应图片）。

    表示一个文档切片的完整信息，包括文本内容、所在页码和关联图片。
    用于文档详情页中按切片维度展示内容。

    属性:
        chunk_id: 切片的唯一标识。
        index: 切片在文档中的序号（从 0 开始）。
        content: 切片的文本内容。
        page: 切片所在页码（PDF 文档适用），为 None 表示不适用。
        images: 该切片涉及的所有图片 URL 列表。
    """
    chunk_id: str
    index: int
    content: str
    page: Optional[int] = None
    images: list[str] = []


class KnowledgeDocumentDetail(BaseModel):
    """知识库文档详情响应模型。

    提供文档的完整信息，包括全量文本、切片级详情和图片列表。
    前端在文档详情页可同时展示文本和图片。

    属性:
        id: 文档唯一标识。
        filename: 系统存储的文件名。
        user_id: 文档所属用户 ID。
        chunk_count: 文档被切分的片段数量。
        content: 文档的全量合并文本。
        chunks: 切片详情列表，每个元素包含切片文本和对应图片。
        images: 文档全量图片 URL 列表。
        created_at: 文档创建时间的 ISO 格式字符串。
    """
    id: str
    filename: str
    user_id: Optional[str] = None
    chunk_count: int
    content: str
    chunks: list[ChunkDetail] = []
    images: list[str] = []
    created_at: Optional[str] = None


class ChunkInfo(BaseModel):
    """文档切片信息模型。

    用于"查看切片"页面，提供切片的详细信息及元数据。

    属性:
        chunk_id: 切片的唯一标识。
        index: 切片在文档中的序号。
        content: 切片的文本内容。
        metadata: 切片的元数据字典，可能包含来源文件、页码等信息。
        images: 该切片关联的图片 URL 列表。
    """
    chunk_id: str
    index: int
    content: str
    metadata: dict
    images: list[str] = []


class DocumentChunksResponse(BaseModel):
    """文档切片列表响应模型。

    返回一个文档的所有切片信息，用于切片管理页面。

    属性:
        filename: 文档文件名。
        total_chunks: 切片总数。
        chunks: 切片信息列表。
    """
    filename: str
    total_chunks: int
    chunks: List[ChunkInfo]


class MD5Record(BaseModel):
    """MD5 记录模型。

    表示一个已上传文件的 MD5 摘要记录，用于文件去重。

    属性:
        md5: 文件的 MD5 摘要值。
        filename: 系统存储的文件名。
        original_filename: 用户上传时的原始文件名。
        upload_time: 上传时间的 ISO 格式字符串。
    """
    md5: str
    filename: Optional[str] = None
    original_filename: Optional[str] = None
    upload_time: Optional[str] = None


class MD5ListResponse(BaseModel):
    """MD5 记录列表响应模型。

    返回用户的所有 MD5 记录及总数。

    属性:
        records: MD5 记录列表。
        total_count: 记录总数。
    """
    records: List[MD5Record]
    total_count: int


class NoteCreate(BaseModel):
    """创建笔记请求模型。

    用户新建笔记时提交的请求体。

    属性:
        title: 笔记标题。
        content: 笔记正文内容（Markdown 格式）。
        category: 笔记分类，为 None 表示未分类。
        tags: 标签列表，为 None 表示无标签。
        llm_config: LLM 配置，用于笔记相关的 AI 功能。
    """
    title: str
    content: str
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    llm_config: Optional[LLMConfig] = None


class NoteUpdate(BaseModel):
    """更新笔记请求模型（所有字段可选）。

    仅更新提交的字段，未提交的字段保持原值不变。

    属性:
        title: 笔记标题（可选）。
        content: 笔记正文内容（可选）。
        tags: 标签列表（可选）。
        category: 笔记分类（可选）。
    """
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None


class NoteResponse(BaseModel):
    """笔记响应模型。

    返回笔记的完整信息，用于笔记详情页和列表页展示。

    属性:
        id: 笔记唯一标识。
        user_id: 笔记所属用户 ID。
        title: 笔记标题。
        content: 笔记正文内容。
        tags: 标签列表。
        category: 笔记分类。
        created_at: 创建时间的 ISO 格式字符串。
        updated_at: 最后更新时间的 ISO 格式字符串。
    """
    id: str
    user_id: str
    title: str
    content: str
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class NoteListResponse(BaseModel):
    """笔记列表响应模型。

    返回笔记列表及总数，用于笔记管理页面的分页展示。

    属性:
        notes: 笔记信息列表。
        total_count: 符合条件的笔记总数。
    """
    notes: List[NoteResponse]
    total_count: int


class RelatedNoteItem(BaseModel):
    """关联笔记项模型。

    表示与当前笔记或查询相关联的一条笔记，包含相似度评分。

    属性:
        id: 关联笔记的唯一标识。
        title: 关联笔记的标题。
        content_preview: 关联笔记的内容预览文本。
        content: 关联笔记的完整内容（按需加载）。
        similarity: 与当前笔记的相似度评分（0-1）。
        source: 数据来源标识，"knowledge_base" 或 "note"。
    """
    id: str
    title: str
    content_preview: str
    content: Optional[str] = None
    similarity: float
    source: str  # 来源：knowledge_base 或 note


class RelatedKnowledgeItem(BaseModel):
    """关联知识库文档项模型。

    表示与当前笔记相关联的一条知识库文档，包含相似度评分。

    属性:
        id: 关联文档的唯一标识。
        title: 关联文档的标题（通常为文件名）。
        content_preview: 关联文档的内容预览文本。
        content: 关联文档的完整内容（按需加载）。
        similarity: 与当前笔记的相似度评分（0-1）。
    """
    id: str
    title: str
    content_preview: str
    content: Optional[str] = None
    similarity: float


class RelatedNotesResponse(BaseModel):
    """关联笔记列表响应模型。

    返回与当前笔记或查询相关的笔记和知识库文档列表。

    属性:
        notes: 关联笔记列表。
        knowledge_docs: 关联知识库文档列表。
    """
    notes: List[RelatedNoteItem] = []
    knowledge_docs: List[RelatedKnowledgeItem] = []