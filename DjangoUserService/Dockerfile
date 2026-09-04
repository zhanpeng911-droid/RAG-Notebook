FROM python:3.12-slim

# 固定 uv 版本（与 uv.lock 一致），保证构建与 CI 安装版本相同
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 先复制元数据与锁文件安装依赖（利用构建缓存）
COPY pyproject.toml uv.lock ./
# locked 安装生产依赖；--no-dev 排除 dev extra（ruff/pip-audit 等）
RUN uv sync --locked --no-dev --no-install-project

COPY . .
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001

# 启动前执行 Django 迁移
CMD ["sh", "-c", "python manage.py migrate --noinput && uvicorn DjangoUserService.asgi:application --host 0.0.0.0 --port 8001"]
