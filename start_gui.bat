@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python, 请先安装 Python 3 并勾选 "Add to PATH"
    echo 安装后重新运行本脚本。
    pause
    exit /b 1
)
echo 正在启动 DASH 视频下载器...
start "" pythonw dash_gui.py 2>nul
if errorlevel 1 start "" python dash_gui.py
