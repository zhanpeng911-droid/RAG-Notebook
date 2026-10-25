"""
文档可见性服务（统一方案 B）——资源身份 owner+document_id+active_generation。

公共判定结果区分"允许""明确拒绝""查询失败/无法验证"；依赖失败默认不返回
正文（fail-closed）。MD5 仅作指纹/存量发现线索，不作为授权身份。

复验修正（2026-09-08）：
- 缺失 owner 一律拒绝（不因 user_id 缺省放行）；
- legacy 解析返回可信结构（document_id + compat_generation + source），
  不信任候选 metadata 标记、不原地修改候选制造长期"已验证"状态；
- legacy 存量必须 compat_generation == active_generation 才可见（compat=0
  不得混入已发布的新代次 active>0）；
- 依赖失败抛 VisibilityUnavailable（调用方分类处理，不吞成空列表）。
"""
from typing import List, Dict, Optional


class VisibilityUnavailable(Exception):
    """可见性查询依赖失败——调用方不得返回正文（fail-closed）。"""


class LegacyResolution:
    """可信的 legacy 解析结果（来自映射仓库，非候选 metadata 标记）。"""

    __slots__ = ("doc", "document_id", "compat_generation", "source")

    def __init__(self, doc, document_id: str, compat_generation: int, source: str):
        self.doc = doc
        self.document_id = document_id
        self.compat_generation = compat_generation
        self.source = source


class DocumentVisibilityService:
    def __init__(self, user_id: str, source: Optional[str] = None):
        self.user_id = user_id
        # legacy 数据源身份：调用方传入实际 collection 身份；缺省取配置集合名
        self.source = source or _default_collection_name()

    async def resolve_visible_generations(
            self, document_ids: List[str]) -> Dict[str, int]:
        """按 document_id 批量取当前可见代次（仅 indexed 文档）。"""
        if not document_ids:
            return {}
        from app.db.db_config import AsyncSessionLocal
        from app.repositories.document_index_repository import DocumentIndexRepository
        try:
            async with AsyncSessionLocal() as session:
                repo = DocumentIndexRepository(session)
                return await repo.get_indexed_document_generations(
                    self.user_id, list(document_ids))
        except Exception as e:
            from app.core.logger_handler import logger
            logger.warning(f"【可见性】按 document_id 查询失败（fail-closed）: {e}")
            raise VisibilityUnavailable(str(e))

    async def _resolve_legacy(self, documents: List) -> List[LegacyResolution]:
        """C legacy resolver：缺 document_id 候选经映射仓库解析。

        返回可信解析结果（含 compat_generation）；不修改候选 metadata。
        映射查询返回 (document_id, compat_generation)；缺失/未解析 → 拒绝。
        """
        legacy_ids = [d.metadata.get("chunk_id") or d.metadata.get("id")
                      for d in documents if not d.metadata.get("document_id")]
        legacy_ids = [str(i) for i in legacy_ids if i]
        if not legacy_ids:
            return []
        from app.db.db_config import AsyncSessionLocal
        from app.repositories.legacy_mapping_repository import LegacyMappingRepository
        try:
            async with AsyncSessionLocal() as session:
                repo = LegacyMappingRepository(session)
                mapped = await repo.get_active_for_owner_with_generation(
                    self.source, self.user_id, legacy_ids)
        except Exception:
            raise VisibilityUnavailable("legacy 映射查询失败")
        resolved = []
        for d in documents:
            if d.metadata.get("document_id"):
                continue
            lid = str(d.metadata.get("chunk_id") or d.metadata.get("id") or "")
            entry = mapped.get(lid)
            if not entry:
                continue  # 未解析：拒绝（不猜测归属）
            doc_id, compat_gen = entry
            resolved.append(LegacyResolution(
                doc=d, document_id=doc_id,
                compat_generation=int(compat_gen or 0), source=self.source))
        return resolved

    async def filter_by_identity(self, documents: List) -> List:
        """按 owner+document_id+generation 复核；返回可见子集。

        - 缺失 owner 一律拒绝（user_id 必须等于请求者）；
        - 缺 document_id 候选经 legacy resolver（compat_generation ==
          active_generation 才可见；compat=0 不得混入已发布新代次）；
        - 带 document_id 候选：active 存在 + generation 匹配（旧代次拒绝）；
        - 依赖失败抛 VisibilityUnavailable。
        """
        if not documents:
            return documents

        # 完整身份：user_id 必须等于请求者；缺失一律拒绝
        documents = [d for d in documents
                     if str(d.metadata.get("user_id")) == self.user_id]

        legacy_resolved: List[LegacyResolution] = []
        if any(not d.metadata.get("document_id") for d in documents):
            legacy_resolved = await self._resolve_legacy(documents)

        doc_ids = [d.metadata.get("document_id") for d in documents
                   if d.metadata.get("document_id")]
        doc_ids += [r.document_id for r in legacy_resolved]
        if not doc_ids:
            return []
        try:
            active = await self.resolve_visible_generations(doc_ids)
        except VisibilityUnavailable:
            raise  # 依赖失败：调用方分类处理，不吞成空列表

        seen = set()
        kept = []
        # 1) 带 document_id 的候选：generation 必须匹配当前 active
        for d in documents:
            did = d.metadata.get("document_id")
            if not did or id(d) in seen:
                continue
            seen.add(id(d))
            active_gen = active.get(did)
            if active_gen is None:
                continue
            gen_meta = d.metadata.get("generation")
            if gen_meta is not None:
                if int(gen_meta) != int(active_gen):
                    continue  # 旧代次
            elif int(active_gen) > 0:
                continue  # gen-less 残留（active>0 无 generation）→ 拒绝；
                # legacy 存量仅经 compat==active 路径放行
            kept.append(d)
        # 2) legacy 解析结果：compat_generation 必须等于当前 active 代次
        for r in legacy_resolved:
            active_gen = active.get(r.document_id)
            if active_gen is None:
                continue  # 文档已删除/非 indexed/映射已撤销
            if int(r.compat_generation) != int(active_gen):
                continue  # compat=0 不得混入已发布新代次（active>0）
            kept.append(r.doc)
        return kept


def _default_collection_name() -> str:
    """legacy 数据源缺省身份：取真实向量库配置 collection 名。

    配置缺失/读取失败时明确抛错（不静默回退 default，避免映射查询与
    revoke 操作错误的数据源范围）。
    """
    from app.utils.config import chroma_config
    name = chroma_config.get("collection_name")
    if not name:
        raise VisibilityUnavailable("chroma_config 缺少 collection_name，无法确定数据源身份")
    return name


def create_visibility_service(user_id: str, source: Optional[str] = None) -> DocumentVisibilityService:
    return DocumentVisibilityService(user_id, source=source)
