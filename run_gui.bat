@echo off
title CoastTideX - Global Coastal Tide Simulation
cd /d "%~dp0"

echo ==============================================================================
echo [CoastTideX] Starting Global Coastal Tide Simulation System...
echo ==============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [*] Initializing virtual environment...
    call setup_env.bat
)

echo [*] Launching application with .venv...
".venv\Scripts\python.exe" app.py

if errorlevel 1 (
    echo.
    echo [!] Application exited with code %errorlevel%.
    pause
)
