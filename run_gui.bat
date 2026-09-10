@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ==============================================================================
echo [CoastTideX] 正在启动全球海岸带潮位模拟与高程基准转换系统...
echo ==============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [*] 检测到本地虚拟环境尚未初始化，正在为您构建...
    call setup_env.bat
)

echo [*] 启动主窗口程序中，请稍候...
".venv\Scripts\python.exe" app.py

if errorlevel 1 (
    echo.
    echo [!] 程序异常退出，请查看上方详细报错信息。
    pause
)
