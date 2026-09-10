@echo off
title CoastTideX Setup Environment
cd /d "%~dp0"

echo ==============================================================================
echo [CoastTideX] Python Virtual Environment Setup
echo ==============================================================================

if exist ".venv\Scripts\python.exe" (
    echo [*] Local .venv environment already exists.
) else (
    echo [*] Creating .venv environment with Python 3.11...
    "E:\Python311_venv\Geo_env\Scripts\python.exe" -m venv --system-site-packages .venv
    echo E:\Python311_venv\Geo_env\Lib\site-packages> .venv\Lib\site-packages\geo_env.pth
    echo [OK] Virtual environment created successfully.
)

echo [*] Installing and verifying requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo ==============================================================================
echo [OK] Environment setup completed! You can now run run_gui.bat.
echo ==============================================================================
pause
