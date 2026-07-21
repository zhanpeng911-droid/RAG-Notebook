# Notebook 本地开发一键脚本（Windows PowerShell）
# 用法：
#   .\scripts\dev.ps1              # 打印启动指引
#   .\scripts\dev.ps1 -Infra       # 仅起 mysql+redis
#   .\scripts\dev.ps1 -DockerFull  # compose 全量
param(
  [switch]$Infra,
  [switch]$DockerFull,
  [switch]$Migrate
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

function Ensure-Env {
  if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已从 .env.example 复制 .env，请替换占位密钥" -ForegroundColor Yellow
  }
}

Ensure-Env

if ($DockerFull) {
  Write-Host "docker compose up -d --build" -ForegroundColor Cyan
  docker compose up -d --build
  exit $LASTEXITCODE
}

if ($Infra) {
  Write-Host "启动基础设施: mysql + redis" -ForegroundColor Cyan
  docker compose up -d mysql redis
  exit $LASTEXITCODE
}

if ($Migrate) {
  Write-Host "Django migrate..." -ForegroundColor Cyan
  Push-Location DjangoUserService
  uv run python manage.py migrate
  Pop-Location
  Write-Host "Alembic upgrade..." -ForegroundColor Cyan
  Push-Location backend
  uv run alembic upgrade head
  Pop-Location
  exit 0
}

Write-Host @"

Notebook 本地开发

1) 基础设施（可选 Docker）:
   .\scripts\dev.ps1 -Infra

2) 迁移:
   .\scripts\dev.ps1 -Migrate

3) 三个终端分别启动:
   cd DjangoUserService; uv run python manage.py runserver 8001
   cd backend; uv run uvicorn main:app --reload --port 8000
   cd front; npm run dev

4) 全量 Docker:
   .\scripts\dev.ps1 -DockerFull

企业 Org 功能默认关闭。开启:
   backend .env: FEATURE_ORG=true
   front localStorage: VITE_FEATURE_ORG=true 或构建时 VITE_FEATURE_ORG=true

"@ -ForegroundColor Green
