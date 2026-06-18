"""Case schema validator grader."""

REQUIRED_FIELDS = ["id", "category", "user_input", "success_criteria", "grader_type"]
VALID_CATEGORIES = {"rag_retrieval", "agent_tool", "safety", "no_answer", "user_isolation", "answer_quality"}
VALID_GRADER_TYPES = {"keyword", "retrieval_keyword", "tool_call", "forbidden_content", "model", "human_review", "answer_quality"}


def validate_case(case: dict) -> dict:
    """Validate a single eval case against the schema.

    Returns:
        dict with keys: valid (bool), errors (list[str])
    """
    errors = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in case:
            errors.append(f"missing required field: {field}")

    # Validate category
    category = case.get("category", "")
    if category and category not in VALID_CATEGORIES:
        errors.append(f"invalid category: {category} (valid: {VALID_CATEGORIES})")

    # Validate grader_type
    grader_type = case.get("grader_type", "")
    if grader_type and grader_type not in VALID_GRADER_TYPES:
        errors.append(f"invalid grader_type: {grader_type} (valid: {VALID_GRADER_TYPES})")

    # Validate field types
    if "expected_tools" in case and not isinstance(case["expected_tools"], list):
        errors.append("expected_tools must be a list")
    if "forbidden_tools" in case and not isinstance(case["forbidden_tools"], list):
        errors.append("forbidden_tools must be a list")
    if "expected_tool_sequence" in case and not isinstance(case["expected_tool_sequence"], list):
        errors.append("expected_tool_sequence must be a list")
    if "expected_tool_argument_keywords" in case and not isinstance(case["expected_tool_argument_keywords"], dict):
        errors.append("expected_tool_argument_keywords must be a dict")
    if "expected_keywords" in case and not isinstance(case["expected_keywords"], list):
        errors.append("expected_keywords must be a list")
    if "forbidden_keywords" in case and not isinstance(case["forbidden_keywords"], list):
        errors.append("forbidden_keywords must be a list")
    if "forbidden_content" in case and not isinstance(case["forbidden_content"], list):
        errors.append("forbidden_content must be a list")
    if "expected_no_answer" in case and not isinstance(case["expected_no_answer"], bool):
        errors.append("expected_no_answer must be a bool")
    if "requires_llm" in case and not isinstance(case["requires_llm"], bool):
        errors.append("requires_llm must be a bool")
    if "requires_human_review" in case and not isinstance(case["requires_human_review"], bool):
        errors.append("requires_human_review must be a bool")

    return {"valid": len(errors) == 0, "errors": errors}
