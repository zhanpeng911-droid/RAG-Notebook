# Notebook 质量保障改进方案

> 本文档是给执行方的完整工作说明书。所有现状描述均已核实过仓库实际状态，可直接按任务清单动手，无需重新调研。
> 执行原则：**每个任务独立提交**（commit 前缀 `ci:` / `test:` / `build:` / `chore:`），不顺手重构业务代码；过程中发现业务 bug 记录到 TODO 清单另行处理，不混入本方案的提交。

---

## 一、现状盘点（已核实）

| 层 | 已有 | 缺口 |
|---|---|---|
| FastAPI 主服务（`backend/`） | `tests/` 下 28 个 pytest 文件，覆盖 Agentic RAG 回归、权限隔离、限流、SSE、eval 等；dev 依赖已声明 ruff/black/isort/pytest-cov 之外的测试基建 | CI 未跑任何 lint；无覆盖率度量；无类型检查 |
| Django 用户服务（`DjangoUserService/`） | `apps/user`、`apps/file` 有测试，CI 在跑 | 同上（优先级较低） |
| Vue 3 前端（`front/`） | Playwright E2E：`tests/e2e/*.spec.js` 6 个 mock 版 + `tests/e2e-full/` 1 个全栈版 | **零单元测试**（无 Vitest/@vue/test-utils）；E2E 未进 CI；无 ESLint |
| CI（`.github/workflows/ci.yml`） | 4 个 job：backend pytest / django test / front build / compose 校验 | 见上述各项缺口 |

关键事实（执行时会用到）：

- 包管理：Python 侧统一用 **uv**（`uv sync --extra dev`、`uv run ...`），Node 侧用 npm。
- `front/vite.config.js` 开发服务器端口为 **3076**，而 `front/playwright.config.js` 的 `baseURL` 和 `webServer.url` 写的是 **3000** —— 两者目前不一致（见任务 P0-B）。
- 根目录 `.env` 未被 git 追踪（已确认安全）；但 `DjangoUserService/.env.docker` 被 git 追踪，需确认其中无真实密钥（见任务 P2-C）。
- 后端测试不依赖真实外部服务（CI 中用假 MySQL host + OLLAMA 环境变量即可跑通），此模式要保持。
- `backend/requirements.txt` 存在，可用于 pip-audit。

---

## 二、任务清单

### P0-A：后端 Lint 接入 CI 门禁

**目标**：让 pyproject.toml 里声明了的 ruff/black/isort 真正被执行。

步骤：

1. 在 `backend/pyproject.toml` 补充工具配置：

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
# 初期只用默认规则集（E4/E7/E9/F），不要一次开全量规则

[tool.isort]
profile = "black"
```

2. 本地执行 `cd backend && uv run ruff check app tests`，查看存量违规数量：
   - 数量少（几十以内）：直接修复（可配合 `ruff check --fix`），单独提交一个 `style:` commit；
   - 数量大：暂不对存量开闸，改为在 CI 中先只对**本次变更文件**检查，或维持默认规则集并接受一次性修复提交。不要为了过 lint 大改业务逻辑。
3. black/isort 建议做**一次性格式化提交**（`uv run black app tests && uv run isort app tests`），之后 CI 用 `--check` 模式强制。若团队不接受大 diff，可推迟 black/isort，只强制 ruff（可接受）。
4. 在 `.github/workflows/ci.yml` 的 `backend` job 中，Pytest 步骤之前加入：

```yaml
      - name: Ruff
        run: |
          if command -v uv >/dev/null 2>&1; then
            uv run ruff check app tests
          else
            python -m ruff check app tests
          fi

      # 若做了格式化统一，再加：
      # - name: Black / isort check
      #   run: uv run black --check app tests && uv run isort --check-only app tests
```

**验收标准**：向任一分支推送一段含明显 lint 错误的代码，CI 变红；当前主干 CI 全绿。

---

### P0-B：前端 Playwright（mock 版）E2E 接入 CI

**目标**：现有 `front/tests/e2e/` 的 6 个 mock 版用例在每次 push/PR 时自动执行。（`tests/e2e-full/` 需要完整 Docker 环境，继续留作本地/发布前手工执行，不进 CI。）

步骤：

1. **先解决端口不一致**：`vite.config.js` 是 3076，`playwright.config.js` 是 3000。推荐统一到 3076——修改 `playwright.config.js` 中 `use.baseURL` 与 `webServer.url` 为 `http://127.0.0.1:3076`。同时确认 mock 版用例是通过 `page.route` 拦截网络、不依赖真实后端代理；若有用例依赖 `/api/v1` 代理转发，需改造为路由拦截。
2. 在 `webServer` 中固定端口，避免端口被占时 Vite 自增导致 CI 抖动：

```js
webServer: {
  command: 'npm run dev',
  url: 'http://127.0.0.1:3076',
  reuseExistingServer: !process.env.CI,
  timeout: 60000,
},
```

3. 本地验证：`cd front && npx playwright test`（不带 `E2E_FULL_STACK`）能全绿。
4. 在 `.github/workflows/ci.yml` 新增 job：

