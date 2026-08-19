"""Answer quality grader for LLM smoke eval cases.

Grades based on:
- expected_keywords present in answer
- forbidden_keywords absent from answer
- expected_refusal: if true, answer should contain refusal/hedging language
- answer emptiness check
- model grader prompt builder (for future LLM-as-judge)
"""

# Hedging/refusal keywords in both Chinese and English
REFUSAL_KEYWORDS = [
    "无法", "不能", "没有", "不知道", "不确定", "抱歉", "抱歉我",
    "根据提供的资料", "根据给定的笔记", "根据上下文",
    "sorry", "cannot", "unable", "not found", "no information",
    "I don't have", "I cannot", "not available",
    "建议补充", "建议提供", "请提供",
]


def _contains_keyword(text: str, keyword: str) -> bool:
    """Case-insensitive check for English keywords, substring for Chinese.

    中文关键词匹配时：
    1. 去除空格和全半角差异（"IO 线程" vs "IO线程"）
    2. 去掉"非常/很/较为"等程度副词（"收益很低" 匹配 "收益低"）
    3. 前缀子串包含（"写操作...的开销" 匹配 "写开销" 时按词元判断）
    """
    import re as _re

    def _normalize(s: str) -> str:
        s = s.replace(" ", "").replace("\u3000", "")
        # 去掉程度副词，使 "收益很低" -> "收益低"
        s = _re.sub(r"(很|非常|较为|十分|极其|特别|太)低", "低", s)
        s = _re.sub(r"(很|非常|较为|十分|极其|特别|太)高", "高", s)
        s = _re.sub(r"(很|非常|较为|十分|极其|特别|太)大", "大", s)
        s = _re.sub(r"(很|非常|较为|十分|极其|特别|太)多", "多", s)
        return s

    if keyword.isascii():
        return keyword.lower() in text.lower()
    return _normalize(keyword) in _normalize(text)


def grade(case: dict, result: dict) -> dict:
    """Grade an answer quality case.

    Args:
        case: The eval case dict.
        result: Dict with 'answer' (str) - the LLM's response.

    Returns:
        dict with keys: passed (bool), score (float), grader_type (str), details (dict)
    """
    answer = result.get("answer", "")
    expected_keywords = case.get("expected_keywords", [])
    forbidden_keywords = case.get("forbidden_keywords", [])
    expected_refusal = case.get("expected_refusal", False)

    # --- expected_keywords check ---
    expected_found = [kw for kw in expected_keywords if _contains_keyword(answer, kw)]
    expected_missing = [kw for kw in expected_keywords if not _contains_keyword(answer, kw)]

    # --- forbidden_keywords check ---
    forbidden_found = [kw for kw in forbidden_keywords if _contains_keyword(answer, kw)]

    # --- refusal check ---
    refusal_detected = False
    refusal_violations = []
    if expected_refusal:
        refusal_detected = any(_contains_keyword(answer, kw) for kw in REFUSAL_KEYWORDS)
        if not refusal_detected and len(answer) > 50:
            refusal_violations.append("expected refusal but answer is substantive without hedging language")

    # --- empty answer check ---
    empty_violations = []
    if not answer or len(answer.strip()) == 0:
        empty_violations.append("answer is empty")

    # --- scoring ---
    if expected_keywords:
        keyword_score = len(expected_found) / len(expected_keywords)
    else:
        keyword_score = 1.0

    penalty = len(forbidden_found) * 0.2
    score = max(0.0, keyword_score - penalty)

    # --- pass/fail ---
    passed = (
        len(expected_missing) == 0
        and len(forbidden_found) == 0
        and len(refusal_violations) == 0
        and len(empty_violations) == 0
    )

    # --- build model grader prompt (for future LLM-as-judge) ---
    model_grader_prompt = _build_model_grader_prompt(case, answer)

    details = {
        "expected_keywords_found": expected_found,
        "expected_keywords_missing": expected_missing,
        "forbidden_keywords_found": forbidden_found,
        "expected_refusal": expected_refusal,
        "refusal_detected": refusal_detected,
        "refusal_violations": refusal_violations,
        "answer_length": len(answer),
        "empty_violations": empty_violations,
        "model_grader_prompt_built": True,
    }

    return {
        "passed": passed,
        "score": round(score, 2),
        "grader_type": "answer_quality",
        "details": details,
        "model_grader_prompt": model_grader_prompt,
    }


def _build_model_grader_prompt(case: dict, answer: str) -> str:
    """Build an LLM-as-judge prompt for future use.

    This prompt is NOT called in Eval 3. It's prepared for Eval 3B+.
    """
    context = case.get("context_setup", {}).get("context", "")
    user_input = case.get("user_input", "")
    expected_behavior = case.get("expected_behavior", "")

    prompt = f"""你是一个回答质量评估专家。请根据以下标准评估回答质量。

## 用户问题
{user_input}

## 参考上下文
{context if context else "（无上下文）"}

## 模型回答
{answer}

## 评估标准
{expected_behavior if expected_behavior else "回答应准确、完整、基于上下文"}

## 请评估
1. 回答是否基于上下文（faithfulness）
2. 回答是否完整覆盖问题（completeness）
3. 回答是否包含无关信息（relevance）
4. 回答语言是否自然流畅

请输出 JSON 格式：
{{
  "faithfulness_score": 0-1,
  "completeness_score": 0-1,
  "relevance_score": 0-1,
  "overall_score": 0-1,
  "issues": ["问题1", "问题2"],
  "suggestions": ["建议1", "建议2"]
}}"""

    return prompt
