@echo off
chcp 65001 > nul
echo ==============================================================================
echo [CoastTideX] Windows 独立可执行程序 (.exe) 打包构建脚本
echo ==============================================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [*] 正在安装 PyInstaller...
    ".venv\Scripts\pip.exe" install pyinstaller
)

echo [*] 开始执行 PyInstaller 打包构建...
".venv\Scripts\pyinstaller.exe" --noconfirm --onedir --windowed ^
    --name "CoastTideX" ^
    --add-data "data/geoid/us_nga_egm08_25.tif;data/geoid" ^
    --add-data "config.yaml;." ^
    --hidden-import "pyfes" ^
    --hidden-import "rasterio" ^
    --hidden-import "scipy.signal" ^
    --hidden-import "scipy.interpolate" ^
    --hidden-import "xarray" ^
    --hidden-import "netCDF4" ^
    --hidden-import "matplotlib.backends.backend_qtagg" ^
    app.py

echo ==============================================================================
echo [✓] 打包完成！生成目录位于: dist\CoastTideX\
echo 包含 CoastTideX.exe 以及所有必要运行时资源。
echo ==============================================================================
pause