```yaml
  frontend-e2e:
    name: Frontend E2E (mocked)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: front
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: front/package-lock.json
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npx playwright test --project=chromium
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: front/test-results/
          retention-days: 7
```

说明：Playwright 在 CI 环境下自动 `reuseExistingServer=false`；CI 只跑 chromium project 省 resources，mobile-chrome 可后续加。

**验收标准**：CI 出现 `frontend-e2e` job 且全绿；故意改坏一个页面元素后该 job 能变红。

---

### P1-A：前端单元测试体系（Vitest + @vue/test-utils）

**目标**：填补最大的空白。前端所有质量目前压在 E2E 上，而 SSE 解析、token 处理这类纯逻辑恰恰是 E2E 最难覆盖的。

步骤：

1. 安装依赖（在 `front/` 下）：

```bash
npm i -D vitest @vue/test-utils jsdom @pinia/testing @vitest/coverage-v8
```

2. 新建 `front/vitest.config.js`（与 vite.config.js 分离，避免互相干扰）：

```js
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['tests/unit/**/*.spec.js'],
  },
})
```

3. `front/package.json` 增加脚本：

```json
"test:unit": "vitest run",
"test:unit:watch": "vitest",
"test:unit:coverage": "vitest run --coverage"
```

4. 在 CI 的 `frontend` job 中，`npm run build` 之前加 `- run: npm run test:unit`。

5. **首批用例清单（按此优先级逐个实现，落到 `front/tests/unit/`，目录结构镜像 `src/`）**：

| 优先级 | 目标文件 | 必须覆盖的行为 |
|---|---|---|
| 1 | `src/services/sseClient.js` | SSE 分帧解析（事件体跨 chunk 到达的半包/粘包）、事件类型分发、abort/断线处理、非法数据容错不抛未捕获异常 |
| 2 | `src/services/http.js` | axios 拦截器：401/token 失效的处理路径、响应错误归一化 |
| 3 | `src/store/session.js`、`qaHistory.js`、`model.js`、`user.js`、`theme.js` | 各 Pinia store 的状态流转与持久化行为（每店至少 1 条正常路径 + 1 条异常分支） |
| 4 | `src/router/index.js` | 路由守卫：未登录访问受保护页的重定向、登录后放行 |
| 5 | `src/components/RetrievalTrace.vue`、`QaHistoryPanel.vue` | 引用 `[n]` 归一化展示逻辑、置信度徽标（high/medium/low/none）到样式/文案的映射 |
| 6 | `src/composables/useChatWorkspace.js`、`useKnowledgeBase.js` | 其中可提取的纯逻辑函数（参数拼装、状态机切换） |
| 7 | `src/config/features.js`、`config/api.js` | 纯配置断言（防止误删 feature flag） |

约定：

- 组件测试只测**逻辑与交互**（渲染出的关键文本/类名/事件），不测像素级样式。
- 外部请求一律 mock（vi.mock / page 层面拦不住，单测里 mock `services/*Api` 模块）。
- 每完成一个文件跑一次 `npm run test:unit`，保持常绿。

**验收标准**：上表优先级 1–4 的用例全部存在且通过；`npm run test:unit` 进入 CI 且全绿。

---

### P1-B：测试覆盖率度量（pytest-cov + vitest coverage）

**目标**：让"哪些代码完全没被测过"显形。覆盖率是仪表盘，不是 KPI。

步骤：

1. `backend/pyproject.toml` 的 `dev` extras 中追加 `"pytest-cov>=5.0"`。
2. 本地与 CI 将 Pytest 命令升级为：

```bash
uv run pytest tests -q --tb=line --cov=app --cov-report=term-missing
```

3. 初期**不设** `--cov-fail-under`，先跑出基线；重点观察 `app/agentic`、`app/rag`、`app/services` 三个核心包的行覆盖。两周后将核心包底线设为实测基线 ±10%（例如基线 55% 则 `--cov-fail-under=50`），写入 CI。
4. 前端覆盖率由 `npm run test:unit:coverage` 提供（Vitest v8 coverage），同样先观察不卡线。
5. Django 服务可用 `coverage run manage.py test ...` 补齐，优先级低，可选。

**验收标准**：CI 日志输出后端覆盖率报表；核心三包的覆盖率数字被记录在案（可写入本文档附录或 TEST_REPORT.md）。

---

### P2-A：前端 ESLint

1. 安装：`npm i -D eslint eslint-plugin-vue`。
2. 使用 flat config 新建 `front/eslint.config.js`，规则集取 `vue3-recommended`，忽略 `dist/`、`node_modules/`、`test-results/`。
3. 先 `npx eslint src --fix` 清理存量，剩余无法自动修的逐个手动处理或在该行禁用并注明原因。
4. 存量清零后，`package.json` 加 `"lint": "eslint src tests"`，并在 CI `frontend` job 中于 build 前执行。

**验收标准**：`npm run lint` 零 error；CI 中 lint 步骤存在且生效。

---

