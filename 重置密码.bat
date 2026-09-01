@echo off
title Reset Password
echo ============================================
echo   AI Review App - Reset Password
echo   重置密码（忘记密码时使用）
echo ============================================
echo.
echo   请先关闭正在运行的服务窗口！
echo.
cd /d F:\AIwork\2026-08-21-18-01-35\backend
venv\Scripts\python.exe launcher.py --reset-password
echo.
pause
