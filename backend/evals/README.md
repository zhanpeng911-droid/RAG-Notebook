# Agent Eval Framework — Notebook

## 目录结构

```
backend/evals/
├── README.md              # 本文件：schema 定义 + 使用说明
├── cases/                 # 评估用例（JSONL 格式）
│   ├── rag_retrieval_cases.jsonl
│   ├── agent_tool_cases.jsonl
│   └── safety_cases.jsonl
├── runners/
│   └── run_eval.py        # 主 runner（dry-run / mock / real）
├── graders/
│   ├── __init__.py
│   ├── schema_validator.py    # Case schema 校验
│   ├── keyword_grader.py      # 关键词匹配 grader
│   ├── tool_call_grader.py    # 工具调用 grader
│   └── forbidden_content_grader.py  # 禁止内容 grader
└── reports/
    └── .gitkeep
```

## Case Schema

每个 eval case 是一个 JSON 对象（每行一个 case，JSONL 格式）。

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 唯一标识，如 `rag-001` |
| `category` | string | 分类：`rag_retrieval` / `agent_tool` / `safety` / `no_answer` / `user_isolation` |
| `user_input` | string | 用户输入文本 |
| `success_criteria` | string | 通过条件的简短描述 |
| `grader_type` | string | 使用的 grader：`keyword` / `tool_call` / `forbidden_content` / `model` / `human_review` |

### 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `description` | string | 用例描述（人类可读） |
| `context_setup` | object | 测试前置条件（种子数据等） |
| `expected_behavior` | string | 期望行为的文字描述 |
| `expected_tools` | list[string] | 期望 Agent 调用的工具名列表 |
| `forbidden_tools` | list[string] | 不应调用的工具名列表 |
| `expected_keywords` | list[string] | 期望在结果中出现的关键词 |
| `forbidden_keywords` | list[string] | 不应出现在结果中的关键词 |
| `forbidden_content` | list[string] | 不应出现的内容模式（如密钥格式） |
| `expected_no_answer` | bool | 是否期望 Agent 拒答（true = 应该说不知道） |
| `requires_llm` | bool | 是否需要真实 LLM 调用（默认 false） |
| `requires_human_review` | bool | 是否需要人工复核 |
| `notes` | string | 开发者备注 |

### 示例 Case

```json
{
  "id": "rag-001",
  "category": "rag_retrieval",
  "description": "用户问已知笔记内容，应召回包含关键词的笔记",
  "user_input": "我之前写的关于机器学习的笔记讲了什么？",
  "context_setup": {
    "seed_notes": [
      {"title": "机器学习入门", "content": "机器学习是人工智能的一个分支，主要包括监督学习、无监督学习和强化学习。"}
    ]
  },
  "expected_keywords": ["机器学习", "监督学习"],
  "success_criteria": "检索结果包含机器学习相关关键词",
  "grader_type": "keyword",
  "requires_llm": false,
  "notes": "验证笔记向量检索的基本能力"
}
```

## Eval 0 边界说明

**Eval 0 只证明：**
- Case schema 已定义并可通过校验
- Case JSONL 文件可加载、可解析
- Runner 可执行（dry-run 模式）
- Deterministic grader 接口可调用（plumbing check）

**Eval 0 不证明：**
- RAG 检索质量
- Agent 工具调用正确性
- LLM 回答质量
- 用户隔离稳定性
- 任何 "准确率" 或 "成功率" 指标

**Dry-run 模式下：**
- `behavior_evaluated` 始终为 0
- `behavior_passed` 始终为 0
- `dry_run_runnable` 表示 "grader 可调用"，不是 "行为正确"
- 不调用真实 LLM、ChromaDB、Agent

## Eval 1：RAG Retrieval Mock Eval

### 目标

验证 retrieval grader 能正确判定 mock retrieved docs 的关键词命中、forbidden keyword 过滤、no-answer 判定。

### Eval 1 证明

- RAG retrieval eval case 可以被执行
- mock retrieval result 可以被 grader 判定
- expected_keywords / forbidden_keywords / expected_no_answer 规则能工作
- runner 可以输出真实 behavior_evaluated / behavior_passed / behavior_failed
- 报告能区分 dry-run 与 mock 模式

### Eval 1 不证明

- 真实 ChromaDB 检索质量
- 真实 embedding 质量
- 真实 LLM 回答质量
- 真实知识库上传链路质量

## Eval 2：Agent Tool-Call Mock Eval

### 目标

验证 tool_call_grader 能正确判定 mock tool calls 的工具选择、forbidden tools、sequence、argument keywords。

### Eval 2 证明

