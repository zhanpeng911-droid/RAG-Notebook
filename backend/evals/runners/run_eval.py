"""Agent Eval Runner — Notebook

Usage:
    cd backend
    python -m evals.runners.run_eval --dry-run
    python -m evals.runners.run_eval --mock --category rag_retrieval
    python -m evals.runners.run_eval --mock --category agent_tool
    python -m evals.runners.run_eval --mock
    python -m evals.runners.run_eval --llm-smoke --category answer_quality --limit 4
    python -m evals.runners.run_eval --real

Dry-run mode verifies:
    - Case files are loadable and parseable JSONL
    - Case schemas pass validation (required fields, valid types)
    - Registered graders can be invoked without crashing
    Dry-run does NOT evaluate Agent behavior, RAG retrieval quality,
    tool call correctness, or LLM answer quality.

Mock mode evaluates:
    - RAG retrieval cases using mock_retrieved_docs from case context_setup
    - Agent tool-call cases using mock_tool_calls from case context_setup
    - Produces real behavior_evaluated / behavior_passed / behavior_failed
    Does NOT call real LLM, ChromaDB, or Agent.

LLM-smoke mode:
    - Requires EVAL_ENABLE_LLM=true environment variable
    - Calls real LLM for answer_quality cases
    - Limited to --limit cases (default 4)
    - Records model info, temperature, success/failure
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add backend to path so imports work
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from evals.graders.schema_validator import validate_case
from evals.graders.keyword_grader import grade as keyword_grade
from evals.graders.tool_call_grader import grade as tool_call_grade
from evals.graders.forbidden_content_grader import grade as forbidden_content_grade
from evals.graders.retrieval_grader import grade as retrieval_grade
from evals.graders.answer_quality_grader import grade as answer_quality_grade

CASES_DIR = Path(__file__).resolve().parent.parent / "cases"
REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

CATEGORY_FILES = {
    "rag_retrieval": "rag_retrieval_cases.jsonl",
    "agent_tool": "agent_tool_cases.jsonl",
    "safety": "safety_cases.jsonl",
    "answer_quality": "answer_quality_cases.jsonl",
}

GRADER_MAP = {
    "keyword": keyword_grade,
    "tool_call": tool_call_grade,
    "forbidden_content": forbidden_content_grade,
    "retrieval_keyword": retrieval_grade,
    "answer_quality": answer_quality_grade,
}

# Categories that support mock mode (have mock data in context_setup)
MOCK_SUPPORTED_CATEGORIES = {"rag_retrieval", "agent_tool"}


def load_cases(category: str | None = None) -> list[dict]:
    """Load eval cases from JSONL files."""
    cases = []
    categories = [category] if category else CATEGORY_FILES.keys()

    for cat in categories:
        filename = CATEGORY_FILES.get(cat)
        if not filename:
            continue
        filepath = CASES_DIR / filename
        if not filepath.exists():
            continue
        with open(filepath, encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    case = json.loads(line)
                    case["_source_file"] = filename
                    case["_source_line"] = line_no
                    cases.append(case)
                except json.JSONDecodeError as e:
                    print(f"  WARNING: invalid JSON in {filename}:{line_no}: {e}")
    return cases


def validate_all_cases(cases: list[dict]) -> tuple[list[dict], list[dict]]:
    """Validate all cases. Returns (valid_cases, validation_errors)."""
    valid = []
    errors = []
    for case in cases:
        result = validate_case(case)
        if result["valid"]:
            valid.append(case)
        else:
            errors.append({
                "id": case.get("id", "unknown"),
                "source": f"{case.get('_source_file', '?')}:{case.get('_source_line', '?')}",
                "errors": result["errors"],
            })
    return valid, errors


def dry_run(cases: list[dict]) -> dict:
    """Dry-run: validate schemas and verify grader plumbing without evaluating behavior."""
    valid_cases, validation_errors = validate_all_cases(cases)

    requires_llm_skipped = sum(1 for c in valid_cases if c.get("requires_llm", False))
    human_review_skipped = sum(1 for c in valid_cases if c.get("requires_human_review", False))
    executable = [c for c in valid_cases if not c.get("requires_llm", False) and not c.get("requires_human_review", False)]

    results = []
    for case in executable:
        grader_type = case.get("grader_type", "")
        grader_fn = GRADER_MAP.get(grader_type)
        if not grader_fn:
            results.append({
                "id": case["id"],
                "status": "no_grader",
                "reason": f"no grader registered for type: {grader_type}",
            })
            continue

        try:
            grader_fn(case, {})
            results.append({
                "id": case["id"],
                "status": "plumbing_ok",
                "grader": grader_type,
            })
        except Exception as e:
            results.append({
                "id": case["id"],
                "status": "grader_error",
                "error": str(e),
            })

    schema_valid_count = len(valid_cases)
    dry_run_runnable_count = sum(1 for r in results if r["status"] == "plumbing_ok")
    grader_error_count = sum(1 for r in results if r["status"] == "grader_error")
    no_grader_count = sum(1 for r in results if r["status"] == "no_grader")

    return {
        "mode": "dry-run",
        "mode_disclaimer": (
            "Dry-run verifies case schemas and grader plumbing only. "
            "It does NOT evaluate Agent behavior, RAG retrieval quality, "
            "tool call correctness, or LLM answer quality."
        ),
        "total_cases_loaded": len(cases),
        "schema_valid": schema_valid_count,
        "schema_invalid": len(validation_errors),
        "validation_errors": validation_errors,
        "dry_run_runnable": dry_run_runnable_count,
        "behavior_evaluated": 0,
        "behavior_passed": 0,
        "behavior_failed": 0,
        "skipped_requires_llm": requires_llm_skipped,
        "skipped_human_review": human_review_skipped,
        "skipped_no_grader": no_grader_count,
        "grader_errors": grader_error_count,
        "results": results,
    }


def mock_run(cases: list[dict]) -> dict:
    """Mock mode: evaluate behavior using mock data from case context_setup.

    Supports:
    - rag_retrieval: uses mock_retrieved_docs + retrieval_grader
    - agent_tool: uses mock_tool_calls + tool_call_grader
    Does NOT call real LLM, ChromaDB, or Agent.
    """
    valid_cases, validation_errors = validate_all_cases(cases)

    requires_llm_skipped = sum(1 for c in valid_cases if c.get("requires_llm", False))
    human_review_skipped = sum(1 for c in valid_cases if c.get("requires_human_review", False))

    results = []
    behavior_evaluated = 0
    behavior_passed = 0
    behavior_failed = 0
    grader_error_count = 0

    for case in valid_cases:
        case_id = case["id"]
        category = case.get("category", "")
        grader_type = case.get("grader_type", "")
        grader_fn = GRADER_MAP.get(grader_type)

        if case.get("requires_llm", False):
            results.append({
                "id": case_id,
                "status": "skipped",
                "reason": "requires_llm",
                "grader": grader_type,
            })
            continue

        if case.get("requires_human_review", False):
            results.append({
                "id": case_id,
                "status": "skipped",
                "reason": "requires_human_review",
                "grader": grader_type,
            })
            continue

        if not grader_fn:
            results.append({
                "id": case_id,
                "status": "skipped",
                "reason": f"no grader for type: {grader_type}",
            })
            continue

        # Check if category supports mock mode
        if category not in MOCK_SUPPORTED_CATEGORIES:
            results.append({
                "id": case_id,
                "status": "skipped",
                "reason": f"category '{category}' does not support mock mode",
                "grader": grader_type,
            })
            continue

        # Build mock result based on category
        mock_result = {}
        if category == "rag_retrieval":
            mock_docs = case.get("context_setup", {}).get("mock_retrieved_docs")
            if mock_docs is None:
                results.append({
                    "id": case_id,
                    "status": "skipped",
                    "reason": "no mock_retrieved_docs in context_setup",
                    "grader": grader_type,
                })
                continue
            mock_result = {"retrieved_docs": mock_docs or []}
        elif category == "agent_tool":
            mock_tool_calls = case.get("context_setup", {}).get("mock_tool_calls")
            if mock_tool_calls is None:
                results.append({
                    "id": case_id,
                    "status": "skipped",
                    "reason": "no mock_tool_calls in context_setup",
                    "grader": grader_type,
                })
                continue
            mock_result = {"tool_calls": mock_tool_calls or []}

        try:
            grade_result = grader_fn(case, mock_result)
            behavior_evaluated += 1
            if grade_result["passed"]:
                behavior_passed += 1
                status = "passed"
            else:
                behavior_failed += 1
                status = "failed"
            results.append({
                "id": case_id,
                "status": status,
                "grader": grader_type,
                "score": grade_result.get("score", 0),
                "details": grade_result.get("details", {}),
            })
        except Exception as e:
            grader_error_count += 1
            results.append({
                "id": case_id,
                "status": "grader_error",
                "error": str(e),
                "grader": grader_type,
            })

    schema_valid_count = len(valid_cases)

    return {
        "mode": "mock",
        "mode_disclaimer": (
            "Mock mode evaluates behavior using case自带 mock data. "
            "It does NOT call real LLM, ChromaDB, or Agent. "
            "Mock pass does NOT equal real behavior pass."
        ),
        "total_cases_loaded": len(cases),
        "schema_valid": schema_valid_count,
        "schema_invalid": len(validation_errors),
        "validation_errors": validation_errors,
        "behavior_evaluated": behavior_evaluated,
        "behavior_passed": behavior_passed,
        "behavior_failed": behavior_failed,
        "skipped_requires_llm": requires_llm_skipped,
        "skipped_human_review": human_review_skipped,
        "grader_errors": grader_error_count,
        "results": results,
    }


def _get_llm_config():
    """Get LLM config from project factory. Returns (model, model_name, provider) or (None, None, None)."""
    try:
        from app.config.validator import get_settings
        settings = get_settings()
        provider = settings.LLM_TYPE  # ALIYUN, OLLAMA, or OPENAI

        # Get model name based on provider
        if provider == "ALIYUN":
            model_name = getattr(settings, "DASHSCOPE_MODEL_NAME", "qwen-plus")
        elif provider == "OLLAMA":
            model_name = getattr(settings, "OLLAMA_MODEL", "qwen3:8b")
        elif provider == "OPENAI":
            model_name = getattr(settings, "OPENAI_MODEL", "gpt-3.5-turbo")
        else:
            return None, None, None

        # Try to create chat model using factory
        from app.utils.factory import create_chat_model_from_settings
        model = create_chat_model_from_settings()
        return model, model_name, provider
    except Exception as e:
        print(f"  WARNING: Could not initialize LLM: {e}")
        return None, None, None


def llm_smoke_run(cases: list[dict], limit: int = 4) -> dict:
    """LLM smoke mode: run a small number of real LLM cases.

    Requires EVAL_ENABLE_LLM=true environment variable.
    """
    llm_enabled = os.environ.get("EVAL_ENABLE_LLM", "").lower() == "true"

    valid_cases, validation_errors = validate_all_cases(cases)

    # Filter to answer_quality cases that require LLM
    llm_cases = [c for c in valid_cases if c.get("requires_llm", False) and c.get("category") == "answer_quality"]
    llm_cases = llm_cases[:limit]

    results = []
    llm_behavior_evaluated = 0
    llm_behavior_passed = 0
    llm_behavior_failed = 0
    llm_cases_skipped = 0
    grader_error_count = 0

    model_info = {
        "provider": None,
        "model_name": None,
        "temperature": None,
        "max_tokens": None,
    }

    if not llm_enabled:
        # Skip all LLM cases
        for case in llm_cases:
            results.append({
                "id": case["id"],
                "status": "skipped",
                "reason": "EVAL_ENABLE_LLM is not true",
                "grader": case.get("grader_type", ""),
            })
            llm_cases_skipped += 1
    else:
        # Initialize LLM
        model, model_name, provider = _get_llm_config()
        model_info["provider"] = provider
        model_info["model_name"] = model_name

        if model is None:
            for case in llm_cases:
                results.append({
                    "id": case["id"],
                    "status": "skipped",
                    "reason": "LLM initialization failed",
                    "grader": case.get("grader_type", ""),
                })
                llm_cases_skipped += 1
        else:
            for case in llm_cases:
                case_id = case["id"]
                temperature = case.get("temperature", 0)
                max_tokens = case.get("max_tokens", 256)
                model_info["temperature"] = temperature
                model_info["max_tokens"] = max_tokens

                try:
                    # Build prompt from case
                    context = case.get("context_setup", {}).get("context", "")
                    user_input = case.get("user_input", "")

                    if context:
                        prompt = f"参考上下文：\n{context}\n\n用户问题：{user_input}\n\n请基于参考上下文回答用户问题。"
                    else:
                        prompt = user_input

                    # Call real LLM
                    start_time = time.time()
                    try:
                        from langchain_core.messages import HumanMessage
                        response = model.invoke([HumanMessage(content=prompt)])
                        answer = response.content
                    except Exception as e:
                        # Try alternative invocation
                        answer = model.invoke(prompt)
                        if hasattr(answer, 'content'):
                            answer = answer.content
                    elapsed = time.time() - start_time

                    # Grade the answer using deterministic pre-check
                    grader_fn = GRADER_MAP.get(case.get("grader_type", ""))
                    if grader_fn:
                        grade_result = grader_fn(case, {"answer": answer})
                        llm_behavior_evaluated += 1
                        if grade_result["passed"]:
                            llm_behavior_passed += 1
                            status = "passed"
                        else:
                            llm_behavior_failed += 1
                            status = "failed"
                        results.append({
                            "id": case_id,
                            "status": status,
                            "grader": case.get("grader_type", ""),
                            "score": grade_result.get("score", 0),
                            "details": grade_result.get("details", {}),
                            "llm_answer_preview": answer[:200] + "..." if len(answer) > 200 else answer,
                            "elapsed_seconds": round(elapsed, 2),
                        })
                    else:
                        grader_error_count += 1
                        results.append({
                            "id": case_id,
                            "status": "grader_error",
                            "error": f"no grader for type: {case.get('grader_type', '')}",
                            "grader": case.get("grader_type", ""),
                        })
                except Exception as e:
                    grader_error_count += 1
                    results.append({
                        "id": case_id,
                        "status": "grader_error",
                        "error": str(e),
                        "grader": case.get("grader_type", ""),
                    })

    schema_valid_count = len(valid_cases)

    return {
        "mode": "llm-smoke",
        "mode_disclaimer": (
            "LLM-smoke mode calls real LLM for a small number of cases. "
            "Results are NOT representative of overall system quality. "
            "Small sample results should NOT be reported as accuracy metrics."
        ),
        "llm_eval_enabled": llm_enabled,
        "total_cases_loaded": len(cases),
        "schema_valid": schema_valid_count,
        "schema_invalid": len(validation_errors),
        "validation_errors": validation_errors,
        "llm_cases_discovered": len(llm_cases),
        "llm_cases_executed": llm_behavior_evaluated,
        "llm_behavior_passed": llm_behavior_passed,
        "llm_behavior_failed": llm_behavior_failed,
        "llm_cases_skipped": llm_cases_skipped,
        "grader_errors": grader_error_count,
        "model_info": model_info,
        "results": results,
    }


def save_report(report: dict) -> Path:
    """Save report to reports directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"eval_report_{timestamp}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report_path


