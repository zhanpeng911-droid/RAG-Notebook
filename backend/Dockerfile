FROM python:3.12-slim

# 固定 uv 版本（与 uv.lock 一致），保证构建与 CI 安装版本相同
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libmagic1 \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制元数据与锁文件安装依赖（利用构建缓存）
COPY pyproject.toml uv.lock ./
# locked 安装生产依赖；--no-dev 排除 dev extra（pytest/ruff/mypy 等），
# 不装 local-embed/docs-heavy（slim 默认依赖，不含 torch）
RUN uv sync --locked --no-dev --no-install-project

# 复制源码后再安装项目自身
COPY app ./app
COPY main.py alembic.ini ./
COPY alembic ./alembic
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

COPY . .

RUN mkdir -p /app/data /app/logs

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
