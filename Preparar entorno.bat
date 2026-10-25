@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 goto error
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
if errorlevel 1 goto error
if not exist "autonexus.json" (
    copy /y "autonexus.example.json" "autonexus.json" >nul
    if errorlevel 1 goto error
)
echo.
echo Entorno listo.
pause
exit /b 0
:error
echo.
echo No se pudo preparar el entorno. Revisa el error anterior.
pause
exit /b 1
