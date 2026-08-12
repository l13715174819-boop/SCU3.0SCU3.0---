# SCU3 桌面版 - 无黑框启动器 (PowerShell)
# 双击 .lnk 快捷方式即可无黑框启动

$ErrorActionPreference = "SilentlyContinue"

# 获取脚本所在目录
$strDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# python.exe 路径
$pythonPath = "C:\Users\若水\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"

# app.py 路径
$appPath = Join-Path $strDir "app.py"

# 切换到程序目录
Set-Location $strDir

# 启动 python（隐藏窗口）
Start-Process -FilePath $pythonPath -ArgumentList "`"$appPath`"" -WorkingDirectory $strDir -WindowStyle Hidden
