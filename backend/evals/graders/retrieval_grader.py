"""Retrieval keyword grader for RAG retrieval eval cases.

Grades based on:
- expected_keywords present in retrieved docs content
- forbidden_keywords absent from retrieved docs content
- expected_no_answer correctness (empty/low-relevance docs vs non-empty)
- min_retrieved_count / max_retrieved_count bounds (optional)
"""


def _combined_text(docs: list[dict]) -> str:
    """Combine content from all retrieved docs into a single string."""
    return " ".join(doc.get("content", "") for doc in docs)


def _contains_keyword(text: str, keyword: str) -> bool:
    """Case-insensitive check for English keywords, substring for Chinese."""
    # If keyword is purely ASCII (English), do case-insensitive match
    if keyword.isascii():
        return keyword.lower() in text.lower()
    # For non-ASCII (Chinese etc.), plain substring match
    return keyword in text


def grade(case: dict, result: dict) -> dict:
    """Grade a retrieval case based on mock or real retrieved docs.

    Args:
        case: The eval case dict.
        result: Dict with optional keys:
            - retrieved_docs (list[dict]): each has 'content' (str) and optional 'metadata'
            - Or None/empty — in which case mock_retrieved_docs from context_setup is used.

    Returns:
        dict with keys: passed (bool), score (float), details (dict)
    """
    # Get retrieved docs from result, falling back to case's mock_retrieved_docs
    retrieved_docs = result.get("retrieved_docs") or []
    if not retrieved_docs:
        retrieved_docs = case.get("context_setup", {}).get("mock_retrieved_docs", [])

    retrieved_count = len(retrieved_docs)
    combined = _combined_text(retrieved_docs)

    # --- expected_keywords check ---
    expected_keywords = case.get("expected_keywords", [])
    expected_found = [kw for kw in expected_keywords if _contains_keyword(combined, kw)]
    expected_missing = [kw for kw in expected_keywords if not _contains_keyword(combined, kw)]

    # --- forbidden_keywords check ---
    forbidden_keywords = case.get("forbidden_keywords", [])
    forbidden_found = [kw for kw in forbidden_keywords if _contains_keyword(combined, kw)]

    # --- expected_no_answer check ---
    expected_no_answer = case.get("expected_no_answer", False)
    no_answer_violations = []
    if expected_no_answer:
        # If expected no-answer but retrieved docs are non-empty and contain expected keywords, flag it
        if retrieved_count > 0 and len(expected_found) > 0:
            no_answer_violations.append(
                f"expected no-answer but got {retrieved_count} docs with {len(expected_found)} matching keywords"
            )
    else:
        # If not expected no-answer, should have at least 1 retrieved doc
        if retrieved_count == 0:
            no_answer_violations.append("expected retrieved docs but got 0")

    # --- min/max retrieved count check ---
    min_count = case.get("min_retrieved_count")
    max_count = case.get("max_retrieved_count")
    count_violations = []
    if min_count is not None and retrieved_count < min_count:
        count_violations.append(f"retrieved_count {retrieved_count} < min_retrieved_count {min_count}")
    if max_count is not None and retrieved_count > max_count:
        count_violations.append(f"retrieved_count {retrieved_count} > max_retrieved_count {max_count}")

    # --- scoring ---
    if expected_keywords:
        keyword_score = len(expected_found) / len(expected_keywords)
    else:
        keyword_score = 1.0

    penalty = len(forbidden_found) * 0.2
    score = max(0.0, keyword_score - penalty)

    # --- pass/fail ---
    # For expected_no_answer cases, missing expected_keywords is EXPECTED (docs are irrelevant)
    # so we only check forbidden_keywords, no_answer_violations, and count_violations
    if expected_no_answer:
        passed = (
            len(forbidden_found) == 0
            and len(no_answer_violations) == 0
            and len(count_violations) == 0
        )
    else:
        passed = (
            len(expected_missing) == 0
            and len(forbidden_found) == 0
            and len(no_answer_violations) == 0
            and len(count_violations) == 0
        )

    details = {
        "expected_keywords_found": expected_found,
        "expected_keywords_missing": expected_missing,
        "forbidden_keywords_found": forbidden_found,
        "retrieved_count": retrieved_count,
        "expected_no_answer": expected_no_answer,
        "no_answer_violations": no_answer_violations,
        "count_violations": count_violations,
    }

    return {
        "passed": passed,
        "score": round(score, 2),
        "grader_type": "retrieval_keyword",
        "details": details,
    }
