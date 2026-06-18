"""Keyword matching grader for RAG retrieval cases."""


def grade(case: dict, result: dict) -> dict:
    """Grade a case based on keyword presence in results.

    Args:
        case: The eval case dict.
        result: Dict with 'retrieved_text' (str) or 'retrieved_texts' (list[str]).

    Returns:
        dict with keys: passed (bool), score (float), details (str)
    """
    expected_keywords = case.get("expected_keywords", [])
    forbidden_keywords = case.get("forbidden_keywords", [])

    # Combine all retrieved text
    texts = result.get("retrieved_texts", [])
    if not texts and "retrieved_text" in result:
        texts = [result["retrieved_text"]]
    combined_text = " ".join(texts)

    # Check expected keywords
    found = [kw for kw in expected_keywords if kw in combined_text]
    missing = [kw for kw in expected_keywords if kw not in combined_text]

    # Check forbidden keywords
    forbidden_found = [kw for kw in forbidden_keywords if kw in combined_text]

    # Score: ratio of expected keywords found, penalized by forbidden keywords
    if expected_keywords:
        base_score = len(found) / len(expected_keywords)
    else:
        base_score = 1.0

    penalty = len(forbidden_found) * 0.2
    score = max(0.0, base_score - penalty)

    passed = len(missing) == 0 and len(forbidden_found) == 0

    details_parts = []
    if found:
        details_parts.append(f"found: {found}")
    if missing:
        details_parts.append(f"missing: {missing}")
    if forbidden_found:
        details_parts.append(f"forbidden_found: {forbidden_found}")

    return {
        "passed": passed,
        "score": round(score, 2),
        "details": "; ".join(details_parts) or "no keywords specified",
    }
