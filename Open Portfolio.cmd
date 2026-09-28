@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please follow README.md to install the Python environment first.
  pause
  exit /b 2
)
rem The portfolio page and LINE manager are one personal assistant.  Starting
rem the manager here keeps the normal double-click workflow from leaving LINE
rem offline.  The manager owns a lock, so this is safe when it is already up.
call "Start LINE Bot.cmd"
echo Updating your watchlist and preparing charts...
".venv\Scripts\python.exe" -m app.main view
if errorlevel 1 (
  echo Please review the message above. Saved charts may contain older data.
  pause
)
endlocal
