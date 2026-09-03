"""
边界情况测试 —— 超长文档 / 跨文档关联 / 模糊查询 / 新旧冲突

用法:
  cd backend
  uv run python -m evals.runners.run_edge_test
"""
import json
import time
import asyncio
import os

USER_ID = "6FLQT8EZB8AbgypHLkeEwk"
CASES_FILE = os.path.join(os.path.dirname(__file__), "..", "cases", "edge_test_cases.jsonl")
REPORT_MD = os.path.join(os.path.dirname(__file__), "..", "reports", "edge_test_report.md")
REPORT_JSON = os.path.join(os.path.dirname(__file__), "..", "reports", "edge_test_report.json")
INTERVAL = 3

REFUSAL_KEYWORDS = ["抱歉", "无法", "不能", "没有找到", "不在", "不包含", "sorry", "cannot", "unable", "未找到"]

def load_cases():
    cases = []
    with open(CASES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases

def check_keywords(answer, keywords):
    """检查答案是否包含期望关键词（中文归一化 + 同义词匹配）"""
    if not keywords:
        return True, []

    # 同义词映射：期望词 -> 常见改写
    SYNONYMS = {
        "不是越多越好": ["并非越多越好", "不是越多越好", "并非越多", "不宜过多"],
        "写开销": ["写操作", "写入开销", "写开销", "写操作的开销"],
    }

    def _norm(s):
        return s.replace(" ", "").replace("\u3000", "")

    answer_norm = _norm(answer.lower())
    missing = []
    for kw in keywords:
        variants = [kw] + SYNONYMS.get(kw, [])
        matched = any(_norm(v.lower()) in answer_norm for v in variants)
        if not matched:
            missing.append(kw)
    return len(missing) == 0, missing

def check_refusal(answer):
    if not answer or len(answer) < 5:
        return True
    answer_lower = answer.lower()
    for kw in REFUSAL_KEYWORDS:
        if kw.lower() in answer_lower:
            return True
    return False

def score_answer(answer, case):
    if case.get("expected_refusal"):
        refused = check_refusal(answer)
        return refused, 1.0 if refused else 0.0, {"refused": refused}

    keywords = case.get("expected_keywords", [])
    if not keywords:
        return True, 1.0, {"no_keywords": True}

    found, missing = check_keywords(answer, keywords)
    score = (len(keywords) - len(missing)) / len(keywords)
    passed = score >= 0.5
    return passed, score, {"found": len(keywords) - len(missing), "total": len(keywords), "missing": missing}

async def agentic_rag(query, user_id):
    from app.agentic.graph import run_agent
    start = time.time()
    try:
        result = await run_agent(query=query, user_id=user_id)
        evidence_count = 0
        for phase in result.get("phases", []):
            state = phase.get("state", {})
            if state.get("evidence_count", 0) > evidence_count:
                evidence_count = state["evidence_count"]
        return {
            "answer": result.get("answer", ""),
            "evidence_count": evidence_count,
            "elapsed": time.time() - start,
            "error": result.get("error"),
        }
    except Exception as e:
        return {"answer": "", "evidence_count": 0, "elapsed": time.time() - start, "error": str(e)[:100]}

async def clear_llm_cache():
    try:
        from app.db.redis_config import get_redis_client
        redis = await get_redis_client()
        if redis:
            keys = await redis.keys("llm:*")
            if keys:
                await redis.delete(*keys)
    except Exception:
        pass

async def main():
    cases = load_cases()
    print(f"Loaded {len(cases)} edge test cases")
    print(f"User ID: {USER_ID}")
    print()

    results = []
    for i, case in enumerate(cases):
        cid = case["id"]
        etype = case["edge_type"]
        query = case["query"]
        print(f"[{i+1}/{len(cases)}] {cid} ({etype}): {query[:40]}...", flush=True)

        await clear_llm_cache()
        agent = await agentic_rag(query, USER_ID)
        passed, score, details = score_answer(agent["answer"], case)
        print(f"  Agentic: {score:.0%} ({agent['elapsed']:.1f}s) {'✓' if passed else '✗'}", flush=True)

        results.append({
            "case_id": cid,
            "edge_type": etype,
            "query": query,
            "expected_keywords": case.get("expected_keywords", []),
            "note": case.get("note", ""),
            "answer_preview": agent["answer"][:300] if agent["answer"] else "",
            "elapsed": round(agent["elapsed"], 2),
            "evidence_count": agent["evidence_count"],
            "passed": passed,
            "score": round(score, 2),
            "details": details,
            "error": agent["error"],
        })

        await asyncio.sleep(INTERVAL)

    generate_report(results, cases)

def generate_report(results, cases):
    types = {}
    for r in results:
        t = r["edge_type"]
        if t not in types:
            types[t] = {"total": 0, "pass": 0, "times": []}
        d = types[t]
        d["total"] += 1
        if r["passed"]:
            d["pass"] += 1
        d["times"].append(r["elapsed"])

    total = len(results)
    total_pass = sum(1 for r in results if r["passed"])
    avg_time = sum(r["elapsed"] for r in results) / total if total else 0

    report = {
        "summary": {
            "total_cases": total,
            "pass": total_pass,
            "rate": round(total_pass / total, 4),
            "avg_time": round(avg_time, 2),
        },
        "by_edge_type": {},
        "details": results,
    }
    for t, d in types.items():
        report["by_edge_type"][t] = {
            "total": d["total"],
            "pass": d["pass"],
            "rate": round(d["pass"] / d["total"], 4),
            "avg_time": round(sum(d["times"]) / len(d["times"]), 2),
        }

    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    md = f"""# 边界情况测试报告

## 测试环境
- 边界文档：10 篇（超长 1 篇 99KB / 跨文档 3 篇 / 模糊 2 篇 / 新旧冲突 4 篇）
- 评估集：{total} 题
- LLM：deepseek-v4-flash / 重排序：qwen3.7-text-rerank

## 汇总

| 指标 | 值 |
|------|-----|
| 总题数 | {total} |
| 通过 | {total_pass}/{total} ({total_pass/total*100:.1f}%) |
| 平均耗时 | {avg_time:.1f}s |

## 分边界类型

| 类型 | 题数 | 通过 | 通过率 | 平均耗时 |
|------|------|------|--------|---------|
"""
    for t in ["long_doc", "cross_doc", "ambiguous", "conflict"]:
        if t in types:
            d = types[t]
            md += f"| {t} | {d['total']} | {d['pass']}/{d['total']} | {d['pass']/d['total']*100:.0f}% | {d['times'][0] if d['times'] else 0:.1f}s |\n"

    md += """
## 逐题明细

| ID | 类型 | 结果 | 耗时 | 关键词命中 | 问题 |
|----|------|:----:|-----:|-----------|------|
"""
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        kw = r["details"].get("found", "-") if isinstance(r["details"], dict) else "-"
        md += f"| {r['case_id']} | {r['edge_type']} | {mark} | {r['elapsed']}s | {kw} | {r['query'][:30]} |\n"

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md)

    print("\n" + "=" * 60)
    print("EDGE TEST SUMMARY")
    print("=" * 60)
    print(f"Total: {total}")
    print(f"Pass:  {total_pass}/{total} ({total_pass/total*100:.1f}%)")
    print(f"Avg:   {avg_time:.1f}s")
    print()
    for t in ["long_doc", "cross_doc", "ambiguous", "conflict"]:
        if t in types:
            d = types[t]
            print(f"  {t:12s}: {d['pass']}/{d['total']} ({d['pass']/d['total']*100:.0f}%)")
    print()
    print(f"Report: {REPORT_MD}")

if __name__ == "__main__":
    asyncio.run(main())
