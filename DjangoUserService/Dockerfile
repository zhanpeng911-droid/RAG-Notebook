FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN pip install --no-cache-dir . -i https://pypi.tuna.tsinghua.edu.cn/simple

EXPOSE 8001

# 启动前执行 Django 迁移
CMD ["sh", "-c", "python manage.py migrate --noinput && uvicorn DjangoUserService.asgi:application --host 0.0.0.0 --port 8001"]