- agent_tool eval cases 可以被执行
- mock tool call trace 可以被 grader 判定
- expected_tools / forbidden_tools / expected_tool_sequence 规则能工作
- runner 可以输出 agent_tool 类 case 的真实 behavior_evaluated / behavior_passed / behavior_failed

### Eval 2 不证明

- 真实 LLM 是否会稳定选择正确工具
- 真实 Agent 是否完整执行工具调用
- 真实工具返回内容是否正确
- 真实 RAG 召回质量
- 真实回答质量

### mock_tool_calls 字段说明

每个 agent_tool case 的 `context_setup` 中可包含 `mock_tool_calls`：

```json
{
  "context_setup": {
    "mock_tool_calls": [
      {
        "name": "search_notes_tool",
        "arguments": {
          "query": "Docker"
        }
      }
    ]
  }
}
```

### tool_call_grader 判定规则

**expected_tools**：
- 所有 expected_tools 必须在 mock_tool_calls 中出现至少一次
- 缺失任何一个即判定 failed

**forbidden_tools**：
- forbidden_tools 不得出现在 mock_tool_calls 中
- 出现任何一个即判定 failed

**expected_tool_sequence**（可选）：
- 如果提供，mock_tool_calls 必须包含 expected_tool_sequence 作为子序列
- 顺序必须正确

**expected_tool_argument_keywords**（可选）：
- 如果提供，检查指定工具的 arguments 中是否包含关键词
- 格式：`{"tool_name": ["keyword1", "keyword2"]}`

**no-tool-needed**：
- `expected_tools` 为空且 `mock_tool_calls` 为空 → pass
- `expected_tools` 为空但 `mock_tool_calls` 非空 → fail

**unknown tools**：
- 如果 mock_tool_calls 中出现不在 known_tools 列表中的工具，记录 warning
- 不直接影响 pass/fail（未来可配置为 fail）

### mock agent_tool 运行命令

```bash
cd backend
python -m evals.runners.run_eval --mock --category agent_tool
python -m evals.runners.run_eval --mock  # 全量 mock（rag_retrieval + agent_tool）
```

### 如何解读 behavior_passed

- mock 模式 `behavior_passed` 表示 "mock tool calls 满足 case 定义的期望条件"
- 不等于 "真实 Agent 选择了正确工具"
- 不等于 "真实 Agent 完整执行了工具调用"
- 只证明 grader 和 case 设计能正确评估 tool call trace

### 为什么 mock tool-call passed 不等于真实 Agent 成功率

- mock 模式使用预定义的 tool calls，不经过 LLM 推理
- 真实 Agent 的工具选择依赖 LLM 的意图理解和工具描述
- 真实 Agent 可能因 LLM 幻觉、工具描述不清、上下文不足等原因选错工具
- mock 模式只能验证 grader 逻辑正确性，不能验证 Agent 行为正确性

### mock_retrieved_docs 字段说明

每个 rag_retrieval case 的 `context_setup` 中可包含 `mock_retrieved_docs`：

```json
{
  "context_setup": {
    "mock_retrieved_docs": [
      {
        "id": "doc-1",
        "content": "文档内容...",
        "metadata": {
          "user_id": "user-a",
          "source": "notes"
        }
      }
    ]
  }
}
```

### retrieval_grader 判定规则

**expected_keywords**：
- 所有 expected_keywords 必须出现在 retrieved docs 的 content 中
- 英文关键词 case-insensitive，中文关键词 substring match

**forbidden_keywords**：
- 所有 forbidden_keywords 不应出现在 retrieved docs 的 content 中
- 出现任何一个即判定 failed

**expected_no_answer**：
- `true`：retrieved docs 应为空或低相关性（无 expected_keywords 命中）
- `false`：应至少有 1 个 retrieved doc

**min_retrieved_count / max_retrieved_count**（可选）：
- 检查 retrieved docs 数量是否在指定范围内

### mock 模式运行命令

```bash
cd backend
python -m evals.runners.run_eval --mock --category rag_retrieval
```

### 报告字段说明

```
Behavior evaluated:       12   (实际评估的 case 数)
Behavior passed:          X    (grader 判定通过)
Behavior failed:          Y    (grader 判定失败)
```

### 如何解读 behavior_passed

- mock 模式 `behavior_passed` 表示 "mock retrieved docs 满足 case 定义的期望条件"
- 不等于 "真实 RAG 检索正确"
- 不等于 "真实 ChromaDB 召回了正确的文档"
- 只证明 grader 和 case 设计能正确评估 retrieval result

## 运行方式

### Dry-run（默认，只验证 schema + grader plumbing）

```bash
cd backend
python -m evals.runners.run_eval --dry-run
```

### Mock 模式（使用 case 自带 mock 数据，不调用真实 LLM）

