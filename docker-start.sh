#!/bin/bash

# Notebook Docker快速启动脚本

set -e

echo "=========================================="
echo "  Notebook Docker部署"
echo "=========================================="
echo ""

# 检查Docker是否安装
if ! command -v docker &> /dev/null; then
    echo "错误: 未检测到Docker，请先安装Docker"
    echo "安装地址: https://docs.docker.com/get-docker/"
    exit 1
fi

# 检查Docker Compose是否安装
if ! command -v docker compose &> /dev/null; then
    echo "错误: 未检测到Docker Compose，请先安装Docker Compose"
    echo "安装地址: https://docs.docker.com/compose/install/"
    exit 1
fi

# 检查环境变量文件
if [ ! -f .env ]; then
    echo "错误: 未找到根目录 .env 文件"
    echo "请先复制 .env.example 为 .env，并替换其中的占位密码和密钥"
    exit 1
fi

if [ ! -f backend/.env.docker ]; then
    echo "错误: 未找到 backend/.env.docker 文件"
    exit 1
fi

if [ ! -f DjangoUserService/.env.docker ]; then
    echo "错误: 未找到 DjangoUserService/.env.docker 文件"
    exit 1
fi

echo "正在构建并启动所有服务..."
echo "首次构建可能需要几分钟时间..."
echo ""

# 构建并启动

# 拒绝弱口令 / 占位密钥
if grep -Eiq 'change-me|replace-me|replace-with' .env; then
  echo "错误: .env 仍包含 change-me / replace-me 占位值，请先替换 MYSQL/REDIS/JWT 密钥"
  exit 1
fi

docker compose up -d --build

echo ""
echo "=========================================="
echo "  服务启动完成！"
echo "=========================================="
echo ""
echo "访问地址:"
echo "  前端: http://localhost:3000"
echo "  后端API: http://localhost:8000"
echo "  Django服务: http://localhost:8001"
echo ""
echo "常用命令:"
echo "  查看日志: docker compose logs -f"
echo "  停止服务: docker compose down"
echo "  重启服务: docker compose restart"
echo "  查看状态: docker compose ps"
echo ""
echo "如需启用Ollama本地模型，请编辑 docker-compose.yml 取消Ollama服务注释"
echo ""