def print_dry_run_report(report: dict) -> None:
    """Print dry-run report summary."""
    print("\n" + "=" * 60)
    print("Agent Eval Report (dry-run mode)")
    print("=" * 60)
    print(f"  Total cases loaded:       {report['total_cases_loaded']}")
    print(f"  Schema valid:             {report['schema_valid']}")
    print(f"  Schema invalid:           {report['schema_invalid']}")
    print(f"  Runnable in dry-run:      {report['dry_run_runnable']}")
    print(f"  Behavior evaluated:       {report['behavior_evaluated']}  (dry-run: no behavior evaluated)")
    print(f"  Behavior passed:          {report['behavior_passed']}  (dry-run: no behavior evaluated)")
    print(f"  Behavior failed:          {report['behavior_failed']}  (dry-run: no behavior evaluated)")
    print(f"  Skipped (requires LLM):   {report['skipped_requires_llm']}")
    print(f"  Skipped (human review):   {report['skipped_human_review']}")
    print(f"  Skipped (no grader):      {report['skipped_no_grader']}")
    print(f"  Grader errors:            {report['grader_errors']}")

    if report["validation_errors"]:
        print("\nValidation Errors:")
        for err in report["validation_errors"]:
            print(f"  [{err['id']}] {err['source']}: {err['errors']}")

    if report["results"]:
        print("\nResults (plumbing check only -- no behavior evaluated):")
        for r in report["results"]:
            status_label = {
                "plumbing_ok": "PLUMBING_OK",
                "grader_error": "GRADER_ERR",
                "no_grader": "NO_GRADER",
            }.get(r["status"], r["status"].upper())
            extra = f" ({r.get('reason', r.get('error', ''))})" if r["status"] in ("no_grader", "grader_error") else ""
            print(f"  {status_label}: {r['id']}{extra}")

    print("=" * 60)
    print("NOTE: Dry-run does not prove Agent/RAG behavior quality.")
    print("      Use --mock for mock behavior evaluation.")
    print("=" * 60)


