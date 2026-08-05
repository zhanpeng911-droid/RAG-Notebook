"""Tool call grader for Agent tool selection cases.

Grades based on:
- expected_tools: all expected tools must appear at least once
- forbidden_tools: forbidden tools must not appear
- expected_tool_sequence: if provided, tools must appear in this order
- expected_tool_argument_keywords: optional, checks tool arguments for keywords
- no-tool-needed: expected_tools empty + mock_tool_calls empty => pass
- unknown tools: tools not in known_tools are recorded as warnings
"""

# Known tools from backend/app/agent/agent_tools.py
KNOWN_TOOLS = {
    "search_notes_tool",
    "get_note_stats_tool",
    "get_today_reviews_tool",
    "mark_reviewed_tool",
    "create_note_tool",
    "get_related_notes_tool",
    "what_time_is_now",
    "get_user_info_tools",
}


def grade(case: dict, result: dict) -> dict:
    """Grade a case based on tool call results.

    Args:
        case: The eval case dict.
        result: Dict with optional keys:
            - tool_calls (list[dict]): each has 'name' (str) and optional 'arguments' (dict)
            - Or tools_called (list[str]): simple list of tool names (backward compat)

    Returns:
        dict with keys: passed (bool), score (float), grader_type (str), details (dict)
    """
    expected_tools = case.get("expected_tools", [])
    forbidden_tools = case.get("forbidden_tools", [])
    expected_sequence = case.get("expected_tool_sequence", [])
    expected_arg_keywords = case.get("expected_tool_argument_keywords", {})

    # Support both tool_calls (structured) and tools_called (simple list)
    tool_calls = result.get("tool_calls", [])
    tools_called = result.get("tools_called", [])
    if not tools_called and tool_calls:
        tools_called = [tc.get("name", "") for tc in tool_calls]

    # --- expected_tools check ---
    expected_found = [t for t in expected_tools if t in tools_called]
    expected_missing = [t for t in expected_tools if t not in tools_called]

    # --- forbidden_tools check ---
    forbidden_found = [t for t in forbidden_tools if t in tools_called]

    # --- unknown tools check ---
    unknown_tools = [t for t in tools_called if t not in KNOWN_TOOLS]

    # --- sequence check ---
    sequence_passed = True
    sequence_violations = []
    if expected_sequence and len(tools_called) >= len(expected_sequence):
        # Check if expected_sequence is a subsequence of tools_called
        seq_idx = 0
        for tool_name in tools_called:
            if seq_idx < len(expected_sequence) and tool_name == expected_sequence[seq_idx]:
                seq_idx += 1
        if seq_idx < len(expected_sequence):
            sequence_passed = False
            sequence_violations.append(
                f"expected sequence {expected_sequence} not found as subsequence in {tools_called}"
            )
    elif expected_sequence and len(tools_called) < len(expected_sequence):
        sequence_passed = False
        sequence_violations.append(
            f"not enough tools called ({len(tools_called)}) for expected sequence ({len(expected_sequence)})"
        )

    # --- argument keywords check ---
    argument_keyword_missing = []
    if expected_arg_keywords and tool_calls:
        for tc in tool_calls:
            tc_name = tc.get("name", "")
            if tc_name in expected_arg_keywords:
                keywords = expected_arg_keywords[tc_name]
                tc_args = tc.get("arguments", {})
                # Convert arguments to string for keyword search
                args_str = " ".join(str(v) for v in tc_args.values())
                for kw in keywords:
                    if kw.lower() not in args_str.lower():
                        argument_keyword_missing.append(f"{tc_name}: missing keyword '{kw}' in arguments")

    # --- no-tool-needed check ---
    no_tool_violations = []
    if not expected_tools and tools_called:
        no_tool_violations.append(
            f"expected no tools but got: {tools_called}"
        )

    # --- scoring ---
    if expected_tools:
        base_score = len(expected_found) / len(expected_tools)
    else:
        base_score = 1.0

    penalty = len(forbidden_found) * 0.3
    score = max(0.0, base_score - penalty)

    # --- pass/fail ---
    passed = (
        len(expected_missing) == 0
        and len(forbidden_found) == 0
        and sequence_passed
        and len(argument_keyword_missing) == 0
        and len(no_tool_violations) == 0
    )

    details = {
        "called_tools": tools_called,
        "expected_tools_found": expected_found,
        "expected_tools_missing": expected_missing,
        "forbidden_tools_found": forbidden_found,
        "expected_sequence": expected_sequence,
        "sequence_passed": sequence_passed,
        "sequence_violations": sequence_violations,
        "unknown_tools": unknown_tools,
        "argument_keyword_missing": argument_keyword_missing,
        "no_tool_violations": no_tool_violations,
    }

    return {
        "passed": passed,
        "score": round(score, 2),
        "grader_type": "tool_call",
        "details": details,
    }
