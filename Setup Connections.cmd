@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README.md to install the Python environment first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m app.main setup
pause
endlocal
