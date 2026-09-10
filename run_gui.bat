@echo off
chcp 65001 > nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [*] 首次运行，正在自动为您构建虚拟环境...
    call setup_env.bat
)

echo [*] 正在启动 CoastTideX 桌面客户端...
start "" ".venv\Scripts\pythonw.exe" app.py
