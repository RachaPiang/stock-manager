@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Python environment is missing. Please follow README.md first.
  pause
  exit /b 2
)
rem pythonw keeps the worker hidden. app.line_webhook uses a private lock, so
rem a second click safely exits instead of producing a duplicate LINE bot.
start "" ".venv\Scripts\pythonw.exe" -m app.line_webhook
echo LINE assistant is starting in the background.
echo It will reconnect its temporary webhook tunnel automatically.
