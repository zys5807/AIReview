#!/bin/bash
# AI复盘APP 一键启动脚本（Git Bash 环境）
# 用法: bash start_dev.sh

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"

echo "=== 启动后端 (FastAPI :8000) ==="
cd "$BACKEND"
./venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "=== 启动前端 (Vite :5173) ==="
cd "$FRONTEND"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "=============================================="
echo "  后端接口文档: http://127.0.0.1:8000/docs"
echo "  前端页面:     http://localhost:5173"
echo "=============================================="
echo "按 Ctrl+C 停止服务"
wait
