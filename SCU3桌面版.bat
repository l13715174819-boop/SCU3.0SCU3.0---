@echo off
cd /d "%~dp0"
title SCU3 Desktop

set "TRAE_PY=%~dp0..\..\..\vm\tools\python\python.exe"

echo ================================================
echo   SCU3 Desktop v3.1
echo   Loading... please wait 10-30 seconds
echo ================================================
echo.

if exist "%TRAE_PY%" (
    "%TRAE_PY%" app.py
) else (
    echo [WARN] TRAE Python not found, trying system python...
    python app.py
)

if errorlevel 1 (
    echo.
    echo [ERROR] Startup failed
    if exist scu3_launcher.log type scu3_launcher.log
    pause
)