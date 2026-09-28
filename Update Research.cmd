@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README.md to install the Python environment first.
  pause
  exit /b 2
)
echo Syncing important official company releases...
".venv\Scripts\python.exe" -m app.main news-sync
if errorlevel 1 (
  echo News sync was not complete. The existing report remains available.
  pause
  exit /b 1
)
echo Creating this week's portfolio review...
".venv\Scripts\python.exe" -m app.main review
".venv\Scripts\python.exe" -m app.main report
start "" "data\live-portfolio.html"
endlocal
