@echo off
title AIReviewSystem - Diagnose
cd /d %~dp0

echo ============================================================
echo   AIReviewSystem - Startup diagnostics
echo   run this when the program will not open / closes at once
echo ============================================================
echo.
echo [Step 0] Checking whether the archive was fully extracted ...
echo.

if not exist "AIReviewSystem.exe" goto no_exe
if not exist "_internal\python313.dll" goto no_internal
if not exist "_internal\dist\index.html" goto no_dist

echo   [OK] All required files are present.
echo.
echo [Step 1] Generating the full diagnostics report ...
echo.
AIReviewSystem.exe --diagnose
echo.
echo ============================================================
echo   Done. Report file:  diagnose_report.txt   in this folder
echo   Please send that file to the author for analysis.
echo   The startup log startup_log.txt can be sent as well.
echo ============================================================
if not "%AIRES_NO_PAUSE%"=="1" pause
goto done

:no_exe
echo   [ERROR] AIReviewSystem.exe not found in this folder.
echo           This file must stay together with the .exe
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:no_internal
echo   [ERROR] the "_internal" folder is missing
echo           The zip was NOT fully extracted.
echo.
echo           Extract the zip again with "Extract to current folder",
echo           then this folder must contain all 4 of these:
echo             AIReviewSystem.exe / _internal / app.db / uploads
echo.
echo           If it keeps happening, temporarily disable antivirus
echo           (360 / Windows Defender), re-extract, then re-enable.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:no_dist
echo   [ERROR] Missing _internal\dist\index.html
echo           Incomplete extraction, please extract again.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:done
