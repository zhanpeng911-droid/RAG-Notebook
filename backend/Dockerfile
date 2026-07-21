FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libmagic1 \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制元数据与源码，再安装（slim 默认依赖，不含 torch）
COPY pyproject.toml ./
COPY app ./app
COPY main.py alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir . -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . .

RUN mkdir -p /app/data /app/logs

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
