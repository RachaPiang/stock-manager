@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please install the Python environment as described in README.md.
  pause
  exit /b 2
)
echo Testing Codex with synthetic stock data. This uses your Codex quota.
echo No stock API calls or LINE messages will be sent.
".venv\Scripts\python.exe" -m app.main test-codex
pause
endlocal
