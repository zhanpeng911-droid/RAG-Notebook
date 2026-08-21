"""IR Eval Runner —— 真实检索四阶段归因评测。

对同一批 ir_eval case，分别以四种检索模式执行，输出各阶段 Recall@K /
Precision@K / MRR 对比，定位"融合与精排是否真的带来增益"：

    vector  : 向量单路检索
    bm25    : BM25 单路检索
    hybrid  : Ensemble 融合（加权 RRF，与生产 HybridRetriever 同构）
    rerank  : hybrid 候选 + Cross-Encoder 精排（与生产管线对齐）

用法:
    cd backend
    python -m evals.runners.run_ir_eval --top-k 3
    python -m evals.runners.run_ir_eval --top-k 3 --modes vector,bm25,hybrid
    python -m evals.runners.run_ir_eval --rebuild   # 强制重建临时索引

前置条件:
- embedding 模型可用（Ollama 本地 或 DashScope，读取应用 .env 配置）
- rerank 模式需要 DASHSCOPE_API_KEY，未配置时自动跳过并警告

隔离性:
- 使用独立临时 Chroma 目录（data/ir_eval_chromadb），不触碰生产 data/chromadb
- 不 import app.rag.vector_store（避开生产单例）
"""
import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Add backend to path so imports work
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter

from evals.graders.ir_metrics import (
    aggregate_by_topic,
    aggregate_ir,
    grade_case_ir,
    refusal_correct,
    sources_from_docs,
)

EVALS_DIR = Path(__file__).resolve().parent.parent
CASES_FILE = EVALS_DIR / "cases" / "ir_eval_cases.jsonl"
SEED_DOCS_DIR = EVALS_DIR / "seed_docs"
REPORTS_DIR = EVALS_DIR / "reports"
IR_EVAL_PERSIST_DIR = BACKEND_DIR / "data" / "ir_eval_chromadb"
COLLECTION_NAME = "ir_eval_collection"

ALL_MODES = ["vector", "bm25", "hybrid", "rerank"]


def load_cases() -> list[dict]:
    """加载 ir_eval cases。"""
    cases = []
    with open(CASES_FILE, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  WARNING: invalid JSON in ir_eval_cases.jsonl:{line_no}: {e}")
    return cases


def load_seed_documents() -> list[Document]:
    """加载 seed_docs 全量文档（metadata.original_filename = 文件名，作为出处标识）。"""
    documents = []
    for path in sorted(SEED_DOCS_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        documents.append(
            Document(
                page_content=text,
                metadata={
                    "original_filename": path.name,
                    "source": path.name,
                    "user_id": "ir-eval",
                },
            )
        )
    return documents


def _chunk_config() -> dict:
    """读取生产同款切分配置（chroma.yaml）。"""
    try:
        import yaml

        config_path = BACKEND_DIR / "app" / "config" / "chroma.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        return {
            "chunk_size": config.get("chunk_size", 500),
            "chunk_overlap": config.get("chunk_overlap", 60),
            "separators": config.get("separators", ["\n\n", "\n", "。", ""]),
        }
    except Exception as e:
        print(f"  WARNING: 读取 chroma.yaml 失败，使用默认切分配置: {e}")
        return {"chunk_size": 500, "chunk_overlap": 60, "separators": ["\n\n", "\n", "。", ""]}


def build_index(rebuild: bool = False) -> tuple[Chroma, list[Document]]:
    """构建（或复用）独立评测索引，返回 (Chroma 实例, 全量切片文档)。"""
    from app.utils.factory import get_default_embed_model

    embed_model = get_default_embed_model()
    cfg = _chunk_config()

    if rebuild and IR_EVAL_PERSIST_DIR.exists():
        import shutil

        shutil.rmtree(IR_EVAL_PERSIST_DIR)

    # 切分（与生产同参数）
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg["chunk_size"],
        chunk_overlap=cfg["chunk_overlap"],
        separators=cfg["separators"],
    )
    raw_docs = load_seed_documents()
    chunks = splitter.split_documents(raw_docs)
    print(f"  语料: {len(raw_docs)} 篇文档 -> {len(chunks)} 个切片")

    # 已有索引则直接打开（判断依据：能取到文档）
    store = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embed_model,
        persist_directory=str(IR_EVAL_PERSIST_DIR),
    )
    existing = store.get(include=["metadatas"])
    if existing and existing.get("ids"):
        print(f"  复用已有评测索引: {len(existing['ids'])} 个切片（--rebuild 强制重建）")
        indexed_chunks = [
            Document(page_content="", metadata=m or {})
            for m in existing["metadatas"]
        ]
        # BM25 需要 chunk 全文，从 Chroma 取回
        fetched = store.get(include=["documents", "metadatas"])
        indexed_chunks = [
            Document(page_content=d or "", metadata=m or {})
            for d, m in zip(fetched["documents"], fetched["metadatas"])
        ]
        return store, indexed_chunks

    store.add_documents(chunks)
    print(f"  已构建评测索引: {len(chunks)} 个切片 -> {IR_EVAL_PERSIST_DIR}")
    return store, chunks


