@echo off
title CoastTideX Setup Environment
cd /d "%~dp0"

echo ==============================================================================
echo [CoastTideX] Python Virtual Environment Setup
echo ==============================================================================

if exist ".venv\Scripts\python.exe" (
    echo [*] Local .venv environment already exists.
    goto check_reqs
)

set "PYTHON_EXE="
if exist "E:\Python311_venv\Geo_env\Scripts\python.exe" (
    set "PYTHON_EXE=E:\Python311_venv\Geo_env\Scripts\python.exe"
    set "SITE_PACKAGES=E:\Python311_venv\Geo_env\Lib\site-packages"
) else (
    for /f "tokens=*" %%i in ('where python 2^>nul') do (
        if not defined PYTHON_EXE set "PYTHON_EXE=%%i"
    )
)

if not defined PYTHON_EXE (
    echo [ERROR] No Python interpreter found on this system!
    echo Please install Python 3.11 or configure the environment path.
    pause
    exit /b 1
)

echo [*] Using Python: %PYTHON_EXE%
echo [*] Creating .venv environment...
"%PYTHON_EXE%" -m venv --system-site-packages .venv

if defined SITE_PACKAGES (
    echo %SITE_PACKAGES%> .venv\Lib\site-packages\geo_env.pth
)
echo [OK] Virtual environment created successfully.

:check_reqs
echo [*] Installing and verifying requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo ==============================================================================
echo [OK] Environment setup completed! You can now run run_gui.bat.
echo ==============================================================================
pause
