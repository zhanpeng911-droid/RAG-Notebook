"""
Agentic RAG 计时测试 -- 27 道题逐题调用，记录耗时。

用法：
  cd backend
  uv run python -m evals.runners.run_timing_test
"""
import json
import time
import httpx
import os
import sys

# ===== 配置 =====
BACKEND_URL = "http://127.0.0.1:8002"
# 从 .env 读取 JWT token（需要已登录用户的 token）
# 也可以直接设置环境变量 TIMING_TEST_TOKEN
TOKEN = os.getenv("TIMING_TEST_TOKEN", "")
LLM_CONFIG = None  # None = 用后端默认模型

# 从对比报告中提取 27 道题
REPORT_PATH = os.path.join(os.path.dirname(__file__), "..", "reports", "comparison_report.json")

def load_cases():
    with open(REPORT_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["details"]

def run_single_query(client, query, token):
    """调用 Agentic RAG 非流式接口，返回 (answer, elapsed_seconds)"""
    url = f"{BACKEND_URL}/api/v1/chat/agent/query"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "query": query,
        "scope": "knowledge",
    }
    if LLM_CONFIG:
        payload["llm_config"] = LLM_CONFIG

    start = time.time()
    try:
        resp = client.post(url, json=payload, headers=headers, timeout=120)
        elapsed = time.time() - start
        if resp.status_code == 200:
            data = resp.json()
            answer = data.get("answer", data.get("data", {}).get("answer", ""))
            return answer, elapsed, None
        else:
            return "", elapsed, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        elapsed = time.time() - start
        return "", elapsed, str(e)

def main():
    cases = load_cases()
    print(f" loaded {len(cases)} test cases")
    print(f" Backend: {BACKEND_URL}")
    print(f" Token: {'provided' if TOKEN else 'NONE (will try without auth)'}")
    print()

    # 先检查后端是否在线
    try:
        with httpx.Client(timeout=5) as check:
            r = check.get(f"{BACKEND_URL}/docs")
            if r.status_code != 200:
                print(f"ERROR: Backend not ready (status {r.status_code})")
                sys.exit(1)
    except Exception as e:
        print(f"ERROR: Cannot connect to backend: {e}")
        sys.exit(1)

    print(" Backend is online. Starting timing test...\n")

    results = []
    total_time = 0

    with httpx.Client(timeout=120) as client:
        for i, case in enumerate(cases):
            cid = case["case_id"]
            query = case["query"]
            print(f"[{i+1}/{len(cases)}] {cid}: {query[:50]}...", end="", flush=True)

            answer, elapsed, error = run_single_query(client, query, TOKEN)
            total_time += elapsed

            status = "ERROR" if error else ("PASS" if answer else "EMPTY")
            print(f" -> {status} ({elapsed:.1f}s)")

            results.append({
                "case_id": cid,
                "query": query,
                "category": case.get("category", ""),
                "elapsed": round(elapsed, 2),
                "answer_preview": answer[:150] if answer else "",
                "error": error,
                "expected_refusal": case.get("expected_refusal", False),
            })

            # 每题之间等 7 秒，避免触发限流（60 秒窗口内最多 10 次）
            if i < len(cases) - 1:
                time.sleep(7)

    # 汇总
    valid_times = [r["elapsed"] for r in results if not r["error"]]
    avg_time = sum(valid_times) / len(valid_times) if valid_times else 0
    min_time = min(valid_times) if valid_times else 0
    max_time = max(valid_times) if valid_times else 0

    print("\n" + "=" * 60)
    print("TIMING TEST SUMMARY")
    print("=" * 60)
    print(f"Total cases:  {len(cases)}")
    print(f"Success:      {len(valid_times)}")
    print(f"Errors:       {len(cases) - len(valid_times)}")
    print(f"Total time:   {total_time:.1f}s")
    print(f"Avg time:     {avg_time:.1f}s")
    print(f"Min time:     {min_time:.1f}s")
    print(f"Max time:     {max_time:.1f}s")
    print()
    print("Previous report avg: 13.4s (Agentic RAG)")
    print(f"Current avg:          {avg_time:.1f}s")
    print()

    # 逐题明细
    print("-" * 60)
    print(f"{'ID':<10} {'Time':>8}  {'Status':<8}  Query")
    print("-" * 60)
    for r in results:
        status = "ERROR" if r["error"] else "OK"
        print(f"{r['case_id']:<10} {r['elapsed']:>6.1f}s  {status:<8}  {r['query'][:40]}")

    # 保存结果
    output_path = os.path.join(os.path.dirname(__file__), "..", "reports", "timing_test_result.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_cases": len(cases),
                "success": len(valid_times),
                "errors": len(cases) - len(valid_times),
                "total_time": round(total_time, 1),
                "avg_time": round(avg_time, 1),
                "min_time": round(min_time, 1),
                "max_time": round(max_time, 1),
                "previous_avg": 13.4,
            },
            "details": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