def _docs_to_eval_dicts(docs: list[Document]) -> list[dict]:
    """langchain Document -> grader/ir_metrics 需要的 dict 形式。"""
    return [{"content": d.page_content, "metadata": dict(d.metadata or {})} for d in docs]


class ModeRetrievers:
    """按模式构建检索器（与生产 HybridRetriever 同构）。"""

    def __init__(self, store: Chroma, chunks: list[Document], top_k: int):
        self.store = store
        self.top_k = top_k
        # BM25 索引基于全量切片（与生产一致：按用户语料建 BM25）
        self.bm25 = BM25Retriever.from_documents(chunks, k=top_k)
        self.vector = store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": top_k},
        )
        # 融合权重与生产默认一致（中等查询 0.5/0.5）
        self.hybrid = EnsembleRetriever(
            retrievers=[self.vector, self.bm25],
            weights=[0.5, 0.5],
        )

    def retrieve(self, mode: str, query: str, rerank_fn=None) -> list[dict]:
        """按模式检索，返回 eval dict 列表（保持排名顺序）。"""
        if mode == "vector":
            docs = self.vector.invoke(query)
        elif mode == "bm25":
            docs = self.bm25.invoke(query)
        elif mode == "hybrid":
            docs = self.hybrid.invoke(query)
        elif mode == "rerank":
            if rerank_fn is None:
                raise ValueError("rerank mode requires rerank_fn")
            candidates = self.hybrid.invoke(query)
            return rerank_fn(query, candidates, self.top_k)
        else:
            raise ValueError(f"unknown mode: {mode}")
        return _docs_to_eval_dicts(docs)


def _make_rerank_fn():
    """构建重排函数（需要 DashScope key；未配置返回 None）。"""
    try:
        from app.rag.reranker import reranker

        if not reranker.api_key:
            return None

        def rerank_fn(query: str, candidates: list[Document], top_k: int) -> list[dict]:
            texts = [d.page_content[:1000] for d in candidates]
            results = asyncio.run(reranker.rerank(query=query, documents=texts, top_n=top_k))
            return [
                _docs_to_eval_dicts([candidates[r.index]])[0]
                for r in sorted(results, key=lambda r: r.score, reverse=True)[:top_k]
                if r.index < len(candidates)
            ]

        return rerank_fn
    except Exception as e:
        print(f"  WARNING: 初始化 reranker 失败: {e}")
        return None


async def run_eval(top_k: int, modes: list[str], rebuild: bool) -> dict:
    """执行评测主流程。"""
    print("构建评测索引...")
    store, chunks = build_index(rebuild=rebuild)

    rerank_fn = None
    if "rerank" in modes:
        rerank_fn = _make_rerank_fn()
        if rerank_fn is None:
            print("  WARNING: DashScope API Key 未配置，跳过 rerank 模式")
            modes = [m for m in modes if m != "rerank"]

    retrievers = ModeRetrievers(store, chunks, top_k)
    cases = load_cases()
    answerable = [c for c in cases if not c.get("expected_no_answer")]
    unanswerable = [c for c in cases if c.get("expected_no_answer")]
    print(f"加载 case: {len(cases)} 条（可答 {len(answerable)} / 不可答 {len(unanswerable)}）")

    mode_results: dict = {}
    case_details: dict = {m: {} for m in modes}

    for mode in modes:
        per_case = []
        topics = []
        case_records = []
        start = time.time()

        for case in answerable:
            relevant = case.get("expected_sources", [])
            docs = retrievers.retrieve(mode, case["user_input"], rerank_fn=rerank_fn)
            metrics = grade_case_ir(docs, relevant, k=top_k)
            per_case.append(metrics)
            topics.append(case.get("topic", "unknown"))
            case_records.append({"id": case["id"], "topic": case.get("topic"), **metrics})

        # 不可答 case：Top-K 内容不含 forbidden_keywords 即为正确拒答
        refusal_flags = []
        for case in unanswerable:
            docs = retrievers.retrieve(mode, case["user_input"], rerank_fn=rerank_fn)
            forbidden = case.get("forbidden_keywords", [])
            combined = " ".join(d.get("content", "") for d in docs)
            ok = not any(kw in combined for kw in forbidden)
            refusal_flags.append(ok)
            case_records.append({
                "id": case["id"],
                "topic": case.get("topic"),
                "refusal_correct": ok,
                "top_sources": sources_from_docs(docs)[:top_k],
            })

        from evals.graders.ir_metrics import refusal_accuracy

        mode_results[mode] = {
            "aggregate": aggregate_ir(per_case),
            "by_topic": aggregate_by_topic(topics, per_case),
            "refusal_accuracy": refusal_accuracy(refusal_flags),
            "elapsed_seconds": round(time.time() - start, 1),
        }
        case_details[mode] = case_records

    return {
        "mode": "ir-eval",
        "top_k": top_k,
        "modes": modes,
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswerable),
        "results": mode_results,
        "case_details": case_details,
    }