def print_mock_report(report: dict) -> None:
    """Print mock mode report summary."""
    print("\n" + "=" * 60)
    print("Agent Eval Report (mock mode)")
    print("=" * 60)
    print(f"  Total cases loaded:       {report['total_cases_loaded']}")
    print(f"  Schema valid:             {report['schema_valid']}")
    print(f"  Schema invalid:           {report['schema_invalid']}")
    print(f"  Behavior evaluated:       {report['behavior_evaluated']}")
    print(f"  Behavior passed:          {report['behavior_passed']}")
    print(f"  Behavior failed:          {report['behavior_failed']}")
    print(f"  Skipped (requires LLM):   {report['skipped_requires_llm']}")
    print(f"  Skipped (human review):   {report['skipped_human_review']}")
    print(f"  Grader errors:            {report['grader_errors']}")

    if report["validation_errors"]:
        print("\nValidation Errors:")
        for err in report["validation_errors"]:
            print(f"  [{err['id']}] {err['source']}: {err['errors']}")

    if report["results"]:
        print("\nResults:")
        for r in report["results"]:
            status_label = {
                "passed": "PASS",
                "failed": "FAIL",
                "skipped": "SKIP",
                "grader_error": "GRADER_ERR",
            }.get(r["status"], r["status"].upper())
            extra = ""
            if r["status"] == "skipped":
                extra = f" ({r.get('reason', '')})"
            elif r["status"] == "failed":
                details = r.get("details", {})
                missing = details.get("expected_tools_missing", []) or details.get("expected_keywords_missing", [])
                forbidden = details.get("forbidden_tools_found", []) or details.get("forbidden_keywords_found", [])
                parts = []
                if missing:
                    parts.append(f"missing: {missing}")
                if forbidden:
                    parts.append(f"forbidden: {forbidden}")
                extra = f" ({'; '.join(parts)})" if parts else ""
            elif r["status"] == "grader_error":
                extra = f" ({r.get('error', '')})"
            print(f"  {status_label}: {r['id']}{extra}")

    print("=" * 60)
    print("NOTE: Mock mode uses case自带 mock data.")
    print("      Mock pass does NOT equal real behavior pass.")
    print("=" * 60)


