"""N0 真实文件验收：PDF / Markdown / DOCX / TXT 的锚点必须能回到原文。"""
import sys

import pytest

from tests.helpers.unmock import LANGCHAIN_STACK, restore_real

restore_real(
    *LANGCHAIN_STACK,
    "langchain_community.document_loaders",
    "app.utils.file_handler",
)
# test_file_handler 可能已用 mock 加载器导入过真实模块；强制重载，
# 确保本文件拿到真实 TextLoader/PyPDFLoader 绑定。
sys.modules.pop("app.utils.file_handler", None)

import fitz  # noqa: E402
from docx import Document as DocxDocument  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

import app.utils.file_handler as fh  # noqa: E402
from app.services.source_anchor_service import (  # noqa: E402
    build_anchor_candidates, hash_quote, normalize_quote,
)


def _split(documents, chunk_size=24, chunk_overlap=0):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_documents(documents)


def _assert_quotes_come_from_source(candidates, source_documents):
    source_text = "\n\n".join(
        normalize_quote(doc.page_content) for doc in source_documents)
    assert candidates
    for candidate in candidates:
        assert candidate.quote_text.strip()
        assert normalize_quote(candidate.quote_text) in source_text
        assert candidate.quote_hash == hash_quote(candidate.quote_text)


def test_real_txt_anchor_maps_to_original_characters(tmp_path):
    path = tmp_path / "sample.txt"
    text = "第一段内容，用于验证字符偏移。\n\n第二段内容，同样需要可定位。"
    path.write_text(text, encoding="utf-8")

    documents = fh.txt_loader_sync(str(path))
    chunks = _split(documents, chunk_size=12)
    candidates = build_anchor_candidates(documents, chunks)

    _assert_quotes_come_from_source(candidates, documents)
    assert all(candidate.page_no is None for candidate in candidates)
    assert any(candidate.char_start is not None for candidate in candidates)
    assert any(candidate.locator_kind == "char" for candidate in candidates)


def test_real_markdown_anchor_keeps_heading_path(tmp_path):
    path = tmp_path / "sample.md"
    path.write_text(
        "# 第一章\n\n检索增强生成依赖外部知识。\n\n"
        "## 1.1 小节\n\n证据锚点必须可定位。\n",
        encoding="utf-8",
    )

    documents = fh.markdown_loader_sync(str(path))
    chunks = _split(documents, chunk_size=14)
    candidates = build_anchor_candidates(documents, chunks)

    _assert_quotes_come_from_source(candidates, documents)
    heading_paths = {c.heading_path for c in candidates if c.heading_path}
    assert "第一章" in heading_paths
    assert "第一章 > 1.1 小节" in heading_paths
    assert all(candidate.page_no is None for candidate in candidates)


def test_real_docx_anchor_uses_heading_style_offsets(tmp_path):
    path = tmp_path / "sample.docx"
    document = DocxDocument()
    document.add_heading("第一章", level=1)
    document.add_paragraph("正文内容需要可定位。")
    document.add_heading("1.1 小节", level=2)
    document.add_paragraph("小节内容同样可定位。")
    document.save(str(path))

    documents = fh.word_loader_sync(str(path))
    chunks = _split(documents, chunk_size=10)
    candidates = build_anchor_candidates(documents, chunks)

    _assert_quotes_come_from_source(candidates, documents)
    heading_paths = {c.heading_path for c in candidates if c.heading_path}
    assert "第一章" in heading_paths
    assert "第一章 > 1.1 小节" in heading_paths
    assert all(candidate.page_no is None for candidate in candidates)


def test_real_pdf_anchor_uses_real_page_numbers(tmp_path):
    path = tmp_path / "sample.pdf"
    pdf = fitz.open()
    page1 = pdf.new_page()
    page1.insert_text((72, 72), "Chapter One", fontsize=14)
    page1.insert_text(
        (72, 100),
        "Retrieval augmented generation depends on external knowledge.",
        fontsize=11,
    )
    page2 = pdf.new_page()
    page2.insert_text((72, 72), "Chapter Two", fontsize=14)
    page2.insert_text(
        (72, 100),
        "Evidence anchors must point to the original passage.",
        fontsize=11,
    )
    pdf.save(str(path))
    pdf.close()

    documents = fh.pdf_loader_sync(str(path))
    assert len(documents) >= 2
    chunks = _split(documents, chunk_size=48)
    candidates = build_anchor_candidates(documents, chunks)

    _assert_quotes_come_from_source(candidates, documents)
    pages = {candidate.page_no for candidate in candidates}
    assert pages == {1, 2}
    # 每一页的引文必须能在该页原文中找到，不能跨页伪造定位
    for candidate in candidates:
        page_doc = documents[candidate.page_no - 1]
        assert normalize_quote(candidate.quote_text) in normalize_quote(
            page_doc.page_content)


@pytest.mark.parametrize("path_name", ["plain.txt", "plain.md"])
def test_formats_without_pages_never_report_page(tmp_path, path_name):
    path = tmp_path / path_name
    path.write_text("没有页码的纯文本内容。\n\n第二段。", encoding="utf-8")
    documents = (
        fh.txt_loader_sync(str(path))
        if path_name.endswith(".txt")
        else fh.markdown_loader_sync(str(path))
    )
    chunks = _split(documents, chunk_size=8)
    candidates = build_anchor_candidates(documents, chunks)
    assert candidates
    assert all(candidate.page_no is None for candidate in candidates)
