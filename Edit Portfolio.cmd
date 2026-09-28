@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please install the project Python environment first.
  pause
  exit /b 2
)
start "" ".venv\Scripts\pythonw.exe" -m app.portfolio_editor
endlocal
