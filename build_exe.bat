@echo off
chcp 65001 >nul
title SCU3 打包工具

cd /d "%~dp0"

echo ================================================
echo   SCU3 桌面版打包
echo ================================================
echo.

REM 清理旧文件
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist __pycache__ rmdir /s /q __pycache__

REM 打包为 exe（无黑框，单文件）
pyinstaller --noconfirm --onedir --noconsole ^
    --name "SCU3" ^
    --hidden-import=webview ^
    --hidden-import=webview.platforms.winforms ^
    --hidden-import=Flask ^
    --hidden-import=flask ^
    --hidden-import=flask_cors ^
    --hidden-import=uvicorn ^
    --hidden-import=httpx ^
    --exclude-module=tkinter ^
    --exclude-module=matplotlib ^
    --exclude-module=scipy ^
    --exclude-module=PyQt5 ^
    --exclude-module=PyQt6 ^
    app.py

if errorlevel 1 (
    echo.
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ================================================
echo   打包完成！
echo   输出目录: dist\SCU3\
echo ================================================
echo.
pause
