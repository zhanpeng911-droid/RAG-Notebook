# RAG-Notebook - 智能笔记助手

<div align="center">

一个把 **笔记管理、RAG 知识库、AI 问答、间隔回顾、写作辅助** 放进同一工作流的个人知识管理项目。

基于 `Vue 3 + FastAPI + LangChain + Django` 构建，支持 `Ollama`、`DashScope`、`OpenAI 兼容接口` 三类模型接入方式。

</div>

---

## 目录

- [项目简介](#项目简介)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [Docker 部署](#docker-部署)
- [配置说明](#配置说明)
- [API 文档](#api-文档)
- [测试说明](#测试说明)
- [项目结构](#项目结构)
- [公开仓库说明](#公开仓库说明)
- [联系方式](#联系方式)

## 项目简介

RAG-Notebook 的目标不是只做一个“能聊天的 RAG Demo”，而是把记录、整理、检索、回顾、问答整合成一个能长期使用的知识工作流。

当前仓库由三个服务组成：

- `front/`：Vue 3 + Vite 前端，负责笔记编辑、知识库管理、AI 对话、设置页和用户界面
- `backend/`：FastAPI + LangChain 主服务，负责 RAG 检索、Agent 对话、笔记服务、回顾服务和健康检查
- `DjangoUserService/`：Django 用户服务，负责注册、登录、JWT、用户资料和文件相关接口

这套结构把“用户认证”和“AI / RAG 业务”拆开，前后端边界更清晰。

## 核心特性

- **智能笔记管理**：支持笔记创建、编辑、删除、列表浏览、分类筛选与基础组织管理
- **Markdown 写作体验**：前端内置 Markdown 编辑能力，配合快捷工具栏和标签展示
- **AI 写作辅助**：支持联机补全、续写、扩写、摘要等写作辅助能力
- **RAG 知识库问答**：支持上传知识文件后进行向量检索与问答
- **多格式文档接入**：知识库支持 `txt`、`pdf`、`md`、`pptx`、`docx`
- **间隔回顾**：提供每日回顾能力，帮助把笔记从“记录过”变成“真正复习过”
- **会话管理**：聊天会话支持持久化与多轮上下文管理
- **多模型切换**：支持 `OLLAMA`、`ALIYUN`、`OPENAI` 三类 LLM 接入方式
- **用户隔离**：通过 JWT 和用户服务实现用户级数据隔离
- **组织能力预留**：已包含组织、空间、权限、审计相关路由与页面基础结构

## 系统架构

```text
Browser
  |
  v
front/ (Vue 3 + Vite + Vant)
  | \
  |  \-- /user, /file --------------------------> DjangoUserService/ (Django)
  |                                             |-- 用户注册 / 登录 / 用户资料
  |                                             \-- JWT / 文件相关接口
  |
  \----- /chat, /note, /knowledge, /review ----> backend/ (FastAPI + LangChain)
                                                |-- Agent 对话
                                                |-- RAG 检索
                                                |-- 笔记服务
                                                |-- 回顾服务
                                                |-- 健康检查
                                                |
                                                |-- MySQL
                                                |-- Redis
                                                |-- ChromaDB
                                                \-- Ollama / DashScope / OpenAI 兼容接口
```

## 技术栈

### 前端

| 技术 | 说明 |
|------|------|
| Vue 3 | 前端框架 |
| Vite | 本地开发与构建工具 |
| Vant 4 | 移动端风格 UI 组件库 |
| Vue Router | 路由与登录守卫 |
| Pinia | 状态管理 |
| Vue I18n | 国际化 |
| ByteMD | Markdown 编辑与渲染能力 |
| Axios | HTTP 请求 |
| Playwright | E2E 测试 |

### 后端

| 技术 | 说明 |
|------|------|
| FastAPI | 主业务 API 服务 |
| LangChain | LLM 应用编排 |
| ChromaDB | 向量存储 |
| SQLAlchemy | 数据模型与数据库访问 |
| aiomysql | MySQL 异步连接 |
| Redis | 缓存与运行态依赖 |
| Celery | 异步任务支持 |
| sentence-transformers | 向量嵌入 |
| DashScope / OpenAI Compatible / Ollama | 模型接入方式 |
| Django + DRF + drf-yasg | 用户认证与用户服务 API |

## 快速开始

### 环境要求

| 环境 | 版本 |
|------|------|
| Python | 3.12+ |
| Node.js | 20+ |
| uv | 已安装即可 |
| MySQL | 8.x |
| Redis | 7.x |
| Ollama | 可选 |
| Docker | 可选 |

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd RAG-Notebook
```

### 2. 安装依赖

```powershell
cd DjangoUserService
uv sync

cd ..\backend
uv sync

cd ..\front
npm install
```

### 3. 准备环境变量

复制示例配置文件：

```powershell
Copy-Item backend/.env.example backend/.env
Copy-Item DjangoUserService/.env.example DjangoUserService/.env
```

至少需要确认这些配置项已经填写：

#### `backend/.env`

- `SECRET_KEY`
- `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`
- `REDIS_HOST`、`REDIS_PORT`
- `DJANGO_API_URL`
- `CORS_ORIGINS`
- `LLM_TYPE`
- 对应模型供应商需要的 Key 或 URL

#### `DjangoUserService/.env`

- `JWT_SECRET_KEY`
- `DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`
- `REDIS_CACHE_URL`
- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`

注意：

- `backend/.env` 中的 `SECRET_KEY` 与 `DjangoUserService/.env` 中的 `JWT_SECRET_KEY` 应保持一致
- 这两个 `.env` 文件只用于本地运行，不应提交到仓库

### 4. 执行 Django 数据库迁移

```powershell
cd DjangoUserService
uv run python manage.py makemigrations
uv run python manage.py migrate
```

### 5. 启动 MySQL、Redis 和可选模型服务

```powershell
net start mysql80
redis-server
```

如果你使用本地 Ollama：

```bash
ollama serve
ollama pull qwen3.5:0.8b
ollama pull qwen3-embedding:0.6b
```

### 6. 启动三个服务

```powershell
# 终端 1
cd DjangoUserService
uv run python manage.py runserver 8001

# 终端 2
cd backend
uv run uvicorn main:app --reload --port 8000

# 终端 3
cd front
npm run dev
```

### 7. 默认访问地址

- 前端：`http://127.0.0.1:3000`
- FastAPI 文档：`http://127.0.0.1:8000/docs`
- Django Swagger：`http://127.0.0.1:8001/docs/`

## Docker 部署

仓库已经提供以下 Docker 相关文件：

- `.env.example`
- `docker-compose.yml`
- `backend/.env.docker`
- `DjangoUserService/.env.docker`
- `docker-start.bat`
- `docker-start.sh`

### 1. 准备根目录 `.env`

```powershell
Copy-Item .env.example .env
```

至少替换以下变量：

- `MYSQL_ROOT_PASSWORD`
- `MYSQL_PASSWORD`
- `JWT_SECRET_KEY`

如需云端模型，还需要按需填写：

- `ALIYUN_ACCESS_KEY_SECRET`
- `DASHSCOPE_API_KEY`
- `OPENAI_API_KEY`

### 2. 启动容器

```bash
docker compose up -d --build
```

也可以直接使用仓库根目录脚本：

```powershell
.\docker-start.bat
```

Docker 默认端口：

- 前端：`http://127.0.0.1:3000`
- FastAPI：`http://127.0.0.1:8000`
- Django：`http://127.0.0.1:8001`

## 配置说明

### LLM 模型切换

后端支持三种模型接入模式：

- `LLM_TYPE=OLLAMA`：本地模型
- `LLM_TYPE=ALIYUN`：阿里云百炼
- `LLM_TYPE=OPENAI`：OpenAI 兼容接口，例如 DeepSeek 一类服务

前端设置页也支持动态传入模型配置，包含：

- `provider`
- `model`
- `api_key`
- `base_url`
- `protocol`

如果前端请求没有携带 `llm_config`，后端会回退到 `backend/.env` 中的默认模型配置。

### 向量检索与知识库配置

知识库配置文件位于：

```text
backend/app/config/chroma.yaml
```

当前默认配置要点：

- 知识库支持 `txt`、`pdf`、`md`、`pptx`、`docx`
- 检索默认 `k=5`
- 文本切片默认 `chunk_size=200`
- 文本切片默认 `chunk_overlap=20`

### 用户认证与隔离

- Django 用户服务负责注册、登录、资料与鉴权相关接口
- FastAPI 业务服务通过 JWT 识别用户身份
- 知识库、会话、笔记等业务按用户维度隔离

## API 文档

### FastAPI 主服务

- 交互式文档：`http://127.0.0.1:8000/docs`
- 健康检查：
  - `GET /health/live`
  - `GET /health/ready`
  - `GET /health/db`
  - `GET /health/redis`
  - `GET /health/vector-store`
  - `GET /health/model`

主要业务路由前缀：

- `/chat`
- `/knowledge`
- `/note`
- `/review`
- `/org`
- `/space`
- `/audit`

### Django 用户服务

- 文档文件：[DjangoUserService/api.md](./DjangoUserService/api.md)
- Swagger UI：`http://127.0.0.1:8001/docs/`
- Redoc：`http://127.0.0.1:8001/redoc/`

## 测试说明

### 后端测试

```powershell
cd backend
uv run pytest tests
```

### 前端 E2E

```powershell
cd front
npm run test:e2e
```

### 前端全链路 E2E

这组测试依赖真实后端与显式传入的本地测试账号：

```powershell
cd front
$env:E2E_FULL_STACK="true"
$env:E2E_USERNAME="your-local-user"
$env:E2E_PASSWORD="your-local-password"
npm run test:e2e:full -- --project=chromium
```

## 项目结构

```text
RAG-Notebook/
├── backend/
│   ├── app/
│   │   ├── agent/             # Agent 相关逻辑
│   │   ├── cache/             # 缓存相关逻辑
│   │   ├── config/            # 配置文件
│   │   ├── core/              # 中间件、异常、日志等核心能力
│   │   ├── db/                # MySQL / Redis 配置
│   │   ├── models/            # 数据模型
│   │   ├── prompt/            # Prompt 模板
│   │   ├── rag/               # RAG 核心逻辑
│   │   ├── repositories/      # 仓储层
│   │   ├── router/            # API 路由
│   │   ├── schemas/           # Pydantic Schema
│   │   ├── services/          # 服务层
│   │   ├── tasks/             # 任务逻辑
│   │   └── utils/             # 工具函数
│   ├── evals/                 # Eval 框架
│   ├── tests/                 # 后端测试
│   ├── .env.example
│   ├── .env.docker
│   └── main.py
├── front/
│   ├── src/
│   │   ├── components/        # 通用组件
│   │   ├── composables/       # 组合式逻辑
│   │   ├── i18n/              # 国际化
│   │   ├── layouts/           # 布局
│   │   ├── pages/             # 业务页面
│   │   ├── router/            # 路由
│   │   ├── services/          # 前端请求封装
│   │   ├── store/             # Pinia 状态管理
│   │   ├── styles/            # 样式
│   │   └── views/             # 视图页面
│   ├── tests/                 # Playwright 测试
│   ├── package.json
│   └── vite.config.js
├── DjangoUserService/
│   ├── apps/
│   │   ├── user/              # 用户注册、登录、资料
│   │   ├── file/              # 文件相关接口
│   │   └── utils/             # 工具模块
│   ├── DjangoUserService/     # Django 项目配置
│   ├── .env.example
│   ├── .env.docker
│   ├── api.md
│   └── manage.py
├── .env.example               # Docker Compose 根配置示例
├── docker-compose.yml
├── docker-start.bat
├── docker-start.sh
└── plan.md
```

## 联系方式

如有问题，欢迎通过 GitHub Issues 交流。
