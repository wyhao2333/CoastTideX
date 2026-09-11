@echo off
title CoastTideX PyInstaller Build
cd /d "%~dp0"

echo ==============================================================================
echo [CoastTideX] Packaging standalone Windows .exe application...
echo ==============================================================================

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [*] Installing PyInstaller in .venv...
    ".venv\Scripts\pip.exe" install pyinstaller
)

echo [*] Running PyInstaller...
".venv\Scripts\pyinstaller.exe" --noconfirm --onedir --windowed ^
    --name "CoastTideX" ^
    --add-data "data/geoid/us_nga_egm08_25.tif;data/geoid" ^
    --add-data "data/geoid/delta_n_goco06s_minus_egm2008.tif;data/geoid" ^
    --add-data "config.yaml;." ^
    --hidden-import "pyfes" ^
    --hidden-import "rasterio" ^
    --hidden-import "scipy.signal" ^
    --hidden-import "scipy.interpolate" ^
    --hidden-import "xarray" ^
    --hidden-import "netCDF4" ^
    --hidden-import "matplotlib.backends.backend_qtagg" ^
    app.py

if errorlevel 1 (
    echo ==============================================================================
    echo [ERROR] PyInstaller build failed with exit code %errorlevel%!
    echo ==============================================================================
    pause
    exit /b %errorlevel%
)

echo ==============================================================================
echo [OK] Build completed! Output directory: dist\CoastTideX\
echo ==============================================================================
pause