def print_llm_smoke_report(report: dict) -> None:
    """Print LLM smoke mode report summary."""
    print("\n" + "=" * 60)
    print("Agent Eval Report (LLM smoke mode)")
    print("=" * 60)
    print(f"  LLM eval enabled:         {report['llm_eval_enabled']}")
    print(f"  Total cases loaded:       {report['total_cases_loaded']}")
    print(f"  Schema valid:             {report['schema_valid']}")
    print(f"  LLM cases discovered:     {report['llm_cases_discovered']}")
    print(f"  LLM cases executed:       {report['llm_cases_executed']}")
    print(f"  LLM behavior passed:      {report['llm_behavior_passed']}")
    print(f"  LLM behavior failed:      {report['llm_behavior_failed']}")
    print(f"  LLM cases skipped:        {report['llm_cases_skipped']}")
    print(f"  Grader errors:            {report['grader_errors']}")

    mi = report.get("model_info", {})
    print(f"  Model provider:           {mi.get('provider', 'N/A')}")
    print(f"  Model name:               {mi.get('model_name', 'N/A')}")
    print(f"  Temperature:              {mi.get('temperature', 'N/A')}")
    print(f"  Max tokens:               {mi.get('max_tokens', 'N/A')}")

    if report["validation_errors"]:
        print("\nValidation Errors:")
        for err in report["validation_errors"]:
            print(f"  [{err['id']}] {err['source']}: {err['errors']}")

    if report["results"]:
        print("\nResults:")
        for r in report["results"]:
            status_label = {
                "passed": "PASS",
                "failed": "FAIL",
                "skipped": "SKIP",
                "grader_error": "GRADER_ERR",
            }.get(r["status"], r["status"].upper())
            extra = ""
            if r["status"] == "skipped":
                extra = f" ({r.get('reason', '')})"
            elif r["status"] == "passed":
                preview = r.get("llm_answer_preview", "")
                elapsed = r.get("elapsed_seconds", "")
                extra = f" ({elapsed}s) {preview[:80]}..." if preview else f" ({elapsed}s)"
            elif r["status"] == "failed":
                details = r.get("details", {})
                missing = details.get("expected_keywords_missing", [])
                forbidden = details.get("forbidden_keywords_found", [])
                refusal_v = details.get("refusal_violations", [])
                parts = []
                if missing:
                    parts.append(f"missing: {missing}")
                if forbidden:
                    parts.append(f"forbidden: {forbidden}")
                if refusal_v:
                    parts.append(f"refusal: {refusal_v}")
                extra = f" ({'; '.join(parts)})" if parts else ""
            elif r["status"] == "grader_error":
                extra = f" ({r.get('error', '')})"
            print(f"  {status_label}: {r['id']}{extra}")

    print("=" * 60)
    if not report["llm_eval_enabled"]:
        print("NOTE: LLM eval is DISABLED. Set EVAL_ENABLE_LLM=true to enable.")
    else:
        print("NOTE: LLM smoke results are from a SMALL SAMPLE.")
        print("      Do NOT report as accuracy metrics.")
        print("      Do NOT use for production quality decisions.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Agent Eval Runner")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Validate schemas and grader plumbing only (no behavior evaluation)")
    mode.add_argument("--mock", action="store_true", help="Run with mock data (no real LLM)")
    mode.add_argument("--llm-smoke", action="store_true", help="Run small LLM smoke test (requires EVAL_ENABLE_LLM=true)")
    mode.add_argument("--real", action="store_true", help="Run with real LLM calls (requires API key)")
    parser.add_argument("--category", type=str, default=None, help="Run only specific category")
    parser.add_argument("--limit", type=int, default=4, help="Max cases for llm-smoke mode")
    parser.add_argument("--report", action="store_true", help="Save report to file")

    args = parser.parse_args()

    print("Loading eval cases...")
    cases = load_cases(args.category)
    print(f"  Loaded {len(cases)} cases")

    if args.dry_run:
        print("\nRunning dry-run (schema validation + grader plumbing check)...")
        print("  NOTE: This does NOT evaluate Agent behavior, RAG retrieval, or LLM quality.")
        report = dry_run(cases)
        print_dry_run_report(report)
    elif args.mock:
        print("\nRunning mock mode (evaluating with mock data)...")
        print("  NOTE: This does NOT call real LLM, ChromaDB, or Agent.")
        report = mock_run(cases)
        print_mock_report(report)
    elif args.llm_smoke:
        print("\nRunning LLM smoke mode...")
        print("  NOTE: This calls real LLM. Requires EVAL_ENABLE_LLM=true.")
        report = llm_smoke_run(cases, limit=args.limit)
        print_llm_smoke_report(report)
    elif args.real:
        print("\nReal mode not yet implemented. Use --llm-smoke for now.")
        sys.exit(1)

    if args.report:
        report_path = save_report(report)
        print(f"\nReport saved to: {report_path}")

    # Exit with error code if there are failures
    if report.get("schema_invalid", 0) > 0 or report.get("grader_errors", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