```bash
cd backend
python -m evals.runners.run_eval --mock --category rag_retrieval
python -m evals.runners.run_eval --mock --category agent_tool
python -m evals.runners.run_eval --mock  # 全量 mock
```

Mock 模式会读取 case 的 `context_setup` 中的 mock 数据（`mock_retrieved_docs` 或 `mock_tool_calls`），调用对应 grader 进行判定。产出真实 `behavior_evaluated` / `behavior_passed` / `behavior_failed`。

### Real 模式（需要 API key）

```bash
cd backend
python -m evals.runners.run_eval --real
```

## 输出报告

报告保存在 `backend/evals/reports/` 目录下，格式：

```
eval_report_YYYYMMDD_HHMMSS.json
```

**注意：报告文件是运行产物，已被 .gitignore 忽略，不应提交到代码仓库。**

Dry-run 报告包含字段：

- `mode`: "dry-run"
- `mode_disclaimer`: 说明 dry-run 不评估行为
- `total_cases_loaded`: 加载的 case 总数
- `schema_valid`: schema 校验通过的 case 数
- `schema_invalid`: schema 校验失败的 case 数
- `dry_run_runnable`: grader plumbing check 通过的 case 数
- `behavior_evaluated`: 0（dry-run 不评估行为）
- `behavior_passed`: 0（dry-run 不评估行为）
- `behavior_failed`: 0（dry-run 不评估行为）
- `skipped_requires_llm`: 需要 LLM 而跳过的 case 数
- `skipped_human_review`: 需要人工复核而跳过的 case 数
- `results[]`: 每个 case 的 plumbing check 结果

Mock 报告包含字段：

- `mode`: "mock"
- `mode_disclaimer`: 说明 mock 模式使用 mock_retrieved_docs
- `total_cases_loaded`: 加载的 case 总数
- `schema_valid`: schema 校验通过的 case 数
- `behavior_evaluated`: 实际评估的 case 数（> 0）
- `behavior_passed`: grader 判定通过的 case 数
- `behavior_failed`: grader 判定失败的 case 数
- `skipped_requires_llm`: 需要 LLM 而跳过的 case 数
- `results[]`: 每个 case 的评估结果（含 details）

## Eval 3：LLM Smoke + Answer Quality Grader

### 目标

建立真实 LLM 小样本 smoke 骨架，设计 answer quality grader。

### Eval 3 证明

- eval 框架可以标记并加载 requires_llm=true 的 case
- runner 可以显式启用 LLM eval
- LLM eval 默认关闭，必须通过环境变量开启
- 能运行 3-5 条低成本真实 LLM smoke case
- 能记录模型名、温度、时间、成功/失败
- 初版 answer_quality grader 已定义

### Eval 3 不证明

- 系统整体回答质量达标
- RAG 准确率
- Agent 工具调用真实成功率
- 模型长期稳定性
- 线上生产质量

### answer_quality_grader 判定规则

**deterministic pre-check**：
- `expected_keywords`：所有关键词必须出现在 answer 中
- `forbidden_keywords`：不应出现在 answer 中
- `expected_refusal=true`：answer 应包含拒答/无法确认/没有权限等关键词
- answer 不能为空

**model grader prompt builder**：
- 生成 LLM-as-judge prompt（faithfulness / completeness / relevance）
- 默认不调用 judge model
- 后续 Eval 3B 再接真实 judge

### LLM 调用开关

真实 LLM 执行必须同时满足：
1. CLI 参数包含 `--llm-smoke`
2. `EVAL_ENABLE_LLM=true` 环境变量
3. case.requires_llm=true

缺少任一条件都不调用 LLM，而是 skip。

### llm-smoke 运行命令

```bash
cd backend

# 未启用 LLM（默认，skip 所有 LLM cases）
python -m evals.runners.run_eval --llm-smoke --category answer_quality --limit 4

# 启用 LLM
$env:EVAL_ENABLE_LLM="true"
python -m evals.runners.run_eval --llm-smoke --category answer_quality --limit 4
```

### LLM 报告字段

```
LLM eval enabled:         true/false
LLM cases discovered:     N
LLM cases executed:       N
LLM behavior passed:      X
LLM behavior failed:      Y
LLM cases skipped:        N
Model provider:           dashscope/openai/ollama
Model name:               xxx
Temperature:              0
Max tokens:               256
```

### 为什么 LLM eval 默认关闭

- 避免意外产生 API 费用
- 避免在 CI/CD 中意外调用真实 LLM
- 避免 mock/dry-run 被真实 LLM 调用污染
- 必须显式启用，确保意图明确

### 为什么小样本结果不能写成准确率

- 3-5 条 case 不具备统计显著性
- 小样本结果受 case 选择影响大
- 不能代表系统整体质量
- 只能作为 smoke 验证框架可用性
