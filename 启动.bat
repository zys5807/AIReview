@echo off
title AIReviewSystem
cd /d %~dp0

if not exist "AIReviewSystem.exe" goto no_exe
if not exist "_internal\python313.dll" goto no_internal
if not exist "_internal\dist\index.html" goto no_dist

AIReviewSystem.exe %*
set RC=%ERRORLEVEL%
if "%RC%"=="0" goto done
echo.
echo ============================================================
echo   [INFO] The program exited with code %RC%
echo ============================================================
echo   If no explanation was printed above, run the diagnostics
echo   batch file in this folder to generate diagnose_report.txt,
echo   then send that file to the author.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
goto done

:no_exe
echo ============================================================
echo   [ERROR] AIReviewSystem.exe not found in this folder
echo ============================================================
echo   This file must stay together with AIReviewSystem.exe
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:no_internal
echo ============================================================
echo   [ERROR] Incomplete files: the "_internal" folder is missing
echo ============================================================
echo   The archive was NOT fully extracted. Common causes:
echo     - only the .exe was dragged out of the zip file
echo     - the .exe was run from inside the zip preview window
echo     - extraction was interrupted, or antivirus removed files
echo.
echo   FIX: extract the zip again with "Extract to current folder",
echo        then this folder must contain all 4 of these:
echo          AIReviewSystem.exe / _internal / app.db / uploads
echo.
echo   TIP: temporarily disable antivirus (360 / 360 Total Security
echo        / Windows Defender) before extracting, or restore the
echo        blocked files from its quarantine list.
echo   TIP: read the Chinese user guide (.txt) in this folder.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:no_dist
echo ============================================================
echo   [ERROR] Missing _internal\dist\index.html
echo ============================================================
echo   Incomplete extraction. Please extract the zip again.
echo.
if not "%AIRES_NO_PAUSE%"=="1" pause
exit /b 1

:done
