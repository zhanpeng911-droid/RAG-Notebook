"""
Agentic RAG vs 传统 RAG 对比压力测试

66 题 × 2 链路，记录准确率 + 耗时，生成对比报告。

用法:
  cd backend
  uv run python -m evals.runners.run_comparison_test
"""
import json
import time
import asyncio
import os
import sys

# ===== 配置 =====
USER_ID = "6FLQT8EZB8AbgypHLkeEwk"
CASES_FILE = os.path.join(os.path.dirname(__file__), "..", "cases", "stress_test_cases.jsonl")
REPORT_MD = os.path.join(os.path.dirname(__file__), "..", "reports", "stress_comparison_report.md")
REPORT_JSON = os.path.join(os.path.dirname(__file__), "..", "reports", "stress_comparison_report.json")
INTERVAL = 3  # 每题间隔秒数

REFUSAL_KEYWORDS = ["抱歉", "无法", "不能", "没有找到", "不在", "不包含", "sorry", "cannot", "unable"]

def load_cases():
    cases = []
    with open(CASES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases

def check_keywords(answer, keywords):
    """检查答案是否包含期望关键词"""
    if not keywords:
        return True, []
    answer_lower = answer.lower()
    missing = []
    for kw in keywords:
        if kw.lower() not in answer_lower:
            missing.append(kw)
    return len(missing) == 0, missing

def check_refusal(answer):
    """检查答案是否正确拒答"""
    if not answer or len(answer) < 5:
        return True
    answer_lower = answer.lower()
    for kw in REFUSAL_KEYWORDS:
        if kw.lower() in answer_lower:
            return True
    return False

def score_answer(answer, case):
    """评分：返回 (passed, score, details)"""
    if case.get("expected_refusal"):
        refused = check_refusal(answer)
        return refused, 1.0 if refused else 0.0, {"refused": refused}

    keywords = case.get("expected_keywords", [])
    if not keywords:
        return True, 1.0, {"no_keywords": True}

    found, missing = check_keywords(answer, keywords)
    score = (len(keywords) - len(missing)) / len(keywords)
    passed = score >= 0.5  # 至少命中一半关键词
    return passed, score, {"found": len(keywords) - len(missing), "total": len(keywords), "missing": missing}

# ===== 传统 RAG（脚本内模拟）=====
async def traditional_rag(query, user_id):
    """传统 RAG：固定参数检索 + 直接生成，无 Planner/Grader/CRAG/Judge"""
    import asyncio
    from app.rag.retrieval_service import RetrievalService
    from langchain_core.messages import HumanMessage
    from app.utils.factory import get_default_chat_model

    start = time.time()
    try:
        # 固定参数（不根据查询类型调整）
        svc = RetrievalService(user_id=user_id)
        evidences = await asyncio.wait_for(
            svc.retrieve(query=query, scope="all", top_k=5, use_hyde=True, use_rerank=True),
            timeout=60.0
        )

        if not evidences:
            return {"answer": "抱歉，没有找到相关的资料来回答这个问题。", "evidence_count": 0, "elapsed": time.time() - start, "error": None}

        # 直接生成（无证据评分、无 CRAG、无 LLM-as-judge）
        context = "\n\n".join([f"[{i+1}] {e.content}" for i, e in enumerate(evidences)])
        prompt = f"基于以下资料回答问题。如果资料中没有相关信息，请说明无法回答。\n\n资料：\n{context}\n\n问题：{query}\n\n回答："
        model = get_default_chat_model()
        resp = await asyncio.wait_for(
            model.ainvoke([HumanMessage(content=prompt)]),
            timeout=30.0
        )

        return {
            "answer": resp.content,
            "evidence_count": len(evidences),
            "elapsed": time.time() - start,
            "error": None,
        }
    except asyncio.TimeoutError:
        return {"answer": "抱歉，生成答案超时。", "evidence_count": 0, "elapsed": time.time() - start, "error": "timeout"}
    except Exception as e:
        return {"answer": f"抱歉，生成答案时出现错误。", "evidence_count": 0, "elapsed": time.time() - start, "error": str(e)[:100]}

# ===== Agentic RAG（完整链路）=====
async def agentic_rag(query, user_id):
    """Agentic RAG：完整 Planner + Grader + CRAG + Answer + Judge"""
    from app.agentic.graph import run_agent

    start = time.time()
    try:
        result = await run_agent(query=query, user_id=user_id)
        elapsed = time.time() - start

        # 从 phases 中提取 evidence_count
        evidence_count = 0
        for phase in result.get("phases", []):
            state = phase.get("state", {})
            if state.get("evidence_count", 0) > evidence_count:
                evidence_count = state["evidence_count"]

        return {
            "answer": result.get("answer", ""),
            "evidence_count": evidence_count,
            "quality_scores": result.get("quality_scores"),
            "elapsed": elapsed,
            "error": result.get("error"),
        }
    except Exception as e:
        return {"answer": "", "evidence_count": 0, "elapsed": time.time() - start, "error": str(e)[:100]}

# ===== 清除 LLM 缓存 =====
async def clear_llm_cache():
    """清除 Redis 中的 LLM 缓存"""
    try:
        from app.db.redis_config import get_redis_client
        redis = await get_redis_client()
        if redis:
            keys = await redis.keys("llm:*")
            if keys:
                await redis.delete(*keys)
    except Exception:
        pass

# ===== 主测试流程 =====
async def main():
    cases = load_cases()
    print(f"Loaded {len(cases)} test cases")
    print(f"User ID: {USER_ID}")
    print()

    results = []

    for i, case in enumerate(cases):
        cid = case["id"]
        intent = case["intent"]
        query = case["query"]
        print(f"[{i+1}/{len(cases)}] {cid} ({intent}): {query[:40]}...", flush=True)

        # === 传统 RAG ===
        await clear_llm_cache()
        trad = await traditional_rag(query, USER_ID)
        trad_passed, trad_score, trad_details = score_answer(trad["answer"], case)
        print(f"  传统: {trad_score:.0%} ({trad['elapsed']:.1f}s) {'✓' if trad_passed else '✗'}", flush=True)

        await asyncio.sleep(INTERVAL)

        # === Agentic RAG ===
        await clear_llm_cache()
        agent = await agentic_rag(query, USER_ID)
        agent_passed, agent_score, agent_details = score_answer(agent["answer"], case)
        print(f"  Agentic: {agent_score:.0%} ({agent['elapsed']:.1f}s) {'✓' if agent_passed else '✗'}", flush=True)

        results.append({
            "case_id": cid,
            "intent": intent,
            "query": query,
            "expected_keywords": case.get("expected_keywords", []),
            "expected_refusal": case.get("expected_refusal", False),
            "traditional": {
                "answer_preview": trad["answer"][:200] if trad["answer"] else "",
                "elapsed": round(trad["elapsed"], 2),
                "evidence_count": trad["evidence_count"],
                "passed": trad_passed,
                "score": round(trad_score, 2),
                "details": trad_details,
                "error": trad["error"],
            },
            "agentic": {
                "answer_preview": agent["answer"][:200] if agent["answer"] else "",
                "elapsed": round(agent["elapsed"], 2),
                "evidence_count": agent["evidence_count"],
                "quality_scores": agent.get("quality_scores"),
                "passed": agent_passed,
                "score": round(agent_score, 2),
                "details": agent_details,
                "error": agent["error"],
            },
        })

        await asyncio.sleep(INTERVAL)

    # ===== 生成报告 =====
    generate_report(results, cases)

def generate_report(results, cases):
    # 按意图分组统计
    intents = {}
    for r in results:
        intent = r["intent"]
        if intent not in intents:
            intents[intent] = {
                "total": 0,
                "trad_pass": 0, "agent_pass": 0,
                "trad_times": [], "agent_times": [],
            }
        d = intents[intent]
        d["total"] += 1
        if r["traditional"]["passed"]: d["trad_pass"] += 1
        if r["agentic"]["passed"]: d["agent_pass"] += 1
        d["trad_times"].append(r["traditional"]["elapsed"])
        d["agent_times"].append(r["agentic"]["elapsed"])

    total = len(results)
    trad_total_pass = sum(1 for r in results if r["traditional"]["passed"])
    agent_total_pass = sum(1 for r in results if r["agentic"]["passed"])
    trad_avg = sum(r["traditional"]["elapsed"] for r in results) / total
    agent_avg = sum(r["agentic"]["elapsed"] for r in results) / total

    # JSON 报告
    report = {
        "summary": {
            "total_cases": total,
            "documents": 102,
            "overall": {
                "traditional": {"pass": trad_total_pass, "total": total, "rate": round(trad_total_pass/total, 4)},
                "agentic": {"pass": agent_total_pass, "total": total, "rate": round(agent_total_pass/total, 4)},
            },
            "avg_time": {"traditional": round(trad_avg, 2), "agentic": round(agent_avg, 2)},
        },
        "by_intent": {},
        "details": results,
    }
    for intent, d in intents.items():
        report["by_intent"][intent] = {
            "total": d["total"],
            "traditional": {"pass": d["trad_pass"], "rate": round(d["trad_pass"]/d["total"], 4),
                            "avg_time": round(sum(d["trad_times"])/len(d["trad_times"]), 2)},
            "agentic": {"pass": d["agent_pass"], "rate": round(d["agent_pass"]/d["total"], 4),
                       "avg_time": round(sum(d["agent_times"])/len(d["agent_times"]), 2)},
        }

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Markdown 报告
    md = f"""# Agentic RAG vs 传统 RAG 压力测试报告

## 测试环境
- 文档数：102 篇 / 10 个技术领域
- 评估集：{total} 题 / 6 类意图 + 拒答
- LLM：deepseek-v4-flash
- Embedding：qwen3.7-text-embedding

## 核心对比

| 维度 | 传统 RAG | Agentic RAG | 提升 |
|------|----------|-------------|------|
| **综合正确率** | {trad_total_pass}/{total} ({trad_total_pass/total*100:.1f}%) | {agent_total_pass}/{total} ({agent_total_pass/total*100:.1f}%) | **+{(agent_total_pass-trad_total_pass)/total*100:.1f}pp** |
| **平均耗时** | {trad_avg:.1f}s | {agent_avg:.1f}s | {'快' if agent_avg < trad_avg else '慢'} {abs(trad_avg-agent_avg)/trad_avg*100:.1f}% |

## 分意图对比

| 意图 | 题数 | 传统正确率 | Agentic正确率 | 传统耗时 | Agentic耗时 |
|------|------|-----------|-------------|---------|------------|
"""
    for intent in ["simple", "factual", "explanatory", "comparative", "procedural", "exploratory", "out_of_scope"]:
        if intent in intents:
            d = intents[intent]
            t_rate = d["trad_pass"]/d["total"]*100
            a_rate = d["agent_pass"]/d["total"]*100
            t_time = sum(d["trad_times"])/len(d["trad_times"])
            a_time = sum(d["agent_times"])/len(d["agent_times"])
            md += f"| {intent} | {d['total']} | {d['trad_pass']}/{d['total']} ({t_rate:.0f}%) | {d['agent_pass']}/{d['total']} ({a_rate:.0f}%) | {t_time:.1f}s | {a_time:.1f}s |\n"

    md += f"""
## 逐题明细

| ID | 意图 | 传统 | Agentic | 传统耗时 | Agentic耗时 | 问题 |
|----|------|:----:|:-------:|--------:|-----------:|------|
"""
    for r in results:
        t = "✓" if r["traditional"]["passed"] else "✗"
        a = "✓" if r["agentic"]["passed"] else "✗"
        md += f"| {r['case_id']} | {r['intent']} | {t} | {a} | {r['traditional']['elapsed']}s | {r['agentic']['elapsed']}s | {r['query'][:30]} |\n"

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    # 打印汇总
    print("\n" + "=" * 60)
    print("COMPARISON TEST SUMMARY")
    print("=" * 60)
    print(f"Total: {total}")
    print(f"Traditional: {trad_total_pass}/{total} ({trad_total_pass/total*100:.1f}%)  avg={trad_avg:.1f}s")
    print(f"Agentic:     {agent_total_pass}/{total} ({agent_total_pass/total*100:.1f}%)  avg={agent_avg:.1f}s")
    print()
    for intent in ["simple", "factual", "explanatory", "comparative", "procedural", "exploratory", "out_of_scope"]:
        if intent in intents:
            d = intents[intent]
            print(f"  {intent:15s}: trad {d['trad_pass']}/{d['total']}  agent {d['agent_pass']}/{d['total']}")
    print()
    print(f"Report: {REPORT_MD}")
    print(f"JSON:   {REPORT_JSON}")


if __name__ == "__main__":
    asyncio.run(main())
