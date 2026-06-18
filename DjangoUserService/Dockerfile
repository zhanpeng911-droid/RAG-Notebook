FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml .

# 安装Python依赖
RUN pip install --no-cache-dir -e . -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY . .

EXPOSE 8001

CMD ["uvicorn", "DjangoUserService.wsgi:application", "--host", "0.0.0.0", "--port", "8001"]
