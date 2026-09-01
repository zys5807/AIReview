@echo off
title AI Review App - Dev Mode (HMR)
echo ============================================
echo   Dev Mode: backend + vite hot-reload
echo ============================================
echo.

echo [1/2] Starting backend on :8000 ...
start "AI-Backend" cmd /k "cd /d F:\AIwork\2026-08-21-18-01-35\backend && F:\AIwork\2026-08-21-18-01-35\backend\venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [2/2] Starting frontend dev server on :5173 ...
start "AI-Frontend" cmd /k "cd /d F:\AIwork\2026-08-21-18-01-35\frontend && D:\ProgramFiles\nodejs\npm.cmd run dev"

echo.
echo   Frontend:  http://localhost:5173  (auto reload on save)
echo   API docs:  http://127.0.0.1:8000/docs
echo.
pause
