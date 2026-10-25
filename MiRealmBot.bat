@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if not exist ".venv\Scripts\pythonw.exe" (
  echo Falta el entorno de MiRealmBot. Ejecuta "Preparar entorno.bat" primero.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -B "%~dp0client_ui.py"
