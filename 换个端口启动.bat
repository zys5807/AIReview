@echo off
title AIReviewSystem - port 8080
cd /d %~dp0

echo ============================================================
echo   Start AIReviewSystem on port 8080
echo   Use this when it says: port 8000 is used by another program
echo ============================================================
echo.
echo   NOTE: do not run two copies on two different ports at the
echo         same time. They write the same database file and may
echo         corrupt it.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
AIReviewSystem.exe --port 8080
if not "%AIRES_NO_PAUSE%"=="1" pause
