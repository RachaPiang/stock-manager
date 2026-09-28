$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$expectedPython = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\pythonw.exe')).Path
# Stop only this project's explicitly named background bot, never other Python workers.
Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
    Where-Object { $_.ExecutablePath -eq $expectedPython -and $_.CommandLine -match '\s-m\s+app\.line_webhook(?:\s|$)' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId }
Write-Output 'Stopped matching background LINE bot processes. Existing stock checks are unchanged.'