### P2-B：后端 mypy 渐进接入（advisory → 强制）

1. `dev` extras 加 `"mypy>=1.10"`，`pyproject.toml` 加：

```toml
[tool.mypy]
python_version = "3.12"
ignore_missing_imports = true
check_untyped_defs = true
```

2. 第一批只查相对独立的包：`app/core`、`app/schemas`、`app/utils`、`app/config`；`app/agentic`、`app/rag` 后续分批纳入。
3. CI 中先建 **advisory job**（`continue-on-error: true`），修完存量类型错误后改为强制步骤并入 backend job。

**验收标准**：advisory job 上线且产出报告；第一批四个包零类型错误后转为强制。

---

### P2-C：依赖漏洞扫描 + 安全自查

1. CI 新增 `security` job：

```yaml
  security:
    name: Dependency audit
    runs-on: ubuntu-latest
    continue-on-error: true   # 初期仅报告，建立 triage 流程后再改为阻断
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pip-audit
      - run: pip-audit -r backend/requirements.txt
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: front/package-lock.json
      - run: cd front && npm ci && npm audit --audit-level=critical
```

2. 安全自查两项：
   - 确认被 git 追踪的 `DjangoUserService/.env.docker`、`backend/.env.docker` 内容仅为部署模板、不含真实密钥；若含，立即从历史中移除并轮换密钥。
   - 确认根目录 `.env` 继续保持在 `.gitignore` 中不被追踪（当前已满足，防回归即可）。

**验收标准**：security job 上线；高危漏洞有 issue 跟踪。

---

## 三、长效规范（写入贡献指南/README「测试与质量验证」一节）

1. **修 bug 必须附带能复现该 bug 的回归测试**，随修复同一 PR 提交。项目已有的 `tests/test_p0_fixes.py`、`tests/test_agentic_rag_regressions.py` 按回归组织的模式很好，延续下去。
2. 新增功能 PR 必须附对应测试；review 时检查断言是否真的验证了行为（而非只求跑通）。
3. `skip` 测试必须在装饰器注明原因与跟踪链接，不允许无主 skip。
4. 覆盖率不设全局 KPI；核心目录（`app/agentic`、`app/rag`、`app/services` 及前端 `services/`、`store/`）设底线并逐步上调。
5. GitHub 仓库设置（非文件改动，提醒管理员操作）：对 `main`/`master` 开启 branch protection，要求上述全部 CI job 通过方可合并。

## 四、执行顺序与验收总清单

建议顺序：P0-A → P0-B（半天～1 天）→ P1-A 骨架 + 优先级 1–2 用例 → P1-B → P1-A 其余用例（持续）→ P2 三项可并行。

- [ ] P0-A：ruff 在 CI 生效，主干全绿
- [ ] P0-B：端口不一致修复，mock 版 E2E 进 CI 且全绿
- [ ] P1-A：Vitest 就绪，sseClient/http/store/router 四类用例通过并入 CI
- [ ] P1-B：后端覆盖率报表出现在 CI 日志，核心包基线数字已记录
- [ ] P2-A：ESLint 零 error 入 CI
- [ ] P2-B：mypy advisory job 上线
- [ ] P2-C：security job 上线，`.env.docker` 密钥自查完成
- [ ] 长效规范已写入 README「测试与质量验证」章节

---

## 五、附录：覆盖率基线（2026-08-26，pytest-cov 首次接入，P1-B 已完成）

> 执行命令：`uv run pytest tests -q --tb=line --cov=app --cov-report=term-missing`
> 初期不设 `--cov-fail-under`；两周后将核心包底线设为实测基线 ±10% 写入 CI。

### 5.1 总览

| 范围 | 行数 | 未覆盖 | 行覆盖 |
|---|---:|---:|---:|
| **app 全量** | 6551 | 4541 | **31%** |

### 5.2 核心包明细（重点观察对象）

| 包/文件 | 覆盖率 |
|---|---:|
| app/agentic/planner.py | 90% |
| app/agentic/state.py | 95% |
| app/agentic/retrieval_grader.py | 80% |
| app/agentic/guardrails.py | 79% |
| app/agentic/graph.py | 71% |
| app/agentic/citation.py | 53% |
| app/agentic/answer_generator.py | 21% |
| app/agentic/tools.py | 0% |
| app/rag/retrieval_service.py | 34% |
| app/rag/vector_store.py | 29% |
| app/rag 重型 IO 组件（hybrid_retriever / md5_store / processor / reranker / text_spliter） | 0%（依赖 Chroma/embedding，由 e2e-full 与 eval runner 兜底） |
| app/services/knowledge_record_service.py、knowledge_sse_events.py | 100% |
| app/services/knowledge_file_validator.py | 83% |
| app/services/note_vector_index.py | 45% |
| app/services/note_service.py | 35% |

### 5.3 后续补测优先级

1. `answer_generator.py`（LLM mock 可测，当前 21%）；
2. `retrieval_service.py` 的 scope/去重/合并纯逻辑分支；
3. rag 重型 IO 组件不强行用单测堆覆盖率。
