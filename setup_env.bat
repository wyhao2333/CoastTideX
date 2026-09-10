@echo off
chcp 65001 > nul
echo ==============================================================================
echo [CoastTideX] 自动化 Python 虚拟环境初始化脚本
echo ==============================================================================

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if exist ".venv\Scripts\python.exe" (
    echo [*] 检测到本地 .venv 虚拟环境已存在。
) else (
    echo [*] 正在创建本地 .venv 虚拟环境 (Python 3.11)...
    "E:\Python311_venv\Geo_env\Scripts\python.exe" -m venv --system-site-packages .venv
    echo E:\Python311_venv\Geo_env\Lib\site-packages> .venv\Lib\site-packages\geo_env.pth
    echo [✓] 虚拟环境创建成功！
)

echo [*] 检查并更新核心依赖包...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo ==============================================================================
echo [✓] CoastTideX 环境配置完成！您可以双击运行 run_gui.bat 启动软件。
echo ==============================================================================
pause
