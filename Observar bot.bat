@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
    echo Ejecuta primero Preparar entorno.bat.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u -B bot_runtime.py %*
pause