def print_report(report: dict) -> None:
    """控制台输出摘要报告。"""
    print("\n" + "=" * 70)
    print(f"IR Eval Report  (top_k={report['top_k']}, cases={report['case_count']})")
    print("=" * 70)

    header = f"  {'mode':<10} {'Recall@K':>10} {'Precision@K':>12} {'MRR':>8} {'拒答正确率':>10} {'耗时(s)':>8}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for mode, data in report["results"].items():
        agg = data["aggregate"]
        print(
            f"  {mode:<10} {agg['recall_at_k']:>10.4f} {agg['precision_at_k']:>12.4f} "
            f"{agg['mrr']:>8.4f} {data['refusal_accuracy']:>10.2%} {data['elapsed_seconds']:>8.1f}"
        )

    print("\n  分主题 Recall@K（定位哪类知识检索质量偏低）:")
    base_mode = report["modes"][0]
    by_topic = report["results"][base_mode]["by_topic"]
    for topic, agg in sorted(by_topic.items(), key=lambda x: x[1]["recall_at_k"]):
        print(f"    {topic:<14} Recall@K={agg['recall_at_k']:.4f}  MRR={agg['mrr']:.4f}  (n={agg['case_count']})")

    # 失败 case 归因（第一个模式下 recall < 1 的）
    print("\n  未命中标准出处的 case（归因清单）:")
    for mode in report["modes"]:
        for rec in report["case_details"].get(mode, []):
            if "recall_at_k" in rec and rec["recall_at_k"] < 1.0:
                print(f"    [{mode}] {rec['id']} ({rec.get('topic')}): recall={rec['recall_at_k']}, hit_rank={rec.get('hit_rank')}")

    print("=" * 70)
    print("NOTE: 结果依赖 embedding 模型与语料版本，请在相同环境下对比不同版本。")
    print("=" * 70)


def save_markdown_report(report: dict) -> Path:
    """保存 Markdown 报告到 reports/ 目录。"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORTS_DIR / f"ir_eval_{timestamp}.md"

    lines = [
        "# IR Eval Report",
        "",
        f"- 时间: {datetime.now().isoformat(timespec='seconds')}",
        f"- Top-K: {report['top_k']}",
        f"- Case: {report['case_count']}（可答 {report['answerable_count']} / 不可答 {report['unanswerable_count']}）",
        f"- 模式: {', '.join(report['modes'])}",
        "",
        "## 各阶段指标对比",
        "",
        "| 模式 | Recall@K | Precision@K | MRR | 拒答正确率 | 耗时(s) |",
        "|---|---|---|---|---|---|",
    ]
    for mode, data in report["results"].items():
        agg = data["aggregate"]
        lines.append(
            f"| {mode} | {agg['recall_at_k']:.4f} | {agg['precision_at_k']:.4f} | {agg['mrr']:.4f} "
            f"| {data['refusal_accuracy']:.2%} | {data['elapsed_seconds']} |"
        )

    base_mode = report["modes"][0]
    lines += ["", f"## 分主题得分（{base_mode} 模式）", "", "| 主题 | Recall@K | MRR | Case 数 |", "|---|---|---|---|"]
    for topic, agg in sorted(report["results"][base_mode]["by_topic"].items(), key=lambda x: x[1]["recall_at_k"]):
        lines.append(f"| {topic} | {agg['recall_at_k']:.4f} | {agg['mrr']:.4f} | {agg['case_count']} |")

    lines += ["", "## 未命中标准出处的 case", ""]
    for mode in report["modes"]:
        for rec in report["case_details"].get(mode, []):
            if "recall_at_k" in rec and rec["recall_at_k"] < 1.0:
                lines.append(
                    f"- [{mode}] {rec['id']} ({rec.get('topic')}): recall={rec['recall_at_k']}, "
                    f"hit_rank={rec.get('hit_rank')}, retrieved={rec.get('retrieved_count')}"
                )

    lines += ["", "## 不可答 case 拒答明细", ""]
    for mode in report["modes"]:
        for rec in report["case_details"].get(mode, []):
            if "refusal_correct" in rec:
                flag = "正确" if rec["refusal_correct"] else "错误（Top-K 命中禁答内容）"
                lines.append(f"- [{mode}] {rec['id']}: {flag}，Top-K 出处: {rec.get('top_sources')}")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description="IR Eval Runner (real retrieval)")
    parser.add_argument("--top-k", type=int, default=3, help="Recall@K 的 K 值（默认 3）")
    parser.add_argument(
        "--modes",
        type=str,
        default=",".join(ALL_MODES),
        help=f"评测模式，逗号分隔（默认全部: {','.join(ALL_MODES)}）",
    )
    parser.add_argument("--rebuild", action="store_true", help="强制重建临时评测索引")
    args = parser.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    invalid = [m for m in modes if m not in ALL_MODES]
    if invalid:
        print(f"ERROR: 未知模式: {invalid}（可选: {ALL_MODES}）")
        sys.exit(1)

    report = asyncio.run(run_eval(top_k=args.top_k, modes=modes, rebuild=args.rebuild))
    print_report(report)

    path = save_markdown_report(report)
    print(f"\nReport saved to: {path}")


if __name__ == "__main__":
    main()
