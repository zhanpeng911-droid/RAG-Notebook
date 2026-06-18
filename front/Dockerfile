# 构建阶段
FROM node:20-alpine AS builder

WORKDIR /app

# 复制依赖文件
COPY package.json pnpm-lock.yaml ./

# 安装依赖
RUN corepack enable && pnpm install --frozen-lockfile

# 复制源代码
COPY . .

# 构建参数
ARG VITE_BACKEND_TARGET=http://backend:8000
ARG VITE_USER_TARGET=http://django:8001

# 设置环境变量
ENV VITE_BACKEND_TARGET=$VITE_BACKEND_TARGET
ENV VITE_USER_TARGET=$VITE_USER_TARGET

# 构建前端
RUN pnpm build

# 运行阶段
FROM nginx:alpine

# 复制nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
